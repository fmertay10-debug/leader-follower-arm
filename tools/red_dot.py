#!/usr/bin/env python3
"""Red dot tracking: finds the largest red blob with an HSV threshold.

Usage:
  python3 red_dot.py 0
Keys:  q = quit   s = save thresholds to red_dot.json   m = toggle mask window
Thresholds are tuned live with trackbars; a saved red_dot.json is loaded at startup.
"""
import json
import os
import sys

import cv2
import numpy as np

CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "red_dot.json")
DEFAULT = {"h_tol": 10, "s_min": 120, "v_min": 70, "min_area": 30}


def red_mask(hsv, p):
    # Red sits near both 0 and 180 in HSV, so two ranges are needed
    lo1 = np.array([0, p["s_min"], p["v_min"]])
    hi1 = np.array([p["h_tol"], 255, 255])
    lo2 = np.array([180 - p["h_tol"], p["s_min"], p["v_min"]])
    hi2 = np.array([180, 255, 255])
    m = cv2.inRange(hsv, lo1, hi1) | cv2.inRange(hsv, lo2, hi2)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)


def find_dot(frame, p):
    """Returns (cx, cy, area) or None; pixel coordinates with sub-pixel precision."""
    hsv = cv2.cvtColor(cv2.GaussianBlur(frame, (5, 5), 0), cv2.COLOR_BGR2HSV)
    mask = red_mask(hsv, p)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = [c for c in cnts if cv2.contourArea(c) >= p["min_area"]]
    if not cnts:
        return None, mask
    c = max(cnts, key=cv2.contourArea)
    m = cv2.moments(c)
    return (m["m10"] / m["m00"], m["m01"] / m["m00"], m["m00"]), mask


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

    win = "red dot (q=quit, s=save, m=mask)"
    cv2.namedWindow(win)
    for key, mx in (("h_tol", 30), ("s_min", 255), ("v_min", 255), ("min_area", 2000)):
        cv2.createTrackbar(key, win, p[key], mx, lambda v: None)

    show_mask = False
    while True:
        ok, frame = cap.read()
        if not ok:
            print("! could not read frame")
            break
        for key in p:
            p[key] = cv2.getTrackbarPos(key, win)

        dot, mask = find_dot(frame, p)
        if dot:
            cx, cy, area = dot
            cv2.circle(frame, (int(round(cx)), int(round(cy))), 10, (0, 255, 0), 2)
            cv2.drawMarker(frame, (int(round(cx)), int(round(cy))), (0, 255, 0), cv2.MARKER_CROSS, 20, 1)
            txt = "dot: (%.1f, %.1f)  area=%.0f" % (cx, cy, area)
        else:
            txt = "NO dot"
        cv2.putText(frame, txt, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
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
