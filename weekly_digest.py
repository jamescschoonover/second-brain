#!/usr/bin/env python3
"""
weekly_digest.py — Second Brain weekly summary for James Schoonover.

Usage:
  python3 ~/second-brain/weekly_digest.py                        # current week (Mon–Sun)
  python3 ~/second-brain/weekly_digest.py --week-of 2026-05-19  # specific week
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR             = Path(__file__).parent
INDEX_FILE           = BASE_DIR / "index.json"
PENDING_ACTIONS_FILE = BASE_DIR / "pending_actions.json"
DIGESTS_DIR          = BASE_DIR / "digests"

SOURCE_LABELS = {
    "mindsera": "Journals",
    "recall":   "Captures",
    "podcast":  "Podcasts",
    "manual":   "Notes",
}

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

WIDTH = 52  # ═ bar width


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_index() -> dict | None:
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
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def week_bounds(reference: date) -> tuple[date, date]:
    """Return Monday and Sunday for the ISO week containing reference."""
    monday = reference - timedelta(days=reference.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def fmt_date(d: date) -> str:
    return d.strftime("%b %-d")


def fmt_date_year(d: date) -> str:
    return d.strftime("%b %-d, %Y")


def emotion_trend(emotions: list[str]) -> tuple[str, str]:
    """Return (trajectory_string, most_common_label)."""
    if not emotions:
        return "(none)", ""

    trajectory = " → ".join(emotions)

    counter: Counter = Counter(emotions)
    most_common, count = counter.most_common(1)[0]
    most_label = f"{most_common} ({count} entr{'y' if count == 1 else 'ies'})"

    return trajectory, most_label


def bar(char="═") -> str:
    return char * WIDTH


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Second Brain weekly digest")
    parser.add_argument(
        "--week-of",
        metavar="YYYY-MM-DD",
        help="Any date within the target week (default: current week)",
    )
    args = parser.parse_args()

    # Determine week
    if args.week_of:
        ref = parse_date(args.week_of)
        if ref is None:
            print(f"[error] Could not parse date: {args.week_of}")
            sys.exit(1)
    else:
        ref = date.today()

    monday, sunday = week_bounds(ref)
    week_label = f"Week of {fmt_date(monday)}–{fmt_date_year(sunday)}"

    # ── Load data ──────────────────────────────────────────────────────────
    index = load_index()
    all_entries = index.get("entries", []) if index else []

    pending_actions = load_pending_actions()

    # Filter entries to this week
    week_entries = []
    for e in all_entries:
        d = parse_date(e.get("date", ""))
        if d and monday <= d <= sunday:
            week_entries.append((d, e))

    week_entries.sort(key=lambda x: x[0])  # oldest → newest

    # ── Build digest lines ────────────────────────────────────────────────
    lines = []

    def add(line=""):
        lines.append(line)

    add(f"SECOND BRAIN WEEKLY DIGEST — {week_label}")
    add(bar())
    add()

    # Entry counts
    if not week_entries:
        add("THIS WEEK: 0 new entries")
        add()
    else:
        add(f"THIS WEEK: {len(week_entries)} new entr{'y' if len(week_entries) == 1 else 'ies'}")
        source_counts: Counter = Counter()
        for _, e in week_entries:
            label = SOURCE_LABELS.get(e.get("source", ""), "Notes")
            source_counts[label] += 1

        # Show in a natural order
        for label in ["Journals", "Podcasts", "Captures", "Notes"]:
            count = source_counts.get(label, 0)
            if count > 0:
                add(f"  {label:<10}: {count}")
        add()

    # Tags / topics / people
    tag_counter:    Counter = Counter()
    people_counter: Counter = Counter()
    emotions_chrono: list[str] = []
    scripture_counter: Counter = Counter()

    for _, e in week_entries:
        for tag in e.get("tags", []):
            tag_counter[tag.lower()] += 1
        for topic in e.get("topics", []):
            tag_counter[topic.lower()] += 1
        for person in e.get("people", []):
            if person.strip():
                people_counter[person] += 1
        emotion = e.get("emotion", "")
        if emotion:
            emotions_chrono.append(emotion)
        for ref_str in e.get("scripture_refs", []):
            if ref_str.strip():
                scripture_counter[ref_str.strip()] += 1

    # Top themes
    top_themes = tag_counter.most_common(5)
    if top_themes:
        themes_str = ", ".join(f"{t} ({c})" for t, c in top_themes)
        add(f"TOP THEMES: {themes_str}")
    else:
        add("TOP THEMES: (none yet)")

    # Top people
    top_people = people_counter.most_common(3)
    if top_people:
        people_str = ", ".join(f"{n} ({c})" for n, c in top_people)
        add(f"TOP PEOPLE: {people_str}")
    add()

    # Emotional week
    trajectory, most_label = emotion_trend(emotions_chrono)
    add(f"EMOTIONAL WEEK: {trajectory}")
    if most_label:
        add(f"  Most common: {most_label}")
    add()

    # Action items this week
    # Entries this week that have actions flagged
    week_entry_ids = {e.get("id") for _, e in week_entries}

    # Pending actions added this week
    week_actions = [
        a for a in pending_actions
        if parse_date(a.get("added", a.get("date", ""))) is not None
        and monday <= parse_date(a.get("added", a.get("date", ""))) <= sunday
    ]

    # Count across all entries this week
    total_actions_flagged = sum(
        1 for _, e in week_entries if e.get("has_actions")
    )

    add(f"ACTION ITEMS THIS WEEK: {total_actions_flagged} captured")

    done_actions    = [a for a in week_actions if a.get("status") == "done"]
    pending_week    = [a for a in week_actions if a.get("status", "pending") == "pending"]

    for a in done_actions[:3]:
        text = a.get("text", a.get("action", "(no text)"))
        add(f'  ✓ DONE   : "{text}"')

    shown_pending = 0
    for a in pending_week[:3]:
        text = a.get("text", a.get("action", "(no text)"))
        add(f'  ✗ PENDING: "{text}"')
        shown_pending += 1

    remaining_pending_count = len(pending_week) - shown_pending
    if remaining_pending_count > 0:
        add(f"  ✗ PENDING: {remaining_pending_count} more — run: python3 query.py --has-actions")

    if total_actions_flagged == 0 and not week_actions:
        add("  (no actions captured this week)")
    add()

    # Scripture
    if scripture_counter:
        refs = ", ".join(
            f"{ref} ({count})" if count > 1 else ref
            for ref, count in scripture_counter.most_common()
        )
        add(f"SCRIPTURE REFERENCES: {refs}")
        add()

    # Carry forward
    all_pending = [a for a in pending_actions if a.get("status", "pending") == "pending"]
    pending_count = len(all_pending)
    if pending_count > 0:
        add(f"CARRY FORWARD: {pending_count} pending action item{'s' if pending_count != 1 else ''} need attention this week.")
        next_sunday = sunday + timedelta(days=7)
        add(f"  Run: python3 weekly_digest.py --week-of {next_sunday.isoformat()} next Sunday to track progress.")
    else:
        add("CARRY FORWARD: All action items resolved — great week.")

    add()
    add(bar())

    # ── Output ─────────────────────────────────────────────────────────────
    output = "\n".join(lines) + "\n"
    print(output, end="")

    # Save to digests/
    DIGESTS_DIR.mkdir(exist_ok=True)
    digest_filename = DIGESTS_DIR / f"{monday.isoformat()}_weekly.txt"
    try:
        digest_filename.write_text(output)
        print(f"\n[saved] {digest_filename}")
    except Exception as ex:
        print(f"\n[warning] Could not save digest: {ex}")


if __name__ == "__main__":
    main()
