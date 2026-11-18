"""Analyse dump jsonl: bolt head_width vs angle (15 deg bins); nut failures vs blur / edge."""

import json
import sys

import numpy as np


def load(p):
    return [json.loads(x) for x in open(p)]


def angle_table(recs, keys=("head_width_in", "head_length_in", "shank_width_in", "total_length_in")):
    print(f"{'ang bin':>9} {'n':>4} " + " ".join(f"{k[:-3]:>17}" for k in keys) + "  conf/n")
    for lo in range(0, 180, 15):
        sel = [r for r in recs if lo <= r["ang"] < lo + 15 and r.get(keys[0]) is not None]
        if not sel:
            print(f"{lo:>3}-{lo + 15:<3}    0")
            continue
        cells = []
        for k in keys:
            v = np.array([r[k] for r in sel if r.get(k) is not None])
            cells.append(f"{np.median(v):.3f} [{np.percentile(v, 10):.3f},{np.percentile(v, 90):.3f}]")
        nconf = sum(r["verdict"] == "confident" for r in sel)
        print(f"{lo:>3}-{lo + 15:<3} {len(sel):>4} " + " ".join(f"{c:>17}" for c in cells) + f"  {nconf}/{len(sel)}")


def blur_table(recs):
    print("blur px bin   n  confident  oos  unres  med circ  hole_kind dist")
    for lo, hi in [(0, 1), (1, 2), (2, 4), (4, 8), (8, 16), (16, 99)]:
        sel = [r for r in recs if r["blur"] is not None and lo <= r["blur"] < hi]
        if not sel:
            continue
        v = [r["verdict"] for r in sel]
        circ = np.median([r["circularity"] for r in sel if r["circularity"] is not None])
        hk = {k: sum(r["hole_kind"] == k for r in sel) for k in (0.0, 1.0, 2.0)}
        print(f"{lo:>3}-{hi:<3} {len(sel):>5} {v.count('confident'):>9} {v.count('out_of_scope'):>5} {v.count('unresolved'):>6}  {circ:.3f}  {hk}")


def edge_table(recs):
    print("edge px bin   n  confident")
    for lo, hi in [(0, 20), (20, 50), (50, 100), (100, 9999)]:
        sel = [r for r in recs if lo <= r["edge"] < hi]
        if sel:
            print(f"{lo:>4}-{hi:<5} {len(sel):>4} {sum(r['verdict'] == 'confident' for r in sel):>5}")


p = sys.argv[1]
recs = load(p)
mode = sys.argv[2] if len(sys.argv) > 2 else "bolt"
if mode == "bolt":
    angle_table(recs)
else:
    blur_table(recs)
    edge_table(recs)
