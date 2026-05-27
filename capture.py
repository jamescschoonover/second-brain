#!/usr/bin/env python3
"""
capture.py — Quick-capture CLI for voice notes, quick thoughts, and text entries.

Usage:
  python3 capture.py "Your quick thought"
  python3 capture.py --note "Longer thought here..."
  python3 capture.py "Bundle pricing idea" --tags idea,reselling,pricing
  python3 capture.py "Affirm stress" --tags finance,emotion --emotion frustrated
  python3 capture.py "Research eBay promoted listings" --action
  python3 capture.py --voice
  python3 capture.py --voice --sec 30
"""

import argparse
import importlib.util as _ilu
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Load import.py (can't `import import` — keyword conflict)
# ---------------------------------------------------------------------------

def _load_brain():
    _spec = _ilu.spec_from_file_location("brain", Path(__file__).parent / "import.py")
    _mod  = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod

_brain = _load_brain()

load_index          = _brain.load_index
save_index          = _brain.save_index
known_ids           = _brain.known_ids
make_id             = _brain.make_id
analyze_with_haiku  = _brain.analyze_with_haiku
build_entry         = _brain.build_entry
index_entry         = _brain.index_entry
ENTRIES_DIR         = _brain.ENTRIES_DIR
ENV_FILE            = _brain.ENV_FILE

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR             = Path(__file__).parent
PENDING_ACTIONS_FILE = BASE_DIR / "pending_actions.json"
WHISPER_MODEL        = "small"
DEFAULT_RECORD_SECS  = 20

# ---------------------------------------------------------------------------
# Pending actions helpers
# ---------------------------------------------------------------------------

def load_pending_actions() -> list:
    if PENDING_ACTIONS_FILE.exists():
        try:
            data = json.loads(PENDING_ACTIONS_FILE.read_text())
            return data if isinstance(data, list) else []
        except Exception:
            pass
    return []


def save_pending_actions(actions: list):
    PENDING_ACTIONS_FILE.write_text(json.dumps(actions, indent=2))


def add_pending_action(entry_id: str, title: str, content: str, tags: list):
    actions = load_pending_actions()
    actions.append({
        "entry_id":   entry_id,
        "title":      title,
        "content":    content[:300],
        "tags":       tags,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status":     "pending",
    })
    save_pending_actions(actions)

# ---------------------------------------------------------------------------
# Haiku client
# ---------------------------------------------------------------------------

def _get_client():
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[error] ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)
    import anthropic
    return anthropic.Anthropic(api_key=api_key)

# ---------------------------------------------------------------------------
# Build and save an entry
# ---------------------------------------------------------------------------

def save_capture(
    text: str,
    source: str,        # "manual" or "voice"
    tags: list[str],
    emotion: str | None,
    is_action: bool,
    title: str | None = None,
) -> str:
    """Build, analyze, save, and index an entry. Returns entry_id."""
    client = _get_client()

    date_str = datetime.now().strftime("%Y-%m-%d")

    # Auto-title from first ~60 chars of content if not given
    if not title:
        first_line = text.strip().splitlines()[0] if text.strip() else "Capture"
        title = first_line[:60]

    raw = {
        "date":          date_str,
        "title":         title,
        "source":        source,
        "james_content": text.strip(),
        "ai_reflection": "",
        "mood":          emotion or "",
        "tags":          tags,
        "prompt":        "",
        "source_file":   None,
        "source_url":    None,
        # Used by build_entry — not a podcast/media entry
        "media_type":    None,
        "duration_s":    None,
        "segments":      None,
    }

    print("Analyzing with Haiku...")
    analysis, in_tok, out_tok = analyze_with_haiku(raw, client)

    # Override content_type to "note"
    analysis["content_type"] = "note"

    # Merge any user-supplied tags onto Haiku tags
    if tags:
        existing_tags = analysis.get("tags", [])
        for t in tags:
            if t not in existing_tags:
                existing_tags.append(t)
        analysis["tags"] = existing_tags

    # Override emotion if user specified one
    if emotion:
        analysis["emotion"] = emotion

    entry    = build_entry(raw, analysis)
    entry_id = entry["id"]

    ENTRIES_DIR.mkdir(parents=True, exist_ok=True)
    entry_path = ENTRIES_DIR / f"{entry_id}.json"
    entry_path.write_text(json.dumps(entry, indent=2))

    # Update index
    index = load_index()
    if entry_id not in known_ids(index):
        index["entries"].append(index_entry(entry))
        save_index(index)

    cost = ((in_tok + out_tok) / 1000) * 0.0008
    print(f"Saved: {entry_id}  (~${cost:.4f})")

    if is_action:
        add_pending_action(entry_id, title, text, analysis.get("tags", []))
        print(f"Added to pending_actions.json")

    return entry_id


# ---------------------------------------------------------------------------
# Text capture
# ---------------------------------------------------------------------------

def capture_text(text: str, args) -> str:
    return save_capture(
        text    = text,
        source  = "manual",
        tags    = _parse_tags(args.tags),
        emotion = args.emotion,
        is_action = args.action,
    )


# ---------------------------------------------------------------------------
# Voice capture
# ---------------------------------------------------------------------------

def check_voice_deps() -> bool:
    """Return True if sounddevice + numpy are available, False otherwise (with message)."""
    missing = []
    try:
        import sounddevice  # noqa: F401
    except ImportError:
        missing.append("sounddevice")
    try:
        import numpy  # noqa: F401
    except ImportError:
        missing.append("numpy")

    if missing:
        print("[error] Voice capture requires additional packages.")
        print()
        print("Install with:")
        print(f"  pip install {' '.join(missing)}")
        if "sounddevice" in missing:
            print()
            print("If sounddevice fails, also try:")
            print("  sudo apt-get install libportaudio2 portaudio19-dev")
            print("  pip install sounddevice")
        return False
    return True


def record_audio(seconds: int) -> str:
    """Record from mic for `seconds` seconds. Returns path to temp WAV file."""
    import sounddevice as sd
    import numpy as np
    import wave

    SAMPLE_RATE = 16000  # 16kHz — optimal for Whisper speech recognition
    CHANNELS    = 1

    print(f"Recording for {seconds} seconds... (speak now)")
    audio = sd.rec(
        int(seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
    )
    sd.wait()
    print("Recording complete.")

    # Write to temp WAV
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit = 2 bytes
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())

    return tmp.name


def transcribe(wav_path: str) -> str:
    """Transcribe WAV with Whisper small model. Returns transcript text."""
    import whisper as _whisper
    print(f"Transcribing with Whisper ({WHISPER_MODEL} model)...")
    model  = _whisper.load_model(WHISPER_MODEL)
    result = model.transcribe(wav_path, verbose=False, fp16=False)
    text   = result.get("text", "").strip()
    return text


def capture_voice(args) -> str:
    if not check_voice_deps():
        sys.exit(1)

    seconds = args.sec if args.sec else DEFAULT_RECORD_SECS
    wav_path = record_audio(seconds)

    try:
        text = transcribe(wav_path)
    finally:
        try:
            os.unlink(wav_path)
        except Exception:
            pass

    if not text:
        print("[warn] Whisper returned empty transcript — nothing saved.")
        sys.exit(1)

    print(f"\nTranscript:\n  {text}\n")

    return save_capture(
        text      = text,
        source    = "voice",
        tags      = _parse_tags(args.tags),
        emotion   = args.emotion,
        is_action = args.action,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_tags(tags_str: str | None) -> list[str]:
    if not tags_str:
        return []
    return [t.strip() for t in tags_str.split(",") if t.strip()]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Quick-capture CLI for the Second Brain",
        epilog=(
            "Examples:\n"
            '  python3 capture.py "Idea: sell jewelry bundles"\n'
            '  python3 capture.py --note "Longer thought here..."\n'
            '  python3 capture.py "Bundle pricing" --tags idea,reselling\n'
            '  python3 capture.py "Affirm stress" --emotion frustrated\n'
            '  python3 capture.py "Research eBay promoted" --action\n'
            "  python3 capture.py --voice\n"
            "  python3 capture.py --voice --sec 30\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Positional text (optional — voice mode uses no positional)
    parser.add_argument(
        "text",
        nargs="?",
        help="Quick text to capture (surround in quotes)",
    )

    # Alternate way to pass text
    parser.add_argument(
        "--note", "-n",
        metavar="TEXT",
        help="Text to capture (use for longer thoughts)",
    )

    # Voice mode
    parser.add_argument(
        "--voice", "-v",
        action="store_true",
        help="Record from microphone and transcribe with Whisper",
    )
    parser.add_argument(
        "--sec",
        type=int,
        metavar="N",
        default=None,
        help=f"Recording duration in seconds (default: {DEFAULT_RECORD_SECS})",
    )

    # Metadata
    parser.add_argument(
        "--tags",
        metavar="tag1,tag2,...",
        default=None,
        help="Comma-separated tags to apply",
    )
    parser.add_argument(
        "--emotion",
        metavar="WORD",
        default=None,
        help=(
            "Emotion override (reflective/hopeful/grateful/anxious/"
            "determined/conflicted/peaceful/struggling/motivated/convicted/joyful/frustrated)"
        ),
    )
    parser.add_argument(
        "--action", "-a",
        action="store_true",
        help="Also add this entry to pending_actions.json",
    )

    args = parser.parse_args()

    # --- Voice mode ---
    if args.voice:
        if args.text or args.note:
            print("[error] Cannot combine --voice with text input")
            sys.exit(1)
        entry_id = capture_voice(args)
        print(f"\nDone. Entry: {entry_id}")
        print(f"Search: python3 ~/second-brain/query.py \"<topic>\"")
        return

    # --- Text mode ---
    text = args.text or args.note
    if not text:
        parser.print_help()
        sys.exit(1)

    entry_id = capture_text(text, args)
    print(f"\nDone. Entry: {entry_id}")
    print(f"Search: python3 ~/second-brain/query.py \"<topic>\"")


if __name__ == "__main__":
    main()
