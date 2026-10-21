"""Mocked Modal backend tests. No real SDK calls."""
import hashlib
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from controller.app import create_app
from controller.core import Settings, Store
from controller.modal_backend import ModalSettings, ModalClient, ModalEngine


class FakeVast:
    def __init__(self):
        self.creates = 0
        self.destroyed = []
    def offers(self): return []
    def create(self, *a, **kw): self.creates += 1; return 999
    def instances(self): return []
    def instance(self, iid): return None
    def destroy(self, iid): self.destroyed.append(iid)


class FakeModalClient:
    def __init__(self):
        self.spawns = []
        self.polls = {}
        self.cancels = []
        self.fail_spawn = False

    def spawn(self, controller_url, job_id, token):
        if self.fail_spawn:
            raise RuntimeError('Modal spawn failed')
        call_id = f'fc-{len(self.spawns)}'
        self.spawns.append((controller_url, job_id, token))
        self.polls[call_id] = ('running', None)
        return call_id

    def poll(self, call_id):
        return self.polls.get(call_id, ('error', 'Call ID unknown'))

    def cancel(self, call_id):
        self.cancels.append(call_id)
        self.polls[call_id] = ('error', 'Cancelled')


class ModalBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_tmp = tempfile.TemporaryDirectory()
        cls.video = Path(cls.media_tmp.name) / 'test.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=c=blue:s=160x120:r=24:d=0.5', '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', str(cls.video)], check=True)
        cls.content = cls.video.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.media_tmp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import os
        os.environ['MODAL_ENABLED'] = '1'
        self.settings = Settings('https://gpu.example.com', 'private-key', 'long-password-1234',
                                 'a' * 40, data=Path(self.tmp.name), max_upload_mb=1)
        self.vast = FakeVast()
        self.modal_client = FakeModalClient()
        self.app = create_app(self.settings, self.vast, run_scheduler=False)
        # Replace modal client with fake
        self.app.state.modal_engine.client = self.modal_client
        self.modal_store = self.app.state.modal_store
        self.modal_engine = self.app.state.modal_engine
        self.client = TestClient(self.app)
        self.worker = TestClient(self.app)
        self.client.auth = ('admin', self.settings.password)
        self.client.headers['X-Controller'] = '1'

    def tearDown(self):
        self.client.close()
        self.worker.close()
        self.modal_store.db.close()
        import os
        os.environ.pop('MODAL_ENABLED', None)
        self.tmp.cleanup()

    def upload(self):
        response = self.client.post('/modal-api/api/upload', content=self.content,
                                    headers={'X-Video-Ext': '.mp4'})
        self.assertEqual(response.status_code, 200, response.text)
        self.job = response.json()
        return self.job

    def start(self):
        response = self.client.post(f'/modal-api/api/jobs/{self.job["id"]}/start',
                                    json={'x': 50, 'y': 50, 'face_guidance': False})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def begin(self):
        self.upload()
        self.start()
        self.modal_engine.tick()
        token = self.modal_store.get(self.job['id'])['token']
        return {'Authorization': 'Bearer ' + token}

    # --- Tests ---

    def test_modal_config_returns_provider(self):
        response = self.client.get('/modal-api/api/config')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['provider'], 'modal')
        self.assertTrue(data['enabled'])
        self.assertEqual(data['gpu'], 'L4')

    def test_modal_page_authenticated(self):
        self.assertEqual(self.worker.get('/modal').status_code, 401)
        self.assertEqual(self.client.get('/modal').status_code, 200)

    def test_modal_worker_bypass_auth(self):
        headers = self.begin()
        response = self.worker.get(f'/modal-api/worker/{self.job["id"]}/task', headers=headers)
        self.assertEqual(response.status_code, 200)

    def test_modal_worker_wrong_token_rejected(self):
        self.begin()
        response = self.worker.get(f'/modal-api/worker/{self.job["id"]}/task',
                                   headers={'Authorization': 'Bearer wrong'})
        self.assertEqual(response.status_code, 403)

    def test_modal_upload_isolated_from_vast(self):
        self.upload()
        self.assertEqual(len(self.modal_store.all()), 1)
        vast_store = self.app.state.store
        self.assertEqual(len(vast_store.all()), 0)
        self.assertEqual(self.vast.creates, 0)

    def test_modal_lifecycle_spawn_claim_complete(self):
        headers = self.begin()
        job = self.modal_store.get(self.job['id'])
        self.assertIn(job['state'], {'booting', 'running'})
        self.assertTrue(job.get('modal_call_id'))
        self.assertEqual(len(self.modal_client.spawns), 1)
        self.assertIn('/modal-api', self.modal_client.spawns[0][0])
        # Claim
        call_id = job['modal_call_id']
        response = self.worker.post(f'/modal-api/worker/{self.job["id"]}/claim',
                                    headers=headers, json={'call_id': call_id})
        self.assertEqual(response.status_code, 200)
        # Duplicate claim same ID is fine
        response = self.worker.post(f'/modal-api/worker/{self.job["id"]}/claim',
                                    headers=headers, json={'call_id': call_id})
        self.assertEqual(response.status_code, 200)
        # Different call_id is rejected
        response = self.worker.post(f'/modal-api/worker/{self.job["id"]}/claim',
                                    headers=headers, json={'call_id': 'fc-other'})
        self.assertEqual(response.status_code, 409)
        # Heartbeat
        self.worker.post(f'/modal-api/worker/{self.job["id"]}/heartbeat',
                         headers=headers, json={'stage': 'Depth (1 dari 4)'})
        self.assertEqual(self.modal_store.get(self.job['id'])['state'], 'running')
        # Upload artifacts
        digest = hashlib.sha256(self.content).hexdigest()
        for name in ['composite.mp4', 'mask_preview.mp4']:
            r = self.worker.put(f'/modal-api/worker/{self.job["id"]}/artifacts/{name}',
                                content=self.content, headers={**headers, 'X-SHA256': digest})
            self.assertEqual(r.status_code, 200, r.text)
        # Complete
        r = self.worker.post(f'/modal-api/worker/{self.job["id"]}/complete',
                             headers=headers, json={})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.modal_store.get(self.job['id'])['state'], 'cleanup')
        # Scheduler transitions to done
        self.modal_client.polls[call_id] = ('done', 'ok')
        self.modal_engine.tick()
        self.assertEqual(self.modal_store.get(self.job['id'])['state'], 'done')

    def test_modal_cancel_before_spawn(self):
        self.upload()
        self.start()
        self.client.post(f'/modal-api/api/jobs/{self.job["id"]}/cancel')
        self.modal_engine.tick()
        # Spawn happened, but job goes to cleanup
        job = self.modal_store.get(self.job['id'])
        self.assertIn(job['state'], {'cleanup', 'cancelled'})

    def test_modal_cancel_during_processing(self):
        headers = self.begin()
        self.client.post(f'/modal-api/api/jobs/{self.job["id"]}/cancel')
        self.modal_engine.tick()
        job = self.modal_store.get(self.job['id'])
        self.assertIn(job['state'], {'cleanup', 'cancelled'})
        self.assertTrue(len(self.modal_client.cancels) > 0)

    def test_modal_ambiguous_spawn_no_respawn(self):
        self.modal_client.fail_spawn = True
        self.upload()
        self.start()
        self.modal_engine.tick()
        job = self.modal_store.get(self.job['id'])
        self.assertEqual(job['state'], 'creating')
        # Tick again: must NOT respawn
        self.modal_client.fail_spawn = False
        self.modal_engine.tick()
        job = self.modal_store.get(self.job['id'])
        self.assertIn(job['state'], {'creating'})
        self.assertEqual(len(self.modal_client.spawns), 0)

    def test_modal_ambiguous_spawn_recovered_by_claim(self):
        self.modal_client.fail_spawn = True
        self.upload()
        self.start()
        self.modal_engine.tick()
        job = self.modal_store.get(self.job['id'])
        self.assertEqual(job['state'], 'creating')
        # Worker claims with its own call_id
        token = job['token']
        headers = {'Authorization': 'Bearer ' + token}
        r = self.worker.post(f'/modal-api/worker/{self.job["id"]}/claim',
                             headers=headers, json={'call_id': 'fc-external'})
        self.assertEqual(r.status_code, 200)
        job = self.modal_store.get(self.job['id'])
        self.assertEqual(job['state'], 'booting')
        self.assertEqual(job['modal_call_id'], 'fc-external')

    def test_modal_timeout(self):
        headers = self.begin()
        # Set deadline in the past
        self.modal_store.update(self.job['id'], deadline=time.time() - 1)
        self.modal_engine.tick()
        job = self.modal_store.get(self.job['id'])
        self.assertIn(job['state'], {'cleanup', 'failed'})

    def test_modal_disabled_rejects_start(self):
        self.modal_engine.settings.enabled = False
        self.upload()
        response = self.client.post(f'/modal-api/api/jobs/{self.job["id"]}/start',
                                    json={'x': 50, 'y': 50, 'face_guidance': False})
        self.assertEqual(response.status_code, 503)
        self.assertIn('belum diaktifkan', response.json()['detail'])

    def test_modal_error_redacted(self):
        headers = self.begin()
        call_id = self.modal_store.get(self.job['id'])['modal_call_id']
        self.modal_client.polls[call_id] = ('error', 'RuntimeError: secret-token-leaked')
        self.modal_engine.tick()
        job = self.modal_store.get(self.job['id'])
        self.assertEqual(job['state'], 'failed')
        self.assertNotIn('secret-token', job.get('error', ''))

    def test_modal_artifact_download(self):
        headers = self.begin()
        digest = hashlib.sha256(self.content).hexdigest()
        for name in ['composite.mp4', 'mask_preview.mp4']:
            self.worker.put(f'/modal-api/worker/{self.job["id"]}/artifacts/{name}',
                            content=self.content, headers={**headers, 'X-SHA256': digest})
        self.worker.post(f'/modal-api/worker/{self.job["id"]}/complete',
                         headers=headers, json={})
        call_id = self.modal_store.get(self.job['id'])['modal_call_id']
        self.modal_client.polls[call_id] = ('done', 'ok')
        self.modal_engine.tick()
        r = self.client.get(f'/modal-api/api/jobs/{self.job["id"]}/files/composite.mp4')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, self.content)


if __name__ == '__main__':
    unittest.main()
