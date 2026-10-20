"""Authenticated VPS UI and token-scoped outbound-only GPU worker callbacks."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .core import Engine, Settings, Store, Vast

ARTIFACTS = {'composite.mp4', 'mask_preview.mp4', 'face-tracking.json'}


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                             'format=duration:stream=codec_type,width,height',
                             '-of', 'json', str(path)], capture_output=True, timeout=30, check=True)
    data = json.loads(result.stdout)
    videos = [s for s in data['streams'] if s.get('codec_type') == 'video']
    if not videos or not 0 < float(data['format']['duration']) <= 31:
        raise ValueError('Video harus memiliki durasi maksimal 30 detik.')
    return data


def prepare_source(source, frame):
    data = probe(source)
    if float(data['format']['duration']) > 30:
        raise ValueError('Potong video menjadi maksimal 30 detik.')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-an', '-vf',
                    "fps=24,scale='min(960,iw)':-2", '-frames:v', '1', str(frame)],
                   check=True, capture_output=True, timeout=60)
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height',
                             '-of', 'json', str(frame)], check=True, capture_output=True, timeout=10)
    shape = json.loads(result.stdout)['streams'][0]
    return dict(width=shape['width'], height=shape['height'],
                seconds=round(float(data['format']['duration']), 2))


class Params(BaseModel):
    x: float = Field(ge=0, allow_inf_nan=False)
    y: float = Field(ge=0, allow_inf_nan=False)
    face_guidance: bool = True
    dilate: int = Field(default=6, ge=0, le=30)
    feather: int = Field(default=3, ge=0, le=15)
    colormap: str = Field(default='inferno', pattern='^(inferno|turbo|viridis|jet)$')


def create_app(settings=None, vast=None, run_scheduler=True):
    settings = settings or Settings.from_env()
    store = Store(settings.data)
    engine = Engine(store, vast or Vast(settings), settings)
    stop = threading.Event()

    def schedule():
        while not stop.is_set():
            engine.tick()
            stop.wait(5)

    @asynccontextmanager
    async def lifespan(app):
        thread = None
        if run_scheduler:
            thread = threading.Thread(target=schedule, daemon=True)
            thread.start()
        yield
        stop.set()
        if thread:
            await asyncio.to_thread(thread.join, 45)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.engine = store, engine

    @app.middleware('http')
    async def authenticate(request, call_next):
        if not request.url.path.startswith('/worker/'):
            ok = False
            header = request.headers.get('authorization', '')
            if header.startswith('Basic '):
                try:
                    user, _, password = base64.b64decode(header[6:], validate=True).decode().partition(':')
                    ok = secrets.compare_digest(user, 'admin') and secrets.compare_digest(
                        password.encode(), settings.password.encode())
                except (ValueError, UnicodeError):
                    pass
            if not ok:
                return JSONResponse({'detail': 'Login diperlukan'}, status_code=401,
                                    headers={'WWW-Authenticate': 'Basic realm="GPU Controller"'})
            if request.method not in {'GET', 'HEAD'} and request.headers.get('x-controller') != '1':
                return JSONResponse({'detail': 'Header permintaan tidak valid'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    def job_or_404(jid):
        if not re.fullmatch(r'[0-9a-f]{24}', jid):
            raise HTTPException(404)
        job = store.get(jid)
        if not job:
            raise HTTPException(404)
        return job

    def worker_job(jid, request):
        job = job_or_404(jid)
        expected = 'Bearer ' + job['token']
        if not secrets.compare_digest(request.headers.get('authorization', '').encode(), expected.encode()):
            raise HTTPException(403)
        return job

    def public(job):
        keys = ('id', 'state', 'stage', 'created', 'width', 'height', 'seconds', 'error',
                'scheduler_error', 'offer_price', 'gpu_name', 'deadline', 'warning', 'instance_ids')
        result = {k: job[k] for k in keys if k in job}
        result['artifacts'] = {name: f'/api/jobs/{job["id"]}/files/{name}'
                               for name in job['artifacts']}
        result['frame_url'] = f'/api/jobs/{job["id"]}/frame'
        return result

    async def save_stream(request, target, limit):
        total, digest = 0, hashlib.sha256()
        try:
            with target.open('xb') as output:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > limit:
                        raise HTTPException(413, 'File melebihi batas ukuran.')
                    output.write(chunk)
                    digest.update(chunk)
            if not total:
                raise HTTPException(400, 'File kosong.')
            return dict(bytes=total, sha256=digest.hexdigest())
        except BaseException:
            target.unlink(missing_ok=True)
            raise

    @app.get('/')
    def index():
        return FileResponse(Path(__file__).parent / 'static/index.html')

    @app.get('/api/config')
    def config():
        return {'machine_id': settings.machine_id, 'max_price': settings.max_price, 'min_vram': settings.min_vram,
                'max_minutes': settings.max_minutes, 'max_upload_mb': settings.max_upload_mb}

    @app.get('/api/jobs')
    def jobs():
        return [public(job) for job in store.all()]

    @app.get('/api/jobs/{jid}')
    def status(jid: str):
        return public(job_or_404(jid))

    @app.post('/api/upload')
    async def upload(request: Request):
        ext = request.headers.get('x-video-ext', '').lower()
        if ext not in {'.mp4', '.mov', '.m4v', '.webm', '.mkv'}:
            raise HTTPException(400, 'Format video tidak didukung.')
        temporary = Path(tempfile.mkdtemp(dir=settings.data))
        try:
            source, frame = temporary / ('source' + ext), temporary / 'first.jpg'
            await save_stream(request, source, settings.max_upload_mb * 1024 ** 2)
            try:
                metadata = await run_in_threadpool(prepare_source, source, frame)
            except (ValueError, subprocess.SubprocessError, KeyError) as exc:
                raise HTTPException(400, 'Video tidak valid atau lebih dari 30 detik.') from exc
            job = store.create(source=source.name, **metadata)
            temporary.rename(settings.data / job['id'])
            return public(job)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    @app.get('/api/jobs/{jid}/frame')
    def frame(jid: str):
        job_or_404(jid)
        return FileResponse(settings.data / jid / 'first.jpg')

    @app.post('/api/jobs/{jid}/start')
    def start(jid: str, params: Params):
        job = job_or_404(jid)
        if params.x >= job['width'] or params.y >= job['height']:
            raise HTTPException(400, 'Titik berada di luar frame.')
        try:
            return public(store.start(jid, params.model_dump()))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post('/api/jobs/{jid}/cancel')
    def cancel(jid: str):
        with store.lock:
            job = job_or_404(jid)
            if job['state'] == 'ready':
                return public(store.update(jid, state='cancelled', stage='Dibatalkan'))
            if job['state'] in {'done', 'failed', 'cancelled', 'cleanup'}:
                return public(job)
            if job['state'] == 'queued':
                return public(store.update(jid, state='cancelled', stage='Dibatalkan'))
            return public(store.update(jid, cancel_requested=True, stage='Pembatalan diminta'))

    @app.get('/api/jobs/{jid}/files/{name}')
    def artifact(jid: str, name: str):
        job = job_or_404(jid)
        if name not in job['artifacts'] or name not in ARTIFACTS:
            raise HTTPException(404)
        return FileResponse(settings.data / jid / name, filename=name)

    @app.get('/worker/{jid}/task')
    def task(jid: str, request: Request):
        job = worker_job(jid, request)
        if job.get('cancel_requested') or job['state'] not in {'creating', 'booting', 'running'}:
            raise HTTPException(409, 'Job tidak aktif.')
        return {'params': job['params'], 'source': job['source'],
                'max_bytes': settings.max_upload_mb * 1024 ** 2}

    @app.get('/worker/{jid}/source')
    def source(jid: str, request: Request):
        job = worker_job(jid, request)
        return FileResponse(settings.data / jid / job['source'])

    @app.post('/worker/{jid}/heartbeat')
    async def heartbeat(jid: str, request: Request):
        worker_job(jid, request)
        body = await request.json()
        with store.lock:
            job = job_or_404(jid)
            if job.get('cancel_requested') or job['state'] not in {'creating', 'booting', 'running'}:
                raise HTTPException(409, 'Job tidak aktif.')
            store.update(jid, state='running' if job['state'] != 'creating' else 'creating',
                         stage=str(body.get('stage', 'Memproses video'))[:200])
        return {'ok': True}

    @app.put('/worker/{jid}/artifacts/{name}')
    async def receive(jid: str, name: str, request: Request):
        job = worker_job(jid, request)
        if name not in ARTIFACTS:
            raise HTTPException(400)
        if job['state'] not in {'booting', 'running'} or job.get('cancel_requested'):
            raise HTTPException(409)
        temporary = settings.data / jid / (name + '.' + secrets.token_hex(6) + '.part')
        try:
            info = await save_stream(request, temporary, settings.max_upload_mb * 1024 ** 2)
            if not secrets.compare_digest(info['sha256'], request.headers.get('x-sha256', '')):
                raise HTTPException(400, 'Checksum hasil tidak sesuai.')
            try:
                if name.endswith('.mp4'):
                    metadata = await run_in_threadpool(probe, temporary)
                    duration = float(metadata['format']['duration'])
                    video = next(s for s in metadata['streams'] if s.get('codec_type') == 'video')
                    if (abs(duration - job['seconds']) > .2 or video['width'] != job['width']
                            or video['height'] != job['height']):
                        raise ValueError('Durasi/resolusi hasil berbeda dari sumber.')
                    await run_in_threadpool(subprocess.run,
                        ['ffmpeg', '-v', 'error', '-xerror', '-i', str(temporary), '-f', 'null', '-'],
                        check=True, capture_output=True, timeout=120)
                else:
                    report = json.loads(temporary.read_text())
                    if not isinstance(report.get('missing_frames'), list):
                        raise ValueError('Invalid tracking report')
            except (ValueError, subprocess.SubprocessError, KeyError) as exc:
                raise HTTPException(400, 'Hasil tidak valid atau video rusak.') from exc
            with store.lock:
                current = job_or_404(jid)
                if current['state'] not in {'booting', 'running'} or current.get('cancel_requested'):
                    raise HTTPException(409)
                temporary.replace(settings.data / jid / name)
                store.update(jid, artifacts={**current['artifacts'], name: info})
            return {'ok': True, **info}
        finally:
            temporary.unlink(missing_ok=True)

    @app.post('/worker/{jid}/complete')
    async def complete(jid: str, request: Request):
        worker_job(jid, request)
        with store.lock:
            job = job_or_404(jid)
            if job['state'] in {'cleanup', 'done'} and job.get('outcome') == 'done':
                return {'ok': True}
            if job['state'] not in {'booting', 'running'} or job.get('cancel_requested'):
                raise HTTPException(409)
            required = {'composite.mp4', 'mask_preview.mp4'}
            if job['params']['face_guidance']:
                required.add('face-tracking.json')
            if not required.issubset(job['artifacts']):
                raise HTTPException(409, 'Hasil belum lengkap; GPU belum dihapus.')
            warning = ''
            if 'face-tracking.json' in required:
                report = json.loads((settings.data / jid / 'face-tracking.json').read_text())
                missing = len(report['missing_frames'])
                if missing:
                    warning = f'Wajah hilang pada {missing} frame. Periksa seluruh hasil.'
            store.update(jid, state='cleanup', outcome='done', warning=warning,
                         stage='Hasil tersimpan di VPS; menghapus GPU')
        return {'ok': True}

    @app.post('/worker/{jid}/failed')
    async def failed(jid: str, request: Request):
        worker_job(jid, request)
        body = await request.json()
        with store.lock:
            job = job_or_404(jid)
            if job['state'] == 'creating':
                store.update(jid, worker_failed=True, error=str(body.get('error', 'Worker gagal'))[:300])
            elif job['state'] in {'booting', 'running'}:
                store.update(jid, state='cleanup', outcome='failed',
                             error=str(body.get('error', 'Worker gagal'))[:300], stage='Menghapus GPU')
        return {'ok': True}

    app.mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static')
    return app
