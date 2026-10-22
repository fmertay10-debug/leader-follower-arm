"""Concatenates all episode videos, speeds them up to fit the length of your own video
and places the two side by side. Uses only OpenCV (no ffmpeg needed).

Usage:
  python3 tools/sidebyside.py --self "/home/mert/Downloads/WhatsApp Video 2026-10-20 at 9.51.25 PM.mp4"
"""
import argparse
import glob
import os

import cv2
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--self", dest="selfvid", required=True, help="the video you recorded of yourself")
ap.add_argument("--episodes", default="data/episodes/*/video.mp4")
ap.add_argument("--out", default="data/sidebyside.mp4")
ap.add_argument("--duration", type=float, default=None,
                help="output length (s); defaults to the length of your own video. If longer, your video is slowed down")
ap.add_argument("--height", type=int, default=720)
ap.add_argument("--self-left", action="store_true", help="put your own video on the left")
args = ap.parse_args()

files = sorted(glob.glob(args.episodes))
counts = []
for f in files:
    c = cv2.VideoCapture(f)
    counts.append(int(c.get(cv2.CAP_PROP_FRAME_COUNT)))
    c.release()
total = sum(counts)

sv = cv2.VideoCapture(args.selfvid)
fps = sv.get(cv2.CAP_PROP_FPS)
ms = int(sv.get(cv2.CAP_PROP_FRAME_COUNT))  # frame count of your own video
m = round(args.duration * fps) if args.duration else ms  # output frame count
print(f"{len(files)} videos, {total} frames -> {m} frames ({total / m:.1f}x speed)")

# pick m evenly spaced frames from the concatenated video
want = np.linspace(0, total - 1, m).astype(int)
selfidx = np.linspace(0, ms - 1, m).round().astype(int)  # own-video frame for each output frame
starts = np.concatenate([[0], np.cumsum(counts)])


def fit(img, h):
    return cv2.resize(img, (round(img.shape[1] * h / img.shape[0]), h))


out = None
H = args.height
wi = 0  # index into want
sread = 0  # number of frames read from your own video
for f, s, n in zip(files, starts, counts):
    cap = cv2.VideoCapture(f)
    for i in range(n):
        if wi >= m:
            break
        if want[wi] != s + i:
            cap.grab()
            continue
        ok, fr = cap.read()
        if not ok:
            break
        # if the same frame is wanted more than once (rare), reuse it
        while wi < m and want[wi] == s + i:
            while sread <= selfidx[wi]:
                ok2, sf = sv.read()
                if not ok2:
                    break
                sread += 1
            a, b = fit(fr, H), fit(sf, H)
            pair = (b, a) if args.self_left else (a, b)
            frame = np.hstack(pair)
            if out is None:
                os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
                out = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), fps,
                                      (frame.shape[1], frame.shape[0]))
            out.write(frame)
            wi += 1
    cap.release()
    print(f"\r{os.path.basename(os.path.dirname(f))} {wi}/{m}", end="", flush=True)

out.release()
print("\nwritten:", args.out)
