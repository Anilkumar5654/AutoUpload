# Songs with Anil — Automated Lofi Video Pipeline (v2)

Automatically picks the next unpublished song from `songs/`, generates a
lofi video, uploads it to YouTube with a custom thumbnail and SEO
metadata, and tracks everything in an upload history — so nothing gets
uploaded twice. Runs on a schedule (or manually) via GitHub Actions.

## What's new in v3 — fully automatic, zero manual songs

- ✅ **Free, open-source music generation (MusicGen)** — when `songs/` has
  no unpublished track left, the pipeline generates a brand-new original
  lofi track itself using Meta's MusicGen model (runs locally in the
  workflow, no API key, no cost). You never have to add songs manually —
  every run is genuinely automatic end-to-end.

## What's new in v2

- ✅ Multiple-song queue — drop as many tracks as you want in `songs/`
- ✅ Duplicate-upload protection — by filename **and** SHA-256 audio content
  hash, so a renamed/re-encoded copy of an already-uploaded track is still
  caught
- ✅ Upload history / status tracking (success + failed runs both logged)
- ✅ Automatic thumbnail generation (video frame + title overlay)
- ✅ Per-song title/description/tags via `songs/metadata.json`
- ✅ Optional YouTube playlist assignment
- ✅ Automatic retry with backoff on auth/upload calls
- ✅ History file auto-committed back to the repo after each run
- ✅ Background rotation — drop several clips in `assets/backgrounds/` and
  one is picked at random each run (falls back to the single
  `assets/background.mp4` if that folder doesn't exist)
- ✅ Workflow concurrency lock — a manual run and a scheduled run can never
  overlap and double-upload the same song
- ✅ Top-level crash safety net — any unexpected exception is logged with a
  full traceback and fails the run cleanly instead of disappearing silently

## Repository structure

```
.
├── .github/workflows/auto_upload.yml
├── assets/
│   ├── background.mp4           # fallback: a single video (add this)
│   └── backgrounds/              # optional: rotate between MULTIPLE visuals
│       ├── bg01.mp4               # videos work as-is
│       └── bg02.jpg               # still images auto-convert to a slow-zoom video
├── songs/
│   ├── metadata.json            # optional per-song overrides
│   ├── song1.mp3                 # add your tracks here
│   └── song2.mp3
├── data/
│   └── upload_history.json      # auto-updated, do not edit manually
├── generator.py
├── requirements.txt
└── README.md
```

> Note: only `assets/background.mp4` (or `assets/backgrounds/`) and files
> under `songs/` are actually read by `generator.py` — nothing else in
> `assets/` is used, so don't leave unrelated audio files there.

**Videos vs. images in `assets/backgrounds/`:** you can mix both in the same
folder. A video (`.mp4`, `.mov`, `.mkv`, `.webm`) is looped or trimmed to
match the song's length as usual. A still image (`.jpg`, `.jpeg`, `.png`,
`.webp`) is automatically turned into a video for that song's full duration,
with a slow continuous zoom-in so it doesn't look like a frozen frame. Either
way, one file is picked at random per run.

---

## 1. Google Cloud Console setup

1. Create/select a project at the [Google Cloud Console](https://console.cloud.google.com/).
2. **APIs & Services → Library** → enable **YouTube Data API v3**.
3. **APIs & Services → OAuth consent screen** → choose **External**, fill
   in the required fields, and add your own account under **Test users**.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   → Application type **Desktop app** → note the **Client ID** and
   **Client Secret**.

## 2. Generate a refresh token locally (one-time)

```bash
pip install google-auth-oauthlib google-auth google-api-python-client
```

Save as `get_refresh_token.py` (local only — never commit it):

```python
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

flow = InstalledAppFlow.from_client_config(
    {
        "installed": {
            "client_id": "YOUR_CLIENT_ID",
            "client_secret": "YOUR_CLIENT_SECRET",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    },
    scopes=SCOPES,
)

credentials = flow.run_local_server(port=0)
print("Refresh token:", credentials.refresh_token)
```

Run it, sign in with the account that owns the "Songs with Anil" channel,
copy the printed refresh token, then delete the script.

## 3. Add GitHub repository secrets

**Settings → Secrets and variables → Actions → New repository secret:**

| Secret name              | Value                              |
|---------------------------|-------------------------------------|
| `YOUTUBE_CLIENT_ID`       | From step 1                        |
| `YOUTUBE_CLIENT_SECRET`   | From step 1                        |
| `YOUTUBE_REFRESH_TOKEN`   | From step 2                        |
| `DEFAULT_PLAYLIST_ID`     | (optional) a playlist ID to auto-add every upload to |

The workflow also needs **write** permission to commit the updated
history file — this is already set via `permissions: contents: write`
in `auto_upload.yml`, but if your org has restricted default workflow
permissions, also check **Settings → Actions → General → Workflow
permissions → Read and write permissions**.

## 4. Add your media

- `assets/background.mp4` (or `assets/backgrounds/` — see above) — this is
  still required; the pipeline doesn't generate visuals, only audio.
- `songs/*.mp3` (or `.wav` / `.m4a`) — **optional now.** If you drop tracks
  here, the pipeline uses them (first alphabetical unpublished one, so
  name them `01_song.mp3`, `02_song.mp3`, etc. for a specific order). If
  `songs/` is empty or everything in it has already been uploaded, the
  pipeline automatically generates a new original lofi track with
  MusicGen instead — no manual step needed.
- Optionally add entries to `songs/metadata.json`, keyed by filename
  *without* extension, for a manually-added song's custom
  title/description/tags/playlist. Auto-generated MusicGen tracks always
  use the auto-generated title templates (they have no fixed filename to
  key off of).

### About the MusicGen auto-generation step

- Uses `facebook/musicgen-small` (the free, open-source small model) via
  Hugging Face `transformers` — runs entirely inside the GitHub Actions
  runner, no external account or API key.
- **CPU-only and slow:** free GitHub Actions runners have no GPU, so
  generating even a 60-second track can take 10–20+ minutes. The workflow
  timeout is set to 120 minutes to give it room; the model weights (~1–2
  GB) are cached between runs via `actions/cache` so only the *first* run
  is slow to download.
- Track length is controlled by the `MUSICGEN_DURATION_SECONDS`
  environment variable (default 60s) — set it as a repository **variable**
  (Settings → Secrets and variables → Actions → **Variables** tab, not
  Secrets) if you want longer/shorter generated tracks. Longer tracks take
  proportionally longer to generate.
- Quality is rougher and more ambient/loopy than a paid service like
  Suno — it's the trade-off for $0 cost and no external dependency. If
  you later want higher quality, you can still drop hand-picked or
  Suno-generated tracks into `songs/` any time; the pipeline always
  prefers real files in `songs/` before falling back to generating one.
- Generated tracks are saved as `songs/musicgen_<timestamp>.wav` for that
  run only — they are **not** committed back to the repo (see
  `.gitignore`), since they've already been uploaded to YouTube by the
  time the run ends and don't need to persist.

## 5. Run it

- **Manually:** Actions tab → **Auto Upload Lofi Video** → **Run
  workflow** (choose privacy status; `unlisted` by default).
- **On schedule:** every Monday 09:00 UTC — edit the `cron` line in
  `auto_upload.yml` to change this. Note: scheduled workflows in public
  repos can be auto-disabled after 60 days of repo inactivity — push a
  small commit occasionally, or star-watch the repo's Actions tab.

Each run:
1. Picks the next song not already marked `"status": "success"` in
   `data/upload_history.json`.
2. Builds the video, generates a thumbnail, uploads, sets the thumbnail,
   adds to the playlist if configured.
3. Appends a record (success or failure, with the video ID/URL on
   success) to the history file and commits it back to the repo.
4. If every song in `songs/` has already been uploaded, the run exits
   cleanly with a log message — nothing gets re-uploaded.

A generated video + thumbnail are also kept as a workflow artifact for 7
days so you can double-check the render.

## 6. Checking upload status

Open `data/upload_history.json` in the repo any time to see what's been
uploaded, when, at what video ID/URL, and whether any run failed (with
the error message attached) so you can fix and let it retry — a failed
song is **not** marked as uploaded, so the next run will pick it up
again automatically.

## 7. Recommended safety workflow

1. Keep `privacy_status` as `unlisted` for your first several runs.
2. Spot-check on YouTube (audio sync, thumbnail, metadata).
3. Switch to `public` once you're confident, either in the workflow
   default or per-run via `workflow_dispatch`.

## Notes on thumbnails

Custom thumbnail upload via the API requires your channel to be
**phone-verified** in YouTube Studio. If it isn't, `generator.py` logs a
warning and continues — the video still uploads, just without the
custom thumbnail.

## Extending further

- Point the pipeline at a Suno (or other AI music) output folder to feed
  `songs/` automatically.
- Add a Slack/Discord notification step after upload.
- Rotate `assets/background.mp4` across multiple visuals per song.
