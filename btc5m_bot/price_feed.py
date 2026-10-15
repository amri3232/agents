"""Realtime-BTC-Spotpreise (Binance / Coinbase / optional Chainlink via Polymarket-RTDS).

Jede Quelle führt eigene Sekundenbalken. Für ein Fenster wird *eine* Quelle konsistent für
Startpreis (Referenz) und aktuellen Preis verwendet - Preise verschiedener Börsen werden nie
gemischt, sonst würde die Basis zwischen den Börsen als "Bewegung" missverstanden.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

import aiohttp

from config import Config
from models import DOWN, UP, SpotSnapshot
from utils import ApiError, HttpClient, RateLimitedLog

log = logging.getLogger("price_feed")
_rl = RateLimitedLog(log, 60.0)

MIN_VOL_BARS = 30  # so viele Sekunden-Returns braucht die Vol-Schätzung mindestens


def sane_ts(event_ts: Optional[float], rx: float, tol: float = 2.0) -> float:
    """Börsen-Zeitstempel verwenden, solange er plausibel zur lokalen Uhr passt."""
    if event_ts is not None and abs(event_ts - rx) <= tol:
        return event_ts
    return rx


class SourceFeed:
    """Basisklasse: Sekundenbalken [sec, open, close] + Verbindungszustand."""

    name = "base"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.bars: Deque[List[float]] = deque(maxlen=cfg.PRICE_HISTORY_SEC)
        self.last_price: Optional[float] = None
        self.last_rx: float = 0.0       # lokale Empfangszeit des letzten Ticks
        self.connected = False
        self.ticks = 0

    # ------------------------------------------------------------------- Daten
    def on_tick(self, ts: float, price: float, rx: Optional[float] = None) -> None:
        if not (price > 0) or math.isnan(price) or math.isinf(price):
            return
        sec = int(ts)
        if self.bars and sec < self.bars[-1][0]:
            sec = int(self.bars[-1][0])  # Out-of-order-Tick in den aktuellen Balken falten
        if self.bars and self.bars[-1][0] == sec:
            self.bars[-1][2] = price
        else:
            self.bars.append([sec, price, price])
        self.last_price = price
        self.last_rx = rx if rx is not None else time.time()
        self.ticks += 1

    def fresh(self, now: float, max_age: float) -> bool:
        return self.last_price is not None and (now - self.last_rx) <= max_age

    def price_at(self, ts: float, tolerance: float = 3.0) -> Optional[float]:
        """Erster Preis ab Zeitpunkt ``ts`` (Open des ersten Balkens), nur wenn er nahe genug liegt."""
        target = int(math.floor(ts))
        for sec, open_, _close in self.bars:
            if sec >= target:
                return open_ if sec - target <= tolerance else None
        return None

    def sigma_per_sqrt_sec(self, now: float, window: int) -> Optional[float]:
        """Realisierte Vol (Std der Log-Returns pro sqrt(Sekunde)) = sqrt(Σr² / Σdt)."""
        cutoff = now - window
        pts = [(b[0], b[2]) for b in self.bars if b[0] >= cutoff]
        if len(pts) < MIN_VOL_BARS + 1:
            return None
        sq, dt = 0.0, 0.0
        for (s0, p0), (s1, p1) in zip(pts, pts[1:]):
            gap = s1 - s0
            if gap <= 0 or p0 <= 0 or p1 <= 0:
                continue
            r = math.log(p1 / p0)
            sq += r * r
            dt += gap
        if dt < MIN_VOL_BARS:
            return None
        return math.sqrt(sq / dt)

    def lookback_price(self, now: float, lookback: float) -> Optional[float]:
        """Preis ca. ``lookback`` Sekunden zuvor (nur wenn die Historie so weit zurückreicht)."""
        if not self.bars or self.bars[0][0] > now - lookback + 3:
            return None
        return self.price_at(now - lookback, tolerance=3.0)

    # --------------------------------------------------------------- Verbindung
    async def run(self, session: aiohttp.ClientSession, stop: asyncio.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            try:
                await self._connect_and_read(session)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except aiohttp.WSServerHandshakeError as e:
                # z. B. HTTP 451 (Geoblock) bei Binance.com - dann lange warten, andere Quellen laufen weiter
                wait = 120.0 if e.status in (403, 451) else backoff
                _rl.warning(f"hs-{self.name}", "%s: Websocket-Handshake abgelehnt (HTTP %s) - Retry in %.0fs",
                            self.name, e.status, wait)
                backoff = min(backoff * 2, 60.0)
                await self._sleep(stop, wait)
            except Exception as e:
                _rl.warning(f"err-{self.name}", "%s: Verbindung verloren (%s: %s) - Reconnect in %.0fs",
                            self.name, type(e).__name__, e, backoff)
                backoff = min(backoff * 2, 60.0)
                await self._sleep(stop, backoff)
            finally:
                self.connected = False

    @staticmethod
    async def _sleep(stop: asyncio.Event, secs: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=secs)
        except asyncio.TimeoutError:
            pass

    async def _connect_and_read(self, session: aiohttp.ClientSession) -> None:
        raise NotImplementedError


class WsFeed(SourceFeed):
    url = ""
    stall_timeout = 20.0   # keine Nachricht so lange -> Verbindung gilt als tot

    def subscribe_messages(self) -> List[str]:
        return []

    def parse(self, data) -> List[Tuple[float, float]]:  # -> [(ts, price)]
        raise NotImplementedError

    async def _connect_and_read(self, session: aiohttp.ClientSession) -> None:
        async with session.ws_connect(self.url, heartbeat=15, max_msg_size=0) as ws:
            self.connected = True
            log.info("%s: Websocket verbunden", self.name)
            for msg in self.subscribe_messages():
                await ws.send_str(msg)
            extra = self.start_extras(ws)
            try:
                while True:
                    # Stillstand (keine Nachricht) -> TimeoutError -> Reconnect durch run()
                    msg = await asyncio.wait_for(ws.receive(), timeout=self.stall_timeout)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        rx = time.time()
                        try:
                            data = json.loads(msg.data)
                        except ValueError:
                            continue
                        for ts, px in self.parse(data):
                            self.on_tick(sane_ts(ts, rx), px, rx)
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                                      aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        raise ConnectionError(f"Websocket geschlossen ({msg.type.name})")
            finally:
                if extra:
                    extra.cancel()

    def start_extras(self, ws) -> Optional[asyncio.Task]:
        return None


class BinanceFeed(WsFeed):
    name = "binance"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.url = cfg.BINANCE_WS_URL

    def parse(self, data):
        # {"e":"trade","T":1700000000123,"p":"67000.10",...}
        if isinstance(data, dict) and "p" in data:
            try:
                ts = float(data["T"]) / 1000.0 if "T" in data else None
                return [(ts, float(data["p"]))]  # type: ignore[list-item]
            except (TypeError, ValueError):
                return []
        return []


class CoinbaseFeed(WsFeed):
    name = "coinbase"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.url = cfg.COINBASE_WS_URL

    def subscribe_messages(self) -> List[str]:
        return [json.dumps({"type": "subscribe", "product_ids": [self.cfg.COINBASE_PRODUCT],
                            "channels": ["ticker"]})]

    def parse(self, data):
        # {"type":"ticker","price":"67000.1","time":"2025-01-01T00:00:00.123456Z",...}
        if isinstance(data, dict) and data.get("type") == "ticker" and "price" in data:
            try:
                ts = None
                if data.get("time"):
                    ts = datetime.fromisoformat(str(data["time"]).replace("Z", "+00:00")).timestamp()
                return [(ts, float(data["price"]))]  # type: ignore[list-item]
            except (TypeError, ValueError):
                return []
        return []


class RtdsChainlinkFeed(WsFeed):
    """EXPERIMENTELL: Chainlink-BTC/USD (die Abrechnungsquelle) über Polymarkets RTDS-Websocket."""

    name = "chainlink"
    stall_timeout = 30.0

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.url = cfg.RTDS_WS_URL

    def subscribe_messages(self) -> List[str]:
        return [json.dumps({"action": "subscribe", "subscriptions": [
            {"topic": "crypto_prices_chainlink", "type": "*", "filters": json.dumps({"symbol": "btc/usd"})}]})]

    def start_extras(self, ws) -> Optional[asyncio.Task]:
        async def pinger():
            try:
                while True:
                    await asyncio.sleep(5)
                    await ws.send_str("PING")
            except asyncio.CancelledError:
                raise
            except Exception:  # Verbindung weg -> run() übernimmt den Reconnect
                return
        return asyncio.create_task(pinger())

    def parse(self, data):
        # {"topic":"crypto_prices_chainlink","payload":{"symbol":"btc/usd","timestamp":ms,"value":67234.5}}
        if isinstance(data, dict) and data.get("topic") == "crypto_prices_chainlink":
            pl = data.get("payload") or {}
            if str(pl.get("symbol", "")).lower() == "btc/usd" and "value" in pl:
                try:
                    ts = float(pl["timestamp"]) / 1000.0 if "timestamp" in pl else None
                    return [(ts, float(pl["value"]))]  # type: ignore[list-item]
                except (TypeError, ValueError):
                    return []
        return []


class PriceFeed:
    """Aggregator über alle Quellen (Prioritätsreihenfolge: Chainlink > Binance > Coinbase)."""

    def __init__(self, cfg: Config, http: Optional[HttpClient] = None):
        self.cfg = cfg
        self.http = http
        self.sources: List[SourceFeed] = []
        if cfg.ENABLE_RTDS_CHAINLINK:
            self.sources.append(RtdsChainlinkFeed(cfg))
        if cfg.ENABLE_BINANCE:
            self.sources.append(BinanceFeed(cfg))
        if cfg.ENABLE_COINBASE:
            self.sources.append(CoinbaseFeed(cfg))
        self._ref: Dict[Tuple[str, int], float] = {}
        self._ref_try: Dict[Tuple[str, int], float] = {}

    # ------------------------------------------------------------------ Laufzeit
    async def run(self, session: aiohttp.ClientSession, stop: asyncio.Event) -> None:
        tasks = [asyncio.create_task(s.run(session, stop), name=f"feed-{s.name}") for s in self.sources]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()

    def source(self, name: str) -> Optional[SourceFeed]:
        return next((s for s in self.sources if s.name == name), None)

    # ----------------------------------------------------------------- Referenz
    def reference(self, source: SourceFeed, start_ts: int) -> Optional[float]:
        key = (source.name, start_ts)
        if key in self._ref:
            return self._ref[key]
        px = source.price_at(start_ts)
        if px is not None:
            self._ref[key] = px
        return px

    async def ensure_reference(self, start_ts: int) -> None:
        """Holt den Startpreis per REST nach, falls der Bot mitten im Fenster gestartet ist."""
        if self.http is None:
            return
        now = time.time()
        if now < start_ts:
            return
        for src in self.sources:
            key = (src.name, start_ts)
            if self.reference(src, start_ts) is not None or now < self._ref_try.get(key, 0.0):
                continue
            self._ref_try[key] = now + 30.0   # REST-Backfill höchstens alle 30 s je Quelle/Fenster
            try:
                px = await self._rest_open_price(src.name, start_ts)
            except (ApiError, ValueError, KeyError, IndexError, TypeError) as e:
                _rl.warning(f"ref-{src.name}", "Referenzpreis-Backfill %s fehlgeschlagen: %s", src.name, e)
                continue
            if px:
                self._ref[(src.name, start_ts)] = px
                log.info("Referenzpreis %s @%s per REST nachgeladen: %.2f", src.name, start_ts, px)

    async def _rest_open_price(self, name: str, start_ts: int) -> Optional[float]:
        assert self.http is not None
        if name == "binance":
            rows = await self.http.get_json(f"{self.cfg.BINANCE_REST_URL}/api/v3/klines", name="ref",
                                            params={"symbol": self.cfg.BINANCE_SYMBOL, "interval": "1s",
                                                    "startTime": start_ts * 1000, "limit": 1}, retries=1)
            if rows and abs(rows[0][0] / 1000.0 - start_ts) <= 2:
                return float(rows[0][1])
        elif name == "coinbase":
            iso = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows = await self.http.get_json(
                f"{self.cfg.COINBASE_REST_URL}/products/{self.cfg.COINBASE_PRODUCT}/candles", name="ref",
                params={"granularity": 60, "start": iso(start_ts), "end": iso(start_ts + 60)}, retries=1)
            for row in rows or []:  # [time, low, high, open, close, volume]
                if int(row[0]) == start_ts:
                    return float(row[3])
        return None

    # ----------------------------------------------------------------- Snapshot
    def snapshot(self, start_ts: int, now: Optional[float] = None) -> Optional[SpotSnapshot]:
        """Spot-Zustand für das Fenster ``start_ts``; None, solange keine konsistente Quelle bereitsteht."""
        now = time.time() if now is None else now
        for src in self.sources:
            if not src.fresh(now, self.cfg.MAX_SPOT_STALENESS_SEC):
                continue
            ref = self.reference(src, start_ts)
            if ref is None or src.last_price is None:
                continue
            spot = src.last_price
            lb = src.lookback_price(now, self.cfg.SIGNAL_LOOKBACK_SEC)
            return SpotSnapshot(
                source=src.name,
                ref_price=ref,
                spot=spot,
                age=now - src.last_rx,
                ret_since_start=math.log(spot / ref),
                ret_lookback=math.log(spot / lb) if lb else None,
                sigma_ps=src.sigma_per_sqrt_sec(now, self.cfg.VOL_WINDOW_SEC),
            )
        return None

    def resolve_by_spot(self, start_ts: int, end_ts: int) -> Optional[str]:
        """Paper-Fallback: Gewinner anhand der Spotpreise (Binance/Coinbase sind nur ein Proxy für Chainlink)."""
        for src in self.sources:
            ref = self.reference(src, start_ts)
            end_px = src.price_at(end_ts, tolerance=5.0)
            if ref is not None and end_px is not None:
                return UP if end_px >= ref else DOWN
        return None

    def status(self, now: Optional[float] = None) -> str:
        now = time.time() if now is None else now
        parts = []
        for s in self.sources:
            age = f"{now - s.last_rx:.1f}s" if s.last_price is not None else "-"
            parts.append(f"{s.name}={'%.2f' % s.last_price if s.last_price else '-'}({age})")
        return " ".join(parts)

