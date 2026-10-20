import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import mediapipe as mp
import numpy as np

from face_guidance import draw_mesh, track_faces


def face(x):
    return [SimpleNamespace(x=x + (i % 20) / 200, y=.2 + (i // 20) / 200)
            for i in range(478)]


class GuidanceTests(unittest.TestCase):
    def track(self, results, folder):
        files = []
        for i in range(len(results)):
            path = folder / f'{i}.jpg'
            cv2.imwrite(str(path), np.zeros((100, 100, 3), np.uint8))
            files.append(path)
        with patch('face_guidance.model_path', return_value=folder / 'model.task'), patch.object(
                mp.tasks.vision.FaceLandmarker, 'create_from_options') as create:
            detector = create.return_value.__enter__.return_value
            detector.detect_for_video.side_effect = [
                SimpleNamespace(face_landmarks=faces) for faces in results]
            return track_faces(files, [15, 30], folder / 'report.json')

    def test_selected_subject_and_missing_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            frames, report = self.track([[face(.7), face(.1)], [], [face(.11)]], Path(directory))
            self.assertAlmostEqual(frames[0][0][0], .1)
            self.assertEqual(report['missing_frames'], [1])
            self.assertIsNone(frames[1])
            self.assertEqual(report['tracked'], 2)

    def test_refuse_large_jump_to_another_face(self):
        with tempfile.TemporaryDirectory() as directory:
            _, report = self.track([[face(.1)], [face(.8)]], Path(directory))
            self.assertEqual(report['missing_frames'], [1])

    def test_no_face_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'Wajah tidak terdeteksi'):
                self.track([[]], Path(directory))
            self.assertTrue((Path(directory) / 'report.json').is_file())

    def test_drawing_and_missing_frame(self):
        image = np.zeros((200, 200, 3), np.uint8)
        self.assertTrue(np.array_equal(draw_mesh(image.copy(), None), image))
        result = draw_mesh(image.copy(), [[p.x, p.y] for p in face(.2)])
        self.assertTrue(result.any())


if __name__ == '__main__':
    unittest.main()
