import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config  # noqa: E402
from models import Market, OrderBook, SpotSnapshot  # noqa: E402

START = 1_700_000_100  # Vielfaches von 300
SIGMA_45 = 0.45 / math.sqrt(365 * 24 * 3600)   # 45 % annualisierte Vol, pro sqrt(s)


@pytest.fixture
def cfg(tmp_path):
    c = Config(DATA_DIR=str(tmp_path / "data"), LOG_DIR=str(tmp_path / "logs"))
    c.validate()
    return c


def make_market(start: int = START, **kw) -> Market:
    base = dict(slug=f"btc-updown-5m-{start}", condition_id="0x" + "ab" * 32, start_ts=start, end_ts=start + 300,
                up_token="111", down_token="222", tick_size=0.01, min_order_size=5.0)
    base.update(kw)
    return Market(**base)


def make_book(token: str, bids, asks, ts: float, tick: float = 0.01, min_size: float = 5.0) -> OrderBook:
    return OrderBook(token_id=token, bids=sorted(bids, key=lambda x: -x[0]), asks=sorted(asks, key=lambda x: x[0]),
                     ts=ts, tick_size=tick, min_order_size=min_size)


def make_snap(move_bp: float = 10.0, lookback_bp=8.0, sigma=SIGMA_45, ref: float = 100_000.0, age: float = 0.2):
    spot = ref * math.exp(move_bp / 1e4)
    return SpotSnapshot(source="binance", ref_price=ref, spot=spot, age=age, ret_since_start=move_bp / 1e4,
                        ret_lookback=None if lookback_bp is None else lookback_bp / 1e4, sigma_ps=sigma)


@pytest.fixture
def market():
    return make_market()
