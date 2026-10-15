import json

from conftest import START
from market_finder import (BookFetcher, MarketFinder, parse_market, parse_winner, slug_for, start_from_slug,
                           window_start)
from models import DOWN, UP
from utils import ApiError

CID = "0x" + "cd" * 32


def raw_market(outcomes=("Up", "Down"), tokens=("t_up", "t_down"), **kw):
    m = {"slug": f"btc-updown-5m-{START}", "conditionId": CID, "question": "Bitcoin Up or Down",
         "clobTokenIds": json.dumps(list(tokens)), "outcomes": json.dumps(list(outcomes)),
         "outcomePrices": json.dumps(["0.5", "0.5"]), "orderPriceMinTickSize": 0.01, "orderMinSize": 5,
         "negRisk": False, "acceptingOrders": True, "closed": False}
    m.update(kw)
    return m


def test_window_math():
    assert window_start(START + 123) == START and window_start(START - 1) == START - 300
    assert slug_for("btc-updown-5m-", START) == f"btc-updown-5m-{START}"
    assert start_from_slug("btc-updown-5m-", f"btc-updown-5m-{START}") == START
    assert start_from_slug("btc-updown-5m-", "eth-updown-5m-1") is None
    assert start_from_slug("btc-updown-5m-", "btc-updown-5m-abc") is None


def test_parse_market_json_string_fields(cfg):
    m = parse_market(raw_market(), START, cfg)
    assert (m.up_token, m.down_token, m.up_index, m.down_index) == ("t_up", "t_down", 0, 1)
    assert m.condition_id == CID and m.end_ts == START + 300 and m.accepting_orders and m.min_order_size == 5


def test_parse_market_handles_swapped_outcome_order(cfg):
    m = parse_market(raw_market(outcomes=("Down", "Up"), tokens=("t_first", "t_second")), START, cfg)
    assert m.up_token == "t_second" and m.down_token == "t_first" and m.up_index == 1 and m.down_index == 0


def test_parse_market_rejects_garbage(cfg):
    assert parse_market({"conditionId": CID}, START, cfg) is None
    assert parse_market(raw_market(clobTokenIds="[1]"), START, cfg) is None
    assert parse_market(raw_market(closed=True), START, cfg).accepting_orders is False
    assert parse_market(raw_market(acceptingOrders=False), START, cfg).accepting_orders is False


def test_parse_winner():
    assert parse_winner(raw_market(outcomePrices=json.dumps(["1", "0"]))) == UP
    assert parse_winner(raw_market(outcomePrices=json.dumps(["0", "1"]))) == DOWN
    assert parse_winner(raw_market(outcomes=("Down", "Up"), outcomePrices=json.dumps(["1", "0"]))) == DOWN
    assert parse_winner(raw_market(outcomePrices=json.dumps(["0.62", "0.38"]))) is None   # noch nicht aufgelöst
    assert parse_winner({"outcomePrices": None}) is None


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def get_json(self, url, params=None, **kw):
        self.calls.append((url, params))
        for key, val in self.routes.items():
            if url.endswith(key):
                if isinstance(val, Exception):
                    raise val
                return val(params) if callable(val) else val
        raise ApiError("404", 404)

    async def get_json_timed(self, url, params=None, **kw):
        return await self.get_json(url, params, **kw), 12.0


def event_for(params):
    return [{"slug": params["slug"], "markets": [raw_market(slug=params["slug"])]}]


async def test_refresh_loads_current_and_upcoming_windows(cfg):
    http = FakeHttp({"/events": event_for})
    f = MarketFinder(cfg, http)
    await f.refresh(START + 10)
    assert [m.start_ts for m in f.known()] == [START, START + 300, START + 600]
    assert f.current(START + 10).slug.endswith(str(START)) and f.current(START + 400) is not None
    n = len(http.calls)
    await f.refresh(START + 12)                 # bereits bekannt -> keine weiteren Aufrufe
    assert len(http.calls) == n


async def test_refresh_survives_api_errors_and_retries_later(cfg):
    http = FakeHttp({"/events": ApiError("boom", 500), "/markets": ApiError("boom", 500)})
    f = MarketFinder(cfg, http)
    await f.refresh(START + 10)                 # darf nicht werfen
    assert f.known() == [] and f.current(START + 10) is None
    http.routes["/events"] = event_for
    await f.refresh(START + 11)                 # Negativ-Cache: noch zu früh für Retry
    assert f.known() == []
    await f.refresh(START + 30)
    assert f.current(START + 30) is not None


async def test_fetch_outcome(cfg):
    resolved = [{"markets": [raw_market(outcomePrices=json.dumps(["0", "1"]))]}]
    f = MarketFinder(cfg, FakeHttp({"/events": resolved}))
    assert await f.fetch_outcome(START) == DOWN
    f = MarketFinder(cfg, FakeHttp({"/events": [], "/markets": []}))
    assert await f.fetch_outcome(START) is None


async def test_fallback_discovery_by_prefix(cfg):
    listing = [raw_market(slug=f"btc-updown-5m-{START}"), raw_market(slug="other-market"),
               raw_market(slug=f"btc-updown-5m-{START + 300}")]
    http = FakeHttp({"/events": [], "/markets": lambda p: listing if "end_date_min" in (p or {}) else []})
    f = MarketFinder(cfg, http)
    await f.refresh(START + 10)
    assert {m.start_ts for m in f.known()} == {START, START + 300}


async def test_book_fetcher(cfg):
    book = {"bids": [{"price": "0.4", "size": "10"}], "asks": [{"price": "0.6", "size": "10"}], "tick_size": "0.01"}
    http = FakeHttp({"/book": lambda p: book if p["token_id"] == "good" else (_ for _ in ()).throw(ApiError("x", 500))})
    out = await BookFetcher(cfg, http).fetch(["good", "bad", "good"])
    assert set(out) == {"good"} and out["good"].best_bid == 0.4 and out["good"].latency_ms == 12.0
