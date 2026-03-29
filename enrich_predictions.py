#!/usr/bin/env python3
"""
enrich_predictions.py — Add ML delay predictions & path comparisons
====================================================================
Reads ml_training_all_sims.csv + ntn_delay_model.pkl, and for every
(simulation, tick) pair:

  1. Predicts one-tick-ahead link delays using the trained GBDT model
  2. Computes AI-optimal path (Dijkstra on predicted delays)
  3. Computes OSPF baseline path (Dijkstra on OSPF costs)
  4. Records actual delay along both paths for comparison

Outputs: ml_training_enriched.csv  (original columns + prediction columns)

Usage:
  python3 enrich_predictions.py
  python3 enrich_predictions.py --csv ml_training_all_sims.csv --model ntn_delay_model.pkl
"""

import argparse
import math
import os
import pickle
import sys
from heapq import heappush, heappop

import numpy as np
import pandas as pd

# ── Import simulation constants and physics from ntn_mlm.py ──────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ntn_mlm
from ntn_mlm import (
    TOPOLOGY, SAT_INFO, LINK_TYPE, FEATURES,
    compute_delay_ms, link_is_up,
)

# Canonical topology link names (model was trained on this order)
TOPO_LINKS = [f"{a}-{b}" for a, b in TOPOLOGY]

# Source / Destination satellites (Host1→Sat4, Sat6→Host2)
SRC_SAT = "Sat4"
DST_SAT = "Sat6"


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_model(path):
    """Load the pickled model bundle, resolving __main__ refs to ntn_mlm."""
    class _Unpickler(pickle.Unpickler):
        def find_class(self, module, name):
            if module == "__main__":
                return getattr(ntn_mlm, name)
            return super().find_class(module, name)

    with open(path, "rb") as f:
        bundle = _Unpickler(f).load()
    return bundle["model"]


# ═══════════════════════════════════════════════════════════════════════════════
# SHORTEST PATH
# ═══════════════════════════════════════════════════════════════════════════════

def dijkstra(adj, source, target):
    """Return (cost, [node_path]) or (inf, []) if unreachable."""
    dist = {source: 0.0}
    prev = {source: None}
    heap = [(0.0, source)]
    while heap:
        d, u = heappop(heap)
        if u == target:
            path = []
            while u is not None:
                path.append(u)
                u = prev[u]
            return d, path[::-1]
        if d > dist.get(u, float("inf")):
            continue
        for v, w in adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heappush(heap, (nd, v))
    return float("inf"), []


def build_adj(link_weights):
    """Build undirected adjacency from {link_name: weight}. Skips None/0."""
    adj = {}
    for link, w in link_weights.items():
        if w is None or w <= 0:
            continue
        a, b = link.split("-")
        adj.setdefault(a, []).append((b, w))
        adj.setdefault(b, []).append((a, w))
    return adj


# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE EXTRACTION  (mirrors ntn_mlm.py's generate_dataset)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_features_for_tick(sat_cur, sat_prev, tick):
    """
    Build feature vectors for all TOPOLOGY links at the current tick.
    Uses sat_prev to compute velocity (dx, dy) approximations.

    Returns: (X_array, link_names)
    """
    rows = []
    links = []

    for (a, b) in TOPOLOGY:
        if a not in sat_cur or b not in sat_cur:
            continue

        x_a,  y_a  = sat_cur[a]["x"],  sat_cur[a]["y"]
        alt_a       = sat_cur[a]["alt"]
        x_b,  y_b  = sat_cur[b]["x"],  sat_cur[b]["y"]
        alt_b       = sat_cur[b]["alt"]

        dist_cur = math.hypot(x_a - x_b, y_a - y_b)
        up_cur   = link_is_up(a, b, (x_a, y_a), (x_b, y_b))
        delay_cur = compute_delay_ms(alt_a, alt_b, x_a, y_a, x_b, y_b) if up_cur else 0.0

        rel_x = x_a - x_b
        rel_y = y_a - y_b

        # Velocity from previous tick (or zero if unavailable)
        if sat_prev and a in sat_prev and b in sat_prev:
            dx_a = x_a - sat_prev[a]["x"]
            dy_a = y_a - sat_prev[a]["y"]
            dx_b = x_b - sat_prev[b]["x"]
            dy_b = y_b - sat_prev[b]["y"]
        else:
            dx_a = dy_a = dx_b = dy_b = 0.0

        approach = -(rel_x * (dx_a - dx_b) + rel_y * (dy_a - dy_b)) / (dist_cur + 1e-9)

        rows.append([
            x_a, y_a, alt_a,
            x_b, y_b, alt_b,
            dist_cur,
            (alt_a + alt_b) / 2, abs(alt_a - alt_b),
            rel_x, rel_y,
            math.atan2(rel_y, rel_x),
            dx_a, dy_a,
            dx_b, dy_b,
            dx_a - dx_b, dy_a - dy_b,
            approach,
            int(up_cur), delay_cur,
            float(LINK_TYPE.get((a, b), 2)),
            float(tick),
        ])
        links.append(f"{a}-{b}")

    return np.array(rows, dtype=float) if rows else np.empty((0, len(FEATURES))), links


# ═══════════════════════════════════════════════════════════════════════════════
# CSV HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_sat_positions(row):
    """Extract {sat_name: {x, y, alt}} from a wide-format CSV row."""
    sats = {}
    for n in range(1, 7):
        name = f"Sat{n}"
        x_col = f"{name}_x"
        if x_col in row.index and pd.notna(row[x_col]) and str(row[x_col]) != "":
            sats[name] = {
                "x": float(row[f"{name}_x"]),
                "y": float(row[f"{name}_y"]),
                "alt": float(row[f"{name}_alt"]),
            }
    return sats


def get_actual_delays(row, csv_links):
    """Get {link_name: delay_ms_or_None} from a wide-format CSV row."""
    delays = {}
    for link in csv_links:
        status = row.get(f"link_{link}_status", 0)
        delay  = row.get(f"link_{link}_delay_ms", "")
        if status == 1 and pd.notna(delay) and str(delay) != "":
            delays[link] = float(delay)
        else:
            delays[link] = None
    return delays


def get_ospf_costs(row, csv_links):
    """Get {link_name: ospf_cost_or_None} from a wide-format CSV row."""
    costs = {}
    for link in csv_links:
        status = row.get(f"link_{link}_status", 0)
        cost   = row.get(f"link_{link}_ospf_cost", "")
        if status == 1 and pd.notna(cost) and str(cost) != "":
            costs[link] = float(cost)
        else:
            costs[link] = None
    return costs


def topo_to_csv_link(topo_link, csv_links):
    """Map a topology link name (e.g. 'Sat2-Sat4') to CSV link name(s)."""
    a, b = topo_link.split("-")
    matches = []
    for cl in csv_links:
        ca, cb = cl.split("-")
        if (ca == a and cb == b) or (ca == b and cb == a):
            matches.append(cl)
    return matches


def path_actual_cost(path, actual_delays, csv_links):
    """Sum actual delays along a satellite path. Returns None if any hop is down."""
    cost = 0.0
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        found = False
        for cl in csv_links:
            ca, cb = cl.split("-")
            if (ca == a and cb == b) or (ca == b and cb == a):
                d = actual_delays.get(cl)
                if d is not None:
                    cost += d
                    found = True
                    break
        if not found:
            return None
    return round(cost, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Enrich CSV with ML predictions & path comparisons")
    ap.add_argument("--csv",   default="ml_training_all_sims.csv", help="Input CSV path")
    ap.add_argument("--model", default="ntn_delay_model.pkl",      help="Model pickle path")
    ap.add_argument("--out",   default="ml_training_enriched.csv", help="Output CSV path")
    args = ap.parse_args()

    print(f"Loading model from {args.model} …")
    model = load_model(args.model)
    print(f"  {len(model.trees)} estimators loaded")

    print(f"Loading CSV from {args.csv} …")
    df = pd.read_csv(args.csv)
    print(f"  {len(df)} rows, {df['sim_number'].nunique()} simulations")

    # Discover CSV link names
    csv_links = [
        c.replace("link_", "").replace("_delay_ms", "")
        for c in df.columns if c.startswith("link_") and c.endswith("_delay_ms")
    ]
    print(f"  {len(csv_links)} links: {csv_links}")

    # Initialize new columns
    for tl in TOPO_LINKS:
        df[f"pred_{tl}_delay_ms"] = np.nan
    df["ai_path"]          = ""
    df["ai_path_cost_ms"]  = np.nan     # actual delay along AI-chosen path
    df["ospf_path"]        = ""
    df["ospf_path_cost_ms"] = np.nan    # actual delay along OSPF-chosen path

    total_sims = df["sim_number"].nunique()

    for sim_i, sim in enumerate(sorted(df["sim_number"].unique())):
        sim_mask = df["sim_number"] == sim
        sim_idx  = df.index[sim_mask]
        sim_df   = df.loc[sim_idx].sort_values("tick")
        ticks    = sim_df["tick"].values
        n_ticks  = len(ticks)

        prev_sats = None

        for ti in range(n_ticks):
            row_idx  = sim_df.index[ti]
            row      = sim_df.iloc[ti]
            tick     = int(row["tick"])
            cur_sats = get_sat_positions(row)

            # Predict next-tick delays using current-tick features
            X, pred_links = extract_features_for_tick(cur_sats, prev_sats, tick)

            if len(X) > 0:
                preds = model.predict(X)
                preds = np.maximum(preds, 0.0)
                pred_dict = dict(zip(pred_links, preds))

                # If there's a next tick, store predictions there
                if ti + 1 < n_ticks:
                    next_idx = sim_df.index[ti + 1]

                    # Store per-link predictions
                    for tl, pv in pred_dict.items():
                        df.at[next_idx, f"pred_{tl}_delay_ms"] = round(float(pv), 2)

                    # ── AI path: Dijkstra on predicted delays ────────────────
                    # Map topology link predictions to satellite graph
                    ai_weights = {}
                    for tl, pv in pred_dict.items():
                        a, b = tl.split("-")
                        # Use predicted delay as weight
                        ai_weights[tl] = float(pv)
                    ai_adj = build_adj(ai_weights)
                    ai_cost, ai_path = dijkstra(ai_adj, SRC_SAT, DST_SAT)

                    if ai_path:
                        df.at[next_idx, "ai_path"] = "→".join(ai_path)
                        # Compute ACTUAL cost of AI path at the next tick
                        next_row    = df.loc[next_idx]
                        act_delays  = get_actual_delays(next_row, csv_links)
                        ai_act_cost = path_actual_cost(ai_path, act_delays, csv_links)
                        if ai_act_cost is not None:
                            df.at[next_idx, "ai_path_cost_ms"] = ai_act_cost

                    # ── OSPF path: Dijkstra on OSPF costs ────────────────────
                    next_row   = df.loc[next_idx]
                    ospf_costs = get_ospf_costs(next_row, csv_links)
                    # Build graph with CSV link names as keys
                    ospf_adj = build_adj(ospf_costs)
                    ospf_cost, ospf_path = dijkstra(ospf_adj, SRC_SAT, DST_SAT)

                    if ospf_path:
                        df.at[next_idx, "ospf_path"] = "→".join(ospf_path)
                        act_delays      = get_actual_delays(next_row, csv_links)
                        ospf_act_cost   = path_actual_cost(ospf_path, act_delays, csv_links)
                        if ospf_act_cost is not None:
                            df.at[next_idx, "ospf_path_cost_ms"] = ospf_act_cost

            prev_sats = cur_sats

        if (sim_i + 1) % 5 == 0 or sim_i == total_sims - 1:
            print(f"  Processed simulation {sim_i + 1}/{total_sims}")

    # ── Summary statistics ────────────────────────────────────────────────────
    pred_cols = [c for c in df.columns if c.startswith("pred_") and c.endswith("_delay_ms")]
    valid_preds = 0
    for tl in TOPO_LINKS:
        pred_c = f"pred_{tl}_delay_ms"
        # Find matching actual column(s)
        csv_matches = topo_to_csv_link(tl, csv_links)
        if csv_matches:
            act_c = f"link_{csv_matches[0]}_delay_ms"
            mask = df[pred_c].notna() & df[act_c].notna() & (df[act_c] != "")
            valid_preds += mask.sum()

    both_paths = df["ai_path_cost_ms"].notna() & df["ospf_path_cost_ms"].notna()
    ai_wins   = (df.loc[both_paths, "ai_path_cost_ms"] < df.loc[both_paths, "ospf_path_cost_ms"]).sum()
    ospf_wins = (df.loc[both_paths, "ai_path_cost_ms"] > df.loc[both_paths, "ospf_path_cost_ms"]).sum()
    ties      = both_paths.sum() - ai_wins - ospf_wins

    print(f"\n  ── Summary ──────────────────────────────────")
    print(f"  Valid predictions : {valid_preds}")
    print(f"  Path comparisons  : {both_paths.sum()}")
    print(f"  AI wins           : {ai_wins}  ({ai_wins/max(both_paths.sum(),1)*100:.1f}%)")
    print(f"  OSPF wins         : {ospf_wins}  ({ospf_wins/max(both_paths.sum(),1)*100:.1f}%)")
    print(f"  Ties              : {ties}")

    if both_paths.sum() > 0:
        savings = df.loc[both_paths, "ospf_path_cost_ms"] - df.loc[both_paths, "ai_path_cost_ms"]
        print(f"  Avg savings (ms)  : {savings.mean():.1f}")

    # Save
    df.to_csv(args.out, index=False)
    size_mb = os.path.getsize(args.out) / 1024 / 1024
    print(f"\n  Saved → {args.out}  ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
