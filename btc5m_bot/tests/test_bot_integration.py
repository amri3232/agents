"""End-to-End im PAPER-Modus: simuliertes Fenster mit steigendem BTC und träge nachgeführten Polymarket-Quotes."""
import random

import pytest

from conftest import make_book, make_market
from execution import PaperExecutor, Settler
from main import Bot
from market_finder import MarketFinder
from models import DOWN, UP
from price_feed import PriceFeed
from risk import RiskManager
from state import Journal, Portfolio
from strategy import Strategy


class StubFinder(MarketFinder):
    winner = None

    async def fetch_outcome(self, start_ts):
        return self.winner


def build(cfg, tmp_path, market=None):
    market = market or make_market()
    t = [float(market.start_ts - 400)]
    pf = Portfolio(cfg, tmp_path / "state.json", Journal(tmp_path / "trades.csv"))
    risk = RiskManager(cfg, pf, clock=lambda: t[0])
    feed = PriceFeed(cfg)
    finder = StubFinder(cfg, http=None)
    finder.markets[market.start_ts] = market
    executor = PaperExecutor(cfg, pf, risk, clock=lambda: t[0])
    settler = Settler(cfg, None, pf, finder, feed, risk)
    bot = Bot(cfg, feed=feed, finder=finder, fetcher=None, executor=executor, risk=risk, strategy=Strategy(cfg),
              portfolio=pf, settler=settler, clock=lambda: t[0])
    return bot, t, market


def set_books(bot, market, now, up=(0.55, 0.57), down=(0.41, 0.43), size=500):
    bot.books[market.up_token] = make_book(market.up_token, [(up[0], size)], [(up[1], size)], now)
    bot.books[market.down_token] = make_book(market.down_token, [(down[0], size)], [(down[1], size)], now)


async def run_window(bot, t, market, drift_bp_total, up_quote_fn, seed=7, until=None, noise=12.0):
    """Spielt Sekunde für Sekunde ab (Vorlauf 400 s für Vol-/Lookback-Historie)."""
    rnd = random.Random(seed)
    src = bot.feed.source("binance")
    px = 100_000.0
    end = until if until is not None else market.end_ts + 2
    for sec in range(market.start_ts - 400, int(end)):
        t[0] = sec + 0.5
        step = rnd.gauss(0, noise)         # Standard: ~1.2 bp Rauschen pro Sekunde
        if sec >= market.start_ts:
            step += px * drift_bp_total / 1e4 / 300.0
        px += step
        src.on_tick(t[0] - 0.2, px, rx=t[0] - 0.2)
        if sec >= market.start_ts - 5:
            bid, ask = up_quote_fn(sec - market.start_ts)
            set_books(bot, market, t[0], up=(bid, ask), down=(round(1 - ask - 0.0, 2), round(1 - bid, 2)))
            await bot.tick(t[0])
    return px


def lagging_quotes(elapsed):
    """Polymarket reagiert träge: Up-Quote bleibt nahe 0.5/0.57, obwohl BTC schon klar gestiegen ist."""
    return (0.52, 0.55)


async def test_bot_buys_lagging_up_token_and_wins(cfg, tmp_path):
    bot, t, market = build(cfg, tmp_path)
    await run_window(bot, t, market, drift_bp_total=+40, up_quote_fn=lagging_quotes, until=market.end_ts - 1)
    pos = bot.pf.get(market.slug, UP)
    assert pos is not None and pos.shares >= 5, bot.status_line()
    assert bot.pf.get(market.slug, DOWN) is None
    assert pos.cost <= cfg.MAX_USD_PER_WINDOW + cfg.MAX_POSITION_USD * 0.05        # Fensterbudget eingehalten
    assert not bot.executor.open_orders(market.slug) or t[0] < market.end_ts - cfg.CANCEL_BEFORE_END_SEC
    assert pos.edge >= cfg.MIN_EDGE - 1e-9 and 0.5 < pos.fair < 1

    bot.finder.winner = UP
    await bot.settler.run_once(market.end_ts + cfg.SETTLE_DELAY_SEC + 1)
    assert pos.settled and pos.won and bot.pf.realized_pnl > 0 and bot.pf.wins == 1
    assert (tmp_path / "trades.csv").exists()


async def test_loss_is_booked_when_market_reverses(cfg, tmp_path):
    bot, t, market = build(cfg, tmp_path)
    await run_window(bot, t, market, drift_bp_total=+40, up_quote_fn=lagging_quotes, until=market.end_ts - 1)
    bot.finder.winner = DOWN
    await bot.settler.run_once(market.end_ts + 60)
    pos = bot.pf.get(market.slug, UP)
    assert pos.settled and not pos.won and bot.pf.realized_pnl == pytest.approx(-pos.cost) and bot.pf.losses == 1


async def test_no_trade_when_quotes_are_efficient(cfg, tmp_path):
    bot, t, market = build(cfg, tmp_path)

    def efficient(elapsed):                      # Markt preist den Anstieg zeitnah ein
        p = min(0.95, 0.5 + 0.4 * elapsed / 150)
        return (round(p - 0.01, 2), round(p + 0.01, 2))

    await run_window(bot, t, market, drift_bp_total=+40, up_quote_fn=efficient, until=market.end_ts - 1)
    pos = bot.pf.get(market.slug, UP)
    assert pos is None or pos.edge < 0.2        # kein (großes) Mispricing -> höchstens kleine Trades


async def test_flat_market_does_not_trade(cfg, tmp_path):
    bot, t, market = build(cfg, tmp_path)
    await run_window(bot, t, market, drift_bp_total=0.0, up_quote_fn=lambda e: (0.49, 0.51),
                     until=market.end_ts - 1, noise=0.5)      # praktisch kein Preisrauschen
    assert not bot.pf.positions


async def test_halted_portfolio_never_opens_positions(cfg, tmp_path):
    bot, t, market = build(cfg, tmp_path)
    bot.pf.halt("Test")
    await run_window(bot, t, market, drift_bp_total=+40, up_quote_fn=lagging_quotes, until=market.end_ts - 1)
    assert not bot.pf.positions and bot.last_eval.reason.startswith("blocked")


async def test_open_breaker_cancels_resting_orders_and_blocks(cfg, tmp_path):
    import asyncio
    from models import BUY, MAKER, Decision
    cfg.BREAKER_COOLDOWN_SEC = cfg.BREAKER_MAX_COOLDOWN_SEC = 3600.0    # länger als das ganze Testfenster
    bot, t, market = build(cfg, tmp_path)
    t[0] = market.start_ts + 50.0
    set_books(bot, market, t[0])
    await bot.executor.place(Decision("ENTER", market, UP, BUY, 0.30, 10, MAKER, 0.8, 0.5), bot.books_for(market))
    assert len(bot.executor.open_orders()) == 1
    for _ in range(cfg.MAX_CONSECUTIVE_API_ERRORS):          # Circuit Breaker löst aus
        bot.risk.on_api_result("book", False, None)
    for _ in range(3):
        await asyncio.sleep(0)                               # Hintergrund-Task (cancel_all) ausführen lassen
    assert not bot.executor.open_orders()
    # solange der Breaker offen ist, entsteht trotz glasklarem Signal keine Position
    await run_window(bot, t, market, drift_bp_total=+40, up_quote_fn=lagging_quotes, until=market.start_ts + 140)
    assert not bot.pf.positions and "circuit_breaker" in bot.last_block_reason


async def test_stale_orderbook_stops_trading(cfg, tmp_path):
    bot, t, market = build(cfg, tmp_path)
    await run_window(bot, t, market, drift_bp_total=+40, up_quote_fn=lagging_quotes, until=market.start_ts + 100)
    shares_before = sum(p.shares for p in bot.pf.positions.values())
    t[0] += 30        # Orderbuch wird nicht mehr aktualisiert, Spot-Feed ebenfalls veraltet
    await bot.tick(t[0])
    assert bot.last_eval.reason in ("no_spot", "book_unavailable")
    assert sum(p.shares for p in bot.pf.positions.values()) == shares_before


async def test_orders_of_finished_windows_are_cancelled(cfg, tmp_path):
    bot, t, market = build(cfg, tmp_path)
    from models import MAKER, BUY, Decision
    d = Decision("ENTER", market, UP, BUY, 0.30, 10, MAKER, 0.8, 0.5)
    set_books(bot, market, market.start_ts + 50.0)
    await bot.executor.place(d, bot.books_for(market))
    assert bot.executor.open_orders()
    await bot.tick(market.end_ts + 1)
    assert not bot.executor.open_orders()


async def test_status_line_renders(cfg, tmp_path):
    bot, t, market = build(cfg, tmp_path)
    assert "kein aktives Fenster" in bot.status_line(market.start_ts + 1)
    await run_window(bot, t, market, drift_bp_total=+40, up_quote_fn=lagging_quotes, until=market.start_ts + 100)
    line = bot.status_line(t[0])
    assert "[PAPER]" in line and "UP" in line and "PnL" in line
