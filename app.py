"""Web UI: unggah klip, klik orangnya, proses depth + SAM 2 di GPU, unduh composite."""
import base64
import os
import re
import secrets
import shutil
import threading
import uuid
from pathlib import Path

import cv2
import numpy as np
import torch
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import prep_composite as pc

ROOT = Path(os.environ.get("RUNS_DIR", "runs")).resolve()
ROOT.mkdir(exist_ok=True)
USER = os.environ.get("APP_USER", "admin")
PASSWORD = os.environ.get("APP_PASSWORD", "")
FPS, WIDTH = 24, 960
DEPTH_MODEL = os.environ.get("DEPTH_MODEL", "depth-anything/Depth-Anything-V2-Large-hf")
SAM_CFG = os.environ.get("SAM_CFG", "configs/sam2.1/sam2.1_hiera_l.yaml")
SAM_CKPT = os.environ.get("SAM_CKPT", "checkpoints/sam2.1_hiera_large.pt")
COLORMAPS = {"inferno", "turbo", "viridis", "jet"}
EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
CUDA = torch.cuda.is_available()

JOBS = {}
GPU_LOCK = threading.Lock()
app = FastAPI()

if not PASSWORD:
    print("PERINGATAN: APP_PASSWORD kosong, siapa pun yang tahu alamatnya bisa memakai app ini.")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if PASSWORD:
        ok = False
        h = request.headers.get("authorization", "")
        if h.startswith("Basic "):
            try:
                u, _, p = base64.b64decode(h[6:]).decode().partition(":")
                ok = secrets.compare_digest(u, USER) and secrets.compare_digest(p, PASSWORD)
            except Exception:
                pass
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="prep"'})
    return await call_next(request)


class Params(BaseModel):
    x: float
    y: float
    dilate: int = 6
    feather: int = 3
    colormap: str = "inferno"


@app.get("/")
def index():
    here = Path(__file__).parent
    for p in (here / "static" / "index.html", here / "index.html"):
        if p.exists():
            return FileResponse(p)
    raise HTTPException(404, "index.html tidak ditemukan.")


@app.post("/api/upload")
def upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in EXTS:
        raise HTTPException(400, "Format tidak didukung. Pakai MP4, MOV, M4V, WebM, atau MKV.")
    jid = uuid.uuid4().hex[:10]
    d = ROOT / jid
    d.mkdir()
    src = d / f"source{ext}"
    with src.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        files = pc.extract_frames(src, d / "frames", FPS, WIDTH)
    except (Exception, SystemExit):
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, "Video tidak bisa dibaca. Ekspor ulang sebagai MP4 (H.264) lalu coba lagi.")
    h, w = cv2.imread(str(files[0])).shape[:2]
    JOBS[jid] = {"state": "ready", "stage": "", "error": ""}
    return {"id": jid, "width": w, "height": h, "frames": len(files),
            "seconds": round(len(files) / FPS, 1), "frame_url": f"/runs/{jid}/frames/00000.jpg"}


@app.post("/api/jobs/{jid}/start")
def start(jid: str, p: Params):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, "Job tidak ditemukan. Unggah ulang klipnya.")
    if job["state"] not in ("ready", "error", "done"):
        raise HTTPException(409, "Job ini sedang diproses.")
    if p.colormap not in COLORMAPS:
        raise HTTPException(400, "Colormap tidak dikenal.")
    p.dilate = max(0, min(p.dilate, 30))
    p.feather = max(0, min(p.feather, 15))
    job.clear()
    job.update(state="queued", stage="Menunggu GPU", error="")
    threading.Thread(target=run_job, args=(jid, p), daemon=True).start()
    return job


@app.get("/api/jobs/{jid}")
def status(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, "Job tidak ditemukan.")
    return JOBS[jid]


@app.delete("/api/jobs/{jid}")
def delete(jid: str):
    if JOBS.get(jid, {}).get("state") in ("queued", "running"):
        raise HTTPException(409, "Job masih berjalan.")
    JOBS.pop(jid, None)
    if re.fullmatch(r"[0-9a-f]{10}", jid):
        shutil.rmtree(ROOT / jid, ignore_errors=True)
    return {"ok": True}


def run_job(jid: str, p: Params):
    job, d = JOBS[jid], ROOT / jid
    frames_dir = d / "frames"
    try:
        with GPU_LOCK:
            files = sorted(frames_dir.glob("*.jpg"))
            job.update(state="running", stage="Depth (1 dari 4)")
            depth = pc.compute_depth(files, DEPTH_MODEL, 0 if CUDA else -1)
            job["stage"] = "Mask orang dengan SAM 2 (2 dari 4)"
            masks = pc.compute_masks(frames_dir, len(files), [p.x, p.y], SAM_CFG, SAM_CKPT,
                                     "cuda" if CUDA else "cpu")
            job["stage"] = "Menempel depth ke area orang (3 dari 4)"
            comp = d / "composite_png"
            pc.composite(files, depth, masks, p.colormap, p.dilate, p.feather, comp)
            job["stage"] = "Menyimpan video (4 dari 4)"
            pc.encode(comp, FPS, d / "composite.mp4")
            mdir = d / "mask_png"
            mdir.mkdir(exist_ok=True)
            blank = np.zeros(depth[0].shape, np.uint8)
            for i, m in enumerate(masks):
                cv2.imwrite(str(mdir / f"{i:05d}.png"), blank if m is None else m)
            pc.run(["ffmpeg", "-y", "-framerate", str(FPS), "-i", str(mdir / "%05d.png"),
                    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / "mask_preview.mp4")])
            shutil.rmtree(comp, ignore_errors=True)
            shutil.rmtree(mdir, ignore_errors=True)
            del depth, masks
            if CUDA:
                torch.cuda.empty_cache()
        job.update(state="done", stage="Selesai",
                   composite=f"/runs/{jid}/composite.mp4", mask=f"/runs/{jid}/mask_preview.mp4")
    except (Exception, SystemExit) as e:
        job.update(state="error", stage="", error=f"{type(e).__name__}: {e}")


app.mount("/runs", StaticFiles(directory=ROOT), name="runs")
