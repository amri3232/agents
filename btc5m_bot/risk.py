"""Risikomanagement: Limits, Drawdown-Halt, Circuit Breaker (API-Latenz/-Fehler), Fenster-Budget."""
from __future__ import annotations

import logging
import statistics
import time
from collections import deque
from typing import Callable, Deque, List, Optional, Tuple

from config import Config
from models import BUY, EXIT, Decision, OpenOrder
from state import Portfolio
from utils import floor_size

log = logging.getLogger("risk")


class CircuitBreaker:
    """Öffnet bei hoher API-Latenz (Median) oder aufeinanderfolgenden Fehlern.

    Beim Auslösen wird für ``cooldown`` Sekunden nicht gehandelt (Cooldown verdoppelt sich bei
    wiederholtem Auslösen, bis ``BREAKER_MAX_COOLDOWN_SEC``) und alle ``on_trip``-Callbacks laufen
    (der Bot storniert dann offene Orders).
    """

    def __init__(self, cfg: Config, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.clock = clock
        self.latencies: Deque[float] = deque(maxlen=cfg.LATENCY_SAMPLES)
        self.consecutive_errors = 0
        self.open_until = 0.0
        self.trips = 0
        self.last_trip_ts = 0.0
        self.reason = ""
        self.on_trip: List[Callable[[str], None]] = []

    def is_open(self, now: Optional[float] = None) -> bool:
        return (self.clock() if now is None else now) < self.open_until

    def record(self, ok: bool, latency_ms: Optional[float] = None) -> None:
        now = self.clock()
        if ok:
            self.consecutive_errors = 0
            if latency_ms is not None:
                self.latencies.append(latency_ms)
                if (len(self.latencies) == self.latencies.maxlen
                        and statistics.median(self.latencies) > self.cfg.API_LATENCY_LIMIT_MS):
                    self._trip(now, f"API-Latenz (Median {statistics.median(self.latencies):.0f} ms "
                                    f"> {self.cfg.API_LATENCY_LIMIT_MS:.0f} ms)")
        else:
            self.consecutive_errors += 1
            if self.consecutive_errors >= self.cfg.MAX_CONSECUTIVE_API_ERRORS:
                self._trip(now, f"{self.consecutive_errors} API-Fehler in Folge")

    def _trip(self, now: float, reason: str) -> None:
        if now < self.open_until:
            return  # läuft bereits
        if now - self.last_trip_ts > 600:   # 10 Minuten Ruhe -> Eskalation zurücksetzen
            self.trips = 0
        cooldown = min(self.cfg.BREAKER_COOLDOWN_SEC * (2 ** self.trips), self.cfg.BREAKER_MAX_COOLDOWN_SEC)
        self.trips += 1
        self.open_until = now + cooldown
        self.last_trip_ts = now
        self.reason = reason
        self.latencies.clear()
        self.consecutive_errors = 0
        log.error("CIRCUIT BREAKER offen für %.0fs: %s", cooldown, reason)
        for cb in self.on_trip:
            try:
                cb(reason)
            except Exception:
                log.exception("on_trip-Callback fehlgeschlagen")


class RiskManager:
    def __init__(self, cfg: Config, portfolio: Portfolio, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.pf = portfolio
        self.clock = clock
        self.breaker = CircuitBreaker(cfg, clock)
        self.pause_until = 0.0
        self.pause_reason = ""
        self.wallet_usd: Optional[float] = None   # LIVE: zuletzt bekanntes USDC-Guthaben

    # -------------------------------------------------- Eingaben (von HTTP/Executor)
    def on_api_result(self, name: str, ok: bool, latency_ms: Optional[float]) -> None:
        """Signatur passt zu ``HttpClient.observers``. Nur Orderbuch-/Order-Aufrufe zählen für die Latenz."""
        self.breaker.record(ok, latency_ms if name in ("book", "order") else None)

    # ------------------------------------------------------------------ Status
    def update(self, now: Optional[float] = None) -> None:
        """Periodisch aufrufen: Tageswechsel, Drawdown-/Verlustgrenzen, Verlustserie."""
        now = self.clock() if now is None else now
        self.pf.roll_day(now)
        pf, cfg = self.pf, self.cfg
        if not pf.halted:
            if pf.drawdown >= cfg.MAX_DRAWDOWN_USD:
                pf.halt(f"Max. Drawdown erreicht ({pf.drawdown:.2f} USD >= {cfg.MAX_DRAWDOWN_USD:.2f} USD)")
        if pf.consecutive_losses >= cfg.MAX_CONSECUTIVE_LOSSES and now >= self.pause_until:
            self.pause_until = now + cfg.LOSS_STREAK_PAUSE_SEC
            self.pause_reason = f"{pf.consecutive_losses} Verluste in Folge"
            pf.consecutive_losses = 0
            log.warning("Pause %.0f min wegen: %s", cfg.LOSS_STREAK_PAUSE_SEC / 60, self.pause_reason)

    def can_trade(self, now: Optional[float] = None) -> Tuple[bool, str]:
        """Dürfen NEUE Positionen eröffnet werden? (Exits sind davon unabhängig erlaubt.)"""
        now = self.clock() if now is None else now
        if self.pf.halted:
            return False, f"halted: {self.pf.halt_reason}"
        if self.pf.day_pnl <= -self.cfg.MAX_DAILY_LOSS_USD:
            return False, f"Tagesverlust-Limit ({self.pf.day_pnl:.2f} USD)"
        if self.breaker.is_open(now):
            return False, f"circuit_breaker: {self.breaker.reason}"
        if now < self.pause_until:
            return False, f"pause: {self.pause_reason}"
        return True, ""

    # -------------------------------------------------------------- Order-Freigabe
    def window_exposure(self, slug: str, open_orders: List[OpenOrder]) -> float:
        """Gebundenes Kapital im Fenster: Positionskosten + noch offene Kauforders."""
        reserved = sum(o.remaining_notional for o in open_orders if o.slug == slug and o.side == BUY)
        return self.pf.window_cost(slug) + reserved

    def approve(self, d: Decision, open_orders: List[OpenOrder], now: Optional[float] = None
                ) -> Tuple[Optional[Decision], str]:
        """Prüft/kürzt eine Entscheidung. Gibt (Decision|None, Begründung) zurück."""
        now = self.clock() if now is None else now
        if d.kind == EXIT:                       # Risiko reduzieren ist immer erlaubt
            return d, "ok"
        ok, why = self.can_trade(now)
        if not ok:
            return None, why
        if not (0.0 < d.price < 1.0) or d.shares <= 0:
            return None, "ungültiger Preis/Größe"
        exposure = self.window_exposure(d.market.slug, open_orders)
        reserved_all = sum(o.remaining_notional for o in open_orders if o.side == BUY)
        room_window = self.cfg.MAX_USD_PER_WINDOW - exposure
        room_cash = self.pf.free_cash(reserved_all)
        if self.wallet_usd is not None:
            room_cash = min(room_cash, max(0.0, self.wallet_usd - reserved_all))
        usd = min(d.notional, self.cfg.MAX_POSITION_USD, room_window, room_cash)
        shares = floor_size(usd / d.price)
        min_sz = d.market.min_order_size
        if shares < min_sz:
            return None, (f"Budget erschöpft (Fenster-Rest {room_window:.2f} USD, "
                          f"freies Kapital {room_cash:.2f} USD)")
        if shares < d.shares - 1e-9:
            d = Decision(d.kind, d.market, d.outcome, d.side, d.price, shares, d.mode, d.fair, d.edge,
                         d.reason + " (gekürzt)")
        return d, "ok"
