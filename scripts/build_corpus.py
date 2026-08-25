#!/usr/bin/env python3
"""
BASK taste corpus builder.

Reads a published Google Sheet of saved reel URLs, pulls transcripts via
Supadata, computes pacing metrics, and regenerates TASTE-CORPUS.md.

Idempotent: already-processed URLs are skipped, so re-running is free.
"""

import csv
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

SHEET_URL = os.environ.get("SHEET_URL", "").strip()
SUPADATA_KEY = os.environ.get("SUPADATA_API_KEY", "").strip()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_JSON = os.path.join(ROOT, "data", "corpus.json")
CORPUS_MD = os.path.join(ROOT, "TASTE-CORPUS.md")

ACTIVE_DAYS = 90          # entries older than this move to the archive section
MAX_NEW_PER_RUN = 10      # 2 credits each (transcript + metadata) of 100/mo
HOOK_WINDOW_SEC = 3.0     # how much of the open counts as "the hook"


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def fetch(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------

def sheet_id(url):
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url)
    if not m:
        raise ValueError("SHEET_URL doesn't look like a Google Sheets link")
    return m.group(1)


def read_sheet():
    """Reads the sheet as XLSX rather than CSV.

    Why: pasting a link into Sheets usually turns it into a smart chip, which
    displays as 'Instagram' and drops the URL from CSV export entirely. The
    XLSX export keeps the real hyperlink target, so you can paste however you
    like and it still works. Plain-text URLs are picked up too.
    """
    import zipfile
    import xml.etree.ElementTree as ET

    url = ("https://docs.google.com/spreadsheets/d/"
           f"{sheet_id(SHEET_URL)}/export?format=xlsx")
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as r:
        blob = r.read()

    zf = zipfile.ZipFile(io.BytesIO(blob))
    found = []

    # 1. hyperlink targets (smart chips and linked text)
    for name in zf.namelist():
        if name.endswith("sheet1.xml.rels"):
            root = ET.fromstring(zf.read(name))
            for rel in root:
                if "hyperlink" in rel.get("Type", ""):
                    found.append(rel.get("Target", ""))

    # 2. plain-text URLs typed directly into cells
    for name in zf.namelist():
        if name.endswith("sharedStrings.xml"):
            for t in ET.fromstring(zf.read(name)).iter():
                txt = (t.text or "").strip()
                if txt.startswith("http"):
                    found.append(txt)

    out, seen = [], set()
    for u in found:
        u = u.split("?")[0].rstrip("/")
        u = u.replace("instagram.com/reels/", "instagram.com/reel/")
        if u.startswith("http") and u not in seen:
            seen.add(u)
            out.append({"url": u, "note": ""})
    return out


def platform_of(url):
    if "instagram.com" in url:
        return "instagram"
    if "tiktok.com" in url:
        return "tiktok"
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    return "other"


def handle_of(url):
    m = re.search(r"tiktok\.com/@([\w.\-]+)", url)
    if m:
        return "@" + m.group(1)
    return ""


# --------------------------------------------------------------------------
# Transcription
# --------------------------------------------------------------------------

def supadata_transcript(url, attempts=10, delay=6):
    """Returns a list of {text, offset_ms, duration_ms}.
    Handles both the immediate response and the queued jobId path."""
    endpoint = "https://api.supadata.ai/v1/transcript?" + urllib.parse.urlencode(
        {"url": url, "lang": "en", "text": "false", "mode": "auto"}
    )
    headers = {"x-api-key": SUPADATA_KEY}
    data = json.loads(fetch(endpoint, headers))

    if "jobId" in data:
        job = data["jobId"]
        for _ in range(attempts):
            time.sleep(delay)
            res = json.loads(fetch(
                f"https://api.supadata.ai/v1/transcript/{job}", headers))
            status = res.get("status", "")
            if status == "completed":
                data = res
                break
            if status == "failed":
                raise RuntimeError(res.get("error", "supadata job failed"))
        else:
            raise RuntimeError("supadata job timed out")

    segments = data.get("content") or data.get("transcript") or []
    norm = []
    for s in segments:
        if not isinstance(s, dict):
            continue
        norm.append({
            "text": (s.get("text") or "").strip(),
            "offset_ms": s.get("offset", s.get("start", 0)) or 0,
            "duration_ms": s.get("duration", 0) or 0,
        })
    return [s for s in norm if s["text"]]


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def supadata_metadata(url, debug=False):
    """Returns platform stats: views, likes, comments, shares.

    Handles the async job pattern — some platforms return HTTP 202 with a
    jobId instead of the data directly. Shares-per-view is the send-rate
    proxy: the variable that separates 'well watched' from 'blew up'.
    """
    endpoint = "https://api.supadata.ai/v1/metadata?" + urllib.parse.urlencode(
        {"url": url})
    headers = {"x-api-key": SUPADATA_KEY}
    data = json.loads(fetch(endpoint, headers))

    if "jobId" in data:
        job = data["jobId"]
        for _ in range(10):
            time.sleep(4)
            res = json.loads(fetch(
                f"https://api.supadata.ai/v1/metadata/{job}", headers))
            if res.get("status") == "completed":
                data = res
                break
            if res.get("status") == "failed":
                raise RuntimeError(res.get("error", "metadata job failed"))
        else:
            raise RuntimeError("metadata job timed out")

    # some responses nest the payload one level down
    if "stats" not in data and isinstance(data.get("result"), dict):
        data = data["result"]

    if debug:
        print(f"     [debug] metadata keys: {sorted(data.keys())}")
        print(f"     [debug] raw: {json.dumps(data)[:400]}")

    stats = data.get("stats") or {}
    author = data.get("author") or {}
    if not stats:
        raise RuntimeError(
            f"no stats in response; keys were {sorted(data.keys())}")

    views, shares = stats.get("views"), stats.get("shares")
    out = {
        "views": views,
        "likes": stats.get("likes"),
        "comments": stats.get("comments"),
        "shares": shares,
        "author": author.get("username") or author.get("displayName") or "",
        "posted": data.get("createdAt", ""),
    }
    if views and shares:
        out["shares_per_10k"] = round(shares / views * 10000, 1)
    if views and stats.get("comments"):
        out["comments_per_10k"] = round(stats["comments"] / views * 10000, 1)
    return out


def dedupe_words(text, n=6):
    """Supadata's ASR sometimes repeats a run of words mid-sentence.
    Drops any 6-gram that already appeared in the last ~40 words."""
    w = text.split()
    out, i = [], 0
    while i < len(w):
        window = " ".join(w[i:i + n]).lower()
        if len(w[i:i + n]) == n and window in " ".join(out[-40:]).lower():
            i += n
            continue
        out.append(w[i])
        i += 1
    return " ".join(out)


def analyse(segments):
    if not segments:
        return {}
    last = segments[-1]
    total_sec = (last["offset_ms"] + last["duration_ms"]) / 1000.0
    words = sum(len(s["text"].split()) for s in segments)
    hook = " ".join(s["text"] for s in segments
                    if s["offset_ms"] / 1000.0 < HOOK_WINDOW_SEC)

    # seconds per spoken beat — the 3-5s target from the pacing research
    gaps = []
    for a, b in zip(segments, segments[1:]):
        gaps.append((b["offset_ms"] - a["offset_ms"]) / 1000.0)
    gaps = [g for g in gaps if g > 0]
    beat = sorted(gaps)[len(gaps) // 2] if gaps else 0.0

    return {
        "duration_sec": round(total_sec, 1),
        "word_count": words,
        "words_per_sec": round(words / total_sec, 2) if total_sec else 0,
        "median_beat_sec": round(beat, 2),
        "hook_text": hook,
        "full_text": dedupe_words(" ".join(s["text"] for s in segments)),
    }


def flags(m):
    """Cheap format gates. Not a verdict — a sorting aid."""
    out = []
    d = m.get("duration_sec", 0)
    wps = m.get("words_per_sec", 0)
    beat = m.get("median_beat_sec", 0)
    # Bands widened after the first 8 measured reels overturned the guesses:
    # observed pace ran 2.99-4.71 (median 4.04) and duration 8.5-100.1s
    # (median 56.2s). These re-derive as the corpus grows — don't hand-edit.
    if d and not (10 <= d <= 105):
        out.append(f"duration {d}s outside measured range")
    if wps and not (2.7 <= wps <= 5.0):
        out.append(f"pace {wps} w/s outside measured range")
    if beat and beat > 5.5:
        out.append(f"beat {beat}s — likely edited, not one-take")
    if m.get("word_count", 0) < 30:
        out.append("sparse speech — check it isn't a skit or text-on-screen")
    return out


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------

def derive_tiers(entries):
    """Marks each reel against its own creator's median views.
    This is why you don't have to label anything: 'hit' means it beat that
    account's normal, which is the only comparison that means much."""
    by_handle = {}
    for e in entries:
        if e.get("views") and e.get("handle"):
            by_handle.setdefault(e["handle"], []).append(e)
    for handle, group in by_handle.items():
        views = sorted(e["views"] for e in group)
        median = views[len(views) // 2]
        for e in group:
            mult = e["views"] / median if median else 1
            e["view_multiple"] = round(mult, 2)
            if len(group) < 2:
                e["tier"] = "unpaired"
            elif mult >= 1.5:
                e["tier"] = "hit"
            elif mult <= 0.8:
                e["tier"] = "base"
            else:
                e["tier"] = "mid"


def load_corpus():
    if os.path.exists(CORPUS_JSON):
        with open(CORPUS_JSON) as f:
            return json.load(f)
    return {"entries": []}


def save_corpus(corpus):
    os.makedirs(os.path.dirname(CORPUS_JSON), exist_ok=True)
    with open(CORPUS_JSON, "w") as f:
        json.dump(corpus, f, indent=2, ensure_ascii=False)


def render_md(corpus):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=ACTIVE_DAYS)
    entries = sorted(corpus["entries"],
                     key=lambda e: e.get("added", ""), reverse=True)

    active, archived = [], []
    for e in entries:
        try:
            added = datetime.fromisoformat(e["added"])
        except Exception:
            added = now
        (active if added >= cutoff else archived).append(e)

    L = []
    L.append("# BASK Taste Corpus")
    L.append("")
    L.append("Reference set of yap-format reels. Read this before writing "
             "any script — pull pacing and hook structure from what's here, "
             "not from priors.")
    L.append("")
    L.append(f"Updated {now.strftime('%Y-%m-%d')} · "
             f"{len(active)} active · {len(archived)} archived "
             f"(older than {ACTIVE_DAYS} days)")
    L.append("")

    if active:
        wps = [e["words_per_sec"] for e in active if e.get("words_per_sec")]
        dur = [e["duration_sec"] for e in active if e.get("duration_sec")]
        if wps:
            L.append("## Measured baseline")
            L.append("")
            L.append(f"- Pace: {min(wps)}–{max(wps)} words/sec "
                     f"(median {sorted(wps)[len(wps)//2]})")
            L.append(f"- Length: {min(dur)}–{max(dur)}s "
                     f"(median {sorted(dur)[len(dur)//2]}s)")
            L.append("")

        L.append("## Hooks only")
        L.append("")
        L.append("First three seconds, verbatim. Scan this before "
                 "running HOOKS.")
        L.append("")
        for e in active:
            if e.get("hook_text"):
                L.append(f"- {e['hook_text']}")
        L.append("")

        L.append("## Entries")
        L.append("")
        for e in active:
            L.append(f"### {e.get('handle') or e['platform']} · "
                     f"{e.get('duration_sec','?')}s")
            L.append("")
            L.append(f"{e['url']}")
            L.append("")
            if e.get("note"):
                L.append(f"**Why saved:** {e['note']}")
                L.append("")
            L.append(f"**Pace:** {e.get('words_per_sec','?')} w/s · "
                     f"**beat:** {e.get('median_beat_sec','?')}s · "
                     f"**words:** {e.get('word_count','?')}")
            L.append("")
            if e.get("views"):
                L.append(f"**Views:** {e['views']:,} · "
                         f"**sends/10k views:** {e.get('shares_per_10k','?')} · "
                         f"**comments/10k:** {e.get('comments_per_10k','?')} · "
                         f"**tier:** {e.get('tier','?')} ({e.get('view_multiple','?')}x this creator's median)")
            L.append("")
            if e.get("flags"):
                L.append("**Check:** " + "; ".join(e["flags"]))
                L.append("")
            L.append(f"**Hook:** {e.get('hook_text','—')}")
            L.append("")
            L.append("**Transcript:**")
            L.append("")
            L.append(e.get("full_text", ""))
            L.append("")

    if archived:
        L.append("---")
        L.append("")
        L.append("## Archived")
        L.append("")
        for e in archived:
            L.append(f"- {e.get('handle') or e['platform']} — {e['url']}")
        L.append("")

    with open(CORPUS_MD, "w") as f:
        f.write("\n".join(L))


# --------------------------------------------------------------------------

def main():
    if not SHEET_URL:
        die("SHEET_URL not set")
    if not SUPADATA_KEY:
        die("SUPADATA_API_KEY not set")

    corpus = load_corpus()
    seen = {e["url"] for e in corpus["entries"]}
    rows = read_sheet()
    new = [r for r in rows if r["url"] not in seen][:MAX_NEW_PER_RUN]

    print(f"{len(rows)} rows in sheet · {len(seen)} already in corpus · "
          f"{len(new)} to process")

    added = 0
    for i, r in enumerate(new):
        if i:
            time.sleep(1.5)   # free tier allows 1 request/second
        print(f"  -> {r['url']}")
        try:
            segs = supadata_transcript(r["url"])
        except Exception as exc:
            print(f"     skipped: {exc}", file=sys.stderr)
            continue
        if not segs:
            print("     skipped: empty transcript", file=sys.stderr)
            continue

        m = analyse(segs)
        try:
            time.sleep(1.2)   # 1 req/sec ceiling on the free tier
            meta = supadata_metadata(r["url"], debug=(added == 0))
        except Exception as exc:
            print(f"     !! METADATA FAILED (no views/handle/tier): {exc}",
                  file=sys.stderr)
            meta = {}

        entry = {
            "url": r["url"],
            "note": r["note"],
            "platform": platform_of(r["url"]),
            "handle": meta.get("author") or handle_of(r["url"]),
            "added": datetime.now(timezone.utc).isoformat(),
            **meta,
            **m,
        }
        entry["flags"] = flags(m)
        corpus["entries"].append(entry)
        added += 1

    derive_tiers(corpus["entries"])
    save_corpus(corpus)
    render_md(corpus)
    print(f"added {added} · corpus now {len(corpus['entries'])} entries")


if __name__ == "__main__":
    main()
