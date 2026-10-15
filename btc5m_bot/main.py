#!/usr/bin/env python3
"""Polymarket BTC-5-Minuten-Bot - Haupt-Loop.

Start (Standard = PAPER, kein echtes Geld):
    python main.py

Weitere Befehle:
    python main.py --check        Verbindungs-/Konfigurationstest aller Schnittstellen
    python main.py --report       Auswertung des Trade-Journals (data/trades.csv)
    python main.py --reset-halt   Drawdown-/Verlust-Halt aufheben
    python main.py --approve      (LIVE, nur EOA-Wallet) einmalig Token-Freigaben setzen
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

import aiohttp

from config import Config, Secrets, load_config
from execution import ChainClient, ExecutorBase, LiveExecutor, PaperExecutor, Settler
from market_finder import BookFetcher, MarketFinder
from models import BUY, ENTER, OUTCOMES, Market, OpenOrder, OrderBook
from price_feed import PriceFeed
from risk import RiskManager
from state import Journal, Portfolio
from strategy import Evaluation, Strategy
from utils import HttpClient, RateLimitedLog, setup_logging, supervise

log = logging.getLogger("main")
_rl = RateLimitedLog(log, 30.0)


class Bot:
    """Verdrahtet alle Komponenten. ``tick`` ist ein einzelner, deterministischer Strategie-Durchlauf."""

    def __init__(self, cfg: Config, *, feed: PriceFeed, finder: MarketFinder, fetcher: Optional[BookFetcher],
                 executor: ExecutorBase, risk: RiskManager, strategy: Strategy, portfolio: Portfolio,
                 settler: Optional[Settler], clock: Callable[[], float] = time.time):
        self.cfg, self.feed, self.finder, self.fetcher = cfg, feed, finder, fetcher
        self.executor, self.risk, self.strategy, self.pf, self.settler = executor, risk, strategy, portfolio, settler
        self.clock = clock
        self.books: Dict[str, OrderBook] = {}
        self.last_order_ts = 0.0
        self.last_eval: Optional[Evaluation] = None
        self.last_market: Optional[Market] = None
        self.last_block_reason = ""
        self._bg: Set[asyncio.Task] = set()
        risk.breaker.on_trip.append(self._on_breaker_trip)

    # ------------------------------------------------------------------ Helfer
    def _on_breaker_trip(self, reason: str) -> None:
        """Circuit Breaker: sofort alle offenen Orders stornieren (läuft im Hintergrund)."""
        try:
            task = asyncio.get_running_loop().create_task(self.executor.cancel_all())
        except RuntimeError:
            return
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    def books_for(self, market: Market) -> Dict[str, Optional[OrderBook]]:
        return {o: self.books.get(market.token(o)) for o in OUTCOMES}

    # -------------------------------------------------------------------- Tick
    async def tick(self, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        self.risk.update(now)
        self.executor.register_markets(self.finder.known())

        # Orders von bereits beendeten Fenstern sind wertlos -> weg damit
        expired = [o for o in self.executor.open_orders() if o.end_ts <= now]
        if expired:
            await self.executor.cancel_many(expired)

        market = self.finder.current(now)
        self.last_market = market
        if market is None:
            self.last_eval = Evaluation(reason="no_market")
            return
        books = self.books_for(market)
        await self.executor.sync(market, books, now)

        snap = self.feed.snapshot(market.start_ts, now)
        holdings = {o: self.pf.shares(market.slug, o) for o in OUTCOMES}
        allowed, self.last_block_reason = self.risk.can_trade(now)
        # Die Strategie plant "ideal" (nur Positionskosten); offene Orders prüft risk.approve beim Platzieren.
        ev = self.strategy.evaluate(market, snap, books, holdings, self.pf.window_cost(market.slug),
                                    self.pf.equity, now, allow_entries=allowed)
        if not allowed and ev.reason == "entries_blocked":
            ev.reason = f"blocked ({self.last_block_reason})"
        self.last_eval = ev
        await self._manage_orders(market, ev, books, now, allowed)

    async def _manage_orders(self, market: Market, ev: Evaluation, books, now: float, allowed: bool) -> None:
        cfg = self.cfg
        remaining = market.remaining(now)
        open_orders = self.executor.open_orders(market.slug)

        def matches(o: OpenOrder) -> bool:
            tick = (books.get(o.outcome).tick_size if books.get(o.outcome) else None) or market.tick_size
            return any(d.outcome == o.outcome and d.side == o.side and abs(d.price - o.price) < tick / 2
                       for d in ev.decisions)

        # 1) Veraltete / nicht mehr gewollte Orders stornieren (Cancel/Replace)
        stale = [o for o in open_orders
                 if remaining < cfg.CANCEL_BEFORE_END_SEC
                 or now - o.created_ts > cfg.ORDER_TTL_SEC
                 or (o.side == BUY and not allowed)
                 or not matches(o)]
        if stale:
            for o in stale:
                log.info("CANCEL %s %s %.2f @ %.3f (%s)", o.side, o.outcome, o.remaining, o.price,
                         "Fensterende" if remaining < cfg.CANCEL_BEFORE_END_SEC else
                         "TTL" if now - o.created_ts > cfg.ORDER_TTL_SEC else "Signal weg")
            await self.executor.cancel_many(stale)
            stale_ids = {o.order_id for o in stale}
            open_orders = [o for o in open_orders if o.order_id not in stale_ids]
        if remaining < cfg.CANCEL_BEFORE_END_SEC:
            return

        # 2) Neue Orders
        for d in ev.decisions:
            if any(o.outcome == d.outcome and o.side == d.side for o in open_orders):
                continue    # es steht bereits eine passende Order im Buch
            if d.kind == ENTER and now - self.last_order_ts < cfg.ORDER_COOLDOWN_SEC:
                continue
            approved, why = self.risk.approve(d, open_orders, now)
            if approved is None:
                _rl.info(f"deny-{why[:20]}", "Order abgelehnt vom Risikomanagement: %s", why)
                continue
            order = await self.executor.place(approved, books)
            if order is not None:
                self.last_order_ts = now
                open_orders = self.executor.open_orders(market.slug)
            if d.kind == ENTER:
                break       # höchstens ein neuer Einstieg pro Tick

    # ------------------------------------------------------------------ Status
    def status_line(self, now: Optional[float] = None) -> str:
        now = self.clock() if now is None else now
        m, ev, pf = self.last_market, self.last_eval, self.pf
        head = f"[{self.cfg.MODE}]"
        if m is None:
            return f"{head} kein aktives Fenster | {self.feed.status(now)}"
        books = self.books_for(m)

        def q(o: str) -> str:
            b = books[o]
            return f"{b.best_bid:.2f}/{b.best_ask:.2f}" if b and b.best_bid and b.best_ask else "-/-"

        sig = ""
        if ev and ev.p_up is not None:
            sig = (f" | Δ={ev.move_bps:+.1f}bp σ={ev.sigma_ann * 100:.0f}% pUP(model/mkt/mix)="
                   f"{ev.p_model_up:.2f}/{(ev.p_mkt_up if ev.p_mkt_up is not None else float('nan')):.2f}/{ev.p_up:.2f}")
        return (f"{head} t+{m.elapsed(now):3.0f}s {m.slug[-10:]} | {self.feed.status(now)} | "
                f"UP {q('UP')} DOWN {q('DOWN')}{sig} | reason={ev.reason if ev else '-'} | "
                f"PnL {pf.realized_pnl:+.2f} (heute {pf.day_pnl:+.2f}, DD {pf.drawdown:.2f}) "
                f"W/L {pf.wins}/{pf.losses} offen {len(pf.pending_settlement())}")


# ============================================================================ Loops
async def _sleep(stop: asyncio.Event, secs: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=secs)
    except asyncio.TimeoutError:
        pass


async def market_loop(bot: Bot, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            now = time.time()
            await bot.finder.refresh(now)
            cur = bot.finder.current(now)
            if cur is not None:
                await bot.feed.ensure_reference(cur.start_ts)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Fehler im Market-Loop")
        await _sleep(stop, min(5.0, bot.cfg.MARKET_REFRESH_SEC))


async def book_loop(bot: Bot, stop: asyncio.Event) -> None:
    assert bot.fetcher is not None
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            now = time.time()
            m = bot.finder.current(now)
            if m is not None:
                bot.books.update(await bot.fetcher.fetch([m.up_token, m.down_token]))
                keep = {t for mk in bot.finder.known() for t in (mk.up_token, mk.down_token)}
                for t in [t for t in bot.books if t not in keep]:
                    del bot.books[t]
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Fehler im Orderbuch-Loop")
        await _sleep(stop, max(0.05, bot.cfg.BOOK_POLL_SEC - (time.monotonic() - t0)))


async def strategy_loop(bot: Bot, stop: asyncio.Event) -> None:
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            await bot.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Fehler im Strategie-Tick (Bot läuft weiter)")
        await _sleep(stop, max(0.05, bot.cfg.STRATEGY_INTERVAL_SEC - (time.monotonic() - t0)))


async def settle_loop(bot: Bot, stop: asyncio.Event) -> None:
    assert bot.settler is not None
    while not stop.is_set():
        try:
            await bot.settler.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Fehler bei Abrechnung/Redeem (Retry im nächsten Zyklus)")
        await _sleep(stop, bot.cfg.SETTLE_INTERVAL_SEC)


async def status_loop(bot: Bot, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await _sleep(stop, bot.cfg.STATUS_INTERVAL_SEC)
        try:
            log.info(bot.status_line())
        except Exception:
            log.exception("Statuszeile fehlgeschlagen")


# ============================================================================== Run
async def check_geoblock(cfg: Config, http: HttpClient) -> None:
    """Hält LIVE an, wenn Polymarket die IP ausdrücklich als blockiert meldet (kein Umgehungsversuch)."""
    try:
        res = await http.get_json("https://polymarket.com/api/geoblock", name="geoblock", retries=1)
    except Exception as e:
        log.warning("Geoblock-Check nicht möglich (%s) - fahre fort", e)
        return
    if isinstance(res, dict) and res.get("blocked") is True:
        msg = (f"Polymarket meldet diese IP/Region als blockiert (Land={res.get('country')}, "
               f"Region={res.get('region')}). Orders würden abgelehnt.")
        if cfg.ENFORCE_GEOBLOCK_CHECK:
            raise SystemExit("LIVE-Start abgebrochen: " + msg)
        log.error(msg)


async def run_bot(cfg: Config, secrets: Secrets) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:   # z. B. Windows
            signal.signal(sig, lambda *_: stop.set())

    data_dir = Path(cfg.DATA_DIR)
    journal = Journal(data_dir / "trades.csv")
    portfolio = Portfolio(cfg, data_dir / "state.json", journal)
    if portfolio.load():
        log.info("State geladen: PnL %+.2f USD, %d offene Positionen%s", portfolio.realized_pnl,
                 len(portfolio.pending_settlement()), " | HALT AKTIV: " + portfolio.halt_reason if portfolio.halted else "")
    elif cfg.is_live:
        log.info("Kein gespeicherter State - starte frisch")

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "btc5m-bot/1.0"}) as session:
        http = HttpClient(session)
        feed = PriceFeed(cfg, http)
        finder = MarketFinder(cfg, http)
        fetcher = BookFetcher(cfg, http)
        risk = RiskManager(cfg, portfolio)
        http.observers.append(risk.on_api_result)
        strategy = Strategy(cfg)

        chain: Optional[ChainClient] = None
        if cfg.is_live:
            missing = secrets.missing_for_live()
            if missing:
                raise SystemExit("LIVE-Modus: fehlende Einträge in .env: " + ", ".join(missing))
            await check_geoblock(cfg, http)
            executor: ExecutorBase = LiveExecutor(cfg, secrets, portfolio, risk)
            await executor.start()
            if secrets.rpc_url:
                chain = ChainClient(cfg, secrets)
                try:
                    pol = await asyncio.to_thread(chain.native_balance)
                    log.info("Wallet %s | POL für Gas: %.4f", chain.account.address, pol)
                    if pol < 0.05:
                        log.warning("Wenig POL (%.4f) für Gas-Gebühren beim Redeem - bitte nachladen", pol)
                except Exception as e:
                    log.warning("RPC nicht erreichbar (%s) - Auto-Redeem erst wieder möglich, wenn RPC läuft", e)
            else:
                log.warning("Kein RPC_URL gesetzt - Gewinne müssen manuell im Polymarket-UI eingelöst werden")
        else:
            executor = PaperExecutor(cfg, portfolio, risk)
            log.info("PAPER-MODUS: Es wird kein echtes Geld verwendet. Startkapital %.2f USD.", cfg.BANKROLL_USD)

        settler = Settler(cfg, secrets, portfolio, finder, feed, risk, chain)
        bot = Bot(cfg, feed=feed, finder=finder, fetcher=fetcher, executor=executor, risk=risk,
                  strategy=strategy, portfolio=portfolio, settler=settler)

        skew = await finder.clock_skew()
        if skew is not None and abs(skew) > 2.0:
            log.warning("Lokale Uhr weicht %.1fs von der Polymarket-Serverzeit ab - bitte NTP synchronisieren!", skew)

        factories = {
            "price_feed": lambda: feed.run(session, stop),
            "markets": lambda: market_loop(bot, stop),
            "books": lambda: book_loop(bot, stop),
            "strategy": lambda: strategy_loop(bot, stop),
            "settle": lambda: settle_loop(bot, stop),
            "status": lambda: status_loop(bot, stop),
        }
        if isinstance(executor, LiveExecutor) and cfg.USE_HEARTBEAT:
            factories["heartbeat"] = executor.heartbeat_loop
        tasks = [asyncio.create_task(supervise(name, fac, stop), name=name) for name, fac in factories.items()]
        log.info("Bot läuft (%s). Beenden mit Ctrl+C / SIGTERM.", cfg.MODE)

        await stop.wait()
        log.info("Beende... storniere offene Orders und speichere State")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await asyncio.wait_for(executor.cancel_all(), timeout=15)
        except Exception:
            log.exception("Storno beim Beenden fehlgeschlagen - bitte offene Orders im Polymarket-UI prüfen")
        portfolio.save()
        await executor.close()
        log.info("Beendet. Offene Positionen werden beim nächsten Start abgerechnet/eingelöst.")


def run_forever(cfg: Config, secrets: Secrets) -> None:
    """Äußere Schutzschicht: unerwartete Fehler führen zum Neustart, nie zum Absturz."""
    backoff = 2.0
    while True:
        started = time.monotonic()
        try:
            asyncio.run(run_bot(cfg, secrets))
            return                                  # sauberer Shutdown (Signal)
        except KeyboardInterrupt:
            return
        except SystemExit:
            raise
        except Exception:
            log.exception("Fataler Fehler im Hauptloop - Neustart in %.0fs", backoff)
        if time.monotonic() - started > 300:
            backoff = 2.0
        time.sleep(backoff)
        backoff = min(backoff * 2, 120.0)


# =============================================================================== CLI
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Polymarket BTC-5-Minuten-Bot")
    ap.add_argument("--check", action="store_true", help="Verbindungs- und Konfigurationstest")
    ap.add_argument("--report", action="store_true", help="Auswertung von data/trades.csv")
    ap.add_argument("--reset-halt", action="store_true", help="Drawdown-/Verlust-Halt aufheben")
    ap.add_argument("--approve", action="store_true", help="(LIVE, EOA) Token-Freigaben für den Exchange setzen")
    args = ap.parse_args(argv)

    cfg = load_config()
    secrets = Secrets.from_env()
    setup_logging(cfg.LOG_LEVEL, cfg.LOG_DIR)

    if args.report:
        from report import print_report
        print_report(Path(cfg.DATA_DIR) / "trades.csv")
        return 0
    if args.reset_halt:
        pf = Portfolio(cfg, Path(cfg.DATA_DIR) / "state.json")
        pf.load()
        pf.reset_halt()
        print("Halt aufgehoben; Drawdown-Referenz zurückgesetzt.")
        return 0
    if args.check:
        from diagnostics import run_checks
        return asyncio.run(run_checks(cfg, secrets))
    if args.approve:
        from diagnostics import approve_allowances
        return approve_allowances(cfg, secrets)

    log.info("Start | Modus=%s | %s", cfg.MODE, secrets)
    if cfg.is_live:
        log.warning("*** LIVE-MODUS: Es wird mit ECHTEM GELD gehandelt. Max. %.2f USD je Order, %.2f USD je Fenster, "
                    "Drawdown-Halt bei %.2f USD. ***", cfg.MAX_POSITION_USD, cfg.MAX_USD_PER_WINDOW,
                    cfg.MAX_DRAWDOWN_USD)
    run_forever(cfg, secrets)
    return 0


if __name__ == "__main__":
    sys.exit(main())
