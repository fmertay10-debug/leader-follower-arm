#!/usr/bin/env python3
"""Camera test: preview, real fps, resolution, brightness.

Usage:
  python3 cam_test.py http://192.168.1.23:8080/video     # IP Webcam
  python3 cam_test.py 0                                   # /dev/video0 (Iriun, USB webcam)
Keys:  s = save frame (frame_N.png)   q = quit

Install: pip install opencv-python  (or sudo apt install python3-opencv)
"""
import sys
import time

import cv2


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    cap = cv2.VideoCapture(int(src) if src.isdigit() else src)
    if not cap.isOpened():
        sys.exit("! Could not open camera: %s" % src)

    n_saved = 0
    t0 = time.time()
    frames = 0
    fps = 0.0
    last_t = None
    jitter = []
    while True:
        ok, frame = cap.read()
        if not ok:
            print("! could not read frame")
            break
        now = time.time()
        if last_t is not None:
            jitter.append(now - last_t)
            jitter = jitter[-120:]
        last_t = now
        frames += 1
        if now - t0 >= 1.0:
            fps = frames / (now - t0)
            frames = 0
            t0 = now
        h, w = frame.shape[:2]
        bright = float(frame.mean())
        info = "%dx%d  %.1f fps  brightness=%.0f" % (w, h, fps, bright)
        if len(jitter) > 10:
            m = sum(jitter) / len(jitter)
            sd = (sum((x - m) ** 2 for x in jitter) / len(jitter)) ** 0.5
            info += "  frame interval std=%.0f ms" % (sd * 1000)
        cv2.putText(frame, info, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
        cv2.imshow("camera test (s=save, q=quit)", frame)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k == ord("s"):
            name = "frame_%d.png" % n_saved
            cv2.imwrite(name, frame)
            print("* saved:", name)
            n_saved += 1
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
