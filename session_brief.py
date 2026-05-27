#!/usr/bin/env python3
"""
session_brief.py — Second Brain session opener for Forge (Claude Code).

Usage:
  python3 ~/second-brain/session_brief.py           # last 7 days (default)
  python3 ~/second-brain/session_brief.py --days 14 # last 14 days
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR            = Path(__file__).parent
INDEX_FILE          = BASE_DIR / "index.json"
PENDING_ACTIONS_FILE = BASE_DIR / "pending_actions.json"

# Maps source value → display label
SOURCE_LABELS = {
    "mindsera": "Journal",
    "recall":   "Capture",
    "podcast":  "Podcast",
    "manual":   "Note",
}

# Emotional valence for trend detection (positive > 0, negative < 0, neutral = 0)
EMOTION_VALENCE = {
    "hopeful":     1,
    "grateful":    1,
    "determined":  1,
    "motivated":   1,
    "peaceful":    1,
    "joyful":      1,
    "convicted":   0,
    "reflective":  0,
    "planning":    0,
    "conflicted": -1,
    "anxious":    -1,
    "struggling": -1,
}

WIDTH = 44  # ═ bar width


def load_index() -> dict:
    if not INDEX_FILE.exists():
        return None
    try:
        return json.loads(INDEX_FILE.read_text())
    except Exception:
        return None


def load_pending_actions() -> list:
    if not PENDING_ACTIONS_FILE.exists():
        return []
    try:
        data = json.loads(PENDING_ACTIONS_FILE.read_text())
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def parse_date(date_str: str) -> date | None:
    """Parse YYYY-MM-DD string to date object."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def format_date_display(d: date) -> str:
    return d.strftime("%b %-d")


def emotion_trend(emotions: list[str]) -> str:
    """Given oldest→newest emotion list, return ↑ / ↓ / →"""
    if len(emotions) < 2:
        return "→"
    valences = [EMOTION_VALENCE.get(e.lower(), 0) for e in emotions]
    first_half = sum(valences[: len(valences) // 2]) / max(len(valences) // 2, 1)
    second_half = sum(valences[len(valences) // 2 :]) / max(
        len(valences) - len(valences) // 2, 1
    )
    delta = second_half - first_half
    if delta > 0.15:
        return "↑ positive"
    if delta < -0.15:
        return "↓ negative"
    return "→ stable"


def bar(char="═") -> str:
    return char * WIDTH


def main():
    parser = argparse.ArgumentParser(description="Second Brain session brief for Forge")
    parser.add_argument(
        "--days", type=int, default=7, help="How many days back to include (default: 7)"
    )
    args = parser.parse_args()

    today = date.today()
    cutoff = today - timedelta(days=args.days)
    today_display = today.strftime("%Y-%m-%d")

    print()
    print(bar())
    print(f"  SECOND BRAIN BRIEF — {today_display}")
    print(bar())
    print()

    # ── Load index ──────────────────────────────────────────────────────────
    index = load_index()
    if index is None or not index.get("entries"):
        print("Second Brain is empty — run import.py to get started")
        print()
        print(bar())
        return

    all_entries = index.get("entries", [])

    # ── Library totals ───────────────────────────────────────────────────────
    total = len(all_entries)
    type_counts: Counter = Counter()
    for e in all_entries:
        label = SOURCE_LABELS.get(e.get("source", ""), "Note")
        type_counts[label] += 1

    count_parts = " · ".join(
        f"{v} {k.lower()}{'s' if v != 1 else ''}" for k, v in type_counts.most_common()
    )
    print(f"LIBRARY: {total} entr{'y' if total == 1 else 'ies'} ({count_parts})")
    print()

    # ── Recent entries ───────────────────────────────────────────────────────
    recent = []
    for e in all_entries:
        d = parse_date(e.get("date", ""))
        if d and d >= cutoff:
            recent.append((d, e))

    # Sort oldest→newest for trajectory, then display newest-first
    recent.sort(key=lambda x: x[0])
    recent_display = list(reversed(recent))

    print(f"RECENT (last {args.days} days): {len(recent)} entr{'y' if len(recent) == 1 else 'ies'}")
    for d, e in recent_display:
        label = SOURCE_LABELS.get(e.get("source", ""), "Note")
        title = e.get("title", "(no title)")
        emotion = e.get("emotion", "")
        date_str = format_date_display(d)
        emotion_suffix = f" · {emotion}" if emotion else ""
        print(f'  [{label}] "{title}" — {date_str}{emotion_suffix}')
    print()

    # ── Emotional trajectory ─────────────────────────────────────────────────
    emotions_chrono = [e.get("emotion", "") for _, e in recent if e.get("emotion")]
    if emotions_chrono:
        trajectory_str = " → ".join(emotions_chrono)
        trend = emotion_trend(emotions_chrono)
        print(f"EMOTIONAL TRAJECTORY: {trajectory_str}  {trend}")
    else:
        print("EMOTIONAL TRAJECTORY: (no emotion data yet)")
    print()

    # ── Recurring themes ─────────────────────────────────────────────────────
    tag_counter: Counter = Counter()
    for _, e in recent:
        for tag in e.get("tags", []):
            tag_counter[tag.lower()] += 1
        for topic in e.get("topics", []):
            tag_counter[topic.lower()] += 1

    top_themes = [t for t, _ in tag_counter.most_common(4)]
    if top_themes:
        print(f"RECURRING THEMES this week: {', '.join(top_themes)}")
    else:
        print("RECURRING THEMES this week: (none yet)")
    print()

    # ── Pending action items ─────────────────────────────────────────────────
    pending_actions = load_pending_actions()
    pending = [a for a in pending_actions if a.get("status", "pending") == "pending"]

    print(f"PENDING ACTION ITEMS: {len(pending)}")
    for action in pending[:5]:
        text  = action.get("text", action.get("action", "(no text)"))
        source = action.get("source", "")
        added  = action.get("added", action.get("date", ""))
        d = parse_date(added)
        date_part = f" ({format_date_display(d)})" if d else ""
        source_part = f" — {source}" if source else ""
        print(f'  ✗ "{text}"{source_part}{date_part}')

    if len(pending) == 0:
        print("  (none — you're clear!)")
    elif len(pending) > 5:
        print(f"  ... and {len(pending) - 5} more — run: python3 query.py --has-actions")
    print()

    # ── People referenced ────────────────────────────────────────────────────
    people_counter: Counter = Counter()
    for _, e in recent:
        for person in e.get("people", []):
            if person.strip():
                people_counter[person] += 1

    if people_counter:
        top_people = people_counter.most_common(3)
        people_str = ", ".join(f"{name} ({count})" for name, count in top_people)
        print(f"PEOPLE referenced this week: {people_str}")
        print()

    print(bar())
    print()


if __name__ == "__main__":
    main()
