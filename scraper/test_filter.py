# ============================================================
# HUDOC Roma Cases — Filter Test Runner
#
# PURPOSE: Iteratively refine the semantic filter prompt.
#   1. Edit the FILTER_SYSTEM_PROMPT block below
#   2. Run this script: python scraper/test_filter.py
#   3. Read the results and adjust the prompt
#   4. Repeat until "unsure" count reaches zero (or only genuine
#      edge cases remain)
#
# This script NEVER writes to cases.json. It reads from
# test_cases.json (built by test_sampler.py) and writes results
# to test_results.json only.
# ============================================================

# ── Imports ───────────────────────────────────────────────────
import json, time, re, os
from datetime import datetime, timezone
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

# Load the .env file so os.getenv("OPENAI_API_KEY") works
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ── Paths ─────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
TEST_CASES   = PROJECT_ROOT / "test_cases.json"
TEST_RESULTS = PROJECT_ROOT / "test_results.json"

MODEL = "gpt-4o-mini-2024-07-18"
DELAY = 0.5   # shorter than production — only 15 calls per run


# ============================================================
# ▼▼▼ EDIT THIS BLOCK BETWEEN RUNS ▼▼▼
# Everything below this block is infrastructure — don't touch it.
# ============================================================

COE_DEFINITION = """The term 'Roma and Travellers' encompasses: Roma, Sinti/Manush, \
Calé, Kaale, Romanichals, Boyash/Rudari; Balkan Egyptians (Egyptians and Ashkali); \
Eastern groups (Dom, Lom and Abdal); as well as Travellers, Yenish, Gens du voyage, \
and persons who identify themselves as Gypsies."""

FILTER_SYSTEM_PROMPT = f"""You are a legal expert at the European Court of Human Rights \
specializing in Roma and Traveller minority rights, ECHR case law, and the European \
Convention on Human Rights. Your primary focus is Articles 3, 8, 14, and Protocol 1-1, \
though you also consider other articles where they reflect vulnerabilities specific to \
Roma communities (housing, education, police violence, segregation). You are familiar \
with the social and historical context of Roma in Europe and are trained to distinguish \
cases where minority identity is legally central from those where it is merely incidental.

Apply the following Council of Europe definition throughout this task:
"{COE_DEFINITION}"

---

TASK: Determine whether this ECHR case has a genuine and substantive Roma/Traveller \
dimension.

A case qualifies (→ yes) ONLY IF both conditions are met:
  1. A Roma/Traveller individual or community is the applicant OR the direct subject \
of the legal dispute
  2. Their Roma/Traveller identity is legally relevant to the alleged violation — not \
merely a background fact

Include cases involving:
  - Ethnic or racial discrimination (Art. 14)
  - Inhuman or degrading treatment (Art. 3)
  - Violations of private or family life, home, or correspondence (Art. 8)
  - Property rights or access to education (P1-1, P1-2)
  - Structural patterns: forced eviction, school segregation, police violence, \
housing exclusion, statelessness, institutional neglect

  ── NEW RULE 1: Explicit identity is sufficient ──────────────────
  If the judgment explicitly names the applicant as Roma, Sinti, Gypsy, Traveller, \
or any group covered by the CoE definition above, and the case involves any rights \
violation under the ECHR, classify as yes. Do not require that Roma identity dominate \
every section of the text — one explicit, confirmed reference in the facts is enough.

  ── NEW RULE 2: Primary violation + suspended Art. 14 examination ─
  If the Court finds a violation of a primary article (e.g. Art. 3 or Art. 8) AND \
explicitly states it is unnecessary to examine Art. 14 separately, do not treat the \
absence of an Art. 14 ruling as evidence that discrimination is absent. When a \
disproportionate impact on Roma/Travellers is argued or evident from the facts, \
classify as yes even if Art. 14 was not reached by the Court.

  ── NEW RULE 3: Institutional markers as identity signals ─────────
  If the case involves representation by, or a third-party intervention from, a \
Roma-specific legal organisation — including but not limited to the European Roma \
Rights Centre (ERRC), Minority Rights Group, Roma rights NGOs — treat this as a \
strong signal of Roma/Traveller identity relevance and classify as yes unless \
the text explicitly contradicts this.

Also include cases where Roma identity is structurally implied but not explicitly \
named — for example, cases referring to "nomadic communities", "camp dwellers", or \
"itinerant populations" where context strongly indicates Roma/Traveller applicants.
→ Mark these as unsure, not yes.

Exclude the case (→ no) if any of the following apply:
  - The applicant may be Roma, but their minority identity plays no role in the dispute
  - Roma or Travellers are mentioned only in passing, as context, or as a reference \
group — not as parties
  - The case concerns a general legal principle with no ethnic or minority-specific \
dimension

Mark as unsure if:
  - Roma identity is implied but never explicitly confirmed AND no institutional \
markers are present
  - The text is too incomplete or ambiguous to judge identity relevance
  - The violation could plausibly apply to Roma specifically, but this is not stated \
AND the applicant's identity is not confirmed
  → Unsure cases will be sent for manual review.

---

Respond strictly in English using this exact format — no additional text:

DECISION: yes / no / unsure
REASON: one sentence covering the applicable axes below:
  - Legal basis: which articles and rights are at stake
  - Social basis: what vulnerability or exclusion is present
  - Identity basis: how Roma/Traveller identity is relevant (or why it is unclear)
  - Contextual conditions: country, time period, or structural circumstances"""

# ============================================================
# ▲▲▲ END OF EDITABLE BLOCK ▲▲▲
# ============================================================


# ── Text budget (keep in sync with step3_summarize.py) ───────
# These control how much text is extracted from each judgment.
# Don't change these unless you also change them in step3.

BUDGET_FACTS     = 6_000
BUDGET_LAW       = 6_000
BUDGET_REMAINDER = 4_000

# Patterns to find the section headers inside a judgment
_SECTION_PATTERNS = {
    "facts": re.compile(
        r"(THE\s+FACTS|LES\s+FAITS|FACTS\s+OF\s+THE\s+CASE|EN\s+FAIT)",
        re.IGNORECASE,
    ),
    "law": re.compile(
        r"(THE\s+LAW|EN\s+DROIT|LEGAL\s+ASSESSMENT|IN\s+LAW|ALLEGED\s+VIOLATION)",
        re.IGNORECASE,
    ),
}


# ── Helpers (copied from step3_summarize.py) ─────────────────
# These are exact copies. If you update the logic in step3,
# update these too.

def extract_sections(text: str) -> str:
    facts_match = _SECTION_PATTERNS["facts"].search(text)
    law_match   = _SECTION_PATTERNS["law"].search(text)
    facts_text  = ""
    law_text    = ""

    if facts_match and law_match:
        facts_start = facts_match.start()
        law_start   = law_match.start()
        if facts_start >= law_start:
            facts_start = 0
        facts_text  = text[facts_start:law_start][:BUDGET_FACTS]
        law_text    = text[law_start:][:BUDGET_LAW]
    elif facts_match:
        facts_text = text[facts_match.start():][:BUDGET_FACTS + BUDGET_LAW]
    elif law_match:
        law_text   = text[law_match.start():][:BUDGET_FACTS + BUDGET_LAW]
    else:
        return text[:BUDGET_REMAINDER]

    parts = []
    if facts_text.strip():
        parts.append(f"[THE FACTS]\n{facts_text.strip()}")
    if law_text.strip():
        parts.append(f"[THE LAW]\n{law_text.strip()}")
    return "\n\n".join(parts)


def parse_filter_response(text):
    decision     = "unsure"
    reason_lines = []
    in_reason    = False
    for line in text.strip().splitlines():
        if line.startswith("DECISION:"):
            in_reason = False
            raw = line.replace("DECISION:", "").strip().lower()
            if "yes" in raw:
                decision = "yes"
            elif "no" in raw:
                decision = "no"
            else:
                decision = "unsure"
        elif line.startswith("REASON:"):
            in_reason = True
            first = line.replace("REASON:", "").strip()
            if first:
                reason_lines.append(first)
        elif in_reason and line.strip():
            reason_lines.append(line.strip())
    return decision, " ".join(reason_lines)


def call_openai(system_prompt, user_message):
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            temperature=0,
            max_tokens=600,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"    ⚠️  API error: {e}")
        return None


# ── Load test set ─────────────────────────────────────────────

print(f"📂 Loading {TEST_CASES}...")
with open(TEST_CASES, encoding="utf-8") as f:
    test_cases = json.load(f)
print(f"   {len(test_cases)} cases\n")


# ── Run ───────────────────────────────────────────────────────
# For each case: extract text → send to AI → compare with ground truth → print result.
# "ground_truth" = the is_roma_related value from the previous step 3 run.
# It's what we're comparing against to see if the prompt changed anything.

results = []

for i, case in enumerate(test_cases, 1):
    item_id      = case["itemid"]
    title        = case["title"][:50]
    ground_truth = case.get("is_roma_related", "?")
    full_text    = case.get("full_text", "")

    # Use whichever language was recorded for this case
    lang = case.get("text_source_language") or case.get("language", "ENG")

    extracted_text = extract_sections(full_text) if full_text else ""

    # Build the same user message format as step3_summarize.py
    user_message = f"""CASE METADATA:
Title:      {case.get('title', '')}
Date:       {case.get('date', '')}
Country:    {case.get('country', '')}
Articles:   {case.get('articles', '')}
Conclusion: {case.get('conclusion', '')}
Violation:  {case.get('violation', '')}
Importance: {case.get('importance', '')}
Text language: {lang}

CASE TEXT (extracted sections):
{extracted_text}"""

    raw_response = call_openai(FILTER_SYSTEM_PROMPT, user_message)

    if not raw_response:
        print(f"[{i:02d}] ⚠️  {item_id} | API call failed")
        results.append({
            "itemid":       item_id,
            "title":        case["title"],
            "ground_truth": ground_truth,
            "decision":     "error",
            "reason":       "API call failed",
            "match":        False,
            "changed":      False,
        })
        continue

    decision, reason = parse_filter_response(raw_response)

    # "match" = new decision agrees with the stored ground truth
    # "changed" = new decision disagrees with ground truth (and ground truth exists)
    match   = (decision == ground_truth)
    changed = (not match and ground_truth != "?")

    icon = "✅" if match else "❌"
    tag  = "  ← CHANGED" if changed else ""

    print(f"[{i:02d}] {icon} {item_id} | {title}")
    print(f"      was={ground_truth}  now={decision}{tag}")
    print(f"      {reason[:100]}")
    print()

    results.append({
        "itemid":       item_id,
        "title":        case["title"],
        "ground_truth": ground_truth,
        "decision":     decision,
        "reason":       reason,
        "match":        match,
        "changed":      changed,
    })

    time.sleep(DELAY)


# ── Summary ───────────────────────────────────────────────────

total   = len(results)
unsure  = sum(1 for r in results if r["decision"] == "unsure")
correct = sum(1 for r in results if r.get("match"))
changed = sum(1 for r in results if r.get("changed"))
errors  = sum(1 for r in results if r["decision"] == "error")

print(f"{'='*50}")
print(f"Unsure:  {unsure} / {total}   ← target: 0")
print(f"Matches: {correct} / {total}")
print(f"Changed: {changed} / {total}")
if errors:
    print(f"Errors:  {errors} / {total}")


# ── Save results ──────────────────────────────────────────────
# The full prompt_text is saved alongside results so each
# test_results.json is self-contained — you can always see
# exactly which prompt produced which decisions.

output = {
    "run_at":        datetime.now(timezone.utc).isoformat(),
    "model":         MODEL,
    "prompt_length": len(FILTER_SYSTEM_PROMPT),
    "prompt_text":   FILTER_SYSTEM_PROMPT,
    "summary": {
        "total":   total,
        "unsure":  unsure,
        "matches": correct,
        "changed": changed,
        "errors":  errors,
    },
    "results": results,
}

with open(TEST_RESULTS, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"\n💾 Results saved to {TEST_RESULTS}")
print(f"\nNext step: if unsure > 0, read the reasons above, adjust the prompt,")
print(f"           and re-run. When stable, copy prompt into step3_summarize.py.")
