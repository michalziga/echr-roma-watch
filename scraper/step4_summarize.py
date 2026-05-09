# ============================================================
# HUDOC Roma Cases — Step 5: Summarize Exported Cases
#
# Reads confirmed Roma cases from data/*.json (written by step4)
# and generates a 180-200 word narrative summary for each.
# Processes 5 cases per run; safe to interrupt and re-run.
#
# Run AFTER step4_export.py has finished all filtering.
# Run: python3 scraper/step5_summarize.py
# ============================================================

import json, time, re, os
from datetime import datetime, timezone
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ── Paths ─────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR     = PROJECT_ROOT / "data"


# ── Settings ──────────────────────────────────────────────────

SUMMARY_MODEL = "gpt-4.1-2025-04-14"
BATCH_SIZE    = 5
DELAY         = 1.0


# ── Text budgets ──────────────────────────────────────────────

BUDGET_FACTS     = 6_000
BUDGET_LAW       = 6_000
BUDGET_REMAINDER = 4_000


# ============================================================
# ▼▼▼ EDIT THIS BLOCK BETWEEN RUNS ▼▼▼
# Everything below is infrastructure — don't touch it.
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

Cover these five topics in order — write them as connected paragraphs, not as \
labelled sections or bullet points:
  1. Parties: name the applicant(s), confirm their Roma/Traveller identity, name the state
  2. Vulnerability: the specific Roma-related harm at the centre of the case
  3. Facts: what happened, to whom, when, and where — chronologically and specifically
  4. Decision: which articles were examined, which were violated, any remedies awarded
  5. Significance: why this case matters for Roma rights, with one sentence of \
historical or temporal context

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
Ms. Lacatus's specific circumstances. This decision underscores the importance of \
protecting the dignity and rights of vulnerable individuals, particularly within the Roma \
community, against overly broad legal measures. Historically, Roma communities in Europe \
have faced systemic discrimination and marginalisation, making this ruling significant in \
affirming their rights and challenging criminalisation of poverty.

EXAMPLE 2 (Stalović v. Serbia):
In the case of Stalović v. Serbia, the applicants, Mr. Marko Stalović, of Roma origin, \
and Ms. Sandra Stalović, an Austrian national, brought a case against Serbia. The central \
issue was racially motivated police ill-treatment and the lack of an effective \
investigation. On April 21, 2017, after reporting a car theft, the applicants were \
subjected to informal questioning by police in Belgrade. Mr. Stalović alleged physical \
abuse, including being slapped, kicked, and suffocated, while both faced racial insults \
and threats. Despite medical evidence of injuries, the Serbian authorities dismissed their \
criminal complaint. The European Court of Human Rights found violations of Article 3 \
(prohibition of torture) and Article 14 (prohibition of discrimination), both \
substantively and procedurally. This case underscores the ongoing challenges Roma \
communities face in Europe, particularly concerning police violence and systemic \
discrimination, reflecting historical patterns of marginalisation.

---

CONSTRAINTS:
- Word count: 180–200 words
- Language: always write in English, even if the source case is in French
- Tone: narrative and accessible — clear sentences, no bullet points, no section headers
- Do not speculate beyond what the judgment states"""


# ── Section extractor ─────────────────────────────────────────

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
        facts_text = text[facts_start:law_start][:BUDGET_FACTS]
        law_text   = text[law_start:][:BUDGET_LAW]
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


# ── OpenAI call ───────────────────────────────────────────────

def call_openai(user_message):
    try:
        response = client.chat.completions.create(
            model=SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            temperature=0,
            max_tokens=500,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"    ⚠️  API error: {e}")
        return None


# ── Main ──────────────────────────────────────────────────────

all_files = sorted(DATA_DIR.glob("*.json"))

if not all_files:
    print(f"⚠️  No files found in {DATA_DIR}")
    print("   Run step3_filter.py first to populate data/ with confirmed Roma cases.")
    raise SystemExit(1)

# Cases without a summary (null or missing field)
to_summarize = []
for filepath in all_files:
    with open(filepath, encoding="utf-8") as f:
        case = json.load(f)
    if not case.get("summary"):
        to_summarize.append(filepath)

already_done = len(all_files) - len(to_summarize)

print(f"📂 {len(all_files)} cases in data/")
print(f"✅ Already summarized: {already_done}")
print(f"⏳ Remaining:          {len(to_summarize)}")

if not to_summarize:
    print(f"\n🎉 All {len(all_files)} cases already have summaries!")
    raise SystemExit(0)

batch = to_summarize[:BATCH_SIZE]
print(f"🔄 Processing next {len(batch)} cases...\n")

batch_results = []
failed        = []

for filepath in batch:
    with open(filepath, encoding="utf-8") as f:
        case = json.load(f)

    item_id = case.get("itemid", filepath.stem)
    title   = case.get("title", "")[:60]
    print(f"  {item_id} | {title}")

    full_text = case.get("full_text", "") or ""
    extracted = extract_sections(full_text) if full_text else ""

    if not extracted:
        print(f"    ⚠️  No text to summarize — skipping")
        failed.append(item_id)
        batch_results.append((item_id, title, False, 0))
        continue

    user_message = f"""CASE METADATA:
Title:      {case.get('title', '')}
Date:       {case.get('date', '')}
Country:    {case.get('country', '')}
Articles:   {case.get('articles', '')}
Conclusion: {case.get('conclusion', '')}
Violation:  {case.get('violation', '')}
Importance: {case.get('importance', '')}

CASE TEXT (extracted sections):
{extracted}"""

    print(f"    📝 Generating summary...")
    summary = call_openai(user_message)

    if not summary:
        print(f"    ❌ API call failed")
        failed.append(item_id)
        batch_results.append((item_id, title, False, 0))
        time.sleep(DELAY)
        continue

    word_count = len(summary.split())

    case["summary"]              = summary
    case["summary_model"]        = SUMMARY_MODEL
    case["summary_generated_at"] = datetime.now(timezone.utc).isoformat()

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(case, f, indent=2, ensure_ascii=False)

    print(f"    ✅ {word_count} words — saved")
    batch_results.append((item_id, title, True, word_count))
    time.sleep(DELAY)


# ── Batch summary ─────────────────────────────────────────────

print(f"\n{'─'*60}")
print(f"Batch results — {len(batch_results)} cases processed:\n")
for item_id, title, success, word_count in batch_results:
    if success:
        print(f"  ✅  {item_id}  {title[:45]}  ({word_count} words)")
    else:
        print(f"  ❌  {item_id}  {title[:45]}  (failed)")

remaining_count = len(to_summarize) - len(batch)
total_done      = already_done + sum(1 for _, _, ok, _ in batch_results if ok)

print(f"\n📊 Progress: {total_done} / {len(all_files)} cases summarized")

if remaining_count > 0:
    print(f"   Re-run to process the next {min(BATCH_SIZE, remaining_count)} cases.")
else:
    print(f"\n🎉 All cases in data/ now have summaries!")
    print(f"   Run scraper/build_index.py to rebuild summaries.json.")
