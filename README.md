# Songs with Anil — Automated Lofi Video Pipeline

Automatically generates lofi/music videos from a background clip + audio
track and uploads them to YouTube on a schedule, using GitHub Actions.

## Repository structure

```
.
├── .github/workflows/auto_upload.yml   # Scheduled/manual CI pipeline
├── assets/
│   ├── background.mp4                  # Your looping visual (add this)
│   └── background_audio.mp3            # Your music track (add this)
├── generator.py                        # Video build + YouTube upload logic
├── requirements.txt
└── README.md
```

You provide `assets/background.mp4` (a looping visual — e.g. a slow pan,
rain animation, cozy room scene) and `assets/background_audio.mp3` (your
lofi track) for each run. Commit new audio/video before a run, or extend
`generator.py` to pull from a folder of many tracks.

---

## 1. Google Cloud Console setup

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and
   create a new project (or select an existing one).
2. Open **APIs & Services → Library**, search for **YouTube Data API v3**,
   and click **Enable**.
3. Open **APIs & Services → OAuth consent screen**.
   - Choose **External** (unless you have a Google Workspace org).
   - Fill in the required app name, support email, etc.
   - Add your own Google account under **Test users** (required while the
     app is in "Testing" publishing status).
4. Open **APIs & Services → Credentials → Create Credentials → OAuth
   client ID**.
   - Application type: **Desktop app**.
   - Name it something like `songs-with-anil-uploader`.
   - Click **Create**, then note the **Client ID** and **Client Secret**
     (or download the JSON).

---

## 2. Generate a refresh token locally

The refresh token lets GitHub Actions authenticate as you without a
browser. You only need to generate it **once**, on your own machine.

1. Clone this repo locally and install dependencies:

   ```bash
   pip install google-auth-oauthlib google-auth google-api-python-client
   ```

2. Save this as `get_refresh_token.py` in the repo root (temporary,
   local-only — do not commit it):

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

3. Replace `YOUR_CLIENT_ID` / `YOUR_CLIENT_SECRET` with the values from
   step 1.4, then run:

   ```bash
   python get_refresh_token.py
   ```

4. A browser window opens — sign in with the **same Google account that
   owns the "Songs with Anil" YouTube channel**, and approve access.
5. The script prints a refresh token in your terminal. Copy it — you'll
   need it for the next step. Then **delete `get_refresh_token.py`** so it
   never gets committed.

> Note: while your OAuth consent screen is in "Testing" mode, refresh
> tokens can expire after 7 days of inactivity. For a long-running
> production pipeline, submit the app for verification or keep it running
> often enough to stay active — Google's OAuth docs cover the verification
> process in more detail.

---

## 3. Add GitHub repository secrets

In your GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add each of the following:

| Secret name              | Value                                  |
|---------------------------|-----------------------------------------|
| `YOUTUBE_CLIENT_ID`       | Client ID from step 1.4                |
| `YOUTUBE_CLIENT_SECRET`   | Client Secret from step 1.4            |
| `YOUTUBE_REFRESH_TOKEN`   | Refresh token generated in step 2      |

These are injected as environment variables into the workflow — they are
never written to logs or the repository itself.

---

## 4. Add your media assets

Place your files at:

- `assets/background.mp4`
- `assets/background_audio.mp3`

Commit and push them. `generator.py` will loop the video (or trim it) so
its length exactly matches the audio duration.

---

## 5. Run it

- **Manually:** go to the **Actions** tab → **Auto Upload Lofi Video** →
  **Run workflow**. You can choose the privacy status (`unlisted` is the
  default — recommended for a first test run).
- **On schedule:** the workflow in `.github/workflows/auto_upload.yml` is
  set to run every Monday at 09:00 UTC. Edit the `cron` expression to
  change the cadence.

A copy of the generated video is also uploaded as a workflow artifact for
7 days, so you can sanity-check the render even before checking YouTube.

---

## 6. Recommended safety workflow

1. Leave the default privacy status as `unlisted` for your first few runs.
2. Check the video on YouTube (thumbnail, audio sync, metadata).
3. Once you're confident, either switch the workflow's default to
   `public`, or manually flip individual uploads from unlisted to public
   in YouTube Studio.

---

## Customization ideas

- Swap `TITLE_TEMPLATES` / `TAGS` in `generator.py` for your own SEO
  copy, or generate them dynamically from a track list / CSV.
- Point `BACKGROUND_VIDEO_PATH` / `BACKGROUND_AUDIO_PATH` at a rotating
  set of assets (e.g. pick randomly from `assets/videos/` and
  `assets/audio/` each run).
- Add a thumbnail upload step using `youtube.thumbnails().set(...)`.
- Add Slack/Discord/email notification on success or failure via an
  additional workflow step.
