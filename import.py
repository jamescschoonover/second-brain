#!/usr/bin/env python3
"""
import.py — Second Brain entry importer for James Schoonover.

Usage:
  python3 import.py --source mindsera /path/to/export/folder/
  python3 import.py --source mindsera /path/to/export.zip
  python3 import.py --source recall /path/to/export/folder/
  python3 import.py --source manual --title "Quick note" --text "..."
  python3 import.py --preview                    # show what would be imported, no write
  python3 import.py --stats                      # show index stats
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR    = Path(__file__).parent
INDEX_FILE  = BASE_DIR / "index.json"
ENTRIES_DIR = BASE_DIR / "entries"
RAW_DIR     = BASE_DIR / "raw"
ENV_FILE    = Path("/home/kallan/todoist-audit/.env")

HAIKU_MODEL = "claude-haiku-4-5-20251001"

# Haiku pricing (per 1K tokens, approximate)
HAIKU_INPUT_COST_PER_1K  = 0.0008
HAIKU_OUTPUT_COST_PER_1K = 0.0008


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------

def load_index() -> dict:
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text())
        except Exception:
            pass
    return {"last_updated": "", "entry_count": 0, "entries": []}


def save_index(index: dict):
    index["last_updated"] = datetime.now(timezone.utc).isoformat()
    index["entry_count"]  = len(index["entries"])
    INDEX_FILE.write_text(json.dumps(index, indent=2))


def known_ids(index: dict) -> set:
    return {e["id"] for e in index["entries"]}


# ---------------------------------------------------------------------------
# Slug / ID helpers
# ---------------------------------------------------------------------------
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = _SLUG_RE.sub("-", text)
    return text[:50].strip("-")


def make_id(date_str: str, title: str) -> str:
    return f"{date_str}_{slugify(title)}"


# ---------------------------------------------------------------------------
# Markdown / frontmatter parsing
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body. Returns (meta_dict, body_str)."""
    meta = {}
    if not text.startswith("---"):
        return meta, text
    end = text.find("\n---", 3)
    if end == -1:
        return meta, text
    fm_block = text[3:end].strip()
    body     = text[end + 4:].strip()
    for line in fm_block.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        # simple list: [a, b] or "- item" not handled deeply — good enough
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1]
            meta[k] = [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
        else:
            meta[k] = v.strip('"').strip("'")
    return meta, body


def parse_date_from_filename(name: str) -> str | None:
    """Try to extract YYYY-MM-DD from a filename."""
    m = re.search(r"(\d{4}[-_]\d{2}[-_]\d{2})", name)
    if m:
        return m.group(1).replace("_", "-")
    return None


# ---------------------------------------------------------------------------
# Mindsera parser
# ---------------------------------------------------------------------------

def parse_mindsera(md_path: Path) -> dict | None:
    """Parse a Mindsera export .md file into a raw entry dict."""
    text = md_path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_frontmatter(text)

    # Date
    date_str = meta.get("date", "") or parse_date_from_filename(md_path.name) or ""
    if not date_str:
        print(f"  [warn] No date found in {md_path.name} — skipping")
        return None

    # Normalize date
    date_str = str(date_str).strip()[:10]

    # Title — from H1 or filename
    title = ""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            break
    if not title:
        title = md_path.stem.replace("_", " ").replace("-", " ").title()

    # Split at AI Response / Reflection heading
    ai_reflection = ""
    split_patterns = [
        r"^#{1,3}\s+(AI Response|Reflection|AI Reflection)",
    ]
    james_content = body
    for pat in split_patterns:
        parts = re.split(pat, body, maxsplit=1, flags=re.MULTILINE | re.IGNORECASE)
        if len(parts) >= 2:
            james_content = parts[0].strip()
            ai_reflection = "".join(parts[1:]).strip()
            # The heading itself got consumed — trim leading colon or whitespace from remainder
            # parts[1] is the captured group (heading text), parts[2] is the rest
            if len(parts) == 3:
                ai_reflection = parts[2].strip()
            break

    mood = meta.get("mood", "")
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    prompt = meta.get("prompt", "")

    return {
        "date": date_str,
        "title": title,
        "source": "mindsera",
        "james_content": james_content,
        "ai_reflection": ai_reflection,
        "mood": mood,
        "tags": tags,
        "prompt": prompt,
        "source_file": md_path.name,
        "source_url": None,
    }


# ---------------------------------------------------------------------------
# Recall parser
# ---------------------------------------------------------------------------

def parse_recall(md_path: Path) -> dict | None:
    """Parse a Recall export .md file into a raw entry dict."""
    text = md_path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_frontmatter(text)

    date_str = meta.get("date", "") or parse_date_from_filename(md_path.name) or ""
    if not date_str:
        print(f"  [warn] No date found in {md_path.name} — skipping")
        return None
    date_str = str(date_str).strip()[:10]

    title = meta.get("title", "") or ""
    if not title:
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("# "):
                title = line[2:].strip()
                break
    if not title:
        title = md_path.stem.replace("_", " ").replace("-", " ").title()

    source_url = meta.get("url", None)

    return {
        "date": date_str,
        "title": title,
        "source": "recall",
        "james_content": body.strip(),
        "ai_reflection": "",
        "mood": "",
        "tags": meta.get("tags", []),
        "prompt": "",
        "source_file": md_path.name,
        "source_url": source_url,
    }


# ---------------------------------------------------------------------------
# Manual entry
# ---------------------------------------------------------------------------

def parse_manual(title: str, text: str) -> dict:
    date_str = datetime.now().strftime("%Y-%m-%d")
    return {
        "date": date_str,
        "title": title,
        "source": "manual",
        "james_content": text.strip(),
        "ai_reflection": "",
        "mood": "",
        "tags": [],
        "prompt": "",
        "source_file": None,
        "source_url": None,
    }


# ---------------------------------------------------------------------------
# Podcast / video parser (yt-dlp + Whisper)
# ---------------------------------------------------------------------------

def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _make_timestamp_url(source_url: str, seconds: int) -> str:
    """Return a deep-link to the source media at a specific second offset."""
    if "youtube.com" in source_url or "youtu.be" in source_url:
        sep = "&" if "?" in source_url else "?"
        return f"{source_url}{sep}t={seconds}"
    # Generic fallback — most podcast players ignore the fragment
    return f"{source_url}#t={seconds}"


def parse_podcast(url: str) -> dict | None:
    """Download audio from URL via yt-dlp and transcribe with Whisper.
    Returns a raw entry dict with a 'segments' field containing timestamped chunks."""

    # Fetch metadata first (title, upload_date) without downloading audio
    print(f"  Fetching metadata: {url}")
    meta_result = subprocess.run(
        ["yt-dlp", "--dump-json", "--no-playlist", url],
        capture_output=True, text=True, timeout=60
    )
    if meta_result.returncode != 0:
        print(f"  [error] yt-dlp metadata failed: {meta_result.stderr[:200]}")
        return None

    try:
        meta = json.loads(meta_result.stdout)
    except json.JSONDecodeError:
        print("  [error] Could not parse yt-dlp metadata JSON")
        return None

    raw_title    = meta.get("title", "Untitled")
    upload_date  = meta.get("upload_date", "")  # YYYYMMDD
    uploader     = meta.get("uploader", "")
    duration_s   = meta.get("duration", 0)
    webpage_url  = meta.get("webpage_url", url)

    # Normalize date
    date_str = ""
    if upload_date and len(upload_date) == 8:
        date_str = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    title = f"{raw_title}"
    if uploader:
        title = f"{raw_title} — {uploader}"

    print(f"  Title   : {raw_title}")
    print(f"  Date    : {date_str}")
    print(f"  Duration: {_fmt_time(duration_s)}")

    # Download audio to temp dir
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_template = os.path.join(tmpdir, "audio.%(ext)s")
        print("  Downloading audio (this may take a moment)...")
        dl_result = subprocess.run(
            [
                "yt-dlp", "-x",
                "--audio-format", "mp3",
                "--audio-quality", "5",  # 128kbps VBR — good enough for speech
                "--no-playlist",
                "--cookies-from-browser", "chrome",
                "-o", audio_template,
                url,
            ],
            capture_output=True, text=True, timeout=600
        )
        if dl_result.returncode != 0:
            # Retry without cookies (public content)
            dl_result = subprocess.run(
                ["yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "5",
                 "--no-playlist", "-o", audio_template, url],
                capture_output=True, text=True, timeout=600
            )
        if dl_result.returncode != 0:
            print(f"  [error] yt-dlp download failed: {dl_result.stderr[:300]}")
            return None

        # Find the downloaded file
        audio_files = list(Path(tmpdir).glob("*.mp3")) + list(Path(tmpdir).glob("*.m4a")) + \
                      list(Path(tmpdir).glob("*.webm")) + list(Path(tmpdir).glob("*.ogg"))
        if not audio_files:
            print("  [error] No audio file found after download")
            return None
        audio_path = str(audio_files[0])
        size_mb = os.path.getsize(audio_path) / 1024 / 1024
        print(f"  Audio   : {Path(audio_path).name} ({size_mb:.1f} MB)")

        # Transcribe with Whisper
        print("  Transcribing with Whisper (base model)...")
        try:
            import whisper as _whisper
            model = _whisper.load_model("base")
            result = model.transcribe(audio_path, verbose=False, fp16=False)
        except Exception as e:
            print(f"  [error] Whisper transcription failed: {e}")
            return None

        raw_segments = result.get("segments", [])
        full_text    = result.get("text", "").strip()

        if not full_text:
            print("  [warn] Whisper returned empty transcript")
            return None

        print(f"  Transcript: {len(full_text.split())} words, {len(raw_segments)} segments")

        # Build timestamped segments
        segments = []
        for seg in raw_segments:
            start_s = int(seg.get("start", 0))
            segments.append({
                "time":    _fmt_time(start_s),
                "seconds": start_s,
                "text":    seg.get("text", "").strip(),
                "url":     _make_timestamp_url(webpage_url, start_s),
            })

    return {
        "date":          date_str,
        "title":         title,
        "source":        "podcast",
        "james_content": full_text,
        "ai_reflection": "",
        "mood":          "",
        "tags":          [],
        "prompt":        "",
        "source_file":   None,
        "source_url":    webpage_url,
        "segments":      segments,
        "media_type":    "podcast",
        "duration_s":    duration_s,
    }


# ---------------------------------------------------------------------------
# Haiku analysis
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT = """\
Analyze this content captured by James Schoonover for his personal Second Brain.
James is a delivery driver, reseller, husband, and man of faith working toward financial freedom.

Source type: {source}
Date: {date}
Title: {title}
Creator/Author: {creator}

Content:
{content}

Respond in this exact JSON format (no markdown fences, raw JSON only):
{{
  "summary": "2-3 sentence summary of the content and why it matters to James specifically",
  "content_type": "exactly one of: personal-reflection | information-capture | teaching | motivation | storytelling | prayer | planning | conversation",
  "key_ideas": ["up to 6 key ideas or insights — be specific, not generic"],
  "action_items": ["concrete actions James could take based on this content — empty array if none"],
  "tags": ["8-12 specific searchable tags — think about every angle James might search: topic, feeling, situation, people, concepts"],
  "topics": ["2-5 from: faith/spirituality | family | finances | debt | business | reselling | health | identity | relationships | growth | mindset | productivity | entrepreneurship | marriage"],
  "people": ["full names of people mentioned or who created this content — empty array if none"],
  "emotion": "one word: reflective/hopeful/grateful/anxious/determined/conflicted/peaceful/struggling/motivated/convicted/joyful",
  "scripture_refs": ["any Bible verses or passages referenced, empty array if none"],
  "applicability": "one sentence: how James can apply this to his life right now given his situation"
}}"""

_DEFAULT_ANALYSIS = {
    "summary": "",
    "content_type": "information-capture",
    "key_ideas": [],
    "action_items": [],
    "tags": [],
    "topics": [],
    "people": [],
    "emotion": "reflective",
    "scripture_refs": [],
    "applicability": "",
}


def analyze_with_haiku(raw: dict, client: anthropic.Anthropic) -> tuple[dict, int, int]:
    """Call Claude Haiku and return (analysis_dict, input_tokens, output_tokens)."""
    # Podcasts are transcripts — use more content for better analysis
    max_chars = 8000 if raw.get("source") == "podcast" else 3000
    content_snippet = raw["james_content"][:max_chars]

    # Derive creator from source_url or title for podcasts
    creator = "James Schoonover"
    if raw.get("source") == "podcast":
        title = raw.get("title", "")
        if " — " in title:
            creator = title.split(" — ", 1)[-1]
        else:
            creator = "Unknown creator"
    elif raw.get("source") == "recall":
        creator = raw.get("source_url", "External source")

    prompt_text = ANALYSIS_PROMPT.format(
        source=raw["source"],
        date=raw["date"],
        title=raw["title"],
        creator=creator,
        content=content_snippet,
    )

    def _call() -> tuple[str, int, int]:
        msg = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt_text}],
        )
        text = msg.content[0].text.strip()
        in_tok  = msg.usage.input_tokens
        out_tok = msg.usage.output_tokens
        return text, in_tok, out_tok

    # Try once, retry once on JSON error
    for attempt in range(2):
        try:
            text, in_tok, out_tok = _call()
            # Strip possible markdown fences
            text = re.sub(r"^```json\s*", "", text, flags=re.MULTILINE)
            text = re.sub(r"^```\s*$", "", text, flags=re.MULTILINE)
            analysis = json.loads(text)
            return analysis, in_tok, out_tok
        except json.JSONDecodeError:
            if attempt == 0:
                print("  [warn] JSON parse error from Haiku — retrying once...")
                time.sleep(1)
                continue
            else:
                print("  [warn] JSON parse error on retry — using defaults")
                return _DEFAULT_ANALYSIS.copy(), 0, 0
        except Exception as e:
            print(f"  [warn] Haiku API error: {e} — using defaults")
            return _DEFAULT_ANALYSIS.copy(), 0, 0


# ---------------------------------------------------------------------------
# Entry assembler
# ---------------------------------------------------------------------------

def build_entry(raw: dict, analysis: dict) -> dict:
    title    = raw["title"]
    date_str = raw["date"]
    entry_id = make_id(date_str, title)

    james_wc = len(raw["james_content"].split())

    entry = {
        "id":               entry_id,
        "date":             date_str,
        "source":           raw["source"],
        "title":            title,
        "original_content": raw["james_content"],
        "ai_reflection":    raw.get("ai_reflection", ""),
        "summary":          analysis.get("summary", ""),
        "key_ideas":        analysis.get("key_ideas", []),
        "action_items":     analysis.get("action_items", []),
        "tags":             analysis.get("tags", raw.get("tags", [])),
        "topics":           analysis.get("topics", []),
        "people":           analysis.get("people", []),
        "content_type":     analysis.get("content_type", "information-capture"),
        "emotion":          analysis.get("emotion", raw.get("mood", "reflective")),
        "scripture_refs":   analysis.get("scripture_refs", []),
        "applicability":    analysis.get("applicability", ""),
        "word_count":       james_wc,
        "imported_at":      datetime.now(timezone.utc).isoformat(),
        "source_url":       raw.get("source_url"),
        "source_file":      f"raw/{raw['source_file']}" if raw.get("source_file") else None,
        # Podcast/video specific (None for non-media sources)
        "media_type":       raw.get("media_type"),
        "duration_s":       raw.get("duration_s"),
        "segments":         raw.get("segments"),  # [{time, seconds, text, url}]
    }
    return entry


def index_entry(entry: dict) -> dict:
    """Build the lightweight index record from a full entry."""
    rec = {
        "id":           entry["id"],
        "date":         entry["date"],
        "source":       entry["source"],
        "content_type": entry.get("content_type", ""),
        "title":        entry["title"],
        "summary":      entry["summary"],
        "key_ideas":    entry["key_ideas"],
        "tags":         entry["tags"],
        "topics":       entry["topics"],
        "people":       entry.get("people", []),
        "emotion":      entry["emotion"],
        "applicability": entry.get("applicability", ""),
        "word_count":   entry["word_count"],
        "has_actions":  bool(entry.get("action_items")),
        "has_scripture": bool(entry.get("scripture_refs")),
        "file":         f"entries/{entry['id']}.json",
    }
    if entry.get("source_url"):
        rec["source_url"] = entry["source_url"]
    if entry.get("duration_s"):
        rec["duration"] = _fmt_time(entry["duration_s"])
    return rec


# ---------------------------------------------------------------------------
# Drive sync
# ---------------------------------------------------------------------------

def sync_to_drive():
    """Sync index.json and entries/ to Google Drive 'Second Brain/' folder."""
    try:
        import sys
        sys.path.insert(0, "/home/kallan/vendoo-tool")
        from google_oauth_helper import get_user_creds
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        creds   = get_user_creds()
        service = build("drive", "v3", credentials=creds)

        # Find or create "Second Brain" folder
        q      = "name='Second Brain' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        result = service.files().list(q=q, fields="files(id,name)").execute()
        files  = result.get("files", [])

        if files:
            folder_id = files[0]["id"]
        else:
            folder_meta = {
                "name":     "Second Brain",
                "mimeType": "application/vnd.google-apps.folder",
            }
            folder = service.files().create(body=folder_meta, fields="id").execute()
            folder_id = folder["id"]
            print(f"  [drive] Created 'Second Brain' folder (id={folder_id})")

        def upload_file(local_path: Path, parent_id: str, subfolder_name: str | None = None):
            """Upload a single file; create subfolder if needed."""
            dest_parent = parent_id
            if subfolder_name:
                sq = (
                    f"name='{subfolder_name}' and "
                    f"'{parent_id}' in parents and "
                    "mimeType='application/vnd.google-apps.folder' and trashed=false"
                )
                sr = service.files().list(q=sq, fields="files(id)").execute()
                sf = sr.get("files", [])
                if sf:
                    dest_parent = sf[0]["id"]
                else:
                    sm = {
                        "name":     subfolder_name,
                        "mimeType": "application/vnd.google-apps.folder",
                        "parents":  [parent_id],
                    }
                    sf_created = service.files().create(body=sm, fields="id").execute()
                    dest_parent = sf_created["id"]

            # Check if file exists already
            fq = f"name='{local_path.name}' and '{dest_parent}' in parents and trashed=false"
            fr = service.files().list(q=fq, fields="files(id)").execute()
            ff = fr.get("files", [])

            media = MediaFileUpload(str(local_path), mimetype="application/json")
            if ff:
                service.files().update(fileId=ff[0]["id"], media_body=media).execute()
            else:
                fm = {"name": local_path.name, "parents": [dest_parent]}
                service.files().create(body=fm, media_body=media, fields="id").execute()

        # Sync index.json
        upload_file(INDEX_FILE, folder_id)

        # Sync entries/
        if ENTRIES_DIR.exists():
            for entry_file in sorted(ENTRIES_DIR.glob("*.json")):
                upload_file(entry_file, folder_id, subfolder_name="entries")

        print(f"  [drive] Synced index.json + entries/ to 'Second Brain/' on Drive")

    except Exception as e:
        print(f"  [drive] Sync skipped: {e}")


# ---------------------------------------------------------------------------
# Collect markdown files from folder or zip
# ---------------------------------------------------------------------------

def collect_md_files(source_path: str) -> list[Path]:
    """Return list of .md files; extracts zip to RAW_DIR first if needed."""
    p = Path(source_path)
    if not p.exists():
        print(f"[error] Path not found: {source_path}")
        sys.exit(1)

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    if p.is_file() and p.suffix.lower() == ".zip":
        print(f"Extracting zip: {p.name}...")
        extract_dir = RAW_DIR / p.stem
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(p, "r") as zf:
            zf.extractall(extract_dir)
        return sorted(extract_dir.rglob("*.md"))
    elif p.is_dir():
        return sorted(p.rglob("*.md"))
    else:
        print(f"[error] Expected a folder or .zip file, got: {source_path}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def cmd_stats(index: dict):
    print(f"Second Brain — Index Stats")
    print(f"  Total entries : {index['entry_count']}")
    print(f"  Last updated  : {index['last_updated'] or 'never'}")
    if index["entries"]:
        sources: dict[str, int] = {}
        emotions: dict[str, int] = {}
        topics_all: dict[str, int] = {}
        for e in index["entries"]:
            s = e.get("source", "unknown")
            sources[s] = sources.get(s, 0) + 1
            em = e.get("emotion", "")
            if em:
                emotions[em] = emotions.get(em, 0) + 1
            for t in e.get("topics", []):
                topics_all[t] = topics_all.get(t, 0) + 1
        print(f"  By source     : {dict(sorted(sources.items(), key=lambda x: -x[1]))}")
        top_em = sorted(emotions.items(), key=lambda x: -x[1])[:5]
        print(f"  Top emotions  : {top_em}")
        top_tp = sorted(topics_all.items(), key=lambda x: -x[1])[:8]
        print(f"  Top topics    : {top_tp}")
        dates = [e["date"] for e in index["entries"] if e.get("date")]
        if dates:
            print(f"  Date range    : {min(dates)} → {max(dates)}")


def _extract_video_id(url: str) -> str | None:
    patterns = [
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/watch\?.*v=([A-Za-z0-9_-]{11})",
        r"youtube\.com/shorts/([A-Za-z0-9_-]{11})",
        r"tiktok\.com/.+/video/(\d+)",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _check_library_duplicate(url: str, index: dict) -> dict | None:
    """Check manifest + index for an existing entry matching this URL."""
    vid_id = _extract_video_id(url)

    # Check manifest (Matt Dearden or any future channel manifest)
    manifest_glob = sorted(BASE_DIR.glob("raw/*/manifest.json"))
    for mf in manifest_glob:
        try:
            manifest = json.loads(mf.read_text())
            for v in manifest.get("videos", []):
                if vid_id and v.get("video_id") == vid_id and v.get("status") == "done":
                    return {
                        "title":       v.get("title", ""),
                        "entry_id":    v.get("entry_id", ""),
                        "imported_at": v.get("imported_at", ""),
                        "url":         v.get("url", url),
                    }
        except Exception:
            pass

    # Check index source_url
    if vid_id:
        for e in index.get("entries", []):
            src = e.get("source_url", "") or ""
            if vid_id in src:
                return {
                    "title":       e.get("title", ""),
                    "entry_id":    e.get("id", ""),
                    "imported_at": e.get("imported_at", ""),
                    "url":         src,
                }
    return None


def _print_duplicate_notice(info: dict):
    title       = info.get("title", "Unknown")
    entry_id    = info.get("entry_id", "")
    imported_at = (info.get("imported_at") or "")[:10]
    url         = info.get("url", "")
    print()
    print(f"  ⚑  Already in your library: \"{title}\"")
    if imported_at:
        print(f"     Imported: {imported_at}")
    if url:
        print(f"     Link    : {url}")
    if entry_id:
        print(f"     Search  : python3 ~/second-brain/query.py \"<topic>\"")
        print(f"     Entry   : python3 ~/second-brain/query.py --entry {entry_id}")
    print()


def cmd_import(args, index: dict, preview: bool):
    # Load env + API client
    load_dotenv(ENV_FILE)
    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[error] ANTHROPIC_API_KEY not found in .env")
        sys.exit(1)
    client = anthropic.Anthropic(api_key=api_key)

    source = args.source

    # --- Collect raw entries ---
    raw_entries: list[dict] = []

    if source == "manual":
        if not args.title or not args.text:
            print("[error] --source manual requires --title and --text")
            sys.exit(1)
        raw_entries.append(parse_manual(args.title, args.text))

    elif source == "podcast":
        urls = args.urls if args.urls else ([args.path] if args.path else [])
        if not urls:
            print("[error] --source podcast requires one or more URLs as arguments")
            sys.exit(1)
        for url in urls:
            # Duplicate check against manifest + index before any download
            dup = _check_library_duplicate(url, index)
            if dup:
                _print_duplicate_notice(dup)
                continue
            print(f"\nProcessing podcast: {url}")
            raw = parse_podcast(url)
            if raw:
                raw_entries.append(raw)

    else:
        if not args.path:
            print("[error] Supply a path to the export folder or zip")
            sys.exit(1)
        md_files = collect_md_files(args.path)
        if not md_files:
            print("[warn] No .md files found at that path")
            return

        parser_fn = parse_mindsera if source == "mindsera" else parse_recall

        for md_path in md_files:
            raw = parser_fn(md_path)
            if raw:
                raw_entries.append(raw)

    # --- Deduplicate ---
    existing = known_ids(index)
    to_process: list[dict] = []
    already_count = 0

    for raw in raw_entries:
        eid = make_id(raw["date"], raw["title"])
        if eid in existing:
            already_count += 1
        else:
            to_process.append(raw)

    print(f"Found {len(raw_entries)} entries | Already imported: {already_count} | New: {len(to_process)}")

    if preview:
        for i, raw in enumerate(to_process, 1):
            eid = make_id(raw["date"], raw["title"])
            print(f"  [{i}/{len(to_process)}] {eid}  ({raw['source']})  {raw['date']}")
        if already_count:
            print(f"  (+ {already_count} already imported — would be skipped)")
        return

    if not to_process:
        return

    # --- Process ---
    ENTRIES_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    total_in_tokens  = 0
    total_out_tokens = 0
    imported_count   = 0

    for i, raw in enumerate(to_process, 1):
        eid = make_id(raw["date"], raw["title"])
        print(f"[{i}/{len(to_process)}] Processing: {eid}...")

        # Copy raw file to raw/
        if raw.get("source_file") and source != "manual":
            # raw["source_file"] at this point is just the filename
            # actual source path we need to find
            src_md = None
            if args.path:
                sp = Path(args.path)
                if sp.is_file():
                    # zip case — already extracted
                    pass
                else:
                    candidates = list(sp.rglob(raw["source_file"]))
                    if candidates:
                        src_md = candidates[0]
            if src_md and src_md.exists():
                dest_raw = RAW_DIR / raw["source_file"]
                if not dest_raw.exists():
                    dest_raw.write_bytes(src_md.read_bytes())

        # Haiku analysis
        analysis, in_tok, out_tok = analyze_with_haiku(raw, client)
        total_in_tokens  += in_tok
        total_out_tokens += out_tok

        # Build + save entry
        entry = build_entry(raw, analysis)
        entry_path = ENTRIES_DIR / f"{eid}.json"
        entry_path.write_text(json.dumps(entry, indent=2))

        # Update index
        index["entries"].append(index_entry(entry))
        existing.add(eid)
        imported_count += 1

    # Save index
    save_index(index)

    # Cost estimate
    cost = (total_in_tokens / 1000 * HAIKU_INPUT_COST_PER_1K) + \
           (total_out_tokens / 1000 * HAIKU_OUTPUT_COST_PER_1K)
    print(f"\nProcessed: {imported_count} entries | Haiku cost: ~${cost:.4f}")
    print(f"Total tokens: {total_in_tokens} in / {total_out_tokens} out")

    # Drive sync
    print("Syncing to Google Drive...")
    sync_to_drive()


def main():
    parser = argparse.ArgumentParser(
        description="Import entries into James Schoonover's Second Brain",
        epilog=(
            "Examples:\n"
            "  python3 import.py --source mindsera /path/to/export/\n"
            "  python3 import.py --source recall /path/to/export.zip\n"
            "  python3 import.py --source podcast https://youtu.be/XXXXXXXXXXX\n"
            "  python3 import.py --source podcast URL1 URL2 URL3\n"
            "  python3 import.py --source manual --title 'Note' --text 'Content'\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path",              nargs="?",       help="Path to export folder, .zip, or (for podcast) a URL")
    parser.add_argument("urls",              nargs="*",       help="Additional podcast URLs (when --source podcast)")
    parser.add_argument("--source",          choices=["mindsera", "recall", "manual", "podcast"])
    parser.add_argument("--title",           help="Title for manual entry")
    parser.add_argument("--text",            help="Text for manual entry")
    parser.add_argument("--preview",         action="store_true", help="Show what would be imported, no write")
    parser.add_argument("--stats",           action="store_true", help="Show index stats")
    args = parser.parse_args()

    index = load_index()

    if args.stats:
        cmd_stats(index)
        return

    if args.preview:
        if not args.source:
            print("[error] --preview requires --source")
            sys.exit(1)
        cmd_import(args, index, preview=True)
        return

    if not args.source:
        parser.print_help()
        sys.exit(1)

    cmd_import(args, index, preview=False)


if __name__ == "__main__":
    main()
