#!/usr/bin/env python3
"""Cleans the episodes, extracts the touch pose, writes data/processed/dataset.npz and plots.

Usage:  .venv/bin/python train/prepare.py
Dropped: low voltage (< MIN_VOLT), episodes with a warning, too short/long episodes, episodes still moving at the touch point.
Touch pose = median of the follower positions over the LAST samples of the episode (an episode ends at the touch).
"""
import glob
import json
import os

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
EPS = os.path.join(ROOT, "data", "episodes")
OUT = os.path.join(ROOT, "data", "processed")
MIN_VOLT = 9.0        # episodes dropping below this are discarded (servos unreliable)
MIN_S, MAX_S = 4.0, 40.0
TAIL_S = 0.3          # last this many seconds are used for the touch pose
PRE_S = 3.0           # approach pose: this many seconds before the touch (just above the table, ~1.5-2 cm)
TAIL_STD = 6.0        # if the joint std over the last seconds exceeds this: "still moving"
N_T = 100             # trajectories are resampled to this many points (normalized time)
JOINTS = ["joint1 base", "joint2 shoulder", "joint3 elbow", "joint4 wrist", "joint5 roll"]


def load():
    keep, dropped = [], []
    for d in sorted(glob.glob(os.path.join(EPS, "ep_*"))):
        name = os.path.basename(d)
        try:
            meta = json.load(open(os.path.join(d, "meta.json")))
            z = np.load(os.path.join(d, "data.npz"))
            t = z["t_pc"] - z["t_pc"][0]
            F = z["follower"].astype(np.float32)
            G = z["goal"].astype(np.float32)
            volt = z["volt"]
        except Exception as e:  # noqa: BLE001
            dropped.append((name, "could not read: %s" % e))
            continue
        why = None
        if volt.min() < MIN_VOLT:
            why = "low voltage %.1f V" % volt.min()
        elif meta.get("warning"):
            why = "warning: %s" % meta["warning"]
        elif not MIN_S <= t[-1] <= MAX_S:
            why = "duration %.1f s" % t[-1]
        else:
            tail = F[t >= t[-1] - TAIL_S]
            if tail.std(axis=0).max() > TAIL_STD:
                why = "still moving at the touch point (std %.1f)" % tail.std(axis=0).max()
        if why:
            dropped.append((name, why))
            continue
        tail = F[t >= t[-1] - TAIL_S]
        pre = np.median(F[(t >= t[-1] - PRE_S - 0.1) & (t <= t[-1] - PRE_S + 0.1)], axis=0)
        keep.append(dict(name=name, t=t, F=F, G=G, uv=np.array(meta["dot_uv"], np.float32),
                         touch=np.median(tail, axis=0), pre=pre))
    return keep, dropped


def resample(ep):
    """Resamples the trajectory to N_T points in normalized time (0 = start, 1 = touch)."""
    s = ep["t"] / ep["t"][-1]
    grid = np.linspace(0, 1, N_T)
    return np.stack([np.interp(grid, s, ep["F"][:, j]) for j in range(5)], axis=1)


def main():
    os.makedirs(OUT, exist_ok=True)
    keep, dropped = load()
    print("used: %d   dropped: %d" % (len(keep), len(dropped)))
    for n, w in dropped:
        print("  dropped %s: %s" % (n, w))

    uv = np.stack([e["uv"] for e in keep])
    touch = np.stack([e["touch"] for e in keep])
    pre = np.stack([e["pre"] for e in keep])
    traj = np.stack([resample(e) for e in keep])               # (N, N_T, 5)
    dur = np.array([e["t"][-1] for e in keep], np.float32)
    start = np.stack([e["F"][0] for e in keep])
    np.savez_compressed(os.path.join(OUT, "dataset.npz"), names=np.array([e["name"] for e in keep]),
                        uv=uv, touch=touch, pre=pre, pre_delta=np.median(pre - touch, axis=0), traj=traj, dur=dur, start=start)
    print("written: data/processed/dataset.npz  (uv %s, touch %s, traj %s)" % (uv.shape, touch.shape, traj.shape))
    print("start pose: median %s  std %s" % (np.median(start, 0), start.std(0).round(1)))
    print("touch pose range: min %s max %s" % (touch.min(0), touch.max(0)))
    d = pre - touch
    print("%.1f s before touch - touch (median, std): %s  %s" % (PRE_S, np.median(d, 0).round(1), d.std(0).round(1)))
    print("duration: median %.1f s  (%.1f..%.1f)" % (np.median(dur), dur.min(), dur.max()))

    # 1) joint trajectories
    fig, ax = plt.subplots(1, 5, figsize=(20, 3.8), sharex=True)
    g = np.linspace(0, 1, N_T)
    for j in range(5):
        for k in range(len(keep)):
            ax[j].plot(g, traj[k, :, j], lw=0.6, alpha=0.5)
        ax[j].set_title(JOINTS[j])
        ax[j].set_xlabel("normalized time (1 = touch)")
    ax[0].set_ylabel("follower position [servo units]")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "trajectories.png"), dpi=80)

    # 2) dot position -> touch pose
    fig, ax = plt.subplots(2, 5, figsize=(20, 7))
    for j in range(5):
        for r, (axis, lab) in enumerate(((0, "u (left to right)"), (1, "v (top to bottom)"))):
            sc = ax[r, j].scatter(uv[:, axis], touch[:, j], c=uv[:, 1 - axis], s=14, cmap="viridis")
            ax[r, j].set_xlabel(lab)
            ax[r, j].set_title("touch " + JOINTS[j])
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "touch_pose.png"), dpi=80)

    # 3) dot positions + duration
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.5))
    ax[0].scatter(uv[:, 0], uv[:, 1], s=16)
    ax[0].set_xlim(0, 1); ax[0].set_ylim(1, 0); ax[0].set_aspect("equal")
    ax[0].set_title("dot positions (table)"); ax[0].set_xlabel("u"); ax[0].set_ylabel("v")
    ax[1].hist(dur, bins=20)
    ax[1].set_title("episode duration [s]")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "dots.png"), dpi=80)
    print("plots: data/processed/{trajectories,touch_pose,dots}.png")


if __name__ == "__main__":
    main()
