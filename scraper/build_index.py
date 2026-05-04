# ============================================================
# HUDOC Roma Cases — Index Builder
#
# Reads all individual summary files from summaries/*.json
# and combines them into two output files at the project root:
#
#   summaries.json     — plain JSON array (for development/inspection)
#   summaries.json.gz  — gzip-compressed version (for production)
#
# Run after summarize_articles.py has generated the summary files:
#   python3 scraper/build_index.py
#
# Loading in Node.js:
#   const zlib = require("zlib");
#   const fs   = require("fs");
#   const data = JSON.parse(zlib.gunzipSync(fs.readFileSync("summaries.json.gz")));
# ============================================================

import json, gzip
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT  = Path(__file__).parent.parent
SUMMARIES_DIR = PROJECT_ROOT / "summaries"
OUT_JSON      = PROJECT_ROOT / "summaries.json"
OUT_GZ        = PROJECT_ROOT / "summaries.json.gz"


# ── Load all summary files ────────────────────────────────────

files = sorted(SUMMARIES_DIR.glob("*.json"))
print(f"📂 Found {len(files)} summary files in {SUMMARIES_DIR.name}/")

cases = []
for filepath in files:
    with open(filepath, encoding="utf-8") as f:
        cases.append(json.load(f))

# Sort by date descending (newest first — most useful for a website listing)
cases.sort(key=lambda c: c.get("date", ""), reverse=True)
print(f"   Sorted by date — newest first: {cases[0]['date']} → {cases[-1]['date']}")


# ── Write plain JSON ──────────────────────────────────────────

payload = {
    "built_at":   datetime.now(timezone.utc).isoformat(),
    "total":      len(cases),
    "cases":      cases,
}

json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

with open(OUT_JSON, "wb") as f:
    f.write(json_bytes)

plain_kb = len(json_bytes) / 1024
print(f"\n💾 summaries.json      → {plain_kb:,.1f} KB")


# ── Write gzipped JSON ────────────────────────────────────────

with gzip.open(OUT_GZ, "wb") as f:
    f.write(json_bytes)

gz_kb = OUT_GZ.stat().st_size / 1024
ratio = (1 - gz_kb / plain_kb) * 100
print(f"💾 summaries.json.gz   → {gz_kb:,.1f} KB  ({ratio:.0f}% smaller)")


# ── Usage reminder ────────────────────────────────────────────

print(f"""
Done! Load in Node.js:

  // CommonJS
  const zlib = require("zlib");
  const fs   = require("fs");
  const {{ cases }} = JSON.parse(zlib.gunzipSync(fs.readFileSync("summaries.json.gz")));

  // ES modules (Node 18+)
  import {{ readFileSync }} from "fs";
  import {{ gunzipSync }}  from "zlib";
  const {{ cases }} = JSON.parse(gunzipSync(readFileSync("summaries.json.gz")));
""")
