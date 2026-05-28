#!/usr/bin/env python3
"""
clip.py — Universal capture: YouTube videos, web articles → second brain.

Usage:
  python3 clip.py "https://youtu.be/..."
  python3 clip.py "https://example.com/article"
  python3 clip.py "https://..." --who kallan
  python3 clip.py "https://..." --tags health,fitness
  python3 clip.py "https://..." --note "Why I saved this"
"""

import re
import sys
import json
import argparse
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).parent

YOUTUBE_PATTERNS = [
    r"youtu\.be/",
    r"youtube\.com/watch",
    r"youtube\.com/shorts",
    r"youtube\.com/embed",
]
SOCIAL_PATTERNS = [
    r"instagram\.com",
    r"tiktok\.com",
    r"twitter\.com",
    r"x\.com",
    r"facebook\.com",
    r"threads\.net",
]

YTDLP_BIN = Path.home() / ".local/bin/yt-dlp"  # updated binary


def is_youtube(url: str) -> bool:
    return any(re.search(p, url) for p in YOUTUBE_PATTERNS)


def is_social(url: str) -> bool:
    return any(re.search(p, url) for p in SOCIAL_PATTERNS)


def get_youtube_content(url: str) -> tuple[str, str, list[str]]:
    """Returns (title, transcript_text, auto_tags)."""
    import yt_dlp

    # Get metadata + captions
    ydl_opts = {
        "quiet": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        "skip_download": True,
        "outtmpl": str(BASE_DIR / "raw" / "%(id)s.%(ext)s"),
        "extractor_args": {"youtube": {"player_skip": ["webpage", "configs"]}},
        "yt_dlp_filename": str(YTDLP_BIN),
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    title = info.get("title", "Untitled Video")
    video_id = info.get("id", "")
    channel = info.get("channel", info.get("uploader", ""))
    duration = info.get("duration", 0)
    tags = info.get("tags", [])[:5]

    # Find downloaded VTT
    vtt_file = None
    for f in (BASE_DIR / "raw").glob(f"{video_id}*.vtt"):
        vtt_file = f
        break

    transcript = ""
    if vtt_file and vtt_file.exists():
        transcript = _parse_vtt(vtt_file.read_text())
        vtt_file.unlink(missing_ok=True)

    # Clean up any other temp files
    for f in (BASE_DIR / "raw").glob(f"{video_id}*"):
        if f.suffix in (".json", ".vtt", ".mp3", ".webm", ".m4a"):
            f.unlink(missing_ok=True)

    duration_str = f"{duration // 60}m" if duration else ""
    meta = f"YouTube · {channel}" + (f" · {duration_str}" if duration_str else "")
    full_text = f"[{meta}]\n\n{transcript}" if transcript else f"[{meta}]\n\n(No captions available)"

    auto_tags = ["youtube", "video"]
    if tags:
        auto_tags += [t.lower().replace(" ", "-") for t in tags[:3]]

    return title, full_text, auto_tags


def _parse_vtt(vtt_text: str) -> str:
    """Strip VTT timestamps and deduplicate lines."""
    lines = vtt_text.split("\n")
    clean = []
    seen = set()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if re.match(r"^\d{2}:\d{2}", line) or re.match(r"^[\d]+$", line):
            continue
        # Strip HTML tags
        line = re.sub(r"<[^>]+>", "", line)
        if line and line not in seen:
            seen.add(line)
            clean.append(line)
    return " ".join(clean)


def get_web_content(url: str) -> tuple[str, str, list[str]]:
    """Returns (title, text, auto_tags) for a web article."""
    import requests
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")

    # Title
    title = ""
    if soup.title:
        title = soup.title.string or ""
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else urlparse(url).netloc

    # Remove nav, footer, scripts, ads
    for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                      "form", "button", "iframe", "noscript"]):
        tag.decompose()

    # Try article tag first, fall back to main, then body
    content = soup.find("article") or soup.find("main") or soup.find("body")
    if not content:
        content = soup

    # Extract paragraphs
    paragraphs = []
    for p in content.find_all(["p", "h1", "h2", "h3", "h4", "li"]):
        text = p.get_text(strip=True)
        if len(text) > 40:
            paragraphs.append(text)

    full_text = "\n\n".join(paragraphs[:80])  # cap at ~80 paragraphs

    domain = urlparse(url).netloc.replace("www.", "")
    auto_tags = ["article", "web", domain.split(".")[0]]

    return title.strip()[:120], full_text, auto_tags


COBALT_URL = "http://localhost:9000/"
COBALT_KEY = "55e3e996-0de8-42e1-ab7e-00df99c22a12"


def get_social_content(url: str) -> tuple[str, str, list[str]]:
    """Instagram, TikTok, Twitter/X — cobalt download + Whisper transcription."""
    import requests
    import whisper

    platform = "social"
    for pat, name in [(r"instagram", "instagram"), (r"tiktok", "tiktok"),
                      (r"twitter|x\.com", "twitter"), (r"threads", "threads"),
                      (r"reddit\.com", "reddit")]:
        if re.search(pat, url):
            platform = name
            break

    # Ask cobalt for audio
    resp = requests.post(
        COBALT_URL,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Api-Key {COBALT_KEY}",
        },
        json={"url": url, "downloadMode": "audio", "audioFormat": "mp3"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    status = data.get("status")
    if status == "error":
        raise RuntimeError(f"Cobalt error: {data.get('error', {}).get('code', 'unknown')}")

    # picker = multi-image post; grab first video item if present, else fail gracefully
    if status == "picker":
        items = data.get("picker", [])
        video_items = [i for i in items if i.get("type") == "video"]
        if not video_items:
            return url[:120], f"[{platform.title()}]\n\n(Image post — no audio to transcribe)\nURL: {url}", [platform, "image", "social"]
        audio_url = video_items[0]["url"]
        filename = "social_audio.mp3"
    else:
        audio_url = data.get("url", "")
        filename = data.get("filename", "social_audio.mp3")

    # Extract title from filename (cobalt names files as "Title - Uploader.mp3")
    title = filename.rsplit(".", 1)[0]  # strip extension
    if " - " in title:
        parts = title.rsplit(" - ", 1)
        title, uploader = parts[0].strip(), parts[1].strip()
    else:
        uploader = ""

    # Download audio to temp file
    audio_resp = requests.get(audio_url, timeout=120, stream=True)
    audio_resp.raise_for_status()
    tmp_audio = Path(tempfile.mktemp(suffix=".mp3"))
    with open(tmp_audio, "wb") as f:
        for chunk in audio_resp.iter_content(chunk_size=8192):
            f.write(chunk)

    # Transcribe with Whisper turbo
    print(f"  🎙️  Transcribing with Whisper ({tmp_audio.stat().st_size // 1024}KB)...")
    model = whisper.load_model("turbo")
    result = model.transcribe(str(tmp_audio), fp16=False)
    transcript = result.get("text", "").strip()
    tmp_audio.unlink(missing_ok=True)

    meta = f"{platform.title()} · {uploader}" if uploader else platform.title()
    full_text = f"[{meta}]\n\n{transcript}" if transcript else f"[{meta}]\n\n(No speech detected)"
    auto_tags = [platform, "video", "social"]
    return title[:120], full_text, auto_tags


def clip(url: str, who: str = "james", extra_tags: list = None, note: str = "") -> str:
    """Main entry point. Returns entry_id."""
    print(f"🔗 Clipping: {url}")

    if is_youtube(url):
        print("  📺 YouTube detected — fetching captions...")
        try:
            title, content, auto_tags = get_youtube_content(url)
        except Exception as e:
            print(f"  ⚠️  Caption fetch failed: {e}")
            title = url
            content = f"[YouTube video — captions unavailable]\nURL: {url}"
            auto_tags = ["youtube", "video"]
    elif is_social(url):
        platform = next((n for p, n in [(r"instagram", "Instagram"), (r"tiktok", "TikTok"),
                         (r"twitter|x\.com", "Twitter/X"), (r"threads", "Threads")]
                         if re.search(p, url)), "Social")
        print(f"  📱 {platform} detected — fetching with Edge cookies...")
        try:
            title, content, auto_tags = get_social_content(url)
        except Exception as e:
            print(f"  ⚠️  {platform} fetch failed: {e}")
            title = url
            content = f"[{platform} — extraction failed]\nURL: {url}"
            auto_tags = ["social", platform.lower()]
    else:
        print("  🌐 Web article detected — extracting text...")
        try:
            title, content, auto_tags = get_web_content(url)
        except Exception as e:
            print(f"  ⚠️  Web fetch failed: {e}")
            title = url
            content = f"[Web page — extraction failed]\nURL: {url}"
            auto_tags = ["web", "article"]

    # Merge tags
    all_tags = list(dict.fromkeys(auto_tags + (extra_tags or [])))

    # Build full content — title first so capture.py auto-titles from it
    parts = [title]
    if note:
        parts.append(f"[Note: {note}]")
    parts.append(content)
    parts.append(f"Source: {url}")
    full_content = "\n\n".join(parts)

    print(f"  📝 Captured: {title[:60]}")
    print(f"  🏷️  Tags: {', '.join(all_tags)}")
    print("  🤖 Analyzing with Haiku...")

    # Call capture.py to analyze + store
    result = subprocess.run(
        [
            sys.executable,
            str(BASE_DIR / "capture.py"),
            full_content[:8000],
            "--tags", ",".join(all_tags),
        ],
        capture_output=True, text=True, timeout=60
    )

    if result.returncode != 0:
        print(f"  ❌ capture.py error: {result.stderr[:200]}")
        return ""

    print(result.stdout.strip())
    # Extract entry ID from output
    for line in result.stdout.splitlines():
        if "Entry:" in line:
            return line.split("Entry:")[-1].strip()
    return ""


def main():
    parser = argparse.ArgumentParser(description="Clip YouTube videos or web articles to the second brain")
    parser.add_argument("url", help="YouTube or web URL to capture")
    parser.add_argument("--who", default="james", choices=["james", "kallan"])
    parser.add_argument("--tags", default="", help="Comma-separated extra tags")
    parser.add_argument("--note", default="", help="Why you saved this")
    args = parser.parse_args()

    extra_tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    entry_id = clip(args.url, who=args.who, extra_tags=extra_tags, note=args.note)

    if entry_id:
        print(f"\n✅ Saved to second brain: {entry_id}")
    else:
        print("\n⚠️  Saved with warnings — check output above")


if __name__ == "__main__":
    main()
