"""Append-only record of every decision the agent makes.

This exists so the question "does this strategy make money?" can be answered
from data instead of assumed. Every evaluated candidate is written with its
forecast, the live ask it was priced against, the computed edge and size, and
what happened to the order - in dry-run mode too, where nothing is sent.

Two things become measurable once enough rows accumulate:

  calibration  - group the forecasts into probability buckets and compare the
                 forecast probability against how often those markets actually
                 resolved yes. A forecaster that says 70% and is right 50% of
                 the time has negative edge no matter what the edge column
                 claims.
  realized P&L - for resolved markets, compare the price paid against the
                 resolution. This is the only number that settles the question.

Run in dry-run mode across a few dozen markets, wait for them to resolve, then
measure. That costs nothing but time and is the only honest way to find out
whether the strategy has an edge before funding it.
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional

DEFAULT_PATH = "trade_journal.jsonl"


class Journal:
    def __init__(self, path: Optional[str] = None):
        self.path = path or os.getenv("POLYMARKET_JOURNAL", DEFAULT_PATH)

    def _append(self, record: dict) -> None:
        record["ts"] = datetime.now(timezone.utc).isoformat()
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")

    def record_candidate(self, candidate, selected: bool) -> None:
        """Log a scored candidate, chosen or not.

        The rejected ones matter: they are the control group for whether the
        selection rule is picking better-than-average trades.
        """
        self._append(
            {
                "kind": "candidate",
                "selected": selected,
                **candidate.model_dump(),
            }
        )

    def record_skip(self, market_question: str, reason: str) -> None:
        self._append(
            {"kind": "skip", "market_question": market_question, "reason": reason}
        )

    def record_order(self, plan, result: str, dry_run: bool) -> None:
        self._append(
            {
                "kind": "order",
                "dry_run": dry_run,
                "result": str(result),
                **plan.model_dump(),
            }
        )

    def notional_since(self, since_iso: str) -> float:
        """Total USDC committed by live orders at or after `since_iso`.

        Used for the daily cap. Dry runs are excluded: they spend nothing.
        """
        if not os.path.exists(self.path):
            return 0.0
        total = 0.0
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("kind") != "order" or row.get("dry_run"):
                    continue
                if str(row.get("ts", "")) >= since_iso:
                    total += float(row.get("amount_usdc", 0.0))
        return total

    def spent_today(self) -> float:
        start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return self.notional_since(start.isoformat())
