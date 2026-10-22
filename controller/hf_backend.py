"""Hugging Face Spaces (ZeroGPU/CPU) scheduler: same claim/dedup protocol over plain HTTP.

The Space runs cloud_worker.py with tokens it receives per run; the controller never
stores or prints the optional HF_TOKEN. Scheduler reuses ModalEngine with client
methods mapped to HTTP, so state machine behavior (no double spawn, claim dedup,
deadline cleanup) stays identical.
"""
import os
import uuid

import requests

from .modal_backend import ModalEngine


class HfSettings:
    """Read-only Hugging Face configuration from environment."""

    def __init__(self, public_url, password, data):
        self.enabled = os.getenv('HF_ENABLED', '0') == '1'
        self.public_url = public_url
        self.password = password
        self.data = data / 'hf'
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.api_base = '/hf-api'
        self.space_url = os.getenv('HF_SPACE_URL', '').rstrip('/')
        self.token = os.getenv('HF_TOKEN', '')
        self.gpu = os.getenv('HF_GPU', 'ZeroGPU')
        self.max_minutes = int(os.getenv('HF_MAX_MINUTES', '20'))
        self.max_upload_mb = int(os.getenv('MAX_UPLOAD_MB', '250'))

    def config_dict(self):
        if not self.enabled:
            configured = 'HF_ENABLED belum diset ke 1.'
        elif not self.space_url:
            configured = 'HF_SPACE_URL belum diset ke URL Space worker.'
        else:
            configured = 'Integrasi Hugging Face aktif.'
        return dict(enabled=self.enabled, provider='huggingface', gpu=self.gpu,
                    max_upload_mb=self.max_upload_mb, max_minutes=self.max_minutes,
                    auto_destroy=True, configured=configured)


class HfClient:
    """Trigger one run inside the Space; job state comes from worker callbacks only."""

    def __init__(self, settings, post=None):
        self.settings = settings
        self._post = post or requests.post

    def spawn(self, controller_url, job_id, token):
        headers = {'Authorization': 'Bearer ' + self.settings.token} if self.settings.token else {}
        response = self._post(self.settings.space_url + '/run',
                              json={'controller': controller_url, 'job_id': job_id, 'token': token},
                              headers=headers, timeout=(15, 120))
        if response.status_code == 409:
            raise RuntimeError('Space sedang memproses job lain.')
        response.raise_for_status()
        # Call id only distinguishes this execution inside the claim endpoint.
        return 'hf-' + uuid.uuid4().hex[:12]

    def poll(self, call_id):
        # Worker complete/failed callbacks finish jobs; poll never blocks the scheduler.
        return ('done', None)

    def cancel(self, call_id):
        # Worker aborts via cancel_requested flag and job deadline; nothing remote to stop.
        return None
