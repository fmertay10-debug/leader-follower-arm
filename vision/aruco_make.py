#!/usr/bin/env python3
"""Generates the 4 corner markers (ID 0-3) and saves them as aruco_markers.png (print on A4).

Usage:  python3 aruco_make.py
Print at 100% scale (fit-to-page OFF). Each marker is about 5 cm.
Cut the markers out and place them at the table corners:
   ID0 = top left   ID1 = top right   ID2 = bottom right   ID3 = bottom left   (as seen from the camera)
Do not cut off the white border (quiet zone); it is required for detection on a dark table.
"""
import cv2
import numpy as np

from common import DICT_ID, make_marker

PX = 300      # marker side (pixels)
PAD = 60      # white border (pixels)

tiles = []
for i in range(4):
    m = make_marker(i, PX)
    m = cv2.copyMakeBorder(m, PAD, PAD, PAD, PAD, cv2.BORDER_CONSTANT, value=255)
    cv2.putText(m, "ID %d" % i, (PAD, PAD - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.9, 0, 2)
    tiles.append(m)
sheet = np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])])
cv2.imwrite("aruco_markers.png", sheet)
print("written: aruco_markers.png  (dictionary id=%d, %dx%d px)" % (DICT_ID, sheet.shape[1], sheet.shape[0]))
