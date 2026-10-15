"""Order-Ausführung (PAPER & LIVE), Order-Management und Abrechnung/Redeem.

* ``PaperExecutor`` simuliert Fills gegen das echte Orderbuch (inkl. Taker-Gebühr).
* ``LiveExecutor`` sendet ausschließlich Limit-Orders über ``py-clob-client`` (GTC; Maker mit
  ``post_only``). Es gibt keinen Pfad für Market-Orders.
* ``ChainClient`` (web3) liest Auszahlungen on-chain und löst Gewinner-Tokens beim
  Conditional-Tokens-Contract ein (``redeemPositions``).
* ``Settler`` rechnet beendete Fenster ab und löst im LIVE-Modus automatisch ein.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from config import Config, Secrets
from models import BUY, MAKER, SELL, TAKER, Decision, Market, OpenOrder, OrderBook, Position
from risk import RiskManager
from state import Portfolio
from utils import RateLimitedLog

log = logging.getLogger("execution")
_rl = RateLimitedLog(log, 30.0)

Books = Dict[str, Optional[OrderBook]]


# =============================================================================== Basis
class ExecutorBase:
    def __init__(self, cfg: Config, portfolio: Portfolio, risk: RiskManager,
                 clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.pf = portfolio
        self.risk = risk
        self.clock = clock
        self.orders: Dict[str, OpenOrder] = {}

    def open_orders(self, slug: Optional[str] = None) -> List[OpenOrder]:
        return [o for o in self.orders.values() if slug is None or o.slug == slug]

    def register_markets(self, markets: Iterable[Market]) -> None:  # nur LIVE relevant
        pass

    async def start(self) -> None:
        pass

    async def place(self, d: Decision, books: Books) -> Optional[OpenOrder]:
        raise NotImplementedError

    async def sync(self, market: Market, books: Books, now: float) -> None:
        raise NotImplementedError

    async def cancel(self, order: OpenOrder) -> bool:
        raise NotImplementedError

    async def cancel_many(self, orders: List[OpenOrder]) -> int:
        n = 0
        for o in orders:
            n += 1 if await self.cancel(o) else 0
        return n

    async def cancel_all(self, slug: Optional[str] = None) -> int:
        return await self.cancel_many(self.open_orders(slug))

    async def close(self) -> None:
        pass

    # gemeinsame Buchungslogik
    def _book_fill(self, order: OpenOrder, qty: float, price: float, taker: bool) -> None:
        if qty <= 0:
            return
        assert order.market is not None
        fee = self.cfg.TAKER_FEE_RATE * price * qty if taker else 0.0
        self.pf.apply_fill(order.market, order.outcome, order.side, qty, price, fee,
                           fair=order.fair, edge=order.edge, ts=self.clock())
        order.filled += qty
        log.info("FILL %s %s %s %.2f @ %.3f (%s, Fee %.3f) | %s", self.cfg.MODE, order.side, order.outcome, qty,
                 price, "taker" if taker else "maker", fee, order.slug)


# ================================================================================ Paper
class PaperExecutor(ExecutorBase):
    """Simulation: Taker-Orders füllen sofort gegen das Buch, Maker-Orders warten auf Durchhandeln."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._seq = 0

    async def place(self, d: Decision, books: Books) -> Optional[OpenOrder]:
        book = books.get(d.outcome)
        if book is None:
            return None
        self._seq += 1
        order = OpenOrder(order_id=f"paper-{self._seq}", slug=d.market.slug, end_ts=d.market.end_ts,
                          outcome=d.outcome, token_id=d.market.token(d.outcome), side=d.side, price=d.price,
                          size=d.shares, created_ts=self.clock(), mode=d.mode, fair=d.fair, edge=d.edge,
                          market=d.market)
        if d.mode == MAKER:  # Post-only darf nicht kreuzen
            crosses = ((d.side == BUY and book.best_ask is not None and d.price >= book.best_ask - 1e-9) or
                       (d.side == SELL and book.best_bid is not None and d.price <= book.best_bid + 1e-9))
            if crosses:
                log.info("PAPER: Post-only-Order würde kreuzen - abgelehnt (%s %s @ %.3f)", d.side, d.outcome, d.price)
                return None
        else:
            filled, vwap = (book.buy_fill(d.shares, d.price) if d.side == BUY
                            else book.sell_fill(d.shares, d.price))
            if filled > 0:
                self._book_fill(order, filled, vwap, taker=True)
        log.info("ORDER %s %s %s %.2f @ %.3f [%s] fair=%.3f edge=%.3f (%s)", "PAPER", d.side, d.outcome,
                 d.shares, d.price, d.mode, d.fair, d.edge, d.reason)
        if order.remaining >= 0.01:           # Rest bleibt als GTC-Limit stehen (wie an der Börse)
            self.orders[order.order_id] = order
        return order

    async def sync(self, market: Market, books: Books, now: float) -> None:
        for order in [o for o in self.orders.values() if o.slug == market.slug]:
            book = books.get(order.outcome)
            if book is None:
                continue
            filled, _ = (book.buy_fill(order.remaining, order.price) if order.side == BUY
                         else book.sell_fill(order.remaining, order.price))
            if filled > 0:
                self._book_fill(order, filled, order.price, taker=False)   # Maker füllt zum eigenen Preis
            if order.remaining < 0.01:
                self.orders.pop(order.order_id, None)

    async def cancel(self, order: OpenOrder) -> bool:
        return self.orders.pop(order.order_id, None) is not None


# ================================================================================= Live
class LiveExecutor(ExecutorBase):
    def __init__(self, cfg: Config, secrets: Secrets, portfolio: Portfolio, risk: RiskManager,
                 clock: Callable[[], float] = time.time):
        super().__init__(cfg, portfolio, risk, clock)
        from py_clob_client.client import ClobClient  # lazy: PAPER braucht die Bibliothek nicht
        self.secrets = secrets
        # Ein Thread für alle (blockierenden) CLOB-Aufrufe: serialisiert und thread-sicher.
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clob")
        # Eigener Thread für den Heartbeat: eine langsame Order darf ihn nicht über die 10-s-Frist verzögern.
        self.hb_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clob-hb")
        self.client = ClobClient(cfg.CLOB_URL, chain_id=cfg.CHAIN_ID, key=secrets.private_key,
                                 signature_type=secrets.signature_type, funder=secrets.funder or None)
        self.known_tokens: Set[str] = set()
        self.wallet_usd: Optional[float] = None
        self._last_sync = 0.0
        self._last_balance = 0.0
        self._hb_id: Optional[str] = None
        self._ambiguous_until = 0.0
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ Aufrufe
    async def _call(self, name: str, fn: Callable[..., Any], *args, retries_429: int = 3,
                    record: bool = True, pool: Optional[ThreadPoolExecutor] = None, **kwargs) -> Any:
        """Blockierenden Client-Aufruf im Thread ausführen: Latenzmessung, 429-Backoff, Fehlerklassifikation.

        Nur bei HTTP 429 (Anfrage wurde nicht verarbeitet) wird wiederholt - nie blind bei Netzwerkfehlern,
        damit eine Order niemals doppelt abgeschickt wird.
        """
        loop = asyncio.get_running_loop()
        attempt = 0
        while True:
            t0 = time.perf_counter()
            try:
                res = await loop.run_in_executor(pool or self.pool, functools.partial(fn, *args, **kwargs))
                if record:
                    self.risk.breaker.record(True, (time.perf_counter() - t0) * 1000.0)
                return res
            except asyncio.CancelledError:
                raise
            except Exception as e:
                status = getattr(e, "status_code", None)
                if status == 429 and attempt < retries_429:
                    delay = min(2.0 ** attempt, 8.0)
                    _rl.warning("429", "CLOB Rate-Limit (429) bei %s - warte %.0fs", name, delay)
                    attempt += 1
                    await asyncio.sleep(delay)
                    continue
                if record:
                    # 4xx (außer 429) = fachliche Ablehnung, kein Verbindungsproblem
                    business = isinstance(status, int) and 400 <= status < 500 and status != 429
                    if not business:
                        self.risk.breaker.record(False)
                raise

    # ---------------------------------------------------------------- Lebenszyklus
    async def start(self) -> None:
        from py_clob_client.clob_types import ApiCreds
        s = self.secrets
        if s.api_key and s.api_secret and s.passphrase:
            creds = ApiCreds(api_key=s.api_key, api_secret=s.api_secret, api_passphrase=s.passphrase)
        else:
            log.info("Keine API-Credentials in .env - leite sie aus dem Private Key ab")
            creds = await self._call("derive", self.client.create_or_derive_api_creds, record=False)
        self.client.set_api_creds(creds)
        await self.refresh_balance(force=True)
        log.info("LIVE-Executor bereit | Signer=%s | Funder=%s | SigType=%s | USDC-Guthaben=%s",
                 self.client.get_address(), s.funder or "(Signer)", s.signature_type,
                 f"{self.wallet_usd:.2f}" if self.wallet_usd is not None else "unbekannt")

    async def close(self) -> None:
        self._stop.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.hb_pool.shutdown(wait=False, cancel_futures=True)

    def register_markets(self, markets: Iterable[Market]) -> None:
        for m in markets:
            self.known_tokens.add(m.up_token)
            self.known_tokens.add(m.down_token)

    async def refresh_balance(self, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._last_balance < 60:
            return
        self._last_balance = now
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
        try:
            res = await self._call("balance", self.client.get_balance_allowance,
                                   BalanceAllowanceParams(asset_type=AssetType.COLLATERAL), record=False)
            self.wallet_usd = float(res.get("balance", 0)) / 1e6
            self.risk.wallet_usd = self.wallet_usd
            allowances = res.get("allowances") or ({"x": res["allowance"]} if "allowance" in res else {})
            if allowances and all(float(v) == 0 for v in allowances.values()):
                _rl.warning("allow", "USDC-Allowance ist 0 - bei EOA-Wallets einmalig `python main.py --approve` ausführen")
        except Exception as e:
            _rl.warning("balance", "Guthaben-Abfrage fehlgeschlagen: %s", e)

    async def heartbeat_loop(self) -> None:
        """Dead-Man's-Switch: bleibt der Heartbeat >10 s aus, storniert der Server alle Orders."""
        while not self._stop.is_set():
            try:
                resp = await self._call("heartbeat", self.client.post_heartbeat, self._hb_id, record=False,
                                        retries_429=0, pool=self.hb_pool)
                if isinstance(resp, dict) and resp.get("heartbeat_id"):
                    self._hb_id = resp["heartbeat_id"]
            except asyncio.CancelledError:
                raise
            except Exception as e:
                _rl.warning("hb", "Heartbeat fehlgeschlagen: %s", e)
            await asyncio.sleep(5.0)

    # --------------------------------------------------------------------- Orders
    async def place(self, d: Decision, books: Books) -> Optional[OpenOrder]:
        if self.clock() < self._ambiguous_until:
            _rl.warning("amb", "Vorherige Order-Antwort war unklar - warte auf Abgleich, bevor neue Orders gehen")
            return None
        from py_clob_client.clob_types import OrderArgs, OrderType
        args = OrderArgs(token_id=d.market.token(d.outcome), price=d.price, size=d.shares, side=d.side)
        try:
            signed = await self._call("order", self.client.create_order, args)
            resp = await self._call("order", self.client.post_order, signed, OrderType.GTC, d.mode == MAKER)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            status = getattr(e, "status_code", None)
            if status is None:      # Netzwerkfehler/Timeout: unklar, ob die Order angenommen wurde
                self._ambiguous_until = self.clock() + 5.0
                self._last_sync = 0.0   # nächster Sync gleicht sofort ab (Orphans werden storniert)
            _rl.warning("place-err", "Order abgelehnt/fehlgeschlagen (%s %s @ %.3f): %s", d.side, d.outcome,
                        d.price, getattr(e, "error_msg", e))
            return None
        if not isinstance(resp, dict) or not resp.get("success") or not resp.get("orderID"):
            _rl.warning("place-rej", "Order abgelehnt: %s", (resp or {}).get("errorMsg") if isinstance(resp, dict) else resp)
            return None
        order = OpenOrder(order_id=resp["orderID"], slug=d.market.slug, end_ts=d.market.end_ts, outcome=d.outcome,
                          token_id=d.market.token(d.outcome), side=d.side, price=d.price, size=d.shares,
                          created_ts=self.clock(), mode=d.mode, fair=d.fair, edge=d.edge, market=d.market)
        log.info("ORDER LIVE %s %s %.2f @ %.3f [%s] id=%s status=%s fair=%.3f edge=%.3f (%s)", d.side, d.outcome,
                 d.shares, d.price, d.mode, order.order_id[:12], resp.get("status"), d.fair, d.edge, d.reason)
        # Sofort gematchter Anteil: making/taking-Amount liefert den exakten Durchschnittspreis
        matched, avg = _immediate_fill(resp, d.side)
        if matched > 0:
            self._book_fill(order, matched, avg if avg else d.price, taker=(d.mode == TAKER))
        if str(resp.get("status", "")).lower() != "matched" and order.remaining >= 0.01:
            self.orders[order.order_id] = order
        return order

    async def sync(self, market: Market, books: Books, now: float) -> None:
        """Gleicht offene Orders mit dem Server ab (Fills, Stornos) und räumt unbekannte Orders ab."""
        if now - self._last_sync < 1.0:
            return
        self._last_sync = now
        from py_clob_client.clob_types import OpenOrderParams
        try:
            remote_list = await self._call("sync", self.client.get_orders, OpenOrderParams(), record=False)
        except Exception as e:
            _rl.warning("sync", "Order-Abgleich fehlgeschlagen: %s", e)
            return
        remote = {r.get("id"): r for r in remote_list or [] if isinstance(r, dict)}
        for order in list(self.orders.values()):
            r = remote.get(order.order_id)
            if r is None:   # nicht (mehr) in der Liste offener Orders -> endgültigen Stand holen
                try:
                    r = await self._call("order-detail", self.client.get_order, order.order_id, record=False)
                except Exception as e:
                    if getattr(e, "status_code", None) == 404:      # Server kennt die Order nicht (mehr)
                        self.orders.pop(order.order_id, None)
                    else:
                        _rl.warning("detail", "Order-Detail %s: %s", order.order_id[:10], e)
                    continue
            # Nur bei terminalem Status aus dem Tracking nehmen (frische Orders erscheinen evtl. verzögert)
            self._apply_remote(order, r)
        # Orphans: offene Orders auf unseren Tokens, die wir nicht kennen -> stornieren
        orphans = [oid for oid, r in remote.items()
                   if oid not in self.orders and r.get("asset_id") in self.known_tokens]
        if orphans:
            log.warning("%d unbekannte offene Order(s) auf BTC-5m-Tokens - storniere", len(orphans))
            await self._cancel_ids(orphans)
        await self.refresh_balance()

    def _apply_remote(self, order: OpenOrder, r: Optional[dict]) -> None:
        if not isinstance(r, dict):
            return
        try:
            matched = float(r.get("size_matched", 0) or 0)
        except (TypeError, ValueError):
            return
        delta = matched - order.filled
        if delta > 1e-9:
            # Spätere Fills einer ruhenden Order laufen zum Limit-Preis (Maker) bzw. besser; Limit = konservativ
            self._book_fill(order, delta, order.price, taker=(order.mode == TAKER))
        status = str(r.get("status", "")).upper()
        order.status = status or order.status
        if status in ("CANCELED", "CANCELLED", "MATCHED", "INVALID", "CANCELED_MARKET_RESOLVED"):
            self.orders.pop(order.order_id, None)

    async def cancel(self, order: OpenOrder) -> bool:
        ok = await self._cancel_ids([order.order_id])
        if ok:
            self.orders.pop(order.order_id, None)
        return ok

    async def cancel_many(self, orders: List[OpenOrder]) -> int:
        ids = [o.order_id for o in orders]
        if not ids:
            return 0
        ok = await self._cancel_ids(ids)
        if ok:
            for i in ids:
                self.orders.pop(i, None)
        return len(ids) if ok else 0

    async def _cancel_ids(self, ids: List[str]) -> bool:
        try:
            res = await self._call("cancel", self.client.cancel_orders, ids)
            not_canceled = (res or {}).get("not_canceled") if isinstance(res, dict) else None
            if not_canceled:
                # z. B. "already canceled / matched" - der nächste Sync klärt den Endzustand
                log.info("Storno: nicht storniert (bereits gefüllt/weg?): %s", not_canceled)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _rl.warning("cancel", "Storno fehlgeschlagen: %s", e)
            return False


def _immediate_fill(resp: dict, side: str) -> Tuple[float, float]:
    """(Shares, Durchschnittspreis) des sofort gematchten Teils aus making/takingAmount."""
    try:
        making = float(resp.get("makingAmount") or 0)
        taking = float(resp.get("takingAmount") or 0)
    except (TypeError, ValueError):
        return 0.0, 0.0
    # BUY: making = USDC bezahlt, taking = Shares erhalten. SELL: making = Shares, taking = USDC.
    shares, usdc = (taking, making) if side == BUY else (making, taking)
    if shares <= 0 or usdc <= 0:
        return 0.0, 0.0
    return shares, usdc / shares


# =============================================================================== Chain
CTF_ABI = [
    {"name": "redeemPositions", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "collateralToken", "type": "address"}, {"name": "parentCollectionId", "type": "bytes32"},
                {"name": "conditionId", "type": "bytes32"}, {"name": "indexSets", "type": "uint256[]"}],
     "outputs": []},
    {"name": "payoutDenominator", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "conditionId", "type": "bytes32"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "payoutNumerators", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "conditionId", "type": "bytes32"}, {"name": "index", "type": "uint256"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "setApprovalForAll", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}], "outputs": []},
    {"name": "isApprovedForAll", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "operator", "type": "address"}],
     "outputs": [{"name": "", "type": "bool"}]},
]
ERC20_ABI = [
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
]
ZERO32 = b"\x00" * 32
MAX_UINT = 2 ** 256 - 1
GWEI = 10 ** 9


class ChainClient:
    """Synchroner web3-Client (Aufrufe laufen über ``asyncio.to_thread``)."""

    def __init__(self, cfg: Config, secrets: Secrets):
        from eth_account import Account
        from web3 import Web3
        self.cfg = cfg
        self.Web3 = Web3
        self.w3 = Web3(Web3.HTTPProvider(secrets.rpc_url, request_kwargs={"timeout": 20}))
        self.account = Account.from_key(secrets.private_key)
        self.ctf = self.w3.eth.contract(address=Web3.to_checksum_address(cfg.CTF_ADDRESS), abi=CTF_ABI)
        self.usdc = self.w3.eth.contract(address=Web3.to_checksum_address(cfg.COLLATERAL_ADDRESS), abi=ERC20_ABI)

    @staticmethod
    def _cid(condition_id: str) -> bytes:
        return bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)

    def native_balance(self) -> float:
        return float(self.Web3.from_wei(self.w3.eth.get_balance(self.account.address), "ether"))

    def usdc_balance(self) -> float:
        return self.usdc.functions.balanceOf(self.account.address).call() / 1e6

    def payout_ratios(self, condition_id: str) -> Optional[List[float]]:
        """Auszahlung je Share pro Outcome-Slot, None solange das Ergebnis on-chain nicht gemeldet ist."""
        cid = self._cid(condition_id)
        den = self.ctf.functions.payoutDenominator(cid).call()
        if den == 0:
            return None
        return [self.ctf.functions.payoutNumerators(cid, i).call() / den for i in (0, 1)]

    def _send(self, fn) -> str:
        w3, acct = self.w3, self.account
        gas = int(fn.estimate_gas({"from": acct.address}) * 1.3)
        try:
            prio = max(int(w3.eth.max_priority_fee), 30 * GWEI)   # Polygon verlangt mind. ~25-30 gwei Tip
        except Exception:
            prio = 35 * GWEI
        max_fee = int(w3.eth.gas_price * 2) + prio
        tx = fn.build_transaction({
            "from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
            "gas": gas, "maxFeePerGas": max_fee, "maxPriorityFeePerGas": prio, "chainId": self.cfg.CHAIN_ID,
        })
        signed = acct.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        tx_hash = w3.eth.send_raw_transaction(raw)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        if receipt.status != 1:
            raise RuntimeError(f"Transaktion {tx_hash.hex()} fehlgeschlagen (status 0)")
        return tx_hash.hex()

    def redeem(self, condition_id: str) -> str:
        fn = self.ctf.functions.redeemPositions(self.Web3.to_checksum_address(self.cfg.COLLATERAL_ADDRESS),
                                                ZERO32, self._cid(condition_id), [1, 2])
        return self._send(fn)

    def approve_exchange(self) -> List[str]:
        """Einmalig für EOA-Wallets: USDC + Conditional Tokens für den CTF-Exchange freigeben."""
        ex = self.Web3.to_checksum_address(self.cfg.EXCHANGE_ADDRESS)
        hashes = []
        if self.usdc.functions.allowance(self.account.address, ex).call() < MAX_UINT // 2:
            hashes.append(self._send(self.usdc.functions.approve(ex, MAX_UINT)))
        if not self.ctf.functions.isApprovedForAll(self.account.address, ex).call():
            hashes.append(self._send(self.ctf.functions.setApprovalForAll(ex, True)))
        return hashes


# ============================================================================== Settler
class Settler:
    """Rechnet beendete Fenster ab und löst im LIVE-Modus Gewinner-Tokens ein."""

    MAX_REDEEM_ATTEMPTS = 8

    def __init__(self, cfg: Config, secrets: Secrets, portfolio: Portfolio, finder, feed, risk: RiskManager,
                 chain: Optional[ChainClient] = None):
        self.cfg, self.secrets, self.pf = cfg, secrets, portfolio
        self.finder, self.feed, self.risk, self.chain = finder, feed, risk, chain

    async def run_once(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        for pos in self.pf.pending_settlement():
            if now < pos.end_ts + self.cfg.SETTLE_DELAY_SEC:
                continue
            ratio = await self._payout_ratio(pos, now)
            if ratio is None:
                _rl.info(f"wait-{pos.slug}", "Warte auf Ergebnis von %s (%s)", pos.slug, pos.outcome)
                continue
            pnl = self.pf.settle(pos, ratio)
            log.info("ABRECHNUNG %s %s: %s | %.2f Shares, Kosten %.2f -> Auszahlung %.2f | PnL %+.2f USD | Gesamt-PnL %+.2f",
                     pos.slug, pos.outcome, "GEWONNEN" if ratio > 0 else "verloren", pos.shares, pos.cost,
                     pos.payout, pnl, self.pf.realized_pnl)
            if not self.cfg.is_live or ratio <= 0:
                pos.redeemed = True          # Paper bzw. Verlierer: nichts einzulösen
            self.pf.save()
        if self.cfg.is_live and self.cfg.AUTO_REDEEM:
            for pos in self.pf.pending_redeem():
                await self._redeem(pos, now)
        self.pf.prune(now)

    async def _payout_ratio(self, pos: Position, now: float) -> Optional[float]:
        if self.cfg.is_live and self.chain is not None:
            try:
                ratios = await asyncio.to_thread(self.chain.payout_ratios, pos.condition_id)
            except Exception as e:
                _rl.warning("rpc", "On-Chain-Abfrage fehlgeschlagen (RPC?): %s", e)
                ratios = None
            if ratios is not None:
                return ratios[pos.outcome_index]
        winner = await self.finder.fetch_outcome(pos.start_ts)
        if winner is None and not self.cfg.is_live \
                and now > pos.end_ts + self.cfg.PAPER_SPOT_FALLBACK_SEC:
            winner = self.feed.resolve_by_spot(pos.start_ts, pos.end_ts)
            if winner:
                log.info("Paper-Abrechnung per Spot-Preis (Gamma lieferte kein Ergebnis): %s", winner)
        if winner is None:
            return None
        return 1.0 if winner == pos.outcome else 0.0

    async def _redeem(self, pos: Position, now: float) -> None:
        if self.secrets.signature_type != 0 or self.chain is None:
            pos.manual_redeem = True
            why = ("Proxy-/Safe-Wallet" if self.secrets.signature_type != 0 else "kein RPC_URL gesetzt")
            log.warning("Bitte %.2f USDC aus %s manuell einlösen (%s) - Polymarket-UI: 'Claim'", pos.payout,
                        pos.slug, why)
            self.pf.save()
            return
        if now < pos.redeem_next_ts:
            return
        try:
            tx = await asyncio.to_thread(self.chain.redeem, pos.condition_id)
            pos.redeemed = True
            log.info("REDEEM %s: %.2f USDC eingelöst | tx=%s", pos.slug, pos.payout, tx)
        except Exception as e:
            pos.redeem_attempts += 1
            pos.redeem_next_ts = now + min(30.0 * 2 ** pos.redeem_attempts, 1800.0)
            _rl.warning(f"redeem-{pos.slug}", "Redeem %s fehlgeschlagen (Versuch %d/%d): %s", pos.slug,
                        pos.redeem_attempts, self.MAX_REDEEM_ATTEMPTS, e)
            if pos.redeem_attempts >= self.MAX_REDEEM_ATTEMPTS:
                pos.manual_redeem = True
                log.error("Redeem %s endgültig fehlgeschlagen - bitte im Polymarket-UI manuell einlösen", pos.slug)
        self.pf.save()
