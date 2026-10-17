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

def get_client() -> replicate.Client:
    token = os.environ.get("REPLICATE_API_TOKEN")
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN not set")
    return replicate.Client(api_token=token)

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
