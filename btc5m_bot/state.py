"""Portfolio-Buchhaltung + Persistenz (JSON) + Trade-Journal (CSV).

Das Portfolio ist die einzige Quelle der Wahrheit für Positionen und realisierten PnL - im
PAPER- wie im LIVE-Modus. Es wird nach jeder Änderung atomar gespeichert, damit ein Neustart
weder offene Positionen (-> Redeem!) noch einen ausgelösten Drawdown-Halt vergisst.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from config import Config
from models import BUY, SELL, Market, Position, position_key

log = logging.getLogger("state")

JOURNAL_FIELDS = ["ts", "mode", "event", "slug", "outcome", "side", "shares", "price", "fee",
                  "fair", "edge", "pnl", "won", "note"]


def utc_day(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc).strftime("%Y-%m-%d")


class Journal:
    """Append-only CSV mit allen Fills und Abrechnungen (Basis für ``--report``)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, **row) -> None:
        try:
            new = not self.path.exists() or self.path.stat().st_size == 0
            with open(self.path, "a", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=JOURNAL_FIELDS, extrasaction="ignore")
                if new:
                    w.writeheader()
                row.setdefault("ts", f"{time.time():.3f}")
                w.writerow(row)
        except OSError:
            log.exception("Journal konnte nicht geschrieben werden")


class Portfolio:
    def __init__(self, cfg: Config, path: Optional[Path] = None, journal: Optional[Journal] = None):
        self.cfg = cfg
        self.path = Path(path) if path else None
        self.journal = journal
        self.positions: Dict[str, Position] = {}
        self.realized_pnl: float = 0.0
        self.peak_realized: float = 0.0
        self.day: str = utc_day()
        self.day_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.halted: bool = False
        self.halt_reason: str = ""
        self.trades: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self.fees_paid: float = 0.0

    # ----------------------------------------------------------------- Abfragen
    def get(self, slug: str, outcome: str) -> Optional[Position]:
        return self.positions.get(position_key(slug, outcome))

    def shares(self, slug: str, outcome: str) -> float:
        p = self.get(slug, outcome)
        return p.shares if p and not p.settled else 0.0

    def window_cost(self, slug: str) -> float:
        """Gebundenes Kapital (Kostenbasis) im Fenster ``slug``."""
        return sum(p.cost for p in self.positions.values() if p.slug == slug and not p.settled)

    def open_cost(self) -> float:
        return sum(p.cost for p in self.positions.values() if not p.settled)

    def pending_settlement(self) -> List[Position]:
        return [p for p in self.positions.values() if not p.settled and p.shares > 1e-9]

    def pending_redeem(self) -> List[Position]:
        return [p for p in self.positions.values()
                if p.settled and p.won and not p.redeemed and not p.manual_redeem and p.payout > 0]

    @property
    def equity(self) -> float:
        """Eigenkapital zum Einstandswert: Bankroll + realisierter PnL."""
        return self.cfg.BANKROLL_USD + self.realized_pnl

    @property
    def drawdown(self) -> float:
        return max(0.0, self.peak_realized - self.realized_pnl)

    def free_cash(self, reserved: float = 0.0) -> float:
        return max(0.0, self.equity - self.open_cost() - reserved)

    # ------------------------------------------------------------------ Mutationen
    def apply_fill(self, market: Market, outcome: str, side: str, shares: float, price: float,
                   fee: float = 0.0, fair: float = 0.0, edge: float = 0.0, ts: Optional[float] = None) -> float:
        """Bucht einen Fill. Gibt den realisierten PnL dieses Fills zurück (nur bei SELL != 0)."""
        if shares <= 0:
            return 0.0
        pos = self.get(market.slug, outcome)
        if pos is None or pos.settled:
            pos = Position(slug=market.slug, condition_id=market.condition_id, outcome=outcome,
                           token_id=market.token(outcome), outcome_index=market.index(outcome),
                           start_ts=market.start_ts, end_ts=market.end_ts, mode=self.cfg.MODE)
            self.positions[pos.key] = pos
        pnl = 0.0
        notional = shares * price
        self.fees_paid += fee
        pos.fees += fee
        if side == BUY:
            pos.shares += shares
            pos.cost += notional + fee
            if fair:
                pos.fair, pos.edge = fair, edge
        elif side == SELL:
            sold = min(shares, pos.shares)
            avg = pos.avg_price
            proceeds = sold * price - fee
            pnl = proceeds - avg * sold
            pos.cost = max(0.0, pos.cost - avg * sold)
            pos.shares -= sold
            if pos.shares < 1e-9:
                pos.shares, pos.cost = 0.0, 0.0
            pos.realized += pnl
            self._book_pnl(pnl)
        if self.journal:
            self.journal.write(ts=f"{ts or time.time():.3f}", mode=self.cfg.MODE, event="FILL", slug=market.slug,
                               outcome=outcome, side=side, shares=f"{shares:.4f}", price=f"{price:.4f}",
                               fee=f"{fee:.4f}", fair=f"{fair:.4f}", edge=f"{edge:.4f}",
                               pnl=f"{pnl:.4f}" if side == SELL else "", note="")
        self.save()
        return pnl

    def settle(self, pos: Position, payout_ratio: float) -> float:
        """Rechnet eine Position ab. ``payout_ratio`` = Auszahlung je Share (1 = Gewinner, 0 = Verlierer,
        0.5 = 50/50-Auflösung). Gibt den PnL der Restposition zurück."""
        if pos.settled:
            return pos.pnl
        payout_ratio = max(0.0, min(1.0, payout_ratio))
        pos.settled = True
        pos.won = payout_ratio > 0
        pos.payout = pos.shares * payout_ratio
        resolved_pnl = pos.payout - pos.cost
        pos.pnl = resolved_pnl + pos.realized
        self._book_pnl(resolved_pnl)
        if pos.shares > 0:
            self.trades += 1
            if resolved_pnl > 0:
                self.wins += 1
                self.consecutive_losses = 0
            else:
                self.losses += 1
                self.consecutive_losses += 1
        if self.journal:
            self.journal.write(mode=pos.mode, event="SETTLE", slug=pos.slug, outcome=pos.outcome, side="",
                               shares=f"{pos.shares:.4f}", price=f"{pos.avg_price:.4f}", fee=f"{pos.fees:.4f}",
                               fair=f"{pos.fair:.4f}", edge=f"{pos.edge:.4f}", pnl=f"{resolved_pnl:.4f}",
                               won=int(bool(pos.won)), note="")
        self.save()
        return resolved_pnl

    def _book_pnl(self, pnl: float) -> None:
        self._roll_day()
        self.realized_pnl += pnl
        self.day_pnl += pnl
        self.peak_realized = max(self.peak_realized, self.realized_pnl)

    def _roll_day(self, ts: Optional[float] = None) -> None:
        today = utc_day(ts)
        if today != self.day:
            self.day = today
            self.day_pnl = 0.0

    def roll_day(self, ts: Optional[float] = None) -> None:
        self._roll_day(ts)

    def halt(self, reason: str) -> None:
        if not self.halted:
            log.error("TRADING GESTOPPT: %s (Neustart hebt den Halt NICHT auf - nutze --reset-halt)", reason)
        self.halted, self.halt_reason = True, reason
        self.save()

    def reset_halt(self) -> None:
        self.halted, self.halt_reason = False, ""
        self.peak_realized = self.realized_pnl  # Drawdown-Referenz zurücksetzen
        self.save()

    def prune(self, now: Optional[float] = None, keep_sec: float = 6 * 3600) -> None:
        """Entfernt alte, vollständig erledigte Positionen aus dem Speicher."""
        cutoff = (time.time() if now is None else now) - keep_sec
        done = lambda p: ((p.settled and (p.redeemed or p.manual_redeem or not p.won))     # noqa: E731
                          or (not p.settled and p.shares <= 1e-9))                          # komplett verkauft
        for k in [k for k, p in self.positions.items() if done(p) and p.end_ts < cutoff]:
            del self.positions[k]

    # --------------------------------------------------------------- Persistenz
    def save(self) -> None:
        if not self.path:
            return
        try:
            data = {
                "realized_pnl": self.realized_pnl, "peak_realized": self.peak_realized, "day": self.day,
                "day_pnl": self.day_pnl, "consecutive_losses": self.consecutive_losses,
                "halted": self.halted, "halt_reason": self.halt_reason, "trades": self.trades,
                "wins": self.wins, "losses": self.losses, "fees_paid": self.fees_paid,
                "positions": [asdict(p) for p in self.positions.values()],
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".state-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            log.exception("State konnte nicht gespeichert werden")

    def load(self) -> bool:
        if not self.path or not self.path.exists():
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("State-Datei unlesbar - starte mit leerem Zustand (Datei bleibt erhalten)")
            return False
        for k in ("realized_pnl", "peak_realized", "day", "day_pnl", "consecutive_losses", "halted",
                  "halt_reason", "trades", "wins", "losses", "fees_paid"):
            if k in data:
                setattr(self, k, data[k])
        known = {f.name for f in fields(Position)}
        for raw in data.get("positions", []):
            pos = Position(**{k: v for k, v in raw.items() if k in known})
            self.positions[pos.key] = pos
        self._roll_day()
        return True
