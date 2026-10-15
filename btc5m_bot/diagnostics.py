"""``python main.py --check``: prüft alle externen Schnittstellen und die Konfiguration.

Gedacht für den ersten Start auf deinem Rechner/Server: zeigt, ob Gamma/CLOB/Preis-Feeds erreichbar
sind, ob das Slug-Schema der 5-Minuten-Märkte (``SLUG_PREFIX``) noch stimmt und - bei gesetztem
Private Key - ob Wallet, API-Credentials und RPC funktionieren. Es werden keine Orders gesendet.
"""
from __future__ import annotations

import asyncio
import time
from typing import List

import aiohttp

from config import Config, Secrets
from execution import ChainClient
from market_finder import BookFetcher, MarketFinder, window_start
from price_feed import PriceFeed
from utils import HttpClient

OK, WARN, FAIL = "[ OK ]", "[WARN]", "[FAIL]"


def _p(status: str, msg: str, results: List[str]) -> None:
    results.append(status)
    print(f"{status} {msg}")


async def run_checks(cfg: Config, secrets: Secrets) -> int:
    res: List[str] = []
    print(f"=== Konfiguration ===\nModus: {cfg.MODE} | {secrets}")
    _p(OK if cfg.MODE == "PAPER" else WARN, f"MODE={cfg.MODE}" + ("" if cfg.MODE == "PAPER" else " (echtes Geld!)"), res)

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        http = HttpClient(session)
        finder = MarketFinder(cfg, http)

        print("\n=== Polymarket ===")
        skew = await finder.clock_skew()
        if skew is None:
            _p(FAIL, f"CLOB {cfg.CLOB_URL}/time nicht erreichbar", res)
        else:
            _p(OK if abs(skew) < 2 else WARN, f"CLOB erreichbar, Uhr-Abweichung {skew:+.2f}s (NTP prüfen, wenn > 2s)", res)

        now = time.time()
        cur = window_start(now, cfg.WINDOW_SEC)
        market = None
        for start in (cur, cur + cfg.WINDOW_SEC):
            m = await finder.fetch_window(start)
            if m is None:
                _p(FAIL, f"Gamma: kein Markt für Slug '{cfg.SLUG_PREFIX}{start}' (SLUG_PREFIX korrekt? Region/Proxy?)", res)
                continue
            _p(OK, f"Gamma: {m.slug} | tick={m.tick_size} min_size={m.min_order_size} neg_risk={m.neg_risk} "
                   f"accepting={m.accepting_orders}\n         UP={m.up_token[:14]}… DOWN={m.down_token[:14]}… "
                   f"condition={m.condition_id[:14]}…", res)
            if market is None and m.start_ts == cur:
                market = m
        if market is not None:
            books = await BookFetcher(cfg, http).fetch([market.up_token, market.down_token])
            for name, tok in (("UP", market.up_token), ("DOWN", market.down_token)):
                b = books.get(tok)
                if b is None:
                    _p(FAIL, f"CLOB /book für {name} nicht lesbar", res)
                else:
                    _p(OK if b.best_bid and b.best_ask else WARN,
                       f"Orderbuch {name}: bid={b.best_bid} ask={b.best_ask} spread={b.spread} Latenz={b.latency_ms:.0f}ms", res)

        print("\n=== BTC-Preisquellen (8 s Messung) ===")
        feed = PriceFeed(cfg, http)
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(session, stop))
        await asyncio.sleep(8)
        for s in feed.sources:
            if s.ticks > 0:
                _p(OK, f"{s.name}: {s.ticks} Ticks, letzter Preis {s.last_price:,.2f}", res)
            else:
                _p(FAIL if s.name != "chainlink" else WARN, f"{s.name}: keine Ticks empfangen (Region geblockt? URL korrekt?)", res)
        if not any(s.ticks for s in feed.sources):
            _p(FAIL, "Keine einzige Preisquelle liefert Daten - der Bot kann nicht handeln", res)
        await feed.ensure_reference(cur)
        for s in feed.sources:
            ref = feed.reference(s, cur)
            if ref:
                print(f"       Referenzpreis {s.name} @ Fensterstart: {ref:,.2f}")
        stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        if secrets.private_key:
            print("\n=== Wallet / LIVE ===")
            await _check_live(cfg, secrets, http, res)
        else:
            print("\n(Kein PRIVATE_KEY gesetzt - Wallet-/LIVE-Checks übersprungen; für PAPER nicht nötig.)")

    fails = res.count(FAIL)
    print(f"\nErgebnis: {res.count(OK)} OK, {res.count(WARN)} Warnungen, {fails} Fehler")
    return 1 if fails else 0


async def _check_live(cfg: Config, secrets: Secrets, http: HttpClient, res: List[str]) -> None:
    try:
        geo = await http.get_json("https://polymarket.com/api/geoblock", name="geoblock", retries=1)
        if isinstance(geo, dict) and geo.get("blocked") is True:
            _p(FAIL, f"Polymarket meldet diese IP als BLOCKIERT (Land={geo.get('country')}). LIVE-Orders würden abgelehnt.", res)
        else:
            _p(OK, "Geoblock-Check: nicht blockiert", res)
    except Exception as e:
        _p(WARN, f"Geoblock-Check nicht möglich: {e}", res)

    miss = secrets.missing_for_live()
    if miss:
        _p(FAIL, "Fehlende .env-Einträge: " + ", ".join(miss), res)
        return
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, AssetType, BalanceAllowanceParams
        client = ClobClient(cfg.CLOB_URL, chain_id=cfg.CHAIN_ID, key=secrets.private_key,
                            signature_type=secrets.signature_type, funder=secrets.funder or None)
        _p(OK, f"Signer-Adresse: {client.get_address()} (SigType {secrets.signature_type}, Funder {secrets.funder or '-'})", res)
        if secrets.api_key and secrets.api_secret and secrets.passphrase:
            creds = ApiCreds(api_key=secrets.api_key, api_secret=secrets.api_secret, api_passphrase=secrets.passphrase)
        else:
            creds = await asyncio.to_thread(client.create_or_derive_api_creds)
            _p(WARN, "Keine API-Credentials in .env - wurden aus dem Private Key abgeleitet (funktioniert, ist aber ok)", res)
        client.set_api_creds(creds)
        bal = await asyncio.to_thread(client.get_balance_allowance, BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        usd = float(bal.get("balance", 0)) / 1e6
        _p(OK if usd > 0 else WARN, f"CLOB-Guthaben (USDC): {usd:,.2f}", res)
        allowances = bal.get("allowances") or {}
        if allowances and all(float(v) == 0 for v in allowances.values()):
            _p(WARN, "USDC-Allowance = 0 -> bei EOA-Wallet einmalig `python main.py --approve` ausführen", res)
    except Exception as e:
        _p(FAIL, f"CLOB-Auth fehlgeschlagen: {type(e).__name__}: {e}", res)

    if not secrets.rpc_url:
        _p(WARN, "Kein RPC_URL - Auto-Redeem deaktiviert (Gewinne manuell im Polymarket-UI einlösen)", res)
        return
    try:
        chain = ChainClient(cfg, secrets)
        cid = await asyncio.to_thread(lambda: chain.w3.eth.chain_id)
        pol = await asyncio.to_thread(chain.native_balance)
        _p(OK if cid == cfg.CHAIN_ID else FAIL, f"RPC erreichbar, chainId={cid} (erwartet {cfg.CHAIN_ID})", res)
        _p(OK if pol >= 0.05 else WARN, f"POL-Guthaben für Gas ({chain.account.address}): {pol:.4f}", res)
        if secrets.signature_type != 0:
            _p(WARN, "Proxy-/Safe-Wallet: Auto-Redeem nicht möglich -> im Polymarket-UI 'Claim' verwenden", res)
    except Exception as e:
        _p(FAIL, f"RPC/Web3-Check fehlgeschlagen: {type(e).__name__}: {e}", res)


def approve_allowances(cfg: Config, secrets: Secrets) -> int:
    """Einmalige Freigaben (nur EOA, SIGNATURE_TYPE=0). Proxy-/Safe-Wallets haben sie bereits."""
    if secrets.signature_type != 0:
        print("SIGNATURE_TYPE != 0: Proxy-/Safe-Wallets aus dem Polymarket-UI haben die Freigaben bereits. Nichts zu tun.")
        return 0
    if not (secrets.private_key and secrets.rpc_url):
        print("PRIVATE_KEY und RPC_URL müssen in .env gesetzt sein.")
        return 1
    chain = ChainClient(cfg, secrets)
    print(f"Wallet: {chain.account.address}\nEs werden zwei On-Chain-Transaktionen gesendet (Gas in POL):\n"
          f"  1) USDC.e: approve(Exchange {cfg.EXCHANGE_ADDRESS}, unbegrenzt)\n"
          f"  2) Conditional Tokens: setApprovalForAll(Exchange, true)\n"
          "Das ist dieselbe Freigabe, die auch die Polymarket-Oberfläche setzt.")
    if input("Fortfahren? (ja/nein): ").strip().lower() not in ("ja", "j", "yes", "y"):
        print("Abgebrochen.")
        return 1
    hashes = chain.approve_exchange()
    print("Fertig." if hashes else "Freigaben waren bereits gesetzt.", *hashes)
    return 0
