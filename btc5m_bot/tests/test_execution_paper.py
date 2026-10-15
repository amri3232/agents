import pytest

from conftest import START, make_book, make_market
from execution import PaperExecutor, Settler
from models import BUY, DOWN, MAKER, SELL, TAKER, UP, Decision
from price_feed import PriceFeed
from risk import RiskManager
from state import Portfolio

NOW = START + 100


class Clock:
    t = float(NOW)

    def __call__(self):
        return self.t


def setup(cfg):
    pf = Portfolio(cfg)
    rm = RiskManager(cfg, pf)
    return pf, PaperExecutor(cfg, pf, rm, clock=Clock())


def decision(mode=TAKER, side=BUY, price=0.60, shares=10.0, outcome=UP):
    return Decision("ENTER", make_market(), outcome, side, price, shares, mode, 0.8, 0.1)


def books(up_bid=0.55, up_ask=0.57, ask_size=100):
    return {UP: make_book("111", [(up_bid, 100)], [(up_ask, ask_size)], NOW),
            DOWN: make_book("222", [(0.41, 100)], [(0.43, 100)], NOW)}


async def test_taker_buy_fills_with_fee_at_book_price(cfg):
    pf, ex = setup(cfg)
    order = await ex.place(decision(price=0.58, shares=10), books())
    pos = pf.get(make_market().slug, UP)
    assert order.filled == 10 and pos.shares == 10
    fee = cfg.TAKER_FEE_RATE * 0.57 * 10
    assert pos.cost == pytest.approx(5.7 + fee) and pos.fees == pytest.approx(fee)
    assert not ex.open_orders()          # vollständig gefüllt -> nichts bleibt im Buch


async def test_taker_partial_fill_rests_remainder(cfg):
    pf, ex = setup(cfg)
    order = await ex.place(decision(price=0.58, shares=10), books(ask_size=4))
    assert order.filled == 4 and pf.shares(make_market().slug, UP) == 4
    assert len(ex.open_orders()) == 1 and ex.open_orders()[0].remaining == pytest.approx(6)


async def test_taker_never_pays_above_limit(cfg):
    pf, ex = setup(cfg)
    bk = books(up_ask=0.65)
    await ex.place(decision(price=0.60, shares=10), bk)
    assert pf.shares(make_market().slug, UP) == 0     # Ask 0.65 > Limit 0.60 -> kein Fill


async def test_maker_rests_then_fills_without_fee_when_market_trades_through(cfg):
    pf, ex = setup(cfg)
    await ex.place(decision(mode=MAKER, price=0.56, shares=10), books())
    assert len(ex.open_orders()) == 1 and pf.shares(make_market().slug, UP) == 0
    await ex.sync(make_market(), books(up_bid=0.54, up_ask=0.58), NOW + 1)       # Ask über unserem Gebot
    assert pf.shares(make_market().slug, UP) == 0
    await ex.sync(make_market(), books(up_bid=0.52, up_ask=0.56, ask_size=6), NOW + 2)   # Ask erreicht 0.56
    pos = pf.get(make_market().slug, UP)
    assert pos.shares == 6 and pos.cost == pytest.approx(6 * 0.56) and pos.fees == 0
    assert len(ex.open_orders()) == 1                  # Rest (4) steht weiter
    await ex.sync(make_market(), books(up_bid=0.52, up_ask=0.55, ask_size=50), NOW + 3)
    assert pf.shares(make_market().slug, UP) == 10 and not ex.open_orders()


async def test_post_only_that_would_cross_is_rejected(cfg):
    _, ex = setup(cfg)
    assert await ex.place(decision(mode=MAKER, price=0.57, shares=10), books()) is None


async def test_cancel(cfg):
    _, ex = setup(cfg)
    order = await ex.place(decision(mode=MAKER, price=0.56, shares=10), books())
    assert await ex.cancel(order) and not ex.open_orders()
    assert await ex.cancel(order) is False


async def test_sell_exit_books_realized_pnl(cfg):
    pf, ex = setup(cfg)
    await ex.place(decision(price=0.58, shares=10), books())
    bk = books()
    bk[UP] = make_book("111", [(0.70, 100)], [(0.72, 100)], NOW)
    await ex.place(decision(side=SELL, price=0.69, shares=10), bk)
    assert pf.shares(make_market().slug, UP) == 0
    assert pf.realized_pnl == pytest.approx(10 * 0.70 * (1 - cfg.TAKER_FEE_RATE) - (5.7 + 0.0175 * 5.7))


# ----------------------------------------------------------------- Settler
class StubFinder:
    def __init__(self, winner=None):
        self.winner = winner
        self.calls = 0

    async def fetch_outcome(self, start_ts):
        self.calls += 1
        return self.winner


def settler(cfg, pf, finder, feed=None):
    return Settler(cfg, None, pf, finder, feed or PriceFeed(cfg), RiskManager(cfg, pf))


async def test_paper_settlement_win_and_loss(cfg):
    pf, ex = setup(cfg)
    m = make_market()
    await ex.place(decision(price=0.58, shares=10), books())
    st = settler(cfg, pf, StubFinder(winner=None))
    await st.run_once(m.end_ts + 1)                      # zu früh (SETTLE_DELAY)
    assert pf.pending_settlement()
    await st.run_once(m.end_ts + cfg.SETTLE_DELAY_SEC + 1)   # Gamma liefert noch nichts
    assert pf.pending_settlement()
    st.finder.winner = UP
    await st.run_once(m.end_ts + cfg.SETTLE_DELAY_SEC + 2)
    pos = pf.get(m.slug, UP)
    assert pos.settled and pos.won and pos.redeemed and pf.realized_pnl > 0 and pf.wins == 1

    m2 = make_market(START + 300)
    d = Decision("ENTER", m2, UP, BUY, 0.58, 10, TAKER, 0.8, 0.1)
    await ex.place(d, books())
    st.finder.winner = DOWN
    await st.run_once(m2.end_ts + 60)
    assert pf.get(m2.slug, UP).won is False and pf.losses == 1


async def test_paper_settlement_falls_back_to_spot(cfg):
    pf, ex = setup(cfg)
    m = make_market()
    await ex.place(decision(price=0.58, shares=10), books())
    feed = PriceFeed(cfg)
    src = feed.source("binance")
    src.on_tick(m.start_ts + 0.5, 100_000.0, rx=m.start_ts)
    src.on_tick(m.end_ts + 0.5, 100_050.0, rx=m.end_ts)
    st = settler(cfg, pf, StubFinder(winner=None), feed)
    await st.run_once(m.end_ts + cfg.PAPER_SPOT_FALLBACK_SEC + 5)
    assert pf.get(m.slug, UP).won is True
