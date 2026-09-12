"""Run the pipeline over a JSON file offline. Prints precision/recall per entity.

Usage: uv run python scripts/replay.py data/holdout.json [--tier1]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

from dotenv import load_dotenv

from blackline import pipeline
from blackline.contract import Message

load_dotenv()
path = sys.argv[1]
use_tier1 = "--tier1" in sys.argv
with open(path) as fh:
    rows = json.load(fh)
tp, fp, fn = defaultdict(int), defaultdict(int), defaultdict(int)
lat = []
for r in rows:
    msg = Message(
        id=r["ts"], channel_id="C0", channel_name=r["channel"], author_id=r["user"], text=r["text"]
    )
    d = pipeline.run(msg, use_tier1=use_tier1)
    lat.append(sum(v or 0 for v in d.timing_ms.values()))
    got = {f.entity for f in d.findings if f.subject != "self"}
    want = (
        {r["label"]["entity"]}
        if r["label"]["entity"] and r["label"]["subject"] != "self"
        else set()
    )
    for e in got & want:
        tp[e] += 1
    for e in got - want:
        fp[e] += 1
    for e in want - got:
        fn[e] += 1
lat.sort()
print(f"{'entity':14} {'prec':>6} {'rec':>6}   tp/fp/fn")
for e in sorted(set(tp) | set(fp) | set(fn)):
    p = tp[e] / (tp[e] + fp[e]) if tp[e] + fp[e] else 0
    r = tp[e] / (tp[e] + fn[e]) if tp[e] + fn[e] else 0
    print(f"{e:14} {p:6.2f} {r:6.2f}   {tp[e]}/{fp[e]}/{fn[e]}")
print(f"\nlatency p50={lat[len(lat) // 2]}ms p95={lat[int(len(lat) * 0.95)]}ms n={len(lat)}")
