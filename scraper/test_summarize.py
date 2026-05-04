# ============================================================
# HUDOC Roma Cases — Summary Test Runner
#
# PURPOSE: Iteratively refine the summary prompt.
#   1. Edit the SUMMARY_SYSTEM_PROMPT block below
#   2. Run this script: python scraper/test_summarize.py
#   3. Read the generated summaries and adjust the prompt
#   4. Repeat until output quality is satisfactory
#
# This script NEVER writes to cases.json. It reads from
# test_cases.json (built by test_sampler.py, contains ~6 yes cases)
# and writes results to test_summary_results.json only.
#
# What to look for in each summary:
#   ✅ Word count: 180–220 (target: exactly 200)
#   ✅ All five section labels present
#   ✅ Factual accuracy (does it match the case text?)
#   ✅ Roma identity clearly stated in [PARTIES]
#   ✅ [SIGNIFICANCE] places the case in historical context
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
TEST_RESULTS = PROJECT_ROOT / "test_summary_results.json"

MODEL = "gpt-4.1-2025-04-14"
DELAY = 0.5   # shorter than production — only ~6 calls per run (yes cases only)


# ============================================================
# ▼▼▼ EDIT THIS BLOCK BETWEEN RUNS ▼▼▼
# Everything below this block is infrastructure — don't touch it.
# ============================================================

COE_DEFINITION = """The term 'Roma and Travellers' encompasses: Roma, Sinti/Manush, \
Calé, Kaale, Romanichals, Boyash/Rudari; Balkan Egyptians (Egyptians and Ashkali); \
Eastern groups (Dom, Lom and Abdal); as well as Travellers, Yenish, Gens du voyage, \
and persons who identify themselves as Gypsies."""

SUMMARY_SYSTEM_PROMPT = f"""You are a spokesperson with legal expertise at the European \
Court of Human Rights specializing in Roma and Traveller minority rights, ECHR case law, \
and the European Convention on Human Rights. You write clear, accessible summaries for \
a general audience interested in human rights.

Apply the following Council of Europe definition throughout this task:
"{COE_DEFINITION}"

---

TASK: Write a case summary of approximately 200 words in flowing, readable prose.

Cover these five topics in order — but write them as connected paragraphs, not as \
labelled sections:
  1. Parties: name the applicant(s), confirm their Roma/Traveller identity, name the state
  2. Vulnerability: the specific Roma-related harm at the centre of the case
  3. Facts: what happened, to whom, when, and where — chronologically and specifically
  4. Decision: which articles were examined, which were violated, any remedies awarded
  5. Significance: why this case matters for Roma rights, with one sentence of \
historical or temporal context

---

STYLE EXAMPLES — write summaries that look and read like these:

EXAMPLE 1 (Lacatus v. Switzerland):
In the case of Lacatus v. Switzerland, the applicant, Ms. Violeta-Sibianca Lacatus, a \
Romanian national belonging to the Roma community, challenged Switzerland's legal \
measures against begging. The case centered on the vulnerability of Roma individuals \
facing criminal penalties for begging, a survival strategy for many impoverished Roma. \
Ms. Lacatus was fined 500 Swiss francs for begging in Geneva and subsequently imprisoned \
for five days for non-payment. The European Court of Human Rights found that the fine and \
imprisonment violated Article 8 of the European Convention on Human Rights, which \
protects the right to respect for private and family life. The Court ruled that the \
blanket ban on begging, as applied, was disproportionate and failed to consider \
Ms. Lacatus's specific circumstances, thus overstepping the narrow margin of \
appreciation afforded to states. This decision underscores the importance of protecting \
the dignity and rights of vulnerable individuals, particularly within the Roma community, \
against overly broad legal measures. Historically, Roma communities in Europe have faced \
systemic discrimination and marginalisation, making this ruling significant in affirming \
their rights and challenging criminalisation of poverty.

EXAMPLE 2 (Stalović v. Serbia):
In the case of Stalović v. Serbia, the applicants, Mr. Marko Stalović, of Roma origin, \
and Ms. Sandra Stalović, an Austrian national, brought a case against Serbia. The central \
issue was racially motivated police ill-treatment and the lack of an effective \
investigation. On April 21, 2017, after reporting a car theft, the applicants were \
subjected to informal questioning by police in Belgrade. Mr. Stalović alleged physical \
abuse, including being slapped, kicked, and suffocated, while both faced racial insults \
and threats. Despite medical evidence of injuries, the Serbian authorities dismissed their \
criminal complaint, citing lack of identification and credibility issues. The European \
Court of Human Rights found violations of Article 3 (prohibition of torture) and \
Article 14 (prohibition of discrimination), both substantively and procedurally. The \
Court highlighted the failure to conduct an effective investigation and recognised the \
discriminatory nature of the treatment. This case underscores the ongoing challenges \
Roma communities face in Europe, particularly concerning police violence and systemic \
discrimination. It reflects historical patterns of marginalisation and the need for \
robust legal protections to ensure accountability and equality for Roma and Travellers.

---

CONSTRAINTS:
- Word count: 180–200 words
- Language: always write in English, even if the source case is in French
- Tone: narrative and accessible — clear sentences, no bullet points, no section headers
- Do not speculate beyond what the judgment states"""

# ============================================================
# ▲▲▲ END OF EDITABLE BLOCK ▲▲▲
# ============================================================


# ── Text budget (keep in sync with step3_summarize.py) ───────
# Don't change these unless you also change them in step3.

BUDGET_FACTS     = 6_000
BUDGET_LAW       = 6_000
BUDGET_REMAINDER = 4_000

# Prose quality checks: look for key signal words rather than section labels
# (summaries are now flowing prose, not labelled sections)
REQUIRED_SIGNALS = ["Roma", "Article", "Court"]

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


def call_openai(system_prompt, user_message):
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            temperature=0,
            max_tokens=800,   # summaries are longer than filter decisions
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"    ⚠️  API error: {e}")
        return None


# ── Load test set — yes cases only ───────────────────────────
# Summaries are only generated for confirmed Roma cases,
# so we skip no/unsure cases from the test set.

print(f"📂 Loading {TEST_CASES}...")
with open(TEST_CASES, encoding="utf-8") as f:
    all_cases = json.load(f)

yes_cases = [c for c in all_cases if c.get("is_roma_related") == "yes"]
print(f"   {len(all_cases)} total cases → {len(yes_cases)} yes cases to summarise\n")


# ── Run ───────────────────────────────────────────────────────

results = []

for i, case in enumerate(yes_cases, 1):
    item_id  = case["itemid"]
    title    = case["title"][:50]
    lang     = case.get("text_source_language") or case.get("language", "ENG")
    full_text = case.get("full_text", "")

    extracted_text = extract_sections(full_text) if full_text else ""

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

    print(f"[{i:02d}] {item_id} | {title}")

    summary = call_openai(SUMMARY_SYSTEM_PROMPT, user_message)

    if not summary:
        print(f"      ⚠️  API call failed\n")
        results.append({
            "itemid":       item_id,
            "title":        case["title"],
            "summary":      None,
            "word_count":   0,
            "wc_ok":        False,
            "sections_ok":  False,
            "missing":   REQUIRED_SIGNALS,
            "previous":  case.get("summary"),
        })
        continue

    # ── Quality checks ────────────────────────────────────────
    word_count = len(summary.split())
    wc_ok      = 180 <= word_count <= 220
    missing    = [s for s in REQUIRED_SIGNALS if s not in summary]
    signals_ok = len(missing) == 0

    wc_icon  = "✅" if wc_ok       else "⚠️ "
    sig_icon = "✅" if signals_ok  else "❌"

    print(f"      Words: {word_count} {wc_icon}  |  Signals: {'ok' if signals_ok else 'MISSING: ' + ', '.join(missing)} {sig_icon}")
    print()
    print(summary)
    print()

    results.append({
        "itemid":      item_id,
        "title":       case["title"],
        "summary":     summary,
        "word_count":  word_count,
        "wc_ok":       wc_ok,
        "signals_ok":  signals_ok,
        "missing":     missing,
        "previous":    case.get("summary"),   # previous run's summary, for comparison
    })

    time.sleep(DELAY)


# ── Summary ───────────────────────────────────────────────────

total      = len(results)
wc_ok      = sum(1 for r in results if r["wc_ok"])
sig_ok     = sum(1 for r in results if r["signals_ok"])
errors     = sum(1 for r in results if r["summary"] is None)

print(f"{'='*50}")
print(f"Word count ok (180-220): {wc_ok} / {total}")
print(f"Signals present:         {sig_ok} / {total}")
if errors:
    print(f"Errors:                  {errors} / {total}")


# ── Save results ──────────────────────────────────────────────
# Both the new summary and the previous one are saved so you can
# compare them in test_summary_results.json.

output = {
    "run_at":        datetime.now(timezone.utc).isoformat(),
    "model":         MODEL,
    "prompt_length": len(SUMMARY_SYSTEM_PROMPT),
    "prompt_text":   SUMMARY_SYSTEM_PROMPT,
    "summary": {
        "total":       total,
        "wc_ok":       wc_ok,
        "signals_ok":  sig_ok,
        "errors":      errors,
    },
    "results": results,
}

with open(TEST_RESULTS, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"\n💾 Results saved to {TEST_RESULTS}")
print(f"\nNext step: read the summaries above, adjust the prompt, and re-run.")
print(f"           When stable, copy prompt into step3_summarize.py.")
