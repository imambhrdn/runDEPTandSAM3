"""Hugging Face provider: same API surface as Modal under /hf-api, claim dedup, engine reuse."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from controller.app import create_app
from controller.core import Settings


class FakeVast:
    def __init__(self):
        self.destroyed = []

    def offers(self): return []
    def create(self, *a, **kw): return 999
    def instances(self): return []
    def instance(self, iid): return None
    def destroy(self, iid): self.destroyed.append(iid)


class FakeHfClient:
    def __init__(self):
        self.calls = []
        self.poll_error = False

    def spawn(self, controller_url, job_id, token):
        self.calls.append((controller_url, job_id))
        return 'hf-' + job_id[:4]

    def poll(self, call_id):
        return ('error', 'Traceback: none') if self.poll_error else ('done', None)

    def cancel(self, call_id):
        return None


class HfBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_tmp = tempfile.TemporaryDirectory()
        cls.video = Path(cls.media_tmp.name) / 'test.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=c=blue:s=160x120:r=24:d=0.5', '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', str(cls.video)], check=True)
        cls.content = cls.video.read_bytes()
        os.environ['HF_ENABLED'] = '1'
        os.environ['HF_SPACE_URL'] = 'https://space.hf.space'

    @classmethod
    def tearDownClass(cls):
        cls.media_tmp.cleanup()
        os.environ.pop('HF_ENABLED', None)
        os.environ.pop('HF_SPACE_URL', None)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings('https://gpu.example.com', 'private-key', 'long-password-1234',
                                 'a' * 40, data=Path(self.tmp.name), max_upload_mb=1)
        self.hf_client = FakeHfClient()
        self.app = create_app(self.settings, FakeVast(), run_scheduler=False)
        self.app.state.hf_engine.client = self.hf_client
        self.store = self.app.state.hf_store
        self.engine = self.app.state.hf_engine
        self.client = TestClient(self.app)
        self.worker = TestClient(self.app)
        self.client.auth = ('admin', self.settings.password)
        self.client.headers['X-Controller'] = '1'

    def tearDown(self):
        self.client.close()
        self.worker.close()
        self.store.db.close()
        self.tmp.cleanup()

    def upload(self):
        response = self.client.post('/hf-api/api/upload', content=self.content,
                                    headers={'X-Video-Ext': '.mp4'})
        self.assertEqual(response.status_code, 200, response.text)
        self.job = response.json()
        return self.job

    def start(self):
        response = self.client.post(f'/hf-api/api/jobs/{self.job["id"]}/start',
                                    json={'x': 50, 'y': 50, 'face_guidance': False})
        self.assertEqual(response.status_code, 200, response.text)
        self.engine.tick()
        return response.json()

    def begin(self):
        self.upload()
        self.start()
        return {'Authorization': 'Bearer ' + self.store.get(self.job['id'])['token']}

    def test_config_and_hf_page(self):
        response = self.client.get('/hf-api/api/config')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['provider'], 'huggingface')
        self.assertTrue(data['enabled'])
        self.assertEqual(data['gpu'], 'ZeroGPU')
        self.assertEqual(self.client.get('/hf').status_code, 200)
        self.assertEqual(self.worker.get('/hf').status_code, 401)

    def test_engine_spawns_with_hf_api_base_once(self):
        job = self.upload()
        self.client.post(f'/hf-api/api/jobs/{job["id"]}/start', json={'x': 50, 'y': 50})
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(self.hf_client.calls, [('https://gpu.example.com/hf-api', job['id'])])
        body = self.store.get(job['id'])
        self.assertEqual(body['state'], 'booting')
        self.assertEqual(body['modal_call_id'], 'hf-' + job['id'][:4])

    def test_claim_and_dedup(self):
        headers = self.begin()
        existing = self.store.get(self.job['id'])['modal_call_id']
        self.assertEqual(self.worker.post(f'/hf-api/worker/{self.job["id"]}/claim',
                                          json={'call_id': existing}, headers=headers).status_code, 200)
        self.assertEqual(self.worker.post(f'/hf-api/worker/{self.job["id"]}/claim',
                                          json={'call_id': 'hf-other'}, headers=headers).status_code, 409)
        self.assertEqual(self.worker.post(f'/hf-api/worker/{self.job["id"]}/claim',
                                          json={'call_id': existing},
                                          headers={'Authorization': 'Bearer anders', 'X-Controller': '1'}
                                          ).status_code, 403)
        body = self.store.get(self.job['id'])
        self.assertEqual(body['modal_call_id'], existing)
        self.assertEqual(body['state'], 'booting')

    def test_cancel_flow_uses_cleanup(self):
        self.begin()
        self.client.post(f'/hf-api/api/jobs/{self.job["id"]}/cancel', json=None)
        self.assertTrue(self.store.get(self.job['id'])['cancel_requested'])
        self.engine.tick()
        self.assertEqual(self.store.get(self.job['id'])['state'], 'cleanup')
        self.assertEqual(self.store.get(self.job['id'])['outcome'], 'cancelled')
        self.engine.tick()
        body = self.store.get(self.job['id'])
        self.assertEqual(body['state'], 'cancelled')

    def test_worker_failed_redacts_to_message(self):
        headers = self.begin()
        self.worker.post(f'/hf-api/worker/{self.job["id"]}/claim',
                         json={'call_id': 'hf-z'}, headers=headers)
        response = self.worker.post(f'/hf-api/worker/{self.job["id"]}/failed', json={'error': 'boom'},
                                    headers=headers)
        self.assertEqual(response.status_code, 200)
        body = self.store.get(self.job['id'])
        self.assertEqual(body['state'], 'cleanup')
        self.assertEqual(body['outcome'], 'failed')


if __name__ == '__main__':
    unittest.main()
