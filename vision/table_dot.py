#!/usr/bin/env python3
"""Finds the ArUco markers at the table corners and gives the TABLE coordinate (0-1) of the white dot.

Usage:  python3 table_dot.py 0
Keys:  q = quit   s = save thresholds   m = mask
Marker layout (as seen from the camera): ID0 top left, ID1 top right, ID2 bottom right, ID3 bottom left.
Output: (u, v)  u: left to right 0..1,  v: top to bottom 0..1  (relative to the marker centers)
"""
import json
import os
import sys

import cv2
import numpy as np

from common import MarkerDetector

CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "table_dot.json")
DEFAULT = {"s_max": 60, "v_min": 170, "min_area": 300, "max_area": 8000, "circ_min": 70}
UNIT = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])


class TableTracker:
    """Builds a homography (pixel -> table 0..1) from the markers; keeps the last value if the arm hides a marker."""

    def __init__(self):
        self.det = MarkerDetector()
        self.centers = {}  # id -> last known center (pixels)
        self.H = None

    def update(self, frame):
        seen = self.det.detect(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        for i in range(4):
            if i in seen:
                self.centers[i] = seen[i].mean(axis=0)
        if len(self.centers) == 4:
            src = np.float32([self.centers[i] for i in range(4)])
            self.H = cv2.getPerspectiveTransform(src, UNIT)
        return seen

    def quad(self):
        if len(self.centers) < 4:
            return None
        return np.int32([self.centers[i] for i in range(4)])

    def to_table(self, x, y):
        if self.H is None:
            return None
        p = cv2.perspectiveTransform(np.float32([[[x, y]]]), self.H)[0, 0]
        return float(p[0]), float(p[1])


def find_dot(frame, p, quad=None):
    """Roundest white blob inside the table: ((cx, cy, area), mask) or (None, mask)."""
    hsv = cv2.cvtColor(cv2.GaussianBlur(frame, (5, 5), 0), cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, p["v_min"]), (180, p["s_max"], 255))
    if quad is not None:
        roi = np.zeros(mask.shape, np.uint8)
        cv2.fillConvexPoly(roi, cv2.convexHull(quad), 255)
        mask &= roi
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)  # removes the thin table lines
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        a = cv2.contourArea(c)
        per = cv2.arcLength(c, True)
        if not p["min_area"] <= a <= p["max_area"] or per == 0:
            continue
        circ = 4 * np.pi * a / per ** 2  # round = 1; arm/cable pieces score low
        if circ * 100 < p["circ_min"]:
            continue
        if best is None or a > best[1]:
            m = cv2.moments(c)
            best = ((m["m10"] / m["m00"], m["m01"] / m["m00"], a), a)
    return (best[0] if best else None), mask


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    cap = cv2.VideoCapture(int(src) if src.isdigit() else src)
    if not cap.isOpened():
        sys.exit("! Could not open camera: %s" % src)

    p = dict(DEFAULT)
    if os.path.exists(CFG):
        p.update(json.load(open(CFG)))
    win = "table + dot (q=quit, s=save, m=mask)"
    cv2.namedWindow(win)
    for key, mx in (("s_max", 255), ("v_min", 255), ("min_area", 3000), ("max_area", 20000), ("circ_min", 100)):
        cv2.createTrackbar(key, win, p[key], mx, lambda v: None)

    tracker = TableTracker()
    show_mask = False
    while True:
        ok, frame = cap.read()
        if not ok:
            print("! could not read frame")
            break
        for key in p:
            p[key] = cv2.getTrackbarPos(key, win)

        seen = tracker.update(frame)
        for i, c in seen.items():
            cv2.polylines(frame, [c.astype(np.int32)], True, (255, 200, 0), 2)
            cv2.putText(frame, str(i), tuple(c[0].astype(int)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 200, 0), 2)
        q = tracker.quad()
        if q is not None:
            cv2.polylines(frame, [q], True, (0, 200, 255), 1)

        dot, mask = find_dot(frame, p, q)
        if tracker.H is None:
            txt = "markers: %d/4 - no table yet" % len(tracker.centers)
        elif dot:
            cx, cy, _ = dot
            cv2.circle(frame, (int(round(cx)), int(round(cy))), 12, (0, 255, 0), 2)
            u, v = tracker.to_table(cx, cy)
            txt = "dot on table: u=%.3f v=%.3f   (%d/4 markers visible)" % (u, v, len(seen))
        else:
            txt = "NO dot   (%d/4 markers visible)" % len(seen)
        cv2.putText(frame, txt, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
        cv2.imshow(win, frame)
        if show_mask:
            cv2.imshow("mask", mask)

        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k == ord("s"):
            json.dump(p, open(CFG, "w"), indent=1)
            print("* saved:", CFG, p)
        if k == ord("m"):
            show_mask = not show_mask
            if not show_mask:
                cv2.destroyWindow("mask")
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
