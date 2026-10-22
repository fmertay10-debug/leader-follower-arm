#!/usr/bin/env python3
"""Data collection: home -> place dot -> START -> touch -> STOP -> save/discard -> home ...

Usage:  python3 tools/record.py 0 [--port /dev/ttyACM0] [--out data/episodes]
firmware/arm_rec must be uploaded to the OpenCM. Run from an Ubuntu terminal (not the VS Code snap terminal).

Keys (with the camera window focused) and button:
  SPACE / button   in READY: start recording (leader is released, HOLD THE LEADER FIRST!)
                   in REC: stop (both arms return home right away, you can let go of the leader)
  ENTER / button   save the episode
  d / BACKSPACE    discard the episode
  h                go home (if something gets stuck)
  q / ESC          quit (arms stay at home with torque on)

Each episode is written under data/episodes/ep_NNNN/:
  data.npz   t_pc, t_ms, state, leader, goal, follower, load, volt, temp (serial, ~50-100 Hz), frame_t
  video.mp4  raw camera footage (matched via frame_t)
  meta.json  dot (u, v) and pixel position, table homography, duration, voltage ...
Battery: warning below VOLT_WARN, no new episode below VOLT_STOP.
Temperature: warning when the hottest servo is above TEMP_WARN, no new episode above TEMP_STOP (wait for it to cool).
"""
import argparse
import datetime
import glob
import json
import os
import shutil
import statistics
import sys
import time
from collections import deque

import cv2
import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "vision"))
sys.path.insert(0, os.path.join(ROOT, "robot"))
from opencm import ST_HOLD, ST_READY, STATE_NAMES, OpenCM  # noqa: E402
from table_dot import CFG as DOT_CFG, DEFAULT as DOT_DEFAULT, TableTracker, find_dot  # noqa: E402

VOLT_WARN = 11.0      # for a 3S LiPo; change with --volt-warn
VOLT_STOP = 10.5
TEMP_WARN = 50        # servo temperature [C]; AX-12A's own limit is 70, firmware emergency stop 60
TEMP_STOP = 55        # above this no new episode starts (let the arms cool down)
DOT_FRAMES = 15       # dot = median of this many frames
DOT_MAX_STD = 0.01    # if (u, v) jitter is larger than this, the dot counts as unstable
MIN_REC_S = 0.5       # guards against an accidental double press
GRID = 4              # coverage map: table split into GRID x GRID cells

GREEN, YELLOW, RED, WHITE, BLUE, GRAY = (0, 220, 0), (0, 220, 255), (0, 0, 255), (255, 255, 255), (255, 160, 0), (140, 140, 140)


def load_episodes(out):
    eps = []
    for m in sorted(glob.glob(os.path.join(out, "ep_*", "meta.json"))):
        try:
            eps.append(json.load(open(m)))
        except (OSError, ValueError):
            print("! could not read:", m)
    return eps


def next_index(out):
    idx = [int(os.path.basename(d)[3:]) for d in glob.glob(os.path.join(out, "ep_*")) if os.path.basename(d)[3:].isdigit()]
    return max(idx, default=0) + 1


def to_px(Hinv, u, v):
    p = cv2.perspectiveTransform(np.float32([[[u, v]]]), Hinv)[0, 0]
    return int(round(p[0])), int(round(p[1]))


def draw_coverage(img, H, eps):
    """GRID x GRID cells on the table (episode count in each cell) and the earlier dots."""
    if H is None:
        return
    Hinv = np.linalg.inv(H)
    counts = np.zeros((GRID, GRID), int)
    for e in eps:
        u, v = e["dot_uv"]
        if 0 <= u < 1 and 0 <= v < 1:
            counts[int(v * GRID), int(u * GRID)] += 1
    for r in range(GRID):
        for c in range(GRID):
            corners = [(c / GRID, r / GRID), ((c + 1) / GRID, r / GRID), ((c + 1) / GRID, (r + 1) / GRID), (c / GRID, (r + 1) / GRID)]
            pts = np.int32([to_px(Hinv, u, v) for u, v in corners])
            cv2.polylines(img, [pts], True, GRAY, 1)
            cx, cy = to_px(Hinv, (c + 0.5) / GRID, (r + 0.5) / GRID)
            n = counts[r, c]
            cv2.putText(img, str(n), (cx - 8, cy + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, RED if n == 0 else GRAY, 2)
    for e in eps:
        cv2.circle(img, to_px(Hinv, *e["dot_uv"]), 4, BLUE, -1)


def text(img, s, y, color=WHITE, scale=0.6):
    cv2.putText(img, s, (10, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4)
    cv2.putText(img, s, (10, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2)


class Recorder:
    def __init__(self, args):
        self.args = args
        self.out = os.path.abspath(args.out)
        os.makedirs(self.out, exist_ok=True)
        self.eps = load_episodes(self.out)
        self.msg = deque(maxlen=3)
        self.arm = OpenCM(args.port, on_msg=self.on_msg)
        self.arm.stream(True)
        self.cap = cv2.VideoCapture(int(args.cam) if args.cam.isdigit() else args.cam)
        if not self.cap.isOpened():
            sys.exit("! Could not open camera: %s" % args.cam)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
        self.dot_p = dict(DOT_DEFAULT)
        if os.path.exists(DOT_CFG):
            self.dot_p.update(json.load(open(DOT_CFG)))
        self.tracker = TableTracker()
        self.dots = deque(maxlen=DOT_FRAMES)
        self.volts = deque()
        self.mode = "HOME"
        self.ep = None
        self.arm.send("h")

    def on_msg(self, line):
        print(line)
        self.msg.append(line.lstrip("# "))

    # ---- measurements ----
    def volt(self):
        """Median of the last 3 s (voltage dips momentarily when the servos are loaded)."""
        s = self.arm.latest
        now = time.time()
        if s is not None:
            self.volts.append((now, s.volt))
        while self.volts and now - self.volts[0][0] > 3:
            self.volts.popleft()
        return statistics.median(v for _, v in self.volts) if self.volts else None

    def dot(self):
        """Stable dot: ((u, v), (px, py)) or (None, reason)."""
        ok = [d for d in self.dots if d is not None]
        if len(ok) < DOT_FRAMES * 2 // 3:
            return None, "dot not visible"
        a = np.array(ok)
        if a[:, :2].std(axis=0).max() > DOT_MAX_STD:
            return None, "dot unstable"
        m = np.median(a, axis=0)
        if not (0 <= m[0] <= 1 and 0 <= m[1] <= 1):
            return None, "dot outside the markers"
        return ((float(m[0]), float(m[1])), (float(m[2]), float(m[3]))), ""

    # ---- episode ----
    def start(self, frame):
        dot, why = self.dot()
        v = self.volt()
        if self.arm.state() != ST_READY:
            return self.on_msg("arm is not READY")
        if dot is None:
            return self.on_msg("cannot start: " + why)
        if v is not None and v < self.args.volt_stop:
            return self.on_msg("cannot start: BATTERY %.1f V < %.1f V, charge it" % (v, self.args.volt_stop))
        s = self.arm.latest
        if s is not None and s.temp >= self.args.temp_stop:
            return self.on_msg("cannot start: ID %d too HOT (%d C >= %d C), let it cool down" % (s.temp_id, s.temp, self.args.temp_stop))
        idx = next_index(self.out)
        tmp = os.path.join(self.out, "_tmp_ep_%04d" % idx)
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        h, w = frame.shape[:2]
        self.ep = {
            "idx": idx, "tmp": tmp, "t0": time.time(), "frame_t": [],
            "writer": cv2.VideoWriter(os.path.join(tmp, "video.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (w, h)),
            "meta": {
                "episode": idx,
                "dot_uv": list(dot[0]), "dot_px": list(dot[1]),
                "table_H": self.tracker.H.tolist(),
                "markers_px": {str(i): c.tolist() for i, c in self.tracker.centers.items()},
                "start_time": datetime.datetime.now().isoformat(timespec="seconds"),
                "frame_size": [w, h], "volt_start": v, "temp_start": s.temp if s else None,
            },
        }
        self.arm.start_capture()
        self.arm.send("g")
        self.mode = "REC"
        self.on_msg("REC ep_%04d  dot u=%.3f v=%.3f" % (idx, *dot[0]))

    def stop(self, reason=""):
        ep = self.ep
        ep["rows"] = self.arm.stop_capture()
        ep["writer"].release()
        ep["meta"]["duration_s"] = round(time.time() - ep["t0"], 2)
        ep["meta"]["volt_end"] = self.volt()
        if self.arm.latest is not None:
            ep["meta"]["temp_end"] = self.arm.latest.temp
        if reason:
            ep["meta"]["warning"] = reason
        self.arm.send("h")   # leader gets torque, you can let go
        self.mode = "CONFIRM"
        n = len(ep["rows"])
        self.on_msg("done: %.1f s, %d samples (%.0f Hz), %d frames%s. ENTER/button = save, d = discard" % (
            ep["meta"]["duration_s"], n, n / max(ep["meta"]["duration_s"], 1e-3), len(ep["frame_t"]),
            ("  ! " + reason) if reason else ""))

    def save(self):
        ep, rows = self.ep, self.ep["rows"]
        if not rows:
            self.on_msg("no samples, not saved")
            return self.discard()
        arr = lambda f: np.array([getattr(r, f) for r in rows])  # noqa: E731
        np.savez_compressed(
            os.path.join(ep["tmp"], "data.npz"),
            t_pc=arr("t_pc"), t_ms=arr("t_ms"), state=arr("state"),
            leader=arr("leader").astype(np.int16), goal=arr("goal").astype(np.int16),
            follower=arr("follower").astype(np.int16), load=arr("load").astype(np.int16),
            volt=arr("volt").astype(np.float32), temp=arr("temp").astype(np.int16), frame_t=np.array(ep["frame_t"]))
        m = ep["meta"]
        m.update(n_samples=len(rows), n_frames=len(ep["frame_t"]),
                 rate_hz=round(len(rows) / max(m["duration_s"], 1e-3), 1))
        json.dump(m, open(os.path.join(ep["tmp"], "meta.json"), "w"), indent=1)
        final = os.path.join(self.out, "ep_%04d" % ep["idx"])
        os.rename(ep["tmp"], final)
        self.eps.append(m)
        self.on_msg("SAVED %s  (%d episodes in total)" % (os.path.basename(final), len(self.eps)))
        self.ep = None
        self.mode = "HOME"

    def discard(self):
        if self.ep:
            self.arm.stop_capture()
            self.ep["writer"].release()
            shutil.rmtree(self.ep["tmp"], ignore_errors=True)
            self.on_msg("discarded")
        self.ep = None
        self.mode = "HOME"

    # ---- loop ----
    def handle(self, key, button, frame):
        go = key == 32 or button
        if key == ord("h") and self.mode != "REC":
            self.arm.send("h")
            self.mode = "HOME" if self.mode != "CONFIRM" else self.mode
        elif self.mode == "READY" and go:
            self.start(frame)
        elif self.mode == "REC" and go and time.time() - self.ep["t0"] > MIN_REC_S:
            self.stop()
        elif self.mode == "CONFIRM":
            if key in (13, 10) or button:
                self.save()
            elif key in (ord("d"), 8):
                self.discard()

    def run(self):
        win = "record (SPACE/button = start/stop, ENTER = save, d = discard, q = quit)"
        cv2.namedWindow(win)
        while True:
            ok, frame = self.cap.read()
            t_frame = time.time()
            if not ok:
                self.on_msg("! could not read camera frame")
                break
            if self.mode == "REC":
                self.ep["writer"].write(frame)
                self.ep["frame_t"].append(t_frame)

            seen = self.tracker.update(frame)
            if self.mode in ("HOME", "READY"):
                d = None
                if self.tracker.H is not None:
                    dot, _ = find_dot(frame, self.dot_p, self.tracker.quad())
                    if dot:
                        u, v = self.tracker.to_table(dot[0], dot[1])
                        d = (u, v, dot[0], dot[1])
                self.dots.append(d)

            st = self.arm.state()
            if self.mode == "HOME" and st == ST_READY:
                self.mode = "READY"
                self.dots.clear()
            if self.mode == "REC" and st == ST_HOLD:
                self.stop("follower FROZE (tracking error/jump), probably discard")

            # ---- screen ----
            img = frame.copy()
            draw_coverage(img, self.tracker.H, self.eps)
            q = self.tracker.quad()
            if q is not None:
                cv2.polylines(img, [q], True, (0, 200, 255), 1)
            dot, why = self.dot() if self.mode in ("HOME", "READY") else (None, "")
            if dot:
                cv2.circle(img, (int(dot[1][0]), int(dot[1][1])), 14, GREEN, 2)
            v = self.volt()
            vcol = GREEN if v is None or v >= self.args.volt_warn else (YELLOW if v >= self.args.volt_stop else RED)
            y = 26
            text(img, "%s   arm: %s   episodes: %d" % (self.mode, STATE_NAMES[st] if st is not None else "?", len(self.eps)), y, scale=0.7)
            y += 28
            text(img, "battery: %s" % ("%.1f V" % v if v is not None else "?") + (
                "  CHARGE IT!" if v is not None and v < self.args.volt_stop else ("  dropping" if v is not None and v < self.args.volt_warn else "")), y, vcol)
            y += 26
            ls = self.arm.latest
            if ls is not None:
                tcol = GREEN if ls.temp < self.args.temp_warn else (YELLOW if ls.temp < self.args.temp_stop else RED)
                text(img, "temperature: %d C (ID %d)" % (ls.temp, ls.temp_id) + (
                    "  TOO HOT, wait!" if ls.temp >= self.args.temp_stop else ("  heating up" if ls.temp >= self.args.temp_warn else "")), y, tcol)
                y += 26
            if self.mode == "READY":
                text(img, ("dot OK u=%.3f v=%.3f  -> HOLD THE LEADER, SPACE/button" % dot[0]) if dot else ("! " + why), y, GREEN if dot else YELLOW)
            elif self.mode == "HOME":
                text(img, "going home... (place the dot)", y, YELLOW)
            elif self.mode == "REC":
                text(img, "REC %.1f s  -> touch, lift, SPACE/button" % (time.time() - self.ep["t0"]), y, RED)
                cv2.circle(img, (img.shape[1] - 30, 30), 12, RED, -1)
            elif self.mode == "CONFIRM":
                text(img, "ENTER/button = SAVE    d = DISCARD", y, YELLOW)
            if len(seen) < 4:
                y += 26
                text(img, "%d/4 markers visible" % len(seen), y, YELLOW)
            msgs = list(self.msg)   # the serial reader thread may append at the same time: iterate over a copy
            for i, m in enumerate(msgs):
                text(img, m[:90], img.shape[0] - 14 - 24 * (len(msgs) - 1 - i), GRAY, 0.5)
            cv2.imshow(win, img)

            key = cv2.waitKey(1) & 0xFF
            button = False
            while not self.arm.events.empty():
                button = button or self.arm.events.get_nowait() == "B"
            if key in (ord("q"), 27):
                break
            self.handle(key, button, frame)

        if self.mode == "REC":
            self.arm.stop_capture()
            self.ep["writer"].release()
            shutil.rmtree(self.ep["tmp"], ignore_errors=True)
            self.arm.send("h")
            print("* the episode being recorded was discarded, arms are going home")
        self.arm.stream(False)
        self.arm.close()
        self.cap.release()
        cv2.destroyAllWindows()
        print("* quit. The arms may still be holding with torque on; send 'q' to the OpenCM or cut the power to release them.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cam", help="camera number or URL (e.g. 0)")
    ap.add_argument("--port", default=None, help="OpenCM serial port (default: first /dev/ttyACM*)")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "episodes"))
    ap.add_argument("--volt-warn", type=float, default=VOLT_WARN)
    ap.add_argument("--volt-stop", type=float, default=VOLT_STOP)
    ap.add_argument("--temp-warn", type=int, default=TEMP_WARN)
    ap.add_argument("--temp-stop", type=int, default=TEMP_STOP)
    Recorder(ap.parse_args()).run()


if __name__ == "__main__":
    main()
