#!/usr/bin/env python3
"""Runs the model on the real arm: the camera finds the dot -> the model predicts the touch pose -> the arm touches.

Usage:
  .venv/bin/python tools/run_model.py 0                 # camera + real arm
  .venv/bin/python tools/run_model.py 0 --speed 0.5     # slower (durations doubled)
  .venv/bin/python tools/run_model.py --uv 0.3 0.5 --dry   # only print the plan, without touching the arm or camera

Flow: home -> approach (above the table) -> touch slowly -> hold -> up -> home.
Keys:  SPACE / button = start (in READY)   x / ESC = EMERGENCY: follower stops in place   h = home   q = quit
If stopped, the arm STAYS IN PLACE (torque on); press 'h' to send it home. Keys work while the camera window is focused.
Safety: rejects targets far outside the training data; every joint is clipped to FOL_MIN/MAX;
the firmware applies a speed limit in PC mode (SLEW_PC_UPS); if commands from the PC stop, the follower stops.
Do the first runs with --speed 0.5 and keep your hand on the emergency stop key.
"""
import argparse
import datetime
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "vision"))
sys.path.insert(0, os.path.join(ROOT, "robot"))

HOME = np.array([512, 512, 512, 512, 365], float)   # must match HOME_F in the firmware
FOL_MIN, FOL_MAX = 100, 923                          # same as the firmware
MAX_NN_DIST = 0.12        # reject if the nearest training point is farther than this (table units)
T_APPROACH, T_LOWER, T_HOLD, T_RAISE, T_RETURN = 6.0, 4.0, 0.7, 3.0, 6.0   # s (scaled by --speed)
RATE = 40.0               # G line send rate [Hz]


# ---- model: polynomial (degree 3) ridge, (u, v) -> joints 1..4 -------------------------------------
def feats(uv, deg=3):
    uv = np.atleast_2d(uv)
    u, v = uv[:, 0], uv[:, 1]
    cols = [np.ones_like(u)]
    for d in range(1, deg + 1):
        for i in range(d + 1):
            cols.append(u ** (d - i) * v ** i)
    return np.stack(cols, 1)


class PoseModel:
    def __init__(self, npz, lam=1e-3):
        D = np.load(npz)
        self.uv = D["uv"].astype(np.float64)
        self.touch = D["touch"].astype(np.float64)
        self.delta = D["pre_delta"].astype(np.float64)[:4]
        X = feats(self.uv)
        self.W = np.linalg.solve(X.T @ X + lam * np.eye(X.shape[1]), X.T @ self.touch[:, :4])
        self.roll = float(np.median(self.touch[:, 4]))
        self.lo, self.hi = self.touch.min(0), self.touch.max(0)

    def nn_dist(self, uv):
        return float(np.linalg.norm(self.uv - np.asarray(uv), axis=1).min())

    def touch_pose(self, uv):
        p = np.append((feats(uv) @ self.W)[0], self.roll)
        return np.clip(p, FOL_MIN, FOL_MAX)

    def approach_pose(self, uv):
        p = self.touch_pose(uv)
        p[:4] += self.delta
        return np.clip(p, FOL_MIN, FOL_MAX)


def min_jerk(a, b, T, rate=RATE):
    n = max(2, int(T * rate))
    s = np.linspace(0, 1, n)[:, None]
    s = 10 * s ** 3 - 15 * s ** 4 + 6 * s ** 5
    return a + (b - a) * s


def plan(model, uv, speed):
    touch, appr = model.touch_pose(uv), model.approach_pose(uv)
    k = 1.0 / speed
    segs = [("approach", HOME, appr, T_APPROACH * k), ("touch", appr, touch, T_LOWER * k),
            ("hold", touch, touch, T_HOLD * k), ("lift", touch, appr, T_RAISE * k),
            ("return", appr, HOME, T_RETURN * k)]
    return touch, appr, segs


def describe(model, uv, touch, appr, segs):
    print("dot (u, v) = (%.3f, %.3f)   nearest training point: %.3f" % (uv[0], uv[1], model.nn_dist(uv)))
    print("approach pose :", appr.round(0))
    print("touch pose    :", touch.round(0))
    print("touch range in training: min %s  max %s" % (model.lo.round(0), model.hi.round(0)))
    print("total duration: %.1f s" % sum(s[3] for s in segs))
    for name, a, b, T in segs:
        print("  %-9s %.1f s   largest joint move %.0f units" % (name, T, np.abs(b - a).max()))


def check(model, uv):
    if model.nn_dist(uv) > MAX_NN_DIST:
        return "dot is outside the training data (nearest sample %.2f > %.2f)" % (model.nn_dist(uv), MAX_NN_DIST)
    return None


# ---- execution ----------------------------------------------------------------------------
def execute(arm, segs, abort):
    """Sends the trajectory at RATE Hz. Stops if abort() returns True. Returns: (ok, log, message)."""
    from opencm import ST_HOLD, ST_PC
    log = []
    arm.send("p")
    t0 = time.time()
    while arm.state() != ST_PC:
        if time.time() - t0 > 3:
            return False, log, "could not enter PC mode"
        time.sleep(0.02)
    for name, a, b, T in segs:
        traj = min_jerk(a, b, T)
        t_seg = time.time()
        for i, g in enumerate(traj):
            if abort():
                arm.send("x")
                return False, log, "stopped by user (%s)" % name
            if arm.state() != ST_PC:
                return False, log, "arm left PC mode (%s): tracking/read error? state=%s" % (name, arm.state())
            arm.send_goal(g)
            s = arm.latest
            if s is not None:
                log.append((time.time(), name, *g, *s.follower))
            time.sleep(max(0, t_seg + (i + 1) / RATE - time.time()))
    return True, log, ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cam", nargs="?", help="camera number (not needed if --uv is given)")
    ap.add_argument("--uv", type=float, nargs=2, metavar=("U", "V"), help="give the dot by hand (no camera)")
    ap.add_argument("--dry", action="store_true", help="do not touch the arm, only print the plan")
    ap.add_argument("--speed", type=float, default=1.0, help="1 = normal, 0.5 = half speed (safer)")
    ap.add_argument("--port", default=None)
    ap.add_argument("--data", default=os.path.join(ROOT, "data", "processed", "dataset.npz"))
    args = ap.parse_args()
    if args.speed <= 0:
        sys.exit("! --speed must be > 0")

    model = PoseModel(args.data)
    if args.dry:
        if not args.uv:
            sys.exit("! --dry needs --uv U V")
        why = check(model, args.uv)
        touch, appr, segs = plan(model, args.uv, args.speed)
        describe(model, args.uv, touch, appr, segs)
        print("-> REJECTED: " + why if why else "-> accepted")
        return

    import cv2
    from opencm import OpenCM, ST_READY, STATE_NAMES
    from table_dot import CFG as DOT_CFG, DEFAULT as DOT_DEFAULT, TableTracker, find_dot

    arm = OpenCM(args.port)
    arm.stream(True)
    cap = None
    if args.cam:
        cap = cv2.VideoCapture(int(args.cam) if args.cam.isdigit() else args.cam)
    if not args.uv and (cap is None or not cap.isOpened()):
        sys.exit("! Could not open camera")
    dot_p = dict(DOT_DEFAULT)
    if os.path.exists(DOT_CFG):
        dot_p.update(json.load(open(DOT_CFG)))
    tracker = TableTracker()
    dots = []
    win = "model (SPACE/button = start, x = EMERGENCY STOP, q = quit)"
    cv2.namedWindow(win)
    arm.send("h")
    out_dir = os.path.join(ROOT, "data", "runs")
    os.makedirs(out_dir, exist_ok=True)
    running = False
    last_msg = "going home..."
    runs = 0

    def abort():
        cv2.waitKey(1)
        k = cv2.waitKey(1) & 0xFF
        return k in (ord("x"), 27)

    while True:
        ok, frame = cap.read() if cap is not None else (False, None)
        if not ok:
            frame = np.zeros((480, 640, 3), np.uint8)
        seen = tracker.update(frame)
        uv = None
        if args.uv:
            uv = np.array(args.uv)
        elif tracker.H is not None:
            dot, _ = find_dot(frame, dot_p, tracker.quad())
            dots.append(tracker.to_table(dot[0], dot[1]) if dot else None)
            dots[:] = dots[-15:]
            good = [d for d in dots if d is not None]
            if len(good) >= 10 and np.std(good, axis=0).max() < 0.01:
                uv = np.median(good, axis=0)
        st = arm.state()
        ready = st == ST_READY
        why = check(model, uv) if uv is not None else "no dot"

        img = frame.copy()
        q = tracker.quad()
        if q is not None:
            cv2.polylines(img, [q], True, (0, 200, 255), 1)
        lines = ["arm: %s" % (STATE_NAMES[st] if st is not None else "?")]
        if uv is not None:
            lines.append("dot u=%.3f v=%.3f" % tuple(uv))
        lines.append(("READY - SPACE/button: start" if ready and not why else (why or "arm is going home")) if not running else "RUNNING")
        lines.append(last_msg)
        for i, s in enumerate(lines):
            cv2.putText(img, s, (10, 26 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
            cv2.putText(img, s, (10, 26 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0) if ready and not why else (0, 220, 255), 2)
        cv2.imshow(win, img)

        k = cv2.waitKey(1) & 0xFF
        button = False
        while not arm.events.empty():
            button = button or arm.events.get_nowait() == "B"
        if k in (ord("q"), 27):
            break
        if k == ord("h") and not running:
            arm.send("h")
            last_msg = "going home..."
        if (k == 32 or button) and ready and uv is not None and not why and not running:
            touch, appr, segs = plan(model, uv, args.speed)
            describe(model, uv, touch, appr, segs)
            running = True
            done, log, msg = execute(arm, segs, abort)
            if done:
                arm.send("h")   # success: go home. If stopped, the arm stays in place and you start homing with 'h'
            running = False
            runs += 1
            last_msg = "done" if done else "STOPPED: " + msg
            print("*", last_msg)
            np.savez_compressed(os.path.join(out_dir, "run_%s.npz" % datetime.datetime.now().strftime("%m%d_%H%M%S")),
                                uv=uv, touch=touch, approach=appr, ok=done, msg=msg,
                                log=np.array([(r[0],) + tuple(r[2:]) for r in log]) if log else np.zeros((0, 11)))
            dots.clear()

    arm.stream(False)
    arm.close()
    if cap is not None:
        cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
