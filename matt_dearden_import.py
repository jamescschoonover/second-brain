#!/usr/bin/env python3
"""
matt_dearden_import.py — Manifest-driven bulk importer for Matt Dearden's YouTube channel.

Usage:
  python3 matt_dearden_import.py --catalog          # fetch full video list, build manifest
  python3 matt_dearden_import.py --run 25           # process next 25 pending videos
  python3 matt_dearden_import.py --status           # show progress
  python3 matt_dearden_import.py --retry            # reprocess failed videos
  python3 matt_dearden_import.py --check URL        # check if a URL is already in library
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import importlib.util as _ilu

def _load_brain():
    """Load import.py by file path (can't use `import import` — keyword conflict)."""
    _spec = _ilu.spec_from_file_location("brain", Path(__file__).parent / "import.py")
    _mod  = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod

_brain = _load_brain()

load_index           = _brain.load_index
save_index           = _brain.save_index
known_ids            = _brain.known_ids
make_id              = _brain.make_id
analyze_with_haiku   = _brain.analyze_with_haiku
build_entry          = _brain.build_entry
index_entry          = _brain.index_entry
_fmt_time            = _brain._fmt_time
_make_timestamp_url  = _brain._make_timestamp_url
sync_to_drive        = _brain.sync_to_drive
ENTRIES_DIR          = _brain.ENTRIES_DIR
HAIKU_INPUT_COST_PER_1K  = _brain.HAIKU_INPUT_COST_PER_1K
HAIKU_OUTPUT_COST_PER_1K = _brain.HAIKU_OUTPUT_COST_PER_1K

import anthropic
from dotenv import load_dotenv

load_dotenv(Path("/home/kallan/todoist-audit/.env"))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR     = Path(__file__).parent
CHANNEL_URL  = "https://www.youtube.com/@MattDearden"
CHANNEL_NAME = "matt-dearden"
MANIFEST_DIR = BASE_DIR / "raw" / CHANNEL_NAME
MANIFEST_FILE = MANIFEST_DIR / "manifest.json"
AUDIO_DIR    = MANIFEST_DIR / "audio"


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def load_manifest() -> dict:
    if MANIFEST_FILE.exists():
        try:
            return json.loads(MANIFEST_FILE.read_text())
        except Exception:
            pass
    return {"channel": CHANNEL_URL, "total": 0, "last_run": "", "videos": []}


def save_manifest(m: dict):
    m["last_run"] = datetime.now(timezone.utc).isoformat()
    MANIFEST_FILE.write_text(json.dumps(m, indent=2))


def video_by_id(manifest: dict, video_id: str) -> dict | None:
    for v in manifest["videos"]:
        if v["video_id"] == video_id:
            return v
    return None


def extract_video_id(url: str) -> str | None:
    """Pull YouTube video ID from various URL forms."""
    import re
    patterns = [
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/watch\?.*v=([A-Za-z0-9_-]{11})",
        r"youtube\.com/shorts/([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Duplicate check (used by both this script and import.py)
# ---------------------------------------------------------------------------

def check_duplicate(url: str, manifest: dict, index: dict) -> dict | None:
    """Return existing entry info if this URL is already in the library, else None."""
    vid_id = extract_video_id(url)

    # Check manifest first
    if vid_id:
        v = video_by_id(manifest, vid_id)
        if v and v.get("status") == "done" and v.get("entry_id"):
            return {
                "source": "manifest",
                "video_id": vid_id,
                "title": v.get("title", ""),
                "entry_id": v.get("entry_id", ""),
                "imported_at": v.get("imported_at", ""),
                "url": url,
            }

    # Check index by entry_id pattern (date + slug) — requires fetching metadata
    # We can't easily check without the title, so check manifest is primary
    # Secondary: check if any index entry has source_url matching
    for e in index.get("entries", []):
        src = e.get("source_url", "") or ""
        if vid_id and vid_id in src:
            return {
                "source": "index",
                "video_id": vid_id,
                "title": e.get("title", ""),
                "entry_id": e.get("id", ""),
                "imported_at": e.get("imported_at", ""),
                "url": src,
            }

    return None


def print_duplicate_notice(info: dict):
    title       = info.get("title", "Unknown title")
    entry_id    = info.get("entry_id", "")
    imported_at = info.get("imported_at", "")
    url         = info.get("url", "")

    date_str = ""
    if imported_at:
        date_str = imported_at[:10]

    print()
    print(f"  ⚑  Already in your library: \"{title}\"")
    if date_str:
        print(f"     Matt Dearden · imported {date_str}")
    if url:
        print(f"     Jump link : {url}")
    if entry_id:
        print(f"     To search : python3 ~/second-brain/query.py \"<your topic>\"")
        print(f"     Full entry: python3 ~/second-brain/query.py --entry {entry_id}")
    print()


# ---------------------------------------------------------------------------
# Phase 1 — Catalog
# ---------------------------------------------------------------------------

def cmd_catalog(manifest: dict):
    print(f"Fetching video list from {CHANNEL_URL}...")
    print("(This pulls metadata only — no audio download)\n")

    result = subprocess.run(
        [
            "yt-dlp",
            "--flat-playlist",
            "--dump-single-json",
            "--no-warnings",
            CHANNEL_URL,
        ],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        print(f"[error] yt-dlp failed: {result.stderr[:300]}")
        sys.exit(1)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("[error] Could not parse yt-dlp output")
        sys.exit(1)

    entries = data.get("entries", [])
    if not entries:
        print("[warn] No videos found")
        return

    # Build existing video_id set from manifest
    existing_ids = {v["video_id"] for v in manifest.get("videos", [])}

    added = 0
    for entry in entries:
        vid_id = entry.get("id", "")
        if not vid_id or vid_id in existing_ids:
            continue

        title        = entry.get("title", "Untitled")
        upload_date  = entry.get("upload_date", "")
        duration_s   = entry.get("duration") or 0
        webpage_url  = entry.get("url") or entry.get("webpage_url") or f"https://youtu.be/{vid_id}"

        date_str = ""
        if upload_date and len(upload_date) == 8:
            date_str = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

        manifest["videos"].append({
            "video_id":    vid_id,
            "title":       title,
            "upload_date": date_str,
            "duration_s":  duration_s,
            "url":         webpage_url,
            "status":      "pending",
            "entry_id":    None,
            "imported_at": None,
            "error":       None,
        })
        existing_ids.add(vid_id)
        added += 1

    manifest["total"] = len(manifest["videos"])
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    save_manifest(manifest)

    total     = len(manifest["videos"])
    pending   = sum(1 for v in manifest["videos"] if v["status"] == "pending")
    done      = sum(1 for v in manifest["videos"] if v["status"] == "done")

    print(f"Catalog complete.")
    print(f"  Total videos : {total}")
    print(f"  New added    : {added}")
    print(f"  Already done : {done}")
    print(f"  Pending      : {pending}")
    print(f"\nManifest saved to: {MANIFEST_FILE}")
    print(f"\nNext step: python3 matt_dearden_import.py --run 25")


# ---------------------------------------------------------------------------
# Phase 2 — Run batch
# ---------------------------------------------------------------------------

def transcribe_audio(audio_path: str) -> tuple[str, list]:
    """Transcribe with Whisper. Returns (full_text, segments)."""
    import whisper as _whisper
    model = _whisper.load_model("base")
    result = model.transcribe(audio_path, verbose=False, fp16=False)
    segments = []
    for seg in result.get("segments", []):
        start_s = int(seg.get("start", 0))
        segments.append({
            "time":    _fmt_time(start_s),
            "seconds": start_s,
            "text":    seg.get("text", "").strip(),
            "url":     "",  # filled after we know the video URL
        })
    return result.get("text", "").strip(), segments


def process_video(v: dict, client: anthropic.Anthropic) -> tuple[bool, str]:
    """Download, transcribe, analyze, and save one video.
    Returns (success, error_message)."""
    import tempfile

    vid_id  = v["video_id"]
    url     = v["url"]
    title   = v.get("title", "Untitled")
    date_str = v.get("upload_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    print(f"  [{vid_id}] {title[:60]}")

    # Download audio
    v["status"] = "downloading"
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_template = os.path.join(tmpdir, "audio.%(ext)s")
        dl = subprocess.run(
            [
                "yt-dlp", "-x",
                "--audio-format", "mp3",
                "--audio-quality", "5",
                "--no-playlist",
                "--cookies-from-browser", "chrome",
                "-o", audio_template,
                url,
            ],
            capture_output=True, text=True, timeout=600
        )
        if dl.returncode != 0:
            # Retry without cookies
            dl = subprocess.run(
                ["yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "5",
                 "--no-playlist", "-o", audio_template, url],
                capture_output=True, text=True, timeout=600
            )
        if dl.returncode != 0:
            return False, f"yt-dlp failed: {dl.stderr[:200]}"

        audio_files = list(Path(tmpdir).glob("*.mp3")) + list(Path(tmpdir).glob("*.m4a"))
        if not audio_files:
            return False, "No audio file after download"

        audio_path = str(audio_files[0])
        size_mb = os.path.getsize(audio_path) / 1024 / 1024
        print(f"    Audio: {size_mb:.1f} MB — transcribing...")

        v["status"] = "transcribing"
        try:
            full_text, segments = transcribe_audio(audio_path)
        except Exception as e:
            return False, f"Whisper error: {e}"

    if not full_text:
        return False, "Empty transcript"

    # Fill in jump URLs now that we have the URL
    for seg in segments:
        seg["url"] = _make_timestamp_url(url, seg["seconds"])

    word_count = len(full_text.split())
    print(f"    Transcript: {word_count} words — analyzing...")

    # Build raw dict and analyze
    raw = {
        "date":          date_str,
        "title":         f"{title} — Matt Dearden",
        "source":        "podcast",
        "james_content": full_text,
        "ai_reflection": "",
        "mood":          "",
        "tags":          [],
        "prompt":        "",
        "source_file":   None,
        "source_url":    url,
        "segments":      segments,
        "media_type":    "podcast",
        "duration_s":    v.get("duration_s", 0),
    }

    analysis, in_tok, out_tok = analyze_with_haiku(raw, client)
    entry    = build_entry(raw, analysis)
    entry_id = entry["id"]

    # Save entry file
    ENTRIES_DIR.mkdir(parents=True, exist_ok=True)
    entry_path = ENTRIES_DIR / f"{entry_id}.json"
    entry_path.write_text(json.dumps(entry, indent=2))

    cost = ((in_tok + out_tok) / 1000) * HAIKU_INPUT_COST_PER_1K
    print(f"    Done → {entry_id}  (~${cost:.4f})")

    return True, entry_id


def cmd_run(n: int, manifest: dict):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[error] ANTHROPIC_API_KEY not set")
        sys.exit(1)
    client = anthropic.Anthropic(api_key=api_key)

    pending = [v for v in manifest["videos"] if v["status"] == "pending"]
    if not pending:
        print("Nothing pending. Run --catalog first or --retry for failures.")
        return

    batch = pending[:n]
    print(f"Processing {len(batch)} videos ({len(pending) - len(batch)} more pending after this)\n")

    index = load_index()
    existing = known_ids(index)

    total_cost  = 0.0
    done_count  = 0
    fail_count  = 0

    for i, v in enumerate(batch, 1):
        print(f"[{i}/{len(batch)}] Processing...")

        # Dedup check against index
        vid_id = v["video_id"]
        candidate_id = make_id(
            v.get("upload_date", ""),
            f"{v.get('title', '')} — Matt Dearden"
        )
        if candidate_id in existing:
            print(f"  ⚑ Already in library — skipping: {v['title'][:60]}")
            v["status"] = "done"
            v["entry_id"] = candidate_id
            done_count += 1
            continue

        success, result = process_video(v, client)

        if success:
            entry_id = result
            v["status"]      = "done"
            v["entry_id"]    = entry_id
            v["imported_at"] = datetime.now(timezone.utc).isoformat()
            v["error"]       = None

            # Load entry and add to index
            entry_path = ENTRIES_DIR / f"{entry_id}.json"
            if entry_path.exists():
                entry = json.loads(entry_path.read_text())
                index["entries"].append(index_entry(entry))
                existing.add(entry_id)

            done_count += 1
        else:
            v["status"] = "failed"
            v["error"]  = result
            print(f"  ✗ Failed: {result}")
            fail_count += 1

        save_manifest(manifest)

        # Brief pause between videos
        if i < len(batch):
            time.sleep(3)

    save_index(index)
    save_manifest(manifest)

    total    = len(manifest["videos"])
    done_all = sum(1 for v in manifest["videos"] if v["status"] == "done")
    failed   = sum(1 for v in manifest["videos"] if v["status"] == "failed")
    still_pending = sum(1 for v in manifest["videos"] if v["status"] == "pending")

    print(f"\n{'='*50}")
    print(f"Batch complete: {done_count} done, {fail_count} failed")
    print(f"Overall: {done_all}/{total} done | {still_pending} pending | {failed} failed")
    if still_pending:
        print(f"Continue: python3 matt_dearden_import.py --run {n}")

    print("\nSyncing to Drive...")
    sync_to_drive()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def cmd_status(manifest: dict):
    videos = manifest.get("videos", [])
    if not videos:
        print("No manifest yet. Run --catalog first.")
        return

    total   = len(videos)
    done    = sum(1 for v in videos if v["status"] == "done")
    pending = sum(1 for v in videos if v["status"] == "pending")
    failed  = sum(1 for v in videos if v["status"] == "failed")
    other   = total - done - pending - failed

    pct = (done / total * 100) if total else 0
    bar_filled = int(pct / 2)
    bar = "█" * bar_filled + "░" * (50 - bar_filled)

    print(f"\nMatt Dearden Import — Progress")
    print(f"  [{bar}] {pct:.1f}%")
    print(f"  Done    : {done:>4} / {total}")
    print(f"  Pending : {pending:>4}")
    print(f"  Failed  : {failed:>4}")
    if other:
        print(f"  Other   : {other:>4}")
    if manifest.get("last_run"):
        print(f"  Last run: {manifest['last_run'][:19]}")

    if failed:
        print(f"\nFailed videos:")
        for v in videos:
            if v["status"] == "failed":
                print(f"  [{v['video_id']}] {v['title'][:60]}")
                if v.get("error"):
                    print(f"    Error: {v['error'][:100]}")

    if pending and not failed:
        print(f"\nNext: python3 matt_dearden_import.py --run 25")
    elif failed:
        print(f"\nRetry failures: python3 matt_dearden_import.py --retry")


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------

def cmd_retry(manifest: dict):
    failed = [v for v in manifest["videos"] if v["status"] == "failed"]
    if not failed:
        print("No failed videos to retry.")
        return
    print(f"Resetting {len(failed)} failed videos to pending...")
    for v in failed:
        v["status"] = "pending"
        v["error"]  = None
    save_manifest(manifest)
    print("Done. Run --run N to process them.")


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

def cmd_check(url: str, manifest: dict):
    from import_core import load_index as _load_index
    index = _load_index()
    info = check_duplicate(url, manifest, index)
    if info:
        print_duplicate_notice(info)
    else:
        vid_id = extract_video_id(url)
        print(f"\n  ✓  Not in library yet: {url}")
        if vid_id:
            print(f"     To import: python3 import.py --source podcast {url}")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Matt Dearden YouTube channel — manifest-driven bulk importer"
    )
    parser.add_argument("--catalog",  action="store_true", help="Fetch full video list and build manifest")
    parser.add_argument("--run",      type=int, metavar="N", help="Process next N pending videos")
    parser.add_argument("--status",   action="store_true", help="Show import progress")
    parser.add_argument("--retry",    action="store_true", help="Reset failed videos to pending")
    parser.add_argument("--check",    metavar="URL", help="Check if a URL is already in library")
    args = parser.parse_args()

    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()

    if args.catalog:
        cmd_catalog(manifest)
    elif args.run:
        cmd_run(args.run, manifest)
    elif args.status:
        cmd_status(manifest)
    elif args.retry:
        cmd_retry(manifest)
    elif args.check:
        cmd_check(args.check, manifest)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
