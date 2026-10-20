#!/usr/bin/env python3
"""
Persiapan input untuk Omni/Seedance: depth berwarna HANYA di area subjek,
latar tetap video asli. Output dibisukan (tanpa audio).

Alur:
  1. ffmpeg: ekstrak frame (24 fps, lebar maks 960)
  2. Depth Anything V2 per frame, dinormalisasi global (kurangi flicker), colormap
  3. SAM 2 video predictor: mask orang dari 1 klik di frame pertama
  4. Composite: depth di area mask, asli di luar mask (dengan dilate + feather)
  5. ffmpeg: encode MP4 tanpa audio

Pemakaian (di Vast, GPU):
  python prep_composite.py --video clip.mp4 --point 280 420 --out composite.mp4

--point X Y = koordinat klik pada orang di frame pertama (pada resolusi
setelah resize; lihat frames/00000.jpg kalau perlu).
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import torch


def run(cmd):
    print("+", " ".join(map(str, cmd)))
    subprocess.run(cmd, check=True)


def extract_frames(video, frames_dir, fps, width):
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)
    vf = f"fps={fps},scale='min({width},iw)':-2"
    run(["ffmpeg", "-y", "-i", str(video), "-an", "-vf", vf,
         "-q:v", "2", "-start_number", "0", str(frames_dir / "%05d.jpg")])
    files = sorted(frames_dir.glob("*.jpg"))
    if not files:
        sys.exit("Tidak ada frame yang terekstrak.")
    return files


def compute_depth(files, model_id, device):
    from PIL import Image
    from transformers import pipeline

    pipe = pipeline("depth-estimation", model=model_id, device=device)
    depths = []
    for i, f in enumerate(files):
        img = Image.open(f).convert("RGB")
        out = pipe(img)["predicted_depth"]  # tensor (H', W') atau (1, H', W')
        d = out.squeeze().float().cpu().numpy()
        d = cv2.resize(d, (img.width, img.height), interpolation=cv2.INTER_CUBIC)
        depths.append(d.astype(np.float16))
        if i % 20 == 0:
            print(f"depth {i + 1}/{len(files)}")
    arr = np.stack(depths).astype(np.float32)
    # normalisasi GLOBAL satu klip supaya warna tidak berkedip antar-frame
    lo, hi = np.percentile(arr, 1), np.percentile(arr, 99)
    arr = np.clip((arr - lo) / max(hi - lo, 1e-6), 0, 1)
    return (arr * 255).astype(np.uint8)


def compute_masks(frames_dir, n, point, sam_cfg, sam_ckpt, device):
    from sam2.build_sam import build_sam2_video_predictor

    predictor = build_sam2_video_predictor(sam_cfg, sam_ckpt, device=device)
    masks = [None] * n
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        state = predictor.init_state(video_path=str(frames_dir))
        predictor.add_new_points_or_box(
            state, frame_idx=0, obj_id=1,
            points=np.array([point], dtype=np.float32),
            labels=np.array([1], dtype=np.int32),
        )
        for fidx, _ids, logits in predictor.propagate_in_video(state):
            masks[fidx] = (logits[0, 0] > 0.0).cpu().numpy().astype(np.uint8) * 255
    return masks


def composite(files, depth_u8, masks, colormap, dilate, feather, out_dir, face_landmarks=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    k = max(1, dilate * 2 + 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    cmap = getattr(cv2, f"COLORMAP_{colormap.upper()}")
    for i, f in enumerate(files):
        orig = cv2.imread(str(f))
        m = masks[i]
        if m is None:
            m = np.zeros(orig.shape[:2], np.uint8)
        m = cv2.dilate(m, kernel)
        if feather > 0:
            fk = feather * 2 + 1
            m = cv2.GaussianBlur(m, (fk, fk), 0)
        alpha = (m.astype(np.float32) / 255.0)[..., None]
        depth_color = cv2.applyColorMap(depth_u8[i], cmap)
        out = depth_color.astype(np.float32) * alpha + orig.astype(np.float32) * (1 - alpha)
        result = out.astype(np.uint8)
        if face_landmarks is not None:
            from face_guidance import draw_mesh
            result = draw_mesh(result, face_landmarks[i])
        cv2.imwrite(str(out_dir / f"{i:05d}.png"), result)


def encode(out_dir, fps, out_path):
    run(["ffmpeg", "-y", "-framerate", str(fps), "-i", str(out_dir / "%05d.png"),
         "-an", "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p", str(out_path)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--face-guidance", action="store_true",
                    help="tambahkan panduan ekspresi wajah dari video asli")
    ap.add_argument("--video", required=True)
    ap.add_argument("--point", nargs=2, type=float, required=True, metavar=("X", "Y"))
    ap.add_argument("--out", default="composite.mp4")
    ap.add_argument("--work", default="work")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--depth-model", default="depth-anything/Depth-Anything-V2-Large-hf")
    ap.add_argument("--sam-cfg", default="configs/sam2.1/sam2.1_hiera_l.yaml")
    ap.add_argument("--sam-ckpt", default="checkpoints/sam2.1_hiera_large.pt")
    ap.add_argument("--colormap", default="inferno",
                    help="nama colormap OpenCV: inferno, turbo, jet, viridis, ...")
    ap.add_argument("--dilate", type=int, default=6, help="perbesar mask (piksel)")
    ap.add_argument("--feather", type=int, default=3, help="haluskan tepi (piksel)")
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    work = Path(args.work)
    frames_dir = work / "frames"
    comp_dir = work / "composite"

    files = extract_frames(args.video, frames_dir, args.fps, args.width)
    print(f"{len(files)} frame, durasi ~{len(files) / args.fps:.1f} dtk")
    if len(files) > args.fps * 10:
        print("PERINGATAN: lebih dari 10 detik. Omni membatasi input 10 dtk.")

    depth = compute_depth(files, args.depth_model, 0 if device == "cuda" else -1)
    masks = compute_masks(frames_dir, len(files), args.point,
                          args.sam_cfg, args.sam_ckpt, device)
    landmarks = None
    if args.face_guidance:
        from face_guidance import track_faces
        landmarks, report = track_faces(files, args.point, work / "face-tracking.json", args.fps)
        if report["missing_frames"]:
            print(f'PERINGATAN: wajah hilang pada {len(report["missing_frames"])} frame; periksa hasil.')
    composite(files, depth, masks, args.colormap, args.dilate, args.feather, comp_dir,
              face_landmarks=landmarks)
    encode(comp_dir, args.fps, Path(args.out))
    # simpan juga mask video untuk diperiksa
    mdir = work / "mask_preview"
    mdir.mkdir(exist_ok=True)
    for i, m in enumerate(masks):
        cv2.imwrite(str(mdir / f"{i:05d}.png"),
                    m if m is not None else np.zeros_like(depth[0]))
    run(["ffmpeg", "-y", "-framerate", str(args.fps), "-i", str(mdir / "%05d.png"),
         "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(work / "mask_preview.mp4")])
    print(f"Selesai: {args.out}\nPeriksa juga: {work / 'mask_preview.mp4'}")


if __name__ == "__main__":
    main()
