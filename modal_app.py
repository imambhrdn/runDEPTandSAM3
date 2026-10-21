"""Modal GPU worker: runs cloud_worker.py in an L4 container.

Models are cached in a Modal Volume so they are downloaded only once.
Never deployed or executed without explicit authorization.
"""
from pathlib import Path
import modal

APP_NAME = "frame-lab-modal"
app = modal.App(APP_NAME)

# Volume for caching models across function calls
volume = modal.Volume.from_name("frame-lab-models", create_if_missing=True)
CHECKPOINTS_DIR = "/root/checkpoints"
HF_CACHE_DIR = "/root/.cache/huggingface"

# Base image with system deps and PyTorch
worker_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "wget", "ffmpeg", "ca-certificates")
    .pip_install(
        "torch>=2.5.1",
        "transformers==4.57.6",
        "mediapipe==0.10.21",
        "opencv-python-headless",
        "pillow",
        "numpy",
        "requests>=2.32,<3",
        "fastapi",
        "uvicorn[standard]",
        "python-multipart",
        "huggingface_hub",
    )
    .run_commands(
        # Build SAM 2 without CUDA kernel compilation for portable image builds
        "SAM2_BUILD_CUDA=0 pip install --no-cache-dir 'git+https://github.com/facebookresearch/sam2.git'",
    )
)


@app.function(
    image=worker_image,
    gpu="L4",
    timeout=1800,  # 30 minutes
    volumes={
        CHECKPOINTS_DIR: volume,
        HF_CACHE_DIR: volume,
    },
)
def process_job(controller_url: str, job_id: str, token: str):
    """Entry point called by the controller via Function.spawn()."""
    import os
    import subprocess
    import sys
    import requests

    call_id = modal.current_function_call_id()

    # Step 1: Claim job at controller before starting any work
    claim_url = f"{controller_url.rstrip('/')}/worker/{job_id}/claim"
    response = requests.post(
        claim_url,
        json={"call_id": call_id},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if response.status_code == 409:
        print(f"Job {job_id} sudah dikerjakan eksekusi lain atau dibatalkan.")
        return {"status": "claimed_elsewhere"}
    response.raise_for_status()

    # Step 2: Ensure models are in volume
    ckpt = Path(CHECKPOINTS_DIR)
    sam_pt = ckpt / "sam2.1_hiera_large.pt"
    if not sam_pt.exists():
        print("Mengunduh SAM 2.1 checkpoint ke Volume...")
        import urllib.request
        urllib.request.urlretrieve(
            "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt",
            str(sam_pt),
        )
        volume.commit()

    # Step 3: Run existing cloud_worker.py as subprocess
    env = {
        **os.environ,
        "WORKER_MODE": "1",
        "CONTROLLER_URL": controller_url,
        "CONTROLLER_JOB": job_id,
        "WORKER_TOKEN": token,
        "SAM_CKPT": str(sam_pt),
        "FACE_MODEL": str(ckpt / "face_landmarker.task"),
    }

    # Clone or use local repo directory
    work_dir = Path("/workspace/gpu-worker")
    if not work_dir.exists():
        work_dir.mkdir(parents=True, exist_ok=True)
        # Copy current codebase into workspace
        subprocess.run(
            ["git", "clone", "https://github.com/imambhrdn/runDEPTandSAM3.git", str(work_dir)],
            check=True,
        )

    result = subprocess.run(
        [sys.executable, "cloud_worker.py"],
        cwd=str(work_dir),
        env=env,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"cloud_worker failed with code {result.returncode}:\n{result.stderr}")
        raise RuntimeError(f"Worker failed: {result.stderr[:200]}")

    return {"status": "success", "call_id": call_id}
