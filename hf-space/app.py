"""Frame Lab worker host for Hugging Face Spaces: claim → run cloud_worker.py → upload back."""
import os
import subprocess
import threading
import uuid

import requests
from fastapi import FastAPI, HTTPException

WORKER_DIR = os.getenv('WORKER_DIR', '/app/worker')
MODELS = os.getenv('MODELS_DIR', '/models')

app = FastAPI()
busy = threading.Lock()


def _launch(env, cwd):
    return subprocess.run(['python', '-u', 'cloud_worker.py'], cwd=cwd, env=env,
                          capture_output=True, text=True)


# ZeroGPU: opt-in with SPACES_USE_GPU=1; spaces.GPU is effect-free on normal hardware.
try:
    import spaces
    if os.getenv('SPACES_USE_GPU', '0') == '1':
        _launch = spaces.GPU(duration=600)(_launch)
except Exception:
    pass


def _bg(controller, job_id, token, call_id):
    try:
        try:
            requests.post(controller + f'/worker/{job_id}/claim', json={'call_id': call_id},
                          headers={'Authorization': 'Bearer ' + token}, timeout=(15, 60)).raise_for_status()
        except Exception:
            return  # Job already inactive or claimed elsewhere; controller state stays correct.
        env = {
            **os.environ,
            'WORKER_MODE': '1',
            'CONTROLLER_URL': controller,
            'CONTROLLER_JOB': job_id,
            'WORKER_TOKEN': token,
            'HF_HOME': f'{MODELS}/huggingface',
            'SAM_CKPT': f'{MODELS}/sam2.1_hiera_large.pt',
            'FACE_MODEL': f'{MODELS}/face_landmarker.task',
            'MODAL_MAX_MINUTES': os.getenv('JOB_MINUTES', '20'),
        }
        for key in ('SAM_CKPT', 'FACE_MODEL'):
            if not os.path.exists(env[key]):
                env.pop(key)  # Let the worker download the model itself.
        result = _launch(env, WORKER_DIR)
        if result.returncode != 0:
            tail = (result.stderr or result.stdout or '').strip().splitlines()
            error = tail[-1] if tail else 'Worker gagal tanpa keluaran.'
            # Never echo tokens or long traces back to the controller log.
            requests.post(controller + f'/worker/{job_id}/failed', json={'error': error[:200]},
                          headers={'Authorization': 'Bearer ' + token}, timeout=(15, 60))
    finally:
        busy.release()


@app.post('/run')
def run(body: dict):
    controller = str(body.get('controller', '')).rstrip('/')
    job_id = str(body.get('job_id', ''))
    token = str(body.get('token', ''))
    if not controller.startswith('https://') or not controller.endswith('/hf-api'):
        raise HTTPException(400, 'controller harus URL base /hf-api VPS.')
    if len(job_id) != 24 or '/' in job_id or not token:
        raise HTTPException(400, 'job_id/token tidak valid.')
    if not busy.acquire(blocking=False):
        raise HTTPException(409, 'Space sedang memproses job lain.')
    threading.Thread(target=_bg, daemon=True,
                     args=(controller, job_id, token, 'hf-' + uuid.uuid4().hex[:12])).start()
    return {'started': True}


@app.get('/health')
def health():
    return {'ok': True}
