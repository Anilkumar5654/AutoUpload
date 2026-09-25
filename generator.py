"""
generator.py

Automated pipeline for "Songs with Anil":
  1. Merge a looping background video with a background audio track,
     matching the final video duration to the audio duration.
  2. Authenticate with the YouTube Data API v3 using a stored OAuth2
     refresh token (no browser interaction required — suitable for
     headless GitHub Actions runs).
  3. Upload the resulting video with SEO-friendly metadata.

Environment variables required (set as GitHub Secrets):
  YOUTUBE_CLIENT_ID
  YOUTUBE_CLIENT_SECRET
  YOUTUBE_REFRESH_TOKEN

Optional environment variables:
  PRIVACY_STATUS        ("unlisted" | "public" | "private") default: "unlisted"
  BACKGROUND_VIDEO_PATH  default: "assets/background.mp4"
  BACKGROUND_AUDIO_PATH  default: "assets/background_audio.mp3"
  OUTPUT_DIR             default: "output"
"""

import os
import sys
import random
import logging
import datetime
from pathlib import Path

from moviepy.editor import VideoFileClip, AudioFileClip, vfx

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("songs_with_anil")

YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"

BACKGROUND_VIDEO_PATH = os.environ.get("BACKGROUND_VIDEO_PATH", "assets/background.mp4")
BACKGROUND_AUDIO_PATH = os.environ.get("BACKGROUND_AUDIO_PATH", "assets/background_audio.mp3")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
PRIVACY_STATUS = os.environ.get("PRIVACY_STATUS", "unlisted")

CHANNEL_NAME = "Songs with Anil"

# Pool of SEO-oriented title/description templates. A new one is chosen at
# random each run so repeated uploads don't look identical.
TITLE_TEMPLATES = [
    "Lofi Beats to Relax / Study to 🎧 | {channel}",
    "Chill Lofi Mix for Deep Focus & Study 📚 | {channel}",
    "Relaxing Lofi Music for Sleep & Study 🌙 | {channel}",
    "Lofi Hip Hop Radio - Beats to Chill / Relax to | {channel}",
    "Cozy Lofi Beats for Work & Study Sessions ☕ | {channel}",
]

DESCRIPTION_TEMPLATE = """\
🎶 {title}

A relaxing lofi mix perfect for studying, working, reading, or unwinding \
after a long day. Sit back, press play, and let the beats carry you \
through a calm and focused session.

🔔 Subscribe to {channel} for new lofi uploads every week!

⏰ Uploaded: {date}

#lofi #lofihiphop #studymusic #relaxingmusic #chillbeats #focusmusic
"""

TAGS = [
    "lofi",
    "lofi hip hop",
    "study music",
    "relaxing music",
    "chill beats",
    "focus music",
    "sleep music",
    "lofi beats",
    "chillhop",
    "background music",
]

CATEGORY_ID = "10"  # YouTube category: Music


# --------------------------------------------------------------------------
# Step 1: Video generation
# --------------------------------------------------------------------------

def build_video(
    video_path: str = BACKGROUND_VIDEO_PATH,
    audio_path: str = BACKGROUND_AUDIO_PATH,
    output_dir: str = OUTPUT_DIR,
) -> str:
    """
    Merge a background video with a background audio track.

    The video is looped (or trimmed) so its length exactly matches the
    audio track's duration. Returns the path to the rendered .mp4 file.
    """
    video_file = Path(video_path)
    audio_file = Path(audio_path)

    if not video_file.exists():
        raise FileNotFoundError(
            f"Background video not found at '{video_path}'. "
            "Add a video to assets/background.mp4 (or set BACKGROUND_VIDEO_PATH)."
        )
    if not audio_file.exists():
        raise FileNotFoundError(
            f"Background audio not found at '{audio_path}'. "
            "Add a track to assets/background_audio.mp3 (or set BACKGROUND_AUDIO_PATH)."
        )

    log.info("Loading audio track: %s", audio_path)
    audio_clip = AudioFileClip(str(audio_file))

    log.info("Loading background video: %s", video_path)
    video_clip = VideoFileClip(str(video_file))

    # Loop the video if it's shorter than the audio; trim it if longer.
    if video_clip.duration < audio_clip.duration:
        log.info(
            "Video (%.1fs) shorter than audio (%.1fs) — looping video.",
            video_clip.duration,
            audio_clip.duration,
        )
        video_clip = video_clip.fx(vfx.loop, duration=audio_clip.duration)
    else:
        log.info(
            "Video (%.1fs) longer than or equal to audio (%.1fs) — trimming video.",
            video_clip.duration,
            audio_clip.duration,
        )
        video_clip = video_clip.subclip(0, audio_clip.duration)

    final_clip = video_clip.set_audio(audio_clip)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_path = str(Path(output_dir) / f"lofi_{timestamp}.mp4")

    log.info("Rendering final video to: %s", output_path)
    final_clip.write_videofile(
        output_path,
        codec="libx264",
        audio_codec="aac",
        fps=24,
        preset="medium",
        threads=4,
        logger=None,  # suppress moviepy's own progress bar in CI logs
    )

    # Free resources
    video_clip.close()
    audio_clip.close()
    final_clip.close()

    return output_path


# --------------------------------------------------------------------------
# Step 2: YouTube authentication
# --------------------------------------------------------------------------

def get_authenticated_service():
    """
    Build an authenticated YouTube API client using a long-lived OAuth2
    refresh token. No browser / local server flow is needed, so this works
    unattended inside GitHub Actions.
    """
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")

    missing = [
        name
        for name, value in [
            ("YOUTUBE_CLIENT_ID", client_id),
            ("YOUTUBE_CLIENT_SECRET", client_secret),
            ("YOUTUBE_REFRESH_TOKEN", refresh_token),
        ]
        if not value
    ]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Set these as GitHub Actions secrets (see README.md)."
        )

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=[YOUTUBE_UPLOAD_SCOPE],
    )

    log.info("Refreshing YouTube OAuth2 access token...")
    credentials.refresh(Request())

    return build(
        YOUTUBE_API_SERVICE_NAME,
        YOUTUBE_API_VERSION,
        credentials=credentials,
    )


# --------------------------------------------------------------------------
# Step 3: Metadata generation
# --------------------------------------------------------------------------

def generate_metadata() -> dict:
    """Build a randomized-but-consistent SEO title/description/tags set."""
    title_template = random.choice(TITLE_TEMPLATES)
    title = title_template.format(channel=CHANNEL_NAME)

    description = DESCRIPTION_TEMPLATE.format(
        title=title,
        channel=CHANNEL_NAME,
        date=datetime.datetime.utcnow().strftime("%Y-%m-%d"),
    )

    return {
        "title": title,
        "description": description,
        "tags": TAGS,
        "categoryId": CATEGORY_ID,
    }


# --------------------------------------------------------------------------
# Step 4: Upload
# --------------------------------------------------------------------------

def upload_video(youtube, file_path: str, metadata: dict, privacy_status: str = PRIVACY_STATUS) -> str:
    """
    Upload the given video file to YouTube using resumable upload.
    Returns the resulting video ID.
    """
    if privacy_status not in ("public", "unlisted", "private"):
        log.warning("Unrecognized privacy_status '%s' — defaulting to 'unlisted'.", privacy_status)
        privacy_status = "unlisted"

    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata["tags"],
            "categoryId": metadata["categoryId"],
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(file_path, chunksize=-1, resumable=True, mimetype="video/mp4")

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    log.info("Starting upload: '%s' (privacy=%s)", metadata["title"], privacy_status)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info("Upload progress: %d%%", int(status.progress() * 100))

    video_id = response["id"]
    log.info("Upload complete. Video ID: %s — https://youtu.be/%s", video_id, video_id)
    return video_id


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    try:
        video_path = build_video()
    except FileNotFoundError as exc:
        log.error(str(exc))
        sys.exit(1)

    try:
        youtube = get_authenticated_service()
    except EnvironmentError as exc:
        log.error(str(exc))
        sys.exit(1)

    metadata = generate_metadata()

    try:
        upload_video(youtube, video_path, metadata, privacy_status=PRIVACY_STATUS)
    except HttpError as exc:
        log.error("YouTube API error during upload: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
