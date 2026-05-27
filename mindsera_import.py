#!/usr/bin/env python3
"""
mindsera_import.py — Import Mindsera journal export into second brain.

Each entry folder contains:
  entry.md    — raw journal content (often voice transcript)
  analysis.md — Mindsera's AI emotional breakdown + summary

Usage:
  python3 mindsera_import.py                           # import ~/second-brain/raw/mindsera.zip
  python3 mindsera_import.py /path/to/mindsera.zip
  python3 mindsera_import.py --dry-run                 # show what would import, don't save
"""

import sys
import json
import zipfile
import argparse
import importlib.util as _ilu
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).parent
ZIP_DEFAULT = BASE_DIR / "raw" / "mindsera.zip"

# Load import.py (can't `import import`)
def _load_brain():
    spec = _ilu.spec_from_file_location("brain", BASE_DIR / "import.py")
    mod  = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

brain = _load_brain()


def parse_analysis(text: str) -> dict:
    """Extract summary bullets and emotional state from Mindsera analysis.md."""
    summary_lines = []
    emotions = {}
    in_summary = False
    in_emotions = False

    for line in text.splitlines():
        stripped = line.strip()
        if "**Summary:**" in stripped:
            in_summary = True
            in_emotions = False
            continue
        if "**Emotional State:**" in stripped:
            in_summary = False
            in_emotions = True
            continue
        # New bold section header = end of current section
        if stripped.startswith("**") and stripped.endswith(":**") and in_summary:
            in_summary = False
        if stripped.startswith("**") and stripped.endswith(":**") and in_emotions:
            in_emotions = False

        if in_summary and stripped.startswith("-"):
            summary_lines.append(stripped.lstrip("- ").strip())
        elif in_emotions and stripped.startswith("-"):
            # "- Stress (30%):" pattern
            import re
            m = re.match(r"-\s+(\w[\w\s]+?)\s+\((\d+)%\)", stripped)
            if m:
                emotions[m.group(1).lower()] = int(m.group(2))

    return {
        "summary_bullets": summary_lines,
        "emotions": emotions,
    }


def import_zip(zip_path: Path, dry_run: bool = False) -> int:
    index   = brain.load_index()
    known   = {e["id"] for e in index.get("entries", [])}
    client  = None

    # Lazy-load anthropic client
    if not dry_run:
        import os
        from dotenv import load_dotenv
        load_dotenv(Path.home() / "todoist-audit/.env")
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    imported = 0
    skipped  = 0

    with zipfile.ZipFile(zip_path) as zf:
        # Group files by entry folder
        folders = {}
        for name in zf.namelist():
            parts = name.split("/")
            if len(parts) >= 2 and parts[1] in ("entry.md", "analysis.md"):
                folder = parts[0]
                folders.setdefault(folder, {})
                folders[folder][parts[1]] = name

        print(f"Found {len(folders)} Mindsera entries in {zip_path.name}")

        for folder, files in sorted(folders.items()):
            # Parse date from folder name: "2026-05-07 - cmow3ki3r..."
            date_str = folder[:10] if len(folder) >= 10 else "2026-05-01"
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                date_str = "2026-05-01"

            entry_text = ""
            analysis_text = ""
            if "entry.md" in files:
                entry_text = zf.read(files["entry.md"]).decode("utf-8", errors="replace")
            if "analysis.md" in files:
                analysis_text = zf.read(files["analysis.md"]).decode("utf-8", errors="replace")

            if not entry_text.strip():
                skipped += 1
                continue

            # Use folder ID suffix for uniqueness (multiple entries per day)
            folder_short = folder.split(" - ")[-1][:12] if " - " in folder else folder[:12]
            entry_id = f"{date_str}_mindsera-{folder_short}"

            if entry_id in known:
                print(f"  SKIP (exists): {date_str}")
                skipped += 1
                continue

            # Parse Mindsera analysis
            parsed = parse_analysis(analysis_text) if analysis_text else {}
            summary_bullets = parsed.get("summary_bullets", [])
            emotions = parsed.get("emotions", {})

            if dry_run:
                print(f"  [dry-run] IMPORT: {date_str} — {len(entry_text)} chars, "
                      f"{len(summary_bullets)} summary bullets, emotions: {emotions}")
                imported += 1
                continue

            # Use Mindsera summary as ai_reflection, ask Haiku for tags/topics
            ai_reflection = "\n".join(f"• {b}" for b in summary_bullets) if summary_bullets else ""

            # Haiku analysis for tags/topics/key_ideas
            prompt = f"""Analyze this journal entry. Return JSON with these keys:
"summary": one sentence,
"key_ideas": list of 3-5 short strings,
"action_items": list of any action items mentioned,
"tags": list of 3-8 lowercase tags (e.g. health, finance, faith, delivery, reselling, relationships),
"topics": list of 2-4 topic areas,
"content_type": one of [journal-entry, reflection, planning, voice-note],
"emotion": primary emotion word

Entry ({date_str}):
{entry_text[:3000]}"""

            try:
                msg = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=400,
                    messages=[{"role": "user", "content": prompt}]
                )
                raw = msg.content[0].text.strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                analysis = json.loads(raw.strip())
            except Exception as e:
                print(f"  ⚠️  Haiku failed for {date_str}: {e} — using defaults")
                analysis = {
                    "summary": summary_bullets[0] if summary_bullets else "",
                    "key_ideas": summary_bullets[:3],
                    "action_items": [],
                    "tags": ["journal", "mindsera"],
                    "topics": ["daily-reflection"],
                    "content_type": "journal-entry",
                    "emotion": list(emotions.keys())[0] if emotions else "",
                }

            entry = {
                "id":               entry_id,
                "date":             date_str,
                "source":           "mindsera",
                "title":            f"Journal — {date_str}",
                "original_content": entry_text,
                "ai_reflection":    ai_reflection,
                "mindsera_emotions": emotions,
                "summary":          analysis.get("summary", ""),
                "key_ideas":        analysis.get("key_ideas", []),
                "action_items":     analysis.get("action_items", []),
                "tags":             analysis.get("tags", ["journal"]),
                "topics":           analysis.get("topics", []),
                "people":           [],
                "emotion":          analysis.get("emotion", list(emotions.keys())[0] if emotions else "reflective"),
                "content_type":     analysis.get("content_type", "journal-entry"),
                "word_count":       len(entry_text.split()),
                "created_at":       datetime.now(timezone.utc).isoformat(),
                "imported_from":    "mindsera",
            }

            # Save entry file
            entry_file = brain.ENTRIES_DIR / f"{entry_id}.json"
            entry_file.write_text(json.dumps(entry, indent=2))

            # Index it
            rec = brain.index_entry(entry)
            index["entries"].append(rec)
            index["entry_count"] = len(index["entries"])
            brain.save_index(index)
            known.add(entry_id)

            emotion_str = ", ".join(f"{k} {v}%" for k, v in list(emotions.items())[:3])
            print(f"  ✓ {date_str}: {analysis.get('summary','')[:60]} [{emotion_str}]")
            imported += 1

    return imported


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_path", nargs="?", default=str(ZIP_DEFAULT))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    zip_path = Path(args.zip_path)
    if not zip_path.exists():
        print(f"ZIP not found: {zip_path}")
        sys.exit(1)

    print(f"Importing Mindsera entries from {zip_path.name}...")
    n = import_zip(zip_path, dry_run=args.dry_run)
    print(f"\n{'[DRY RUN] Would import' if args.dry_run else 'Imported'}: {n} entries")


if __name__ == "__main__":
    main()
