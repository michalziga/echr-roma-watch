"""
Re-run the step4 filter on a specific list of item IDs.
Resets filter fields, runs quick_classify then the LLM if needed,
and exports YES cases to data/ — identical logic to step4_export.py.
"""

import json, time, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scraper.step3_filter import (
    SCRAPED_JSON,
    DATA_DIR,
    FILTER_MODEL,
    FILTER_SYSTEM_PROMPT,
    DELAY,
    quick_classify,
    extract_sections,
    parse_filter_response,
    call_openai,
    save_progress,
)

# ── Target cases ──────────────────────────────────────────────────────────────

TARGET_IDS = [
    "001-234411", "001-228992", "001-220954", "001-219209", "001-217255",
    "001-187203", "001-185124", "001-185152", "001-171994", "001-171508",
    "001-170054", "001-169047", "001-163938", "001-162117",
]

# ── Load data ─────────────────────────────────────────────────────────────────

print(f"📂 Loading {SCRAPED_JSON.name}...")
with open(SCRAPED_JSON, encoding="utf-8") as f:
    data = json.load(f)

cases      = data["cases"]
by_id      = {c["itemid"]: c for c in cases}
to_process = [by_id[i] for i in TARGET_IDS if i in by_id]

missing = [i for i in TARGET_IDS if i not in by_id]
if missing:
    print(f"⚠️  Not found in dataset: {missing}")

print(f"   {len(cases)} cases loaded — re-filtering {len(to_process)} targets\n")

# ── French fallback index ─────────────────────────────────────────────────────

french_by_appno = {
    c["app_no"]: c
    for c in cases
    if c.get("language") == "FRE" and c.get("full_text") and c.get("app_no")
}

# ── Reset filter fields ───────────────────────────────────────────────────────

for case in to_process:
    case["is_roma_related"]      = None
    case["filter_reason"]        = None
    case["text_source_language"] = None
    case["filtered_at"]          = None
    case["refiltered_at"]        = None

# ── Processing loop ───────────────────────────────────────────────────────────

batch_results = []

for i, case in enumerate(to_process, 1):
    item_id = case["itemid"]
    title   = case["title"][:60]
    print(f"[{i}/{len(to_process)}] {item_id} | {title}")

    full_text        = case.get("full_text")
    text_source_lang = case.get("language", "ENG")

    if not full_text and case.get("language") != "FRE":
        app_no = case.get("app_no", "")
        french = french_by_appno.get(app_no)
        if french:
            full_text        = french.get("full_text")
            text_source_lang = "FRE"
            print(f"    ℹ️  Using French sibling as fallback")

    if not full_text:
        print(f"    ⚠️  No text — marking as no_text")
        now = datetime.now(timezone.utc).isoformat()
        case["is_roma_related"]      = "no_text"
        case["filter_reason"]        = "No full text available in English or French"
        case["text_source_language"] = case.get("language", "ENG")
        case["filtered_at"]          = now
        case["refiltered_at"]        = now
        save_progress(data)
        batch_results.append((item_id, title, "no_text", "No text available"))
        continue

    # Deterministic pre-pass on full text
    pre_result = quick_classify(full_text)
    if pre_result:
        decision, reason = pre_result
        print(f"    ⚡ Hard rule fired → {decision.upper()}: {reason[:80]}")
        now = datetime.now(timezone.utc).isoformat()
        case["is_roma_related"]      = decision
        case["filter_reason"]        = reason
        case["text_source_language"] = text_source_lang
        case["filtered_at"]          = now
        case["refiltered_at"]        = now
        data_path = DATA_DIR / f"{item_id}.json"
        if decision == "yes":
            with open(data_path, "w", encoding="utf-8") as f:
                json.dump(case, f, indent=2, ensure_ascii=False)
            print(f"    ✅ YES → exported to data/{item_id}.json")
        else:
            if data_path.exists():
                data_path.unlink()
                print(f"    → {decision.upper()} (removed stale file from data/)")
            else:
                print(f"    → {decision.upper()}")
        save_progress(data)
        batch_results.append((item_id, title, decision, reason))
        time.sleep(DELAY)
        continue

    extracted_text = extract_sections(full_text)

    user_message = f"""CASE METADATA:
Title:      {case.get('title', '')}
Date:       {case.get('date', '')}
Country:    {case.get('country', '')}
Articles:   {case.get('articles', '')}
Conclusion: {case.get('conclusion', '')}
Violation:  {case.get('violation', '')}
Importance: {case.get('importance', '')}
Text language: {text_source_lang}

CASE TEXT (extracted sections):
{extracted_text}"""

    print(f"    🔍 Running filter...")
    filter_response = call_openai(FILTER_SYSTEM_PROMPT, user_message, FILTER_MODEL)

    if not filter_response:
        print(f"    ❌ API call failed — skipping")
        batch_results.append((item_id, title, "FAILED", "API error"))
        time.sleep(DELAY)
        continue

    decision, reason = parse_filter_response(filter_response)

    now = datetime.now(timezone.utc).isoformat()
    case["is_roma_related"]      = decision
    case["filter_reason"]        = reason
    case["text_source_language"] = text_source_lang
    case["filtered_at"]          = now
    case["refiltered_at"]        = now

    data_path = DATA_DIR / f"{item_id}.json"

    if decision == "yes":
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(case, f, indent=2, ensure_ascii=False)
        print(f"    ✅ YES → exported to data/{item_id}.json")
    else:
        if data_path.exists():
            data_path.unlink()
            print(f"    → {decision.upper()} (removed stale file from data/)")
        else:
            print(f"    → {decision.upper()}")

    save_progress(data)
    batch_results.append((item_id, title, decision, reason))
    time.sleep(DELAY)

# ── Summary ───────────────────────────────────────────────────────────────────

LABELS = {
    "yes":     "✅ YES   ",
    "no":      "❌ NO    ",
    "unsure":  "❓ UNSURE",
    "no_text": "⚠️  NO TEXT",
    "FAILED":  "💥 FAILED",
}

print(f"\n{'─'*60}")
print(f"Done — {len(batch_results)} cases re-filtered:\n")
for item_id, title, decision, reason in batch_results:
    label = LABELS.get(decision, decision)
    print(f"  {label}  {item_id}  {title[:45]}")
    if reason and decision not in ("no_text", "FAILED"):
        print(f"           {reason[:90]}")

yes_count = sum(1 for _ in DATA_DIR.glob("*.json"))
print(f"\n💾 Results saved to {SCRAPED_JSON.name}")
print(f"   {yes_count} confirmed Roma cases in data/")
