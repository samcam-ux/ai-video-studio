"""AI Video Studio — text-to-video web app."""
import os
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import video as video_gen
import billing

APP_URL = os.environ.get("APP_URL", "http://localhost:8000")

app = FastAPI(title="AI Video Studio")

# Serve frontend — resolve relative to this file so it works regardless of WORKDIR
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(_BASE, "frontend")
app.mount("/static", StaticFiles(directory=_FRONTEND), name="static")

# Served combined/stitched videos
_MEDIA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")
os.makedirs(_MEDIA, exist_ok=True)
app.mount("/media", StaticFiles(directory=_MEDIA), name="media")

@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(_FRONTEND, "index.html")) as f:
        return f.read()

@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}

# ---- Auth ----
class RegisterIn(BaseModel):
    email: str
    password: str

@app.post("/api/register")
def register(body: RegisterIn):
    try:
        return auth.register(body.email, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.post("/api/login")
def login(body: RegisterIn):
    try:
        return auth.login(body.email, body.password)
    except ValueError as e:
        raise HTTPException(401, str(e))

def _user_or_401(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Login required")
    user = auth.get_user(authorization[7:])
    if not user:
        raise HTTPException(401, "Invalid session")
    return user

@app.get("/api/me")
def me(authorization: str | None = Header(default=None)):
    user = _user_or_401(authorization)
    return {"email": user["email"], "credits": user["credits"], "videos": user["videos"]}

# ---- Generation ----
class GenIn(BaseModel):
    prompt: str
    duration: int = 5
    enhance: bool = True

@app.post("/api/generate")
def generate(body: GenIn, authorization: str | None = Header(default=None)):
    user = _user_or_401(authorization)
    if not body.prompt.strip():
        raise HTTPException(400, "Prompt is required")
    if not auth.spend_credit(user["email"]):
        raise HTTPException(402, "No credits left — buy more to continue")
    raw = body.prompt.strip()
    final_prompt = video_gen.enhance_prompt(raw) if body.enhance else raw
    try:
        pred = video_gen.generate_video(final_prompt, duration=body.duration)
    except RuntimeError as e:
        # refund the credit if we couldn't even start
        auth.add_credits(user["email"], 1)
        raise HTTPException(500, str(e))
    except Exception as e:
        auth.add_credits(user["email"], 1)
        raise HTTPException(502, f"Video provider error: {e}")
    video = {"prompt": raw, "enhanced_prompt": final_prompt, "prediction_id": pred["id"], "status": pred["status"], "url": None, "cost": 1}
    auth.add_video(user["email"], video)
    return {"prediction_id": pred["id"], "status": pred["status"], "credits": user["credits"]}

@app.get("/api/status/{prediction_id}")
def status(prediction_id: str, authorization: str | None = Header(default=None)):
    user = _user_or_401(authorization)
    try:
        s = video_gen.get_status(prediction_id)
    except Exception as e:
        raise HTTPException(502, f"Video provider error: {e}")
    # update stored video (persisted)
    updates = {"status": s["status"]}
    out = s.get("output")
    if isinstance(out, list) and out:
        updates["url"] = out[0]
    elif isinstance(out, str):
        updates["url"] = out
    auth.update_video(user["email"], prediction_id, updates)
    # refund if the provider failed this generation
    if s["status"] == "failed":
        auth.refund_if_failed(user["email"], prediction_id)
    return s

# ---- Upscale / enhance ----
UPSCALE_COST = 2
DIALOGUE_COST = 2

class UpscaleIn(BaseModel):
    prediction_id: str
    target: str = "2K"

@app.post("/api/upscale")
def upscale(body: UpscaleIn, authorization: str | None = Header(default=None)):
    user = _user_or_401(authorization)
    # find the source video
    src = next((v for v in user["videos"] if v.get("prediction_id") == body.prediction_id and v.get("url")), None)
    if not src:
        raise HTTPException(404, "Finished video not found")
    # charge credits (all-or-nothing)
    charged = 0
    for _ in range(UPSCALE_COST):
        if not auth.spend_credit(user["email"]):
            break
        charged += 1
    if charged < UPSCALE_COST:
        auth.add_credits(user["email"], charged)  # refund partial
        raise HTTPException(402, f"Enhancing costs {UPSCALE_COST} credits")
    try:
        pred = video_gen.upscale_video(src["url"], target=body.target)
    except Exception as e:
        auth.add_credits(user["email"], UPSCALE_COST)
        raise HTTPException(502, f"Upscale provider error: {e}")
    video = {"prompt": f"✨ Enhanced: {src['prompt']}", "prediction_id": pred["id"], "status": pred["status"], "url": None, "enhanced_from": src["prediction_id"], "cost": UPSCALE_COST}
    auth.add_video(user["email"], video)
    return {"prediction_id": pred["id"], "status": pred["status"], "credits": user["credits"]}

# ---- Dialogue (TTS + lip-sync) ----
class DialogueIn(BaseModel):
    prediction_id: str
    text: str

@app.post("/api/dialogue")
def dialogue(body: DialogueIn, authorization: str | None = Header(default=None)):
    user = _user_or_401(authorization)
    if not body.text.strip():
        raise HTTPException(400, "Dialogue text is required")
    if len(body.text) > 300:
        raise HTTPException(400, "Dialogue too long (300 chars max)")
    src = next((v for v in user["videos"] if v.get("prediction_id") == body.prediction_id and v.get("url")), None)
    if not src:
        raise HTTPException(404, "Finished video not found")
    # charge credits (all-or-nothing)
    charged = 0
    for _ in range(DIALOGUE_COST):
        if not auth.spend_credit(user["email"]):
            break
        charged += 1
    if charged < DIALOGUE_COST:
        auth.add_credits(user["email"], charged)  # refund partial
        raise HTTPException(402, f"Dialogue costs {DIALOGUE_COST} credits")
    try:
        pred = video_gen.add_dialogue(src["url"], body.text.strip())
    except Exception as e:
        auth.add_credits(user["email"], DIALOGUE_COST)
        raise HTTPException(502, f"Dialogue provider error: {e}")
    video = {"prompt": f"🗣️ {src['prompt']}", "dialogue": body.text.strip(), "prediction_id": pred["id"], "status": pred["status"], "url": None, "dialogue_from": src["prediction_id"], "cost": DIALOGUE_COST}
    auth.add_video(user["email"], video)
    return {"prediction_id": pred["id"], "status": pred["status"], "credits": user["credits"]}

# ---- Combine clips into longer video ----
COMBINE_COST = 1

class CombineIn(BaseModel):
    prediction_ids: list[str]

@app.post("/api/combine")
def combine(body: CombineIn, authorization: str | None = Header(default=None)):
    user = _user_or_401(authorization)
    if not 2 <= len(body.prediction_ids) <= 4:
        raise HTTPException(400, "Pick 2 to 4 clips to combine")
    clips = []
    for pid in body.prediction_ids:
        v = next((x for x in user["videos"] if x.get("prediction_id") == pid and x.get("url")), None)
        if not v:
            raise HTTPException(404, f"Finished video not found: {pid}")
        clips.append(v)
    if not auth.spend_credit(user["email"]):
        raise HTTPException(402, f"Combining costs {COMBINE_COST} credit")
    try:
        path = video_gen.combine_videos([c["url"] for c in clips])
    except Exception as e:
        auth.add_credits(user["email"], COMBINE_COST)
        raise HTTPException(502, f"Combine failed: {e}")
    url = APP_URL.rstrip("/") + path
    video = {"prompt": f"🎬 Combined ({len(clips)} clips): " + clips[0]["prompt"], "prediction_id": f"combined-{path.split('-')[-1].split('.')[0]}", "status": "succeeded", "url": url, "combined_from": [c["prediction_id"] for c in clips], "cost": COMBINE_COST}
    auth.add_video(user["email"], video)
    return {"url": url, "credits": user["credits"]}

# ---- Billing ----
@app.post("/api/checkout")
def checkout(authorization: str | None = Header(default=None)):
    user = _user_or_401(authorization)
    try:
        url = billing.create_checkout(user["email"], APP_URL)
    except Exception as e:
        raise HTTPException(500, f"Checkout error: {e}")
    return {"url": url}

@app.post("/api/stripe-webhook")
async def webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        result = billing.handle_webhook(payload, sig)
    except Exception as e:
        raise HTTPException(400, f"Webhook error: {e}")
    if result and result["email"]:
        auth.add_credits(result["email"], result["credits"])
    return {"ok": True}
