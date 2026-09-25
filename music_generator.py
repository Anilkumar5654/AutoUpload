"""
music_generator.py

Generates a short original lofi track using Meta's MusicGen (open-source,
free, runs locally — no API key, no cost). Used by generator.py only when
songs/ has no unpublished track left, so the pipeline never runs dry.

Runs on CPU in GitHub Actions (free runners have no GPU), so generation is
slow and tracks are kept short by default — see MUSICGEN_DURATION_SECONDS.
Quality is rougher/more ambient than a service like Suno; it's the
trade-off for zero cost and no external account.
"""

import os
import random
import logging
import datetime
from pathlib import Path

log = logging.getLogger("songs_with_anil.music_generator")

MODEL_NAME = os.environ.get("MUSICGEN_MODEL", "facebook/musicgen-small")
DURATION_SECONDS = int(os.environ.get("MUSICGEN_DURATION_SECONDS", "60"))
# MusicGen's audio codebook runs at ~50 tokens/sec of output audio.
TOKENS_PER_SECOND = 50

PROMPTS = [
    "lofi hip hop beat, mellow piano, soft vinyl crackle, relaxing, slow tempo",
    "chill lofi beat with warm jazzy chords, gentle drums, cozy study music",
    "dreamy lofi instrumental, rain ambience, soft electric piano, nostalgic mood",
    "lofi chillhop beat, mellow guitar loop, dusty drums, late night study vibe",
    "calm lofi beat, soft rhodes piano, tape hiss, slow relaxing groove",
    "peaceful lofi instrumental, gentle bassline, soft hi-hats, sleepy afternoon mood",
]


def generate_track(songs_dir: str = "songs", duration_seconds: int = DURATION_SECONDS) -> Path:
    """
    Generates one lofi track with MusicGen and saves it as a .wav file in
    songs_dir. Returns the path to the new file.

    Heavy imports (torch/transformers) happen inside this function so the
    rest of generator.py stays fast to import when generation isn't needed.
    """
    import torch
    from transformers import AutoProcessor, MusicgenForConditionalGeneration
    from scipy.io import wavfile

    prompt = random.choice(PROMPTS)
    log.info("Loading MusicGen model '%s' (first run downloads ~1-2 GB, cached after)...", MODEL_NAME)
    processor = AutoProcessor.from_pretrained(MODEL_NAME)
    model = MusicgenForConditionalGeneration.from_pretrained(MODEL_NAME)

    log.info("Generating ~%ds lofi track with prompt: '%s'", duration_seconds, prompt)
    inputs = processor(text=[prompt], padding=True, return_tensors="pt")

    max_new_tokens = max(1, int(duration_seconds * TOKENS_PER_SECOND))
    with torch.no_grad():
        audio_values = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=True)

    sampling_rate = model.config.audio_encoder.sampling_rate
    audio_array = audio_values[0, 0].cpu().numpy()

    Path(songs_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_path = Path(songs_dir) / f"musicgen_{timestamp}.wav"

    wavfile.write(str(output_path), sampling_rate, audio_array)
    log.info("Generated track saved to: %s", output_path)

    return output_path
