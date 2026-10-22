"""Track one selected face on original frames and draw expression guidance."""
import json
import os
from pathlib import Path
import urllib.request

MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/face_landmarker/'
             'face_landmarker/float16/1/face_landmarker.task')


def model_path():
    target = Path(os.environ.get('FACE_MODEL', 'checkpoints/face_landmarker.task'))
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        # A unique temporary file prevents partial downloads being reused.
        import tempfile
        fd, name = tempfile.mkstemp(dir=target.parent, suffix='.download')
        os.close(fd)
        temporary = Path(name)
        try:
            import shutil
            try:
                with urllib.request.urlopen(MODEL_URL, timeout=120) as response, temporary.open('wb') as output:
                    shutil.copyfileobj(response, output)
            except OSError as exc:
                raise RuntimeError('Model wajah gagal diunduh dari storage.googleapis.com. '
                                   'Periksa akses internet atau atur FACE_MODEL ke file lokal.') from exc
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def track_faces(files, point, report_path, fps=24):
    import cv2
    import mediapipe as mp
    import numpy as np

    options = mp.tasks.vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path())),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        num_faces=5,
    )
    frames, missing = [], []
    anchor = None
    with mp.tasks.vision.FaceLandmarker.create_from_options(options) as detector:
        for i, path in enumerate(files):
            image = cv2.imread(str(path))
            if image is None:
                raise ValueError(f'Frame tidak bisa dibaca: {path}')
            h, w = image.shape[:2]
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            result = detector.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)),
                round(i * 1000 / fps),
            )
            candidates = result.face_landmarks
            selected = None
            if candidates:
                centers = [(sum(p.x for p in face) / len(face),
                            sum(p.y for p in face) / len(face)) for face in candidates]
                # Initially choose the face nearest the user's subject click.
                # Thereafter follow its previous center, without switching across large gaps.
                reference = anchor or (point[0] / w, point[1] / h)
                idx = min(range(len(centers)), key=lambda k:
                          (centers[k][0] - reference[0]) ** 2 +
                          (centers[k][1] - reference[1]) ** 2)
                center = centers[idx]
                if anchor is None or ((center[0] - anchor[0]) ** 2 +
                                      (center[1] - anchor[1]) ** 2) <= 0.15 ** 2:
                    selected = [[float(p.x), float(p.y)] for p in candidates[idx]]
                    anchor = center
            if selected is None:
                missing.append(i)
            frames.append(selected)
    report = {'fps': fps, 'frames': len(files), 'tracked': len(files) - len(missing),
              'missing_frames': missing, 'landmarks': frames}
    Path(report_path).write_text(json.dumps(report), encoding='utf-8')
    if len(missing) == len(files):
        report['warning'] = 'Wajah tidak terdeteksi di seluruh frame; mesh wajah dilewati.'
    return frames, report


def draw_mesh(image, landmarks):
    """Draw on the finished composite; coordinates come from the original frame."""
    if landmarks is None:
        return image
    import cv2
    import mediapipe as mp
    h, w = image.shape[:2]
    points = [(round(x * w), round(y * h)) for x, y in landmarks]
    connections = mp.tasks.vision.FaceLandmarksConnections
    for name, color in [('FACE_LANDMARKS_TESSELATION', (205, 170, 50)),
                        ('FACE_LANDMARKS_LIPS', (100, 135, 255)),
                        ('FACE_LANDMARKS_LEFT_EYE', (150, 255, 130)),
                        ('FACE_LANDMARKS_RIGHT_EYE', (150, 255, 130)),
                        ('FACE_LANDMARKS_LEFT_EYEBROW', (150, 255, 130)),
                        ('FACE_LANDMARKS_RIGHT_EYEBROW', (150, 255, 130))]:
        for edge in getattr(connections, name):
            cv2.line(image, points[edge.start], points[edge.end], color, 1, cv2.LINE_AA)
    return image
