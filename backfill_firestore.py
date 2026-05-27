#!/usr/bin/env python3
"""
backfill_firestore.py — Write all local second-brain entries to Firestore.

Usage:
  python3 ~/second-brain/backfill_firestore.py           # write all
  python3 ~/second-brain/backfill_firestore.py --limit 50
  python3 ~/second-brain/backfill_firestore.py --dry-run
"""

import argparse, json, time, sys
from pathlib import Path
from google.oauth2 import service_account
from google.auth.transport.requests import Request
import requests

SA_KEY_PATH  = Path.home() / "todoist-audit/firebase_service_account.json"
ENTRIES_DIR  = Path.home() / "second-brain/entries"
PROJECT_ID   = "forge-game"
SCOPES       = ["https://www.googleapis.com/auth/datastore"]
BATCH_URL    = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents:batchWrite"
DOC_BASE     = f"projects/{PROJECT_ID}/databases/(default)/documents/brain_entries"
BATCH_SIZE   = 200   # conservative; each entry can be ~5KB
MAX_CONTENT  = 6000  # chars — cap original_content to keep doc size sane


def get_token():
    creds = service_account.Credentials.from_service_account_file(str(SA_KEY_PATH), scopes=SCOPES)
    creds.refresh(Request())
    return creds.token


def to_fs(v):
    if v is None:            return {"nullValue": None}
    if isinstance(v, bool):  return {"booleanValue": v}
    if isinstance(v, int):   return {"integerValue": str(v)}
    if isinstance(v, float): return {"doubleValue": v}
    if isinstance(v, list):  return {"arrayValue": {"values": [to_fs(i) for i in v]}}
    if isinstance(v, dict):  return {"mapValue": {"fields": {k: to_fs(val) for k, val in v.items()}}}
    return {"stringValue": str(v)[:5000]}  # cap string length


def write_batch(batch, token):
    writes = []
    for entry in batch:
        eid = entry.get("id") or ""
        if not eid:
            continue
        # Only keep fields the Browse tab actually uses; cap big fields
        doc = {
            "id":               entry.get("id", ""),
            "title":            entry.get("title", "Untitled"),
            "summary":          (entry.get("summary") or "")[:500],
            "date":             entry.get("date", ""),
            "source":           entry.get("source", "manual"),
            "emotion":          entry.get("emotion", ""),
            "tags":             (entry.get("tags") or [])[:15],
            "topics":           (entry.get("topics") or [])[:10],
            "people":           (entry.get("people") or [])[:10],
            "key_ideas":        (entry.get("key_ideas") or [])[:5],
            "has_actions":      bool(entry.get("has_actions")),
            "has_scripture":    bool(entry.get("has_scripture")),
            "word_count":       entry.get("word_count", 0),
            "who":              entry.get("who", "james"),
            "created_at":       entry.get("created_at", entry.get("date", "")),
            "original_content": (entry.get("original_content") or "")[:MAX_CONTENT],
        }
        fields = {k: to_fs(v) for k, v in doc.items()}
        writes.append({
            "update": {
                "name":   f"{DOC_BASE}/{eid}",
                "fields": fields,
            }
        })
    if not writes:
        return True
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(BATCH_URL, headers=headers, json={"writes": writes}, timeout=90)
    if r.status_code not in (200, 201):
        print(f"\n  [error] {r.status_code}: {r.text[:300]}")
        return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = sorted(ENTRIES_DIR.glob("*.json"))
    print(f"Found {len(files)} entry files in {ENTRIES_DIR}")

    entries = []
    for f in files:
        try:
            data = json.loads(f.read_text())
            if not data.get("who"):
                data["who"] = "james"   # all existing entries belong to James
            entries.append(data)
        except Exception as e:
            print(f"  [skip] {f.name}: {e}")

    if args.limit:
        entries = entries[:args.limit]

    total = len(entries)

    if args.dry_run:
        print(f"Dry run — would write {total} entries")
        sample = entries[:3]
        for e in sample:
            print(f"  {e['id'][:60]}  who={e['who']}  source={e.get('source')}")
        return

    print(f"Writing {total} entries in batches of {BATCH_SIZE}...")
    token = get_token()
    written = 0
    errors  = 0
    n_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, total, BATCH_SIZE):
        batch     = entries[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"  [{batch_num:>3}/{n_batches}] {len(batch)} entries...", end=" ", flush=True)

        # Refresh token every 5 batches (~10 min at 2s/batch → well within 1hr)
        if batch_num % 5 == 0:
            token = get_token()

        ok = write_batch(batch, token)
        if ok:
            written += len(batch)
            print("✓")
        else:
            errors += len(batch)
            print("✗")
        time.sleep(2)

    print(f"\nDone: {written} written, {errors} errors")


if __name__ == "__main__":
    main()
