"""
Illinois IDPH Plumber License Scraper
======================================
Uses SeleniumBase (undetected + incognito) to scrape all active plumber
licenses for every city in Illinois City Names.csv.

Usage:
  python scraper.py --test "Chicago"    <- test one city
  python scraper.py                      <- run all cities
  python scraper.py --headless           <- no browser window (GitHub Actions)
"""

import argparse
import csv
import os
import time

from bs4 import BeautifulSoup
from seleniumbase import SB

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = (
    "https://ildohplmprod.glsuite.us/GLSuiteWeb/Clients/ILDOHPLM/"
    "PUBLIC/Verification/Plumber_License_Verification.aspx"
)
INPUT_CSV     = "Illinois City Names.csv"
OUTPUT_CSV    = "plumbers_output.csv"
PROGRESS_FILE = "progress.txt"

HEADERS = [
    "City_Searched",
    "Name",
    "License_Number",
    "License_Type",
    "License_Status",
    "Address",
    "Phone",
    "Employer",
    "Renewal_Date",
    "Licensed_Since",
    "CE",
    "Current_Employees",   # semicolon-separated apprentice names
]

DELAY_SEARCH = 3.0   # wait after clicking Search
DELAY_DETAIL = 1.5   # wait after opening each detail page
DELAY_CITY   = 2.0   # wait between cities


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_cities():
    cities = []
    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)           # skip header row
        for row in reader:
            if row and row[0].strip():
                cities.append(row[0].strip())
    return cities


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    return set()


def mark_done(city):
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(city + "\n")


# ── Detail page parser ────────────────────────────────────────────────────────

def parse_detail_page(html: str, city: str) -> dict:
    """
    Parse the plumber detail page.

    The detail table (id='dtgResults') has 4 <td> per row:
        [label1]  [value1]  [label2]  [value2]

    Row layout from the actual HTML:
      Row 1: Name | <name>          | License Number | <num>
      Row 2: Address | <addr>       | License Type   | <type>
      Row 3: Phone | <phone>        | License Status | <status>
      Row 4: Employer | <employer>  | Renewal Date   | <date>
      Row 5: (blank)  | (blank)     | Licensed Since | <date>
      Row 6: (blank)  | (blank)     | CE             | <ce>

    The employees table (id='dtgEmployees') lists apprentice names.
    """
    record = {h: "" for h in HEADERS}
    record["City_Searched"] = city

    soup = BeautifulSoup(html, "html.parser")

    FIELD_MAP = {
        "Name":           "Name",
        "License Number": "License_Number",
        "Address":        "Address",
        "License Type":   "License_Type",
        "Phone":          "Phone",
        "License Status": "License_Status",
        "Employer":       "Employer",
        "Renewal Date":   "Renewal_Date",
        "Licensed Since": "Licensed_Since",
        "CE":             "CE",
    }

    # ── Main info table ────────────────────────────────────────────────────
    table = soup.find("table", {"id": "dtgResults"})
    if table:
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]

            # Build (label, value) pairs from columns 0+1 and 2+3
            pairs = []
            if len(cells) >= 2:
                pairs.append((cells[0], cells[1]))
            if len(cells) >= 4:
                pairs.append((cells[2], cells[3]))

            for label, value in pairs:
                field = FIELD_MAP.get(label)
                if field and value and value != "\xa0" and not record[field]:
                    record[field] = value

    # ── Current Employees / Apprentices ────────────────────────────────────
    emp_table = soup.find("table", {"id": "dtgEmployees"})
    if emp_table:
        names = []
        for tr in emp_table.find_all("tr")[1:]:   # skip header row
            cells = tr.find_all("td")
            if cells:
                name = cells[0].get_text(strip=True)
                if name:
                    names.append(name)
        if names:
            record["Current_Employees"] = "; ".join(names)

    return record


# ── Per-city scraping ─────────────────────────────────────────────────────────

def scrape_city(sb, city_name: str) -> list:
    """
    Key insight: every name in the results table already has a full direct URL
    in its href (opens with target="_blank"). So instead of clicking links and
    managing popup windows, we just collect all the URLs and navigate to each
    one directly — much simpler and more reliable.
    """
    print(f"\n--- {city_name} ---")

    try:
        sb.open(BASE_URL)
        sb.sleep(2)

        # City field defaults to "Abbot" — clear it and type our city
        sb.clear("#txtCity")
        sb.type("#txtCity", city_name)
        sb.sleep(0.5)

        # Click Search button
        sb.click("input[name='Plumber_License_Verification_btnSearch']")
        sb.sleep(DELAY_SEARCH)

        # No results table = no matches for this city
        if not sb.is_element_present("#dtgList"):
            print(f"  No results.")
            return []

        # Collect all detail page URLs from the results table
        link_elems = sb.find_elements("#dtgList a")
        detail_urls = []
        for el in link_elems:
            href = el.get_attribute("href") or ""
            if "PLUMBER_LICENSE_DETAILS" in href.upper():
                detail_urls.append(href)

        if not detail_urls:
            print(f"  Table found but no links.")
            return []

        print(f"  Found {len(detail_urls)} plumber(s) — fetching details...")

        results = []
        for idx, url in enumerate(detail_urls):
            try:
                sb.open(url)
                sb.sleep(DELAY_DETAIL)

                record = parse_detail_page(sb.get_page_source(), city_name)
                results.append(record)
                print(f"  [{idx + 1}/{len(detail_urls)}] OK  {record.get('Name', '?')}")

                sb.sleep(0.5)

            except Exception as e:
                print(f"  [{idx + 1}] ERROR: {e}")
                continue

        return results

    except Exception as e:
        print(f"  City-level error: {e}")
        return []


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Illinois IDPH Plumber Scraper")
    parser.add_argument("--test",     metavar="CITY", default=None,
                        help="Test with one city only, e.g. --test Chicago")
    parser.add_argument("--headless", action="store_true",
                        help="Run without a visible browser window")
    args = parser.parse_args()

    # ── City list ──────────────────────────────────────────────────────────
    if args.test:
        cities = [args.test]
        print(f"TEST MODE — city: '{args.test}'")
    else:
        all_cities = load_cities()
        done       = load_progress()
        cities     = [c for c in all_cities if c not in done]
        print(f"Cities to scrape: {len(cities)}  (already done: {len(done)})")

    if not cities:
        print("Nothing to scrape — all cities already completed.")
        return

    is_new_file = not os.path.exists(OUTPUT_CSV)

    # ── Browser session ────────────────────────────────────────────────────
    with SB(uc=True, incognito=True, test=True, headless=args.headless) as sb:

        with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            if is_new_file:
                writer.writeheader()

            total = 0
            for i, city in enumerate(cities):
                print(f"[{i + 1}/{len(cities)}]", end=" ")
                records = scrape_city(sb, city)

                for r in records:
                    writer.writerow(r)
                total += len(records)

                if not args.test:
                    mark_done(city)

                # Flush to disk every 10 cities so progress isn't lost
                if (i + 1) % 10 == 0:
                    f.flush()
                    print(f"  --- Running total: {total} records ---")

                time.sleep(DELAY_CITY)

    print(f"\nDONE! {total} records saved to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
