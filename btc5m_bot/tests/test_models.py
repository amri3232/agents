from conftest import make_book
from models import OrderBook


def test_from_api_sorts_and_filters():
    payload = {"bids": [{"price": "0.40", "size": "10"}, {"price": "0.45", "size": "5"}, {"price": "0", "size": "3"}],
               "asks": [{"price": "0.60", "size": "10"}, {"price": "0.55", "size": "7"}, {"price": "x", "size": "1"}],
               "tick_size": "0.01", "min_order_size": "5"}
    b = OrderBook.from_api("tok", payload, ts=1.0, latency_ms=12)
    assert b.best_bid == 0.45 and b.best_ask == 0.55
    assert [p for p, _ in b.bids] == [0.45, 0.40] and [p for p, _ in b.asks] == [0.55, 0.60]
    assert b.tick_size == 0.01 and b.min_order_size == 5.0
    assert abs(b.mid - 0.50) < 1e-9 and abs(b.spread - 0.10) < 1e-9


def test_empty_book_has_no_quotes():
    b = OrderBook.from_api("tok", {"bids": [], "asks": None}, ts=0)
    assert b.best_bid is None and b.best_ask is None and b.mid is None and b.spread is None


def test_buy_fill_vwap_respects_limit():
    b = make_book("t", [(0.4, 10)], [(0.50, 10), (0.52, 10), (0.60, 100)], ts=0)
    shares, vwap = b.buy_fill(15, limit=0.52)
    assert shares == 15 and abs(vwap - (10 * 0.50 + 5 * 0.52) / 15) < 1e-9
    shares, _ = b.buy_fill(50, limit=0.52)
    assert shares == 20                       # Level 0.60 liegt über dem Limit
    assert b.buy_fill(5, limit=0.49) == (0.0, 0.0)


def test_sell_fill_respects_limit():
    b = make_book("t", [(0.50, 4), (0.48, 10), (0.30, 100)], [(0.6, 1)], ts=0)
    shares, vwap = b.sell_fill(10, limit=0.48)
    assert shares == 10 and abs(vwap - (4 * 0.50 + 6 * 0.48) / 10) < 1e-9
    assert b.sell_fill(100, limit=0.48)[0] == 14
