# Songs with Anil — Automated Lofi Video Pipeline (v2)

Automatically picks the next unpublished song from `songs/`, generates a
lofi video, uploads it to YouTube with a custom thumbnail and SEO
metadata, and tracks everything in an upload history — so nothing gets
uploaded twice. Runs on a schedule (or manually) via GitHub Actions.

## What's new in v2

- ✅ Multiple-song queue — drop as many tracks as you want in `songs/`
- ✅ Duplicate-upload protection via `data/upload_history.json`
- ✅ Upload history / status tracking (success + failed runs both logged)
- ✅ Automatic thumbnail generation (video frame + title overlay)
- ✅ Per-song title/description/tags via `songs/metadata.json`
- ✅ Optional YouTube playlist assignment
- ✅ Automatic retry with backoff on auth/upload calls
- ✅ History file auto-committed back to the repo after each run

## Repository structure

```
.
├── .github/workflows/auto_upload.yml
├── assets/
│   └── background.mp4          # shared looping visual (add this)
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

- `assets/background.mp4` — one shared looping visual.
- `songs/*.mp3` (or `.wav` / `.m4a`) — as many tracks as you like. The
  script always picks the **first alphabetically that hasn't been
  uploaded yet**, so name them `01_song.mp3`, `02_song.mp3`, etc. if you
  want a specific order.
- Optionally add entries to `songs/metadata.json`, keyed by filename
  *without* extension, to set a custom title/description/tags/playlist
  for a specific song. Anything you leave out falls back to an
  auto-generated lofi template.

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
