import importlib.util
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch


class BatchWorkerTests(unittest.TestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location('batch_worker_test', Path(__file__).parents[2] / 'cloud_worker.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, CONTROLLER_URL='https://example.com', CONTROLLER_JOB='owner', WORKER_TOKEN='owner-token'):
            spec.loader.exec_module(module)
        return module

    def test_each_video_gets_own_token_and_runner_keeps_owner_token(self):
        worker = self.load()
        responses = [{'done': False, 'id': 'first', 'token': 'first-token'},
                     {'done': False, 'id': 'second', 'token': 'second-token'}, {'done': True}]
        with patch.object(worker, 'call', side_effect=[Mock(json=Mock(return_value=r)) for r in responses]) as call, \
             patch.object(worker.subprocess, 'Popen') as launch:
            launch.return_value.poll.return_value = 0
            worker.batch()
            self.assertEqual([c.kwargs['env']['CONTROLLER_JOB'] for c in launch.call_args_list], ['first', 'second'])
            self.assertEqual([c.kwargs['env']['WORKER_TOKEN'] for c in launch.call_args_list], ['first-token', 'second-token'])
            self.assertEqual([c.kwargs['json']['after'] for c in call.call_args_list], [None, 'first', 'second'])
            self.assertEqual(worker.TOKEN, 'owner-token')

    def test_lost_lease_terminates_child_process_group(self):
        worker = self.load()
        task = Mock(json=Mock(return_value={'done': False, 'id': 'first', 'token': 'token'}))
        with patch.object(worker, 'call', return_value=task), patch.object(worker.subprocess, 'Popen') as launch, \
             patch.object(worker.requests, 'get', side_effect=RuntimeError('lease lost')), patch.object(worker.os, 'killpg') as kill:
            process = launch.return_value
            process.pid = 1234
            process.poll.return_value = None
            process.wait.side_effect = [subprocess.TimeoutExpired('worker', 5), 0]
            with self.assertRaisesRegex(RuntimeError, 'lease lost'): worker.batch()
            kill.assert_called_once_with(1234, worker.signal.SIGTERM)
