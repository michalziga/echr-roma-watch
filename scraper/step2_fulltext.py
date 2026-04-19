# ============================================================
# HUDOC Roma Cases Scraper — Step 2
# Fetches full text for each case from Step 1
# Google Colab ready — just run the cell
# ============================================================

import requests, json, time, os, re
from datetime import datetime, timezone
from pathlib import Path

# ── Settings ─────────────────────────────────────────────────

INPUT_FILE  = "cases.json"       # output from Step 1
OUTPUT_DIR  = "cases"            # one JSON file per case goes here
DELAY       = 0.8                # seconds between requests (be polite)
MAX_RETRIES = 3                  # retries on network error

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Referer": "https://hudoc.echr.coe.int/",
}


# ── HTML → plain text ─────────────────────────────────────────

def strip_html(html):
    """Remove HTML tags and clean up whitespace."""
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL)  # ← remove <style> blocks entirely
    text = re.sub(r"<[^>]+>", " ", html)           # remove tags
    text = re.sub(r"&nbsp;", " ", text)             # html entities
    text = re.sub(r"&amp;",  "&", text)
    text = re.sub(r"&lt;",   "<", text)
    text = re.sub(r"&gt;",   ">", text)
    text = re.sub(r"\s+",    " ", text).strip()     # collapse whitespace
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


# ── Save one case to its own JSON file ────────────────────────

def save_case(case, output_dir):
    item_id  = case["itemid"]
    filepath = output_dir / f"{item_id}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(case, f, indent=2, ensure_ascii=False)


# ── Main ──────────────────────────────────────────────────────

# Load Step 1 output
with open(INPUT_FILE, encoding="utf-8") as f:
    data = json.load(f)

cases = data["cases"]
print(f"📂 Loaded {len(cases)} cases from {INPUT_FILE}")

# Create output folder
output_dir = Path(OUTPUT_DIR)
output_dir.mkdir(exist_ok=True)

# Skip cases already downloaded (so we can resume if interrupted)
already_done = {p.stem for p in output_dir.glob("*.json")}
remaining    = [c for c in cases if c["itemid"] not in already_done]

print(f"✅ Already done: {len(already_done)}")
print(f"⏳ Remaining:    {len(remaining)}\n")

# ── Fetch loop ────────────────────────────────────────────────

failed = []

for i, case in enumerate(remaining, 1):
    item_id  = case["itemid"]
    title    = case["title"][:60]
    text_url = case["text_url"]

    print(f"[{i}/{len(remaining)}] {item_id} | {title}")

    full_text = fetch_full_text(text_url)

    if full_text:
        case["full_text"]        = full_text
        case["full_text_length"] = len(full_text)
        case["fetched_at"]       = datetime.now(timezone.utc).isoformat()
        save_case(case, output_dir)
        print(f"  💾 Saved ({len(full_text):,} chars)")
    else:
        case["full_text"]        = None
        case["full_text_length"] = 0
        case["fetched_at"]       = datetime.now(timezone.utc).isoformat()
        save_case(case, output_dir)   # save anyway so we don't retry forever
        failed.append(item_id)
        print(f"  ❌ No text found")

    time.sleep(DELAY)

# ── Summary ───────────────────────────────────────────────────

total_done = len(list(output_dir.glob("*.json")))

print(f"\n✅ Done! {total_done} case files in /{OUTPUT_DIR}/")
print(f"❌ Failed to fetch text: {len(failed)}")

if failed:
    print("   Failed IDs:", failed[:10], "..." if len(failed) > 10 else "")
