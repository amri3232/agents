import math

import pytest

from conftest import START
from price_feed import BinanceFeed, CoinbaseFeed, PriceFeed, RtdsChainlinkFeed, SourceFeed, sane_ts


def feed_with_history(cfg, start=START, pre=400, post=130, vol=20.0, drift=0.0, seed=1):
    """Binance-Feed mit 1-Hz-Ticks von start-pre bis start+post."""
    import random
    rnd = random.Random(seed)
    feed = PriceFeed(cfg)
    src = feed.source("binance")
    px = 100_000.0
    for t in range(start - pre, start + post):
        px += rnd.gauss(0, vol) + drift
        src.on_tick(t + 0.3, px, rx=t + 0.3)
    return feed, src, px


def test_bars_and_staleness(cfg):
    s = SourceFeed(cfg)
    s.on_tick(100.1, 10.0, rx=100.1)
    s.on_tick(100.9, 11.0, rx=100.9)
    s.on_tick(101.2, 12.0, rx=101.2)
    assert [list(b) for b in s.bars] == [[100, 10.0, 11.0], [101, 12.0, 12.0]]
    assert s.fresh(102.0, 3.0) and not s.fresh(110.0, 3.0)
    s.on_tick(float("nan"), 0, rx=1)          # ungültige Preise werden ignoriert
    s.on_tick(100.0, -5.0, rx=1)
    assert s.ticks == 3


def test_price_at_requires_coverage(cfg):
    s = SourceFeed(cfg)
    for t in range(1000, 1010):
        s.on_tick(t, 50.0 + t - 1000, rx=t)
    assert s.price_at(1004) == 54.0
    assert s.price_at(900) == 50.0 or s.price_at(900) is None      # Historie beginnt erst bei 1000
    assert s.price_at(900, tolerance=3.0) is None
    assert s.price_at(2000) is None


def test_sigma_estimate_matches_simulation(cfg):
    feed, src, _ = feed_with_history(cfg, vol=30.0, seed=3)
    sigma = src.sigma_per_sqrt_sec(START + 129, 300)
    assert sigma == pytest.approx(30.0 / 100_000.0, rel=0.25)       # ~3 bp pro sqrt(s)


def test_sigma_needs_warmup(cfg):
    s = SourceFeed(cfg)
    for t in range(10):
        s.on_tick(t, 100 + t, rx=t)
    assert s.sigma_per_sqrt_sec(10, 300) is None


def test_snapshot_uses_consistent_source_and_reference(cfg):
    feed, src, last = feed_with_history(cfg, drift=0.5)
    now = START + 129.5
    snap = feed.snapshot(START, now)
    assert snap is not None and snap.source == "binance"
    ref = src.price_at(START)
    assert snap.ref_price == ref and snap.spot == last
    assert snap.ret_since_start == pytest.approx(math.log(last / ref))
    assert snap.ret_lookback is not None and snap.sigma_ps is not None


def test_reference_is_frozen_once_seen(cfg):
    feed, src, _ = feed_with_history(cfg)
    ref = feed.snapshot(START, START + 129.5).ref_price
    src.bars.clear()                                    # selbst bei verlorener Historie bleibt die Referenz
    src.on_tick(START + 130, 123_456.0, rx=START + 130)
    assert feed.snapshot(START, START + 130.2).ref_price == ref


def test_snapshot_none_when_stale_or_no_reference(cfg):
    feed, src, _ = feed_with_history(cfg)
    assert feed.snapshot(START, START + 129.5 + 10) is None          # Feed veraltet (> MAX_SPOT_STALENESS_SEC)
    assert feed.snapshot(START + 3600, START + 129.5) is None        # Fenster nicht abgedeckt


def test_no_mixing_of_exchanges(cfg):
    """Binance hat keine Referenz, Coinbase schon -> Snapshot muss komplett aus Coinbase stammen."""
    feed = PriceFeed(cfg)
    cb = feed.source("coinbase")
    for t in range(START - 100, START + 100):
        cb.on_tick(t + 0.2, 99_900.0 + (t - START) * 0.1, rx=t + 0.2)
    bn = feed.source("binance")
    bn.on_tick(START + 99.5, 100_000.0, rx=START + 99.5)             # nur ein später Tick, kein Start
    snap = feed.snapshot(START, START + 99.6)
    assert snap.source == "coinbase" and abs(snap.spot - cb.last_price) < 1e-9


def test_resolve_by_spot(cfg):
    feed = PriceFeed(cfg)
    src = feed.source("binance")
    src.on_tick(START + 0.5, 100.0, rx=START)
    src.on_tick(START + 300.5, 99.0, rx=START + 300)
    assert feed.resolve_by_spot(START, START + 300) == "DOWN"
    src.on_tick(START + 301.5, 101.0, rx=START + 301)
    assert feed.resolve_by_spot(START, START + 300) == "DOWN"       # Endpreis = erster Tick ab Fensterende


def test_message_parsers(cfg):
    assert BinanceFeed(cfg).parse({"e": "trade", "T": 1_700_000_000_123, "p": "67000.10"}) == [(1_700_000_000.123, 67000.10)]
    assert BinanceFeed(cfg).parse({"result": None}) == []
    cb = CoinbaseFeed(cfg).parse({"type": "ticker", "price": "67001.5", "time": "2025-01-01T00:00:00.500000Z"})
    assert cb[0][1] == 67001.5 and cb[0][0] == pytest.approx(1_735_689_600.5)
    assert CoinbaseFeed(cfg).parse({"type": "subscriptions"}) == []
    rt = RtdsChainlinkFeed(cfg).parse({"topic": "crypto_prices_chainlink",
                                       "payload": {"symbol": "btc/usd", "timestamp": 1_700_000_000_000, "value": 67000.5}})
    assert rt == [(1_700_000_000.0, 67000.5)]
    assert RtdsChainlinkFeed(cfg).parse({"topic": "crypto_prices_chainlink", "payload": {"symbol": "eth/usd", "value": 1}}) == []


def test_sane_ts():
    assert sane_ts(100.5, 100.0) == 100.5
    assert sane_ts(50.0, 100.0) == 100.0        # unplausibler Börsen-Zeitstempel -> lokale Zeit
    assert sane_ts(None, 100.0) == 100.0
