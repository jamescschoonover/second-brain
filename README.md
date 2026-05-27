# Second Brain — James Schoonover

Persistent memory system for Forge (Claude Code). Imports journal entries and content captures, enriches them with Claude Haiku analysis, stores them locally, and syncs to Google Drive.

---

## Directory Layout

```
~/second-brain/
  import.py        ← import CLI (Mindsera, Recall, manual)
  query.py         ← search/retrieval CLI for Forge
  index.json       ← master index, loaded by Forge each session
  entries/         ← one JSON per entry (created on first import)
  raw/             ← original markdown files, preserved as-is
  README.md        ← this file
```

---

## Import

```bash
# Import a Mindsera export folder
python3 import.py --source mindsera /path/to/mindsera-export/

# Import a Mindsera export zip
python3 import.py --source mindsera /path/to/mindsera-export.zip

# Import a Recall export folder
python3 import.py --source recall /path/to/recall-export/

# Add a quick manual note
python3 import.py --source manual --title "Quick thought" --text "James said X today..."

# Preview what would be imported (no write)
python3 import.py --preview --source mindsera /path/to/export/

# Show index stats
python3 import.py --stats
```

Each entry gets:
- Full JSON stored in `entries/`
- Summary + key ideas + tags + topics + emotion from Claude Haiku
- Deduplication by source + date + title
- Auto-sync to Google Drive `Second Brain/` folder

Cost: ~$0.0007/entry with Haiku. 100 entries ≈ $0.07.

---

## Query (for Forge)

```bash
# Keyword search across title, tags, topics, key ideas, summary
python3 query.py "faith and resilience"

# Most recent N entries
python3 query.py --recent 10

# Filter by topic
python3 query.py --topic faith
python3 query.py --topic finances

# Filter by emotion
python3 query.py --emotion struggling
python3 query.py --emotion hopeful

# Date range (YYYY-MM or YYYY-MM-DD)
python3 query.py --date-range 2024-01 2024-06

# Combine filters
python3 query.py --source mindsera --recent 5
python3 query.py --topic family --emotion grateful

# Full entry by ID
python3 query.py --entry 2024-03-15_grateful-heart

# Control result count
python3 query.py "debt snowball" --top 10
```

---

## Entry Format

**Index record** (lightweight, in `index.json`):
```json
{
  "id":        "2024-03-15_grateful-heart",
  "date":      "2024-03-15",
  "source":    "mindsera",
  "title":     "Grateful Heart",
  "summary":   "James reflected on...",
  "key_ideas": ["faith anchors decisions"],
  "tags":      ["faith", "gratitude", "family"],
  "topics":    ["identity", "spiritual growth"],
  "emotion":   "reflective",
  "word_count": 342,
  "file":      "entries/2024-03-15_grateful-heart.json"
}
```

**Full entry** (in `entries/`): adds `original_content`, `ai_reflection`, `action_items`, `scripture_refs`, `source_url`, `imported_at`, `source_file`.

---

## Supported Sources

| Source   | What it is |
|----------|-----------|
| mindsera | Mindsera journal exports (YAML frontmatter + H2 AI Response split) |
| recall   | Recall content captures (URL + highlights) |
| manual   | Quick notes added directly from Forge |

---

## Google Drive Sync

After each import run, `index.json` and `entries/` sync to the `Second Brain/` folder on James's Google Drive (same OAuth token as vendoo-tool). Raw files are not synced (too large). Sync failures are non-fatal — local always works.

---

## API Key

Loaded from `/home/kallan/todoist-audit/.env` — `ANTHROPIC_API_KEY=`.

---

## Session Usage (Forge)

At session start, Forge can run:
```bash
python3 /home/kallan/second-brain/query.py --recent 5
```
to surface recent context about James. Or target specific queries before giving advice:
```bash
python3 /home/kallan/second-brain/query.py "finances debt"
python3 /home/kallan/second-brain/query.py --topic faith --recent 3
```
