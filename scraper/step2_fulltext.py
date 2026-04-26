# ============================================================
# HUDOC Roma Cases Scraper — Step 2
# Fetches full text for each case from Step 1
# Google Colab ready — just run the cell
# ============================================================

import requests, json, time, os, re
from datetime import datetime, timezone
from pathlib import Path

# ── Settings ─────────────────────────────────────────────────

# ▼▼▼ REMOVED ▼▼▼ ────────────────────────────────────────────
# OUTPUT_DIR = "cases"
# The original script saved each case as a separate file inside
# a "cases" folder. We removed that — everything stays in cases.json.
# ▲▲▲ END REMOVED ▲▲▲ ────────────────────────────────────────

INPUT_FILE = "/Users/michalziga/Documents/GitHub/echr-roma-watch/cases.json"
DELAY       = 0.8                # seconds between requests (be polite)
MAX_RETRIES = 3                  # retries on network error

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Referer": "https://hudoc.echr.coe.int/",
}


# ── HTML → plain text ─────────────────────────────────────────

def strip_html(html):
    if not html:
        return html

    # Remove <style>...</style> blocks entirely
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL)

    # Remove all HTML tags like <p>, <div>, <span> etc.
    text = re.sub(r"<[^>]+>", " ", text)

    # Remove inline CSS class definitions left in body text
    # e.g. ".sDD6737AE { font-size:11pt }"
    text = re.sub(r"\.[a-zA-Z0-9]+\s*\{[^}]*\}", " ", text)

    # Decode HTML entities
    text = text.replace("&#xa0;", " ")   # non-breaking space (hex)
    text = text.replace("&nbsp;",  " ")  # non-breaking space (named)
    text = text.replace("&amp;",   "&")
    text = text.replace("&lt;",    "<")
    text = text.replace("&gt;",    ">")
    text = text.replace("&#xd;",   "")   # carriage return
    text = text.replace("&#x9;",   " ")  # tab

    # Remove BOM character that sometimes appears at the start
    text = text.replace("\ufeff", "")

    # Collapse all whitespace into single spaces
    text = re.sub(r"\s+", " ", text).strip()

    return text


# ── Fetch full text for one case ──────────────────────────────

def fetch_full_text(text_url):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(text_url, headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                return strip_html(resp.text)
            if resp.status_code == 404:
                return None   # document genuinely doesn't exist
        except requests.RequestException as e:
            print(f"    ⚠️  Attempt {attempt}/{MAX_RETRIES} failed: {e}")

        time.sleep(2 ** attempt)   # back-off: 2s, 4s, 8s

    return None   # gave up

# ▼▼▼ NEW FUNCTION ▼▼▼ ────────────────────────────────────────
# The original script had a save_case() function that wrote each
# case to its own file. We replaced it with save_progress(), which
# writes the entire cases.json after every single case is fetched.
#
# Why after every case?
# If the script crashes halfway through, all the work done so far
# is already saved. Just re-run the script and it will pick up
# where it left off (skipping the already-fetched cases).
def save_progress(data):
    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ── Main ──────────────────────────────────────────────────────

# Load Step 1 output
with open(INPUT_FILE, encoding="utf-8") as f:
    data = json.load(f)

cases = data["cases"]
print(f"📂 Loaded {len(cases)} cases from {INPUT_FILE}")

already_done = sum(1 for c in cases if c.get("full_text_length") is not None)
remaining    = [c for c in cases if c.get("full_text_length") is None]

# ─────────────────────────────────────────────────────────────

print(f"✅ Already done: {already_done}")
print(f"⏳ Remaining:    {len(remaining)}\n")

# ── Fetch loop ────────────────────────────────────────────────

# ▼▼▼ NEW ▼▼▼ ─────────────────────────────────────────────────
# Build a lookup dictionary: { "001-xxxxx": <the case dict>, ... }
# This lets us find and update the right case inside data["cases"]
# using just the itemid, without looping through the whole list each time.
case_index = {c["itemid"]: c for c in data["cases"]}

failed = []

# ── Re-clean loop — OUTSIDE the fetch loop ───────────────────
print("🧹 Re-cleaning existing full_text...")
recleaned = 0

for case in cases:
    raw = case.get("full_text")
    if raw:
        cleaned = strip_html(raw)
        if cleaned != raw:
            case["full_text"]        = cleaned
            case["full_text_length"] = len(cleaned)
            recleaned += 1

if recleaned:
    save_progress(data)
    print(f"   ✅ Re-cleaned {recleaned} cases\n")
else:
    print(f"   ✅ Nothing to re-clean\n")

# ── Fetch loop — comes after ──────────────────────────────────
for i, case in enumerate(remaining, 1):
    item_id  = case["itemid"]
    title    = case["title"][:60]
    text_url = case["text_url"]

    print(f"[{i}/{len(remaining)}] {item_id} | {title}")

    full_text = fetch_full_text(text_url)
    target    = case_index[item_id]

    if full_text:
        target["full_text"]        = full_text
        target["full_text_length"] = len(full_text)
        target["fetched_at"]       = datetime.now(timezone.utc).isoformat()
        print(f"  💾 Saved ({len(full_text):,} chars)")
    else:
        target["full_text"]        = None
        target["full_text_length"] = 0
        target["fetched_at"]       = datetime.now(timezone.utc).isoformat()
        failed.append(item_id)
        print(f"  ❌ No text found")

    save_progress(data)
    time.sleep(DELAY)

# ── Summary ───────────────────────────────────────────────────

done_count = sum(1 for c in data["cases"] if c.get("full_text_length") is not None)

print(f"\n✅ Done! {done_count} / {len(cases)} cases now have full_text in {INPUT_FILE}")
print(f"❌ Failed to fetch text: {len(failed)}")

if failed:
    print("   Failed IDs:", failed[:10], "..." if len(failed) > 10 else "")
