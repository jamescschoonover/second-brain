#!/usr/bin/env python3
"""
query.py — Second Brain search for James Schoonover (Forge).

KEYWORD SEARCH
  python3 query.py "debt snowball"
  python3 query.py "faith identity" --type journal
  python3 query.py "mindset" --person "Matt Dearden" --has-actions

FILTER BY TYPE (journal / capture / podcast / note)
  python3 query.py --type journal          # Mindsera reflections only
  python3 query.py --type capture          # Recall saves only
  python3 query.py --type podcast          # audio/video only

FILTER BY CONTENT SHAPE
  python3 query.py --content-type teaching
  python3 query.py --has-actions           # entries where you committed to something
  python3 query.py --has-scripture         # faith entries with Bible refs

FILTER BY PERSON / TAG / TOPIC / EMOTION
  python3 query.py --person "Matt Dearden"
  python3 query.py --tag debt
  python3 query.py --topic finances
  python3 query.py --emotion determined

DATE & RECENCY
  python3 query.py --recent 10
  python3 query.py --since 2026-01-01
  python3 query.py --date-range 2025-06 2026-01

DEEP FULL-TEXT (searches original content, not just index)
  python3 query.py --fulltext "snowball method"

PODCAST TIMESTAMP JUMP LINKS
  python3 query.py --find-moment "get uncomfortable"

FULL ENTRY
  python3 query.py --entry 2026-03-15_debt-mindset

DISCOVERY (what's in the library)
  python3 query.py --list-tags
  python3 query.py --list-topics
  python3 query.py --list-people
  python3 query.py --list-types

COMBINE FREELY
  python3 query.py "identity" --type journal --emotion struggling --has-actions
  python3 query.py --person "Matt Dearden" --topic finances --recent 5
"""

import argparse
import json
import re
import sys
from pathlib import Path

BASE_DIR    = Path(__file__).parent
INDEX_FILE  = BASE_DIR / "index.json"
ENTRIES_DIR = BASE_DIR / "entries"

TOP_N = 5

# Maps --type flag to source values
TYPE_MAP = {
    "journal": ["mindsera"],
    "capture": ["recall"],
    "podcast": ["podcast"],
    "note":    ["manual"],
    "all":     [],  # no filter
}

CONTENT_TYPES = [
    "personal-reflection", "information-capture", "teaching",
    "motivation", "storytelling", "prayer", "planning", "conversation",
]


# ---------------------------------------------------------------------------
# Index + entry loading
# ---------------------------------------------------------------------------

def load_index() -> dict:
    if not INDEX_FILE.exists():
        print("[error] No index.json found. Run import.py first.")
        sys.exit(1)
    try:
        return json.loads(INDEX_FILE.read_text())
    except Exception as e:
        print(f"[error] Could not load index.json: {e}")
        sys.exit(1)


def load_entry(entry_id: str) -> dict | None:
    path = ENTRIES_DIR / f"{entry_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def all_entries(index: dict) -> list[dict]:
    return index.get("entries", [])


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def apply_filters(entries: list[dict], args) -> list[dict]:
    result = entries

    # --type  →  source filter
    if getattr(args, "type", None) and args.type != "all":
        allowed_sources = TYPE_MAP.get(args.type, [])
        if allowed_sources:
            result = [e for e in result if e.get("source") in allowed_sources]

    # --source (legacy/direct)
    if getattr(args, "source", None):
        result = [e for e in result if e.get("source") == args.source]

    # --content-type
    if getattr(args, "content_type", None):
        ct = args.content_type.lower()
        result = [e for e in result if ct in (e.get("content_type") or "").lower()]

    # --person
    if getattr(args, "person", None):
        name = args.person.lower()
        result = [
            e for e in result
            if any(name in p.lower() for p in (e.get("people") or []))
            or name in e.get("title", "").lower()
        ]

    # --tag
    if getattr(args, "tag", None):
        tag = args.tag.lower()
        result = [
            e for e in result
            if any(tag in t.lower() for t in (e.get("tags") or []))
        ]

    # --topic
    if getattr(args, "topic", None):
        topic = args.topic.lower()
        result = [
            e for e in result
            if any(topic in t.lower() for t in (e.get("topics") or []))
        ]

    # --emotion
    if getattr(args, "emotion", None):
        em = args.emotion.lower()
        result = [e for e in result if em in (e.get("emotion") or "").lower()]

    # --has-actions
    if getattr(args, "has_actions", False):
        result = [e for e in result if e.get("has_actions")]

    # --has-scripture
    if getattr(args, "has_scripture", False):
        result = [e for e in result if e.get("has_scripture")]

    # --since
    if getattr(args, "since", None):
        result = [e for e in result if (e.get("date") or "") >= args.since]

    # --date-range
    if getattr(args, "date_range", None):
        start, end = args.date_range
        result = [
            e for e in result
            if start <= (e.get("date") or "") <= end
        ]

    return result


# ---------------------------------------------------------------------------
# Keyword scoring
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[a-z0-9']+", text.lower()) if len(t) > 1]


def score_entry(entry: dict, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    score = 0.0
    title       = " ".join(tokenize(entry.get("title", "")))
    summary     = " ".join(tokenize(entry.get("summary", "")))
    tags        = " ".join(tokenize(" ".join(entry.get("tags", []))))
    topics      = " ".join(tokenize(" ".join(entry.get("topics", []))))
    key_ideas   = " ".join(tokenize(" ".join(entry.get("key_ideas", []))))
    people      = " ".join(tokenize(" ".join(entry.get("people", []))))
    applicability = " ".join(tokenize(entry.get("applicability", "")))

    for kw in keywords:
        if kw in title:       score += 3.0
        if kw in people:      score += 2.5
        if kw in tags:        score += 2.0
        if kw in topics:      score += 2.0
        if kw in summary:     score += 1.5
        if kw in key_ideas:   score += 1.0
        if kw in applicability: score += 1.0
    return score


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

SOURCE_LABELS = {
    "mindsera": "Journal",
    "recall":   "Capture",
    "podcast":  "Podcast",
    "manual":   "Note",
}

CONTENT_TYPE_LABELS = {
    "personal-reflection": "reflection",
    "information-capture": "capture",
    "teaching":            "teaching",
    "motivation":          "motivation",
    "storytelling":        "story",
    "prayer":              "prayer",
    "planning":            "planning",
    "conversation":        "conversation",
}


def _wrap(text: str, width: int = 72, indent: str = "    ") -> str:
    words = text.split()
    lines, line = [], []
    for w in words:
        if len(" ".join(line + [w])) > width:
            lines.append(indent + " ".join(line))
            line = [w]
        else:
            line.append(w)
    if line:
        lines.append(indent + " ".join(line))
    return "\n".join(lines)


def print_snippet(entry: dict, rank: int, verbose: bool = False):
    eid     = entry.get("id", "")
    date    = entry.get("date", "?")
    title   = entry.get("title", "Untitled")
    source  = SOURCE_LABELS.get(entry.get("source", ""), entry.get("source", ""))
    ct      = CONTENT_TYPE_LABELS.get(entry.get("content_type", ""), "")
    emotion = entry.get("emotion", "")
    wc      = entry.get("word_count", 0)
    tags    = entry.get("tags", [])
    people  = entry.get("people", [])
    summary = entry.get("summary", "")
    dur     = entry.get("duration", "")
    has_act = entry.get("has_actions", False)
    has_scr = entry.get("has_scripture", False)
    apply_  = entry.get("applicability", "")
    url     = entry.get("source_url", "")

    # Header
    print(f"\n#{rank}  [{date}]  {title}")

    # Meta row
    meta_parts = [source]
    if ct:
        meta_parts.append(ct)
    if emotion:
        meta_parts.append(emotion)
    if dur:
        meta_parts.append(f"{dur}")
    else:
        meta_parts.append(f"{wc:,} words")
    flags = []
    if has_act:
        flags.append("✓ actions")
    if has_scr:
        flags.append("✓ scripture")
    if flags:
        meta_parts.append(" | ".join(flags))
    print(f"    {' · '.join(meta_parts)}")

    # People
    if people:
        print(f"    People : {', '.join(people)}")

    # Tags (first 6)
    if tags:
        shown = tags[:6]
        more  = f" +{len(tags)-6}" if len(tags) > 6 else ""
        print(f"    Tags   : {', '.join(shown)}{more}")

    # Summary
    if summary:
        print(_wrap(summary))

    # Applicability (verbose or if short)
    if apply_ and (verbose or len(apply_) < 100):
        print(f"    → {apply_}")

    # Link + ID
    if url:
        print(f"    Link   : {url}")
    print(f"    ID     : {eid}")


# ---------------------------------------------------------------------------
# Search commands
# ---------------------------------------------------------------------------

def cmd_search(keywords_raw: str, args, index: dict):
    keywords = tokenize(keywords_raw)
    entries  = apply_filters(all_entries(index), args)

    scored = [(e, score_entry(e, keywords)) for e in entries]
    scored = [(e, s) for e, s in scored if s > 0]
    scored.sort(key=lambda x: (-x[1], -(x[0].get("word_count") or 0)))

    n   = getattr(args, "top", TOP_N)
    top = scored[:n]

    if not top:
        print(f"\nNo matches for \"{keywords_raw}\".")
        _suggest_alternatives(args, index)
        return

    label = _active_filter_label(args)
    print(f"\nTop {len(top)} results for \"{keywords_raw}\"{label}:")
    for rank, (e, _) in enumerate(top, 1):
        print_snippet(e, rank)
    print(f"\n{'─'*60}")
    print(f"Total searched: {len(entries)} entries")


def cmd_recent(n: int, args, index: dict):
    entries = apply_filters(all_entries(index), args)
    sorted_e = sorted(entries, key=lambda e: e.get("date", ""), reverse=True)
    top = sorted_e[:n]

    if not top:
        print("\nNo entries found.")
        return

    label = _active_filter_label(args)
    print(f"\nMost recent {len(top)} entries{label}:")
    for rank, e in enumerate(top, 1):
        print_snippet(e, rank)
    print(f"\n{'─'*60}")


def cmd_fulltext(phrase: str, args, index: dict):
    """Search original_content inside entry files — slower, deeper."""
    entries = apply_filters(all_entries(index), args)
    phrase_lower = phrase.lower()
    hits = []

    for e in entries:
        entry = load_entry(e["id"])
        if not entry:
            continue
        content = (entry.get("original_content") or "").lower()
        if phrase_lower in content:
            # Find context window around the match
            idx = content.find(phrase_lower)
            start = max(0, idx - 80)
            end   = min(len(content), idx + len(phrase_lower) + 80)
            ctx   = entry.get("original_content", "")[start:end].replace("\n", " ").strip()
            hits.append((e, ctx))

    if not hits:
        print(f"\nNo full-text matches for \"{phrase}\".")
        return

    print(f"\n{len(hits)} full-text match(es) for \"{phrase}\":")
    for rank, (e, ctx) in enumerate(hits[:10], 1):
        print_snippet(e, rank)
        print(f"    Context: \"...{ctx}...\"")
    print(f"\n{'─'*60}")


def cmd_find_moment(phrase: str, index: dict):
    """Search inside podcast segments for a phrase — returns timestamp deep-links."""
    kws = [k.lower() for k in phrase.split()]
    hits = []

    for idx_e in all_entries(index):
        if idx_e.get("source") not in ("podcast",):
            continue
        entry = load_entry(idx_e["id"])
        if not entry:
            continue
        for seg in (entry.get("segments") or []):
            text = seg.get("text", "").lower()
            if all(k in text for k in kws):
                hits.append({
                    "title":   entry["title"],
                    "date":    entry["date"],
                    "time":    seg["time"],
                    "url":     seg.get("url", entry.get("source_url", "")),
                    "context": seg["text"].strip(),
                })

    if not hits:
        print(f"\nNo moments found for \"{phrase}\".")
        return

    print(f"\n{len(hits)} moment(s) matching \"{phrase}\":")
    for i, h in enumerate(hits, 1):
        print(f"\n#{i}  {h['title']}  [{h['date']}]")
        print(f"    At {h['time']}  →  {h['url']}")
        ctx = h["context"][:200] + ("..." if len(h["context"]) > 200 else "")
        print(f"    \"{ctx}\"")


def cmd_full_entry(entry_id: str):
    entry = load_entry(entry_id)
    if not entry:
        if not ENTRIES_DIR.exists():
            print("[error] entries/ directory not found.")
            sys.exit(1)
        candidates = [f.stem for f in ENTRIES_DIR.glob("*.json") if entry_id in f.stem]
        if len(candidates) == 1:
            entry = load_entry(candidates[0])
        elif len(candidates) > 1:
            print(f"Ambiguous ID — matches: {candidates}")
            sys.exit(1)
        else:
            print(f"Entry not found: {entry_id}")
            sys.exit(1)
    print(json.dumps(entry, indent=2))


# ---------------------------------------------------------------------------
# Discovery commands
# ---------------------------------------------------------------------------

def cmd_list_tags(index: dict):
    counts: dict[str, int] = {}
    for e in all_entries(index):
        for t in (e.get("tags") or []):
            counts[t] = counts.get(t, 0) + 1
    if not counts:
        print("No tags yet.")
        return
    sorted_tags = sorted(counts.items(), key=lambda x: -x[1])
    print(f"\nAll tags ({len(sorted_tags)} unique):\n")
    for tag, n in sorted_tags:
        bar = "█" * min(n, 20)
        print(f"  {tag:<30} {bar} {n}")


def cmd_list_topics(index: dict):
    counts: dict[str, int] = {}
    for e in all_entries(index):
        for t in (e.get("topics") or []):
            counts[t] = counts.get(t, 0) + 1
    if not counts:
        print("No topics yet.")
        return
    print(f"\nTopics ({len(counts)} unique):\n")
    for topic, n in sorted(counts.items(), key=lambda x: -x[1]):
        bar = "█" * min(n, 30)
        print(f"  {topic:<25} {bar} {n}")


def cmd_list_people(index: dict):
    counts: dict[str, int] = {}
    for e in all_entries(index):
        for p in (e.get("people") or []):
            counts[p] = counts.get(p, 0) + 1
    if not counts:
        print("No people indexed yet.")
        return
    print(f"\nPeople mentioned ({len(counts)}):\n")
    for person, n in sorted(counts.items(), key=lambda x: -x[1]):
        bar = "█" * min(n, 30)
        print(f"  {person:<30} {bar} {n} entries")


def cmd_list_types(index: dict):
    source_counts: dict[str, int] = {}
    ct_counts: dict[str, int] = {}
    for e in all_entries(index):
        src = SOURCE_LABELS.get(e.get("source", ""), e.get("source", "unknown"))
        source_counts[src] = source_counts.get(src, 0) + 1
        ct = e.get("content_type", "unknown")
        ct_counts[ct] = ct_counts.get(ct, 0) + 1

    total = len(all_entries(index))
    print(f"\nLibrary overview — {total} total entries\n")
    print("By type:")
    for src, n in sorted(source_counts.items(), key=lambda x: -x[1]):
        bar = "█" * min(n, 30)
        print(f"  {src:<12} {bar} {n}")
    print("\nBy content shape:")
    for ct, n in sorted(ct_counts.items(), key=lambda x: -x[1]):
        bar = "█" * min(n, 30)
        print(f"  {ct:<25} {bar} {n}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _active_filter_label(args) -> str:
    parts = []
    if getattr(args, "type", None) and args.type != "all":
        parts.append(f"type={args.type}")
    if getattr(args, "person", None):
        parts.append(f"person={args.person}")
    if getattr(args, "tag", None):
        parts.append(f"tag={args.tag}")
    if getattr(args, "topic", None):
        parts.append(f"topic={args.topic}")
    if getattr(args, "emotion", None):
        parts.append(f"emotion={args.emotion}")
    if getattr(args, "has_actions", False):
        parts.append("has-actions")
    if getattr(args, "has_scripture", False):
        parts.append("has-scripture")
    return f" [{', '.join(parts)}]" if parts else ""


def _suggest_alternatives(args, index: dict):
    total = len(all_entries(index))
    filtered = len(apply_filters(all_entries(index), args))
    if filtered < total:
        print(f"  ({filtered} entries matched your filters out of {total} total — try removing a filter)")
    else:
        print(f"  ({total} entries total — try different keywords or --list-tags to browse)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Query James Schoonover's Second Brain",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("KEYWORD")[1] if "KEYWORD" in __doc__ else "",
    )

    # Positional
    parser.add_argument("query", nargs="?", help="Keyword search string")

    # Type / source
    type_choices = list(TYPE_MAP.keys())
    parser.add_argument("--type",   choices=type_choices, help="Content type: journal|capture|podcast|note|all")
    parser.add_argument("--source", choices=["mindsera", "recall", "manual", "podcast"], help="Filter by raw source")

    # Content shape
    parser.add_argument("--content-type", dest="content_type",
                        choices=CONTENT_TYPES, help="Filter by content shape")
    parser.add_argument("--has-actions",  dest="has_actions",  action="store_true",
                        help="Only entries with action items")
    parser.add_argument("--has-scripture", dest="has_scripture", action="store_true",
                        help="Only entries with Bible references")

    # Facet filters
    parser.add_argument("--person",  help="Filter by person mentioned (partial match)")
    parser.add_argument("--tag",     help="Filter by tag (partial match)")
    parser.add_argument("--topic",   help="Filter by topic (partial match)")
    parser.add_argument("--emotion", help="Filter by emotion (partial match)")

    # Date
    parser.add_argument("--recent",     type=int, metavar="N", help="N most recent entries")
    parser.add_argument("--since",      metavar="DATE", help="Entries on or after YYYY-MM-DD")
    parser.add_argument("--date-range", nargs=2, metavar=("START", "END"),
                        help="Date range (YYYY-MM or YYYY-MM-DD)")

    # Deep search
    parser.add_argument("--fulltext",    metavar="PHRASE", help="Search inside original content (slower)")
    parser.add_argument("--find-moment", dest="find_moment", metavar="PHRASE",
                        help="Search podcast segments — returns timestamp jump links")

    # Single entry
    parser.add_argument("--entry", metavar="ID", help="Print full entry JSON by ID")

    # Discovery
    parser.add_argument("--list-tags",    dest="list_tags",    action="store_true")
    parser.add_argument("--list-topics",  dest="list_topics",  action="store_true")
    parser.add_argument("--list-people",  dest="list_people",  action="store_true")
    parser.add_argument("--list-types",   dest="list_types",   action="store_true")

    # Output control
    parser.add_argument("--top", type=int, default=TOP_N, help=f"Max results (default {TOP_N})")

    args = parser.parse_args()

    # Full entry — no index needed
    if args.entry:
        cmd_full_entry(args.entry)
        return

    index = load_index()

    if not all_entries(index):
        print("Second Brain is empty. Run import.py to add entries.")
        return

    # Discovery
    if args.list_tags:
        cmd_list_tags(index)
        return
    if args.list_topics:
        cmd_list_topics(index)
        return
    if args.list_people:
        cmd_list_people(index)
        return
    if args.list_types:
        cmd_list_types(index)
        return

    # Deep searches
    if args.find_moment:
        cmd_find_moment(args.find_moment, index)
        return
    if args.fulltext:
        cmd_fulltext(args.fulltext, args, index)
        return

    # Recent / filter-only
    if args.recent:
        cmd_recent(args.recent, args, index)
        return

    # Filters with no keyword → treat as recent
    has_filter = any([
        getattr(args, "type", None),
        getattr(args, "person", None),
        getattr(args, "tag", None),
        getattr(args, "topic", None),
        getattr(args, "emotion", None),
        getattr(args, "has_actions", False),
        getattr(args, "has_scripture", False),
        getattr(args, "since", None),
        getattr(args, "date_range", None),
        getattr(args, "content_type", None),
    ])
    if has_filter and not args.query:
        cmd_recent(args.top, args, index)
        return

    # Keyword search
    if args.query:
        cmd_search(args.query, args, index)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
