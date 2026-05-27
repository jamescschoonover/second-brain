#!/usr/bin/env python3
"""
recall_import.py — Import Recall knowledge card export into second brain.

Recall exports a ZIP of category folders, each containing Markdown files with
YAML frontmatter (title, tags, createdAt, updatedAt) and bullet-point summaries.

Usage:
  python3 recall_import.py                        # import ~/second-brain/raw/recall_export.zip
  python3 recall_import.py /path/to/recall.zip
  python3 recall_import.py --dry-run              # show what would import, don't save
  python3 recall_import.py --no-ai                # skip Haiku, use title/bullets only
"""

import re
import sys
import json
import zipfile
import argparse
import importlib.util as _ilu
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).parent
ZIP_DEFAULT = BASE_DIR / "raw" / "recall_export.zip"

def _load_brain():
    spec = _ilu.spec_from_file_location("brain", BASE_DIR / "import.py")
    mod  = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

brain = _load_brain()


def parse_recall_md(text: str) -> dict:
    """Parse YAML frontmatter + bullet content from a Recall markdown card."""
    lines = text.splitlines()
    frontmatter = {}
    body_lines = []
    in_fm = False
    fm_done = False
    fm_lines = []

    for line in lines:
        if line.strip() == "---" and not fm_done:
            if not in_fm:
                in_fm = True
            else:
                in_fm = False
                fm_done = True
                # Parse frontmatter
                import yaml
                try:
                    frontmatter = yaml.safe_load("\n".join(fm_lines)) or {}
                except Exception:
                    frontmatter = {}
            continue
        if in_fm:
            fm_lines.append(line)
        elif fm_done:
            body_lines.append(line)

    # If no frontmatter, treat whole text as body
    if not fm_done:
        body_lines = lines

    # Extract bullet points from body
    bullets = []
    sources = []
    in_sources = False
    for line in body_lines:
        stripped = line.strip()
        if stripped.lower().startswith("## source"):
            in_sources = True
            continue
        if stripped.startswith("## ") and in_sources:
            in_sources = False
        if in_sources and stripped.startswith("- ["):
            # Source link: - [label](url)
            m = re.match(r'-\s+\[([^\]]+)\]\(([^)]+)\)', stripped)
            if m:
                sources.append({"label": m.group(1), "url": m.group(2)})
        elif not in_sources and stripped.startswith("-") and len(stripped) > 3:
            bullets.append(stripped.lstrip("- ").strip())

    # Parse dates from frontmatter
    def parse_recall_date(val):
        if not val:
            return None
        s = str(val)
        # "Wed Jun 25 2025 18:30:54 GMT-0400 (Eastern Daylight Time)"
        m = re.search(r'(\w{3}\s+\w{3}\s+\d{1,2}\s+\d{4})', s)
        if m:
            try:
                return datetime.strptime(m.group(1), "%a %b %d %Y").strftime("%Y-%m-%d")
            except Exception:
                pass
        # ISO or simple date
        m2 = re.search(r'(\d{4}-\d{2}-\d{2})', s)
        if m2:
            return m2.group(1)
        return None

    created_date = parse_recall_date(frontmatter.get("createdAt"))
    updated_date = parse_recall_date(frontmatter.get("updatedAt"))

    return {
        "title":        frontmatter.get("title", ""),
        "tags":         frontmatter.get("tags", []),
        "bullets":      bullets,
        "sources":      sources,
        "created_date": created_date,
        "updated_date": updated_date,
    }


def import_zip(zip_path: Path, dry_run: bool = False, use_ai: bool = True) -> int:
    index  = brain.load_index()
    known  = {e["id"] for e in index.get("entries", [])}
    client = None

    if not dry_run and use_ai:
        import os
        from dotenv import load_dotenv
        load_dotenv(Path.home() / "todoist-audit/.env")
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    imported = 0
    skipped  = 0
    errors   = 0

    with zipfile.ZipFile(zip_path) as zf:
        md_files = [n for n in zf.namelist() if n.endswith(".md") and "/" in n]
        print(f"Found {len(md_files)} Recall cards in {zip_path.name}")

        for filepath in sorted(md_files):
            parts = filepath.split("/")
            category = parts[0]
            filename = parts[-1]
            card_name = filename[:-3]  # strip .md

            # Use category + slugified title for ID
            slug = re.sub(r'[^a-z0-9]+', '-', card_name.lower()).strip('-')[:40]
            entry_id = f"recall_{re.sub(r'[^a-z0-9]+', '-', category.lower())[:20]}_{slug}"

            if entry_id in known:
                skipped += 1
                continue

            try:
                text = zf.read(filepath).decode("utf-8", errors="replace")
            except Exception:
                errors += 1
                continue

            parsed = parse_recall_md(text)
            title = parsed["title"] or card_name
            bullets = parsed["bullets"]
            sources = parsed["sources"]
            created_date = parsed["created_date"] or "2025-01-01"

            if dry_run:
                print(f"  [dry-run] IMPORT: [{category}] {title[:50]} — {len(bullets)} bullets, {len(sources)} sources")
                imported += 1
                continue

            # Build summary from bullets
            summary_text = "; ".join(bullets[:3]) if bullets else title
            content_for_brain = f"{title}\n\n" + "\n".join(f"• {b}" for b in bullets)
            if sources:
                content_for_brain += "\n\nSources: " + ", ".join(s["url"] for s in sources[:3])

            if use_ai and client and len(content_for_brain) > 50:
                prompt = f"""Analyze this knowledge card. Return JSON with these keys:
"summary": one sentence describing what this is,
"key_ideas": list of 2-4 short strings,
"tags": list of 3-6 lowercase tags,
"topics": list of 1-3 topic areas,
"content_type": one of [reference, research, fact, concept],
"emotion": neutral

Card category: {category}
Title: {title}
Content:
{content_for_brain[:2000]}"""
                try:
                    msg = client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=300,
                        messages=[{"role": "user", "content": prompt}]
                    )
                    raw = msg.content[0].text.strip()
                    if raw.startswith("```"):
                        raw = raw.split("```")[1]
                        if raw.startswith("json"):
                            raw = raw[4:]
                    analysis = json.loads(raw.strip())
                except Exception as e:
                    analysis = {
                        "summary": summary_text[:120],
                        "key_ideas": bullets[:3],
                        "tags": [category.lower().replace(" ", "-"), "recall", "reference"],
                        "topics": [category.lower()],
                        "content_type": "reference",
                        "emotion": "neutral",
                    }
            else:
                analysis = {
                    "summary": summary_text[:120],
                    "key_ideas": bullets[:3],
                    "tags": [category.lower().replace(" ", "-"), "recall", "reference"],
                    "topics": [category.lower()],
                    "content_type": "reference",
                    "emotion": "neutral",
                }

            # Merge recall tags with AI tags
            recall_tags = [t.lower().replace(" ", "-") for t in parsed["tags"]]
            ai_tags = analysis.get("tags", [])
            all_tags = list(dict.fromkeys(recall_tags + ai_tags))[:10]

            entry = {
                "id":               entry_id,
                "date":             created_date,
                "source":           "recall",
                "title":            title,
                "original_content": content_for_brain,
                "ai_reflection":    "",
                "summary":          analysis.get("summary", summary_text[:120]),
                "key_ideas":        analysis.get("key_ideas", bullets[:3]),
                "action_items":     [],
                "tags":             all_tags,
                "topics":           analysis.get("topics", [category.lower()]),
                "people":           [],
                "emotion":          analysis.get("emotion", "neutral"),
                "content_type":     analysis.get("content_type", "reference"),
                "recall_category":  category,
                "recall_sources":   sources,
                "word_count":       len(content_for_brain.split()),
                "created_at":       datetime.now(timezone.utc).isoformat(),
                "imported_from":    "recall",
            }

            entry_file = brain.ENTRIES_DIR / f"{entry_id}.json"
            entry_file.write_text(json.dumps(entry, indent=2))

            rec = brain.index_entry(entry)
            index["entries"].append(rec)
            index["entry_count"] = len(index["entries"])
            brain.save_index(index)
            known.add(entry_id)

            print(f"  ✓ [{category}] {title[:55]}")
            imported += 1

    return imported, skipped, errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_path", nargs="?", default=str(ZIP_DEFAULT))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-ai", action="store_true", help="Skip Haiku analysis (faster, cheaper)")
    args = parser.parse_args()

    zip_path = Path(args.zip_path)
    if not zip_path.exists():
        print(f"ZIP not found: {zip_path}")
        sys.exit(1)

    print(f"Importing Recall cards from {zip_path.name}...")
    n, skipped, errors = import_zip(zip_path, dry_run=args.dry_run, use_ai=not args.no_ai)
    label = "[DRY RUN] Would import" if args.dry_run else "Imported"
    print(f"\n{label}: {n} cards | Skipped: {skipped} | Errors: {errors}")


if __name__ == "__main__":
    main()
