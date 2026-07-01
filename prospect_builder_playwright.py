#!/usr/bin/env python3
"""
prospect_builder_playwright.py
==============================
Lagos SME Prospect Collector  -  No API Key Required

Collects publicly listed business contact info from Google Maps
and business websites, exports to a CSV ready for outreach.

SETUP (run these once before first use):
    pip install playwright beautifulsoup4 requests
    playwright install chromium

USAGE:
    python3 prospect_builder_playwright.py

    Optional flags:
      --headless          run browser invisibly (default is visible so you can watch)
      --max 15            max results per query (default 20)
      --out myfile.csv    custom output filename
"""

import csv
import time
import re
import sys
import argparse
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("\nMissing dependency. Run:\n  pip install playwright\n  playwright install chromium\n")
    sys.exit(1)


# =============================================================================
#  CONFIG  -  Edit this section to change what you search for
# =============================================================================

OUTPUT_FILE = "lagos_prospects.csv"

SEARCH_QUERIES = [
    # -- Schools (best first target sector per our strategy) --
    "private secondary school Lekki Lagos",
    "private secondary school Victoria Island Lagos",
    "private secondary school Ikoyi Lagos",
    "private secondary school Ikeja Lagos",
    "private secondary school Ajah Lagos",
    "private secondary school Yaba Lagos",
    "private secondary school Surulere Lagos",

    # -- Uncomment these when you move to the next sector --
    # "private hospital Victoria Island Lagos",
    # "private hospital Lekki Lagos",
    # "private clinic Ikeja Lagos",
    # "real estate company Lekki Lagos",
    # "real estate company Victoria Island Lagos",
    # "private university Lagos",
]

MAX_RESULTS_PER_QUERY = 20   # Google Maps shows ~20 per search

# Extra paths to check on each website for email addresses
CONTACT_PATHS = [
    "/contact",
    "/contact-us",
    "/contact.html",
    "/contact-us.html",
    "/about",
    "/about-us",
    "/about.html",
    "/admissions",
    "/reach-us",
    "/get-in-touch",
]

# Strings that mark an email as useless
EMAIL_BLACKLIST = {
    "noreply", "no-reply", "donotreply", "example.com",
    "test@", "sample@", "yourname@", "name@", "user@",
}

# Delay between requests (seconds). Don't reduce this below 1.5
MAP_DELAY    = 2.5   # between Google Maps page loads
RESULT_DELAY = 2.0   # between clicking individual results
SITE_DELAY   = 0.4   # between website page requests

# =============================================================================
#  EMAIL UTILITIES
# =============================================================================

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def is_valid_email(email: str) -> bool:
    email_lower = email.lower()
    if any(bl in email_lower for bl in EMAIL_BLACKLIST):
        return False
    bad_ext = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".woff")
    if any(email_lower.endswith(x) for x in bad_ext):
        return False
    parts = email_lower.split("@")
    return len(parts) == 2 and "." in parts[1]


def extract_emails_from_html(html: str) -> set:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ")
    emails = set(EMAIL_RE.findall(text))
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if href.lower().startswith("mailto:"):
            email = href[7:].split("?")[0].strip()
            if email:
                emails.add(email)
    return {e for e in emails if is_valid_email(e)}


def scrape_website_emails(website_url: str) -> set:
    if not website_url:
        return set()
    parsed     = urlparse(website_url)
    base       = f"{parsed.scheme}://{parsed.netloc}"
    urls       = [website_url] + [urljoin(base, p) for p in CONTACT_PATHS]
    visited    = set()
    all_emails = set()

    for url in urls:
        if url in visited:
            continue
        visited.add(url)
        try:
            resp = requests.get(
                url, headers=REQUEST_HEADERS,
                timeout=8, allow_redirects=True,
            )
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and "text/html" in ct:
                all_emails.update(extract_emails_from_html(resp.text))
            time.sleep(SITE_DELAY)
        except Exception:
            continue

    return all_emails


# =============================================================================
#  GOOGLE MAPS SCRAPER
# =============================================================================

def dismiss_consent(page) -> None:
    """Click through any cookie / consent dialogs that may appear."""
    consent_selectors = [
        'button[aria-label*="Reject"]',
        'button[aria-label*="Accept all"]',
        'button:has-text("Reject all")',
        'button:has-text("Accept all")',
        'form:nth-child(2) button',   # Google's consent form layout
    ]
    for sel in consent_selectors:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                time.sleep(1)
                return
        except Exception:
            continue


def get_text(el, attribute: str = None) -> str:
    """Safely extract text or attribute value from a Playwright element."""
    if not el:
        return ""
    try:
        if attribute:
            val = el.get_attribute(attribute) or ""
            return val.strip()
        return el.inner_text().strip()
    except Exception:
        return ""


def collect_place_urls(page, query: str, max_results: int) -> list:
    """
    Load a Google Maps search page, scroll the results sidebar,
    and return a deduplicated list of /maps/place/ URLs.
    """
    search_url = (
        "https://www.google.com/maps/search/"
        + query.replace(" ", "+")
    )
    print(f"    Loading: {search_url}")

    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
    except PWTimeout:
        print("    Page load timed out. Skipping query.")
        return []

    time.sleep(MAP_DELAY)
    dismiss_consent(page)

    # Wait for results feed
    try:
        page.wait_for_selector('div[role="feed"]', timeout=15_000)
    except PWTimeout:
        print("    Results feed not found. Skipping query.")
        return []

    # Scroll the sidebar to load more results
    for _ in range(4):
        try:
            page.evaluate("""
                const feed = document.querySelector('div[role="feed"]');
                if (feed) feed.scrollTop = feed.scrollHeight;
            """)
            time.sleep(1.8)
        except Exception:
            break

    # Collect all unique place URLs from the feed
    links = page.query_selector_all('div[role="feed"] a[href*="/maps/place/"]')
    seen  = set()
    urls  = []
    for link in links:
        href = get_text(link, "href")
        if href and href not in seen:
            seen.add(href)
            urls.append(href)
        if len(urls) >= max_results:
            break

    print(f"    Collected {len(urls)} place URLs.")
    return urls


def scrape_place_page(page, url: str) -> dict:
    """
    Navigate to a single Google Maps place page and extract
    name, address, phone number, and website.
    Uses multiple selector fallbacks since Google changes DOM periodically.
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        time.sleep(RESULT_DELAY)
        dismiss_consent(page)
    except PWTimeout:
        return {}

    # --- Name ---
    name = ""
    for sel in [
        'h1.DUwDvf',
        'h1[class*="fontHeadlineLarge"]',
        'h1[class*="fontHeadline"]',
        'h1',
    ]:
        el = page.query_selector(sel)
        if el:
            name = get_text(el)
            break

    if not name:
        return {}

    # --- Address ---
    address = ""
    for sel in [
        'button[data-item-id="address"]',
        '[data-tooltip="Copy address"]',
        'button[aria-label*="Address:"]',
    ]:
        el = page.query_selector(sel)
        if el:
            raw = get_text(el, "aria-label")
            address = re.sub(r"^Address:\s*", "", raw).strip()
            if not address:
                address = get_text(el)
            break

    # --- Phone ---
    phone = ""
    for sel in [
        'button[data-item-id*="phone:tel"]',
        '[data-tooltip="Copy phone number"]',
        'button[aria-label*="Phone:"]',
    ]:
        el = page.query_selector(sel)
        if el:
            raw = get_text(el, "aria-label")
            phone = re.sub(r"^Phone:\s*", "", raw).strip()
            if not phone:
                phone = get_text(el)
            break

    # --- Website ---
    website = ""
    for sel in [
        'a[data-item-id="authority"]',
        'a[aria-label*="website"]',
        'a[aria-label*="Website"]',
    ]:
        el = page.query_selector(sel)
        if el:
            website = get_text(el, "href")
            break

    return {
        "name":    name,
        "address": address,
        "phone":   phone,
        "website": website,
    }


# =============================================================================
#  CSV EXPORT
# =============================================================================

CSV_COLUMNS = ["name", "address", "phone", "website", "emails",
               "search_query", "notes"]


def export_csv(places: list, output_file: str) -> None:
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for place in places:
            writer.writerow({col: place.get(col, "") for col in CSV_COLUMNS})
    print(f"\n  Saved to: {output_file}")


# =============================================================================
#  MAIN
# =============================================================================

def print_summary(places: list, output_file: str) -> None:
    total        = len(places)
    with_email   = sum(1 for p in places if p.get("emails"))
    with_phone   = sum(1 for p in places if p.get("phone"))
    with_website = sum(1 for p in places if p.get("website"))
    no_contact   = sum(1 for p in places if not p.get("emails") and not p.get("phone"))

    print("\n" + "=" * 58)
    print("  DONE")
    print("=" * 58)
    print(f"  Total businesses found   : {total}")
    print(f"  With email address       : {with_email}")
    print(f"  With phone number        : {with_phone}")
    print(f"  With website             : {with_website}")
    print(f"  No contact info at all   : {no_contact}  (call to find admin name)")
    print(f"\n  Output: {output_file}")
    print("=" * 58)
    print()
    print("Next steps:")
    print("  1. Open the CSV in Excel or Google Sheets")
    print("  2. Sort by 'emails' first, work those rows first")
    print("  3. Rows with only a phone: call, ask for admin manager name")
    print("  4. LinkedIn lookup: only for the ones that reply to outreach")
    print()


def parse_args():
    p = argparse.ArgumentParser(description="Lagos SME Prospect Builder (Playwright)")
    p.add_argument("--headless", action="store_true",
                   help="Run browser in headless mode (invisible)")
    p.add_argument("--max", type=int, default=MAX_RESULTS_PER_QUERY,
                   help=f"Max results per query (default {MAX_RESULTS_PER_QUERY})")
    p.add_argument("--out", type=str, default=OUTPUT_FILE,
                   help=f"Output CSV filename (default {OUTPUT_FILE})")
    return p.parse_args()


def main():
    args       = parse_args()
    max_res    = args.max
    output     = args.out
    headless   = args.headless

    print("=" * 58)
    print("  PROSPECT BUILDER  -  Lagos SME Security Outreach")
    print("  Mode: Playwright (no API key)")
    print("=" * 58)
    if not headless:
        print("  Browser will open visibly so you can see what's happening.")
        print("  Pass --headless to run in the background.\n")

    all_places = []
    seen_names = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx     = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = ctx.new_page()

        # ---------- Stage 1: Google Maps ----------
        print("\n[Stage 1/3]  Collecting businesses from Google Maps...\n")

        for query in SEARCH_QUERIES:
            print(f"  Query: \"{query}\"")
            place_urls = collect_place_urls(page, query, max_res)

            for i, url in enumerate(place_urls, 1):
                details = scrape_place_page(page, url)
                if not details or not details.get("name"):
                    continue

                name = details["name"]
                if name in seen_names:
                    continue
                seen_names.add(name)

                entry = {
                    "name":         name,
                    "address":      details.get("address", ""),
                    "phone":        details.get("phone", ""),
                    "website":      details.get("website", ""),
                    "emails":       "",
                    "search_query": query,
                    "notes":        "",
                }
                all_places.append(entry)
                print(f"    [{i}/{len(place_urls)}] {name}")

            time.sleep(MAP_DELAY)

        browser.close()

    print(f"\n  Total unique businesses: {len(all_places)}")

    if not all_places:
        print("\n  Nothing found. Check your internet connection and try again.")
        sys.exit(1)

    # ---------- Stage 2: Website email scraping ----------
    print("\n[Stage 2/3]  Scraping websites for email addresses...\n")

    for i, place in enumerate(all_places, 1):
        name    = place["name"]
        website = place.get("website", "")
        tag     = f"  [{i:>2}/{len(all_places)}]"

        if website:
            print(f"{tag} Scanning {name}...")
            emails = scrape_website_emails(website)
            place["emails"] = ", ".join(sorted(emails)) if emails else ""
            if emails:
                print(f"         Found: {place['emails']}")
        else:
            print(f"{tag} {name}  (no website)")

    # ---------- Stage 3: Export ----------
    print("\n[Stage 3/3]  Writing CSV...")
    export_csv(all_places, output)
    print_summary(all_places, output)


if __name__ == "__main__":
    main()
