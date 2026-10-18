"""Video generation via Replicate API."""
import os
import replicate
from typing import Optional

# Default model: PixVerse v4.5 — true text-to-video, good quality, ~$0.01/video
# (LTX was cheap but poor quality; MiniMax/Kling were too pricey or image-to-video).
# Swappable via REPLICATE_MODEL env var.
DEFAULT_MODEL = os.environ.get(
    "REPLICATE_MODEL",
    "pixverse/pixverse-v4.5",
)

# Video upscaler for enhancing generated clips.
# ByteDance vCube upscaler with 'aigc' scene preset (tuned for AI footage).
# Swappable via UPSCALE_MODEL env var.
UPSCALE_MODEL = os.environ.get(
    "UPSCALE_MODEL",
    "bytedance/vcube-upscaler",
)

# Hermes 3 (Nous Research LLM) for prompt enhancement.
# Swappable via HERMES_MODEL env var.
HERMES_MODEL = os.environ.get(
    "HERMES_MODEL",
    "nousresearch/hermes-3-llama-3.1-405b",
)

ENHANCE_SYSTEM = (
    "You are a cinematic prompt engineer for AI video generation. "
    "Expand the user's short idea into one vivid, detailed video prompt: "
    "describe the subject, setting, lighting, camera movement, mood, and style. "
    "Output ONLY the enhanced prompt, no preamble or explanation. "
    "Keep it under 120 words."
)

def get_client() -> replicate.Client:
    token = os.environ.get("REPLICATE_API_TOKEN")
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN not set")
    return replicate.Client(api_token=token)

_version_cache: dict[str, str] = {}

def _create_prediction(model_name: str, input: dict):
    """
    Create a prediction by resolving the model's latest version first.
    (The installed replicate client requires a version hash, not a name.)
    """
    client = get_client()
    version = _version_cache.get(model_name)
    if not version:
        model = client.models.get(model_name)
        version = model.latest_version.id
        _version_cache[model_name] = version
    return client.predictions.create(version=version, input=input)

def enhance_prompt(raw_prompt: str) -> str:
    """
    Use Hermes 3 to expand a short user prompt into a detailed
    cinematic video prompt. Falls back to the raw prompt on any error.
    """
    try:
        client = get_client()
        output = client.run(
            HERMES_MODEL,
            input={
                "prompt": raw_prompt,
                "system_prompt": ENHANCE_SYSTEM,
                "max_tokens": 300,
                "temperature": 0.7,
            },
        )
        # output may be a list of tokens or a string
        if isinstance(output, list):
            text = "".join(output).strip()
        else:
            text = str(output).strip()
        return text or raw_prompt
    except Exception:
        return raw_prompt

def generate_video(prompt: str, duration: int = 5, aspect: str = "9:16") -> dict:
    """
    Kick off a video generation. Returns the prediction record.
    Replicate runs async; caller polls for completion.
    PixVerse v4.5 takes duration in seconds (5 or 8).
    """
    dur = 8 if duration >= 10 else 5
    client = get_client()
    prediction = _create_prediction(
        DEFAULT_MODEL,
        input={
            "prompt": prompt,
            "duration": dur,
            "aspect_ratio": aspect,
            "quality": "1080p",
        },
    )
    return {"id": prediction.id, "status": prediction.status}

# TTS + lip-sync for adding dialogue to finished videos.
# Swappable via TTS_MODEL / LIPSYNC_MODEL env vars.
TTS_MODEL = os.environ.get(
    "TTS_MODEL",
    "suno-ai/bark",
)
LIPSYNC_MODEL = os.environ.get(
    "LIPSYNC_MODEL",
    "devxpy/cog-wav2lip",
)

def upscale_video(video_url: str, target: str = "2K") -> dict:
    """
    Enhance/upscale a finished video via the ByteDance upscaler.
    Uses the 'aigc' scene preset tuned for AI-generated footage.
    Returns the prediction record; caller polls via get_status.
    """
    client = get_client()
    prediction = _create_prediction(
        UPSCALE_MODEL,
        input={
            "video": video_url,
            "scene": "aigc",
            "resolution": target,
        },
    )
    return {"id": prediction.id, "status": prediction.status}

def get_status(prediction_id: str) -> dict:
    client = get_client()
    p = client.predictions.get(prediction_id)
    out = {
        "id": p.id,
        "status": p.status,
        "output": p.output,
        "error": p.error,
    }
    return out

import subprocess
import tempfile
import urllib.request
import uuid

MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

def combine_videos(video_urls: list[str]) -> str:
    """
    Stitch 2-4 clips end-to-end with ffmpeg (re-encoded to normalize).
    Returns the public /media/ URL path of the combined file.
    """
    if not 2 <= len(video_urls) <= 4:
        raise ValueError("Combine 2 to 4 clips")
    tmpdir = tempfile.mkdtemp()
    parts = []
    try:
        for i, url in enumerate(video_urls):
            p = os.path.join(tmpdir, f"part{i}.mp4")
            urllib.request.urlretrieve(url, p)
            parts.append(p)
        # concat demuxer needs same codecs -> re-encode each to h264/aac first
        normed = []
        for i, p in enumerate(parts):
            n = os.path.join(tmpdir, f"norm{i}.mp4")
            subprocess.run(
                ["ffmpeg", "-y", "-i", p, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-shortest", n],
                check=True, capture_output=True,
            )
            normed.append(n)
        lst = os.path.join(tmpdir, "list.txt")
        with open(lst, "w") as f:
            for n in normed:
                f.write(f"file '{n}'\n")
        out_name = f"combined-{uuid.uuid4().hex[:8]}.mp4"
        out_path = os.path.join(MEDIA_DIR, out_name)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
             "-c", "copy", out_path],
            check=True, capture_output=True,
        )
        return f"/media/{out_name}"
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

TEXT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

def edit_video(video_url: str, edit_prompt: str, duration: int = 5, aspect: str = "9:16") -> dict:
    """
    AI-edit an existing video: extract its first frame and regenerate via
    PixVerse image-to-video with the edit description as the prompt.
    Returns the prediction record (async; caller polls).
    """
    import base64
    dur = 8 if duration >= 10 else 5
    tmpdir = tempfile.mkdtemp()
    try:
        src = os.path.join(tmpdir, "src.mp4")
        urllib.request.urlretrieve(video_url, src)
        frame = os.path.join(tmpdir, "frame.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-vframes", "1", "-q:v", "2", frame],
            check=True, capture_output=True,
        )
        with open(frame, "rb") as f:
            data_uri = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
        prediction = _create_prediction(
            DEFAULT_MODEL,
            input={
                "prompt": edit_prompt,
                "image": data_uri,
                "duration": dur,
                "aspect_ratio": aspect,
                "quality": "1080p",
            },
        )
        return {"id": prediction.id, "status": prediction.status}
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

def add_text_overlay(video_url: str, text: str, position: str = "bottom") -> str:
    """
    Burn text onto a video with ffmpeg drawtext (white with black outline).
    Returns the public /media/ URL path of the new file.
    """
    text = (text or "").strip()[:120]
    if not text:
        raise ValueError("Text is empty")
    safe = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")
    y_expr = {"top": "80", "center": "(h-text_h)/2", "bottom": "h-text_h-80"}.get(position, "h-text_h-80")
    tmpdir = tempfile.mkdtemp()
    try:
        src = os.path.join(tmpdir, "src.mp4")
        urllib.request.urlretrieve(video_url, src)
        out_name = f"text-{uuid.uuid4().hex[:8]}.mp4"
        out_path = os.path.join(MEDIA_DIR, out_name)
        vf = (
            f"drawtext=fontfile={TEXT_FONT}:text='{safe}':fontsize=56:"
            f"fontcolor=white:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y={y_expr}"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-vf", vf,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", out_path],
            check=True, capture_output=True,
        )
        return f"/media/{out_name}"
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

def add_dialogue(video_url: str, text: str) -> dict:
    """
    Two-step: TTS the dialogue text, then lip-sync it onto the video.
    Returns the lip-sync prediction record; caller polls via get_status.
    """
    client = get_client()
    # Step 1: text to speech
    tts = _create_prediction(TTS_MODEL, input={"prompt": text})
    tts = client.predictions.wait(tts.id)
    if tts.status != "succeeded":
        raise RuntimeError(f"TTS failed: {tts.error}")
    audio_url = tts.output if isinstance(tts.output, str) else (tts.output or [None])[0]
    if not audio_url:
        raise RuntimeError("TTS produced no audio")
    # Step 2: lip-sync audio onto video
    lip = _create_prediction(LIPSYNC_MODEL, input={"video": video_url, "audio": audio_url})
    return {"id": lip.id, "status": lip.status}
