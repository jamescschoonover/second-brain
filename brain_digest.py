#!/usr/bin/env python3
"""
brain_digest.py — Weekly Second Brain digest email for James and Kallan.

Includes:
  - This week's entries (summary + emotion)
  - "On This Day" memories (1 month, 6 months, 1 year ago)
  - Emotional arc of the week
  - Open action items

Usage:
  python3 ~/second-brain/brain_digest.py              # send to both
  python3 ~/second-brain/brain_digest.py --who james  # James only
  python3 ~/second-brain/brain_digest.py --who kallan
  python3 ~/second-brain/brain_digest.py --dry-run    # print only
"""

import argparse, base64, json, sys
from collections import Counter
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

BASE_DIR    = Path(__file__).parent
INDEX_FILE  = BASE_DIR / "index.json"
TOKEN_FILE  = Path.home() / "vendoo-tool/oauth_token.json"

JAMES_EMAIL  = "james.c.schoonover@gmail.com"
KALLAN_EMAIL = "kallan.a.schoonover@gmail.com"
SENDER       = JAMES_EMAIL

PORTAL_URL   = "https://jamescschoonover.github.io/Game-Of-Life/brain.html"

SOURCE_LABEL = {
    "mindsera": "Journal",
    "recall":   "Capture",
    "manual":   "Note",
    "journal":  "Journal",
    "podcast":  "Podcast",
    "clip":     "Clip",
}

EMO_COLOR = {
    "hopeful":    "#2ecc71", "grateful":   "#f1c40f", "determined": "#e67e22",
    "motivated":  "#3498db", "peaceful":   "#1abc9c", "joyful":     "#f39c12",
    "reflective": "#9b59b6", "convicted":  "#e74c3c", "anxious":    "#c0392b",
    "struggling": "#922b21", "excited":    "#e91e63", "proud":      "#00bcd4",
}


def load_index():
    if not INDEX_FILE.exists():
        return []
    return json.loads(INDEX_FILE.read_text()).get("entries", [])


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def week_bounds(ref):
    mon = ref - timedelta(days=ref.weekday())
    return mon, mon + timedelta(days=6)


def on_this_day(all_entries, ref, who_key):
    """Find entries from ±3 days around 1mo / 6mo / 1yr ago."""
    results = {}
    for label, delta in [("1 month ago", 30), ("6 months ago", 182), ("1 year ago", 365)]:
        target = ref - timedelta(days=delta)
        lo, hi = target - timedelta(days=3), target + timedelta(days=3)
        hits = []
        for e in all_entries:
            if e.get("who", "james") != who_key:
                continue
            d = parse_date(e.get("date", ""))
            if d and lo <= d <= hi:
                hits.append((abs((d - target).days), e))
        if hits:
            hits.sort(key=lambda x: x[0])
            results[label] = hits[0][1]
    return results


def emo_badge(emo):
    color = EMO_COLOR.get(emo, "#555")
    return f'<span style="background:{color};color:#000;padding:1px 8px;border-radius:10px;font-size:0.7rem;font-weight:600;">{emo}</span>' if emo else ""


def get_gmail():
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def build_html(who_name, week_entries, otd, open_actions):
    today = date.today()
    mon, sun = week_bounds(today)
    week_label = f"{mon.strftime('%b %-d')} – {sun.strftime('%b %-d, %Y')}"

    # ── This Week section ────────────────────────────────────────────────────
    if week_entries:
        source_counts = Counter(SOURCE_LABEL.get(e.get("source", ""), "Note") for e in week_entries)
        src_summary   = " &nbsp;·&nbsp; ".join(
            f"{n} {s.lower()}{'s' if n>1 else ''}"
            for s, n in sorted(source_counts.items())
        )
        emotions_list = [e.get("emotion", "") for e in week_entries if e.get("emotion")]
        emo_arc       = " → ".join(emotions_list[:8]) if emotions_list else "—"

        all_tags = []
        for e in week_entries:
            all_tags.extend(e.get("tags") or [])
        top_tags = [t for t, _ in Counter(all_tags).most_common(7)]
        tags_html = " ".join(
            f'<span style="background:#1e1e3a;border:1px solid #3a3a5a;border-radius:12px;'
            f'padding:2px 10px;font-size:0.72rem;color:#a78bfa;">{t}</span>'
            for t in top_tags
        )

        entry_cards = ""
        for e in week_entries[:12]:
            d    = parse_date(e.get("date", ""))
            dstr = d.strftime("%a %b %-d") if d else ""
            src  = SOURCE_LABEL.get(e.get("source", ""), "Note")
            emo  = e.get("emotion", "")
            summ = (e.get("summary") or "")[:140]
            if len(e.get("summary") or "") > 140:
                summ += "…"
            entry_cards += f"""
            <div style="background:#0e0e1e;border:1px solid #2a2a4a;border-radius:8px;
                        padding:13px 15px;margin-bottom:9px;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;
                          margin-bottom:5px;">
                <span style="color:#e0e0e0;font-weight:600;font-size:0.88rem;
                             max-width:78%;">{e.get('title','Untitled')}</span>
                <span style="color:#555;font-size:0.72rem;white-space:nowrap;
                             margin-left:8px;">{dstr}</span>
              </div>
              <div style="color:#888;font-size:0.72rem;margin-bottom:5px;">
                {src} {emo_badge(emo)}
              </div>
              <div style="color:#b0b0c0;font-size:0.83rem;line-height:1.55;">{summ}</div>
            </div>"""
        more_note = (
            f'<div style="color:#555;font-size:0.78rem;text-align:center;margin-top:6px;">'
            f'+ {len(week_entries)-12} more entries this week</div>'
        ) if len(week_entries) > 12 else ""

        week_section = f"""
        <div style="background:#12122a;border:1px solid #2a2a4a;border-radius:12px;
                    padding:20px;margin-bottom:16px;">
          <div style="color:#a78bfa;font-size:0.68rem;letter-spacing:2px;
                      text-transform:uppercase;margin-bottom:14px;">
            THIS WEEK — {len(week_entries)} {'entry' if len(week_entries)==1 else 'entries'}
            &nbsp;&nbsp;{src_summary}
          </div>
          {entry_cards}{more_note}
          <div style="border-top:1px solid #2a2a4a;margin-top:14px;padding-top:14px;">
            <div style="color:#666;font-size:0.68rem;margin-bottom:6px;">EMOTIONAL ARC</div>
            <div style="color:#ccc;font-size:0.82rem;line-height:1.8;margin-bottom:10px;">{emo_arc}</div>
            <div style="color:#666;font-size:0.68rem;margin-bottom:7px;">TOP THEMES</div>
            <div style="line-height:2;">{tags_html}</div>
          </div>
        </div>"""
    else:
        week_section = """
        <div style="background:#12122a;border:1px solid #2a2a4a;border-radius:12px;
                    padding:20px;margin-bottom:16px;">
          <div style="color:#555;font-size:0.88rem;line-height:1.6;">
            No entries this week yet.<br>
            <span style="color:#777;">Open the journal and add one — even a sentence counts.</span>
          </div>
        </div>"""

    # ── On This Day section ──────────────────────────────────────────────────
    otd_section = ""
    if otd:
        otd_cards = ""
        for label, e in otd.items():
            d    = parse_date(e.get("date", ""))
            dstr = d.strftime("%B %-d, %Y") if d else ""
            src  = SOURCE_LABEL.get(e.get("source", ""), "Note")
            emo  = e.get("emotion", "")
            summ = (e.get("summary") or "")[:160]
            if len(e.get("summary") or "") > 160:
                summ += "…"
            otd_cards += f"""
            <div style="background:#0e0e1e;border-left:3px solid #f1c40f;
                        border-radius:0 8px 8px 0;padding:13px 15px;margin-bottom:10px;">
              <div style="color:#f1c40f;font-size:0.68rem;letter-spacing:1px;
                          text-transform:uppercase;margin-bottom:4px;">
                ⏰ {label} &nbsp;·&nbsp; {dstr}
              </div>
              <div style="color:#e0e0e0;font-weight:600;font-size:0.88rem;
                          margin-bottom:5px;">{e.get('title','Untitled')}</div>
              <div style="color:#888;font-size:0.72rem;margin-bottom:5px;">
                {src} {emo_badge(emo)}
              </div>
              <div style="color:#b0b0c0;font-size:0.83rem;line-height:1.55;">{summ}</div>
            </div>"""
        otd_section = f"""
        <div style="background:#12122a;border:1px solid rgba(241,196,15,0.25);
                    border-radius:12px;padding:20px;margin-bottom:16px;">
          <div style="color:#f1c40f;font-size:0.68rem;letter-spacing:2px;
                      text-transform:uppercase;margin-bottom:14px;">✨ ON THIS DAY</div>
          {otd_cards}
        </div>"""

    # ── Open Action Items ────────────────────────────────────────────────────
    action_section = ""
    if open_actions:
        items_html = "".join(
            f'<div style="color:#c0c0d0;font-size:0.83rem;padding:7px 0;'
            f'border-bottom:1px solid #1e1e3a;">◦ {a.get("text") or a.get("action","?")}</div>'
            for a in open_actions[:6]
        )
        more_html = (
            f'<div style="color:#555;font-size:0.78rem;margin-top:7px;">'
            f'+ {len(open_actions)-6} more</div>'
        ) if len(open_actions) > 6 else ""
        action_section = f"""
        <div style="background:#12122a;border:1px solid rgba(231,76,60,0.3);
                    border-radius:12px;padding:20px;margin-bottom:16px;">
          <div style="color:#e74c3c;font-size:0.68rem;letter-spacing:2px;
                      text-transform:uppercase;margin-bottom:12px;">
            ⚡ OPEN ACTION ITEMS ({len(open_actions)})
          </div>
          {items_html}{more_html}
        </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta charset="utf-8">
</head>
<body style="margin:0;padding:0;background:#0a0a0f;
             font-family:'Segoe UI',Arial,sans-serif;color:#e0e0e0;">
<div style="max-width:600px;margin:0 auto;padding:20px 16px;">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#13132a,#1a1535);
              border:1px solid #a78bfa;border-radius:14px;
              padding:26px 24px;text-align:center;margin-bottom:18px;">
    <div style="color:#a78bfa;font-size:0.68rem;letter-spacing:3px;
                text-transform:uppercase;margin-bottom:6px;">Second Brain Weekly</div>
    <div style="color:#fff;font-size:1.65rem;font-weight:700;margin:6px 0;">
      {who_name}'s Week
    </div>
    <div style="color:#777;font-size:0.82rem;">{week_label}</div>
  </div>

  {week_section}
  {otd_section}
  {action_section}

  <!-- CTA -->
  <div style="text-align:center;margin:20px 0 10px;">
    <a href="{PORTAL_URL}"
       style="display:inline-block;background:#a78bfa;color:#000;
              font-weight:700;font-size:0.85rem;padding:12px 28px;
              border-radius:24px;text-decoration:none;">
      Search Your Brain →
    </a>
  </div>

  <!-- Footer -->
  <div style="text-align:center;padding:18px;color:#333;font-size:0.72rem;">
    Forge · Schoonover Family · Second Brain Digest<br>
    Replies to this email are not monitored.
  </div>

</div>
</body>
</html>"""


def send_digest(who_name, email, week_entries, otd, open_actions, dry_run=False):
    today = date.today()
    mon, sun = week_bounds(today)

    if dry_run:
        print(f"\n─── {who_name} Digest ───")
        print(f"  Week: {mon} → {sun}")
        print(f"  This week: {len(week_entries)} entries")
        print(f"  On This Day: {list(otd.keys()) or 'none'}")
        print(f"  Open actions: {len(open_actions)}")
        print(f"  Would send to: {email}")
        return

    html = build_html(who_name, week_entries, otd, open_actions)
    svc  = get_gmail()
    msg  = MIMEMultipart("alternative")
    msg["Subject"] = f"📓 Your Week in the Second Brain — {today.strftime('%b %-d')}"
    msg["From"]    = SENDER
    msg["To"]      = email
    msg.attach(MIMEText(html, "html"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"  Digest → {who_name} ({email}) ✅")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--who",     choices=["james", "kallan", "all"], default="all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    all_entries = load_index()
    today       = date.today()
    mon, sun    = week_bounds(today)

    actions_file = BASE_DIR / "pending_actions.json"
    open_actions = []
    if actions_file.exists():
        raw = json.loads(actions_file.read_text())
        if isinstance(raw, list):
            open_actions = [a for a in raw if a.get("status", "pending") == "pending"]

    targets = []
    if args.who in ("james", "all"):
        targets.append(("James", "james", JAMES_EMAIL))
    if args.who in ("kallan", "all"):
        targets.append(("Kallan", "kallan", KALLAN_EMAIL))

    for who_name, who_key, email in targets:
        week_entries = sorted(
            [
                e for e in all_entries
                if e.get("who", "james") == who_key
                and (d := parse_date(e.get("date", ""))) is not None
                and mon <= d <= sun
            ],
            key=lambda e: e.get("date", ""),
        )
        otd = on_this_day(all_entries, today, who_key)
        send_digest(who_name, email, week_entries, otd, open_actions, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
