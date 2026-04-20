# ============================================================
# HUDOC Roma Cases Scraper — Step 1
# Google Colab ready — just run the cell
# ============================================================

import requests, json, time
from datetime import datetime, timezone

# ── Settings ─────────────────────────────────────────────────

DATE_FROM = "1996-09-01"
DATE_TO   = datetime.utcnow().strftime("%Y-%m-%d")  # current date in UTC
PAGE_SIZE = 100
OUTPUT_FILE = "cases.json"

QUERY = (
    'contentsitename:ECHR'
    ' AND (NOT (doctype=PR OR doctype=HFCOMOLD OR doctype=HECOMOLD))'
    ' AND ((documentcollectionid2="JUDGMENTS") OR (documentcollectionid2="DECISIONS"))'
    ' AND (Roma OR Gypsy OR Sinti OR Travellers)'
    ' AND (languageisocode="ENG" OR languageisocode="FRE")'   # ← add this

    f' AND kpdate>="{DATE_FROM}T00:00:00.0Z"'
    f' AND kpdate<="{DATE_TO}T00:00:00.0Z"'
)

SELECT = (
    "itemid,docname,appno,kpdate,"
    "respondent,documentcollectionid2,doctypebranch,"
    "conclusion,violation,nonviolation,article,importance,ecli"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://hudoc.echr.coe.int/",
}


# ── Fetch one page ────────────────────────────────────────────

def fetch_page(start):
    resp = requests.get(
        "https://hudoc.echr.coe.int/app/query/results",
        params={
            "query":          QUERY,
            "select":         SELECT,
            "sort":           "kpdate Descending",
            "rankingModelId": "22222222-ffff-0000-0000-000000000000",
            "start":          start,
            "length":         PAGE_SIZE,
        },
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ── Parse response ────────────────────────────────────────────
#
# Real HUDOC response shape (confirmed from debug):
# {
#   "resultcount": 1260,
#   "results": [
#     { "columns": { "itemid": "001-xx", "docname": "...", ... } },
#     { "columns": { ... } },
#     ...
#   ]
# }
# Each element in "results" is {"columns": {field: value, ...}}

def parse(data):
    total   = data.get("resultcount", 0)
    results = data.get("results", [])

    cases = []
    for row in results:
        s       = row.get("columns", {})   # ← the fix: data lives in "columns"
        item_id = s.get("itemid", "")
        cases.append({
            "itemid":       item_id,
            "title":        s.get("docname", ""),
            "app_no":       s.get("appno", ""),
            "date":         (s.get("kpdate") or "")[:10],
            "country":      s.get("respondent", ""),
            "collection":   s.get("documentcollectionid2", ""),
            "doc_type":     s.get("doctypebranch", ""),
            "importance":   s.get("importance", ""),
            "conclusion":   s.get("conclusion", ""),
            "articles":     s.get("article", ""),
            "violation":    s.get("violation", ""),
            "nonviolation": s.get("nonviolation", ""),
            "ecli":         s.get("ecli", ""),
            "url":          f"https://hudoc.echr.coe.int/eng?i={item_id}",
            "text_url":     f"https://hudoc.echr.coe.int/app/conversion/docx/html/body?library=ECHR&id={item_id}&filename=document.html",
        })

    return cases, total


# ── Collect all pages ─────────────────────────────────────────

def collect_all():
    all_cases = []
    start     = 0
    total     = None

    while True:
        data          = fetch_page(start)
        cases, total  = parse(data)

        print(f"  Page start={start} → {len(cases)} cases (total: {total})")

        if not cases:
            break

        all_cases.extend(cases)

        if len(all_cases) >= total or len(cases) < PAGE_SIZE:
            break

        start += PAGE_SIZE
        time.sleep(0.5)

    return all_cases, total


# ── Run ───────────────────────────────────────────────────────

print("🔍 Scraping HUDOC — Roma / Gypsy / Sinti / Travellers cases")
print(f"   Date range: {DATE_FROM} → {DATE_TO}\n")

cases, total = collect_all()

print(f"\n✅ Done! {len(cases)} / {total} cases collected.")

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump({
        "scraped_at":  datetime.now(timezone.utc).isoformat(),
        "total_cases": len(cases),
        "date_from":   DATE_FROM,
        "date_to":     DATE_TO,
        "cases":       cases,
    }, f, indent=2, ensure_ascii=False)

print(f"💾 Saved to {OUTPUT_FILE}")

# Country breakdown
from collections import Counter
countries = Counter(
    c["country"] for c in cases if c["country"]
)
print("\n📊 Top respondent countries:")
for country, n in countries.most_common(10):
    print(f"   {country}: {n}")
