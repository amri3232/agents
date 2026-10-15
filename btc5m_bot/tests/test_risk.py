import pytest

from conftest import make_market
from models import BUY, ENTER, EXIT, SELL, TAKER, UP, Decision, OpenOrder
from risk import RiskManager
from state import Portfolio


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def rm(cfg):
    clock = Clock()
    return RiskManager(cfg, Portfolio(cfg), clock=clock)


def dec(kind=ENTER, price=0.5, shares=10.0, outcome=UP, side=BUY):
    return Decision(kind, make_market(), outcome, side, price, shares, TAKER, 0.8, 0.1)


def resting(slug, notional_price=0.5, size=10.0):
    return OpenOrder("o1", slug, 0, UP, "111", BUY, notional_price, size, 0.0, TAKER)


# ------------------------------------------------------------- Circuit Breaker
def test_latency_breaker_trips_on_median_and_recovers(rm, cfg):
    tripped = []
    rm.breaker.on_trip.append(tripped.append)
    for _ in range(cfg.LATENCY_SAMPLES):
        rm.on_api_result("book", True, cfg.API_LATENCY_LIMIT_MS * 2)
    assert tripped and rm.breaker.is_open()
    ok, why = rm.can_trade()
    assert not ok and "circuit_breaker" in why
    rm.clock.t += cfg.BREAKER_COOLDOWN_SEC + 1
    assert rm.can_trade()[0]


def test_single_latency_spike_is_ignored(rm):
    for ms in (100, 120, 9000, 110, 90):
        rm.on_api_result("book", True, ms)
    assert not rm.breaker.is_open()


def test_latency_of_non_trading_calls_is_ignored(rm, cfg):
    for _ in range(10):
        rm.on_api_result("gamma", True, 10_000)
    assert not rm.breaker.is_open()


def test_error_streak_trips_and_success_resets(rm, cfg):
    for _ in range(cfg.MAX_CONSECUTIVE_API_ERRORS - 1):
        rm.on_api_result("book", False, None)
    rm.on_api_result("book", True, 50)
    for _ in range(cfg.MAX_CONSECUTIVE_API_ERRORS - 1):
        rm.on_api_result("book", False, None)
    assert not rm.breaker.is_open()
    rm.on_api_result("book", False, None)
    assert rm.breaker.is_open()


def test_breaker_cooldown_escalates(rm, cfg):
    def trip():
        for _ in range(cfg.MAX_CONSECUTIVE_API_ERRORS):
            rm.on_api_result("book", False, None)
        return rm.breaker.open_until - rm.clock.t

    first = trip()
    rm.clock.t += first + 1
    second = trip()
    assert first == pytest.approx(cfg.BREAKER_COOLDOWN_SEC) and second == pytest.approx(2 * first)


# ------------------------------------------------------------------ Limits
def test_drawdown_halts_and_requires_manual_reset(cfg):
    pf = Portfolio(cfg)
    rm = RiskManager(cfg, pf)
    pf._book_pnl(+10)
    pf._book_pnl(-(cfg.MAX_DRAWDOWN_USD + 1))
    rm.update()
    ok, why = rm.can_trade()
    assert pf.halted and not ok and "Drawdown" in why
    pf.reset_halt()
    assert rm.can_trade()[0]


def test_daily_loss_limit(cfg):
    pf = Portfolio(cfg)
    rm = RiskManager(cfg, pf)
    cfg.MAX_DRAWDOWN_USD = 1e9
    pf._book_pnl(-cfg.MAX_DAILY_LOSS_USD)
    rm.update()
    ok, why = rm.can_trade()
    assert not ok and "Tagesverlust" in why


def test_loss_streak_pause(cfg):
    pf = Portfolio(cfg)
    clock = Clock()
    rm = RiskManager(cfg, pf, clock=clock)
    pf.consecutive_losses = cfg.MAX_CONSECUTIVE_LOSSES
    rm.update()
    assert not rm.can_trade()[0]
    clock.t += cfg.LOSS_STREAK_PAUSE_SEC + 1
    assert rm.can_trade()[0]


# ----------------------------------------------------------------- Freigabe
def test_approve_passes_small_order(rm):
    d, why = rm.approve(dec(price=0.5, shares=10), [])
    assert d is not None and d.shares == 10


def test_approve_caps_position_and_window(rm, cfg):
    d, _ = rm.approve(dec(price=0.5, shares=100), [])
    assert d.notional <= cfg.MAX_POSITION_USD + 1e-9
    slug = make_market().slug
    d, _ = rm.approve(dec(price=0.5, shares=20), [resting(slug, 0.5, 2 * (cfg.MAX_USD_PER_WINDOW - 5))])
    assert d is not None and d.notional <= 5 + 1e-9               # nur noch 5 USD Fensterbudget


def test_approve_rejects_when_budget_below_min_size(rm, cfg):
    slug = make_market().slug
    d, why = rm.approve(dec(price=0.5, shares=20), [resting(slug, 0.5, 2 * (cfg.MAX_USD_PER_WINDOW - 1))])
    assert d is None and "Budget" in why


def test_approve_respects_wallet_balance(rm):
    rm.wallet_usd = 3.0                                   # 3 USD = 6 Shares zu 0.50 -> gekürzt, aber >= Mindestgröße
    d, _ = rm.approve(dec(price=0.5, shares=20), [])
    assert d is not None and d.shares == 6.0
    rm.wallet_usd = 2.0                                   # 4 Shares < Mindestgröße 5 -> abgelehnt
    d, why = rm.approve(dec(price=0.5, shares=20), [])
    assert d is None and "Budget" in why


def test_exit_always_approved_even_when_halted(rm):
    rm.pf.halt("test")
    d, _ = rm.approve(dec(kind=EXIT, side=SELL), [])
    assert d is not None
    d, why = rm.approve(dec(kind=ENTER), [])
    assert d is None and "halted" in why
