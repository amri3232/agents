"""Hilfsfunktionen: Logging, Rundung, Normalverteilung, robuster HTTP-Client, Supervisor."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import aiohttp

log = logging.getLogger("utils")

SQRT2 = math.sqrt(2.0)
SECONDS_PER_YEAR = 365.0 * 24 * 3600


# --------------------------------------------------------------------------- Logging
def setup_logging(level: str, log_dir: str) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    fileh = RotatingFileHandler(Path(log_dir) / "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fileh.setFormatter(fmt)
    root.addHandler(fileh)
    # Bibliotheken leiser stellen
    for noisy in ("urllib3", "httpx", "httpcore", "websockets", "asyncio", "web3", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class RateLimitedLog:
    """Verhindert Log-Flut: dieselbe Meldung höchstens alle ``interval`` Sekunden."""

    def __init__(self, logger: logging.Logger, interval: float = 30.0):
        self.logger = logger
        self.interval = interval
        self._last: Dict[str, float] = {}

    def warning(self, key: str, msg: str, *args) -> None:
        t = time.monotonic()
        if t - self._last.get(key, -1e18) >= self.interval:
            self._last[key] = t
            self.logger.warning(msg, *args)

    def info(self, key: str, msg: str, *args) -> None:
        t = time.monotonic()
        if t - self._last.get(key, -1e18) >= self.interval:
            self._last[key] = t
            self.logger.info(msg, *args)


# ----------------------------------------------------------------------------- Mathe
def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def floor_to_tick(price: float, tick: float) -> float:
    return round(math.floor(price / tick + 1e-9) * tick, 6)


def ceil_to_tick(price: float, tick: float) -> float:
    return round(math.ceil(price / tick - 1e-9) * tick, 6)


def floor_size(shares: float, decimals: int = 2) -> float:
    f = 10 ** decimals
    return math.floor(shares * f + 1e-9) / f


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ------------------------------------------------------------------------ HTTP-Client
class ApiError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


Observer = Callable[[str, bool, Optional[float]], None]  # (name, ok, latency_ms)


class HttpClient:
    """aiohttp-Wrapper mit Retries, Backoff (inkl. HTTP 429 / Retry-After) und Beobachtern.

    Beobachter erhalten (name, ok, latency_ms) und füttern z. B. den Circuit Breaker.
    """

    def __init__(self, session: aiohttp.ClientSession, retries: int = 3, timeout: float = 8.0):
        self.session = session
        self.retries = retries
        self.timeout = timeout
        self.observers: List[Observer] = []

    def _notify(self, name: str, ok: bool, latency: Optional[float]) -> None:
        for cb in self.observers:
            try:
                cb(name, ok, latency)
            except Exception:  # Beobachter dürfen nie den Request-Pfad zerstören
                log.exception("HTTP-Observer-Fehler")

    async def get_json(self, url: str, params: Optional[dict] = None, *, name: str = "http",
                       retries: Optional[int] = None, timeout: Optional[float] = None) -> Any:
        data, _ = await self.get_json_timed(url, params, name=name, retries=retries, timeout=timeout)
        return data

    async def get_json_timed(self, url: str, params: Optional[dict] = None, *, name: str = "http",
                             retries: Optional[int] = None, timeout: Optional[float] = None) -> Tuple[Any, float]:
        max_retries = self.retries if retries is None else retries
        to = aiohttp.ClientTimeout(total=timeout or self.timeout)
        attempt = 0
        while True:
            t0 = time.perf_counter()
            failure: ApiError
            delay: float
            try:
                async with self.session.get(url, params=params, timeout=to) as resp:
                    status = resp.status
                    text = await resp.text()
                    retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                failure = ApiError(f"{type(e).__name__}: {e}", 0)
                delay = min(2.0 ** attempt * 0.3, 4.0)
            else:
                latency = (time.perf_counter() - t0) * 1000.0
                if status == 429:
                    failure = ApiError("HTTP 429 (Rate Limit)", 429)
                    delay = retry_after if retry_after is not None else min(2.0 ** attempt, 10.0)
                elif status >= 500:
                    failure = ApiError(f"HTTP {status}", status)
                    delay = min(2.0 ** attempt * 0.5, 5.0)
                elif status >= 400:  # 4xx (außer 429) sind endgültig - nicht wiederholen
                    self._notify(name, False, latency)
                    raise ApiError(f"HTTP {status}: {text[:200]}", status)
                else:
                    try:
                        data = json.loads(text)
                    except ValueError:
                        failure = ApiError("ungültiges JSON", status)
                        delay = 0.5
                    else:
                        self._notify(name, True, latency)
                        return data, latency
            attempt += 1
            self._notify(name, False, None)
            if attempt > max_retries:
                raise ApiError(f"{name}: {failure} (nach {attempt} Versuchen)", failure.status) from failure
            await asyncio.sleep(delay + random.uniform(0, 0.15))


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, min(float(value), 30.0))
    except ValueError:
        return None


# ------------------------------------------------------------------------- Supervisor
async def supervise(name: str, factory: Callable[[], Awaitable[None]], stop: asyncio.Event,
                    logger: Optional[logging.Logger] = None) -> None:
    """Startet eine Coroutine neu, sobald sie endet oder abstürzt (exponentieller Backoff)."""
    lg = logger or logging.getLogger("supervisor")
    backoff = 1.0
    while not stop.is_set():
        started = time.monotonic()
        try:
            await factory()
            lg.warning("Task %s endete unerwartet - Neustart", name)
        except asyncio.CancelledError:
            raise
        except Exception:
            lg.exception("Task %s abgestürzt - Neustart in %.0fs", name, backoff)
        if time.monotonic() - started > 60:
            backoff = 1.0
        try:
            await asyncio.wait_for(stop.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, 60.0)
