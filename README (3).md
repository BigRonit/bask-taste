# BASK taste corpus

Weekly pipeline that turns saved reels into a measured reference corpus.
Runs free on GitHub Actions. Nothing gets downloaded, so no IP blocking.

**Your weekly effort: paste links into a sheet. That's it.**

---

## Setup (about 15 minutes, once)

### 1. Google Sheet

New sheet, two columns in row 1:

| url | note |
|---|---|
| https://tiktok.com/@handle/video/123 | good midpoint turn |

`note` is optional — one line on why you saved it. It ends up in the corpus
and it's the thing that makes the corpus useful six months from now when you
can't remember why that reel was in there.

Then **File → Share → Publish to web → Comma-separated values (.csv)**.
Copy the URL it gives you. Publishing exposes only this sheet, not your Drive.

### 2. Supadata key

Sign up at supadata.ai and grab an API key. Free tier is 100 credits/month,
roughly one per transcript. The pipeline caps itself at 12 new reels per run,
so you can't accidentally burn the month in one go.

This is a separate key from your Composio connection — the Action can't reach
that one.

### 3. Repo

Push this folder to a **public** repo. Two reasons: Actions minutes are
unlimited on public repos, and Claude can read the corpus directly from the
raw URL without you pasting anything.

Everything in it is public reels and their transcripts. If you'd rather it be
private, that works too — you'd just sync `TASTE-CORPUS.md` to Drive instead.
Your API keys are in Secrets either way, which is safe in a public repo.

### 4. Secrets

**Settings → Secrets and variables → Actions → New repository secret**

- `SHEET_CSV_URL` — from step 1
- `SUPADATA_API_KEY` — from step 2

### 5. First run

Actions tab → "Build taste corpus" → **Run workflow**. Don't wait for Monday;
confirm it works now while you still remember what you configured.

---

## Weekly loop

While scrolling, save anything that lands to an IG collection called `yap`.
Sunday, paste the links into the sheet — ten seconds. Monday morning the
Action transcribes them, measures pace, and rewrites `TASTE-CORPUS.md`.

Prefer TikTok URLs when a creator cross-posts. Supadata handles both, but
TikTok metadata is cleaner.

---

## What lands in the corpus

Per reel: verbatim transcript, first-3s hook in isolation, duration,
words/sec, and median seconds-per-beat.

The last one earns its place. A one-take yap sits around 2.5–4s per beat.
Anything above ~5.5s is usually edited with cuts, which means it's a different
format wearing yap clothes. The script flags those rather than dropping them —
you decide.

The **Hooks only** section is the highest-value part. Forty hooks in a row,
stripped of context, is the fastest hook-writing calibration there is.

Entries older than 90 days drop to an archive section automatically, so the
active set stays current without you pruning it.

---

## Frames (optional, monthly, local)

The corpus is text. Your format definition is visual — talking head, one-take
feel, something in hand, background activity. Transcripts can't show any of
that.

Once a month, run `/watch` locally on your three or four best entries. It
pulls frames alongside the transcript. Locally, yt-dlp works fine because
you're on a residential IP.

Set up a Groq key first (console.groq.com, free, no credit card) so `/watch`
has a Whisper fallback.

---

## Gotchas

**Scheduled workflows can go dormant** after a stretch of repository
inactivity. The weekly commit should keep it alive, but glance at the Actions
tab once a month. If it's paused, one manual run wakes it up.

**Supadata will fail on some reels** — private accounts, region locks,
audio it can't process. The script logs the failure and moves on rather than
dying. Failed URLs stay in the sheet, so delete them once you see them skipped
twice.

**Don't let the sheet become a dumping ground.** Twenty-five sharp entries
beat two hundred mediocre ones. If it isn't a reel you'd want a script to
sound like, it doesn't go in.
