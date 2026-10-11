"""Check every external dependency the agent needs, before risking money.

Nothing in this module can be verified from a sandbox with no network, so it
exists to be run on the machine that will actually trade. It answers, in one
command, the questions the test suite cannot: does the Gamma API still return
the fields this code reads, does the CLOB answer, does the wallet resolve to a
balance, and does research work.

Every check is independent and catches its own failure, so one broken
dependency does not hide the state of the others.
"""

import os
import sys
from typing import Callable, List, Tuple

# Exactly the keys map_api_to_market reads. If the API stops returning one,
# the trading path raises KeyError and nothing works, so this is the check
# that matters most after two years of pinned dependencies.
REQUIRED_MARKET_FIELDS = [
    "id",
    "question",
    "endDate",
    "description",
    "active",
    "funded",
    "rewardsMinSize",
    "rewardsMaxSpread",
    "spread",
    "outcomes",
    "outcomePrices",
    "clobTokenIds",
]

OK, WARN, FAIL = "ok", "warn", "fail"


def _check_python() -> Tuple[str, str]:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 9):
        return FAIL, f"Python {major}.{minor}; py_clob_client needs >= 3.9.10"
    return OK, f"Python {major}.{minor}"


def _check_required_env() -> Tuple[str, str]:
    missing = [
        k for k in ("OPENAI_API_KEY", "POLYGON_WALLET_PRIVATE_KEY") if not os.getenv(k)
    ]
    if missing:
        return FAIL, f"missing: {', '.join(missing)}"
    return OK, "OPENAI_API_KEY and POLYGON_WALLET_PRIVATE_KEY set"


def _check_research_env() -> Tuple[str, str]:
    present = [k for k in ("TAVILY_API_KEY", "NEWSAPI_API_KEY") if os.getenv(k)]
    if not present:
        return (
            WARN,
            "neither TAVILY_API_KEY nor NEWSAPI_API_KEY is set, so the "
            "forecaster runs blind and cannot know what the market knows",
        )
    return OK, f"research sources configured: {', '.join(present)}"


def _check_gamma() -> Tuple[str, str]:
    import httpx

    res = httpx.get(
        "https://gamma-api.polymarket.com/markets",
        params={"limit": 5, "closed": "false"},
        timeout=30,
    )
    res.raise_for_status()
    markets = res.json()
    if not isinstance(markets, list) or not markets:
        return FAIL, f"expected a non-empty list of markets, got {type(markets)}"

    missing = sorted(
        {f for market in markets for f in REQUIRED_MARKET_FIELDS if f not in market}
    )
    if missing:
        return (
            FAIL,
            f"API no longer returns: {', '.join(missing)} - map_api_to_market "
            f"will raise KeyError. This is the field drift to fix first.",
        )
    return OK, f"{len(markets)} markets fetched, all required fields present"


def _check_market_parsing() -> Tuple[str, str]:
    """Parse a real market through the real code path."""
    import httpx

    from agents.polymarket.polymarket import Polymarket
    from agents.utils.objects import SimpleMarket

    res = httpx.get(
        "https://gamma-api.polymarket.com/markets",
        params={"limit": 5, "closed": "false"},
        timeout=30,
    )
    res.raise_for_status()

    parsed = 0
    for market in res.json():
        data = Polymarket.map_api_to_market(market)
        SimpleMarket(**data)
        parsed += 1
    return OK, f"{parsed} live markets parsed into SimpleMarket"


def _check_clob_and_orderbook() -> Tuple[str, str]:
    from agents.polymarket.polymarket import Polymarket

    poly = Polymarket()
    markets = poly.get_sampling_simplified_markets()
    if not markets:
        return WARN, "CLOB answered but returned no sampling markets"

    token_id = None
    for market in markets:
        if market and market.get("clob_token_ids"):
            import ast

            token_id = str(ast.literal_eval(market["clob_token_ids"])[0])
            break
    if token_id is None:
        return WARN, "no token id found in sampling markets"

    quote = poly.quote_buy(token_id, poly.MIN_ORDER_USDC)
    return (
        OK,
        f"orderbook priced: best ask {quote['best_ask']:.3f}, "
        f"vwap {quote['vwap']:.3f} for {poly.MIN_ORDER_USDC:.0f} USDC",
    )


def _check_wallet() -> Tuple[str, str]:
    from agents.polymarket.polymarket import Polymarket

    poly = Polymarket()
    address = poly.get_address_for_private_key()
    funder = getattr(poly, "funder", None)
    balance = poly.get_usdc_balance()

    where = f"proxy wallet {funder}" if funder else f"EOA {address}"
    if balance <= 0:
        detail = f"balance is 0.00 USDC on {where}."
        if not funder:
            detail += (
                " If you deposited through polymarket.com the funds are in a "
                "proxy wallet: set POLYMARKET_SIGNATURE_TYPE and "
                "POLYMARKET_FUNDER."
            )
        return WARN, detail
    return OK, f"{balance:.2f} USDC on {where}"


def _check_tavily() -> Tuple[str, str]:
    from agents.connectors.search import Search

    search = Search()
    if not search.available:
        return WARN, "TAVILY_API_KEY not set"
    context = search.get_context("prediction market news", max_results=1)
    if not context or not str(context).strip():
        return WARN, "Tavily answered but returned no context"
    return OK, f"Tavily answered ({len(str(context))} chars of context)"


def _check_newsapi() -> Tuple[str, str]:
    from agents.connectors.news import News

    news = News()
    if not news.available:
        return WARN, "NEWSAPI_API_KEY not set"
    articles = news.get_articles_for_question("election", max_articles=1)
    if not articles:
        return WARN, "NewsAPI answered but returned no articles"
    return OK, f"NewsAPI answered ({len(articles)} article(s))"


def _check_safety() -> Tuple[str, str]:
    dry = (os.getenv("POLYMARKET_DRY_RUN") or "true").strip().lower()
    live = dry in ("0", "false", "no", "off")
    limits = (
        f"max order {os.getenv('POLYMARKET_MAX_ORDER_USDC', '5')} USDC, "
        f"max daily {os.getenv('POLYMARKET_MAX_DAILY_USDC', '20')} USDC, "
        f"min edge {os.getenv('POLYMARKET_MIN_EDGE', '0.10')}"
    )
    if live:
        return WARN, f"LIVE TRADING IS ON - orders will be sent. {limits}"
    return OK, f"dry run (no orders sent); {limits}"


CHECKS: List[Tuple[str, Callable[[], Tuple[str, str]]]] = [
    ("python", _check_python),
    ("required env", _check_required_env),
    ("research env", _check_research_env),
    ("gamma api fields", _check_gamma),
    ("market parsing", _check_market_parsing),
    ("clob + orderbook", _check_clob_and_orderbook),
    ("wallet balance", _check_wallet),
    ("tavily", _check_tavily),
    ("newsapi", _check_newsapi),
    ("safety settings", _check_safety),
]

SYMBOL = {OK: "PASS", WARN: "WARN", FAIL: "FAIL"}


def run_checks(checks=None) -> List[Tuple[str, str, str]]:
    """Run every check, returning (name, status, detail). Never raises."""
    results = []
    for name, check in checks or CHECKS:
        try:
            status, detail = check()
        except Exception as e:
            status, detail = FAIL, f"{type(e).__name__}: {e}"
        results.append((name, status, detail))
    return results


def format_results(results) -> str:
    lines = []
    for name, status, detail in results:
        lines.append(f"  [{SYMBOL[status]}] {name:18} {detail}")

    failed = [r for r in results if r[1] == FAIL]
    warned = [r for r in results if r[1] == WARN]
    lines.append("")
    if failed:
        lines.append(
            f"  {len(failed)} check(s) FAILED. The agent will not work until "
            f"these are fixed."
        )
    elif warned:
        lines.append(
            f"  No failures, {len(warned)} warning(s). Read them: a blind "
            f"forecaster or a zero balance means no trade will happen."
        )
    else:
        lines.append(
            "  All checks passed. That means the plumbing works - it does NOT "
            "mean the strategy is profitable. Run dry across a few dozen "
            "markets and read `journal-report` before going live."
        )
    return "\n".join(lines)
