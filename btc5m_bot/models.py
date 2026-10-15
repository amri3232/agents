"""Gemeinsame Datentypen (Markt, Orderbuch, Position, Entscheidung, Order)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

UP = "UP"
DOWN = "DOWN"
OUTCOMES = (UP, DOWN)

BUY = "BUY"
SELL = "SELL"

TAKER = "TAKER"
MAKER = "MAKER"

ENTER = "ENTER"
EXIT = "EXIT"


def opposite(outcome: str) -> str:
    return DOWN if outcome == UP else UP


def _f(value, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Market:
    """Ein 5-Minuten-BTC-Up/Down-Markt (ein Fenster)."""

    slug: str
    condition_id: str
    start_ts: int
    end_ts: int
    up_token: str
    down_token: str
    question: str = ""
    tick_size: float = 0.01
    min_order_size: float = 5.0
    neg_risk: bool = False
    accepting_orders: bool = True
    up_index: int = 0     # Position im CTF-Outcome-Array (indexSet = 1 << index)
    down_index: int = 1

    def token(self, outcome: str) -> str:
        return self.up_token if outcome == UP else self.down_token

    def index(self, outcome: str) -> int:
        return self.up_index if outcome == UP else self.down_index

    def elapsed(self, now: float) -> float:
        return now - self.start_ts

    def remaining(self, now: float) -> float:
        return self.end_ts - now

    def is_active(self, now: float) -> bool:
        return self.start_ts <= now < self.end_ts


@dataclass
class OrderBook:
    """Orderbuch eines Tokens. bids absteigend, asks aufsteigend sortiert."""

    token_id: str
    bids: List[Tuple[float, float]] = field(default_factory=list)
    asks: List[Tuple[float, float]] = field(default_factory=list)
    ts: float = 0.0               # lokale Empfangszeit
    latency_ms: float = 0.0
    tick_size: Optional[float] = None
    min_order_size: Optional[float] = None

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def age(self, now: float) -> float:
        return now - self.ts

    def buy_fill(self, shares: float, limit: float) -> Tuple[float, float]:
        """Wie viel kann man mit einem Kauf-Limit ``limit`` sofort kaufen? -> (Shares, VWAP)."""
        return self._sweep(self.asks, shares, lambda p: p <= limit + 1e-9)

    def sell_fill(self, shares: float, limit: float) -> Tuple[float, float]:
        """Wie viel kann man mit einem Verkaufs-Limit ``limit`` sofort verkaufen? -> (Shares, VWAP)."""
        return self._sweep(self.bids, shares, lambda p: p >= limit - 1e-9)

    @staticmethod
    def _sweep(levels, shares: float, ok) -> Tuple[float, float]:
        got = 0.0
        cost = 0.0
        for price, size in levels:
            if not ok(price) or got >= shares - 1e-12:
                break
            take = min(size, shares - got)
            got += take
            cost += take * price
        return (got, cost / got) if got > 0 else (0.0, 0.0)

    @classmethod
    def from_api(cls, token_id: str, payload: dict, ts: float, latency_ms: float = 0.0) -> "OrderBook":
        """Parst die Antwort von ``GET /book`` (Reihenfolge der Level wird nicht vorausgesetzt)."""

        def levels(raw) -> List[Tuple[float, float]]:
            out = []
            for lvl in raw or []:
                p = _f(lvl.get("price") if isinstance(lvl, dict) else None)
                s = _f(lvl.get("size") if isinstance(lvl, dict) else None)
                if p is not None and s is not None and s > 0 and 0 < p < 1:
                    out.append((p, s))
            return out

        bids = sorted(levels(payload.get("bids")), key=lambda x: -x[0])
        asks = sorted(levels(payload.get("asks")), key=lambda x: x[0])
        return cls(
            token_id=token_id,
            bids=bids,
            asks=asks,
            ts=ts,
            latency_ms=latency_ms,
            tick_size=_f(payload.get("tick_size")),
            min_order_size=_f(payload.get("min_order_size")),
        )


@dataclass
class SpotSnapshot:
    """Aktueller Spot-Zustand relativ zum Fensterstart."""

    source: str
    ref_price: float          # Preis bei Fensterstart ("Price to beat")
    spot: float
    age: float                # Sekunden seit dem letzten Tick
    ret_since_start: float    # ln(spot / ref)
    ret_lookback: Optional[float]   # ln(spot / Preis vor SIGNAL_LOOKBACK_SEC); None, wenn Historie fehlt
    sigma_ps: Optional[float]       # realisierte Vol pro sqrt(Sekunde); None während Warmup


@dataclass
class Decision:
    kind: str                 # ENTER | EXIT
    market: Market
    outcome: str              # UP | DOWN
    side: str                 # BUY | SELL
    price: float              # Limit-Preis
    shares: float
    mode: str                 # TAKER (aggressives Limit) | MAKER (post-only)
    fair: float               # P(outcome gewinnt) laut Modell
    edge: float               # erwarteter Netto-Gewinn je Share in USD
    reason: str = ""

    @property
    def notional(self) -> float:
        return self.price * self.shares


@dataclass
class OpenOrder:
    order_id: str
    slug: str
    end_ts: int
    outcome: str
    token_id: str
    side: str
    price: float
    size: float
    created_ts: float
    mode: str
    filled: float = 0.0
    fair: float = 0.0
    edge: float = 0.0
    status: str = "LIVE"
    market: Optional[Market] = field(default=None, repr=False)

    @property
    def remaining(self) -> float:
        return max(0.0, self.size - self.filled)

    @property
    def remaining_notional(self) -> float:
        return self.remaining * self.price


@dataclass
class Position:
    slug: str
    condition_id: str
    outcome: str
    token_id: str
    outcome_index: int
    start_ts: int
    end_ts: int
    shares: float = 0.0
    cost: float = 0.0            # USD für die aktuell gehaltenen Shares (inkl. Gebühren)
    realized: float = 0.0        # realisierter PnL aus Teilverkäufen
    fees: float = 0.0
    fair: float = 0.0            # Modellwahrscheinlichkeit beim (letzten) Einstieg
    edge: float = 0.0
    settled: bool = False
    won: Optional[bool] = None
    payout: float = 0.0
    pnl: float = 0.0             # Gesamt-PnL nach Abrechnung
    redeemed: bool = False
    redeem_attempts: int = 0
    redeem_next_ts: float = 0.0
    manual_redeem: bool = False  # Proxy/Safe-Wallet: im Polymarket-UI einlösen
    mode: str = "PAPER"

    @property
    def key(self) -> str:
        return position_key(self.slug, self.outcome)

    @property
    def avg_price(self) -> float:
        return self.cost / self.shares if self.shares > 0 else 0.0


def position_key(slug: str, outcome: str) -> str:
    return f"{slug}:{outcome}"


Books = Dict[str, Optional[OrderBook]]
