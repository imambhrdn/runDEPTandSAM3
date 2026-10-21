import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from controller.app import create_app
from controller.core import Engine, RentalRejected, Settings, Store, Vast


class FakeVast:
    def __init__(self):
        self.rows = {}
        self.creates = 0
        self.destroyed = []
        self.ambiguous = False
        self.reject = False
        self.delete_error = False
        self.hide_create = False

    def offers(self):
        return [{'id': 123, 'machine_id': 28726, 'dph_total': .2, 'gpu_name': 'RTX 3090'}]

    def create(self, offer_id, job):
        self.creates += 1
        if self.reject:
            raise RentalRejected('Offer unavailable')
        if not self.hide_create:
            self.rows[789] = {'id': 789, 'label': job['label']}
        if self.ambiguous:
            raise TimeoutError()
        return 789

    def instances(self):
        return list(self.rows.values())

    def instance(self, iid):
        return self.rows.get(iid)

    def destroy(self, iid):
        if self.delete_error:
            raise RuntimeError('temporary outage')
        self.destroyed.append(iid)
        self.rows.pop(iid, None)

    def balance(self):
        return 1.72


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings('https://gpu.example.com', 'private-key', 'long-password-1234',
                                 'a' * 40, data=Path(self.tmp.name))
        self.store = Store(self.settings.data)
        self.vast = FakeVast()
        self.now = 1000
        self.engine = Engine(self.store, self.vast, self.settings, lambda: self.now)
        self.job = self.store.create(source='source.mp4', width=160, height=120, seconds=.5)
        self.params = dict(x=50, y=50, face_guidance=False, colormap='inferno', dilate=6, feather=3)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def start(self):
        self.store.start(self.job['id'], self.params)
        self.engine.tick()
        return self.store.get(self.job['id'])

    def test_success_cleans_only_own_instance_and_waits_confirmation(self):
        self.vast.rows[999] = {'id': 999, 'label': 'unrelated'}
        job = self.start()
        self.assertEqual(job['state'], 'booting')
        self.engine.tick()
        self.assertEqual(self.vast.destroyed, [])  # Never delete before artifacts are received.
        self.store.update(job['id'], state='cleanup', outcome='done')
        self.engine.tick()
        self.assertEqual(self.store.get(job['id'])['state'], 'cleanup')
        self.engine.tick()
        self.assertEqual(self.store.get(job['id'])['state'], 'done')
        self.assertEqual(self.vast.destroyed, [789])
        self.assertIn(999, self.vast.rows)

    def test_timeout_destroys_instance(self):
        job = self.start()
        self.now = job['deadline'] + 1
        self.engine.tick()
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(self.store.get(job['id'])['state'], 'failed')
        self.assertEqual(self.vast.destroyed, [789])

    def test_manual_cleanup_retains_gpu_until_user_deletes_it(self):
        self.settings.auto_destroy = False
        job = self.start()
        self.now = job['deadline'] + 1
        self.engine.tick()
        for outcome in ['done', 'failed', 'cancelled']:
            self.store.update(job['id'], state='cleanup', outcome=outcome)
            self.engine.tick()
            self.assertEqual(self.vast.destroyed, [])
            self.assertEqual(self.store.get(job['id'])['state'], 'cleanup')
            self.assertIn('Hapus manual', self.store.get(job['id'])['stage'])
        another = self.store.create(source='source.mp4')
        with self.assertRaises(ValueError):
            self.store.start(another['id'], self.params)
        vast = Vast(self.settings)
        vast.request = Mock()
        with self.assertRaises(RuntimeError):
            vast.destroy(789)
        vast.request.assert_not_called()
        del self.vast.rows[789]  # Simulate deletion by the user in Vast.ai.
        self.engine.tick()
        self.assertEqual(self.store.get(job['id'])['state'], 'cancelled')

    def test_uncertain_create_recovers_without_rerenting_after_restart(self):
        self.vast.ambiguous = True
        self.assertEqual(self.start()['state'], 'creating')
        restarted = Store(self.settings.data)
        engine = Engine(restarted, self.vast, self.settings, lambda: self.now)
        engine.tick()
        self.assertEqual(restarted.get(self.job['id'])['instance_ids'], [789])
        self.assertEqual(self.vast.creates, 1)
        restarted.db.close()

    def test_ambiguous_missing_instance_blocks_new_rentals(self):
        self.vast.hide_create, self.vast.ambiguous = True, True
        self.start()
        self.engine.tick()
        another = self.store.create(source='source.mp4')
        with self.assertRaises(ValueError):
            self.store.start(another['id'], self.params)
        self.assertEqual(self.vast.creates, 1)

    def test_known_rejection_does_not_stay_ambiguous(self):
        self.vast.reject = True
        self.assertEqual(self.start()['state'], 'failed')

    def test_cleanup_failure_retries_after_restart(self):
        job = self.start()
        self.store.update(job['id'], state='cleanup', outcome='cancelled')
        self.vast.delete_error = True
        self.engine.tick()
        self.assertEqual(self.store.get(job['id'])['state'], 'cleanup')
        self.assertTrue(self.store.get(job['id'])['scheduler_error'])
        self.vast.delete_error = False
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(self.store.get(job['id'])['state'], 'cancelled')

    def test_no_offers_never_creates(self):
        self.vast.offers = lambda: []
        self.assertEqual(self.start()['state'], 'failed')
        self.assertEqual(self.vast.creates, 0)

    def test_query_and_local_filter_enforce_machine_and_budget(self):
        vast = Vast(self.settings)
        vast.request = Mock(return_value={'offers': [
            {'id': 1, 'machine_id': 28726, 'dph_total': .2, 'gpu_ram': 16384, 'disk_space': 30},
            {'id': 2, 'machine_id': 999, 'dph_total': .1},
            {'id': 3, 'machine_id': 28726, 'dph_total': 5}]})
        self.assertEqual([o['id'] for o in vast.offers()], [1])
        query = vast.request.call_args.kwargs['json']
        self.assertEqual(query['machine_id'], {'eq': 28726})
        self.assertEqual(query['dph_total'], {'lte': .30})
        self.assertEqual(query['gpu_ram'], {'gte': 16000})

    def test_worker_environment_has_no_vast_key_and_pins_code(self):
        vast = Vast(self.settings)
        vast.request = Mock(return_value={'success': True, 'new_contract': 789})
        job = {**self.job, 'label': 'depth-vps-test'}
        vast.create(123, job)
        payload = vast.request.call_args.kwargs['json']
        self.assertNotIn(self.settings.api_key, json.dumps(payload))
        self.assertEqual(payload['env']['WORKER_TOKEN'], job['token'])
        self.assertIn(self.settings.worker_ref, payload['onstart'])
        script = payload['onstart']
        self.assertIn('printf "transformers==4.57.6\\n" > /workspace/gpu-worker/.worker-constraints.txt', script)
        self.assertIn('export PIP_CONSTRAINT=/workspace/gpu-worker/.worker-constraints.txt', script)
        self.assertLess(script.index('export PIP_CONSTRAINT='), script.index('exec bash run.sh'))
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)

    def test_zero_min_vram_disables_only_vram_filter(self):
        environment = {
            'PUBLIC_URL': self.settings.public_url, 'VAST_API_KEY': self.settings.api_key,
            'CONTROLLER_PASSWORD': self.settings.password, 'WORKER_REF': self.settings.worker_ref,
            'DATA_DIR': str(self.settings.data), 'MIN_VRAM_GB': '0',
        }
        with patch.dict('os.environ', environment, clear=True):
            settings = Settings.from_env()
        vast = Vast(settings)
        vast.request = Mock(return_value={'offers': [
            {'id': 1, 'machine_id': 28726, 'dph_total': .2, 'gpu_ram': 12288, 'disk_space': 30},
            {'id': 2, 'machine_id': 999, 'dph_total': .1},
            {'id': 3, 'machine_id': 28726, 'dph_total': 5}]})
        self.assertEqual([o['id'] for o in vast.offers()], [1])
        query = vast.request.call_args.kwargs['json']
        self.assertNotIn('gpu_ram', query)
        self.assertEqual(query['machine_id'], {'eq': 28726})
        self.assertEqual(query['dph_total'], {'lte': .30})
        self.assertEqual(query['compute_cap'], {'gte': 800})
        self.assertEqual(query['rentable'], {'eq': True})
        with patch.dict('os.environ', {**environment, 'MIN_VRAM_GB': '-1'}, clear=True):
            with self.assertRaises(ValueError):
                Settings.from_env()

    def test_all_machines_filter_vram_then_sort_disk_and_price(self):
        environment = {
            'PUBLIC_URL': self.settings.public_url, 'VAST_API_KEY': self.settings.api_key,
            'CONTROLLER_PASSWORD': self.settings.password, 'WORKER_REF': self.settings.worker_ref,
            'DATA_DIR': str(self.settings.data), 'VAST_MACHINE_ID': '0',
            'MIN_VRAM_GB': '12', 'MAX_PRICE_HOUR': '0.099',
        }
        with patch.dict('os.environ', environment, clear=True):
            settings = Settings.from_env()
        vast = Vast(settings)
        rows = [
            dict(id=1, machine_id=100, gpu_ram=12288, disk_space=70, dph_total=.05),
            dict(id=2, machine_id=200, gpu_ram=12288, disk_space=55, dph_total=.099),
            dict(id=3, machine_id=300, gpu_ram=24576, disk_space=55, dph_total=.09),
            dict(id=4, machine_id=400, gpu_ram=8192, disk_space=30, dph_total=.04),
            dict(id=5, machine_id=500, gpu_ram=12288, disk_space=50, dph_total=.10),
            dict(id=6, machine_id=600, gpu_ram=12288, disk_space=29, dph_total=.04),
            dict(id=7, machine_id=700, gpu_ram=12288, dph_total=.04),
        ]
        vast.request = Mock(return_value={'offers': rows})
        self.assertEqual([o['id'] for o in vast.offers()], [3, 2, 1])
        query = vast.request.call_args.kwargs['json']
        self.assertNotIn('machine_id', query)
        self.assertEqual(query['gpu_ram'], {'gte': 12000})
        self.assertEqual(query['disk_space'], {'gte': 30})
        self.assertEqual(query['allocated_storage'], 30)
        self.assertEqual(query['dph_total'], {'lte': .099})
        self.assertEqual(query['order'], [['disk_space', 'asc'], ['dph_total', 'asc']])
        self.assertEqual(query['cuda_max_good'], {'gte': 12.4})
        self.assertEqual(query['compute_cap'], {'gte': 800})
        self.assertEqual(query['rentable'], {'eq': True})
        self.assertEqual(query['verified'], {'eq': True})
        self.vast.offers = lambda: []
        self.settings.machine_id = 0
        self.assertEqual(self.start()['stage'], 'GPU tidak tersedia atau tidak memenuhi batas harga/spesifikasi')


class WebTests(unittest.TestCase):
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
        self.settings = Settings('https://gpu.example.com', 'private-key', 'long-password-1234',
                                 'a' * 40, data=Path(self.tmp.name), max_upload_mb=1)
        self.vast = FakeVast()
        self.app = create_app(self.settings, self.vast, run_scheduler=False)
        self.store = self.app.state.store
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
        response = self.client.post('/api/upload', content=self.content, headers={'X-Video-Ext': '.mp4'})
        self.assertEqual(response.status_code, 200, response.text)
        self.job = response.json()
        self.params = {'x': 50, 'y': 50, 'face_guidance': False}
        return self.job

    def begin(self):
        self.upload()
        response = self.client.post(f'/api/jobs/{self.job["id"]}/start', json=self.params)
        self.assertEqual(response.status_code, 200)
        self.app.state.engine.tick()
        token = self.store.get(self.job['id'])['token']
        return {'Authorization': 'Bearer ' + token}

    def test_authentication_and_csrf_header(self):
        self.assertEqual(self.client.get('/api/jobs', auth=('admin', 'wrong')).status_code, 401)
        self.client.headers.pop('X-Controller')
        self.assertEqual(self.client.post('/api/upload').status_code, 403)

    def test_modal_page_is_authenticated_and_separate_from_vast(self):
        self.assertEqual(self.worker.get('/modal').status_code, 401)
        response = self.client.get('/modal')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Modal Studio', response.text)
        self.assertIn('/static/modal.js', response.text)
        self.assertNotIn('/static/app.js', response.text)
        self.assertIn('/static/app.js', self.client.get('/').text)
        self.assertEqual(self.vast.creates, 0)
        self.assertFalse(self.store.all())

    def test_upload_does_not_rent_or_expose_secrets(self):
        job = self.upload()
        self.assertNotIn('token', job)
        self.assertNotIn('private-key', json.dumps(job))
        self.assertEqual(self.vast.creates, 0)
        self.assertEqual(self.client.get(job['frame_url']).status_code, 200)

    def test_complete_requires_verified_results_then_deletes_gpu(self):
        headers = self.begin()
        base = f'/worker/{self.job["id"]}'
        self.assertEqual(self.worker.post(base + '/complete', headers=headers, json={}).status_code, 409)
        digest = hashlib.sha256(self.content).hexdigest()
        for name in ['composite.mp4', 'mask_preview.mp4']:
            response = self.worker.put(base + '/artifacts/' + name, content=self.content,
                                       headers={**headers, 'X-SHA256': digest})
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.vast.destroyed, [])
        self.assertEqual(self.worker.post(base + '/complete', headers=headers, json={}).status_code, 200)
        self.app.state.engine.tick()
        self.app.state.engine.tick()
        self.assertEqual(self.store.get(self.job['id'])['state'], 'done')
        response = self.client.get(f'/api/jobs/{self.job["id"]}/files/composite.mp4')
        self.assertEqual(response.content, self.content)

    def test_wrong_worker_token_and_checksum_rejected(self):
        headers = self.begin()
        base = f'/worker/{self.job["id"]}'
        self.assertEqual(self.worker.get(base + '/task', headers={'Authorization': 'Bearer wrong'}).status_code, 403)
        response = self.worker.put(base + '/artifacts/composite.mp4', content=self.content,
                                   headers={**headers, 'X-SHA256': 'wrong'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.store.get(self.job['id'])['artifacts'])
        self.assertEqual(self.vast.destroyed, [])

    def test_invalid_video_even_with_valid_hash_rejected(self):
        headers = self.begin()
        body = b'not a real video'
        response = self.worker.put(f'/worker/{self.job["id"]}/artifacts/composite.mp4', content=body,
                                   headers={**headers, 'X-SHA256': hashlib.sha256(body).hexdigest()})
        self.assertEqual(response.status_code, 400)

    def test_late_completion_cannot_override_cancel(self):
        headers = self.begin()
        jid = self.job['id']
        self.client.post(f'/api/jobs/{jid}/cancel')
        response = self.worker.post(f'/worker/{jid}/complete', headers=headers, json={})
        self.assertEqual(response.status_code, 409)
        self.app.state.engine.tick()
        self.app.state.engine.tick()
        self.app.state.engine.tick()
        self.assertEqual(self.store.get(jid)['state'], 'cancelled')

    def test_oversized_source_rejected(self):
        response = self.client.post('/api/upload', content=b'x' * (1024 ** 2 + 1),
                                    headers={'X-Video-Ext': '.mp4'})
        self.assertEqual(response.status_code, 413)
        self.assertFalse(self.store.all())

    def test_out_of_bounds_selection_rejected(self):
        self.upload()
        response = self.client.post(f'/api/jobs/{self.job["id"]}/start', json={'x': 999, 'y': 2})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.vast.creates, 0)

    def test_startup_failure_before_rental_response_is_reconciled(self):
        self.vast.ambiguous = True
        headers = self.begin()
        self.worker.post(f'/worker/{self.job["id"]}/failed', headers=headers,
                         json={'error': 'Setup failed'})
        self.app.state.engine.tick()
        self.assertEqual(self.store.get(self.job['id'])['state'], 'cleanup')
        self.app.state.engine.tick()
        self.app.state.engine.tick()
        self.assertEqual(self.store.get(self.job['id'])['state'], 'failed')
        self.assertEqual(self.vast.creates, 1)

    def test_failed_worker_cleans_up(self):
        headers = self.begin()
        self.worker.post(f'/worker/{self.job["id"]}/failed', headers=headers, json={'error': 'CUDA OOM'})
        self.app.state.engine.tick()
        self.app.state.engine.tick()
        self.assertEqual(self.store.get(self.job['id'])['state'], 'failed')
        self.assertEqual(self.vast.destroyed, [789])


if __name__ == '__main__':
    unittest.main()
