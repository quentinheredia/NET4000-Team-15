#!/usr/bin/env python3
"""
ntn_mlm.py  —  NTN Link Delay Prediction Model
===============================================
Generates training data from the NTN orbit simulation, engineers per-link
features, trains a Gradient Boosted Decision Tree regressor (numpy-only) to
predict ONE-TICK-AHEAD link delay (ms), evaluates performance, and saves the
trained model for integration into the routing pipeline.

Dependencies : numpy, pandas  (stdlib only — no scikit-learn required)

Usage
-----
  python3 ntn_mlm.py                             # generate data, train, eval
  python3 ntn_mlm.py --scenarios 50 --ticks 200  # larger dataset
  python3 ntn_mlm.py --predict                   # demo inference, saved model
"""

import argparse
import csv
import math
import os
import pickle
import random
import sys
from collections import defaultdict

import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  SIMULATION CONSTANTS  (mirrors simulation/NTN.py)
# ═══════════════════════════════════════════════════════════════════════════════

PLANET_SIZE_ROOT = 3        # planet side-length (km)  → matches NTN.py size=3
GRID_SIZE   = (PLANET_SIZE_ROOT + 15) ** 2          # = 324  (L3 orbit side²)
CENTER      = [GRID_SIZE / 2, GRID_SIZE / 2]        # = [162, 162]

# Satellite name → (orbit_altitude_km, base_speed_km_s)
SAT_INFO = {
    "Sat1": (15,  25),
    "Sat2": (10,  50),
    "Sat3": (10,  50),
    "Sat4": ( 5, 100),
    "Sat5": ( 5, 100),
    "Sat6": ( 5, 100),
}

# Topology: satellite pairs that have physical veth links in the namespace setup
TOPOLOGY = [
    ("Sat1", "Sat2"), ("Sat1", "Sat3"),
    ("Sat2", "Sat3"),
    ("Sat2", "Sat4"), ("Sat2", "Sat5"),
    ("Sat3", "Sat5"), ("Sat3", "Sat6"),
    ("Sat4", "Sat5"),
    ("Sat5", "Sat6"),
]

# Maximum visibility range per link (km) — matches can_see_sat_sat() in NTN.py
RANGE_MAP = {
    ("Sat1", "Sat2"): 250, ("Sat1", "Sat3"): 250,
    ("Sat2", "Sat3"): 200,
    ("Sat2", "Sat4"): 220, ("Sat2", "Sat5"): 220,
    ("Sat3", "Sat5"): 220, ("Sat3", "Sat6"): 220,
    ("Sat4", "Sat5"): 180,
    ("Sat5", "Sat6"): 180,
}

# Link type label — encodes the orbital layer pair for the model
LINK_TYPE = {
    ("Sat1", "Sat2"): 0,   # L3–L2
    ("Sat1", "Sat3"): 0,
    ("Sat2", "Sat3"): 1,   # L2–L2
    ("Sat2", "Sat4"): 2,   # L2–L1
    ("Sat2", "Sat5"): 2,
    ("Sat3", "Sat5"): 2,
    ("Sat3", "Sat6"): 2,
    ("Sat4", "Sat5"): 3,   # L1–L1
    ("Sat5", "Sat6"): 3,
}

TICK_DT = 10.0   # seconds per simulation tick


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  ORBIT PHYSICS  (replicated from NTN.py / attempt-to-link.py)
# ═══════════════════════════════════════════════════════════════════════════════

def orbit_half_side(altitude):
    """Half the side-length of the square orbit for a given altitude (km)."""
    return ((PLANET_SIZE_ROOT + altitude) ** 2) / 2


def square_position(time_s, speed, phase, half_side):
    """
    Satellite position on a clockwise square orbit.
    Matches Orbit.update_position() in NTN.py exactly.
    """
    cx, cy = CENTER
    if half_side <= 0:
        return cx, cy
    side      = half_side * 2
    perimeter = side * 4
    dist      = (phase + speed * time_s) % perimeter

    x = cx + half_side
    y = cy + half_side
    if dist <= side:
        y -= dist
    elif dist <= side * 2:
        y -= side
        x -= (dist - side)
    elif dist <= side * 3:
        x -= side
        y += (dist - side * 2)
    else:
        y += side
        x += (dist - side * 3)
    return x, y


def compute_phases():
    """
    Phase offsets matching NTN.py: satellites in the same orbit are evenly
    distributed around the perimeter.
    """
    phases = {"Sat1": 0.0}

    l2_half  = orbit_half_side(10)
    l2_perim = l2_half * 2 * 4
    phases["Sat2"] = 0.0
    phases["Sat3"] = l2_perim / 2

    l1_half  = orbit_half_side(5)
    l1_perim = l1_half * 2 * 4
    phases["Sat4"] = 0.0
    phases["Sat5"] = l1_perim / 3
    phases["Sat6"] = 2 * l1_perim / 3

    return phases


def compute_delay_ms(alt_a, alt_b, x1, y1, x2, y2):
    """
    One-way link delay estimate.
    Matches compute_delay_ms() in namespace-network/attempt-to-link.py.
    """
    dist     = math.hypot(x2 - x1, y2 - y1)
    avg_alt  = (alt_a + alt_b) / 2
    return round(avg_alt * 8 + dist * 0.05, 2)


def link_is_up(a, b, pos_a, pos_b):
    """True when the two satellites are within visibility range."""
    key = tuple(sorted([a, b]))
    return math.hypot(pos_a[0] - pos_b[0], pos_a[1] - pos_b[1]) <= RANGE_MAP.get(key, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  DATA GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def generate_dataset(n_scenarios=30, ticks=120, speed_variation=0.15):
    """
    Produce a training DataFrame by running the orbit simulation with slight
    speed variations across scenarios to create diverse position patterns.

    Each row = one (link, tick) pair with current-tick features and the
    ONE-TICK-AHEAD delay as the regression target.  Rows where the link is
    down at the target tick are excluded (delay = undefined).
    """
    base_phases = compute_phases()
    records     = []

    for scenario in range(n_scenarios):
        factor = 1.0 + random.uniform(-speed_variation, speed_variation)
        speeds = {sat: info[1] * factor for sat, info in SAT_INFO.items()}

        # Scale phases proportionally so spacing stays even
        phases = {sat: base_phases[sat] * factor for sat in base_phases}

        # Pre-compute all positions for this scenario
        half_sides = {sat: orbit_half_side(SAT_INFO[sat][0]) for sat in SAT_INFO}
        tick_pos   = {}
        for tick in range(ticks):
            time_s = tick * TICK_DT
            tick_pos[tick] = {
                sat: square_position(time_s, speeds[sat], phases[sat], half_sides[sat])
                for sat in SAT_INFO
            }

        # Build link-level rows: features at tick t, target at tick t+1
        for tick in range(ticks - 1):
            pos_cur  = tick_pos[tick]
            pos_next = tick_pos[tick + 1]

            for (a, b) in TOPOLOGY:
                alt_a, alt_b = SAT_INFO[a][0], SAT_INFO[b][0]

                # ── current-tick values ──────────────────────────────────────
                x_a,  y_a  = pos_cur[a]
                x_b,  y_b  = pos_cur[b]
                dist_cur   = math.hypot(x_a - x_b, y_a - y_b)
                up_cur     = link_is_up(a, b, pos_cur[a],  pos_cur[b])
                delay_cur  = (compute_delay_ms(alt_a, alt_b, x_a, y_a, x_b, y_b)
                              if up_cur else 0.0)

                # ── next-tick values (TARGET) ────────────────────────────────
                x_an, y_an = pos_next[a]
                x_bn, y_bn = pos_next[b]
                up_next    = link_is_up(a, b, pos_next[a], pos_next[b])

                if not up_next:
                    continue   # skip: target delay undefined when link is down

                delay_next = compute_delay_ms(alt_a, alt_b, x_an, y_an, x_bn, y_bn)
                dist_next  = math.hypot(x_an - x_bn, y_an - y_bn)

                # ── velocity components (Δposition / tick) ──────────────────
                dx_a = x_an - x_a;  dy_a = y_an - y_a
                dx_b = x_bn - x_b;  dy_b = y_bn - y_b

                rel_x = x_a  - x_b
                rel_y = y_a  - y_b

                # Speed of approach (> 0 means satellites getting closer)
                approach = -(rel_x * (dx_a - dx_b) + rel_y * (dy_a - dy_b)) / (dist_cur + 1e-9)

                records.append({
                    # identifiers
                    "scenario":      scenario,
                    "tick":          tick,
                    "link":          f"{a}-{b}",
                    "link_type_enc": LINK_TYPE.get((a, b), 2),
                    # current-tick satellite positions
                    "x_a":   x_a,  "y_a":  y_a,  "alt_a": alt_a,
                    "x_b":   x_b,  "y_b":  y_b,  "alt_b": alt_b,
                    # derived geometry
                    "dist_cur":    dist_cur,
                    "avg_alt":     (alt_a + alt_b) / 2,
                    "alt_diff":    abs(alt_a - alt_b),
                    "rel_x":       rel_x,
                    "rel_y":       rel_y,
                    "link_angle":  math.atan2(rel_y, rel_x),
                    # velocity & dynamics
                    "dx_a":        dx_a,  "dy_a": dy_a,
                    "dx_b":        dx_b,  "dy_b": dy_b,
                    "rel_dx":      dx_a - dx_b,
                    "rel_dy":      dy_a - dy_b,
                    "approach":    approach,
                    # current link state
                    "up_cur":      int(up_cur),
                    "delay_cur":   delay_cur,
                    # targets
                    "dist_next":   dist_next,
                    "delay_next":  delay_next,
                })

    df = pd.DataFrame(records)
    print(f"  Generated {len(df):,} samples  |  "
          f"{df['link'].nunique()} links  |  "
          f"{n_scenarios} scenarios × {ticks} ticks")
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  MACHINE LEARNING  —  Gradient Boosted Decision Trees (numpy)
# ═══════════════════════════════════════════════════════════════════════════════

class _Node:
    """Single node in a regression tree."""
    __slots__ = ("feat", "thresh", "left", "right", "value")

    def __init__(self):
        self.feat   = None
        self.thresh = None
        self.left   = None
        self.right  = None
        self.value  = None   # leaf prediction (mean of y)


class DecisionTreeRegressor:
    """
    Variance-reduction decision tree for regression.
    Uses up to `n_splits` candidate thresholds per feature (percentiles),
    which keeps pure-Python tree building fast even for 10k+ samples.
    """

    def __init__(self, max_depth=5, min_samples=5, n_feats=None, n_splits=20):
        self.max_depth   = max_depth
        self.min_samples = min_samples
        self.n_feats     = n_feats    # None → use all
        self.n_splits    = n_splits
        self.root        = None
        self.n_features_ = 0

    # ── training ─────────────────────────────────────────────────────────────

    def fit(self, X, y):
        self.n_features_ = X.shape[1]
        self.root = self._build(X, y, 0)
        return self

    def _build(self, X, y, depth):
        node = _Node()
        if len(y) < self.min_samples or depth >= self.max_depth or np.ptp(y) < 1e-8:
            node.value = float(np.mean(y))
            return node

        n_feats   = self.n_feats or X.shape[1]
        feat_idx  = np.random.choice(X.shape[1], min(n_feats, X.shape[1]), replace=False)
        best_score, best_feat, best_thresh = np.inf, None, None
        parent_n  = len(y)

        for fi in feat_idx:
            col = X[:, fi]
            pcts = np.percentile(col, np.linspace(5, 95, self.n_splits))
            threshs = np.unique(pcts)
            for t in threshs:
                lm = col <= t
                rm = ~lm
                if lm.sum() < 2 or rm.sum() < 2:
                    continue
                score = (np.var(y[lm]) * lm.sum() + np.var(y[rm]) * rm.sum())
                if score < best_score:
                    best_score, best_feat, best_thresh = score, fi, t

        if best_feat is None:
            node.value = float(np.mean(y))
            return node

        node.feat   = best_feat
        node.thresh = best_thresh
        lm = X[:, best_feat] <= best_thresh
        node.left  = self._build(X[lm],  y[lm],  depth + 1)
        node.right = self._build(X[~lm], y[~lm], depth + 1)
        return node

    # ── inference ─────────────────────────────────────────────────────────────

    def _predict_row(self, x, node):
        if node.value is not None:
            return node.value
        return (self._predict_row(x, node.left)
                if x[node.feat] <= node.thresh
                else self._predict_row(x, node.right))

    def predict(self, X):
        return np.array([self._predict_row(x, self.root) for x in X])


class GradientBoostingRegressor:
    """
    Gradient Boosted Decision Trees for regression (MSE loss).
    Fits shallow trees to residuals and accumulates them with a shrinkage
    learning-rate — identical in concept to sklearn's GradientBoostingRegressor.
    """

    def __init__(self, n_estimators=100, lr=0.08,
                 max_depth=5, min_samples=8, subsample=0.8, n_splits=20):
        self.n_estimators = n_estimators
        self.lr           = lr
        self.max_depth    = max_depth
        self.min_samples  = min_samples
        self.subsample    = subsample
        self.n_splits     = n_splits
        self.base_pred    = 0.0
        self.trees        = []
        self.train_losses = []

    def fit(self, X, y, verbose=True):
        n = len(y)
        self.base_pred = float(np.mean(y))
        residuals      = y - self.base_pred

        for i in range(self.n_estimators):
            # stochastic sub-sampling
            idx    = np.random.choice(n, int(n * self.subsample), replace=False)
            tree   = DecisionTreeRegressor(
                        max_depth=self.max_depth,
                        min_samples=self.min_samples,
                        n_splits=self.n_splits)
            tree.fit(X[idx], residuals[idx])
            update    = tree.predict(X)
            residuals -= self.lr * update
            self.trees.append(tree)

            mse = float(np.mean(residuals ** 2))
            self.train_losses.append(mse)
            if verbose and (i + 1) % 25 == 0:
                print(f"    [{i+1:3d}/{self.n_estimators}]  train MSE = {mse:.4f}")
        return self

    def predict(self, X):
        pred = np.full(len(X), self.base_pred)
        for tree in self.trees:
            pred += self.lr * tree.predict(X)
        return pred

    def feature_importances(self, n_features):
        """
        Approximate importance: count how often each feature is used as a
        split across all trees, weighted by the node's training-set size
        (approximated here by a uniform count).
        """
        counts = np.zeros(n_features)
        def _visit(node):
            if node.value is not None:
                return
            counts[node.feat] += 1
            _visit(node.left)
            _visit(node.right)
        for tree in self.trees:
            _visit(tree.root)
        total = counts.sum() or 1
        return counts / total


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  FEATURE / TARGET COLUMNS
# ═══════════════════════════════════════════════════════════════════════════════

FEATURES = [
    "x_a",  "y_a",  "alt_a",
    "x_b",  "y_b",  "alt_b",
    "dist_cur",
    "avg_alt",  "alt_diff",
    "rel_x",    "rel_y",
    "link_angle",
    "dx_a",     "dy_a",
    "dx_b",     "dy_b",
    "rel_dx",   "rel_dy",
    "approach",
    "up_cur",   "delay_cur",
    "link_type_enc",
    "tick",
]
TARGET = "delay_next"


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  METRICS
# ═══════════════════════════════════════════════════════════════════════════════

def mae(y_true, y_pred):
    return float(np.mean(np.abs(y_true - y_pred)))

def rmse(y_true, y_pred):
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))

def r2(y_true, y_pred):
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    return float(1 - ss_res / (ss_tot + 1e-12))


# ═══════════════════════════════════════════════════════════════════════════════
# 7.  TRAINING PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def train(df, n_estimators=100, lr=0.08, max_depth=5):
    """
    Train/test split by scenario (last 20% of scenarios held out),
    train a GradientBoostingRegressor, print evaluation metrics.
    Returns (model, X_test_np, y_test_np, feature_names).
    """
    X = df[FEATURES].values.astype(float)
    y = df[TARGET].values.astype(float)

    n_scen     = df["scenario"].nunique()
    split_scen = int(n_scen * 0.8)
    test_mask  = df["scenario"] >= split_scen

    X_tr, X_te = X[~test_mask], X[test_mask]
    y_tr, y_te = y[~test_mask], y[test_mask]

    sep = "═" * 62
    print(f"\n{sep}")
    print("  NTN Link Delay Regression  —  Gradient Boosted Trees")
    print(sep)
    print(f"  Train samples : {len(X_tr):,}   "
          f"({split_scen}/{n_scen} scenarios)")
    print(f"  Test  samples : {len(X_te):,}   "
          f"({n_scen - split_scen}/{n_scen} scenarios)")
    print(f"  Features      : {len(FEATURES)}")
    print(f"  Target        : {TARGET}  (ms, one tick ahead)")
    print(f"\n  Training  {n_estimators} estimators  "
          f"(lr={lr}, max_depth={max_depth}) …")

    model = GradientBoostingRegressor(
        n_estimators=n_estimators,
        lr=lr,
        max_depth=max_depth,
        min_samples=8,
        subsample=0.8,
    )
    model.fit(X_tr, y_tr, verbose=True)

    # ── evaluate ─────────────────────────────────────────────────────────────
    tr_pred = model.predict(X_tr)
    te_pred = model.predict(X_te)

    print(f"\n  {'─'*58}")
    print(f"  {'Metric':<22}  {'Train':>10}  {'Test':>10}")
    print(f"  {'─'*58}")
    print(f"  {'MAE  (ms)':<22}  {mae(y_tr, tr_pred):>10.3f}  {mae(y_te, te_pred):>10.3f}")
    print(f"  {'RMSE (ms)':<22}  {rmse(y_tr, tr_pred):>10.3f}  {rmse(y_te, te_pred):>10.3f}")
    print(f"  {'R²':<22}  {r2(y_tr, tr_pred):>10.4f}  {r2(y_te, te_pred):>10.4f}")
    print(f"  {'─'*58}")

    # ── feature importances ───────────────────────────────────────────────────
    imps = model.feature_importances(len(FEATURES))
    ranked = sorted(zip(FEATURES, imps), key=lambda kv: kv[1], reverse=True)
    print(f"\n  Top 10 Feature Importances:")
    for name, imp in ranked[:10]:
        bar = "█" * int(imp * 60)
        print(f"    {name:<22}  {bar}  {imp:.4f}")

    # ── sample predictions ────────────────────────────────────────────────────
    print(f"\n  Sample Predictions vs Actuals (first 8 test rows):")
    print(f"    {'Link':<16}  {'Predicted':>10}  {'Actual':>8}  {'Error':>8}")
    for i in range(min(8, len(X_te))):
        link = df.loc[test_mask, "link"].iloc[i]
        print(f"    {link:<16}  {te_pred[i]:>9.1f}ms  "
              f"{y_te[i]:>6.1f}ms  {abs(te_pred[i]-y_te[i]):>6.1f}ms")

    return model, X_te, y_te


# ═══════════════════════════════════════════════════════════════════════════════
# 8.  MODEL PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

MODEL_FILE = "ntn_delay_model.pkl"

def save(model, path=MODEL_FILE):
    bundle = {"model": model, "features": FEATURES, "target": TARGET}
    with open(path, "wb") as f:
        pickle.dump(bundle, f)
    size_kb = os.path.getsize(path) / 1024
    print(f"\n  Model saved → {path}  ({size_kb:.1f} KB)")

def load(path=MODEL_FILE):
    with open(path, "rb") as f:
        return pickle.load(f)


# ═══════════════════════════════════════════════════════════════════════════════
# 9.  PREDICTION INTERFACE
#     Drop-in for use alongside attempt-to-link.py
# ═══════════════════════════════════════════════════════════════════════════════

def predict_next_tick(sat_states, model, tick=0):
    """
    Predict link delays for the NEXT tick given the current tick's sat states.

    Parameters
    ----------
    sat_states : dict
        Matches the format from attempt-to-link.py load_simulation():
        { sat_name: {"alt": float, "x": float, "y": float, "can_see": [...]} }
    model : GradientBoostingRegressor
        Loaded via ntn_mlm.load()["model"]
    tick : int
        Current tick index (used as a feature)

    Returns
    -------
    dict  { "SatA-SatB": predicted_delay_ms }
    """
    rows = []
    link_keys = []

    for (a, b) in TOPOLOGY:
        if a not in sat_states or b not in sat_states:
            continue
        sa, sb = sat_states[a], sat_states[b]
        x_a, y_a, alt_a = sa["x"], sa["y"], sa["alt"]
        x_b, y_b, alt_b = sb["x"], sb["y"], sb["alt"]

        dist    = math.hypot(x_a - x_b, y_a - y_b)
        up      = link_is_up(a, b, (x_a, y_a), (x_b, y_b))
        delay_c = compute_delay_ms(alt_a, alt_b, x_a, y_a, x_b, y_b) if up else 0.0
        rel_x   = x_a - x_b
        rel_y   = y_a - y_b
        # velocity unknown without previous tick → zeros
        dx_a = dy_a = dx_b = dy_b = 0.0
        approach = 0.0

        rows.append([
            x_a,  y_a,  alt_a,
            x_b,  y_b,  alt_b,
            dist,
            (alt_a + alt_b) / 2,  abs(alt_a - alt_b),
            rel_x,  rel_y,
            math.atan2(rel_y, rel_x),
            dx_a,  dy_a,
            dx_b,  dy_b,
            dx_a - dx_b,  dy_a - dy_b,
            approach,
            int(up),  delay_c,
            float(LINK_TYPE.get((a, b), 2)),
            float(tick),
        ])
        link_keys.append(f"{a}-{b}")

    if not rows:
        return {}

    X    = np.array(rows, dtype=float)
    pred = model.predict(X)
    return {k: max(0.0, round(float(p), 1)) for k, p in zip(link_keys, pred)}


# ═══════════════════════════════════════════════════════════════════════════════
# 10.  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="NTN Link Delay Prediction MLM")
    ap.add_argument("--predict",   action="store_true",
                    help="Load saved model and run demo inference")
    ap.add_argument("--scenarios", type=int, default=30,
                    help="Number of simulation scenarios  (default 30)")
    ap.add_argument("--ticks",     type=int, default=120,
                    help="Ticks per scenario              (default 120)")
    ap.add_argument("--trees",     type=int, default=100,
                    help="Number of boosting estimators   (default 100)")
    ap.add_argument("--depth",     type=int, default=5,
                    help="Max tree depth                  (default 5)")
    ap.add_argument("--lr",        type=float, default=0.08,
                    help="Gradient boosting learning rate (default 0.08)")
    args = ap.parse_args()

    # ── demo prediction mode ─────────────────────────────────────────────────
    if args.predict:
        if not os.path.exists(MODEL_FILE):
            print(f"[error] No saved model found at {MODEL_FILE}.")
            print("        Run without --predict first to train and save the model.")
            sys.exit(1)

        print(f"Loading model from {MODEL_FILE} …")
        bundle = load()
        model  = bundle["model"]

        # Tick-0 satellite states taken from the existing simulation CSV
        demo_state = {
            "Sat1": {"alt": 15, "x": 324.0,    "y": 324.0,    "can_see": ["Sat2"]},
            "Sat2": {"alt": 10, "x": 246.5,    "y": 246.5,    "can_see": ["Sat1", "Sat4", "Sat5"]},
            "Sat3": {"alt": 10, "x":  77.5,    "y":  77.5,    "can_see": ["Sat5", "Sat6"]},
            "Sat4": {"alt":  5, "x": 194.0,    "y": 194.0,    "can_see": ["Sat2", "Sat5", "Host1"]},
            "Sat5": {"alt":  5, "x": 172.666,  "y": 130.0,    "can_see": ["Sat2", "Sat3", "Sat4", "Sat6"]},
            "Sat6": {"alt":  5, "x": 130.0,    "y": 236.666,  "can_see": ["Sat3", "Sat5"]},
        }

        preds = predict_next_tick(demo_state, model, tick=0)
        print("\n  Predicted delays at next tick (Tick 0 → Tick 1):")
        print(f"  {'Link':<16}  {'Predicted Delay':>16}")
        print(f"  {'─'*16}  {'─'*16}")
        for link, delay in sorted(preds.items()):
            print(f"  {link:<16}  {delay:>13.1f} ms")
        print()
        return

    # ── training pipeline ────────────────────────────────────────────────────
    random.seed(42)
    np.random.seed(42)

    print("=" * 62)
    print("  NTN Link Delay Prediction Model  —  Training Pipeline")
    print("=" * 62)
    print(f"\nStep 1/3  Generating data …")
    print(f"  Scenarios : {args.scenarios}  |  Ticks : {args.ticks}  "
          f"|  Speed variation : ±15 %")
    df = generate_dataset(n_scenarios=args.scenarios, ticks=args.ticks)

    print(f"\nStep 2/3  Training model …")
    model, X_te, y_te = train(df, n_estimators=args.trees,
                               lr=args.lr, max_depth=args.depth)

    print(f"\nStep 3/3  Saving model …")
    save(model)

    print("\n" + "=" * 62)
    print("  Training complete.")
    print("  Run `python3 ntn_mlm.py --predict` to test inference.")
    print("  Import predict_next_tick() into attempt-to-link.py to")
    print("  enable delay-predictive routing in the live namespace.")
    print("=" * 62)


if __name__ == "__main__":
    main()
