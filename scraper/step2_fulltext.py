# ============================================================
# HUDOC Roma Cases Scraper — Step 2
# For every case in cases.json, fetches the full judgment text
# from HUDOC and stores it back into cases.json.
# ============================================================

# ── Imports ───────────────────────────────────────────────────
# requests: sends HTTP requests to download web pages
# json: reads/writes JSON files
# time: used for sleep() — pausing between requests
# os: not used directly here but kept for potential path operations
# re: regular expressions — used to clean HTML tags from the text
# datetime/timezone: for recording when each case was fetched

import requests, json, time, os, re
from datetime import datetime, timezone
from pathlib import Path


# ── Settings ─────────────────────────────────────────────────

INPUT_FILE  = "/Users/michalziga/Documents/GitHub/echr-roma-watch/cases.json"
DELAY       = 0.8    # seconds to wait between requests
MAX_RETRIES = 3      # how many times to retry a failed request before giving up

# Request headers: sent with every HTTP request.
# We mimic a real browser so HUDOC doesn't block us.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Referer": "https://hudoc.echr.coe.int/",
}


# ── HTML → plain text ─────────────────────────────────────────
# HUDOC returns the judgment as an HTML page.
# This function strips out all the HTML tags and CSS,
# leaving only the readable text content.

def strip_html(html):
    # Guard: if input is empty/None, return it unchanged
    if not html:
        return html

    # re.sub() finds all matches of a pattern and replaces them.
    # re.DOTALL makes "." match newlines too (needed for multi-line blocks).
    # Remove <style>...</style> blocks entirely (CSS definitions)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL)

    # Remove all HTML tags: <p>, <div>, <span>, etc.
    # [^>]+ means "one or more characters that are not >"
    text = re.sub(r"<[^>]+>", " ", text)

    # Remove leftover inline CSS class definitions, e.g. ".sDD6737AE { font-size:11pt }"
    text = re.sub(r"\.[a-zA-Z0-9]+\s*\{[^}]*\}", " ", text)

    # Decode HTML entities — these are special codes for characters like spaces
    text = text.replace("&#xa0;", " ")   # non-breaking space (hex encoding)
    text = text.replace("&nbsp;",  " ")  # non-breaking space (named encoding)
    text = text.replace("&amp;",   "&")  # ampersand
    text = text.replace("&lt;",    "<")  # less-than sign
    text = text.replace("&gt;",    ">")  # greater-than sign
    text = text.replace("&#xd;",   "")   # carriage return character
    text = text.replace("&#x9;",   " ")  # tab character

    # Remove BOM (Byte Order Mark) — an invisible character sometimes at file start
    text = text.replace("﻿", "")

    # Collapse all whitespace (spaces, newlines, tabs) into single spaces
    # \s+ means "one or more whitespace characters"
    text = re.sub(r"\s+", " ", text).strip()

    return text


# ── Fetch full text for one case ──────────────────────────────
# Downloads the HTML from HUDOC and returns clean plain text.
# Retries up to MAX_RETRIES times on network errors,
# with exponential back-off (waits 2s, then 4s, then 8s).

def fetch_full_text(text_url):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(text_url, headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                # Success — strip HTML and return the clean text
                return strip_html(resp.text)
            if resp.status_code == 404:
                # Document doesn't exist on HUDOC — no point retrying
                return None
        except requests.RequestException as e:
            print(f"    ⚠️  Attempt {attempt}/{MAX_RETRIES} failed: {e}")

        # 2 ** attempt = 2, 4, 8 — waits longer after each failure
        time.sleep(2 ** attempt)

    return None   # all retries exhausted


# ── Save progress ─────────────────────────────────────────────
# Writes the entire cases.json after every single case is fetched.
# This means if the script crashes or is interrupted, all work so far
# is saved — just re-run the script and it picks up where it left off.

def save_progress(data):
    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Main ──────────────────────────────────────────────────────

# Load cases.json (the output of Step 1)
with open(INPUT_FILE, encoding="utf-8") as f:
    data = json.load(f)

cases = data["cases"]   # the list of case dicts
print(f"📂 Loaded {len(cases)} cases from {INPUT_FILE}")

# Count cases already done vs still to fetch.
# full_text_length is None if not yet fetched; 0 if fetched but no text found.
already_done = sum(1 for c in cases if c.get("full_text_length") is not None)
remaining    = [c for c in cases if c.get("full_text_length") is None]

print(f"✅ Already done: {already_done}")
print(f"⏳ Remaining:    {len(remaining)}\n")

# ── Case index ────────────────────────────────────────────────
# A dictionary mapping itemid → case dict.
# This lets us look up and update a case by its ID in O(1) time,
# instead of looping through the whole list each time.
# dict comprehension: { key: value for item in iterable }
case_index = {c["itemid"]: c for c in data["cases"]}

failed = []

# ── Re-clean loop ─────────────────────────────────────────────
# If strip_html() was improved since the last run, this re-applies
# it to all existing full_text values to clean them up again.
# This runs before the fetch loop so it doesn't interfere with
# the "remaining" list (which contains only unfetched cases).

print("🧹 Re-cleaning existing full_text...")
recleaned = 0

for case in cases:
    raw = case.get("full_text")
    if raw:   # only process cases that already have text
        cleaned = strip_html(raw)
        if cleaned != raw:   # only save if something actually changed
            case["full_text"]        = cleaned
            case["full_text_length"] = len(cleaned)
            recleaned += 1

if recleaned:
    save_progress(data)
    print(f"   ✅ Re-cleaned {recleaned} cases\n")
else:
    print(f"   ✅ Nothing to re-clean\n")

# ── Fetch loop ────────────────────────────────────────────────
# Iterates over all cases that haven't been fetched yet.
# enumerate(remaining, 1) gives us (1, case), (2, case), ... for progress display.

for i, case in enumerate(remaining, 1):
    item_id  = case["itemid"]
    title    = case["title"][:60]   # truncate long titles for display
    text_url = case["text_url"]

    print(f"[{i}/{len(remaining)}] {item_id} | {title}")

    full_text = fetch_full_text(text_url)

    # Look up this case in the index so we update the object inside data["cases"]
    target = case_index[item_id]

    if full_text:
        target["full_text"]        = full_text
        target["full_text_length"] = len(full_text)   # character count
        target["fetched_at"]       = datetime.now(timezone.utc).isoformat()
        print(f"  💾 Saved ({len(full_text):,} chars)")
    else:
        # No text found — mark as 0 (not None) so we don't retry it next run
        target["full_text"]        = None
        target["full_text_length"] = 0
        target["fetched_at"]       = datetime.now(timezone.utc).isoformat()
        failed.append(item_id)
        print(f"  ❌ No text found")

    save_progress(data)
    time.sleep(DELAY)   # be polite — don't hammer the server


# ── Summary ───────────────────────────────────────────────────

done_count = sum(1 for c in data["cases"] if c.get("full_text_length") is not None)

print(f"\n✅ Done! {done_count} / {len(cases)} cases now have full_text in {INPUT_FILE}")
print(f"❌ Failed to fetch text: {len(failed)}")

if failed:
    print("   Failed IDs:", failed[:10], "..." if len(failed) > 10 else "")
