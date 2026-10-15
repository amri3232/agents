import math

import pytest

from conftest import START, make_book, make_market, make_snap
from models import BUY, DOWN, ENTER, EXIT, MAKER, SELL, TAKER, UP
from strategy import Strategy, fair_prob_up, market_prob_up

NOW = START + 150   # 150 s vergangen, 150 s übrig


def books(up_bid, up_ask, dn_bid, dn_ask, ts=NOW, size=100):
    return {UP: make_book("111", [(up_bid, size)], [(up_ask, size)], ts),
            DOWN: make_book("222", [(dn_bid, size)], [(dn_ask, size)], ts)}


def evaluate(cfg, snap, bk, holdings=None, exposure=0.0, bankroll=100.0, now=NOW, allow=True, market=None):
    return Strategy(cfg).evaluate(market or make_market(), snap, bk, holdings or {UP: 0.0, DOWN: 0.0}, exposure,
                                  bankroll, now, allow_entries=allow)


# ---------------------------------------------------------------- Modell
def test_fair_prob_basic_properties():
    sig = 0.45 / math.sqrt(365 * 24 * 3600)
    assert fair_prob_up(0.0, sig, 100, 1e-4) == pytest.approx(0.5)
    up, down = fair_prob_up(5e-4, sig, 100, 1e-4), fair_prob_up(-5e-4, sig, 100, 1e-4)
    assert up > 0.5 and up + down == pytest.approx(1.0)                      # Symmetrie
    assert fair_prob_up(5e-4, sig, 30, 1e-4) > fair_prob_up(5e-4, sig, 200, 1e-4)  # weniger Restzeit -> sicherer
    assert fair_prob_up(5e-4, sig * 2, 100, 1e-4) < up                        # mehr Vol -> unsicherer
    assert fair_prob_up(1e-4, sig, 0, 0) == 1.0 and fair_prob_up(-1e-4, sig, 0, 0) == 0.0


def test_market_prob_up_uses_both_books():
    bk = books(0.55, 0.57, 0.41, 0.43)
    assert market_prob_up(bk) == pytest.approx((0.56 + (1 - 0.42)) / 2)
    assert market_prob_up({UP: None, DOWN: None}) is None


# ---------------------------------------------------------------- Einstieg
def test_taker_entry_when_up_is_underpriced(cfg):
    ev = evaluate(cfg, make_snap(10), books(0.55, 0.57, 0.41, 0.43))
    assert ev.reason == "signal" and len(ev.decisions) == 1
    d = ev.decisions[0]
    assert (d.kind, d.outcome, d.side, d.mode) == (ENTER, UP, BUY, TAKER)
    assert d.price >= 0.57 and d.price <= 0.58 + 1e-9          # Ask + max. Slippage
    assert d.shares >= 5 and d.notional <= cfg.MAX_POSITION_USD + 1e-6
    fee = cfg.TAKER_FEE_RATE
    assert d.edge >= cfg.MIN_EDGE and d.edge == pytest.approx(d.fair - 0.57 * (1 + fee), abs=0.005)


def test_down_move_buys_down_token(cfg):
    ev = evaluate(cfg, make_snap(-10, lookback_bp=-8), books(0.41, 0.43, 0.55, 0.57))
    assert ev.decisions and ev.decisions[0].outcome == DOWN


def test_no_trade_when_market_already_prices_it_in(cfg):
    ev = evaluate(cfg, make_snap(10), books(0.82, 0.84, 0.14, 0.16))
    assert not ev.decisions and ev.reason == "no_edge"


def test_fee_is_part_of_the_edge(cfg):
    """Ohne Gebühr reicht der Edge beim Ask 0.79, mit 1,75 % Taker-Gebühr nicht mehr."""
    snap, bk = make_snap(10), books(0.77, 0.79, 0.19, 0.21)
    cfg.USE_MAKER_ORDERS = False
    cfg.TAKER_FEE_RATE = 0.0
    free = evaluate(cfg, snap, bk)
    assert free.decisions and free.decisions[0].mode == TAKER
    cfg.TAKER_FEE_RATE = 0.0175
    ev = evaluate(cfg, snap, bk)
    assert not ev.decisions and ev.reason == "no_edge"


def test_maker_fallback_when_ask_too_expensive(cfg):
    ev = evaluate(cfg, make_snap(10), books(0.77, 0.79, 0.19, 0.21))
    assert len(ev.decisions) == 1
    d = ev.decisions[0]
    assert d.mode == MAKER and 0.77 <= d.price < 0.79
    assert d.edge >= cfg.MAKER_MIN_EDGE - 1e-9


def test_maker_disabled(cfg):
    cfg.USE_MAKER_ORDERS = False
    ev = evaluate(cfg, make_snap(10), books(0.77, 0.79, 0.19, 0.21))
    assert not ev.decisions


@pytest.mark.parametrize("now, reason", [(START + 20, "too_early"), (START + 285, "too_late")])
def test_time_gates(cfg, now, reason):
    bk = books(0.55, 0.57, 0.41, 0.43, ts=now)
    assert evaluate(cfg, make_snap(10), bk, now=now).reason == reason


def test_small_move_and_momentum_gates(cfg):
    bk = books(0.50, 0.52, 0.46, 0.48)
    assert evaluate(cfg, make_snap(2, lookback_bp=2), bk).reason == "move_too_small"
    assert evaluate(cfg, make_snap(10, lookback_bp=-3), bk).reason == "momentum_disagrees"
    assert evaluate(cfg, make_snap(10, lookback_bp=None), bk).reason == "no_lookback"
    cfg.REQUIRE_MOMENTUM_AGREEMENT = False
    assert evaluate(cfg, make_snap(10, lookback_bp=-3), books(0.55, 0.57, 0.41, 0.43)).decisions


def test_book_gates(cfg):
    assert evaluate(cfg, make_snap(10), books(0.50, 0.70, 0.30, 0.50)).reason == "wide_spread"
    stale = books(0.55, 0.57, 0.41, 0.43, ts=NOW - 60)
    assert evaluate(cfg, make_snap(10), stale).reason == "book_unavailable"
    assert evaluate(cfg, make_snap(10), {UP: None, DOWN: None}).reason == "book_unavailable"


def test_no_snapshot_or_warmup(cfg):
    bk = books(0.55, 0.57, 0.41, 0.43)
    assert evaluate(cfg, None, bk).reason == "no_spot"
    assert evaluate(cfg, make_snap(10, sigma=None), bk).reason == "vol_warmup"


def test_entries_blocked_flag_and_closed_market(cfg):
    bk = books(0.55, 0.57, 0.41, 0.43)
    assert evaluate(cfg, make_snap(10), bk, allow=False).reason == "entries_blocked"
    assert evaluate(cfg, make_snap(10), bk, market=make_market(accepting_orders=False)).reason == "market_closed"


def test_price_bounds(cfg):
    cfg.USE_MAKER_ORDERS = False
    ev = evaluate(cfg, make_snap(25, lookback_bp=20), books(0.93, 0.95, 0.03, 0.05))
    assert not ev.decisions        # Ask > MAX_ENTRY_PRICE


def test_window_budget_caps_size(cfg):
    ev = evaluate(cfg, make_snap(10), books(0.55, 0.57, 0.41, 0.43), exposure=cfg.MAX_USD_PER_WINDOW - 0.5)
    assert ev.reason == "window_budget_used"
    ev = evaluate(cfg, make_snap(10), books(0.55, 0.57, 0.41, 0.43), exposure=cfg.MAX_USD_PER_WINDOW - 6.0)
    assert ev.decisions and ev.decisions[0].notional <= 6.0 + 1e-6


def test_kelly_scales_with_bankroll(cfg):
    big = evaluate(cfg, make_snap(10), books(0.55, 0.57, 0.41, 0.43), bankroll=1000.0).decisions[0]
    small = evaluate(cfg, make_snap(10), books(0.55, 0.57, 0.41, 0.43), bankroll=40.0).decisions[0]
    assert big.shares > small.shares
    assert evaluate(cfg, make_snap(10), books(0.55, 0.57, 0.41, 0.43), bankroll=2.0).reason == "no_edge"


def test_opposite_position_blocks_entry(cfg):
    cfg.ENABLE_EV_EXIT = False      # sonst würde die überbewertete DOWN-Position zuerst verkauft
    ev = evaluate(cfg, make_snap(10), books(0.55, 0.57, 0.41, 0.43), holdings={UP: 0.0, DOWN: 10.0})
    assert ev.reason == "opposite_position_held"


def test_suspicious_edge_is_rejected(cfg):
    cfg.MODEL_SHRINKAGE = 0.0
    ev = evaluate(cfg, make_snap(20, lookback_bp=15), books(0.20, 0.22, 0.76, 0.78))
    assert not ev.decisions and ev.reason.startswith("edge_suspicious")


def test_thin_liquidity_below_min_size(cfg):
    cfg.USE_MAKER_ORDERS = False    # ein Maker-Gebot braucht keine Gegenliquidität
    thin = books(0.55, 0.57, 0.41, 0.43, size=3)
    assert not evaluate(cfg, make_snap(10), thin).decisions


# ---------------------------------------------------------------- Exit
def test_ev_exit_sells_when_bid_exceeds_expected_value(cfg):
    # Position in UP, Modell sieht UP nur bei ~50 %, Markt zahlt aber 0.70 -> verkaufen
    bk = books(0.70, 0.72, 0.26, 0.28)
    ev = evaluate(cfg, make_snap(0.5, lookback_bp=0.4, sigma=3e-4), bk, holdings={UP: 20.0, DOWN: 0.0})
    assert ev.reason == "exit" and len(ev.decisions) == 1
    d = ev.decisions[0]
    assert (d.kind, d.side, d.outcome) == (EXIT, SELL, UP) and d.shares == 20.0 and d.price <= 0.70


def test_no_exit_when_position_is_worth_holding(cfg):
    ev = evaluate(cfg, make_snap(10), books(0.55, 0.57, 0.41, 0.43), holdings={UP: 20.0, DOWN: 0.0})
    assert ev.reason != "exit"


def test_no_exit_in_last_seconds_or_when_disabled(cfg):
    bk = books(0.70, 0.72, 0.26, 0.28, ts=START + 297)
    snap = make_snap(0.5, lookback_bp=0.4, sigma=3e-4)
    assert evaluate(cfg, snap, bk, holdings={UP: 20.0, DOWN: 0.0}, now=START + 297).reason != "exit"
    cfg.ENABLE_EV_EXIT = False
    assert evaluate(cfg, snap, books(0.70, 0.72, 0.26, 0.28), holdings={UP: 20.0, DOWN: 0.0}).reason != "exit"
