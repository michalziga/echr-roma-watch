# ============================================================
# HUDOC Roma Cases — Step 3
# Administrator role: semantic filter + 200 word summary
# ============================================================

import json, time, re
from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()   # reads the .env file

API_KEY = os.getenv("OPENAI_API_KEY")

if not API_KEY:
    raise ValueError("OPENAI_API_KEY not found in environment variables. Please set it in your .env file.")

client = OpenAI(api_key=API_KEY)

# ── Settings ─────────────────────────────

CASES_JSON  = "/Users/michalziga/Documents/GitHub/echr-roma-watch/cases.json"
MODEL       = "gpt-4o"
DELAY       = 1.0   # seconds between API calls (be polite)


# ── Council of Europe definition ──────────────────────────────
# Injected into both prompts so the AI uses a consistent,
# authoritative definition of who counts as Roma/Traveller.

COE_DEFINITION = """The term 'Roma and Travellers' encompasses: Roma, Sinti/Manush, \
Calé, Kaale, Romanichals, Boyash/Rudari; Balkan Egyptians (Egyptians and Ashkali); \
Eastern groups (Dom, Lom and Abdal); as well as Travellers, Yenish, Gens du voyage, \
and persons who identify themselves as Gypsies."""


# ── Prompt 1: Semantic Filter ─────────────────────────────────

FILTER_SYSTEM_PROMPT = f"""You are an administrator of legal cases at the European Court \
of Human Rights, with expertise in Roma and Traveller minority rights, ECHR case law as \
well as the European Convention on Human Rights with particular focus on Art. 3, 8, 14, \
P1-1; although you do not exclude other articles related to the specific vulnerabilities \
Roma face (housing, education, police violence, segregation). Additionally, you are aware \
of the social and historical context of Roma communities in Europe. You are experienced \
in identifying whether a case has a genuine Roma/Traveller dimension or whether minority \
identity is merely incidental to the legal dispute.

For the purpose of this task, use the following Council of Europe definition:
"{COE_DEFINITION}"

Ask yourself: is this case genuinely and substantively about Roma, Gypsy, Sinti, or \
Traveller minorities? The case must involve a Roma/Traveller individual or community as \
the applicant or as the direct subject of the legal dispute, AND the Roma/Traveller \
identity must be legally relevant to the case.

Exclude the case if:
- The applicant may be Roma but their minority identity plays no role in the dispute
- Roma or Travellers are mentioned only in passing or as a reference
- The case is about a general legal principle with no ethnic or minority dimension

If you are unsure — for example because the Roma identity is implied but never explicitly \
stated, or the full text is too incomplete to judge — mark it as unsure with a reason. \
It will be sent for manual review.

Always respond in English in this exact format:
DECISION: yes / no / unsure
REASON: one sentence explaining why, covering the following where relevant:
- Legal basis (which articles and rights are at stake)
- Social basis (what vulnerability or exclusion is present)
- Identity basis (how Roma/Traveller identity is relevant)
- Contextual conditions (country, time period, circumstances)"""


# ── Prompt 2: 200 Word Summary ────────────────────────────────

SUMMARY_SYSTEM_PROMPT = f"""You are an administrator of legal cases at the European Court \
of Human Rights, with expertise in Roma and Traveller minority rights, ECHR case law as \
well as the European Convention on Human Rights with particular focus on Art. 3, 8, 14, \
P1-1; although you do not exclude other articles related to the specific vulnerabilities \
Roma face (housing, education, police violence, segregation). Additionally, you are aware \
of the social and historical context of Roma communities in Europe.

For the purpose of this task, use the following Council of Europe definition:
"{COE_DEFINITION}"

Write a summary of exactly 200 words for the following ECHR case. Structure it as follows:
a) Applicant's name, their belonging to the Roma and Travellers community, and the nation state involved
b) What Roma and Travellers-related vulnerability is at the centre of the case
c) What happened — the key facts of the case
d) The Court's decision and which articles were found violated or not
e) Why it matters — including one sentence of temporal or historical context

Always write in English, even if the case text is in French.
Be precise, factual and concise. Do not exceed 200 words."""


# ── Parse AI filter response ──────────────────────────────────
# Extracts DECISION and REASON from the AI's response text.

def parse_filter_response(text):
    decision = "unsure"   # default if parsing fails
    reason   = ""

    for line in text.strip().splitlines():
        if line.startswith("DECISION:"):
            raw = line.replace("DECISION:", "").strip().lower()
            if "yes" in raw:
                decision = "yes"
            elif "no" in raw:
                decision = "no"
            else:
                decision = "unsure"
        elif line.startswith("REASON:"):
            reason = line.replace("REASON:", "").strip()

    return decision, reason


# ── Call OpenAI API ───────────────────────────────────────────
# A single reusable function for both prompts.

def call_openai(system_prompt, user_message):
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            temperature=0,       # 0 = consistent, deterministic responses
            max_tokens=500,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"    ⚠️  API error: {e}")
        return None


# ── Save progress ─────────────────────────────────────────────
# Writes the whole cases.json after every case so progress
# is never lost if the script is interrupted.

def save_progress(data):
    with open(CASES_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Main ──────────────────────────────────────────────────────

print(f"📂 Loading {CASES_JSON}...")
with open(CASES_JSON, encoding="utf-8") as f:
    data = json.load(f)

cases = data["cases"]
print(f"   {len(cases)} cases loaded\n")

# ── Build French fallback index ───────────────────────────────
# Groups cases by app_no so we can find a French version
# if the English full_text is missing for a case.
# Same app_no = same case in a different language.

french_by_appno = {}
for c in cases:
    if c.get("language") == "FRE" and c.get("full_text"):
        app_no = c.get("app_no", "")
        if app_no:
            french_by_appno[app_no] = c

# ── Decide which cases to process ────────────────────────────
# Skip:
#   - French cases (processed separately or not at all for now)
#   - Cases with no full text (full_text_length is 0 or None)
#   - Cases already filtered (is_roma_related is already set)

to_process = [
    c for c in cases
    if c.get("language") != "FRE"                       # English only for now
    and c.get("full_text_length")                        # must have full text
    and c.get("is_roma_related") is None                 # not yet filtered
]

already_done = sum(1 for c in cases if c.get("is_roma_related") is not None)
print(f"✅ Already processed: {already_done}")
print(f"⏳ Remaining:         {len(to_process)}\n")

# ── Processing loop ───────────────────────────────────────────

filter_yes    = 0
filter_no     = 0
filter_unsure = 0
failed        = []

for i, case in enumerate(to_process, 1):
    item_id = case["itemid"]
    title   = case["title"][:60]
    print(f"[{i}/{len(to_process)}] {item_id} | {title}")

    # ── Get the text to work with ─────────────────────────────
    # Use English full_text if available.
    # Fall back to French version of same case if not.

    full_text       = case.get("full_text")
    text_source_lang = "ENG"

    if not full_text:
        app_no = case.get("app_no", "")
        french = french_by_appno.get(app_no)
        if french:
            full_text        = french.get("full_text")
            text_source_lang = "FRE"
            print(f"    ℹ️  Using French text as fallback")

    if not full_text:
        print(f"    ⚠️  No text available, skipping")
        case["is_roma_related"]   = "no_text"
        case["filter_reason"]     = "No full text available in English or French"
        save_progress(data)
        continue

    # ── Build user message for both prompts ───────────────────
    # We feed the AI the metadata + full text together.

    user_message = f"""CASE METADATA:
Title:      {case.get('title', '')}
Date:       {case.get('date', '')}
Country:    {case.get('country', '')}
Articles:   {case.get('articles', '')}
Conclusion: {case.get('conclusion', '')}
Violation:  {case.get('violation', '')}
Importance: {case.get('importance', '')}

FULL TEXT:
{full_text[:8000]}"""   # limit to 8000 chars to stay within token budget

    # ── Prompt 1: Semantic Filter ─────────────────────────────

    print(f"    🔍 Running semantic filter...")
    filter_response = call_openai(FILTER_SYSTEM_PROMPT, user_message)

    if not filter_response:
        failed.append(item_id)
        print(f"    ❌ Filter API call failed")
        time.sleep(DELAY)
        continue

    decision, reason = parse_filter_response(filter_response)
    print(f"    → DECISION: {decision} | {reason[:80]}")

    case["is_roma_related"]      = decision
    case["filter_reason"]        = reason
    case["text_source_language"] = text_source_lang
    case["filtered_at"]          = datetime.now(timezone.utc).isoformat()

    if decision == "yes":
        filter_yes += 1
    elif decision == "no":
        filter_no += 1
    else:
        filter_unsure += 1

    # ── Prompt 2: Summary (only for confirmed Roma cases) ─────

    if decision == "yes":
        print(f"    📝 Generating summary...")
        summary_response = call_openai(SUMMARY_SYSTEM_PROMPT, user_message)

        if summary_response:
            case["summary"]            = summary_response
            case["summary_generated_at"] = datetime.now(timezone.utc).isoformat()
            print(f"    ✅ Summary saved ({len(summary_response)} chars)")
        else:
            case["summary"] = None
            failed.append(item_id)
            print(f"    ❌ Summary API call failed")

    save_progress(data)
    time.sleep(DELAY)

# ── Summary ───────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"✅ Done!")
print(f"   Roma-related (yes):    {filter_yes}")
print(f"   Not Roma-related (no): {filter_no}")
print(f"   Unsure (manual review):{filter_unsure}")
print(f"   API failures:          {len(failed)}")

if failed:
    print(f"\n   Failed IDs: {failed[:10]}{'...' if len(failed) > 10 else ''}")

print(f"\n💾 Results saved to {CASES_JSON}")