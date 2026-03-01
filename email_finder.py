"""
Standalone Email Finder
=======================
Takes a CSV with full_name and domain columns, guesses email candidates
using 26 common business patterns, validates them via Truelist batch API,
and writes back the CSV with email and email_confidence columns.

Usage:
    python email_finder.py contacts.csv -c "Acme Corp"     # Organize under clients/acme_corp/
    python email_finder.py contacts.csv -c "Acme" --enhanced
    python email_finder.py contacts.csv                     # Legacy: read/write in place
    python email_finder.py contacts.csv -o results.csv      # Custom output path

With --client / -c the input CSV is moved into clients/<name>/input/ and
results are written to clients/<name>/output/. The clients/ folder is
gitignored so no client data is ever pushed to the repository.

Fast mode:  pattern guess + Truelist only  (0 ScrapingDog credits)
Enhanced:   scrape websites first, then guess + verify  (2-4 SD credits/domain)
"""

import argparse
import csv
import io
import os
import re
import shutil
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════

TRUELIST_API_KEY = os.getenv("TRUELIST_API_KEY", "")
SCRAPINGDOG_API_KEY = os.getenv("SCRAPINGDOG_API_KEY", "")

TRUELIST_BASE_URL = "https://api.truelist.io/api/v1"
SCRAPINGDOG_BASE_URL = "https://api.scrapingdog.com"

TRUELIST_BATCH_POLL_INTERVAL = 10
TRUELIST_BATCH_TIMEOUT = 40 * 60
CSV_GENERATION_DELAY = 15
CSV_URL_RETRY_DELAY = 10
CSV_URL_MAX_RETRIES = 6
MAX_PARALLEL_REQUESTS = 5
SCRAPE_TIMEOUT = 15

VALID_STATUSES = {"email_ok"}
PASS_STATUSES = {"email_ok"}
RETRY_STATUSES = {"unknown", "unknown_error", "timeout", "error"}

_GENERIC_PREFIXES = {
    "info", "contact", "hello", "support", "sales", "admin", "team",
    "help", "noreply", "no-reply", "billing", "press", "media",
    "marketing", "office", "careers", "hr", "legal", "privacy",
}

_CONTACT_PAGE_PATTERNS = re.compile(
    r'href=["\'](?P<url>[^"\']*?'
    r'(?:/contact|/about|/about-us|/contact-us|/get-in-touch|/connect|/team|/our-team)'
    r'[^"\']*?)["\']',
    re.IGNORECASE,
)

_CONTACT_PAGE_FALLBACKS = ("/contact", "/about")
_SMB_EXTRA_PATHS = (
    "/team", "/our-team", "/staff", "/people",
    "/meet-the-team", "/about-us/meet-the-team",
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ══════════════════════════════════════════════════════════════
# Log Tee (mirrors stdout to a log file)
# ══════════════════════════════════════════════════════════════

class _TeeWriter:
    """Wraps stdout so every print() also writes to a log file."""

    def __init__(self, log_path: str):
        self._file = open(log_path, "w", encoding="utf-8")
        self._stdout = sys.stdout

    def write(self, data: str):
        self._stdout.write(data)
        self._file.write(data)
        self._file.flush()

    def flush(self):
        self._stdout.flush()
        self._file.flush()

    def close(self):
        sys.stdout = self._stdout
        self._file.close()


# ══════════════════════════════════════════════════════════════
# Name Helpers
# ══════════════════════════════════════════════════════════════

def _sanitize_name(name: str) -> str:
    s = name.lower().strip()
    s = s.replace(".", "")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^a-z]", "", s)
    return s


def _split_full_name(full_name: str) -> tuple[str, str]:
    """Split a full name into (first, last).

    Handles single-word names, multi-word names, and common prefixes/suffixes.
    """
    parts = full_name.strip().split()
    if not parts:
        return ("", "")
    if len(parts) == 1:
        return (parts[0], "")

    prefixes = {"dr", "mr", "mrs", "ms", "prof", "sir", "dame"}
    suffixes = {"jr", "sr", "ii", "iii", "iv", "phd", "md", "esq", "cpa"}

    filtered = []
    for p in parts:
        clean = p.lower().rstrip(".")
        if clean in prefixes or clean in suffixes:
            continue
        filtered.append(p)

    if not filtered:
        filtered = parts
    if len(filtered) == 1:
        return (filtered[0], "")
    return (filtered[0], filtered[-1])


# ══════════════════════════════════════════════════════════════
# Email Candidate Generation
# ══════════════════════════════════════════════════════════════

def generate_candidates(first: str, last: str, domain: str) -> list[str]:
    """Generate 26 common business email pattern candidates."""
    f = _sanitize_name(first)
    l = _sanitize_name(last)

    if not f or len(l) < 2 or not domain:
        return []

    domain = domain.lower().strip()
    domain = domain.replace("https://", "").replace("http://", "").replace("www.", "")
    domain = domain.split("/")[0]

    separators = ["", ".", "_", "-"]
    candidates = []

    for sep in separators:
        candidates.append(f"{f}{sep}{l}@{domain}")
        candidates.append(f"{f}{sep}{l[0]}@{domain}")
        candidates.append(f"{f[0]}{sep}{l}@{domain}")
        candidates.append(f"{l}{sep}{f}@{domain}")
        candidates.append(f"{l}{sep}{f[0]}@{domain}")
        candidates.append(f"{l[0]}{sep}{f}@{domain}")

    candidates.append(f"{f}@{domain}")
    candidates.append(f"{l}@{domain}")

    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


# ══════════════════════════════════════════════════════════════
# Email-Contact Matching
# ══════════════════════════════════════════════════════════════

def _email_matches_contact(email: str, first: str, last: str) -> bool:
    """Check if an email's local part plausibly belongs to a given name."""
    raw_local = email.split("@")[0].lower()
    first_l = first.lower().strip()
    last_l = last.lower().strip()

    segments = re.split(r'[.\-_]', raw_local)
    segments = [s for s in segments if s]

    def _name_compat(segment: str, expected_first: str) -> bool:
        if not segment:
            return True
        if segment == expected_first:
            return True
        if len(segment) == 1 and segment == expected_first[:1]:
            return True
        return False

    if len(segments) >= 2:
        for i, seg in enumerate(segments):
            others = [segments[j] for j in range(len(segments)) if j != i]
            if len(last_l) >= 3 and seg == last_l:
                if any(_name_compat(o, first_l) for o in others):
                    return True
            if len(first_l) >= 3 and seg == first_l:
                if any(o == last_l for o in others) or len(segments) == 2:
                    return True

    concat = raw_local.replace(".", "").replace("-", "").replace("_", "")

    if len(first_l) >= 3 and first_l in concat:
        return True

    if len(last_l) >= 3 and last_l in concat:
        idx = concat.find(last_l)
        prefix = concat[:idx]
        if not prefix:
            return True
        if prefix == first_l:
            return True
        if len(prefix) == 1 and prefix == first_l[:1]:
            return True
        return False

    if len(first_l) >= 2 and len(last_l) >= 2:
        if concat.startswith(first_l[0]) and last_l in concat:
            return True

    return False


# ══════════════════════════════════════════════════════════════
# Website Scraping (Enhanced Mode)
# ══════════════════════════════════════════════════════════════

def _fetch_page(url: str) -> str | None:
    """Fetch a page directly via requests (no proxy)."""
    try:
        resp = requests.get(
            url, timeout=SCRAPE_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
            allow_redirects=True,
        )
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


def _extract_emails_from_html(html: str, domain_clean: str) -> set[str]:
    matches = re.findall(
        r'[a-zA-Z0-9._%+\-]+@' + re.escape(domain_clean),
        html, re.IGNORECASE,
    )
    return {e.lower() for e in matches}


def _scrape_contact_pages(
    homepage_html: str | None, domain_clean: str,
) -> set[str]:
    """Scrape contact/about/team pages for published emails."""
    max_pages = 3
    found: set[str] = set()
    urls_to_try: list[str] = []

    if homepage_html:
        for m in _CONTACT_PAGE_PATTERNS.finditer(homepage_html):
            href = m.group("url").strip()
            if href.startswith("http"):
                urls_to_try.append(href)
            elif href.startswith("/"):
                urls_to_try.append(f"https://{domain_clean}{href}")

    fallbacks = list(_CONTACT_PAGE_FALLBACKS) + list(_SMB_EXTRA_PATHS)
    if not urls_to_try:
        urls_to_try = [f"https://{domain_clean}{path}" for path in fallbacks]
    else:
        urls_to_try.extend(f"https://{domain_clean}{path}" for path in _SMB_EXTRA_PATHS)

    seen: set[str] = set()
    scraped = 0
    for url in urls_to_try:
        if scraped >= max_pages:
            break
        norm = url.rstrip("/").lower()
        if norm in seen:
            continue
        seen.add(norm)
        html = _fetch_page(url)
        if html:
            found |= _extract_emails_from_html(html, domain_clean)
        scraped += 1
    return found


def _google_email_search(domain_clean: str) -> set[str]:
    """Use ScrapingDog Google Search to find public emails at a domain."""
    if not SCRAPINGDOG_API_KEY:
        return set()
    try:
        resp = requests.get(
            f"{SCRAPINGDOG_BASE_URL}/google",
            params={
                "api_key": SCRAPINGDOG_API_KEY,
                "query": f'"@{domain_clean}" email',
                "results": 5,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return set()
        data = resp.json()
        found: set[str] = set()
        for r in data.get("organic_results", []):
            text = f"{r.get('title', '')} {r.get('snippet', '')}"
            found |= _extract_emails_from_html(text, domain_clean)
        return found
    except Exception:
        return set()


def detect_email_format(
    domain: str, first_name: str, last_name: str,
) -> dict[str, list[str]]:
    """Scrape a company's website to find emails and infer naming patterns.

    Returns {"prioritized": [...], "generic": [...]}.
    """
    found_emails: set[str] = set()
    domain_clean = domain.lower().strip()

    homepage_html = _fetch_page(f"https://{domain_clean}")
    if homepage_html:
        found_emails |= _extract_emails_from_html(homepage_html, domain_clean)

    personal_so_far = [
        e for e in found_emails if e.split("@")[0] not in _GENERIC_PREFIXES
    ]
    if len(personal_so_far) <= 1:
        contact_emails = _scrape_contact_pages(homepage_html, domain_clean)
        found_emails |= contact_emails

    google_emails = _google_email_search(domain_clean)
    found_emails |= google_emails

    personal_emails = [
        e for e in found_emails if e.split("@")[0] not in _GENERIC_PREFIXES
    ]
    generic_emails = [
        e for e in found_emails if e.split("@")[0] in _GENERIC_PREFIXES
    ]

    if not personal_emails and not generic_emails:
        return {"prioritized": [], "generic": []}

    f = _sanitize_name(first_name)
    l = _sanitize_name(last_name)
    if not f or len(l) < 2:
        return {"prioritized": [], "generic": generic_emails}

    prioritized: list[str] = []
    patterns_seen: set[str] = set()

    for email in personal_emails:
        local = email.split("@")[0]
        for sep in [".", "_", "-", ""]:
            if sep:
                parts = local.split(sep)
            else:
                parts = [local]

            if len(parts) == 2:
                for pattern in [
                    f"{f}{sep}{l}", f"{l}{sep}{f}",
                    f"{f[0]}{sep}{l}", f"{l}{sep}{f[0]}",
                    f"{f}{sep}{l[0]}", f"{l[0]}{sep}{f}",
                ]:
                    candidate = f"{pattern}@{domain_clean}"
                    if candidate not in patterns_seen:
                        patterns_seen.add(candidate)
                        prioritized.append(candidate)
            elif len(parts) == 1 and not sep:
                for pattern in [f, l, f"{f}{l}", f"{l}{f}", f"{f[0]}{l}", f"{l}{f[0]}"]:
                    candidate = f"{pattern}@{domain_clean}"
                    if candidate not in patterns_seen:
                        patterns_seen.add(candidate)
                        prioritized.append(candidate)

    scraped_first = [e for e in personal_emails if e not in patterns_seen]
    if scraped_first:
        prioritized = scraped_first + prioritized

    return {"prioritized": prioritized, "generic": generic_emails}


# ══════════════════════════════════════════════════════════════
# DNS / Domain Pre-checks
# ══════════════════════════════════════════════════════════════

def _dns_mx_check(domain: str) -> bool:
    try:
        import dns.resolver
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        return len(answers) > 0
    except Exception:
        return False


def _truelist_inline_verify(emails: list[str]) -> dict[str, dict]:
    """Verify up to 3 emails via Truelist inline API."""
    if not TRUELIST_API_KEY or not emails:
        return {}
    batch = emails[:3]
    email_param = " ".join(batch)
    try:
        resp = requests.post(
            f"{TRUELIST_BASE_URL}/verify_inline?email={email_param}",
            headers={"Authorization": f"Bearer {TRUELIST_API_KEY}"},
            timeout=35,
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
        results = {}
        for ed in data.get("emails", []):
            addr = ed.get("address", ed.get("email_address", ""))
            status = ed.get("email_sub_state", ed.get("email_state", "unknown"))
            results[addr.lower()] = {"status": status.lower()}
        return results
    except Exception:
        return {}


def precheck_domains(domains: set[str]) -> tuple[set[str], set[str]]:
    """Check domains for MX records and catch-all behaviour.

    Returns (catchall_domains, no_mx_domains).
    """
    catchall: set[str] = set()
    no_mx: set[str] = set()

    if not domains:
        return catchall, no_mx

    print(f"  Pre-checking {len(domains)} unique domains for MX + catch-all...")

    dns_passed: list[str] = []
    for domain in sorted(domains):
        if _dns_mx_check(domain):
            dns_passed.append(domain)
        else:
            no_mx.add(domain)
            print(f"    {domain} -> no MX record")

    for domain in dns_passed:
        probe = f"noreply-test-xz9q7@{domain}"
        result = _truelist_inline_verify([probe])
        info = result.get(probe.lower(), {})
        status = info.get("status", "")
        if status == "accept_all":
            catchall.add(domain)
            print(f"    {domain} -> catch-all")
        elif status == "failed_mx_check":
            no_mx.add(domain)
            print(f"    {domain} -> no MX (Truelist)")

    valid = len(domains) - len(catchall) - len(no_mx)
    print(f"  Domain pre-check done: {len(catchall)} catch-all, {len(no_mx)} no-MX, {valid} valid")
    return catchall, no_mx


# ══════════════════════════════════════════════════════════════
# Truelist Batch Submission & Polling
# ══════════════════════════════════════════════════════════════

def _parse_batch_csv(csv_content: str) -> dict[str, dict]:
    results: dict[str, dict] = {}
    try:
        reader = csv.DictReader(io.StringIO(csv_content))
        for row in reader:
            email = (
                row.get("Email Address")
                or row.get("email") or row.get("Email")
                or row.get("email_address") or row.get("Column A", "")
            ).lower().strip()
            if not email or "@" not in email:
                continue
            status = (
                row.get("Email Sub-State") or row.get("email_sub_state")
                or row.get("Email Sub State") or row.get("Email State")
                or row.get("email_state") or row.get("sub_state")
                or row.get("Sub State") or row.get("state")
                or row.get("status") or row.get("Status")
                or row.get("result") or row.get("Result")
                or "unknown"
            ).lower()
            passed = status in PASS_STATUSES
            results[email] = {"status": status, "passed": passed}
        return results
    except Exception:
        return {}


def _download_csv(url: str) -> dict[str, dict]:
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200:
            return _parse_batch_csv(resp.text)
    except Exception:
        pass
    return {}


def _download_fallback_csvs(batch_data: dict) -> dict[str, dict]:
    combined: dict[str, dict] = {}
    for key in ("highest_reach_csv_url", "only_invalid_csv_url", "safest_bet_csv_url"):
        url = batch_data.get(key)
        if url:
            results = _download_csv(url)
            for email, result in results.items():
                if email not in combined:
                    combined[email] = result
    return combined


def _wait_for_csv_url(batch_id: str, headers: dict) -> str | None:
    for retry in range(CSV_URL_MAX_RETRIES):
        print(f"    CSV URL not ready, retry {retry + 1}/{CSV_URL_MAX_RETRIES}...")
        time.sleep(CSV_URL_RETRY_DELAY)
        try:
            resp = requests.get(
                f"{TRUELIST_BASE_URL}/batches/{batch_id}",
                headers=headers, timeout=30,
            )
            if resp.status_code == 200:
                url = resp.json().get("annotated_csv_url")
                if url:
                    return url
        except Exception:
            pass
    return None


def submit_batch(emails: list[str]) -> str | None:
    if not TRUELIST_API_KEY:
        raise ValueError("TRUELIST_API_KEY not set in .env")
    valid = [e for e in emails if "@" in e]
    if not valid:
        return None
    name = f"emailfinder_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.csv"
    print(f"  Submitting {len(valid)} emails to Truelist batch...")
    try:
        resp = requests.post(
            f"{TRUELIST_BASE_URL}/batches",
            headers={"Authorization": f"Bearer {TRUELIST_API_KEY}"},
            json={
                "data": [[e] for e in valid],
                "validation_strategy": "fast",
                "name": name,
            },
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"  Batch submit error {resp.status_code}: {resp.text[:200]}")
            return None
        batch_id = resp.json().get("id")
        print(f"  Batch submitted: {batch_id}")
        return batch_id
    except Exception as e:
        print(f"  Batch submit error: {e}")
        return None


def poll_batch(batch_id: str) -> dict[str, dict]:
    headers = {"Authorization": f"Bearer {TRUELIST_API_KEY}"}
    start = time.time()
    poll_count = 0
    consecutive_errors = 0

    print(f"  Polling batch {batch_id}...")

    while time.time() - start < TRUELIST_BATCH_TIMEOUT:
        poll_count += 1
        try:
            resp = requests.get(
                f"{TRUELIST_BASE_URL}/batches/{batch_id}",
                headers=headers, timeout=30,
            )
            if resp.status_code != 200:
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    print(f"  Aborting: {consecutive_errors} consecutive poll errors")
                    return {}
                time.sleep(TRUELIST_BATCH_POLL_INTERVAL)
                continue

            consecutive_errors = 0
            data = resp.json()
            state = data.get("batch_state", "unknown")
            processed = data.get("processed_count", 0)
            total = data.get("email_count", 0)
            elapsed = time.time() - start

            if poll_count % 5 == 1 or state == "completed":
                pct = (processed / total * 100) if total > 0 else 0
                print(f"    Poll #{poll_count} | {state} | {processed}/{total} ({pct:.0f}%) | {elapsed:.0f}s")

            if state == "completed" and processed < total:
                time.sleep(TRUELIST_BATCH_POLL_INTERVAL)
                continue

            if state == "completed":
                print(f"  Batch completed. Waiting {CSV_GENERATION_DELAY}s for CSV...")
                time.sleep(CSV_GENERATION_DELAY)

                try:
                    refresh = requests.get(
                        f"{TRUELIST_BASE_URL}/batches/{batch_id}",
                        headers=headers, timeout=30,
                    )
                    if refresh.status_code == 200:
                        data = refresh.json()
                except Exception:
                    pass

                csv_url = (
                    data.get("annotated_csv_url")
                    or data.get("results_url")
                    or data.get("download_url")
                    or data.get("csv_url")
                )
                if not csv_url:
                    csv_url = _wait_for_csv_url(batch_id, headers)

                if csv_url:
                    results = _download_csv(csv_url)
                    if results and len(results) < total:
                        supplemental = _download_fallback_csvs(data)
                        for email, result in supplemental.items():
                            if email not in results:
                                results[email] = result
                    if results:
                        print(f"  Got {len(results)} results from Truelist")
                        return results

                results = _download_fallback_csvs(data)
                if results:
                    print(f"  Got {len(results)} results from fallback CSVs")
                    return results
                print("  Could not download results after all fallbacks")
                return {}

            elif state == "failed":
                print(f"  Batch failed!")
                return {}

            time.sleep(TRUELIST_BATCH_POLL_INTERVAL)

        except Exception as e:
            consecutive_errors += 1
            print(f"  Poll error: {e}")
            if consecutive_errors >= 5:
                return {}
            time.sleep(TRUELIST_BATCH_POLL_INTERVAL)

    print(f"  Batch timed out after {TRUELIST_BATCH_TIMEOUT // 60}m")
    return {}


def delete_all_batches() -> int:
    if not TRUELIST_API_KEY:
        return 0
    headers = {"Authorization": f"Bearer {TRUELIST_API_KEY}"}
    deleted = 0
    try:
        resp = requests.get(f"{TRUELIST_BASE_URL}/batches", headers=headers, timeout=30)
        if resp.status_code != 200:
            return 0
        data = resp.json()
        batches = data if isinstance(data, list) else data.get("batches", [])
        for batch in batches:
            bid = batch.get("id") if isinstance(batch, dict) else batch
            if bid:
                try:
                    r = requests.delete(
                        f"{TRUELIST_BASE_URL}/batches/{bid}",
                        headers=headers, timeout=30,
                    )
                    if r.status_code in (200, 204):
                        deleted += 1
                except Exception:
                    pass
    except Exception:
        pass
    if deleted:
        print(f"  Cleaned up {deleted} old Truelist batches")
    return deleted


# ══════════════════════════════════════════════════════════════
# Main Pipeline
# ══════════════════════════════════════════════════════════════

def find_emails(rows: list[dict], enhanced: bool = False) -> list[dict]:
    """Find emails for a list of {"full_name": ..., "domain": ...} dicts.

    Returns the same list with "email" and "email_confidence" keys added.
    """
    if not TRUELIST_API_KEY:
        raise ValueError(
            "TRUELIST_API_KEY not set. Add it to your .env file.\n"
            "  Get a free key at https://truelist.io"
        )
    if enhanced and not SCRAPINGDOG_API_KEY:
        print(
            "  WARNING: --enhanced requires SCRAPINGDOG_API_KEY for Google email search.\n"
            "  Website scraping will still work, but Google search will be skipped."
        )

    # Parse names
    for row in rows:
        first, last = _split_full_name(row["full_name"])
        row["_first"] = first
        row["_last"] = last

    # Collect unique domains
    all_domains = {
        row["domain"].lower().strip()
        for row in rows
        if row.get("domain")
    }

    # Step 1: Domain pre-check
    print(f"\n{'='*60}")
    print(f"  EMAIL FINDER — {len(rows)} contacts, {len(all_domains)} unique domains")
    print(f"  Mode: {'enhanced' if enhanced else 'fast'}")
    print(f"{'='*60}\n")

    catchall_domains, no_mx_domains = precheck_domains(all_domains)

    # Step 2: Enhanced mode — website email detection
    detected_patterns: dict[str, list[str]] = {}
    if enhanced:
        print(f"\n  Scanning websites for published emails...")
        skip_domains = catchall_domains | no_mx_domains
        domains_to_detect = {}
        for row in rows:
            domain = row.get("domain", "").lower().strip()
            first, last = row["_first"], row["_last"]
            if not first or not last or not domain:
                continue
            if domain in skip_domains or domain in domains_to_detect:
                continue
            domains_to_detect[domain] = (first, last)

        if domains_to_detect:
            def _detect_one(item):
                d, (f, l) = item
                return d, detect_email_format(d, f, l)

            with ThreadPoolExecutor(max_workers=MAX_PARALLEL_REQUESTS) as executor:
                futures = [executor.submit(_detect_one, item) for item in domains_to_detect.items()]
                for future in as_completed(futures):
                    domain, result = future.result()
                    if isinstance(result, dict) and result.get("prioritized"):
                        detected_patterns[domain] = result["prioritized"]

            found = sum(1 for p in detected_patterns.values() if p)
            print(f"  Website detection: {found}/{len(domains_to_detect)} domains yielded patterns")

    # Step 3: Generate candidates
    print(f"\n  Generating email candidates...")
    candidates_map: dict[int, list[str]] = {}
    website_sourced_map: dict[int, set[str]] = {}
    all_emails: list[str] = []
    skipped: set[int] = set()

    for i, row in enumerate(rows):
        domain = row.get("domain", "").lower().strip()
        first, last = row["_first"], row["_last"]

        if not first or not last or not domain:
            row["email"] = ""
            row["email_confidence"] = ""
            skipped.add(i)
            print(f"    [{i+1}/{len(rows)}] {row['full_name']} — skipped (missing name or domain)")
            continue

        if domain in no_mx_domains:
            row["email"] = ""
            row["email_confidence"] = ""
            skipped.add(i)
            print(f"    [{i+1}/{len(rows)}] {row['full_name']} — skipped (no MX: {domain})")
            continue

        if domain in catchall_domains:
            row["email"] = ""
            row["email_confidence"] = ""
            skipped.add(i)
            print(f"    [{i+1}/{len(rows)}] {row['full_name']} — skipped (catch-all: {domain})")
            continue

        candidates = generate_candidates(first, last, domain)
        website_sourced: set[str] = set()

        detected = detected_patterns.get(domain, [])
        if detected:
            compatible = [e for e in detected if _email_matches_contact(e, first, last)]
            website_sourced.update(e.lower() for e in compatible)
            candidates = compatible + [c for c in candidates if c not in compatible]

        candidates_map[i] = candidates
        if website_sourced:
            website_sourced_map[i] = website_sourced
        all_emails.extend(candidates)
        print(f"    [{i+1}/{len(rows)}] {row['full_name']} @ {domain} — {len(candidates)} candidates")

    if not candidates_map:
        print("\n  No contacts to validate.")
        return rows

    print(f"\n  Total: {len(all_emails)} candidates across {len(candidates_map)} contacts")

    # Step 4: Clean old batches + submit
    delete_all_batches()

    MAX_RETRIES = 2
    results = None
    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            print(f"\n  Retry {attempt}/{MAX_RETRIES}: waiting 60s...")
            time.sleep(60)
            delete_all_batches()

        batch_id = submit_batch(all_emails)
        if not batch_id:
            continue
        results = poll_batch(batch_id)
        if results:
            break

    if not results:
        print("  All Truelist attempts failed. No emails validated.")
        for row in rows:
            if "email" not in row:
                row["email"] = ""
                row["email_confidence"] = ""
        return rows

    # Step 5: Map results back
    print(f"\n  Mapping results to contacts...")
    found_count = 0

    for i, candidates in candidates_map.items():
        row = rows[i]
        ws_emails = website_sourced_map.get(i, set())
        domain = row.get("domain", "").lower().strip()

        winning_email = None
        ws_fallback_email = None
        is_catchall = False

        for email in candidates:
            result = results.get(email.lower(), {})
            status = result.get("status", "unknown")
            passed = result.get("passed", False)

            if passed and winning_email is None:
                winning_email = email

            if (not passed and status == "unknown"
                    and email.lower() in ws_emails
                    and ws_fallback_email is None):
                ws_fallback_email = email

            if status == "accept_all":
                is_catchall = True

        if is_catchall and not winning_email:
            row["email"] = ""
            row["email_confidence"] = ""
            print(f"    [{i+1}/{len(rows)}] {row['full_name']} — catch-all domain, skipped")
        elif winning_email:
            row["email"] = winning_email
            row["email_confidence"] = "verified"
            found_count += 1
            print(f"    [{i+1}/{len(rows)}] {row['full_name']} — VERIFIED: {winning_email}")
        elif ws_fallback_email and not is_catchall:
            row["email"] = ws_fallback_email
            row["email_confidence"] = "website-sourced"
            found_count += 1
            print(f"    [{i+1}/{len(rows)}] {row['full_name']} — WEBSITE-SOURCED: {ws_fallback_email}")
        else:
            row["email"] = ""
            row["email_confidence"] = ""
            print(f"    [{i+1}/{len(rows)}] {row['full_name']} — no verified email found")

    for i in skipped:
        if "email" not in rows[i]:
            rows[i]["email"] = ""
        if "email_confidence" not in rows[i]:
            rows[i]["email_confidence"] = ""

    # Summary
    print(f"\n{'='*60}")
    print(f"  RESULTS")
    print(f"{'='*60}")
    verified = sum(1 for r in rows if r.get("email_confidence") == "verified")
    ws = sum(1 for r in rows if r.get("email_confidence") == "website-sourced")
    none_ = sum(1 for r in rows if not r.get("email"))
    print(f"    Verified:        {verified}")
    print(f"    Website-sourced: {ws}")
    print(f"    No email:        {none_}")
    print(f"    Total:           {len(rows)}")
    print()

    return rows


# ══════════════════════════════════════════════════════════════
# CSV I/O & CLI
# ══════════════════════════════════════════════════════════════

def read_csv(path: str) -> list[dict]:
    """Read input CSV. Expects columns: full_name, domain."""
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []

        name_col = None
        domain_col = None
        for col in fieldnames:
            cl = col.strip().lower()
            if cl in ("full_name", "fullname", "name", "full name", "contact name"):
                name_col = col
            elif cl in ("domain", "website", "company_domain", "company domain"):
                domain_col = col

        if not name_col or not domain_col:
            raise ValueError(
                f"CSV must have a name column (full_name/name) and a domain column "
                f"(domain/website). Found: {fieldnames}"
            )

        for row in reader:
            full_name = (row.get(name_col) or "").strip()
            domain = (row.get(domain_col) or "").strip()
            domain = domain.replace("https://", "").replace("http://", "").replace("www.", "")
            domain = domain.split("/")[0]
            if full_name and domain:
                rows.append({"full_name": full_name, "domain": domain})
            elif full_name:
                rows.append({"full_name": full_name, "domain": ""})

    return rows


def write_csv(rows: list[dict], path: str) -> None:
    """Write output CSV with full_name, domain, email, email_confidence."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["full_name", "domain", "email", "email_confidence"],
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "full_name": row.get("full_name", ""),
                "domain": row.get("domain", ""),
                "email": row.get("email", ""),
                "email_confidence": row.get("email_confidence", ""),
            })
    print(f"  Output written to: {path}")


def _sanitize_client_name(name: str) -> str:
    """Turn a human client name into a safe folder name.

    'Agri Corp' -> 'agri_corp', 'O\'Brien & Sons!' -> 'obrien_sons'
    """
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s\-]+", "_", s)
    s = s.strip("_")
    return s or "unnamed"


def main():
    parser = argparse.ArgumentParser(
        description="Find verified emails from names + domains via pattern guessing and Truelist.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python email_finder.py contacts.csv -c "Acme Corp"\n'
            '  python email_finder.py contacts.csv -c "Acme" --enhanced\n'
            "  python email_finder.py contacts.csv\n"
            "  python email_finder.py contacts.csv -o results.csv\n"
            "\n"
            "Client mode (-c / --client):\n"
            "  Moves the input CSV into clients/<name>/input/, writes\n"
            "  results to clients/<name>/output/, and saves a full run\n"
            "  log to clients/<name>/logs/. The clients/ folder is\n"
            "  gitignored so client data never reaches the repository.\n"
            "\n"
            "Input CSV format:\n"
            "  full_name,domain\n"
            '  John Doe,example.com\n'
            '  Jane Smith,acme.io\n'
            "\n"
            "Requires TRUELIST_API_KEY in .env (free at truelist.io).\n"
            "Enhanced mode optionally uses SCRAPINGDOG_API_KEY for Google email search."
        ),
    )
    parser.add_argument("input_csv", help="Path to input CSV with full_name and domain columns")
    parser.add_argument("-o", "--output", help="Output CSV path (default: <input>_results.csv)")
    parser.add_argument(
        "-c", "--client",
        help="Client name. Creates clients/<name>/input/, output/, and logs/ "
             "folders. Moves the input CSV into input/, writes results to "
             "output/, and saves a timestamped run log to logs/.",
    )
    parser.add_argument(
        "--enhanced", action="store_true",
        help="Enable website scraping to detect email patterns before guessing",
    )

    args = parser.parse_args()

    if not os.path.exists(args.input_csv):
        print(f"Error: File not found: {args.input_csv}")
        return

    input_path = args.input_csv
    csv_basename = os.path.basename(input_path)
    base_no_ext, ext = os.path.splitext(csv_basename)

    tee: _TeeWriter | None = None

    if args.client:
        safe_name = _sanitize_client_name(args.client)
        client_dir = os.path.join("clients", safe_name)
        input_dir = os.path.join(client_dir, "input")
        output_dir = os.path.join(client_dir, "output")
        logs_dir = os.path.join(client_dir, "logs")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(logs_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(logs_dir, f"run_{timestamp}.log")
        tee = _TeeWriter(log_path)
        sys.stdout = tee

        output_path = args.output or os.path.join(output_dir, f"{base_no_ext}_results{ext}")

        rows = read_csv(input_path)
        if not rows:
            print("No valid rows found in input CSV.")
            return

        dest = os.path.join(input_dir, csv_basename)
        if os.path.abspath(input_path) != os.path.abspath(dest):
            shutil.move(input_path, dest)
            print(f"  Moved input CSV -> {dest}")

        print(f"  Client: {args.client}")
        print(f"  Loaded {len(rows)} contacts from {csv_basename}")
    else:
        output_path = args.output or f"{base_no_ext}_results{ext}"

        rows = read_csv(input_path)
        if not rows:
            print("No valid rows found in input CSV.")
            return

        print(f"  Loaded {len(rows)} contacts from {input_path}")

    try:
        rows = find_emails(rows, enhanced=args.enhanced)
        write_csv(rows, output_path)
    finally:
        if tee:
            print(f"  Log saved to: {log_path}")
            tee.close()


if __name__ == "__main__":
    main()
