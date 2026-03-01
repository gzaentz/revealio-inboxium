# Revealio Inboxium

Finds verified business email addresses from a list of names and company domains. Drop a CSV with contact names and domains, and the tool generates 26 common email pattern candidates per contact, validates them against the Truelist API, and returns only verified results.

## How It Works

```
Input CSV (name + domain)
        |
        v
  Domain Pre-check
  - MX record lookup (skip domains with no mail server)
  - Catch-all detection (skip domains that accept any address)
        |
        v
  Candidate Generation
  - 26 patterns per contact (first.last@, flast@, f.last@, etc.)
  - Enhanced mode: scrape company websites for published emails first
        |
        v
  Batch Verification (Truelist API)
  - All candidates submitted in a single batch
  - Only emails with "email_ok" status are kept
        |
        v
  Output CSV (name, domain, verified email, confidence)
```

## Quick Start

```bash
pip install -r requirements.txt
cp env.example .env
# Add your API keys to .env
```

### Run with client organization (recommended)

```bash
python email_finder.py contacts.csv -c "Acme Corp"
```

This creates a structured folder under `clients/`:

```
clients/
  acme_corp/
    input/      <- your original CSV is moved here
    output/     <- results CSV written here
    logs/       <- timestamped log of the full run
```

### Run standalone (no client folder)

```bash
python email_finder.py contacts.csv
python email_finder.py contacts.csv -o custom_output.csv
```

### Enhanced mode (website scraping)

```bash
python email_finder.py contacts.csv -c "Acme Corp" --enhanced
```

Scrapes company homepages, contact pages, and team pages for published emails before generating pattern candidates. Uses those to prioritize likely formats. Optionally queries Google via ScrapingDog for additional matches.

## Input CSV Format

The CSV needs two columns (header names are flexible):

| Accepted name headers                                        | Accepted domain headers                                 |
| ------------------------------------------------------------ | ------------------------------------------------------- |
| `full_name`, `fullname`, `name`, `full name`, `contact name` | `domain`, `website`, `company_domain`, `company domain` |

```csv
full_name,domain
John Doe,example.com
Jane Smith,acme.io
```

## Output CSV Format

```csv
full_name,domain,email,email_confidence
John Doe,example.com,john.doe@example.com,verified
Jane Smith,acme.io,jane@acme.io,verified
Bad Domain,nodomain.fake,,
```

Only two confidence levels appear in output:

- **verified** -- Truelist confirmed the mailbox exists
- **website-sourced** -- email found published on the company's website (enhanced mode only)

Contacts where no email could be verified get an empty `email` cell.

## API Keys

Create a `.env` file (see `env.example`):

| Key                   | Required | Source                                         | Purpose                             |
| --------------------- | -------- | ---------------------------------------------- | ----------------------------------- |
| `TRUELIST_API_KEY`    | Yes      | [truelist.io](https://truelist.io)             | Email verification                  |
| `SCRAPINGDOG_API_KEY` | No       | [scrapingdog.com](https://www.scrapingdog.com) | Google email search (enhanced mode) |

## Client Data Privacy

All client data lives under `clients/` which is gitignored. The `.gitignore` also excludes `*.csv` files from the root directory. No input data, output results, or run logs will ever be committed to the repository.

## CLI Reference

```
usage: email_finder.py [-h] [-o OUTPUT] [-c CLIENT] [--enhanced] input_csv

positional arguments:
  input_csv             Path to input CSV with full_name and domain columns

options:
  -h, --help            show this help message and exit
  -o, --output OUTPUT   Output CSV path (default: <input>_results.csv)
  -c, --client CLIENT   Client name. Creates clients/<name>/input/, output/,
                         and logs/ folders.
  --enhanced            Enable website scraping to detect email patterns
                         before guessing
```

```
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀ ⠀ ⢀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀  ⠀⠀⠀⣠⠋⠳⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀ ⠀ ⠀⠀⡴⠁⢀⠀⠹⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀ ⠀ ⠀⠀⡼⠀⡀⠈⡀⠀⢱⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀  ⠀⠀⣼⠀⠀⡇⠀⢡⠀⠀⢣⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀ ⠀ ⠀⣸⠁⠀⢀⠃⠀⠸⡀⠀⠈⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀ ⠀ ⢠⠇⠀⡀⢸⠀⡀⠀⡇⠀⠀⢹⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀  ⢀⡟⠀⠀⡇⡎⠀⢡⠀⢸⠀⡀⠘⡆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀  ⣀⣀⣸⣗⣀⣀⣷⣃⣀⣈⣀⣘⣀⣃⣀⣹⣀⣀⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⣠⡶⠛⠉⠀⠀⠀⠀⠀⠀⠀⠈⠙⠻⢦⡀⠀⢀⣴⠞⠋ ⠉⠙⠛⠶⣤⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⢀⡾⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠹⣶⠋⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠻⣦⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⢠⡟⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⣀⣀⣀⣀⣀⠀⠸⣇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⢷⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠘⠀⠀⠀⠀⠀⠀⢀⣴⠖⠛⠋⠉⠉⠉⠉⠉⠉⠙⠛⠻⢦⣄⠀⠀⣀⣠⣤⣤⣤⣤⣤⣄⣀⢷⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⢠⡾⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⢻⣏⠉⠀⠀⠀⠀⠀⠀⠈⠉⠙⠲⣷⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⢷⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⠻⣤⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⣤⣀⣀⣀⣀⣤⣄⣤⣤⣄⣀⣀⣤⣀⡈⢷⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡀⡈⢻⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⢀⡴⠟⠉⣉⣉⣩⣭⣽⠥⠦⣤⣌⣉⠛⠿⢦⣄⠈⠛⢶⣗⠀⠀⠀⠀⠀⢰⣞⣻⣽⣽⣭⣭⣭⣽⣷⣦⡀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⢀⡴⢋⣠⠾⠛⠉⠀⠀⠀⠀⠀⠀⠀⠀⠉⠻⢦⣄⡙⢷⣄⠀⠹⣧⡀⠀⢀⡶⠟⣫⣭⢿⡿⠿⠿⠷⣦⡉⢻⣿⡄⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⢻⣧⣾⣁⣤⡤⠴⠶⠖⣶⣶⣶⣶⣶⣶⣶⣶⠒⠛⠛⠳⣿⢷⣤⢺⣇⠀⠉⣢⣿⣿⣿⣾⣶⣶⣦⣄⡀⠹⣾⡏⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠉⠙⡳⠶⣄⣼⣿⣷⢾⣿⡟⠋⠛⣿⡇⠀⠀⠀⠈⣷⠘⢷⡟⢀⡾⣿⣿⣩⣿⣿⠿⢿⣧⠈⠙⠳⢾⣇⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠈⠻⣦⡈⠻⢿⣿⣼⣿⣷⣦⣾⣿⠇⠀⠀⠀⠀⠘⣧⢸⢣⡟⠀⣿⣿⣟⣿⣿⣤⣾⡿⠀⢀⣴⢿⡟⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠻⣦⣀⠀⠉⠉⠛⠿⠿⠿⢤⣤⣤⡴⠖⠛⢉⣿⠈⢹⡓⢿⠿⠿⠿⠿⠿⠿⠷⠞⠋⣡⡾⠁⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠙⠛⠛⠛⠛⠳⠶⠤⠴⠶⢤⣴⠾⠋⠁⠀⠈⠛⠶⣤⡤⠤⠴⠆⢀⡾⢷⣾⢯⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⣴⡶⠶⠖⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣠⠞⠁⠀⠀⠀⠀⠀⠀⠀⠈⢳⣄⠀⠀⠛⠛⠛⠁⠀⢻⣆⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⣀⣠⣴⡶⠾⠆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⣤⠶⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⢧⠀⠀⠀⠀⠀⠀⠀⣿⣦⠀⠀⠀⠀⠀⠀⠀
⠀⣼⢏⣿⠛⠿⠶⢤⣄⣀⡀⠀⠀⠀⠀⠐⠻⠛⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣸⠇⢸⡇⠀⠀⠀⠀⠀⠀
⠀⠈⠘⣿⣄⠘⢷⣄⣀⠉⠙⠛⠒⠲⠶⣤⣤⣄⣀⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡾⠃⣠⡟⠁⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠈⠙⠷⣄⣈⠉⠙⠳⠶⢤⣄⣀⡀⠀⠀⠉⠉⠉⠛⠛⠳⠶⠶⠶⠶⠶⠶⠤⢤⣤⣤⣤⣤⣤⣤⡤⠶⠾⠋⣠⣾⡋⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠙⠛⢦⣄⡀⠀⠈⠉⠙⠛⠛⠛⠛⠛⠛⠶⢦⣤⣤⣤⣀⣀⣀⣀⣀⣀⣀⣀⣀⣀⣀⣀⣀⣤⠾⠋⢸⡇⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠙⠳⢶⣄⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠀⠀⣰⠟⠁⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠛⠶⠦⠤⠤⢤⣄⣀⣀⣀⣀⣀⣀⣀⣀⣀⣀⣀⣠⣤⣄⣀⣀⣠⡤⠞⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⠉⣉⣭⣉⠁⠀⣠⡿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣠⡶⠛⠉⠉⠙⢷⣴⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⡴⠛⠁⠀⠀⠀⠀⠀⠀⠹⠦⣄⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⡶⠒⠳⣦⠾⠛⢷⡄⠀⠀⣠⡴⢶⣤⣄⠀⣠⡌⠙⠷⣄⡀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢿⣄⠀⠀⠹⣦⣠⣾⣃⡴⠟⢁⡼⢋⣴⣯⠞⠋⠀⠀⠀⠈⠻⣆⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠻⣦⡀⠀⠈⠉⢿⠁⢠⡼⣋⡴⠟⠋⠁⠀⠀⠀⠀⠀⠀⠀⠙⢷⡄⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢷⡄⠀⠀⢸⣶⢋⣼⠋⠀⠀⠀⠀⣀⡴⠟⠀⠀⠀⠀⠀⠀⢻⣄⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⡇⠀⠀⠀⠙⠿⣧⡀⠀⠀⠀⣴⠏⠀⠀⠀⢀⣴⠆⠀⢀⠀⠻⣆⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣼⣇⠀⠀⠀⠀⠀⠈⠻⣦⣤⣼⠃⠀⠀⢀⣠⠞⠁⠀⣠⡾⠀⠀⠻⡆
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠟⠃⠹⠗⠀⠀⠀⠀⠀⠀⠀⠀⠙⠓⠀⠀⠾⠃⠀⠀⠸⠋⠀⠀⠀⠀⠿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
```
