#!/usr/bin/env python3
"""Grabs a single frame from the camera and saves it as data/frame.png (opens no window, for testing).

Usage:  python3 tools/grab_frame.py 0 [output.png]
"""
import os
import sys

import cv2

cap = cv2.VideoCapture(int(sys.argv[1]))
for _ in range(15):  # drop a few frames so auto exposure settles
    ok, frame = cap.read()
if not ok:
    sys.exit("! could not read frame")
out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "..", "data", "frame.png")
cv2.imwrite(out, frame)
print("saved:", os.path.abspath(out), frame.shape)
