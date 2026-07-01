# Lagos Prospect Collector

A Python tool that searches Google Maps for businesses by keyword,
scrapes their websites for publicly listed contact email addresses,
and exports a clean CSV ready for outreach.

Works for any city and any business type, not just Lagos.

## What it collects

- Business name
- Address
- Phone number
- Website
- Publicly listed email addresses from contact pages

## Two versions

| Version | File | Requires |
|---------|------|----------|
| API (recommended) | prospect_builder.py | Google Places API key |
| No-API | prospect_builder_playwright.py | Chromium via Playwright |

## Setup (API version)

```bash
pip install requests beautifulsoup4
export GOOGLE_PLACES_API_KEY=your_key_here
python3 prospect_builder.py
```

## Setup (Playwright version)

```bash
pip install playwright beautifulsoup4 requests
playwright install chromium
python3 prospect_builder_playwright.py
```

## Customise your searches

Open either script and edit the SEARCH_QUERIES list at the top:

```python
SEARCH_QUERIES = [
    "construction company Lagos",
    "private hospital Abuja",
    "logistics company Port Harcourt",
    "accounting firm Ikeja Lagos",
]
```

Any search term that works on Google Maps works here.

## Output

CSV file with columns: name, address, phone, website, emails, search_query, notes

## Important

- Only collects publicly visible contact information
- Never commit your API key or collected CSV files to this repo
- .gitignore is already configured to block CSV and .env files

## Author

Ijilusi Precious Ayomide | Cybersecurity Consultant, Lagos
EOF
