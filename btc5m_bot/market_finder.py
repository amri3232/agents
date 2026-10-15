"""Findet die aktuellen/kommenden 5-Minuten-BTC-Märkte (Gamma API) und liest Orderbücher (CLOB API).

Die Märkte folgen einem festen Slug-Schema: ``btc-updown-5m-<Unix-Startzeit>``, wobei die
Startzeit ein Vielfaches von 300 s ist. Das Präfix steht in ``Config.SLUG_PREFIX`` - sollte
Polymarket das Schema ändern, genügt es, dort (oder per ``BOT_SLUG_PREFIX``) anzupassen.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from config import Config
from models import DOWN, UP, Market, OrderBook
from utils import ApiError, HttpClient, RateLimitedLog

log = logging.getLogger("market_finder")
_rl = RateLimitedLog(log, 30.0)


def _loads_maybe(value: Any) -> Any:
    """Gamma liefert Listenfelder teils als JSON-String ('["a","b"]'), teils als echte Liste."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _to_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def window_start(now: float, window_sec: int = 300) -> int:
    return int(now // window_sec) * window_sec


def slug_for(prefix: str, start_ts: int) -> str:
    return f"{prefix}{start_ts}"


def start_from_slug(prefix: str, slug: str) -> Optional[int]:
    if slug and slug.startswith(prefix):
        try:
            return int(slug[len(prefix):])
        except ValueError:
            return None
    return None


def parse_market(market_json: Dict[str, Any], start_ts: int, cfg: Config) -> Optional[Market]:
    """Wandelt ein Gamma-Marktobjekt in ``Market`` um (None bei unbrauchbaren Daten)."""
    tokens = _loads_maybe(market_json.get("clobTokenIds"))
    outcomes = _loads_maybe(market_json.get("outcomes"))
    condition_id = market_json.get("conditionId") or market_json.get("condition_id")
    if not condition_id or not isinstance(tokens, list) or len(tokens) != 2:
        return None
    labels = [str(o).strip().lower() for o in outcomes] if isinstance(outcomes, list) and len(outcomes) == 2 else []
    up_idx, down_idx = 0, 1
    if labels:
        if labels[0] in ("down", "no") and labels[1] in ("up", "yes"):
            up_idx, down_idx = 1, 0
        elif not (labels[0] in ("up", "yes") and labels[1] in ("down", "no")):
            log.warning("Unerwartete Outcome-Labels %s im Markt %s - nehme Reihenfolge [Up, Down] an",
                        labels, market_json.get("slug"))
    accepting = market_json.get("acceptingOrders")
    closed = bool(market_json.get("closed"))
    return Market(
        slug=str(market_json.get("slug") or slug_for(cfg.SLUG_PREFIX, start_ts)),
        condition_id=str(condition_id),
        start_ts=start_ts,
        end_ts=start_ts + cfg.WINDOW_SEC,
        up_token=str(tokens[up_idx]),
        down_token=str(tokens[down_idx]),
        question=str(market_json.get("question") or ""),
        tick_size=_to_float(market_json.get("orderPriceMinTickSize"), 0.01),
        min_order_size=_to_float(market_json.get("orderMinSize"), 5.0),
        neg_risk=bool(market_json.get("negRisk", False)),
        accepting_orders=(accepting is not False) and not closed,
        up_index=up_idx,
        down_index=down_idx,
    )


def parse_winner(market_json: Dict[str, Any]) -> Optional[str]:
    """Gewinner (UP/DOWN) eines abgerechneten Marktes, sonst None."""
    prices = _loads_maybe(market_json.get("outcomePrices"))
    outcomes = _loads_maybe(market_json.get("outcomes"))
    if not isinstance(prices, list) or len(prices) != 2:
        return None
    p = [_to_float(x, -1.0) for x in prices]
    if max(p) < 0.99 or min(p) > 0.01:      # noch nicht eindeutig aufgelöst
        return None
    win_idx = 0 if p[0] > p[1] else 1
    label = ""
    if isinstance(outcomes, list) and len(outcomes) == 2:
        label = str(outcomes[win_idx]).strip().lower()
    if label in ("up", "yes"):
        return UP
    if label in ("down", "no"):
        return DOWN
    return UP if win_idx == 0 else DOWN


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MarketFinder:
    def __init__(self, cfg: Config, http: HttpClient):
        self.cfg = cfg
        self.http = http
        self.markets: Dict[int, Market] = {}
        self._retry_at: Dict[int, float] = {}
        self._fetched_at: Dict[int, float] = {}
        self._fallback_at: float = 0.0

    # ---------------------------------------------------------------- Zugriff
    def current(self, now: float) -> Optional[Market]:
        m = self.markets.get(window_start(now, self.cfg.WINDOW_SEC))
        return m if m and m.is_active(now) else None

    def get(self, start_ts: int) -> Optional[Market]:
        return self.markets.get(start_ts)

    def known(self) -> List[Market]:
        return sorted(self.markets.values(), key=lambda m: m.start_ts)

    # ---------------------------------------------------------------- Laden
    async def fetch_raw(self, start_ts: int) -> Optional[Dict[str, Any]]:
        """Gamma-Marktobjekt zum Fenster (zuerst /events?slug=, dann /markets?slug=)."""
        slug = slug_for(self.cfg.SLUG_PREFIX, start_ts)
        data = await self.http.get_json(f"{self.cfg.GAMMA_URL}/events", params={"slug": slug}, name="gamma")
        events = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        for ev in events:
            for m in ev.get("markets") or []:
                if m.get("conditionId"):
                    return m
        data = await self.http.get_json(f"{self.cfg.GAMMA_URL}/markets", params={"slug": slug}, name="gamma")
        markets = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        return markets[0] if markets else None

    async def fetch_window(self, start_ts: int) -> Optional[Market]:
        try:
            raw = await self.fetch_raw(start_ts)
        except ApiError as e:
            _rl.warning(f"gamma-{start_ts}", "Gamma-Abfrage für Fenster %s fehlgeschlagen: %s", start_ts, e)
            return None
        if not raw:
            return None
        market = parse_market(raw, start_ts, self.cfg)
        if market is None:
            _rl.warning(f"parse-{start_ts}", "Marktdaten für Fenster %s nicht auswertbar", start_ts)
        return market

    async def refresh(self, now: Optional[float] = None) -> None:
        """Stellt sicher, dass aktuelles + nächste Fenster bekannt sind."""
        now = time.time() if now is None else now
        cur = window_start(now, self.cfg.WINDOW_SEC)
        for k in range(0, self.cfg.MARKETS_AHEAD + 1):
            start = cur + k * self.cfg.WINDOW_SEC
            known = self.markets.get(start)
            if known is not None and (known.accepting_orders
                                      or now - self._fetched_at.get(start, 0.0) < self.cfg.MARKET_REFRESH_SEC):
                continue
            if now < self._retry_at.get(start, 0.0):
                continue
            m = await self.fetch_window(start)
            if m is not None:
                if known is None:
                    log.info("Markt gefunden: %s (Start %s UTC)", m.slug, _iso(m.start_ts))
                self.markets[start] = m
                self._fetched_at[start] = now
                self._retry_at.pop(start, None)
            else:
                self._retry_at[start] = now + (4.0 if k == 0 else 20.0)
        if self.current(now) is None and now >= self._fallback_at:
            self._fallback_at = now + 30.0
            await self.discover(now)
        self.prune(now)

    async def discover(self, now: float) -> None:
        """Fallback: Märkte mit dem Slug-Präfix über die Enddatums-Suche finden."""
        try:
            data = await self.http.get_json(
                f"{self.cfg.GAMMA_URL}/markets", name="gamma",
                params={"closed": "false", "limit": 100, "order": "endDate", "ascending": "true",
                        "end_date_min": _iso(now), "end_date_max": _iso(now + 20 * 60)})
        except ApiError as e:
            _rl.warning("discover", "Fallback-Suche fehlgeschlagen: %s", e)
            return
        for raw in data if isinstance(data, list) else []:
            start = start_from_slug(self.cfg.SLUG_PREFIX, str(raw.get("slug", "")))
            if start is None or start in self.markets:
                continue
            m = parse_market(raw, start, self.cfg)
            if m:
                self.markets[start] = m
                log.info("Markt per Fallback-Suche gefunden: %s", m.slug)

    def prune(self, now: float) -> None:
        for start in [s for s, m in self.markets.items() if m.end_ts < now - 3600]:
            del self.markets[start]
            self._retry_at.pop(start, None)
            self._fetched_at.pop(start, None)

    # ----------------------------------------------------------- Auflösung
    async def fetch_outcome(self, start_ts: int) -> Optional[str]:
        """Gewinner des Fensters laut Gamma (None, solange nicht abgerechnet)."""
        try:
            raw = await self.fetch_raw(start_ts)
        except ApiError as e:
            _rl.warning("outcome", "Ergebnis-Abfrage fehlgeschlagen: %s", e)
            return None
        return parse_winner(raw) if raw else None

    async def clock_skew(self) -> Optional[float]:
        """Differenz lokale Uhr - CLOB-Serverzeit in Sekunden (positiv: lokale Uhr geht vor)."""
        try:
            t0 = time.time()
            server = await self.http.get_json(f"{self.cfg.CLOB_URL}/time", name="time", retries=1)
            t1 = time.time()
            return (t0 + t1) / 2.0 - float(server)
        except (ApiError, TypeError, ValueError):
            return None


class BookFetcher:
    """Pollt öffentliche Orderbücher (``GET /book?token_id=``) und meldet Latenz an den Circuit Breaker."""

    def __init__(self, cfg: Config, http: HttpClient):
        self.cfg = cfg
        self.http = http

    async def fetch_one(self, token_id: str) -> Optional[OrderBook]:
        try:
            data, latency = await self.http.get_json_timed(
                f"{self.cfg.CLOB_URL}/book", params={"token_id": token_id}, name="book", retries=1, timeout=5.0)
        except ApiError as e:
            _rl.warning(f"book-{token_id[:8]}", "Orderbuch %s…: %s", token_id[:8], e)
            return None
        if not isinstance(data, dict):
            return None
        return OrderBook.from_api(token_id, data, ts=time.time(), latency_ms=latency)

    async def fetch(self, token_ids: Iterable[str]) -> Dict[str, OrderBook]:
        ids = list(dict.fromkeys(token_ids))
        results = await asyncio.gather(*(self.fetch_one(t) for t in ids), return_exceptions=True)
        out: Dict[str, OrderBook] = {}
        for tid, res in zip(ids, results):
            if isinstance(res, OrderBook):
                out[tid] = res
            elif isinstance(res, Exception) and not isinstance(res, asyncio.CancelledError):
                _rl.warning(f"bookx-{tid[:8]}", "Orderbuch-Fehler: %s", res)
        return out
