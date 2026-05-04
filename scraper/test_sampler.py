# ============================================================
# HUDOC Roma Cases — Test Sampler
# Builds a stratified 15-case test set from cases that have
# already been classified by step 3. Run this once to produce
# test_cases.json, then use test_filter.py for iteration.
# ============================================================

# ── Imports ───────────────────────────────────────────────────
# random: used to randomly sample cases from each category
# json: reads/writes JSON files
# Path: lets us build file paths relative to this script's location
#       (so the script works regardless of which directory you run it from)

import json, random
from pathlib import Path


# ── Paths ─────────────────────────────────────────────────────
# Path(__file__) = the full path to this script file
# .parent        = the scraper/ directory
# .parent again  = the project root (one level up from scraper/)

PROJECT_ROOT = Path(__file__).parent.parent
CASES_JSON   = PROJECT_ROOT / "cases.json"
OUTPUT_FILE  = PROJECT_ROOT / "test_cases.json"


# ── Random seed ───────────────────────────────────────────────
# A fixed seed makes the sample reproducible: running this script
# twice will always produce the same 15 cases.
# Change the number if you want a different random sample.

random.seed(42)


# ── Load ──────────────────────────────────────────────────────

print(f"📂 Loading {CASES_JSON}...")
with open(CASES_JSON, encoding="utf-8") as f:
    data = json.load(f)

cases = data["cases"]
print(f"   {len(cases)} total cases\n")


# ── Filter to classifiable cases ─────────────────────────────
# We can only use cases that:
#   1. Have a confirmed classification (not None and not "no_text")
#   2. Have full text (needed so test_filter.py can run extract_sections)
#
# "not in (None, 'no_text')" means: skip unprocessed and text-missing cases.
# c.get("full_text_length") returns None or 0 if text is absent — both are falsy.

classified = [
    c for c in cases
    if c.get("is_roma_related") not in (None, "no_text")
    and c.get("full_text_length")
]

# Split into three groups by their classification
yes_cases    = [c for c in classified if c["is_roma_related"] == "yes"]
no_cases     = [c for c in classified if c["is_roma_related"] == "no"]
unsure_cases = [c for c in classified if c["is_roma_related"] == "unsure"]

print("Available for sampling:")
print(f"   yes:    {len(yes_cases)}")
print(f"   no:     {len(no_cases)}")
print(f"   unsure: {len(unsure_cases)}\n")


# ── Stratified sample ─────────────────────────────────────────
# We pick more "yes" and "no" cases as anchors (ground truth we trust),
# and include all available "unsure" cases up to 4 — these are the
# primary diagnostic targets for prompt refinement.
#
# min(n, len(group)) ensures we don't ask for more than exist.

n_yes    = min(6, len(yes_cases))
n_no     = min(5, len(no_cases))
n_unsure = min(4, len(unsure_cases))

# random.sample(population, k) picks k items without replacement
sample = (
    random.sample(yes_cases,    n_yes)    +
    random.sample(no_cases,     n_no)     +
    random.sample(unsure_cases, n_unsure)
)

# Shuffle so the output file isn't grouped by category
random.shuffle(sample)

print(f"✅ Test set: {len(sample)} cases ({n_yes} yes, {n_no} no, {n_unsure} unsure)")


# ── Save ──────────────────────────────────────────────────────
# We save the full case dicts, including full_text.
# test_filter.py needs full_text to run extract_sections().
# Estimated file size: ~750 KB (15 cases × ~50 KB average).

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(sample, f, indent=2, ensure_ascii=False)

print(f"💾 Saved to {OUTPUT_FILE}")
print(f"\nNext step: run  python scraper/test_filter.py")
