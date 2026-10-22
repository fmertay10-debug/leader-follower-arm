#!/usr/bin/env python3
"""Generates the figures and equation images for the report (report/figures/*.png, report/stats.json).

Usage:  .venv/bin/python report/make_figures.py
Reads the real data under data/; train/prepare.py must have been run (data/processed/dataset.npz).
"""
import glob
import json
import os
import sys

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import patches  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
FIG = os.path.join(HERE, "figures")
os.makedirs(FIG, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "train"))
import baseline  # noqa: E402
import prepare  # noqa: E402

C1, C2, C3, C4, C5, C6 = "#1f4e79", "#d9822b", "#3a8f5c", "#b84a4a", "#6b6b6b", "#7b5ea7"
JN = ["Base", "Shoulder", "Elbow", "Wrist", "Roll"]
DEG = 300 / 1023
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.25,
    "figure.dpi": 100, "savefig.dpi": 200, "savefig.bbox": "tight", "mathtext.fontset": "cm",
})
STATS = {}


def save(fig, name):
    fig.savefig(os.path.join(FIG, name + ".png"))
    plt.close(fig)
    print("  ", name)


# ---------------------------------------------------------------- data
KEEP, DROPPED = prepare.load()
NAMES = [e["name"] for e in KEEP]
ALL = sorted(glob.glob(os.path.join(ROOT, "data", "episodes", "ep_*")))


def ep_npz(name):
    return np.load(os.path.join(ROOT, "data", "episodes", name, "data.npz"))


# ---------------------------------------------------------------- Figure: block diagram
def box(ax, x, y, w, h, text, fc, ec=None, fs=9, bold=False):
    ax.add_patch(patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12",
                                        fc=fc, ec=ec or C1, lw=1.3))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, fontweight="bold" if bold else "normal")


def arrow(ax, p, q, text="", both=False, color=C5, off=(0, 0.14), fs=7.5, ha="center"):
    ax.annotate("", xy=q, xytext=p, arrowprops=dict(arrowstyle="<|-|>" if both else "-|>", color=color, lw=1.4,
                                                    shrinkA=0, shrinkB=0))
    if text:
        ax.text((p[0] + q[0]) / 2 + off[0], (p[1] + q[1]) / 2 + off[1], text, ha=ha, va="bottom", fontsize=fs, color=color)


def fig_block():
    fig, ax = plt.subplots(figsize=(7.4, 3.9))
    ax.set_xlim(0, 10.3); ax.set_ylim(0, 5.2); ax.axis("off"); ax.grid(False)
    box(ax, 0.05, 3.5, 2.0, 1.3, "Camera\n(phone, Iriun)\n1280×720", "#eaf1f8")
    box(ax, 3.0, 1.7, 2.2, 3.1, "PC (Python)\n\nrecord.py\nrun_model.py\nvision/  train/\nrobot/opencm.py", "#dcebf7")
    box(ax, 6.5, 2.3, 1.7, 1.9, "OpenCM9.04\narm_rec.ino\n(STM32)", "#fbeede", ec=C2)
    box(ax, 9.0, 3.45, 1.25, 1.0, "Leader\n5×AX-12A", "#e6f3ea", ec=C3)
    box(ax, 9.0, 1.55, 1.25, 1.0, "Follower\n5×AX-12A", "#e6f3ea", ec=C3)
    box(ax, 6.5, 0.3, 1.7, 0.8, "Button", "#f4f4f4", ec=C5)
    box(ax, 9.0, 0.3, 1.25, 0.8, "12 V\npower", "#f4f4f4", ec=C5)
    arrow(ax, (2.05, 4.1), (3.0, 3.9), "video", off=(0, 0.2))
    arrow(ax, (5.2, 3.25), (6.5, 3.25), "USB serial\n115200", both=True, off=(0, 0.12))
    arrow(ax, (8.2, 3.7), (9.0, 3.95), "")
    arrow(ax, (8.2, 2.8), (9.0, 2.2), "")
    ax.text(8.6, 4.6, "Dynamixel bus · 1 Mbps", ha="center", fontsize=7.5, color=C5)
    arrow(ax, (7.35, 1.1), (7.35, 2.3), "E,B", off=(0.3, 0.0), ha="left")
    arrow(ax, (9.62, 1.1), (9.62, 1.55), "")
    ax.text(3.0, 1.2, "↑ D (data stream), E (event)", fontsize=7.5, color=C5, ha="left")
    ax.text(3.0, 0.85, "↓ G (goal), h g p x q (command)", fontsize=7.5, color=C5, ha="left")
    save(fig, "f01_block")


# ---------------------------------------------------------------- Figure: state machine
def fig_states():
    fig, ax = plt.subplots(figsize=(7.4, 4.0))
    ax.set_xlim(0, 10); ax.set_ylim(0, 5.6); ax.axis("off"); ax.grid(False)
    nodes = {"WAIT\n(0)": (1.2, 2.8, "#f4f4f4", C5), "HOMING\n(5)": (4.3, 4.8, "#fbeede", C2),
             "READY\n(6)": (7.6, 4.8, "#e6f3ea", C3), "SYNC\n(1)": (8.6, 3.0, "#dcebf7", C1),
             "FOLLOW\n(2)": (8.6, 1.1, "#dcebf7", C1), "PC\n(4)": (5.0, 2.7, "#dcebf7", C1),
             "HOLD\n(3)": (4.6, 0.8, "#f6dede", C4)}
    pos = {}
    for k, (x, y, fc, ec) in nodes.items():
        ax.add_patch(patches.Ellipse((x, y), 2.0, 0.95, fc=fc, ec=ec, lw=1.4))
        ax.text(x, y, k, ha="center", va="center", fontsize=8.2)
        pos[k.split("\n")[0]] = (x, y)

    def link(a, b, text, rad=0.0, off=(0, 0), color=C5, ls="-"):
        pa, pb = pos[a], pos[b]
        ax.annotate("", xy=pb, xytext=pa, arrowprops=dict(arrowstyle="-|>", color=color, lw=1.3, ls=ls, shrinkA=22, shrinkB=22,
                                                          connectionstyle="arc3,rad=%g" % rad))
        ax.text((pa[0] + pb[0]) / 2 + off[0], (pa[1] + pb[1]) / 2 + off[1], text, fontsize=8, color=color, ha="center")
    link("WAIT", "HOMING", "h", off=(-0.35, 0.2))
    link("HOMING", "READY", "arrived (≤10 units)", off=(0, 0.15))
    link("READY", "SYNC", "g (leader released)", off=(1.25, 0.05))
    link("SYNC", "FOLLOW", "sync done", off=(0.85, 0))
    link("READY", "PC", "p", rad=0.2, off=(-0.55, 0.05))
    link("FOLLOW", "HOLD", "x / error", off=(0.3, -0.45), color=C4)
    link("PC", "HOLD", "x / timeout", off=(-0.85, 0), color=C4)
    link("HOLD", "HOMING", "h", rad=-0.55, off=(-1.1, 0.25), ls="--")
    ax.text(0.15, 0.02, "q: torque off on both arms from any state → WAIT      red: safety stop", fontsize=7.5, color=C5)
    save(fig, "f04_states")


# ---------------------------------------------------------------- Figure: homography sketch
def fig_homography():
    fig, axs = plt.subplots(1, 2, figsize=(7.4, 3.2), gridspec_kw={"width_ratios": [1.25, 1]})
    a = axs[0]
    quad = np.array([[0.9, 3.2], [4.6, 3.45], [5.0, 0.35], [0.5, 0.55]])
    a.add_patch(patches.Polygon(quad, closed=True, fc="#eaf1f8", ec=C1, lw=1.5))
    for (x, y), t in zip(quad, ["ID0", "ID1", "ID2", "ID3"]):
        a.plot(x, y, "s", color=C1, ms=9)
        a.text(x + (0.12 if x > 2.5 else -0.12), y + (0.2 if y > 2 else -0.3), t, ha="left" if x > 2.5 else "right", fontsize=8.5, color=C1)
    a.plot(2.6, 1.7, "o", color=C2, ms=12, mec="k"); a.text(2.8, 1.75, "dot (x, y)", fontsize=8.5)
    a.set_xlim(-0.3, 5.7); a.set_ylim(-0.1, 3.9); a.set_aspect("equal"); a.axis("off"); a.grid(False)
    a.set_title("Camera image (pixels)")
    b = axs[1]
    b.add_patch(patches.Rectangle((0, 0), 1, 1, fc="#eaf1f8", ec=C1, lw=1.5))
    for (x, y), t in zip([(0, 0), (1, 0), (1, 1), (0, 1)], ["ID0", "ID1", "ID2", "ID3"]):
        b.plot(x, y, "s", color=C1, ms=9); b.text(x + (0.05 if x else -0.05), y + (0.06 if y == 0 else -0.1), t, ha="left" if x else "right", fontsize=8.5, color=C1)
    b.plot(0.46, 0.52, "o", color=C2, ms=12, mec="k"); b.text(0.52, 0.55, "(u, v)", fontsize=8.5)
    b.set_xlim(-0.25, 1.3); b.set_ylim(1.2, -0.2); b.set_aspect("equal"); b.set_xlabel("u → (right)     v ↓ (down)")
    b.set_title("Table coordinates (0–1)")
    fig.patches.append(patches.FancyArrowPatch((0.50, 0.5), (0.56, 0.5), transform=fig.transFigure, arrowstyle="-|>", mutation_scale=16, color=C4, lw=2))
    fig.text(0.53, 0.56, "H", color=C4, ha="center", fontsize=11, fontweight="bold")
    save(fig, "f03_homography")


# ---------------------------------------------------------------- Figure: camera frame + mask
def fig_camera():
    fa = cv2.imread(os.path.join(ROOT, "data", "frame_annotated.png"))
    fm = cv2.imread(os.path.join(ROOT, "data", "frame_mask.png"), cv2.IMREAD_GRAYSCALE)
    fig, axs = plt.subplots(1, 2, figsize=(7.4, 2.85))
    axs[0].imshow(cv2.cvtColor(fa, cv2.COLOR_BGR2RGB)); axs[0].set_title("(a) Markers, table and detected dot", fontsize=9)
    axs[1].imshow(fm, cmap="gray"); axs[1].set_title("(b) Mask after white threshold and opening", fontsize=9)
    for a in axs:
        a.axis("off"); a.grid(False)
    save(fig, "f02_camera")


# ---------------------------------------------------------------- Figure: example episode (leader / follower)
def fig_example():
    name = "ep_0060"
    z = ep_npz(name)
    t = z["t_pc"] - z["t_pc"][0]
    L, F, G = z["leader"].astype(float), z["follower"].astype(float), z["goal"].astype(float)
    sgn = np.array([1, 1, -1, -1, 1])
    mapped = F[0] + sgn * (L - L[0])
    fig, axs = plt.subplots(5, 1, figsize=(7.0, 7.2), sharex=True)
    for j in range(5):
        axs[j].plot(t, mapped[:, j], color=C5, lw=1.6, label="Leader (converted to follower units)")
        axs[j].plot(t, G[:, j], color=C2, lw=1.0, label="Goal (slew limit + lead term)")
        axs[j].plot(t, F[:, j], color=C1, lw=1.0, ls="--", label="Follower (measured)")
        axs[j].set_ylabel(JN[j] + "\n[units]")
    axs[0].legend(ncol=3, fontsize=7, loc="lower center", bbox_to_anchor=(0.5, 1.0), frameon=False)
    axs[-1].set_xlabel("time [s]")
    save(fig, "f05_example")
    STATS["example"] = name


# ---------------------------------------------------------------- Figure: tracking error
def fig_tracking():
    err = [[] for _ in range(5)]
    for n in NAMES:
        z = ep_npz(n)
        e = np.abs(z["goal"].astype(float) - z["follower"].astype(float))
        for j in range(5):
            err[j].append(e[:, j])
    err = [np.concatenate(x) for x in err]
    fig, ax = plt.subplots(figsize=(6.3, 3.2))
    xs = np.linspace(0, 20, 400)
    for j in range(5):
        cdf = [(err[j] <= x).mean() * 100 for x in xs]
        ax.plot(xs, cdf, label=JN[j], lw=1.6)
    ax.set_xlabel("|goal − measured position| [servo units]"); ax.set_ylabel("share of samples [%]")
    ax.set_ylim(0, 101); ax.legend(ncol=5, fontsize=8, loc="lower right")
    ax.axhline(95, color=C5, lw=0.8, ls=":")
    save(fig, "f06_tracking")
    STATS["tracking"] = {JN[j]: {"p50": float(np.median(err[j])), "p95": float(np.percentile(err[j], 95)),
                                 "p99": float(np.percentile(err[j], 99)), "max": float(err[j].max())} for j in range(5)}
    STATS["n_samples"] = int(len(err[0]))
    sag = []
    for n in NAMES:
        z = ep_npz(n)
        tt = z["t_pc"] - z["t_pc"][0]
        w = tt > tt[-1] - 0.3
        sag.append(np.median(z["goal"][w].astype(float) - z["follower"][w].astype(float), axis=0))
    sag = np.array(sag)
    STATS["touch_offset"] = {JN[j]: {"median": float(np.median(sag[:, j])), "p10": float(np.percentile(sag[:, j], 10)),
                                     "p90": float(np.percentile(sag[:, j], 90))} for j in range(5)}


# ---------------------------------------------------------------- Figure: dot positions
def fig_dots():
    name = "ep_0060"
    meta = json.load(open(os.path.join(ROOT, "data", "episodes", name, "meta.json")))
    H = np.array(meta["table_H"])
    cap = cv2.VideoCapture(os.path.join(ROOT, "data", "episodes", name, "video.mp4"))
    ok, frame = cap.read()
    cap.release()
    uv = np.stack([e["uv"] for e in KEEP]).astype(float)
    Hi = np.linalg.inv(H)
    px = cv2.perspectiveTransform(uv.reshape(-1, 1, 2).astype(np.float32), Hi.astype(np.float32)).reshape(-1, 2)
    fig, axs = plt.subplots(1, 2, figsize=(7.4, 3.5), gridspec_kw={"width_ratios": [1.5, 1]})
    axs[0].imshow(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    axs[0].scatter(px[:, 0], px[:, 1], s=14, c=C2, ec="k", lw=0.3)
    axs[0].set_title("(a) Touched dots (camera frame)", fontsize=9); axs[0].axis("off"); axs[0].grid(False)
    cnt = np.histogram2d(uv[:, 1], uv[:, 0], bins=4, range=[[0, 1], [0, 1]])[0].astype(int)
    for k in range(5):
        axs[1].axhline(k / 4, color=C5, lw=0.5); axs[1].axvline(k / 4, color=C5, lw=0.5)
    for r in range(4):
        for c in range(4):
            axs[1].text((c + 0.5) / 4, (r + 0.5) / 4, str(cnt[r, c]), ha="center", va="center", fontsize=11,
                        color=C4 if cnt[r, c] == 0 else C5, alpha=0.8)
    axs[1].scatter(uv[:, 0], uv[:, 1], s=10, c=C1)
    axs[1].set_xlim(0, 1); axs[1].set_ylim(1, 0); axs[1].set_aspect("equal"); axs[1].set_xlabel("u"); axs[1].set_ylabel("v")
    axs[1].set_title("(b) Table coordinates and 4×4 counts"); axs[1].grid(False)
    save(fig, "f07_dots")
    STATS["uv_range"] = {"u": [float(uv[:, 0].min()), float(uv[:, 0].max())], "v": [float(uv[:, 1].min()), float(uv[:, 1].max())]}
    STATS["coverage_4x4"] = cnt.tolist()


# ---------------------------------------------------------------- Figure: duration + voltage
def fig_duration_voltage():
    dur = np.array([e["t"][-1] for e in KEEP])
    vmin, idx = [], []
    for d in ALL:
        try:
            z = np.load(os.path.join(d, "data.npz"))
            vmin.append(float(z["volt"].min())); idx.append(int(os.path.basename(d)[3:]))
        except Exception:  # noqa: BLE001
            pass
    vmin, idx = np.array(vmin), np.array(idx)
    fig, axs = plt.subplots(1, 2, figsize=(7.4, 3.0))
    axs[0].hist(dur, bins=18, color=C1, ec="white")
    axs[0].axvline(np.median(dur), color=C2, lw=1.6, label="median %.1f s" % np.median(dur))
    axs[0].set_xlabel("episode duration [s]"); axs[0].set_ylabel("number of episodes"); axs[0].legend(fontsize=8)
    axs[0].set_title("(a) Episode durations")
    bad = vmin < prepare.MIN_VOLT
    axs[1].scatter(idx[~bad], vmin[~bad], s=9, c=C1, label="used")
    axs[1].scatter(idx[bad], vmin[bad], s=16, c=C4, marker="x", label="dropped (< %.0f V)" % prepare.MIN_VOLT)
    axs[1].axhline(prepare.MIN_VOLT, color=C4, lw=0.8, ls=":")
    axs[1].set_xlabel("episode number"); axs[1].set_ylabel("lowest voltage in episode [V]"); axs[1].legend(fontsize=8, loc="center right")
    axs[1].set_title("(b) Battery voltage")
    save(fig, "f08_duration_voltage")
    STATS["duration"] = {"median": float(np.median(dur)), "min": float(dur.min()), "max": float(dur.max())}
    STATS["voltage_used"] = [float(vmin[~bad].min()), float(np.median(vmin[~bad])), float(vmin[~bad].max())]
    STATS["voltage_dropped"] = [float(vmin[bad].min()), float(vmin[bad].max())] if bad.any() else None
    STATS["n_total"] = int(len(ALL)); STATS["n_used"] = len(KEEP); STATS["n_dropped"] = len(DROPPED)


# ---------------------------------------------------------------- Figure: trajectories
def fig_trajectories():
    D = np.load(os.path.join(ROOT, "data", "processed", "dataset.npz"))
    tr = D["traj"]
    g = np.linspace(0, 1, tr.shape[1])
    fig, axs = plt.subplots(1, 5, figsize=(7.4, 2.5), sharex=True)
    for j in range(5):
        for k in range(len(tr)):
            axs[j].plot(g, tr[k, :, j], lw=0.5, alpha=0.35, color=C1)
        axs[j].plot(g, np.median(tr[:, :, j], axis=0), lw=1.8, color=C2)
        axs[j].set_title(JN[j], fontsize=9); axs[j].set_xlabel("normalized time", fontsize=7.5)
        axs[j].tick_params(labelsize=7)
    axs[0].set_ylabel("follower position [units]")
    save(fig, "f09_trajectories")


# ---------------------------------------------------------------- Figure: dot -> pose
def fig_dot_pose():
    uv = np.stack([e["uv"] for e in KEEP]).astype(float)
    T = np.stack([e["touch"] for e in KEEP]).astype(float)
    fig, axs = plt.subplots(2, 4, figsize=(7.4, 3.9))
    corr = {}
    for j in range(4):
        for r, (ax_i, lab) in enumerate(((0, "u"), (1, "v"))):
            a = axs[r, j]
            a.scatter(uv[:, ax_i], T[:, j], s=9, c=uv[:, 1 - ax_i], cmap="viridis")
            c = np.corrcoef(uv[:, ax_i], T[:, j])[0, 1]
            c = 0.0 if abs(c) < 0.005 else c
            corr["%s~%s" % (JN[j], lab)] = float(c)
            a.set_title("%s  (r = %.2f)" % (JN[j], c), fontsize=8.5); a.set_xlabel(lab, fontsize=8)
            a.tick_params(labelsize=7)
        axs[0, j].set_ylabel("touch pose [units]" if j == 0 else "", fontsize=8)
        axs[1, j].set_ylabel("touch pose [units]" if j == 0 else "", fontsize=8)
    fig.subplots_adjust(hspace=0.6, wspace=0.42)
    save(fig, "f10_dot_pose")
    STATS["correlation"] = corr


# ---------------------------------------------------------------- Figure: redundancy sketch
def fig_redundancy():
    a, b, c, h, T = 1.0, 1.0, 0.7, 1.5, 1.55
    S = np.array([0.0, h])
    fig, ax = plt.subplots(figsize=(5.6, 3.3))
    ax.axhline(0, color="k", lw=1.5); ax.fill_between([-0.5, 2.3], -0.15, 0, color="#dddddd")
    ax.plot(*S, "s", color="k", ms=10)
    for alpha_deg, col, lab in ((-30, C1, r"$\alpha=-30^\circ$"), (-80, C2, r"$\alpha=-80^\circ$")):
        al = np.radians(alpha_deg)
        W = np.array([T, 0]) - c * np.array([np.cos(al), np.sin(al)])
        d = np.linalg.norm(W - S)
        ang = np.arctan2(W[1] - S[1], W[0] - S[0])
        cosA = (a * a + d * d - b * b) / (2 * a * d)
        A = np.arccos(np.clip(cosA, -1, 1))
        E = S + a * np.array([np.cos(ang + A), np.sin(ang + A)])      # elbow up
        pts = np.array([S, E, W, [T, 0]])
        ax.plot(pts[:, 0], pts[:, 1], "-o", color=col, lw=3, ms=7, label=lab)
    ax.plot(T, 0, "*", color=C4, ms=16, zorder=5)
    ax.text(T + 0.05, 0.12, "touch point", fontsize=8, color=C4)
    ax.text(S[0] - 0.05, S[1] + 0.12, "shoulder", fontsize=8, ha="right")
    ax.text(0.5, 0.05, "table", fontsize=8, color=C5)
    ax.set_xlim(-0.4, 2.3); ax.set_ylim(-0.15, 2.6); ax.set_aspect("equal"); ax.axis("off"); ax.grid(False)
    ax.legend(loc="upper right", fontsize=9, frameon=False, title="angle of the last link")
    save(fig, "f11_redundancy")


# ---------------------------------------------------------------- Figure: model comparison
def fig_models():
    TOUCH = baseline.TOUCH
    res = {"Mean pose (reference)": TOUCH.std(0)}
    errs = {}
    errs["Polynomial degree 1"] = baseline.loo_poly(1, 1e-3)
    errs["Polynomial degree 2"] = baseline.loo_poly(2, 1e-3)
    errs["Polynomial degree 3 (used)"] = baseline.loo_poly(3, 1e-3)
    errs["GP / RBF"] = baseline.loo_krr(0.4, 0.01)
    errs["k-nearest neighbours (k=5)"] = baseline.loo_knn(5)
    errs["MLP 2×64 (10-fold)"] = baseline.kfold_mlp()
    for k, e in errs.items():
        res[k] = np.sqrt((e ** 2).mean(0))
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    names = list(res)
    cols = ["#b0b0b0", "#9fbcd8", "#6b9bc6", C1, C3, C2, C6]
    w = 0.115
    for i, n in enumerate(names):
        ax.bar(np.arange(4) + (i - len(names) / 2 + 0.5) * w, res[n], w, label=n, color=cols[i], ec="white", lw=0.4)
    ax.set_xticks(range(4)); ax.set_xticklabels(["Base", "Shoulder", "Elbow", "Wrist"])
    ax.set_ylabel("LOO error, RMS [servo units]"); ax.legend(fontsize=7.2, ncol=2, loc="upper left")
    ax.set_ylim(0, 118)
    save(fig, "f12_models")
    STATS["rms"] = {k: [float(x) for x in v] for k, v in res.items()}
    return errs


def fig_predictions(errs):
    e = errs["Polynomial degree 3 (used)"]
    T = baseline.TOUCH
    pred = T + e
    fig, axs = plt.subplots(1, 4, figsize=(7.4, 2.5))
    for j in range(4):
        a = axs[j]
        a.scatter(T[:, j], pred[:, j], s=9, c=C1)
        lo, hi = T[:, j].min(), T[:, j].max()
        a.plot([lo, hi], [lo, hi], "--", color=C4, lw=1)
        a.set_title("%s (RMS %.1f)" % (JN[j], np.sqrt((e[:, j] ** 2).mean())), fontsize=8.5)
        a.set_xlabel("actual", fontsize=8); a.tick_params(labelsize=7)
    axs[0].set_ylabel("prediction (LOO)", fontsize=8)
    save(fig, "f13_predictions")


def fig_covariance(errs):
    e = errs["Polynomial degree 3 (used)"][:, 1:]
    C = np.cov(e.T)
    w, V = np.linalg.eigh(C)
    w, V = w[::-1], V[:, ::-1]
    share = w / w.sum()
    fig, axs = plt.subplots(1, 2, figsize=(7.2, 2.8))
    axs[0].bar(["1st", "2nd", "3rd"], share * 100, color=[C2, C1, C1])
    for i, s in enumerate(share):
        axs[0].text(i, s * 100 + 1.5, "%.1f%%" % (s * 100), ha="center", fontsize=8.5)
    axs[0].set_ylabel("share of error variance [%]"); axs[0].set_ylim(0, 110); axs[0].set_title("(a) Eigenvalue shares"); axs[0].set_xlabel("error direction")
    v1 = V[:, 0] * np.sign(V[0, 0] if V[0, 0] != 0 else 1)
    axs[1].bar(["Shoulder", "Elbow", "Wrist"], v1, color=[C1, C1, C1])
    for i, x in enumerate(v1):
        axs[1].text(i, x + (0.05 if x >= 0 else -0.12), "%.2f" % x, ha="center", fontsize=8.5)
    axs[1].axhline(0, color="k", lw=0.8); axs[1].set_ylim(-1.1, 1.1); axs[1].set_title("(b) Components of the 1st direction (eigenvector)")
    save(fig, "f14_covariance")
    rest = np.sqrt((e @ V[:, 1:]) ** 2).sum() if False else np.sqrt(((e @ V[:, 1:]) ** 2).sum(1).mean())
    STATS["covariance"] = {"std": [float(np.sqrt(x)) for x in w], "share": [float(x) for x in share], "dir1": [float(x) for x in v1],
                           "rest_rms_units": float(rest), "rest_rms_deg": float(rest * DEG)}


def fig_surface():
    uv = np.stack([e["uv"] for e in KEEP]).astype(float)
    T = np.stack([e["touch"] for e in KEEP]).astype(float)[:, :4]
    X = baseline.feats(uv, 3)
    W = np.linalg.solve(X.T @ X + 1e-3 * np.eye(X.shape[1]), X.T @ T)
    gu = np.linspace(0.02, 0.58, 110); gv = np.linspace(0.04, 0.98, 160)
    U, Vv = np.meshgrid(gu, gv)
    P = baseline.feats(np.stack([U.ravel(), Vv.ravel()], 1), 3) @ W
    d = np.sqrt(((np.stack([U.ravel(), Vv.ravel()], 1)[:, None] - uv[None]) ** 2).sum(-1)).min(1)
    P[d > 0.12] = np.nan
    fig, axs = plt.subplots(1, 4, figsize=(7.4, 3.1), sharey=True)
    for j in range(4):
        im = axs[j].pcolormesh(U, Vv, P[:, j].reshape(U.shape), cmap="viridis", shading="auto")
        axs[j].scatter(uv[:, 0], uv[:, 1], s=3, c="w", alpha=0.7)
        axs[j].set_ylim(1, 0); axs[j].set_xlim(0, 0.6); axs[j].set_aspect("equal"); axs[j].set_title(JN[j], fontsize=9)
        axs[j].set_xlabel("u", fontsize=8); axs[j].tick_params(labelsize=7); axs[j].grid(False)
        cb = fig.colorbar(im, ax=axs[j], orientation="horizontal", pad=0.18, fraction=0.06); cb.ax.tick_params(labelsize=6.5)
    axs[0].set_ylabel("v")
    save(fig, "f15_surface")


def fig_minjerk():
    tau = np.linspace(0, 1, 300)
    s = 10 * tau ** 3 - 15 * tau ** 4 + 6 * tau ** 5
    ds = 30 * tau ** 2 * (1 - tau) ** 2
    dds = 60 * tau * (1 - tau) * (1 - 2 * tau)
    fig, axs = plt.subplots(1, 3, figsize=(7.4, 2.4))
    axs[0].plot(tau, s, color=C1, lw=2, label="minimum-jerk"); axs[0].plot(tau, tau, "--", color=C5, lw=1.2, label="linear")
    axs[0].set_title("position  s(τ)"); axs[0].legend(fontsize=7.5)
    axs[1].plot(tau, ds, color=C1, lw=2); axs[1].axhline(1, color=C5, ls="--", lw=1.2)
    axs[1].plot(0.5, 1.875, "o", color=C4); axs[1].text(0.53, 1.8, "peak 15/8", fontsize=8, color=C4)
    axs[1].set_title("velocity  ds/dτ")
    axs[2].plot(tau, dds, color=C1, lw=2); axs[2].axhline(0, color=C5, lw=0.8); axs[2].set_title("acceleration  d²s/dτ²")
    for a in axs:
        a.set_xlabel("τ = t / T")
    save(fig, "f16_minjerk")


def fig_approach():
    secs = np.array([0.25, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 8])
    rows = []
    for e in KEEP:
        t, F = e["t"], e["F"]
        end = np.median(F[t > t[-1] - 0.3], axis=0)
        r = []
        for s in secs:
            w = (t >= t[-1] - s - 0.1) & (t <= t[-1] - s + 0.1)
            r.append(np.abs(np.median(F[w], axis=0) - end)[:4].max() if w.any() and t[-1] > s + 0.2 else np.nan)
        rows.append(r)
    r = np.array(rows)
    med, p10, p90 = np.nanmedian(r, 0), np.nanpercentile(r, 10, axis=0), np.nanpercentile(r, 90, axis=0)
    fig, ax = plt.subplots(figsize=(5.8, 3.0))
    ax.fill_between(secs, p10, p90, color="#9fbcd8", alpha=0.6, label="10–90% range")
    ax.plot(secs, med, "-o", color=C1, label="median")
    ax.axvline(3, color=C4, ls="--", lw=1.2); ax.text(3.1, ax.get_ylim()[1] * 0.85, "chosen: 3 s", color=C4, fontsize=8.5)
    ax.set_xlabel("time before touch [s]"); ax.set_ylabel("difference from touch pose\n(largest joint, units)")
    ax.legend(fontsize=8, loc="upper left")
    save(fig, "f17_approach")
    STATS["approach"] = {"s": secs.tolist(), "median": [float(x) for x in med], "p90": [float(x) for x in p90]}


def fig_run():
    runs = []
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "runs", "*.npz"))):
        d = np.load(f, allow_pickle=True)
        runs.append((os.path.basename(f), d["uv"], bool(d["ok"]), str(d["msg"]), d["log"]))
    STATS["runs"] = {"total": len(runs), "ok": sum(r[2] for r in runs),
                     "aborted": sum((not r[2]) for r in runs), "abort_reasons": sorted(set(r[3] for r in runs if not r[2]))}
    good = [r for r in runs if r[2] and len(r[4]) > 100]
    if good:
        r = max(good, key=lambda x: len(x[4]))
        log = r[4]
        t = log[:, 0] - log[0, 0]
        G, F = log[:, 1:6], log[:, 6:11]
        tot = t[-1]
        durs = np.array([6, 4, 0.7, 3, 6.0])
        bounds = np.concatenate([[0], np.cumsum(durs)]) / durs.sum() * tot
        names = ["approach", "touch", "hold", "lift", "return"]
        fig, axs = plt.subplots(4, 1, figsize=(7.0, 6.0), sharex=True)
        for j in range(4):
            axs[j].plot(t, G[:, j], color=C2, lw=1.8, label="sent goal")
            axs[j].plot(t, F[:, j], color=C1, lw=1.0, ls="--", label="measured follower position")
            axs[j].set_ylabel(JN[j] + "\n[units]")
            for k in range(5):
                if k % 2 == 0:
                    axs[j].axvspan(bounds[k], bounds[k + 1], color="#f0f0f0", zorder=0)
        for k in range(5):
            axs[0].text((bounds[k] + bounds[k + 1]) / 2, axs[0].get_ylim()[1], names[k], ha="center", va="bottom", fontsize=7.5, color=C5)
        axs[0].legend(fontsize=7.5, loc="lower center", ncol=2, bbox_to_anchor=(0.5, 1.12), frameon=False)
        axs[-1].set_xlabel("time [s]")
        save(fig, "f18_run")
        STATS["run_example"] = {"file": r[0], "uv": [float(x) for x in r[1]], "duration": float(tot)}
        # tracking quality: goal - measured
        e = np.abs(G - F)[:, :4]
        STATS["run_example"]["err_median"] = [float(x) for x in np.median(e, 0)]
        STATS["run_example"]["err_p95"] = [float(x) for x in np.percentile(e, 95, axis=0)]
    uv = np.stack([e["uv"] for e in KEEP]).astype(float)
    fig, ax = plt.subplots(figsize=(3.6, 3.8))
    ax.scatter(uv[:, 0], uv[:, 1], s=8, c="#c8d6e5", label="training points (114)")
    pts = np.array([r[1] for r in runs if r[2]])
    if len(pts):
        ax.scatter(pts[:, 0], pts[:, 1], s=60, marker="*", c=C2, ec="k", lw=0.5, label="completed runs (%d)" % len(pts))
    ax.set_xlim(0, 0.6); ax.set_ylim(1, 0); ax.set_aspect("equal"); ax.set_xlabel("u"); ax.set_ylabel("v"); ax.legend(fontsize=7, loc="lower right")
    save(fig, "f19_run_points")


# ---------------------------------------------------------------- Equations
EQS = {
    "deg": r"$\mathrm{degrees} = p \cdot \dfrac{300}{1023}$",
    "base": r"$b_i = H^{F}_i + s_i\,\left(l_i - H^{L}_i\right)$",
    "goal": r"$\tilde g_i = \mathrm{clip}\left(b_i + \ell_i,\; m_i,\; M_i\right)$",
    "vfilt": r"$\hat v_i[k] = (1-\alpha)\,\hat v_i[k-1] + \alpha\,\dfrac{b_i[k]-b_i[k-1]}{\Delta t}$",
    "lead": r"$\ell_i = \mathrm{clip}\left(T_{\ell}\,\hat v_i,\; -\ell_{\max},\; +\ell_{\max}\right)$",
    "slew": r"$g_i[k] = g_i[k-1] + \mathrm{clip}\left(\tilde g_i - g_i[k-1],\; -R\,\Delta t,\; +R\,\Delta t\right)$",
    "trackerr": r"$\left|g_i - f_i\right| > 80 \ \mathrm{units}\quad \mathrm{for}\quad t > 500\ \mathrm{ms} \;\Rightarrow\; \mathrm{HOLD}$",
    "homog": r"$\left(u',\, v',\, w'\right)^{\!\top} = \mathbf{H}\,\left(x,\, y,\, 1\right)^{\!\top},\qquad u = \dfrac{u'}{w'},\;\; v = \dfrac{v'}{w'}$",
    "dlt_u": r"$u\,\left(h_{31}x + h_{32}y + h_{33}\right) = h_{11}x + h_{12}y + h_{13}$",
    "dlt_v": r"$v\,\left(h_{31}x + h_{32}y + h_{33}\right) = h_{21}x + h_{22}y + h_{23}$",
    "circ": r"$C = \dfrac{4\pi A}{P^{2}} \;\leq\; 1,\qquad C \geq 0.70$",
    "moment": r"$c_x = \dfrac{m_{10}}{m_{00}},\qquad c_y = \dfrac{m_{01}}{m_{00}}$",
    "phi": r"$\varphi(u,v) = \left[\,1,\; u,\; v,\; u^2,\; uv,\; v^2,\; u^3,\; u^2v,\; uv^2,\; v^3\,\right]^{\!\top}$",
    "ridge": r"$\mathbf{W} = \left(\mathbf{X}^{\!\top}\mathbf{X} + \lambda \mathbf{I}\right)^{-1}\mathbf{X}^{\!\top}\mathbf{Y},\qquad \lambda = 10^{-3}$",
    "pred": r"$\hat{\mathbf{q}}(u,v) = \mathbf{W}^{\!\top}\varphi(u,v)$",
    "gp": r"$k(\mathbf{a},\mathbf{b}) = \exp\left(-\dfrac{\|\mathbf{a}-\mathbf{b}\|^{2}}{2\,\ell^{2}}\right)$",
    "knn": r"$\hat{\mathbf{q}} = \dfrac{\sum_{n\in\mathcal{N}_k} w_n\,\mathbf{q}_n}{\sum_{n\in\mathcal{N}_k} w_n},\qquad w_n = \dfrac{1}{d_n + 10^{-3}}$",
    "loo": r"$\mathrm{RMS}_j = \sqrt{\dfrac{1}{N}\sum_{i=1}^{N}\left(\hat q^{(-i)}_{i,j} - q_{i,j}\right)^{2}}$",
    "fk_r": r"$r(\theta) = \sum_{k=1}^{3} l_k \cos\phi_k,\qquad z(\theta) = \sum_{k=1}^{3} l_k \sin\phi_k,\qquad \phi_k = \sum_{j=1}^{k}\theta_j$",
    "fk_c": r"$r(\theta) = r^{*},\quad z(\theta) = 0,\qquad \mathbf{J} = \dfrac{\partial (r,z)}{\partial \theta}\ \mathrm{(2\times 3)},\quad \dim\ker\mathbf{J} = 3-2 = 1$",
    "cov": r"$\mathbf{C} = \dfrac{1}{N}\sum_{i=1}^{N}\mathbf{e}_i\mathbf{e}_i^{\!\top},\qquad \mathbf{C}\,\mathbf{v}_k = \lambda_k\mathbf{v}_k,\qquad \mathrm{share}_k = \dfrac{\lambda_k}{\sum_m \lambda_m}$",
    "small": r"$\delta \approx L\,\delta\theta = 34\ \mathrm{cm} \times 0.054\ \mathrm{rad} \approx 1.8\ \mathrm{cm}$",
    "pre": r"$\mathbf{q}_{\mathrm{approach}} = \hat{\mathbf{q}}_{\mathrm{touch}} + \delta,\qquad \delta = \mathrm{median}\left(\mathbf{q}_{3\,\mathrm{s}} - \mathbf{q}_{\mathrm{touch}}\right)$",
    "mj_s": r"$s(\tau) = 10\tau^{3} - 15\tau^{4} + 6\tau^{5},\qquad \tau = t/T$",
    "mj_q": r"$\mathbf{q}(t) = \mathbf{q}_0 + \left(\mathbf{q}_1 - \mathbf{q}_0\right) s(t/T)$",
    "mj_v": r"$\dfrac{ds}{d\tau} = 30\,\tau^{2}(1-\tau)^{2},\qquad \max_t \dot q = \dfrac{15}{8}\,\dfrac{|\Delta q|}{T}$",
    "mj_j": r"$\min \int_{0}^{T} \left(\frac{d^{3}q}{dt^{3}}\right)^{2} dt \;\;\Rightarrow\;\; q(t)\ \mathrm{is\ a\ degree\ 5\ polynomial}$",
}
EQ_ORDER = list(EQS)


def make_equations():
    meta = {}
    for i, (k, s) in enumerate(EQS.items()):
        fig = plt.figure(figsize=(0.1, 0.1))
        fig.text(0, 0, s, fontsize=17)
        p = os.path.join(FIG, "eq_%s.png" % k)
        fig.savefig(p, dpi=300, bbox_inches="tight", pad_inches=0.04, transparent=False, facecolor="white")
        plt.close(fig)
        im = cv2.imread(p)
        meta[k] = {"w": int(im.shape[1]), "h": int(im.shape[0]), "dpi": 300}
    json.dump({"order": EQ_ORDER, "size": meta}, open(os.path.join(FIG, "equations.json"), "w"))
    print("   equations:", len(EQS))


if __name__ == "__main__":
    print("figures:")
    fig_block(); fig_camera(); fig_homography(); fig_states(); fig_example(); fig_tracking(); fig_dots(); fig_duration_voltage()
    fig_trajectories(); fig_dot_pose(); fig_redundancy()
    errs = fig_models(); fig_predictions(errs); fig_covariance(errs); fig_surface(); fig_minjerk(); fig_approach(); fig_run()
    make_equations()
    json.dump(STATS, open(os.path.join(HERE, "stats.json"), "w"), indent=1, ensure_ascii=False)
    print("done: report/figures, report/stats.json")
