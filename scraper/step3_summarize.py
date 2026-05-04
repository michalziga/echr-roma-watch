# ============================================================
# HUDOC Roma Cases — Step 3
# Reads cases from cases.json, asks GPT-4o whether each case
# genuinely involves Roma/Traveller rights (semantic filter),
# and generates a 200-word structured summary for confirmed cases.
# ============================================================

# ── Imports ───────────────────────────────────────────────────
# json: reads/writes JSON files
# time: used for sleep() — pausing between API calls
# re: regular expressions — used to find section headers in judgment text
# datetime/timezone: for recording when each case was processed
# openai: the official Python library for calling the OpenAI API
# dotenv: loads environment variables from a .env file
# os: lets us read environment variables (like the API key)

import json, time, re
from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv
import os

# load_dotenv() reads the .env file in the project root
# and makes its values available via os.getenv()
load_dotenv()

# Create an OpenAI client using the API key from the .env file.
# os.getenv("OPENAI_API_KEY") reads the value of that variable.
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ── Settings ─────────────────────────────────────────────────

CASES_JSON = "/Users/michalziga/Documents/GitHub/echr-roma-watch/cases.json"


# ── Text budget ───────────────────────────────────────────────
# Problem: 93% of judgments exceed 8,000 chars (median ~49k, max ~537k).
# Sending only the first 8k chars means the model reads less than 11%
# of most judgments — burying Roma-relevant content that appears later
# (e.g. in the Law section or reasoning).
#
# Fix: extract two named sections from each judgment and send those
# instead of a raw character slice. Each section has its own budget so
# the total stays within token limits while covering the full document.
#
# Section budgets (chars):
BUDGET_FACTS     = 6_000   # "THE FACTS" — applicant identity, events
BUDGET_LAW       = 6_000   # "THE LAW" — articles argued, legal reasoning
BUDGET_REMAINDER = 4_000   # fallback: start of text if sections not found


# ── Council of Europe definition ─────────────────────────────
# Injected into both AI prompts so the model uses a consistent,
# authoritative definition of who counts as Roma/Traveller.

COE_DEFINITION = """The term 'Roma and Travellers' encompasses: Roma, Sinti/Manush, \
Calé, Kaale, Romanichals, Boyash/Rudari; Balkan Egyptians (Egyptians and Ashkali); \
Eastern groups (Dom, Lom and Abdal); as well as Travellers, Yenish, Gens du voyage, \
and persons who identify themselves as Gypsies."""


# ── Prompt 1: Semantic Filter ─────────────────────────────────
# This prompt is sent to the AI for every case.
# The AI decides: yes / no / unsure — does this case have a genuine
# Roma/Traveller dimension?
#
# f-string (f"""..."""): Python inserts the value of {COE_DEFINITION}
# into the string at runtime.

FILTER_MODEL  = "gpt-4o-mini-2024-07-18"   # fast + cheap: runs on every case
SUMMARY_MODEL = "gpt-4.1-2025-04-14"        # higher quality: only for confirmed yes cases
DELAY         = 1.0   # seconds between API calls


# ============================================================
# ▼▼▼ EDIT THIS BLOCK BETWEEN RUNS ▼▼▼
# Everything below this block is infrastructure — don't touch it.
# ============================================================


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

# ── Prompt 2: 200-Word Summary ────────────────────────────────
# This prompt is only sent for cases where the filter decided "yes".
# The AI writes a structured 200-word summary with five labelled sections.

SUMMARY_SYSTEM_PROMPT = f"""You are a legal expert at the European Court of Human Rights \
specializing in Roma and Traveller minority rights, ECHR case law, and the European \
Convention on Human Rights, with particular focus on Articles 3, 8, 14, and Protocol \
1-1. You are also familiar with the social and historical context of Roma communities \
in Europe.

Apply the following Council of Europe definition throughout this task:
"{COE_DEFINITION}"

---

TASK: Write a structured case summary of exactly 200 words.

Use the following five section labels exactly as written:

[PARTIES]
Identify the applicant(s) by name, confirm their Roma or Traveller identity, and name \
the respondent state.

[VULNERABILITY]
State the specific Roma/Traveller-related vulnerability at the centre of the case \
(e.g. forced eviction, school segregation, police brutality, property rights).

[FACTS]
Describe the key facts chronologically: what happened, to whom, when, and where. \
Be factual and specific.

[DECISION]
State the Court's ruling: which articles were examined, which were found violated or \
not violated, and any notable remedies or just satisfaction awarded.

[SIGNIFICANCE]
Explain why this case matters for Roma rights. Include one sentence of temporal or \
historical context situating it within the broader pattern of Roma discrimination \
in Europe.

---

CONSTRAINTS:
- Total word count: exactly 200 words across all five sections
- Language: always write in English, even if the source case text is in French
- Tone: precise, factual, concise — no legal jargon without explanation
- Do not speculate beyond what the judgment states"""


# ── Smart text extraction ─────────────────────────────────────
# ECHR judgments are structured documents. They almost always have
# a "THE FACTS" section and a "THE LAW" section.
# Instead of blindly taking the first N characters (which may miss
# the relevant content), we find these section headers and extract
# the text from them specifically.
#
# re.compile() creates a reusable pattern object.
# re.IGNORECASE makes the match case-insensitive.
# The | in the pattern means OR — any of these header variants will match.

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
    # .search() scans the text and returns the first match object, or None
    facts_match = _SECTION_PATTERNS["facts"].search(text)
    law_match   = _SECTION_PATTERNS["law"].search(text)

    facts_text = ""
    law_text   = ""

    if facts_match and law_match:
        facts_start = facts_match.start()   # character position of "THE FACTS"
        law_start   = law_match.start()     # character position of "THE LAW"

        # Guard: if the "facts" match is actually after the law section, it's a
        # prose mention (e.g. "the facts of the case"), not a real section header.
        # Reset to the document start so we don't produce an empty negative slice.
        if facts_start >= law_start:
            facts_start = 0

        # Slice from THE FACTS up to THE LAW, capped at BUDGET_FACTS chars.
        facts_text = text[facts_start:law_start][:BUDGET_FACTS]

        # Slice from THE LAW to end of document, capped at BUDGET_LAW chars.
        law_text = text[law_start:][:BUDGET_LAW]

    elif facts_match:
        # Only THE FACTS found — take it and everything after (up to combined budget)
        facts_start = facts_match.start()
        facts_text  = text[facts_start:][:BUDGET_FACTS + BUDGET_LAW]

    elif law_match:
        # Only THE LAW found — take it and everything after (up to combined budget)
        law_start = law_match.start()
        law_text  = text[law_start:][:BUDGET_FACTS + BUDGET_LAW]

    else:
        # No section headers found at all — fall back to the start of the text
        return text[:BUDGET_REMAINDER]

    # Build the output: only include sections that have content
    parts = []
    if facts_text.strip():
        parts.append(f"[THE FACTS]\n{facts_text.strip()}")
    if law_text.strip():
        parts.append(f"[THE LAW]\n{law_text.strip()}")

    # "\n\n".join(parts) combines the parts with two newlines between them
    return "\n\n".join(parts)


# ── Parse filter response ─────────────────────────────────────
# The AI responds in this format:
#   DECISION: yes
#   REASON: The applicant is a Roma man who...
#
# This function reads each line, finds the DECISION and REASON lines,
# and returns them as separate values.

def parse_filter_response(text):
    decision     = "unsure"   # default: if parsing fails, treat as unsure
    reason_lines = []
    in_reason    = False

    for line in text.strip().splitlines():   # splitlines() splits on newlines
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
            # Reason continued on the next line
            reason_lines.append(line.strip())

    return decision, " ".join(reason_lines)


# ── OpenAI call ───────────────────────────────────────────────
# A single reusable function for both the filter and summary prompts.
# system_prompt = the AI's role and instructions
# user_message  = the specific case data to analyse

def call_openai(system_prompt, user_message, model):
    try:
        response = client.chat.completions.create(
            model=model,
            # messages is a list of dicts: each has a "role" and "content"
            # "system" sets the AI's persona and task
            # "user" is the input we're asking it to process
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            temperature=0,      # 0 = deterministic output (same input → same output)
            max_tokens=600,     # maximum length of the AI's response
        )
        # response.choices[0] is the first (and only) generated reply
        # .message.content is the text of that reply
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"    ⚠️  API error: {e}")
        return None   # return None so the caller can handle the failure


# ── Save progress ─────────────────────────────────────────────
# Writes the full cases.json after every case is processed.
# If the script is interrupted, nothing is lost.

def save_progress(data):
    with open(CASES_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Main ──────────────────────────────────────────────────────

print(f"📂 Loading {CASES_JSON}...")
with open(CASES_JSON, encoding="utf-8") as f:
    data = json.load(f)

cases = data["cases"]
print(f"   {len(cases)} cases loaded\n")


# ── Backfill schema fields ────────────────────────────────────
# If cases.json was created before Step 1 was updated to include
# the Step 3 placeholder fields, those keys won't exist yet.
# This loop adds them silently so the rest of the script can
# safely read and write those fields without KeyError exceptions.

SCHEMA_DEFAULTS = {
    "is_roma_related":       None,
    "filter_reason":         None,
    "text_source_language":  None,
    "filtered_at":           None,
    "summary":               None,
    "summary_generated_at":  None,
}
backfilled = 0
for case in cases:
    for field, default in SCHEMA_DEFAULTS.items():
        # "not in" checks if the key is absent from the dict
        if field not in case:
            case[field] = default
            backfilled += 1
if backfilled:
    print(f"🔧 Backfilled {backfilled} missing schema fields across existing cases\n")


# ── French fallback index ─────────────────────────────────────
# If an English case has no full text, we try the French version
# of the same case. Cases for the same application have the same
# app_no (application number), just different language codes.
#
# Dict comprehension: builds { app_no: case_dict } for all French cases.

french_by_appno = {
    c["app_no"]: c
    for c in cases
    if c.get("language") == "FRE" and c.get("full_text") and c.get("app_no")
}


# ── Cases to process ──────────────────────────────────────────
# List comprehension: select only cases that:
#   1. Have full text (full_text_length is a non-zero, non-None value)
#   2. Haven't been filtered yet (is_roma_related is still None)
# This includes both ENG and FRE cases — FRE cases use their own text directly.

to_process = [
    c for c in cases
    if c.get("full_text_length")          # must have text
    and c.get("is_roma_related") is None  # not yet filtered
]

# Count cases already processed (any non-None value = done)
already_done = sum(1 for c in cases if c.get("is_roma_related") is not None)
print(f"✅ Already processed: {already_done}")
print(f"⏳ Remaining:         {len(to_process)}\n")


# ── Processing loop ───────────────────────────────────────────
# Counters for the final summary report

filter_yes    = 0
filter_no     = 0
filter_unsure = 0
failed        = []   # collects item IDs where API calls failed

for i, case in enumerate(to_process, 1):
    item_id = case["itemid"]
    title   = case["title"][:60]   # truncate for display
    print(f"[{i}/{len(to_process)}] {item_id} | {title}")

    # ── Resolve text source ───────────────────────────────────
    # Use the case's own full_text if available.
    # For English cases without text, fall back to the French sibling.

    full_text        = case.get("full_text")
    text_source_lang = case.get("language", "ENG")

    if not full_text and case.get("language") != "FRE":
        # English case with no text → check if there's a French version
        app_no = case.get("app_no", "")
        french = french_by_appno.get(app_no)
        if french:
            full_text        = french.get("full_text")
            text_source_lang = "FRE"
            print(f"    ℹ️  Using French sibling as fallback")

    if not full_text:
        # No text at all — mark and skip
        print(f"    ⚠️  No text available, skipping")
        case["is_roma_related"]  = "no_text"
        case["filter_reason"]    = "No full text available in English or French"
        case["filtered_at"]      = datetime.now(timezone.utc).isoformat()
        save_progress(data)
        continue   # "continue" jumps to the next iteration of the for loop

    # ── Smart section extraction ──────────────────────────────
    # Extract THE FACTS and THE LAW sections rather than slicing
    # the raw text from the beginning.
    extracted_text = extract_sections(full_text)

    # ── Build user message ────────────────────────────────────
    # This is the input we send to the AI for both prompts.
    # It combines structured metadata with the extracted case text.
    # Triple-quoted f-string: preserves line breaks in the string.

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

    # ── Semantic filter ───────────────────────────────────────
    print(f"    🔍 Running semantic filter...")
    filter_response = call_openai(FILTER_SYSTEM_PROMPT, user_message, FILTER_MODEL)

    if not filter_response:
        # API call failed — record it and move on
        failed.append(item_id)
        print(f"    ❌ Filter API call failed")
        time.sleep(DELAY)
        continue

    # Parse the AI's response into a decision and reason
    decision, reason = parse_filter_response(filter_response)
    print(f"    → DECISION: {decision} | {reason[:80]}")

    # Write the filter results back into the case dict
    case["is_roma_related"]      = decision
    case["filter_reason"]        = reason
    case["text_source_language"] = text_source_lang
    case["filtered_at"]          = datetime.now(timezone.utc).isoformat()

    # Update counters
    if decision == "yes":
        filter_yes += 1
    elif decision == "no":
        filter_no += 1
    else:
        filter_unsure += 1

    # ── Summary (yes cases only) ──────────────────────────────
    # Only ask for a summary if the filter confirmed this is a Roma case.
    if decision == "yes":
        print(f"    📝 Generating summary...")
        summary_response = call_openai(SUMMARY_SYSTEM_PROMPT, user_message, SUMMARY_MODEL)

        if summary_response:
            case["summary"]              = summary_response
            case["summary_generated_at"] = datetime.now(timezone.utc).isoformat()
            print(f"    ✅ Summary saved ({len(summary_response)} chars)")
        else:
            case["summary"] = None
            failed.append(item_id)
            print(f"    ❌ Summary API call failed")

    # Save after every case so progress is never lost
    save_progress(data)
    time.sleep(DELAY)


# ── Final report ──────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"✅ Done!")
print(f"   Roma-related (yes):     {filter_yes}")
print(f"   Not Roma-related (no):  {filter_no}")
print(f"   Unsure (manual review): {filter_unsure}")
print(f"   API failures:           {len(failed)}")

if failed:
    print(f"\n   Failed IDs: {failed[:10]}{'...' if len(failed) > 10 else ''}")

print(f"\n💾 Results saved to {CASES_JSON}")
