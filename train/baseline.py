#!/usr/bin/env python3
"""Approach A: (u, v) -> touch pose. Leave-one-out (LOO) error comparison.

Usage:  .venv/bin/python train/baseline.py
train/prepare.py must have been run first. Error: servo units (1 unit = 0.293 degrees).
Roll (joint 5) is practically constant, so it is not predicted; it stays at its home value.
"""
import os

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
D = np.load(os.path.join(ROOT, "data", "processed", "dataset.npz"))
UV, TOUCH = D["uv"].astype(np.float64), D["touch"].astype(np.float64)[:, :4]
N = len(UV)
JN = ["base", "shoulder", "elbow", "wrist"]
DEG = 300 / 1023


def feats(uv, deg):
    u, v = uv[:, 0], uv[:, 1]
    cols = [np.ones_like(u)]
    for d in range(1, deg + 1):
        for i in range(d + 1):
            cols.append(u ** (d - i) * v ** i)
    return np.stack(cols, 1)


def loo_poly(deg, lam):
    err = np.zeros_like(TOUCH)
    for i in range(N):
        m = np.arange(N) != i
        X = feats(UV[m], deg)
        W = np.linalg.solve(X.T @ X + lam * np.eye(X.shape[1]), X.T @ TOUCH[m])
        err[i] = feats(UV[i:i + 1], deg) @ W - TOUCH[i]
    return err


def rbf(A, B, ls):
    d2 = ((A[:, None] - B[None]) ** 2).sum(-1)
    return np.exp(-d2 / (2 * ls ** 2))


def loo_krr(ls, lam):
    """Kernel ridge (GP mean) + linear base. The LOO error is not in closed form here; it is computed directly."""
    err = np.zeros_like(TOUCH)
    for i in range(N):
        m = np.arange(N) != i
        X = feats(UV[m], 1)
        lin = np.linalg.lstsq(X, TOUCH[m], rcond=None)[0]
        res = TOUCH[m] - X @ lin
        K = rbf(UV[m], UV[m], ls) + lam * np.eye(m.sum())
        a = np.linalg.solve(K, res)
        pred = feats(UV[i:i + 1], 1) @ lin + rbf(UV[i:i + 1], UV[m], ls) @ a
        err[i] = pred - TOUCH[i]
    return err


def loo_knn(k):
    err = np.zeros_like(TOUCH)
    for i in range(N):
        m = np.where(np.arange(N) != i)[0]
        d = np.linalg.norm(UV[m] - UV[i], axis=1)
        idx = m[np.argsort(d)[:k]]
        w = 1 / (np.sort(d)[:k] + 1e-3)
        err[i] = (TOUCH[idx] * w[:, None]).sum(0) / w.sum() - TOUCH[i]
    return err


def kfold_mlp(folds=10, epochs=1500, hidden=64, wd=1e-4, seed=0):
    import torch
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    order = rng.permutation(N)
    err = np.zeros_like(TOUCH)
    mu, sd = TOUCH.mean(0), TOUCH.std(0)
    for f in range(folds):
        te = order[f::folds]
        tr = np.setdiff1d(np.arange(N), te)
        Xtr = torch.tensor(UV[tr], dtype=torch.float32)
        Ytr = torch.tensor((TOUCH[tr] - mu) / sd, dtype=torch.float32)
        net = torch.nn.Sequential(torch.nn.Linear(2, hidden), torch.nn.Tanh(), torch.nn.Linear(hidden, hidden),
                                  torch.nn.Tanh(), torch.nn.Linear(hidden, 4))
        opt = torch.optim.AdamW(net.parameters(), 3e-3, weight_decay=wd)
        for _ in range(epochs):
            opt.zero_grad()
            torch.nn.functional.mse_loss(net(Xtr), Ytr).backward()
            opt.step()
        with torch.no_grad():
            pred = net(torch.tensor(UV[te], dtype=torch.float32)).numpy() * sd + mu
        err[te] = pred - TOUCH[te]
    return err


def report(name, err):
    rms = np.sqrt((err ** 2).mean(0))
    print("%-34s" % name + "  ".join("%s %5.1f" % (JN[j][:3], rms[j]) for j in range(4)) +
          "   | base(deg) %.1f  shoulder+elbow+wrist mean(deg) %.1f" % (rms[0] * DEG, rms[1:].mean() * DEG))
    return rms


def main():
    print("N=%d  error = RMS [servo units]   (1 unit = %.3f deg)\n" % (N, DEG))
    print("%-34s" % "reference: mean pose" + "  ".join("%s %5.1f" % (JN[j][:3], TOUCH[:, j].std()) for j in range(4)))
    res = {}
    for deg in (1, 2, 3):
        for lam in (1e-6, 1e-3, 1e-1):
            res["poly%d lam=%g" % (deg, lam)] = report("polynomial degree %d, lam %g" % (deg, lam), loo_poly(deg, lam))
    for ls in (0.1, 0.2, 0.4):
        for lam in (1e-2, 1e-1, 1.0):
            res["krr ls=%g lam=%g" % (ls, lam)] = report("GP/RBF ls %g, lam %g" % (ls, lam), loo_krr(ls, lam))
    for k in (3, 5, 8):
        res["knn%d" % k] = report("k-nearest neighbours k=%d" % k, loo_knn(k))
    res["mlp"] = report("MLP 2x64 (10-fold cross-val)", kfold_mlp())
    best = min(res, key=lambda k: res[k][0] + res[k][1:].mean())
    print("\nbest (base + mean): %s" % best)


if __name__ == "__main__":
    main()
