import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from controller.app import create_app
from controller.core import Settings, Store, Engine
from controller.tests.test_controller import FakeVast


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings('https://example.com', 'key', 'long-password-1234', 'a' * 40, data=Path(self.tmp.name))
        self.vast = FakeVast()
        self.app = create_app(self.settings, self.vast, run_scheduler=False)
        self.store = self.app.state.store
        self.engine = self.app.state.engine
        self.client = TestClient(self.app)
        self.jobs = [self.store.create(source='source.mp4', width=160, height=120, seconds=.5) for _ in range(3)]
        self.owner = self.jobs[0]['id']
        self.auth = {'Authorization': 'Bearer ' + self.jobs[0]['token']}
        self.params = dict(x=30, y=40, face_guidance=False)

    def tearDown(self):
        self.client.close()
        for store in (self.store, self.app.state.modal_store, self.app.state.hf_store):
            store.db.close()
        self.tmp.cleanup()

    def start(self):
        response = self.client.post('/api/batches/start', auth=('admin', self.settings.password),
            headers={'X-Controller': '1'}, json={'jobs': [{'id': j['id'], 'params': self.params} for j in self.jobs]})
        self.assertEqual(response.status_code, 200, response.text)
        self.engine.tick()

    def next(self, after=None):
        return self.client.post(f'/worker/{self.owner}/next', headers=self.auth, json={'after': after})

    def complete(self, job):
        self.store.update(job['id'], artifacts={'composite.mp4': {}, 'mask_preview.mp4': {}})
        response = self.client.post(f'/worker/{job["id"]}/complete', headers={'Authorization': 'Bearer ' + job['token']}, json={})
        self.assertEqual(response.status_code, 200, response.text)

    def test_three_videos_one_rental_handoff_retry_restart_and_cleanup(self):
        self.start()
        self.assertEqual(self.next().json()['id'], self.owner)
        self.assertEqual(self.next().json()['id'], self.owner)
        self.assertEqual(self.next(self.owner).status_code, 409)
        self.complete(self.jobs[0])
        second = self.next(self.owner).json()
        self.assertEqual(second['id'], self.jobs[1]['id'])
        self.assertEqual(self.next(self.owner).json(), second)
        restarted = Store(self.settings.data)
        Engine(restarted, self.vast, self.settings).tick()
        restarted.db.close()
        self.assertEqual(self.vast.creates, 1)
        self.assertEqual(self.vast.destroyed, [])
        self.complete(self.jobs[1])
        self.assertEqual(self.next(self.jobs[1]['id']).json()['id'], self.jobs[2]['id'])
        self.complete(self.jobs[2])
        self.assertTrue(self.next(self.jobs[2]['id']).json()['done'])
        self.assertTrue(self.next(self.jobs[2]['id']).json()['done'])
        self.engine.tick(); self.engine.tick()
        self.assertEqual(self.vast.destroyed, [789])
        self.assertEqual([self.store.get(j['id'])['state'] for j in self.jobs], ['done'] * 3)

    def test_cancel_child_stops_entire_queue_and_retains_manual_gpu(self):
        self.settings.auto_destroy = False
        self.start(); self.next(); self.complete(self.jobs[0]); self.next(self.owner)
        response = self.client.post(f'/api/jobs/{self.jobs[1]["id"]}/cancel',
            auth=('admin', self.settings.password), headers={'X-Controller': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(f'/worker/{self.owner}/lease', headers=self.auth).status_code, 409)
        self.engine.tick(); self.engine.tick()
        self.assertEqual(self.vast.destroyed, [])
        self.assertEqual(self.store.get(self.owner)['state'], 'cleanup')
        self.assertEqual(self.store.get(self.jobs[2]['id'])['state'], 'cancelled')

    def test_validation_is_atomic_and_no_offers_release_queue(self):
        items = [{'id': j['id'], 'params': self.params} for j in self.jobs]
        items[-1]['params'] = dict(x=999, y=10)
        with self.assertRaises(ValueError): self.store.start_batch(items)
        self.assertTrue(all(j['state'] == 'ready' for j in self.store.all()))
        self.vast.offers = lambda: []
        self.start(); self.engine.tick()
        self.assertTrue(all(j['state'] == 'failed' for j in self.store.all()))
        self.assertEqual(self.vast.creates, 0)

    def test_deadline_covers_whole_batch_and_rejects_cross_job_token(self):
        self.start()
        bad = self.client.post(f'/worker/{self.owner}/next',
            headers={'Authorization': 'Bearer ' + self.jobs[1]['token']}, json={})
        self.assertEqual(bad.status_code, 403)
        self.next(); self.complete(self.jobs[0]); self.next(self.owner)
        self.store.update(self.owner, deadline=0)
        self.assertEqual(self.next(self.jobs[1]['id']).status_code, 409)
        self.engine.tick(); self.engine.tick()
        self.assertEqual(self.store.get(self.jobs[1]['id'])['state'], 'failed')
        self.assertEqual(self.vast.creates, 1)

    def test_failed_video_continues_but_runner_failure_stops_batch(self):
        self.start(); self.next()
        response = self.client.post(f'/worker/{self.owner}/failed', headers=self.auth, json={'error': 'bad video'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.next(self.owner).json()['id'], self.jobs[1]['id'])
        self.assertEqual(self.vast.creates, 1)
        self.client.post(f'/worker/{self.owner}/failed', headers=self.auth,
            json={'error': 'runner exited', 'batch_abort': True})
        self.engine.tick()
        self.assertEqual(self.store.get(self.jobs[1]['id'])['state'], 'failed')
        self.assertEqual(self.vast.destroyed, [789])
