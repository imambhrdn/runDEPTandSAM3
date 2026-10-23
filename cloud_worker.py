"""Run one controller job; never receive the Vast.ai account API key."""
import hashlib
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
import threading
import time

import requests

BASE = os.environ['CONTROLLER_URL'].rstrip('/') + '/worker/' + os.environ['CONTROLLER_JOB']
TOKEN = os.environ['WORKER_TOKEN']


def call(method, path, **kwargs):
    # Idempotent callback operations may be retried; instance creation is controller-only.
    last = None
    extra_headers = kwargs.pop('extra_headers', {})
    for attempt in range(5):
        try:
            # Re-open/re-read streamed bodies on retries in upload(), not here.
            response = requests.request(method, BASE + path,
                                        headers={'Authorization': 'Bearer ' + TOKEN,
                                                 **extra_headers},
                                        timeout=(15, 180), **kwargs)
            if response.status_code in {401, 403, 409}:
                raise RuntimeError('Controller menolak job yang tidak aktif.')
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last = type(exc).__name__
            if attempt < 4:
                time.sleep(min(2 ** attempt, 10))
    raise RuntimeError('Koneksi controller gagal: ' + str(last))


def upload(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    for attempt in range(5):
        try:
            with path.open('rb') as source:
                response = requests.put(BASE + '/artifacts/' + path.name, data=source,
                                        headers={'Authorization': 'Bearer ' + TOKEN,
                                                 'X-SHA256': digest.hexdigest()}, timeout=(15, 240))
            if response.status_code in {401, 403, 409}:
                raise RuntimeError('Job sudah tidak aktif.')
            response.raise_for_status()
            if response.json()['sha256'] != digest.hexdigest():
                raise RuntimeError('Checksum balasan controller tidak sesuai.')
            return
        except requests.RequestException:
            if attempt == 4:
                raise RuntimeError('Unggah hasil ke VPS gagal.') from None
            time.sleep(5)


def main():
    import app as worker

    task = call('GET', '/task').json()
    root = worker.ROOT / 'controller-job'
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    source = root / task['source']
    response = call('GET', '/source', stream=True)
    size = 0
    try:
        with source.open('wb') as target:
            for chunk in response.iter_content(1024 * 1024):
                size += len(chunk)
                if size > task['max_bytes']:
                    raise RuntimeError('Video melebihi batas ukuran.')
                target.write(chunk)
    finally:
        response.close()
    worker.JOBS['controller-job'] = {'state': 'queued', 'stage': 'Mengekstrak frame asli'}
    stop = threading.Event()

    def heartbeat():
        while not stop.is_set():
            try:
                call('POST', '/heartbeat', json={'stage': worker.JOBS['controller-job'].get('stage', '')})
            except Exception:
                pass  # Controller deadline still bounds the lease if connection fails.
            stop.wait(15)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        files = worker.pc.extract_frames(source, root / 'frames', worker.FPS, worker.WIDTH)
        if len(files) > worker.FPS * 31:
            raise RuntimeError('Video terlalu panjang.')
        worker.run_job('controller-job', worker.Params(**task['params']))
        result = worker.JOBS['controller-job']
        if result['state'] != 'done':
            raise RuntimeError(result.get('error', 'Pemrosesan gagal.'))
        result['stage'] = 'Mengirim hasil ke VPS'
        for name in ['composite.mp4', 'mask_preview.mp4', 'depth_preview.mp4']:
            p = root / name
            if p.exists():
                upload(p)
        if task['params']['face_guidance']:
            upload(root / 'face-tracking.json')
        call('POST', '/complete', json={})
    finally:
        stop.set()
        thread.join(timeout=1)


def batch():
    after = None
    while True:
        task = call('POST', '/next', json={'after': after}).json()
        if task.get('wait'):
            time.sleep(5)
            continue
        if task['done']:
            return
        # Isolate each job's heartbeat, CUDA state, and failures; reuse installed models on disk.
        env = {**os.environ, 'WORKER_BATCH': '0', 'CONTROLLER_JOB': task['id'], 'WORKER_TOKEN': task['token']}
        process = subprocess.Popen([sys.executable, __file__], env=env, start_new_session=True)
        try:
            while True:
                try:
                    process.wait(timeout=5)
                    break
                except subprocess.TimeoutExpired:
                    # Stop promptly on cancellation or a lost controller, not after long artifact retry timeouts.
                    response = requests.get(BASE + '/lease', headers={'Authorization': 'Bearer ' + TOKEN}, timeout=10)
                    response.raise_for_status()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        after = task['id']


if __name__ == '__main__':
    try:
        batch() if os.getenv('WORKER_BATCH') == '1' else main()
    except Exception as exc:
        try:
            call('POST', '/failed', json={'error': str(exc)[:300], 'batch_abort': os.getenv('WORKER_BATCH') == '1'})
        except Exception:
            pass
        raise
