"""Durable, single-GPU scheduler. Only instances created for this app are destroyed."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import sqlite3
import threading
import time

import requests

TERMINAL = {'ready', 'done', 'failed', 'cancelled'}


class VastAPIError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'Vast.ai HTTP {status}')


class RentalRejected(RuntimeError):
    pass


@dataclass
class Settings:
    public_url: str
    api_key: str
    password: str
    worker_ref: str
    data: Path = Path('controller/data')
    machine_id: int = 28726
    max_price: float = .30
    min_vram: int = 16
    max_minutes: int = 45
    disk_gb: int = 30
    max_upload_mb: int = 250
    image: str = 'pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime'

    @classmethod
    def from_env(cls):
        config = cls(
            public_url=os.environ['PUBLIC_URL'].rstrip('/'),
            api_key=os.environ['VAST_API_KEY'], password=os.environ['CONTROLLER_PASSWORD'],
            worker_ref=os.environ['WORKER_REF'],
            data=Path(os.getenv('DATA_DIR', '/data')),
            machine_id=int(os.getenv('VAST_MACHINE_ID', '28726')),
            max_price=float(os.getenv('MAX_PRICE_HOUR', '.30')),
            min_vram=int(os.getenv('MIN_VRAM_GB', '16')),
            max_minutes=int(os.getenv('MAX_RENT_MINUTES', '45')),
            disk_gb=int(os.getenv('DISK_GB', '30')),
            max_upload_mb=int(os.getenv('MAX_UPLOAD_MB', '250')),
            image=os.getenv('VAST_IMAGE', cls.image),
        )
        if not re.fullmatch(r'https://[a-zA-Z0-9.-]+(?::\d+)?', config.public_url):
            raise ValueError('PUBLIC_URL harus berupa origin HTTPS, tanpa path.')
        if not re.fullmatch(r'[0-9a-f]{40}', config.worker_ref):
            raise ValueError('WORKER_REF harus commit Git 40 karakter.')
        if len(config.password) < 16 or not config.api_key:
            raise ValueError('Isi VAST_API_KEY dan password minimal 16 karakter.')
        if not (0 < config.max_price <= 100 and 1 <= config.max_minutes <= 240
                and config.min_vram >= 16 and config.disk_gb >= 20
                and config.max_upload_mb > 0 and config.machine_id > 0):
            raise ValueError('Batas harga/waktu/VRAM/disk/upload tidak valid.')
        config.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        return config


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / 'jobs.sqlite', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.commit()

    def all(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute('SELECT body FROM jobs ORDER BY rowid DESC')]

    def get(self, jid):
        with self.lock:
            row = self.db.execute('SELECT body FROM jobs WHERE id=?', (jid,)).fetchone()
            return json.loads(row[0]) if row else None

    def create(self, **fields):
        with self.lock:
            jid = secrets.token_hex(12)
            job = dict(id=jid, state='ready', stage='Pilih subjek', created=time.time(),
                       token=secrets.token_urlsafe(32), artifacts={}, instance_ids=[], **fields)
            self.db.execute('INSERT INTO jobs VALUES (?,?)', (jid, json.dumps(job)))
            self.db.commit()
            return job

    def update(self, jid, **fields):
        with self.lock:
            job = self.get(jid)
            if job is None:
                raise KeyError(jid)
            job.update(fields)
            self.db.execute('UPDATE jobs SET body=? WHERE id=?', (json.dumps(job), jid))
            self.db.commit()
            return job

    def start(self, jid, params):
        with self.lock:
            job = self.get(jid)
            if not job or job['state'] != 'ready':
                raise ValueError('Job sudah dimulai; unggah klip baru untuk variasi.')
            if any(j['state'] not in TERMINAL for j in self.all()):
                raise ValueError('Tunggu GPU/job sebelumnya selesai dibersihkan.')
            return self.update(jid, state='queued', stage='Mencari GPU', params=params)


class Vast:
    BASE = 'https://console.vast.ai'

    def __init__(self, settings):
        self.settings = settings
        self.http = requests.Session()
        self.http.headers['Authorization'] = 'Bearer ' + settings.api_key

    def request(self, method, path, **kwargs):
        response = self.http.request(method, self.BASE + path, timeout=(10, 30), **kwargs)
        # Do not propagate response bodies or request headers containing credentials.
        if response.status_code == 404:
            return None
        if not response.ok:
            raise VastAPIError(response.status_code)
        return response.json()

    def offers(self):
        s = self.settings
        query = {'verified': {'eq': True}, 'external': {'eq': False},
                 'rentable': {'eq': True}, 'rented': {'eq': False},
                 'machine_id': {'eq': s.machine_id}, 'num_gpus': {'eq': 1}, 'gpu_ram': {'gte': s.min_vram * 1000},
                 'compute_cap': {'gte': 800}, 'cuda_max_good': {'gte': 12.4},
                 'cpu_arch': {'eq': 'amd64'}, 'reliability': {'gte': .98},
                 'inet_down': {'gte': 100}, 'disk_space': {'gte': s.disk_gb},
                 'dph_total': {'lte': s.max_price},
                 'type': 'on-demand', 'allocated_storage': s.disk_gb,
                 'order': [['dph_total', 'asc']], 'limit': 20}
        result = self.request('POST', '/api/v0/bundles/', json=query)
        offers = (result or {}).get('offers', [])
        # Enforce the price cap locally as well; never silently relax it.
        return sorted([o for o in offers if o.get('machine_id') == s.machine_id
                       and 0 < float(o.get('dph_total', float('inf'))) <= s.max_price],
                      key=lambda o: float(o['dph_total']))

    def create(self, offer_id, job):
        s = self.settings
        script = ('set -eu\ncd /workspace\n'
                  '(command -v git >/dev/null && command -v wget >/dev/null) || '
                  '(apt-get update -qq && apt-get install -y -qq git wget ca-certificates)\n'
                  'if [ ! -d gpu-worker/.git ]; then git clone --no-checkout '
                  'https://github.com/imambhrdn/runDEPTandSAM3.git gpu-worker; fi\n'
                  'cd gpu-worker\ngit fetch origin\ngit checkout --detach ' + shlex.quote(s.worker_ref) +
                  '\nexec bash run.sh\n')
        payload = {'client_id': 'me', 'image': s.image, 'disk': s.disk_gb,
                   'label': job['label'], 'runtype': 'ssh', 'onstart': script,
                   'cancel_unavail': True,
                   'env': {'WORKER_MODE': '1', 'CONTROLLER_URL': s.public_url,
                           'CONTROLLER_JOB': job['id'], 'WORKER_TOKEN': job['token']}}
        result = self.request('PUT', f'/api/v0/asks/{int(offer_id)}/', json=payload)
        if result and result.get('success') is False:
            raise RentalRejected('Offer ditolak Vast.ai; tidak ada sewa baru.')
        if not result or not result.get('success') or not result.get('new_contract'):
            raise RuntimeError('Vast.ai tidak mengonfirmasi pembuatan instance.')
        return int(result['new_contract'])

    def instances(self):
        rows, token = [], None
        while True:
            params = {'limit': 100, 'order_by': json.dumps([{'col': 'id', 'dir': 'asc'}])}
            if token:
                params['after_token'] = token
            result = self.request('GET', '/api/v1/instances/', params=params)
            if result is None:
                raise RuntimeError('Endpoint daftar instance Vast.ai tidak tersedia.')
            rows.extend(result.get('instances') or [])
            token = result.get('next_token')
            if not token:
                return rows

    def instance(self, iid):
        result = self.request('GET', f'/api/v0/instances/{int(iid)}/', params={'owner': 'me'})
        return result.get('instances') if result else None

    def destroy(self, iid):
        result = self.request('DELETE', f'/api/v0/instances/{int(iid)}/', json={})
        if result is not None and not result.get('success'):
            raise RuntimeError('Vast.ai belum mengonfirmasi penghapusan instance.')


class Engine:
    def __init__(self, store, vast, settings, clock=time.time):
        self.store, self.vast, self.settings, self.clock = store, vast, settings, clock
        self.tick_lock = threading.Lock()

    def tick(self):
        # Exactly one scheduler in one Uvicorn process; persistent state survives restarts.
        with self.tick_lock:
            for job in self.store.all():
                if job['state'] in TERMINAL:
                    continue
                try:
                    self.step(job)
                except Exception as exc:
                    # Keep uncertain rentals/cleanup active and retry reconciliation, never re-rent.
                    message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                    if job['state'] == 'queued':
                        self.store.update(job['id'], state='failed', stage='Gagal mencari GPU', error=message)
                    else:
                        self.store.update(job['id'], scheduler_error=message)

    def step(self, job):
        jid, state = job['id'], job['state']
        now = self.clock()
        if state == 'queued':
            offers = self.vast.offers()
            if not offers:
                self.store.update(jid, state='failed', stage=f'Machine {self.settings.machine_id} tidak tersedia atau tidak memenuhi batas harga/spesifikasi')
                return
            # Persist intent BEFORE the non-idempotent rental call.
            with self.store.lock:
                if self.store.get(jid)['state'] != 'queued':
                    return
                job = self.store.update(jid, state='creating', label='depth-vps-' + jid,
                                        rent_started=now, deadline=now + self.settings.max_minutes * 60,
                                        stage='Menyewa GPU', offer_price=offers[0]['dph_total'],
                                        gpu_name=offers[0].get('gpu_name', 'GPU'))
            try:
                iid = self.vast.create(offers[0]['id'], job)
            except (RentalRejected, VastAPIError) as exc:
                if isinstance(exc, RentalRejected) or (400 <= exc.status < 500 and exc.status != 408):
                    self.store.update(jid, state='failed', stage='Permintaan sewa ditolak', error=str(exc))
                    return
                self.store.update(jid, stage='Memeriksa hasil permintaan sewa; tidak menyewa ulang')
                return
            except Exception:
                self.store.update(jid, stage='Memeriksa hasil permintaan sewa; tidak menyewa ulang')
                return
            with self.store.lock:
                current = self.store.get(jid)
                self.store.update(jid, instance_ids=[iid],
                                  state='cleanup' if current.get('cancel_requested') or current.get('worker_failed') else 'booting',
                                  outcome='cancelled' if current.get('cancel_requested') else 'failed',
                                  stage='Menyiapkan model di GPU')
            return
        if state == 'creating':
            # A timeout may have succeeded remotely. Recover ONLY by the unique saved label.
            matches = [int(i['id']) for i in self.vast.instances() if i.get('label') == job['label']]
            if matches:
                cancelled = job.get('cancel_requested')
                expired = now >= job['deadline'] or job.get('worker_failed', False)
                self.store.update(jid, instance_ids=matches,
                                  state='cleanup' if cancelled or expired else 'booting',
                                  outcome='cancelled' if cancelled else 'failed',
                                  stage='Menghapus GPU' if cancelled or expired else 'Menyiapkan model di GPU')
            else:
                # Stay active: absence immediately after an ambiguous request is not proof of failure.
                self.store.update(jid, stage='Menunggu rekonsiliasi Vast.ai; cek console jika terus tertunda')
            return
        if state in {'booting', 'running'}:
            if job.get('cancel_requested') or now >= job['deadline']:
                self.store.update(jid, state='cleanup',
                                  outcome='cancelled' if job.get('cancel_requested') else 'failed',
                                  error='' if job.get('cancel_requested') else 'Batas waktu sewa tercapai.',
                                  stage='Menghapus GPU')
            return
        if state == 'cleanup':
            remaining = []
            for iid in job['instance_ids']:
                instance = self.vast.instance(iid)
                if instance is not None:
                    self.vast.destroy(iid)
                    remaining.append(iid)
            if remaining:
                self.store.update(jid, stage='Menunggu konfirmasi GPU terhapus', scheduler_error='')
            else:
                self.store.update(jid, state=job.get('outcome') or 'failed', stage='GPU terhapus',
                                  finished=now, scheduler_error='')
