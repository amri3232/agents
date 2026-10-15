"""Signal-Engine: BTC-Spot gegen Polymarket-Quotes (Latenz-/Mispricing-Arbitrage im 5-Minuten-Fenster).

Idee
----
Der Markt zahlt 1 USD, wenn der BTC-Preis am Fensterende >= Preis am Fensterstart ist ("Up"),
sonst 0. Bei driftloser Brownscher Bewegung ist die faire Wahrscheinlichkeit

    P(Up) = Φ( ln(S_t / S_0) / sqrt(σ²·τ + b²) )

mit aktuellem Spot S_t, Startpreis S_0, realisierter Vol σ (pro √s), Restzeit τ und einer
Basis-Unsicherheit b (Börsenpreis vs. Chainlink-Abrechnungspreis). Liegt der Polymarket-Token
nach Abzug der Taker-Gebühr deutlich unter diesem fairen Wert, kaufen wir - ausschließlich per
Limit-Order (aggressives Limit mit Preisdeckel oder Post-only-Maker, niemals Market-Orders).

Schutzmechanismen gegen Modellfehler ("Winner's Curse")
------------------------------------------------------
* Shrinkage: die Modellwahrscheinlichkeit wird mit dem Marktpreis gemischt.
* Nur in Richtung der Spot-Bewegung handeln (kein "Fade" gegen den Markt).
* Momentum-Bestätigung über die letzten ``SIGNAL_LOOKBACK_SEC`` Sekunden.
* Plausibilitätsgrenze für den Edge: absurd große Edges deuten auf Datenfehler hin -> kein Trade.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import Config
from models import (BUY, DOWN, ENTER, EXIT, MAKER, OUTCOMES, SELL, TAKER, UP, Decision, Market, OrderBook,
                    SpotSnapshot, opposite)
from utils import SECONDS_PER_YEAR, floor_size, floor_to_tick, norm_cdf

log = logging.getLogger("strategy")

MAX_EDGE_SANITY = 0.35   # Edge je Share darüber gilt als verdächtig (Daten-/Referenzfehler)


@dataclass
class Evaluation:
    decisions: List[Decision] = field(default_factory=list)
    reason: str = ""
    p_model_up: Optional[float] = None
    p_mkt_up: Optional[float] = None
    p_up: Optional[float] = None
    move_bps: Optional[float] = None
    lookback_bps: Optional[float] = None
    sigma_ann: Optional[float] = None


def fair_prob_up(ret_since_start: float, sigma_ps: float, remaining: float, basis_noise: float,
                 min_sigma_ps: float = 0.0) -> float:
    """P(S_T >= S_0) für driftlose GBM mit zusätzlicher Basis-Unsicherheit."""
    sigma = max(sigma_ps, min_sigma_ps)
    total_sd = math.sqrt(sigma * sigma * max(remaining, 0.0) + basis_noise * basis_noise)
    if total_sd <= 0:
        return 1.0 if ret_since_start >= 0 else 0.0
    return norm_cdf(ret_since_start / total_sd)


def market_prob_up(books: Dict[str, Optional[OrderBook]]) -> Optional[float]:
    est: List[float] = []
    up, dn = books.get(UP), books.get(DOWN)
    if up is not None and up.mid is not None:
        est.append(up.mid)
    if dn is not None and dn.mid is not None:
        est.append(1.0 - dn.mid)
    return sum(est) / len(est) if est else None


class Strategy:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.min_sigma_ps = cfg.MIN_ANNUAL_VOL / math.sqrt(SECONDS_PER_YEAR)
        self.basis_noise = cfg.BASIS_NOISE_BPS / 1e4

    # ----------------------------------------------------------------- Helfer
    def taker_fee(self, price: float) -> float:
        """Gebühr je Share bei Taker-Ausführung (flach auf den Notional - konservativ)."""
        return self.cfg.TAKER_FEE_RATE * price

    def probabilities(self, snap: SpotSnapshot, remaining: float,
                      books: Dict[str, Optional[OrderBook]]) -> Evaluation:
        assert snap.sigma_ps is not None
        p_model = fair_prob_up(snap.ret_since_start, snap.sigma_ps, remaining, self.basis_noise, self.min_sigma_ps)
        p_mkt = market_prob_up(books)
        w = self.cfg.MODEL_SHRINKAGE
        p_up = p_model if p_mkt is None else (1.0 - w) * p_model + w * p_mkt
        return Evaluation(
            p_model_up=p_model, p_mkt_up=p_mkt, p_up=p_up,
            move_bps=snap.ret_since_start * 1e4,
            lookback_bps=None if snap.ret_lookback is None else snap.ret_lookback * 1e4,
            sigma_ann=max(snap.sigma_ps, self.min_sigma_ps) * math.sqrt(SECONDS_PER_YEAR),
        )

    def _book_ok(self, book: Optional[OrderBook], now: float) -> bool:
        return (book is not None and book.best_bid is not None and book.best_ask is not None
                and book.age(now) <= self.cfg.MAX_BOOK_STALENESS_SEC)

    # --------------------------------------------------------------- Hauptlogik
    def evaluate(self, market: Market, snap: Optional[SpotSnapshot], books: Dict[str, Optional[OrderBook]],
                 holdings: Dict[str, float], window_exposure: float, bankroll: float, now: float,
                 allow_entries: bool = True) -> Evaluation:
        cfg = self.cfg
        if snap is None:
            return Evaluation(reason="no_spot")
        if snap.sigma_ps is None:
            return Evaluation(reason="vol_warmup")
        remaining = market.remaining(now)
        ev = self.probabilities(snap, remaining, books)

        # 1) Bestehende Positionen absichern/verkaufen, bevor neue Risiken eingegangen werden
        exits = self._exits(market, books, holdings, ev.p_up or 0.5, remaining, now)
        if exits:
            ev.decisions, ev.reason = exits, "exit"
            return ev

        # 2) Neue Einstiege
        if not allow_entries:
            ev.reason = "entries_blocked"
            return ev
        reason = self._entry_gates(market, snap, books, holdings, remaining, now, window_exposure, ev)
        if reason:
            ev.reason = reason
            return ev

        outcome = UP if snap.ret_since_start > 0 else DOWN      # nur in Richtung der Bewegung
        p = ev.p_up if outcome == UP else 1.0 - (ev.p_up or 0.0)
        budget = cfg.MAX_USD_PER_WINDOW - window_exposure
        dec = self._entry(market, outcome, p or 0.0, books[outcome], bankroll, budget)  # type: ignore[arg-type]
        if dec is None:
            ev.reason = "no_edge"
            return ev
        if dec.edge > MAX_EDGE_SANITY:
            ev.reason = f"edge_suspicious({dec.edge:.2f})"
            log.warning("Verdächtig großer Edge %.3f bei %s - Trade verworfen (Referenzpreis/Daten prüfen)",
                        dec.edge, market.slug)
            return ev
        ev.decisions, ev.reason = [dec], "signal"
        return ev

    def _entry_gates(self, market: Market, snap: SpotSnapshot, books, holdings, remaining: float, now: float,
                     window_exposure: float, ev: Evaluation) -> str:
        cfg = self.cfg
        if not market.accepting_orders:
            return "market_closed"
        if market.elapsed(now) < cfg.MIN_ELAPSED_SEC:
            return "too_early"
        if remaining < cfg.ENTRY_CUTOFF_REMAINING_SEC:
            return "too_late"
        if abs(ev.move_bps or 0.0) < cfg.MIN_MOVE_BPS:
            return "move_too_small"
        if cfg.REQUIRE_MOMENTUM_AGREEMENT:
            lb = snap.ret_lookback
            if lb is None:
                return "no_lookback"
            if lb * snap.ret_since_start <= 0:
                return "momentum_disagrees"
        for o in OUTCOMES:
            if not self._book_ok(books.get(o), now):
                return "book_unavailable"
            if (books[o].spread or 0.0) > cfg.MAX_SPREAD:
                return "wide_spread"
        if holdings.get(opposite(UP if snap.ret_since_start > 0 else DOWN), 0.0) > 0:
            return "opposite_position_held"
        if cfg.MAX_USD_PER_WINDOW - window_exposure < 1.0:
            return "window_budget_used"
        return ""

    # ------------------------------------------------------------------ Einstieg
    def _entry(self, market: Market, outcome: str, p: float, book: OrderBook, bankroll: float,
               budget: float) -> Optional[Decision]:
        cfg = self.cfg
        tick = book.tick_size or market.tick_size
        min_sz = book.min_order_size or market.min_order_size
        ask, bid = book.best_ask, book.best_bid
        fee = cfg.TAKER_FEE_RATE

        def size_for(price: float, fee_rate: float, worst_price: float) -> float:
            """Shares so, dass der Einsatz auch zum schlechtesten Fill-Preis (Limit) im Budget bleibt."""
            eff = price * (1.0 + fee_rate)
            if p <= eff or eff >= 1.0:
                return 0.0
            kelly = (p - eff) / (1.0 - eff)       # optimaler Bankroll-Anteil bei Quote 'price'
            usd = min(cfg.MAX_POSITION_USD, budget, cfg.KELLY_FRACTION * kelly * bankroll)
            return floor_size(usd / worst_price) if usd > 0 else 0.0

        # --- Taker: aggressives Limit, gedeckelt durch Slippage und Mindest-Edge
        if ask is not None and cfg.MIN_ENTRY_PRICE <= ask <= cfg.MAX_ENTRY_PRICE:
            max_price = (p - cfg.MIN_EDGE) / (1.0 + fee)           # teuerster Preis mit Edge >= MIN_EDGE
            limit = floor_to_tick(min(ask + cfg.SLIPPAGE, max_price, cfg.MAX_ENTRY_PRICE), tick)
            if limit >= ask - 1e-9:
                shares = size_for(ask, fee, limit)
                if shares >= min_sz:
                    filled, vwap = book.buy_fill(shares, limit)
                    shares = floor_size(filled)
                    if shares >= min_sz:
                        edge = p - vwap * (1.0 + fee)
                        return Decision(ENTER, market, outcome, BUY, limit, shares, TAKER, p, edge,
                                        f"taker ask={ask:.2f} vwap={vwap:.3f}")

        # --- Maker: Post-only-Gebot am/über dem besten Bid, nie über (fair - MAKER_MIN_EDGE)
        if cfg.USE_MAKER_ORDERS and bid is not None:
            price = bid + tick
            if ask is not None and price >= ask - 1e-9:
                price = bid
            price = min(price, floor_to_tick(p - cfg.MAKER_MIN_EDGE, tick))
            if (price >= bid - 1e-9 and cfg.MIN_ENTRY_PRICE <= price <= cfg.MAX_ENTRY_PRICE
                    and (ask is None or price < ask - 1e-9)):
                shares = size_for(price, 0.0, price)
                if shares >= min_sz:
                    return Decision(ENTER, market, outcome, BUY, round(price, 6), shares, MAKER, p, p - price,
                                    f"maker bid={bid:.2f}")
        return None

    # ---------------------------------------------------------------------- Exit
    def _exits(self, market: Market, books: Dict[str, Optional[OrderBook]], holdings: Dict[str, float],
               p_up: float, remaining: float, now: float) -> List[Decision]:
        """EV-Exit: verkaufen, wenn der Bid (netto nach Gebühr) mehr zahlt, als die Position erwartbar wert ist."""
        cfg = self.cfg
        out: List[Decision] = []
        if not cfg.ENABLE_EV_EXIT or remaining < cfg.EXIT_MIN_REMAINING_SEC:
            return out
        for outcome in OUTCOMES:
            shares = holdings.get(outcome, 0.0)
            book = books.get(outcome)
            if shares <= 0 or not self._book_ok(book, now):
                continue
            assert book is not None and book.best_bid is not None
            min_sz = book.min_order_size or market.min_order_size
            tick = book.tick_size or market.tick_size
            p_out = p_up if outcome == UP else 1.0 - p_up
            net_bid = book.best_bid * (1.0 - cfg.TAKER_FEE_RATE)
            if net_bid <= p_out + cfg.EXIT_MARGIN or shares < min_sz:
                continue
            limit = max(floor_to_tick(book.best_bid - cfg.SLIPPAGE, tick), tick)
            filled, vwap = book.sell_fill(shares, limit)
            qty = floor_size(min(shares, filled))
            if qty >= min_sz:
                edge = vwap * (1.0 - cfg.TAKER_FEE_RATE) - p_out
                out.append(Decision(EXIT, market, outcome, SELL, limit, qty, TAKER, p_out, edge,
                                    f"ev_exit bid={book.best_bid:.2f} fair={p_out:.2f}"))
        return out
