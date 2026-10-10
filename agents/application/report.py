"""Summarise the decision journal.

Answers the only question that matters before funding the agent: were the
forecasts calibrated? A forecaster that says 70% and is right 50% of the time
has negative edge however large the computed edge column looks.

Resolution outcomes are not fetched here. Add a `resolved` field to a journal
row (true/false, for whether the chosen outcome happened) and this reports
realized calibration and P&L; without it, it reports what was decided and
staked so the shape of the strategy is visible.
"""

import json
import os
from collections import defaultdict
from typing import List

BUCKETS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]


def _load(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def summarise(path: str) -> str:
    rows = _load(path)
    if not rows:
        return f"No journal at {path}. Run the agent in dry-run mode first."

    candidates = [r for r in rows if r.get("kind") == "candidate"]
    orders = [r for r in rows if r.get("kind") == "order"]
    skips = [r for r in rows if r.get("kind") == "skip"]
    selected = [r for r in candidates if r.get("selected")]

    lines = [
        f"journal: {path}",
        f"  markets scored with edge : {len(candidates)}",
        f"  markets skipped          : {len(skips)}",
        f"  orders planned           : {len(orders)}",
        f"    of which live          : {sum(1 for o in orders if not o.get('dry_run'))}",
        f"  notional in live orders  : "
        f"{sum(float(o.get('amount_usdc', 0)) for o in orders if not o.get('dry_run')):.2f} USDC",
    ]

    if selected:
        avg_edge = sum(float(r.get("edge", 0)) for r in selected) / len(selected)
        avg_ev = sum(float(r.get("expected_value", 0)) for r in selected) / len(
            selected
        )
        lines += [
            "",
            f"  selected trades          : {len(selected)}",
            f"  mean claimed edge        : {avg_edge:+.3f}",
            f"  mean claimed EV / $1     : {avg_ev:+.3f}",
        ]

    resolved = [r for r in candidates if "resolved" in r]
    if not resolved:
        lines += [
            "",
            "  No resolved rows yet, so calibration cannot be measured and the",
            "  edge above is only a claim. Add a boolean `resolved` field to a",
            "  journal row once its market settles, then re-run this.",
        ]
        return "\n".join(lines)

    by_bucket = defaultdict(list)
    for r in resolved:
        p = float(r["probability"])
        for low, high in BUCKETS:
            if low <= p < high or (high == 1.0 and p == 1.0):
                by_bucket[(low, high)].append(r)
                break

    lines += ["", "  calibration (forecast vs. how often it actually happened):"]
    for bucket in BUCKETS:
        group = by_bucket.get(bucket, [])
        if not group:
            continue
        mean_p = sum(float(r["probability"]) for r in group) / len(group)
        hit = sum(1 for r in group if r["resolved"]) / len(group)
        lines.append(
            f"    p in [{bucket[0]:.1f},{bucket[1]:.1f}) n={len(group):3d}  "
            f"said {mean_p:.2f}  actual {hit:.2f}  gap {hit - mean_p:+.2f}"
        )

    pnl = 0.0
    for r in resolved:
        stake = float(r.get("size_fraction", 0)) * float(r.get("available_notional", 0))
        price = float(r["ask_price"])
        pnl += (stake / price - stake) if r["resolved"] else -stake
    lines += ["", f"  realized P&L on resolved rows: {pnl:+.2f} USDC"]
    return "\n".join(lines)
