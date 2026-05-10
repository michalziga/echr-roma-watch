# ============================================================
# HUDOC Roma Cases — Article Summarizer
#
# 1. Checks that every "yes" case in scraped_cases.json has a
#    corresponding file in data/ (reports any that are missing).
# 2. Reads each case from data/*.json and generates a summary.
# 3. All summaries → summaries/<itemid>.json
#
# Safe to interrupt and re-run: already-summarised cases
# are skipped automatically.
#
# Run: python3 scraper/step4_summarize.py
# ============================================================

import json, time, re, os
from datetime import datetime, timezone
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ── Paths ─────────────────────────────────────────────────────

PROJECT_ROOT  = Path(__file__).parent.parent
DATA_DIR      = PROJECT_ROOT / "data"
SUMMARIES_DIR = PROJECT_ROOT / "summaries"
SCRAPED_JSON  = PROJECT_ROOT / "scraped_cases.json"
SUMMARIES_DIR.mkdir(exist_ok=True)


# ── Settings ──────────────────────────────────────────────────

MODEL  = "gpt-4.1-2025-04-14"
DELAY  = 1.0   # seconds between API calls

# ── Test mode ─────────────────────────────────────────────────
# Set TEST_MODE = True to process only a small sample.
# Set TEST_LIMIT to however many cases you want to run.
# Set TEST_MODE = False to process all remaining cases.

TEST_MODE  = True
TEST_LIMIT = 100   # ← change this number


# ── Text budget ───────────────────────────────────────────────

BUDGET_FACTS     = 6_000
BUDGET_LAW       = 6_000
BUDGET_REMAINDER = 4_000


# ── CoE definition (shared by both prompts) ───────────────────

COE_DEFINITION = """The term 'Roma and Travellers' encompasses: Roma, Sinti/Manush, \
Calé, Kaale, Romanichals, Boyash/Rudari; Balkan Egyptians (Egyptians and Ashkali); \
Eastern groups (Dom, Lom and Abdal); as well as Travellers, Yenish, Gens du voyage, \
and persons who identify themselves as Gypsies."""


# ── Summary prompt ────────────────────────────────────────────

SUMMARY_SYSTEM_PROMPT = f"""You are a spokesperson with legal expertise at the European \
Court of Human Rights specializing in Roma and Traveller minority rights, ECHR case law, \
and the European Convention on Human Rights. You write clear, accessible summaries for \
a general audience interested in human rights. Always write in English, even if the \
source case is in French or another language.

Apply the following Council of Europe definition throughout this task:
"{COE_DEFINITION}"

---

TASK: Write a case summary of approximately 200 words in flowing, readable prose.

Your summary must cover the following five elements. Use them as a content guide — \
weave them naturally into prose and do not treat them as a sequential structure or \
labelled sections:

  1. Parties: name the applicant(s) and the respondent state; describe the applicant's \
Roma/Traveller identity as stated in the judgment. If identity is implied but not \
explicitly confirmed, describe it as such — e.g. "an applicant from a Roma settlement" \
— rather than asserting Roma identity directly.
  2. Vulnerability: the specific Roma-related harm or exclusion at the centre of the case.
  3. Facts: what happened, to whom, when, and where — chronologically and specifically, \
drawn only from what the judgment states.
  4. Decision: which articles were examined, which were violated, and any remedies awarded.
  5. Significance: include only if the judgment contains a specific finding, precedent, \
or remark that distinguishes this case. Do not add generic statements about Roma \
marginalisation that could apply to any case.

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
Ms. Lacatus's specific circumstances. This decision underscores the importance of \
protecting the dignity and rights of vulnerable individuals, particularly within the Roma \
community, against overly broad legal measures.

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
substantively and procedurally. This case is notable as one of the first ECtHR rulings \
to address the intersection of police violence and racial discrimination against Roma in \
Serbia.

---

CONSTRAINTS:
- Word count: aim for approximately 200 words; do not exceed 230
- Tone: narrative and accessible — clear sentences, no bullet points, no section headers
- Do not speculate or infer beyond what the judgment explicitly states
- Do not add generic closing sentences about Roma marginalisation unless the judgment \
itself contains a specific remark that warrants it"""


# ── HTML stripper ─────────────────────────────────────────────

def strip_html(html):
    if not html:
        return html
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\.[a-zA-Z0-9]+\s*\{[^}]*\}", " ", text)
    text = text.replace("&#xa0;", " ").replace("&nbsp;", " ")
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&#xd;", "").replace("&#x9;", " ").replace("﻿", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


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


# ── OpenAI calls ──────────────────────────────────────────────

def call_openai(user_message):
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            temperature=0,
            max_completion_tokens=600,
        )
        msg = response.choices[0].message
        if getattr(msg, "refusal", None):
            print(f"    ⚠️  Model refused: {msg.refusal[:120]}")
            return None
        if not msg.content:
            finish = response.choices[0].finish_reason
            print(f"    ⚠️  Empty content (finish_reason={finish})")
            return None
        return msg.content.strip()
    except Exception as e:
        print(f"    ⚠️  API error: {e}")
        return None


# ── scraped_cases.json helpers ────────────────────────────────

def load_scraped_cases():
    if not SCRAPED_JSON.exists():
        return None, {}
    with open(SCRAPED_JSON, encoding="utf-8") as f:
        data = json.load(f)
    index = {c["itemid"]: c for c in data["cases"]}
    return data, index


def save_scraped_cases(data):
    with open(SCRAPED_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Website-ready JSON format ─────────────────────────────────

def build_output(case, summary):
    def split_field(value):
        if not value:
            return []
        return [v.strip() for v in str(value).split(";") if v.strip()]

    return {
        "itemid":        case.get("itemid", ""),
        "title":         case.get("title", ""),
        "date":          case.get("date", ""),
        "country":       case.get("country", ""),
        "importance":    case.get("importance", ""),
        "articles":      split_field(case.get("articles", "")),
        "violations":    split_field(case.get("violation", "")),
        "nonviolations": split_field(case.get("nonviolation", "")),
        "conclusion":    case.get("conclusion", ""),
        "ecli":          case.get("ecli", ""),
        "url":           case.get("url", ""),
        "summary":       summary,
        "model":         MODEL,
        "summarized_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Step 1: Check for missing data files and export them ──────

print("🔍 Checking data/ against scraped_cases.json...")
scraped_data, cases_index = load_scraped_cases()

if scraped_data:
    yes_cases   = [c for c in scraped_data["cases"] if c.get("is_roma_related") == "yes"]
    missing     = [c for c in yes_cases if not (DATA_DIR / f"{c['itemid']}.json").exists()]

    print(f"   {len(yes_cases)} 'yes' cases in scraped_cases.json")
    print(f"   {len(list(DATA_DIR.glob('*.json')))} files in data/")

    if missing:
        print(f"\n📥 {len(missing)} 'yes' case(s) missing from data/ — exporting now:")
        for c in missing:
            out_path = DATA_DIR / f"{c['itemid']}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(c, f, indent=2, ensure_ascii=False)
            print(f"   ✅ Exported {c['itemid']}  {c.get('title', '')[:60]}")
    else:
        print("   ✅ All 'yes' cases have a file in data/")
else:
    print("   ⚠️  scraped_cases.json not found — skipping cross-check")

print()


# ── Step 2: Build list of cases to process ────────────────────

all_files = sorted(DATA_DIR.glob("*.json"))

def already_summarized(filepath: Path) -> bool:
    return (SUMMARIES_DIR / filepath.name).exists()

remaining = [f for f in all_files if not already_summarized(f)]
done      = len(all_files) - len(remaining)

if TEST_MODE:
    remaining = remaining[:TEST_LIMIT]
    print(f"🧪 TEST MODE — processing {len(remaining)} of {len(all_files)} cases")
else:
    print(f"📂 {len(all_files)} cases in {DATA_DIR.name}/")

print(f"✅ Already done: {done}")
print(f"⏳ Remaining:    {len(remaining)}\n")

failed = []

for i, filepath in enumerate(remaining, 1):
    with open(filepath, encoding="utf-8") as f:
        case = json.load(f)

    item_id = case.get("itemid", filepath.stem)
    title   = case.get("title", "")[:60]

    print(f"[{i}/{len(remaining)}] {item_id} | {title}")

    # Clean HTML if needed, then extract the key sections
    full_text = case.get("full_text", "") or ""
    if full_text and ("<" in full_text or "{" in full_text[:500]):
        full_text = strip_html(full_text)

    extracted = extract_sections(full_text) if full_text else ""

    if not extracted:
        print(f"    ⚠️  No text to summarise — skipping")
        failed.append(item_id)
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

    summary = call_openai(user_message)

    if not summary:
        print(f"    ❌ API call failed")
        failed.append(item_id)
        time.sleep(DELAY)
        continue

    word_count = len(summary.split())
    print(f"    ✅ Summary: {word_count} words")

    output   = build_output(case, summary)
    out_path = SUMMARIES_DIR / filepath.name
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"    💾 Saved to summaries/{filepath.name}")

    time.sleep(DELAY)


# ── Final report ──────────────────────────────────────────────

total_done = len(list(SUMMARIES_DIR.glob("*.json")))

print(f"\n{'='*50}")
print(f"✅ Done! {total_done} / {len(all_files)} cases now summarised")
print(f"💾 Saved to {SUMMARIES_DIR}")
if failed:
    print(f"❌ Failed: {len(failed)} cases — {failed[:5]}{'...' if len(failed) > 5 else ''}")
