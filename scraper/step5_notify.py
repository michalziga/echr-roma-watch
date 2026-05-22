"""
step5_notify.py — Telegram notifications for new Roma cases

Compares the current summaries.json against a local state file
(notified_cases.json) and sends one Telegram message per new case.

Required env vars:
  TELEGRAM_BOT_TOKEN  — token from BotFather
  TELEGRAM_CHAT_ID    — your chat / channel ID

Usage:
  python scraper/step5_notify.py
"""

# --- Imports ---
# These are Python "libraries" — pre-built toolboxes we load at the top.
# Instead of writing all the code ourselves, we reuse existing tools.

import json          # reads and writes JSON files (like summaries.json)
import os            # lets us read environment variables (secret values like tokens)
import sys           # lets us exit the script early with an error message
from pathlib import Path  # a clean way to work with file paths that works on any OS

import requests              # makes HTTP requests — here we use it to call the Telegram API
from dotenv import load_dotenv  # reads a local .env file so you can store secrets there during development


# --- Load environment variables ---
# load_dotenv() looks for a file called .env in your project folder.
# If it finds one, it loads key=value pairs from it as environment variables.
# This is useful locally so you don't have to set secrets in your terminal every time.
# In GitHub Actions, the secrets are set via the repository settings instead.
load_dotenv()


# --- File paths ---
# Path(__file__) is the path to THIS script file.
# .parent goes one folder up (from scraper/ to the project root).
# .parent.parent goes up two levels, reaching the project root.
# We then build the full paths to the files we need.
PROJECT_ROOT   = Path(__file__).parent.parent
SUMMARIES_FILE = PROJECT_ROOT / "summaries.json"       # the file with all summarized Roma cases
STATE_FILE     = PROJECT_ROOT / "notified_cases.json"  # our memory — which cases we already notified about


# --- Read secrets from environment variables ---
# os.getenv("NAME") looks up a variable called NAME.
# If it doesn't exist, it returns None (nothing).
# These values come from your .env file locally, or GitHub Secrets in Actions.
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # the long token BotFather gave you
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")    # the ID of the chat where messages will be sent


# --- Country code lookup table ---
# ECHR stores country as a 3-letter code (e.g. "BGR" for Bulgaria).
# This dictionary maps those codes to readable names.
# dict syntax: {"key": "value", ...} — you look up a value by its key.
COUNTRY_NAMES = {
    "ALB": "Albania", "AND": "Andorra", "ARM": "Armenia", "AUT": "Austria",
    "AZE": "Azerbaijan", "BEL": "Belgium", "BGR": "Bulgaria", "BIH": "Bosnia",
    "CHE": "Switzerland", "CYP": "Cyprus", "CZE": "Czechia", "DEU": "Germany",
    "DNK": "Denmark", "ESP": "Spain", "EST": "Estonia", "FIN": "Finland",
    "FRA": "France", "GBR": "UK", "GEO": "Georgia", "GRC": "Greece",
    "HRV": "Croatia", "HUN": "Hungary", "IRL": "Ireland", "ISL": "Iceland",
    "ITA": "Italy", "LIE": "Liechtenstein", "LTU": "Lithuania", "LUX": "Luxembourg",
    "LVA": "Latvia", "MCO": "Monaco", "MDA": "Moldova", "MKD": "N. Macedonia",
    "MLT": "Malta", "MNE": "Montenegro", "NLD": "Netherlands", "NOR": "Norway",
    "POL": "Poland", "PRT": "Portugal", "ROU": "Romania", "RUS": "Russia",
    "SMR": "San Marino", "SRB": "Serbia", "SVK": "Slovakia", "SVN": "Slovenia",
    "SWE": "Sweden", "TUR": "Turkey", "UKR": "Ukraine",
}

# Maps the numeric importance level from ECHR to a human-readable label.
# "1" is most important (key cases), "4" is routine.
IMPORTANCE_LABELS = {"1": "Key case", "2": "Important", "3": "Notable", "4": "Standard"}


# ---------------------------------------------------------------------------
# FUNCTION: load_state
# ---------------------------------------------------------------------------
# A "function" is a reusable block of code you can call by name.
# def means "define a function". The part after -> tells you what type it returns.
# set[str] means a set of strings — like a list but with no duplicates, good for fast lookups.
def load_state() -> set[str]:
    """Returns the set of case IDs we have already sent a notification for."""

    # Check if the state file exists yet (it won't on the very first run).
    if STATE_FILE.exists():
        # Read the file as text, parse it as JSON (it's a list of IDs),
        # and convert it to a Python set for fast "is this ID in here?" checks.
        return set(json.loads(STATE_FILE.read_text()))

    # If the file doesn't exist yet, return an empty set (we haven't notified anyone).
    return set()


# ---------------------------------------------------------------------------
# FUNCTION: save_state
# ---------------------------------------------------------------------------
# -> None means this function doesn't return a value; it just does something (writes a file).
def save_state(seen: set[str]) -> None:
    """Saves the set of notified case IDs back to notified_cases.json."""

    # sorted() turns the set into a sorted list (sets have no order).
    # json.dumps() converts a Python object to a JSON string.
    # indent=2 makes it pretty-printed with 2 spaces so the file is human-readable.
    # .write_text() writes that string to the file, creating it if needed.
    STATE_FILE.write_text(json.dumps(sorted(seen), indent=2))


# ---------------------------------------------------------------------------
# FUNCTION: format_message
# ---------------------------------------------------------------------------
# Takes one case (a dict) and returns a nicely formatted string for Telegram.
# dict means "dictionary" — a collection of key-value pairs, like a JSON object.
def format_message(case: dict) -> str:
    """Builds the Telegram message text for a single case."""

    # .get("key", "default") safely reads a value from the dictionary.
    # If the key doesn't exist, it returns the default instead of crashing.
    title   = case.get("title", "Unknown")
    date    = case.get("date", "")
    country = case.get("country", "")

    # Look up the country code in our table above.
    # COUNTRY_NAMES.get(code, code) means: if the code is in our table use the full name,
    # otherwise fall back to the raw code itself (e.g. "XYZ" if we don't have it).
    country  = COUNTRY_NAMES.get(country, country)

    # case.get("articles", []) returns a list of article numbers like ["8", "14"].
    # ", ".join(...) glues them together into one string: "8, 14"
    articles = ", ".join(case.get("articles", []))

    imp_raw = case.get("importance", "")
    # Look up a readable label; if the number isn't in our table, show "Level X".
    # f"Level {imp_raw}" is an f-string — the {} part gets replaced by the variable's value.
    imp     = IMPORTANCE_LABELS.get(imp_raw, f"Level {imp_raw}")

    verdict = case.get("conclusion", "")
    url     = case.get("url", "")

    # .strip() removes any leading/trailing whitespace or newlines from the summary text.
    summary = case.get("summary", "").strip()

    # Keep the message short — Telegram has a 4096 character limit and long texts are hard to read.
    # len(summary) counts the number of characters.
    if len(summary) > 600:
        # Keep only the first 597 characters and add "…" to show it was cut off.
        # summary[:597] is "slice" syntax — give me characters from position 0 up to (not including) 597.
        summary = summary[:597] + "…"

    # Build a list of lines for the message.
    # *bold text* is Telegram Markdown syntax for bold.
    # [link text](url) is Telegram Markdown syntax for a clickable hyperlink.
    # An empty string f"" creates a blank line (paragraph break).
    # The "X if condition else Y" pattern is a one-line if/else — include the line only if there's content.
    lines = [
        f"*New Roma case — ECHR*",
        f"",
        f"*{title}*",
        f"📅 {date}  |  🌍 {country}  |  ⚖️ {imp}",
        f"Articles: {articles}" if articles else "",   # skip this line if articles is empty
        f"Conclusion: {verdict}" if verdict else "",   # skip this line if verdict is empty
        f"",
        summary,
        f"",
        f"[Read judgment]({url})",
    ]

    # Join all lines with a newline character "\n" to form the final message.
    # "line for line in lines if line is not None" filters out any None values
    # (though here all values are strings, this is a safety guard).
    return "\n".join(line for line in lines if line is not None)


# ---------------------------------------------------------------------------
# FUNCTION: send_message
# ---------------------------------------------------------------------------
# Sends one message to Telegram. Returns True if it worked, False if it failed.
def send_message(text: str) -> bool:
    """Calls the Telegram Bot API to send a message to our chat."""

    # Build the API endpoint URL. Every Telegram bot has its own URL with its token in it.
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    # requests.post() sends an HTTP POST request to that URL — like submitting a form.
    # json={...} is the data we're sending (Telegram expects JSON).
    #   chat_id: where to send it
    #   text: the message content
    #   parse_mode: "Markdown" tells Telegram to render *bold* and [links](url)
    #   disable_web_page_preview: False means Telegram WILL show a link preview
    # timeout=15 means give up if Telegram doesn't respond within 15 seconds.
    resp = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }, timeout=15)

    # resp.ok is True if Telegram returned a success status (HTTP 200).
    # If something went wrong, print the error to stderr (the error output stream).
    if not resp.ok:
        # file=sys.stderr sends this print to the error output instead of normal output.
        print(f"  Telegram error {resp.status_code}: {resp.text}", file=sys.stderr)

    return resp.ok  # return True (success) or False (failure)


# ---------------------------------------------------------------------------
# FUNCTION: main
# ---------------------------------------------------------------------------
# This is the "entry point" — where the actual work happens when you run the script.
def main() -> None:
    """Main logic: find new cases, send notifications, save updated state."""

    # Guard: make sure the secrets are loaded before doing anything.
    # "not BOT_TOKEN" is True when BOT_TOKEN is None or an empty string.
    if not BOT_TOKEN or not CHAT_ID:
        # sys.exit() stops the script immediately and prints an error message.
        sys.exit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables.")

    # Guard: make sure summaries.json actually exists.
    if not SUMMARIES_FILE.exists():
        sys.exit(f"summaries.json not found at {SUMMARIES_FILE}")

    # Read and parse summaries.json into a Python dictionary.
    # json.loads() converts a JSON string into a Python dict/list.
    data  = json.loads(SUMMARIES_FILE.read_text())

    # data is a dict with a "cases" key whose value is a list of case dicts.
    # If "cases" doesn't exist for some reason, default to an empty list [].
    cases = data.get("cases", [])

    # Load the set of case IDs we've already notified about.
    seen  = load_state()

    # List comprehension: build a new list containing only cases we haven't seen yet.
    # Syntax: [item for item in collection if condition]
    # c["itemid"] is the unique ID of a case, e.g. "001-250240".
    # "not in seen" is True when that ID is NOT in our already-notified set.
    new_cases = [c for c in cases if c["itemid"] not in seen]

    # If everything is already known, there's nothing to do.
    if not new_cases:
        print("No new cases to notify.")
        return  # exit the function early (and therefore stop the script)

    print(f"Sending notifications for {len(new_cases)} new case(s)…")

    notified = 0  # counter — how many we successfully sent

    # Loop over each new case one at a time.
    for case in new_cases:
        msg = format_message(case)   # build the message text

        if send_message(msg):        # try to send it; True = success
            seen.add(case["itemid"]) # mark this ID as notified so we don't send it again
            notified += 1            # increment our success counter
            print(f"  ✓ {case['itemid']}  {case.get('title', '')}")
        else:
            # Don't add to seen — next run will try again.
            print(f"  ✗ {case['itemid']}  (failed, will retry next run)")

    # Write the updated set of notified IDs back to the file.
    save_state(seen)
    print(f"Done. {notified}/{len(new_cases)} notifications sent.")


# ---------------------------------------------------------------------------
# Entry point guard
# ---------------------------------------------------------------------------
# This block runs ONLY when you execute this file directly (e.g. python step5_notify.py).
# If another script imports this file as a module, this block is skipped.
# It's a Python convention to always wrap your main() call this way.
if __name__ == "__main__":
    main()
