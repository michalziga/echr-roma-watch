# ============================================================
# HUDOC Roma Cases — Step 4: Filter & Export
#
# Re-filters all cases in scraped_cases.json, 5 at a time.
# Confirmed Roma cases ("yes") are exported as full JSON to data/.
# Progress is tracked via a refiltered_at timestamp field so
# re-runs always continue from where the last run left off.
#
# Run: python3 scraper/step4_export.py
# ============================================================

import json, time, re, os, argparse
from datetime import datetime, timezone
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ── Paths ─────────────────────────────────────────────────────

PROJECT_ROOT  = Path(__file__).parent.parent
SCRAPED_JSON  = PROJECT_ROOT / "scraped_cases.json"
DATA_DIR      = PROJECT_ROOT / "data"
SUMMARIES_DIR = PROJECT_ROOT / "summaries"
DATA_DIR.mkdir(exist_ok=True)


# ── Settings ──────────────────────────────────────────────────

FILTER_MODEL = "gpt-4o-mini-2024-07-18"
BATCH_SIZE   = 500
DELAY        = 1.0


# ── Text budgets ──────────────────────────────────────────────

BUDGET_PREAMBLE       = 5_000   # text before first section header: parties, representation, ERRC
BUDGET_FACTS          = 6_000
BUDGET_LAW            = 12_000  # doubled: Roma identity often appears deep in the reasoning
BUDGET_REMAINDER      = 4_000
BUDGET_KEYWORD_WINDOW = 1_500   # chars per keyword snippet (≈300 before + 1,200 after match)
KEYWORD_MAX_SNIPPETS  = 5       # max extra snippets appended beyond section budgets


# ============================================================
# ▼▼▼ EDIT THIS BLOCK BETWEEN RUNS ▼▼▼
# Everything below is infrastructure — don't touch it.
# ============================================================

COE_DEFINITION_EN = """The term 'Roma and Travellers' encompasses: Roma, Sinti/Manush, \
Calé, Kaale, Romanichals, Boyash/Rudari; Balkan Egyptians (Egyptians and Ashkali); \
Eastern groups (Dom, Lom and Abdal); as well as Travellers, Yenish, Gens du voyage, \
and persons who identify themselves as Gypsies."""

COE_DEFINITION_FR = """Le terme « Roms » utilisé au Conseil de l'Europe fait référence \
aux Roms, Sinti, Kalé et groupes apparentés en Europe, y compris les Voyageurs \
(Travellers) et les groupes de l'Est (Dom et Lom), et couvre la grande diversité des \
groupes concernés, y compris les personnes qui s'identifient eux-mêmes comme Tsiganes."""

FILTER_SYSTEM_PROMPT = f"""You are a legal expert at the European Court of Human Rights \
specialising in Roma and Traveller minority rights. Cases may be in English or French; \
apply the appropriate definition below regardless of the language of the judgment.

Council of Europe definition (English):
"{COE_DEFINITION_EN}"

Définition du Conseil de l'Europe (français) :
"{COE_DEFINITION_FR}"

---

TASK: Determine whether this ECHR case is Roma-relevant.

A case is Roma-relevant if ANY ONE of the following conditions is met:

  1. EXPLICIT IDENTITY
     The statement of facts or the Court's own assessment explicitly identifies the \
applicant, a co-applicant, or a direct victim as Roma, Sinti, Gypsy, Traveller, or \
any group covered by either CoE definition above — including French-language terms \
such as Tsigane, Voyageur, Kalé, or Gens du voyage.

  2. IMPLICIT IDENTITY
     The applicant's Roma/Traveller origin is not stated outright but is strongly \
implied by the facts — for example, the applicant lives in a Roma settlement, is \
subject to measures targeting a nomadic or itinerant community, or is described in \
terms that contextually indicate Roma origin.

  3. INSTITUTIONAL MARKER
     A Roma-specific legal organisation — including but not limited to the European \
Roma Rights Centre (ERRC), Minority Rights Group, or similar Roma rights NGOs — \
appears as the applicant's representative or as a third-party intervener.

  4. ROMA AS DIRECT VICTIM OR AFFECTED GROUP
     The applicant is not Roma themselves (e.g. a journalist, NGO, or public official) \
but the case directly concerns harm to, or discrimination against, Roma individuals \
or communities, who are the direct victims or subject of the contested act.

---

WHAT DOES NOT QUALIFY — read carefully before deciding:

  - CITATIONAL MENTIONS: Roma/Traveller terms appear only inside quoted legal \
instruments, cited ECtHR precedents, domestic legislation, policy documents, or \
sections titled "Relevant law", "International materials", "Droit pertinent", or \
"Textes internationaux". These do not establish Roma relevance.

  - GEOGRAPHIC FALSE POSITIVE: "Roma" refers to the Italian city of Rome. Disregard \
references such as "tribunale di Roma", "prefetto di Roma", "in Roma", "a Roma", \
"di Roma". If "Roma" appears only in this geographic or institutional form with no \
other minority indicator, the case is not Roma-relevant.

  - APPENDIX/ANNEXE FALSE POSITIVE: "Roma" appears only in an Appendix or Annexe \
section — for example, in lists of Italian applicants, courts, or laws where "Roma" \
denotes the city. Treat any "Roma" mention occurring after the last section marked \
"Appendix", "Annexe", or "Annexe" as a geographic reference only.

  - BACKGROUND MENTION: A Roma or Traveller community is cited only as background \
context — drawn from legal instruments, public policies, reports, or comparative statistics — with no Roma applicant, victim, or intervener present.

---

If Roma identity is suggested but the evidence is too thin or ambiguous to decide, \
mark as UNSURE. Unsure cases will be sent for manual review.

---

Respond strictly in English using this exact format — no additional text:

DECISION: yes / no / unsure
REASON: one sentence identifying which condition was met (or why none was), naming \
the specific evidence (e.g. phrase or organisation) and where it appears in the \
judgment (facts, Court's assessment, representation section, appendix)."""

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

# Keywords that signal Roma/Traveller identity anywhere in the text.
# Covers all groups in the CoE definition (Signal 1 & 2). Used to extract
# snippets from parts of the document beyond the section budgets.
_IDENTITY_RE = re.compile(
    r"\b(Roma|Sinti|Gypsy|Gypsies|Travell?er|Romani|Manush|Cal[eé]|Kaale|"
    r"Romanichal|Boyash|Rudari|Ashkali|Abdal|Yenish|ERRC)\b"
    r"|Gens\s+du\s+voyage",
    re.IGNORECASE,
)

# Roma-specific legal organisations whose presence in a judgment is an
# unambiguous institutional marker (Signal 3). Checked on the full text
# before any budget truncation occurs.
_INSTITUTIONAL_RE = re.compile(
    r"(ERRC"
    r"|European\s+Roma\s+Rights\s+Centr[e]"
    r"|Minority\s+Rights\s+Group"
    r"|Romani\s+CRISS"
    r"|European\s+Roma\s+Information\s+Office"
    r")",
    re.IGNORECASE,
)

# Unambiguous minority terms — excludes bare "Roma" which also names an Italian city
# (see "what does NOT qualify" in the prompt). Used by the Signal 1 hard check so
# geographic references like "tribunale di Roma" cannot trigger a false positive.
_UNAMBIGUOUS_IDENTITY_RE = re.compile(
    r"\b(Sinti|Gypsy|Gypsies|Travell?er|Romani(?!a)|Manush|Cal[eé]|Kaale|"
    r"Romanichal|Boyash|Rudari|Ashkali|Abdal|Yenish)\b"
    r"|Gens\s+du\s+voyage",
    re.IGNORECASE,
)

# Citation-context markers: if any of these appear within CITATION_LOOKBACK chars
# before an identity match, that match is treated as citational and excluded from
# the hard-check count.  Handles "(cited above, §§ 61-63)" and "(cited above, p. 956)".
_CITATION_NEAR_RE = re.compile(
    r"\(cited above",
    re.IGNORECASE,
)
CITATION_LOOKBACK = 350  # chars to look back before a match

# Appendix/annexe section markers. Content after the LAST such marker is stripped
# before identity counting and section extraction to prevent "Roma" (Italian city)
# in Italian-law appendices from triggering false positives.
_APPENDIX_RE = re.compile(r"\b(appendix|annexe)\b", re.IGNORECASE)


def truncate_before_appendix(text: str) -> str:
    """Return text up to (not including) the last appendix/annexe section marker."""
    matches = list(_APPENDIX_RE.finditer(text))
    if matches:
        return text[:matches[-1].start()]
    return text


def _is_citational(text: str, match: re.Match) -> bool:
    """Return True if the identity match appears inside a cited text excerpt."""
    look_back = text[max(0, match.start() - CITATION_LOOKBACK): match.start()]
    return bool(_CITATION_NEAR_RE.search(look_back))


def quick_classify(full_text: str):
    """
    Deterministic pre-pass over the FULL text before any budget truncation.
    Returns (decision, reason) if a hard rule fires, or None to proceed to the LLM.

    Runs before extract_sections so preamble truncation can never cause a miss.
    Signal 2 (implicit identity) always goes to the LLM — it requires judgment.
    """
    # Signal 3 (hard): any Roma-specific legal organisation in the full text → yes.
    # Representation or third-party intervention by such an org is a strong independent
    # marker of Roma identity even where the applicant is not explicitly labelled.
    m = _INSTITUTIONAL_RE.search(full_text)
    if m:
        return ("yes",
                f"Institutional marker '{m.group()}' found in judgment — Roma relevance "
                f"confirmed without LLM (Signal 3 hard check).")

    # Signal 1 (hard): ≥3 non-citational Roma/Traveller mentions AND at least one
    # unambiguous term.  Citational matches (identity terms appearing within
    # CITATION_LOOKBACK chars of "(cited above") are excluded from the count to
    # prevent judgments where Roma/Traveller terms appear only inside references to
    # other cases (e.g. Čonka) from triggering a false positive.
    # "Roma" alone is excluded from the unambiguous set because it also names an
    # Italian city; Italian-language judgments routinely contain "di Roma", etc.
    # Text is truncated at the last appendix/annexe marker so that Italian-law
    # appendices listing parties from Rome do not inflate the Roma mention count.
    search_text = truncate_before_appendix(full_text)
    roma_hits = [m for m in _IDENTITY_RE.finditer(search_text)
                 if not _is_citational(search_text, m)]
    unambiguous_hits = [m for m in _UNAMBIGUOUS_IDENTITY_RE.finditer(search_text)
                        if not _is_citational(search_text, m)]
    if len(roma_hits) >= 3 and len(unambiguous_hits) >= 1:
        return ("yes",
                f"{len(roma_hits)} non-citational Roma/Traveller identity mentions (incl. "
                f"{len(unambiguous_hits)} unambiguous) found in full text — "
                f"Roma relevance confirmed without LLM (Signal 1 hard check).")

    return None


def find_keyword_snippets(text: str, covered_up_to: int) -> list:
    """Return up to KEYWORD_MAX_SNIPPETS excerpts around identity keywords
    that fall beyond the already-captured section budgets."""
    snippets  = []
    last_end  = covered_up_to
    for match in _IDENTITY_RE.finditer(text, covered_up_to):
        if match.start() < last_end:
            continue  # inside a window we already captured
        start = max(covered_up_to, match.start() - 300)
        end   = min(len(text), match.start() + BUDGET_KEYWORD_WINDOW)
        snippets.append(text[start:end].strip())
        last_end = end
        if len(snippets) >= KEYWORD_MAX_SNIPPETS:
            break
    return snippets

def extract_sections(text: str) -> str:
    text = truncate_before_appendix(text)
    facts_match = _SECTION_PATTERNS["facts"].search(text)
    law_match   = _SECTION_PATTERNS["law"].search(text)
    facts_text  = ""
    law_text    = ""
    covered_up_to = 0

    # Preamble: text before the first section header.
    # Contains parties, representation, and third-party interventions (e.g. ERRC),
    # which are strong Roma identity signals often missed by section-based extraction.
    first_section_start = min(
        facts_match.start() if facts_match else len(text),
        law_match.start()   if law_match   else len(text),
    )
    preamble_text = text[:first_section_start][:BUDGET_PREAMBLE].strip()

    if facts_match and law_match:
        facts_start = facts_match.start()
        law_start   = law_match.start()
        if facts_start >= law_start:
            facts_start = 0
        facts_text    = text[facts_start:law_start][:BUDGET_FACTS]
        law_text      = text[law_start:][:BUDGET_LAW]
        covered_up_to = law_start + BUDGET_LAW
    elif facts_match:
        facts_text    = text[facts_match.start():][:BUDGET_FACTS + BUDGET_LAW]
        covered_up_to = facts_match.start() + BUDGET_FACTS + BUDGET_LAW
    elif law_match:
        law_text      = text[law_match.start():][:BUDGET_FACTS + BUDGET_LAW]
        covered_up_to = law_match.start() + BUDGET_FACTS + BUDGET_LAW
    else:
        covered_up_to = BUDGET_REMAINDER

    parts = []
    if preamble_text:
        parts.append(f"[PREAMBLE]\n{preamble_text}")
    if facts_text.strip():
        parts.append(f"[THE FACTS]\n{facts_text.strip()}")
    if law_text.strip():
        parts.append(f"[THE LAW]\n{law_text.strip()}")

    if not parts:
        parts.append(text[:BUDGET_REMAINDER])

    for i, snippet in enumerate(find_keyword_snippets(text, covered_up_to), 1):
        parts.append(f"[ADDITIONAL CONTEXT {i}]\n{snippet}")

    return "\n\n".join(parts)


# ── Filter response parser ────────────────────────────────────

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


# ── OpenAI call ───────────────────────────────────────────────

def call_openai(system_prompt, user_message, model):
    try:
        response = client.chat.completions.create(
            model=model,
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


# ── File cleanup ─────────────────────────────────────────────

def remove_stale_files(item_id: str, label: str):
    for path, name in [
        (DATA_DIR      / f"{item_id}.json", "data/"),
        (SUMMARIES_DIR / f"{item_id}.json", "summaries/"),
    ]:
        if path.exists():
            path.unlink()
            print(f"    → removed stale {name}{item_id}.json ({label})")


# ── Save scraped_cases.json ───────────────────────────────────

def save_progress(data):
    with open(SCRAPED_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Main ──────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset", action="store_true",
        help="Clear refiltered_at for all cases so every case is re-processed from scratch.",
    )
    args = parser.parse_args()

    print(f"📂 Loading {SCRAPED_JSON.name}...")
    with open(SCRAPED_JSON, encoding="utf-8") as f:
        data = json.load(f)

    cases = data["cases"]
    print(f"   {len(cases)} cases loaded\n")

    if args.reset:
        for case in cases:
            case["refiltered_at"] = None
        save_progress(data)
        print(f"🔄 Reset refiltered_at for all {len(cases)} cases — starting fresh.\n")

    # Backfill schema fields missing from cases created before these fields existed
    SCHEMA_DEFAULTS = {
        "is_roma_related":      None,
        "filter_reason":        None,
        "text_source_language": None,
        "filtered_at":          None,
        "refiltered_at":        None,
        "summary":              None,
        "summary_model":        None,
        "summary_generated_at": None,
    }
    backfilled = 0
    for case in cases:
        for field, default in SCHEMA_DEFAULTS.items():
            if field not in case:
                case[field] = default
                backfilled += 1
    if backfilled:
        print(f"🔧 Backfilled {backfilled} missing schema fields across existing cases\n")

    # French fallback index
    french_by_appno = {
        c["app_no"]: c
        for c in cases
        if c.get("language") == "FRE" and c.get("full_text") and c.get("app_no")
    }

    # Find cases not yet processed in this re-filter pass
    to_process   = [c for c in cases if c.get("refiltered_at") is None]
    already_done = len(cases) - len(to_process)

    print(f"✅ Already re-filtered: {already_done} / {len(cases)}")
    print(f"⏳ Remaining:           {len(to_process)}")

    if not to_process:
        yes_count = sum(1 for _ in DATA_DIR.glob("*.json"))
        print(f"\n🎉 All {len(cases)} cases have been re-filtered!")
        print(f"   {yes_count} confirmed Roma cases are in data/")
        raise SystemExit(0)

    batch = to_process[:BATCH_SIZE]
    print(f"🔄 Processing next {len(batch)} cases...\n")

    batch_results = []

    for case in batch:
        item_id = case["itemid"]
        title   = case["title"][:60]
        print(f"  {item_id} | {title}")

        # Resolve text source (English first, French sibling as fallback)
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
            remove_stale_files(item_id, "no_text")
            save_progress(data)
            batch_results.append((item_id, title, "no_text", "No text available"))
            continue

        # Deterministic pre-pass — runs on full text, bypasses all budget limits
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
            print(f"    ❌ API call failed — skipping (will retry on next run)")
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
            remove_stale_files(item_id, decision)
            print(f"    → {decision.upper()}")

        save_progress(data)
        batch_results.append((item_id, title, decision, reason))
        time.sleep(DELAY)

    # ── Batch summary ─────────────────────────────────────────────

    LABELS = {
        "yes":     "✅ YES   ",
        "no":      "❌ NO    ",
        "unsure":  "❓ UNSURE",
        "no_text": "⚠️  NO TEXT",
        "FAILED":  "💥 FAILED",
    }

    print(f"\n{'─'*60}")
    print(f"Batch results — {len(batch_results)} cases processed:\n")
    for item_id, title, decision, reason in batch_results:
        label = LABELS.get(decision, decision)
        print(f"  {label}  {item_id}  {title[:45]}")
        if reason and decision not in ("no_text", "FAILED"):
            print(f"           {reason[:90]}")

    remaining_count = sum(1 for c in cases if c.get("refiltered_at") is None)
    yes_count       = sum(1 for _ in DATA_DIR.glob("*.json"))

    print(f"\n📊 Progress: {len(cases) - remaining_count} / {len(cases)} re-filtered")
    print(f"   {yes_count} confirmed Roma cases exported to data/")

    if remaining_count > 0:
        print(f"\n   Re-run to process the next {min(BATCH_SIZE, remaining_count)} cases.")
    else:
        print(f"\n🎉 All {len(cases)} cases have been re-filtered!")
