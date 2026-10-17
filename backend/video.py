"""Video generation via Replicate API."""
import os
import replicate
from typing import Optional

# Default model: a fast, quality text-to-video model on Replicate.
# Swappable via REPLICATE_MODEL env var.
DEFAULT_MODEL = os.environ.get(
    "REPLICATE_MODEL",
    "stability-ai/stable-video-diffusion",
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

def generate_video(prompt: str, duration: int = 5, aspect: str = "16:9") -> dict:
    """
    Kick off a video generation. Returns the prediction record.
    Replicate runs async; caller polls for completion.
    """
    client = get_client()
    prediction = client.predictions.create(
        model=DEFAULT_MODEL,
        input={
            "prompt": prompt,
            "duration": duration,
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
    prediction = client.predictions.create(
        model=UPSCALE_MODEL,
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

def add_dialogue(video_url: str, text: str) -> dict:
    """
    Two-step: TTS the dialogue text, then lip-sync it onto the video.
    Returns the lip-sync prediction record; caller polls via get_status.
    """
    client = get_client()
    # Step 1: text to speech
    tts = client.predictions.create(
        model=TTS_MODEL,
        input={"prompt": text},
    )
    tts = client.predictions.wait(tts.id)
    if tts.status != "succeeded":
        raise RuntimeError(f"TTS failed: {tts.error}")
    audio_url = tts.output if isinstance(tts.output, str) else (tts.output or [None])[0]
    if not audio_url:
        raise RuntimeError("TTS produced no audio")
    # Step 2: lip-sync audio onto video
    lip = client.predictions.create(
        model=LIPSYNC_MODEL,
        input={"video": video_url, "audio": audio_url},
    )
    return {"id": lip.id, "status": lip.status}
