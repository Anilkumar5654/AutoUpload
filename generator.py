"""
generator.py

Automated multi-song pipeline for "Songs with Anil":

  1. Scan songs/ for audio tracks not yet uploaded (checked against
     data/upload_history.json — duplicate-upload protection).
  2. Pick the next unpublished song.
  3. Merge it with a background video (looping/trimming to match duration).
  4. Generate a thumbnail (frame + title overlay).
  5. Build SEO metadata — from songs/metadata.json if present for that
     song, else from auto-generated templates.
  6. Authenticate with YouTube via OAuth2 refresh token.
  7. Upload video + thumbnail, optionally add to a playlist.
  8. Record the result (success or failure) in upload history.

Environment variables required (GitHub Secrets):
  YOUTUBE_CLIENT_ID
  YOUTUBE_CLIENT_SECRET
  YOUTUBE_REFRESH_TOKEN

Optional:
  PRIVACY_STATUS         default "unlisted"
  SONGS_DIR              default "songs"
  BACKGROUND_VIDEO_PATH  default "assets/background.mp4"
  BACKGROUND_DIR         default "assets/backgrounds" (if present, one file
                          is picked at random per run instead of the single
                          BACKGROUND_VIDEO_PATH — lets you rotate visuals)
  OUTPUT_DIR             default "output"
  HISTORY_PATH           default "data/upload_history.json"
  DEFAULT_PLAYLIST_ID    default "" (no playlist)
"""

import os
import sys
import json
import random
import logging
import hashlib
import traceback
import datetime
from pathlib import Path

from moviepy.editor import VideoFileClip, AudioFileClip, ImageClip, vfx
from PIL import Image, ImageDraw, ImageFont

# MoviePy 1.0.3's resize effect calls the removed PIL.Image.ANTIALIAS
# constant. Pillow is pinned to 9.5.0 in requirements.txt for this reason;
# this patch is a safety net in case a transitive dependency ever pulls in
# a newer Pillow anyway.
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

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

SONGS_DIR = os.environ.get("SONGS_DIR", "songs")
BACKGROUND_VIDEO_PATH = os.environ.get("BACKGROUND_VIDEO_PATH", "assets/background.mp4")
BACKGROUND_DIR = os.environ.get("BACKGROUND_DIR", "assets/backgrounds")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
HISTORY_PATH = os.environ.get("HISTORY_PATH", "data/upload_history.json")
PRIVACY_STATUS = os.environ.get("PRIVACY_STATUS", "unlisted")
DEFAULT_PLAYLIST_ID = os.environ.get("DEFAULT_PLAYLIST_ID", "").strip()

CHANNEL_NAME = "Songs with Anil"
AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a")

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

DEFAULT_TAGS = [
    "lofi", "lofi hip hop", "study music", "relaxing music",
    "chill beats", "focus music", "sleep music", "lofi beats",
    "chillhop", "background music",
]

CATEGORY_ID = "10"  # Music


# --------------------------------------------------------------------------
# Upload history (duplicate protection + status tracking)
# --------------------------------------------------------------------------

def load_history(path: str = HISTORY_PATH) -> list:
    history_file = Path(path)
    if not history_file.exists():
        return []
    try:
        with open(history_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read history file (%s) — starting fresh. %s", path, exc)
        return []


def save_history(history: list, path: str = HISTORY_PATH) -> None:
    history_file = Path(path)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def already_uploaded(history: list) -> set:
    """Filenames that have at least one 'success' record — safe to skip."""
    return {record["song"] for record in history if record.get("status") == "success"}


def already_uploaded_hashes(history: list) -> set:
    """
    Audio content hashes (SHA-256) that have already succeeded. Catches a
    song that was renamed/re-encoded but is the same underlying audio,
    which a filename-only check would miss.
    """
    return {
        record["audio_hash"]
        for record in history
        if record.get("status") == "success" and record.get("audio_hash")
    }


def compute_audio_hash(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """SHA-256 of the file's bytes, used for content-based duplicate detection."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record_result(history: list, song_filename: str, status: str, **extra) -> list:
    entry = {
        "song": song_filename,
        "status": status,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        **extra,
    }
    history.append(entry)
    return history


# --------------------------------------------------------------------------
# Song selection
# --------------------------------------------------------------------------

def pick_next_song(songs_dir: str = SONGS_DIR, history: list = None) -> Path | None:
    """
    Picks the first alphabetical song not yet uploaded — checked by both
    filename AND content hash, so a renamed/re-encoded duplicate of an
    already-uploaded track is still skipped.
    """
    history = history or []
    uploaded_names = already_uploaded(history)
    uploaded_hashes = already_uploaded_hashes(history)

    songs_path = Path(songs_dir)
    if not songs_path.exists():
        songs_path.mkdir(parents=True, exist_ok=True)

    candidates = sorted(
        p for p in songs_path.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    )

    if not candidates:
        return None

    for candidate in candidates:
        if candidate.name in uploaded_names:
            continue
        if uploaded_hashes:
            try:
                if compute_audio_hash(candidate) in uploaded_hashes:
                    log.info("Skipping '%s' — content matches an already-uploaded track (renamed duplicate).", candidate.name)
                    continue
            except OSError as exc:
                log.warning("Could not hash '%s' for duplicate check: %s", candidate.name, exc)
        return candidate

    return None  # everything already uploaded successfully


# --------------------------------------------------------------------------
# Per-song metadata (songs/metadata.json)
# --------------------------------------------------------------------------

def load_song_metadata_overrides(songs_dir: str = SONGS_DIR) -> dict:
    meta_path = Path(songs_dir) / "metadata.json"
    if not meta_path.exists():
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read songs/metadata.json — ignoring. %s", exc)
        return {}


def generate_metadata(song_path: Path, overrides: dict) -> dict:
    """
    Build title/description/tags/playlist for a song.
    overrides is keyed by filename stem, e.g. "song1": {...}.
    Any field not provided falls back to an auto-generated default.
    """
    key = song_path.stem
    custom = overrides.get(key, {})

    title = custom.get("title") or random.choice(TITLE_TEMPLATES).format(channel=CHANNEL_NAME)
    description = custom.get("description") or DESCRIPTION_TEMPLATE.format(
        title=title,
        channel=CHANNEL_NAME,
        date=datetime.datetime.utcnow().strftime("%Y-%m-%d"),
    )
    tags = custom.get("tags") or DEFAULT_TAGS
    playlist_id = custom.get("playlist_id") or DEFAULT_PLAYLIST_ID or None

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "categoryId": CATEGORY_ID,
        "playlist_id": playlist_id,
    }


# --------------------------------------------------------------------------
# Video generation
# --------------------------------------------------------------------------

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def pick_background_video(video_path: str = BACKGROUND_VIDEO_PATH, backgrounds_dir: str = BACKGROUND_DIR) -> Path:
    """
    If assets/backgrounds/ exists and has video OR image files, pick one at
    random so repeated uploads don't all reuse the same visual. A still
    image is automatically turned into a slow-zoom video clip in
    build_video() below. Otherwise falls back to the single
    BACKGROUND_VIDEO_PATH (which must be a video).
    """
    backgrounds_path = Path(backgrounds_dir)
    if backgrounds_path.exists():
        options = sorted(
            p for p in backgrounds_path.iterdir()
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS + IMAGE_EXTENSIONS
        )
        if options:
            chosen = random.choice(options)
            log.info("Picked background '%s' from %s (%d available).", chosen.name, backgrounds_dir, len(options))
            return chosen
    return Path(video_path)


def build_video(audio_path: Path, video_path: str = BACKGROUND_VIDEO_PATH, output_dir: str = OUTPUT_DIR) -> str:
    background_file = pick_background_video(video_path)
    if not background_file.exists():
        raise FileNotFoundError(
            f"No background video/image found. Add a video at '{video_path}', or drop videos "
            f"and/or images into '{BACKGROUND_DIR}/' to rotate between them."
        )
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found at '{audio_path}'.")

    is_image = background_file.suffix.lower() in IMAGE_EXTENSIONS

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_path = str(Path(output_dir) / f"{audio_path.stem}_{timestamp}.mp4")

    audio_clip = None
    video_clip = None
    final_clip = None
    try:
        log.info("Loading audio track: %s", audio_path)
        audio_clip = AudioFileClip(str(audio_path))

        if is_image:
            log.info("Background is a still image (%s) — generating a slow-zoom video for %.1fs.", background_file.name, audio_clip.duration)
            base_clip = ImageClip(str(background_file)).set_duration(audio_clip.duration)
            # Subtle continuous zoom-in ("Ken Burns" effect) so a static
            # image doesn't look like a frozen frame for the whole video.
            duration = max(audio_clip.duration, 1)
            video_clip = base_clip.fx(vfx.resize, lambda t: 1 + 0.04 * (t / duration))
        else:
            log.info("Loading background video: %s", background_file)
            video_clip = VideoFileClip(str(background_file))

            if video_clip.duration < audio_clip.duration:
                log.info("Looping video to match audio duration (%.1fs).", audio_clip.duration)
                video_clip = video_clip.fx(vfx.loop, duration=audio_clip.duration)
            else:
                log.info("Trimming video to match audio duration (%.1fs).", audio_clip.duration)
                video_clip = video_clip.subclip(0, audio_clip.duration)

        final_clip = video_clip.set_audio(audio_clip)

        log.info("Rendering final video to: %s", output_path)
        final_clip.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            fps=24,
            preset="medium",
            threads=4,
            logger=None,
        )
    finally:
        for clip in (final_clip, video_clip, audio_clip):
            if clip is not None:
                try:
                    clip.close()
                except Exception:
                    pass

    return output_path


def generate_thumbnail(video_path: str, title: str, output_dir: str = OUTPUT_DIR) -> str | None:
    """
    Grab a frame from the rendered video and overlay the title text to make
    a simple, consistent thumbnail. Returns the thumbnail path, or None if
    generation fails (upload continues without a custom thumbnail).
    """
    try:
        clip = VideoFileClip(video_path)
        frame_time = min(5, max(0, clip.duration / 2))
        frame = clip.get_frame(frame_time)
        clip.close()

        image = Image.fromarray(frame)
        draw = ImageDraw.Draw(image)

        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", size=60)
        except OSError:
            font = ImageFont.load_default()

        margin = 40
        text = title.split("|")[0].strip()
        # Simple dark bar behind text for readability
        text_bbox = draw.textbbox((0, 0), text, font=font)
        text_h = text_bbox[3] - text_bbox[1]
        bar_top = image.height - text_h - margin * 2
        draw.rectangle([0, bar_top, image.width, image.height], fill=(0, 0, 0, 160))
        draw.text((margin, bar_top + margin // 2), text, font=font, fill=(255, 255, 255))

        thumb_path = str(Path(output_dir) / (Path(video_path).stem + "_thumb.jpg"))
        image.convert("RGB").save(thumb_path, "JPEG", quality=90)
        return thumb_path
    except Exception as exc:
        log.warning("Thumbnail generation failed, continuing without it: %s", exc)
        return None


# --------------------------------------------------------------------------
# YouTube authentication
# --------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def get_authenticated_service():
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")

    missing = [
        name for name, value in [
            ("YOUTUBE_CLIENT_ID", client_id),
            ("YOUTUBE_CLIENT_SECRET", client_secret),
            ("YOUTUBE_REFRESH_TOKEN", refresh_token),
        ] if not value
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

    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, credentials=credentials)


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    retry=retry_if_exception_type(HttpError),
    reraise=True,
)
def upload_video(youtube, file_path: str, metadata: dict, privacy_status: str = PRIVACY_STATUS) -> str:
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
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    log.info("Starting upload: '%s' (privacy=%s)", metadata["title"], privacy_status)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info("Upload progress: %d%%", int(status.progress() * 100))

    video_id = response["id"]
    log.info("Upload complete. Video ID: %s — https://youtu.be/%s", video_id, video_id)
    return video_id


def set_thumbnail(youtube, video_id: str, thumbnail_path: str) -> None:
    if not thumbnail_path:
        return
    try:
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg"),
        ).execute()
        log.info("Thumbnail set for video %s", video_id)
    except HttpError as exc:
        # Custom thumbnails require a phone-verified channel; don't fail the
        # whole run just because this optional step was rejected.
        log.warning("Could not set custom thumbnail (channel may need phone verification): %s", exc)


def add_to_playlist(youtube, video_id: str, playlist_id: str) -> None:
    if not playlist_id:
        return
    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
        log.info("Added video %s to playlist %s", video_id, playlist_id)
    except HttpError as exc:
        log.warning("Could not add video to playlist %s: %s", playlist_id, exc)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    history = load_history()

    try:
        song_path = pick_next_song(history=history)
    except FileNotFoundError as exc:
        log.error(str(exc))
        sys.exit(1)

    if song_path is None:
        log.info("No unpublished songs left in '%s' — generating a new original track with MusicGen.", SONGS_DIR)
        try:
            from music_generator import generate_track
            song_path = generate_track(songs_dir=SONGS_DIR)
        except Exception as exc:
            log.error("Music generation failed: %s", exc)
            sys.exit(1)

    log.info("Selected next song: %s", song_path.name)

    try:
        audio_hash = compute_audio_hash(song_path)
    except OSError as exc:
        log.warning("Could not hash song file: %s", exc)
        audio_hash = None

    overrides = load_song_metadata_overrides()
    metadata = generate_metadata(song_path, overrides)

    try:
        video_path = build_video(song_path)
    except FileNotFoundError as exc:
        log.error(str(exc))
        history = record_result(history, song_path.name, "failed", audio_hash=audio_hash, error=str(exc))
        save_history(history)
        sys.exit(1)

    thumbnail_path = generate_thumbnail(video_path, metadata["title"])

    try:
        youtube = get_authenticated_service()
    except EnvironmentError as exc:
        log.error(str(exc))
        history = record_result(history, song_path.name, "failed", audio_hash=audio_hash, error=str(exc))
        save_history(history)
        sys.exit(1)

    try:
        video_id = upload_video(youtube, video_path, metadata, privacy_status=PRIVACY_STATUS)
    except HttpError as exc:
        log.error("YouTube API error during upload: %s", exc)
        history = record_result(history, song_path.name, "failed", audio_hash=audio_hash, error=str(exc))
        save_history(history)
        sys.exit(1)

    set_thumbnail(youtube, video_id, thumbnail_path)
    add_to_playlist(youtube, video_id, metadata.get("playlist_id"))

    history = record_result(
        history,
        song_path.name,
        "success",
        audio_hash=audio_hash,
        video_id=video_id,
        title=metadata["title"],
        privacy_status=PRIVACY_STATUS,
        url=f"https://youtu.be/{video_id}",
    )
    save_history(history)
    log.info("Done. History saved to %s", HISTORY_PATH)


if __name__ == "__main__":
    # Top-level safety net: ANY unexpected exception still gets logged and
    # exits non-zero (so the Actions run shows red), instead of silently
    # crashing with no trace of what happened.
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log.error("Unhandled exception in generator.py:\n%s", traceback.format_exc())
        sys.exit(1)
