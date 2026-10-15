"""LiveExecutor gegen einen Fake-ClobClient (echte Polymarket-Aufrufe sind in CI/Sandbox nicht möglich)."""
import asyncio

import pytest

pytest.importorskip("py_clob_client")

from conftest import START, make_book, make_market
from config import Secrets
from execution import LiveExecutor, _immediate_fill
from models import BUY, DOWN, MAKER, SELL, TAKER, UP, Decision
from risk import RiskManager
from state import Portfolio

NOW = float(START + 100)


class ApiExc(Exception):
    def __init__(self, status_code, msg="err"):
        super().__init__(msg)
        self.status_code = status_code
        self.error_msg = msg


class FakeClob:
    def __init__(self, *a, **kw):
        self.posted = []
        self.post_responses = []
        self.post_exceptions = []
        self.open_orders = []
        self.details = {}
        self.cancelled = []
        self.hb = []

    def get_address(self):
        return "0xSigner"

    def set_api_creds(self, creds):
        self.creds = creds

    def create_or_derive_api_creds(self):
        return "derived"

    def get_balance_allowance(self, params):
        return {"balance": "50000000", "allowances": {"0xex": "1000"}}

    def create_order(self, args):
        return ("signed", args)

    def post_order(self, signed, order_type, post_only=False):
        self.posted.append((signed[1], order_type, post_only))
        if self.post_exceptions:
            exc = self.post_exceptions.pop(0)
            if exc is not None:
                raise exc
        return self.post_responses.pop(0)

    def get_orders(self, params=None):
        return list(self.open_orders)

    def get_order(self, oid):
        return self.details[oid]

    def cancel_orders(self, ids):
        self.cancelled.append(list(ids))
        return {"canceled": ids, "not_canceled": {}}

    def post_heartbeat(self, hb_id):
        self.hb.append(hb_id)
        return {"heartbeat_id": "hb-1"}


@pytest.fixture
def live(cfg, monkeypatch):
    monkeypatch.setattr("py_clob_client.client.ClobClient", FakeClob)
    cfg.MODE = "LIVE"
    pf = Portfolio(cfg)
    rm = RiskManager(cfg, pf)
    ex = LiveExecutor(cfg, Secrets(private_key="0x" + "11" * 32), pf, rm, clock=lambda: NOW)
    ex.register_markets([make_market()])
    return ex, ex.client, pf, rm


def books():
    return {UP: make_book("111", [(0.55, 100)], [(0.57, 100)], NOW), DOWN: make_book("222", [(0.41, 100)], [(0.43, 100)], NOW)}


def dec(mode=TAKER, side=BUY, price=0.58, shares=10.0):
    return Decision("ENTER", make_market(), UP, side, price, shares, mode, 0.8, 0.1)


def test_immediate_fill_parsing():
    assert _immediate_fill({"makingAmount": "5.7", "takingAmount": "10"}, BUY) == (10.0, pytest.approx(0.57))
    assert _immediate_fill({"makingAmount": "10", "takingAmount": "7"}, SELL) == (10.0, pytest.approx(0.70))
    assert _immediate_fill({}, BUY) == (0.0, 0.0) and _immediate_fill({"makingAmount": "x"}, BUY) == (0.0, 0.0)


async def test_start_derives_creds_and_reads_balance(live):
    ex, clob, _, rm = live
    await ex.start()
    assert clob.creds == "derived" and ex.wallet_usd == 50.0 and rm.wallet_usd == 50.0


async def test_only_limit_orders_are_ever_sent(live):
    ex, clob, _, _ = live
    clob.post_responses += [{"success": True, "orderID": "0x1", "status": "live"},
                            {"success": True, "orderID": "0x2", "status": "live"}]
    await ex.place(dec(mode=TAKER), books())
    await ex.place(dec(mode=MAKER, price=0.56), books())
    (a1, t1, po1), (a2, t2, po2) = clob.posted
    assert t1 == t2 == "GTC"                      # nie FOK/Market
    assert po1 is False and po2 is True           # Maker -> post_only
    assert (a1.price, a1.size, a1.side, a1.token_id) == (0.58, 10.0, "BUY", "111")


async def test_matched_taker_order_books_exact_fill(live, cfg):
    ex, clob, pf, _ = live
    clob.post_responses.append({"success": True, "orderID": "0xabc", "status": "matched",
                                "makingAmount": "5.70", "takingAmount": "10"})
    order = await ex.place(dec(), books())
    pos = pf.get(make_market().slug, UP)
    assert order.filled == 10 and pos.shares == 10
    assert pos.cost == pytest.approx(5.70 + cfg.TAKER_FEE_RATE * 5.70)      # Durchschnittspreis aus makingAmount
    assert not ex.open_orders()


async def test_resting_order_fills_are_synced_then_removed(live):
    ex, clob, pf, _ = live
    clob.post_responses.append({"success": True, "orderID": "0xm", "status": "live"})
    await ex.place(dec(mode=MAKER, price=0.56), books())
    assert len(ex.open_orders()) == 1
    clob.open_orders = [{"id": "0xm", "asset_id": "111", "size_matched": "4", "status": "LIVE"}]
    await ex.sync(make_market(), books(), NOW + 2)
    assert pf.shares(make_market().slug, UP) == 4 and len(ex.open_orders()) == 1
    clob.open_orders = []
    clob.details["0xm"] = {"id": "0xm", "size_matched": "10", "status": "MATCHED"}
    await ex.sync(make_market(), books(), NOW + 4)
    pos = pf.get(make_market().slug, UP)
    assert pos.shares == 10 and pos.cost == pytest.approx(10 * 0.56) and pos.fees == 0   # Maker: Limit-Preis, keine Fee
    assert not ex.open_orders()


async def test_unknown_orders_on_our_tokens_are_cancelled(live):
    ex, clob, _, _ = live
    clob.open_orders = [{"id": "0xghost", "asset_id": "111", "size_matched": "0"},
                        {"id": "0xother", "asset_id": "999", "size_matched": "0"}]   # fremder Markt: nicht anfassen
    await ex.sync(make_market(), books(), NOW + 2)
    assert clob.cancelled == [["0xghost"]]


async def test_rejected_order_leaves_no_state(live):
    ex, clob, pf, _ = live
    clob.post_responses.append({"success": False, "errorMsg": "not enough balance / allowance"})
    assert await ex.place(dec(), books()) is None
    assert not ex.open_orders() and pf.shares(make_market().slug, UP) == 0


async def test_429_is_retried_without_double_posting(live, monkeypatch):
    ex, clob, _, rm = live
    async def fast_sleep(_):
        return None
    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    clob.post_exceptions += [ApiExc(429), ApiExc(429)]
    clob.post_responses.append({"success": True, "orderID": "0x9", "status": "live"})
    order = await ex.place(dec(mode=MAKER, price=0.56), books())
    assert order is not None and len(clob.posted) == 3      # 2x abgelehnt (429), 1x angenommen
    assert not rm.breaker.is_open()


async def test_network_error_blocks_further_orders_until_reconciled(live):
    ex, clob, _, rm = live
    clob.post_exceptions.append(ConnectionError("timeout"))           # kein status_code -> unklarer Ausgang
    assert await ex.place(dec(), books()) is None
    assert len(clob.posted) == 1                                      # NICHT blind wiederholt
    assert await ex.place(dec(), books()) is None and len(clob.posted) == 1   # gesperrt
    assert rm.breaker.consecutive_errors == 1


async def test_business_rejection_does_not_count_as_connectivity_error(live):
    ex, clob, _, rm = live
    for _ in range(10):
        clob.post_exceptions.append(ApiExc(400, "invalid order"))
        await ex.place(dec(), books())
    assert rm.breaker.consecutive_errors == 0 and not rm.breaker.is_open()


async def test_cancel_and_cancel_all(live):
    ex, clob, _, _ = live
    clob.post_responses += [{"success": True, "orderID": "0xa", "status": "live"},
                            {"success": True, "orderID": "0xb", "status": "live"}]
    a = await ex.place(dec(mode=MAKER, price=0.56), books())
    await ex.place(dec(mode=MAKER, price=0.55), books())
    assert await ex.cancel(a) and len(ex.open_orders()) == 1
    assert await ex.cancel_all() == 1 and not ex.open_orders()
    assert clob.cancelled == [["0xa"], ["0xb"]]


async def test_heartbeat_chain(live):
    ex, clob, _, _ = live
    task = asyncio.create_task(ex.heartbeat_loop())
    await asyncio.sleep(0.2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert clob.hb[0] is None and ex._hb_id == "hb-1"


async def test_fresh_order_missing_from_open_list_stays_tracked(live):
    """Eventual Consistency: get_orders kennt die Order noch nicht, get_order sagt LIVE -> weiter tracken."""
    ex, clob, pf, _ = live
    clob.post_responses.append({"success": True, "orderID": "0xnew", "status": "live"})
    await ex.place(dec(mode=MAKER, price=0.56), books())
    clob.open_orders = []
    clob.details["0xnew"] = {"id": "0xnew", "size_matched": "0", "status": "LIVE"}
    await ex.sync(make_market(), books(), NOW + 2)
    assert len(ex.open_orders()) == 1


async def test_order_unknown_to_server_is_dropped(live):
    ex, clob, _, _ = live
    clob.post_responses.append({"success": True, "orderID": "0xgone", "status": "live"})
    await ex.place(dec(mode=MAKER, price=0.56), books())
    clob.open_orders = []
    clob.details = {}                                        # get_order -> KeyError (kein status_code) -> bleibt
    await ex.sync(make_market(), books(), NOW + 2)
    assert len(ex.open_orders()) == 1
    def raise404(oid):
        raise ApiExc(404, "not found")
    clob.get_order = raise404
    await ex.sync(make_market(), books(), NOW + 4)
    assert not ex.open_orders()
