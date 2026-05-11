# ============================================================
# HUDOC Roma Cases Scraper — Step 1
# Fetches a list of matching cases from the HUDOC search API
# and saves them to cases.json as the starting dataset.
# ============================================================

# ── Imports ───────────────────────────────────────────────────
# requests: lets Python make HTTP requests (like a browser visiting a URL)
# json: reads and writes JSON files
# time: lets us pause the script between requests (time.sleep)
# datetime/timezone: used to record when the scrape ran, in UTC time

import requests, json, time
from datetime import datetime, timezone
from pathlib import Path


# ── Settings ─────────────────────────────────────────────────
# These constants control the scrape. Change them here rather
# than hunting through the code.

DATE_FROM   = "1996-09-01"    # earliest case date to include
DATE_TO     = "2026-04-19"    # latest case date to include
PAGE_SIZE   = 100             # how many results to fetch per request (max 100)
OUTPUT_FILE = Path(__file__).parent.parent / "scraped_cases.json"


# ── Search query ──────────────────────────────────────────────
# This is the HUDOC search query, written in their query language.
# It's built by concatenating string pieces together.
# The parentheses + AND/OR are boolean logic, just like a search engine.
#
# What it means in plain English:
#   - Site: ECHR only
#   - Exclude procedural documents (press releases, old committee docs)
#   - Only JUDGMENTS and DECISIONS (not admissibility decisions or reports)
#   - Must contain at least one of: Roma, Gypsy, Sinti, Travellers
#   - Language: English or French
#   - Date range: DATE_FROM to DATE_TO

QUERY = (
    'contentsitename:ECHR'
    ' AND (NOT (doctype=PR OR doctype=HFCOMOLD OR doctype=HECOMOLD))'
    ' AND ((documentcollectionid2="JUDGMENTS") OR (documentcollectionid2="DECISIONS"))'
    ' AND (Roma OR Gypsy OR Sinti OR Travellers)'
    ' AND (languageisocode="ENG" OR languageisocode="FRE")'
    f' AND kpdate>="{DATE_FROM}T00:00:00.0Z"'
    f' AND kpdate<="{DATE_TO}T00:00:00.0Z"'
)

# ── Fields to request ─────────────────────────────────────────
# HUDOC only sends back the fields you ask for.
# This is a comma-separated list of field names.

SELECT = (
    "itemid,docname,appno,kpdate,"
    "respondent,documentcollectionid2,doctypebranch,"
    "conclusion,violation,nonviolation,article,importance,ecli,languageisocode"
)

# ── Request headers ───────────────────────────────────────────
# Headers are sent with every HTTP request.
# They identify who is making the request.
# User-Agent mimics a real browser so HUDOC doesn't block us.
# Referer tells the server we're coming from their own site.

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://hudoc.echr.coe.int/",
}


# ── Fetch one page ────────────────────────────────────────────
# HUDOC paginates results: it can only return 100 at a time.
# "start" is the offset — 0 = first 100, 100 = next 100, etc.
# We call this function repeatedly to collect all pages.

def fetch_page(start):
    # requests.get() sends an HTTP GET request to the HUDOC API URL.
    # "params" are added to the URL as query string parameters
    # (e.g. ?query=...&start=0&length=100).
    resp = requests.get(
        "https://hudoc.echr.coe.int/app/query/results",
        params={
            "query":          QUERY,
            "select":         SELECT,
            "sort":           "kpdate Descending",   # newest cases first
            "rankingModelId": "22222222-ffff-0000-0000-000000000000",
            "start":          start,
            "length":         PAGE_SIZE,
        },
        headers=HEADERS,
        timeout=30,   # give up if server doesn't respond in 30 seconds
    )
    # raise_for_status() throws an error if the request failed (e.g. 404, 500)
    resp.raise_for_status()
    # .json() parses the response body from a JSON string into a Python dict
    return resp.json()


# ── Parse response ────────────────────────────────────────────
# Transforms the raw API response into a clean list of case dicts.
#
# HUDOC response shape:
# {
#   "resultcount": 1260,
#   "results": [
#     { "columns": { "itemid": "001-xx", "docname": "...", ... } },
#     ...
#   ]
# }

def parse(data):
    # .get() safely reads a key from a dict; returns the default if key is missing
    total   = data.get("resultcount", 0)   # total number of matching cases in HUDOC
    results = data.get("results", [])      # list of result rows for this page

    cases = []

    for row in results:
        # Each row wraps its data inside a "columns" key
        s       = row.get("columns", {})
        item_id = s.get("itemid", "")

        cases.append({
            # ── Core metadata ─────────────────────────────────
            # These come directly from the HUDOC API response.
            "itemid":       item_id,
            "title":        s.get("docname", ""),
            "language":     s.get("languageisocode", ""),   # "ENG" or "FRE"
            "app_no":       s.get("appno", ""),              # application number (e.g. "1234/99")
            "date":         (s.get("kpdate") or "")[:10],   # "2003-07-15T00:00:00" → "2003-07-15"
            "country":      s.get("respondent", ""),
            "collection":   s.get("documentcollectionid2", ""),
            "doc_type":     s.get("doctypebranch", ""),
            "importance":   s.get("importance", ""),
            "conclusion":   s.get("conclusion", ""),
            "articles":     s.get("article", ""),
            "violation":    s.get("violation", ""),
            "nonviolation": s.get("nonviolation", ""),
            "ecli":         s.get("ecli", ""),

            # ── URLs ──────────────────────────────────────────
            # f-strings: Python inserts the value of {item_id} into the string.
            "url":      f"https://hudoc.echr.coe.int/eng?i={item_id}",
            "text_url": f"https://hudoc.echr.coe.int/app/conversion/docx/html/body?library=ECHR&id={item_id}&filename=document.html",

            # ── Step 2 placeholders (full text) ───────────────
            # Set to None now. Step 2 fills them in later.
            # Step 2 checks: if full_text_length is None → not fetched yet → fetch it.
            "full_text":        None,
            "full_text_length": None,
            "fetched_at":       None,

            # ── Step 4/5 placeholders (filter + summary) ──────
            # Declared here so every case has a consistent schema
            # from the start, regardless of which step added them.
            "is_roma_related":       None,   # "yes" / "no" / "unsure" / "no_text"
            "filter_reason":         None,   # one-sentence explanation from the AI
            "text_source_language":  None,   # "ENG" or "FRE" (which text step 4 used)
            "filtered_at":           None,
            "refiltered_at":         None,   # set by step 4; None = not yet processed
            "summary":               None,   # 200-word summary (yes cases only)
            "summary_model":         None,   # model used to generate the summary
            "summary_generated_at":  None,
        })

    # A function can return multiple values at once as a tuple.
    # The caller unpacks them: cases, total = parse(data)
    return cases, total


# ── Collect all pages ─────────────────────────────────────────
# HUDOC returns 100 results per page. This function keeps
# fetching the next page until it has collected all results.

def collect_all():
    all_cases = []   # accumulates cases from all pages
    start     = 0    # page offset: starts at 0, increases by PAGE_SIZE each loop
    total     = None # filled in after the first request

    while True:
        data         = fetch_page(start)
        cases, total = parse(data)

        print(f"  Page start={start} → {len(cases)} cases (total: {total})")

        # Stop if this page returned nothing
        if not cases:
            break

        # extend() appends all items from "cases" into "all_cases"
        all_cases.extend(cases)

        # Stop if we've collected everything, or if the last page was incomplete
        if len(all_cases) >= total or len(cases) < PAGE_SIZE:
            break

        start += PAGE_SIZE
        time.sleep(0.5)   # wait half a second between pages (polite to the server)

    return all_cases, total


# ── Run ───────────────────────────────────────────────────────

print("🔍 Scraping HUDOC — Roma / Gypsy / Sinti / Travellers cases")
print(f"   Date range: {DATE_FROM} → {DATE_TO}\n")

cases, total = collect_all()

print(f"\n✅ Done! {len(cases)} / {total} cases collected.")

# Load existing data so we can preserve already-fetched fields.
# Step 1 only discovers cases; steps 2–4 enrich them. If we overwrite
# without merging, the workflow re-fetches everything from scratch each run.
existing_index = {}
if OUTPUT_FILE.exists():
    with open(OUTPUT_FILE, encoding="utf-8") as f:
        existing_data = json.load(f)
    existing_index = {c["itemid"]: c for c in existing_data.get("cases", [])}
    print(f"   Loaded {len(existing_index)} existing cases for merge")

PRESERVED_FIELDS = (
    "full_text", "full_text_length", "fetched_at",
    "is_roma_related", "filter_reason", "text_source_language",
    "filtered_at", "refiltered_at",
    "summary", "summary_model", "summary_generated_at",
)

new_count = 0
for case in cases:
    existing = existing_index.get(case["itemid"])
    if existing:
        for field in PRESERVED_FIELDS:
            if existing.get(field) is not None:
                case[field] = existing[field]
    else:
        new_count += 1

print(f"   {new_count} new cases added, {len(cases) - new_count} existing cases merged")

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump({
        "scraped_at":  datetime.now(timezone.utc).isoformat(),
        "total_cases": len(cases),
        "date_from":   DATE_FROM,
        "date_to":     DATE_TO,
        "cases":       cases,
    }, f, indent=2, ensure_ascii=False)

print(f"💾 Saved to {OUTPUT_FILE}")

# ── Country breakdown ─────────────────────────────────────────
# Counter counts how many times each value appears in a list.
# c["country"] for c in cases if c["country"] is a list comprehension:
# it reads the "country" field from every case (skipping empty strings).

from collections import Counter
countries = Counter(c["country"] for c in cases if c["country"])
print("\n📊 Top respondent countries:")
for country, n in countries.most_common(10):
    print(f"   {country}: {n}")
