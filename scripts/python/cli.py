import typer
from devtools import pprint

from agents.polymarket.polymarket import Polymarket
from agents.connectors.chroma import PolymarketRAG
from agents.connectors.news import News
from agents.application.trade import Trader
from agents.application.executor import Executor
from agents.application.creator import Creator

app = typer.Typer()

# Built on first use, not at import. Constructing Polymarket() derives CLOB
# API credentials and therefore needs a private key, so doing it at module
# level meant every command failed on import when the key was missing -
# including `check-setup`, whose job is to report that.
_clients = {}


def _get(name, factory):
    if name not in _clients:
        _clients[name] = factory()
    return _clients[name]


def polymarket() -> Polymarket:
    return _get("polymarket", Polymarket)


def newsapi_client() -> News:
    return _get("news", News)


def polymarket_rag() -> PolymarketRAG:
    return _get("rag", PolymarketRAG)


@app.command()
def get_all_markets(limit: int = 5, sort_by: str = "spread") -> None:
    """
    Query Polymarket's markets
    """
    print(f"limit: int = {limit}, sort_by: str = {sort_by}")
    markets = polymarket().get_all_markets()
    markets = polymarket().filter_markets_for_trading(markets)
    if sort_by == "spread":
        markets = sorted(markets, key=lambda x: x.spread, reverse=True)
    markets = markets[:limit]
    pprint(markets)


@app.command()
def get_relevant_news(keywords: str) -> None:
    """
    Use NewsAPI to query the internet
    """
    articles = newsapi_client().get_articles_for_cli_keywords(keywords)
    pprint(articles)


@app.command()
def get_all_events(limit: int = 5, sort_by: str = "number_of_markets") -> None:
    """
    Query Polymarket's events
    """
    print(f"limit: int = {limit}, sort_by: str = {sort_by}")
    events = polymarket().get_all_events()
    events = polymarket().filter_events_for_trading(events)
    if sort_by == "number_of_markets":
        events = sorted(events, key=lambda x: len(x.markets), reverse=True)
    events = events[:limit]
    pprint(events)


@app.command()
def create_local_markets_rag(local_directory: str) -> None:
    """
    Create a local markets database for RAG
    """
    polymarket_rag().create_local_markets_rag(local_directory=local_directory)


@app.command()
def query_local_markets_rag(vector_db_directory: str, query: str) -> None:
    """
    RAG over a local database of Polymarket's events
    """
    response = polymarket_rag().query_local_markets_rag(
        local_directory=vector_db_directory, query=query
    )
    pprint(response)


@app.command()
def ask_superforecaster(event_title: str, market_question: str, outcome: str) -> None:
    """
    Ask a superforecaster about a trade
    """
    print(
        f"event: str = {event_title}, question: str = {market_question}, outcome (usually yes or no): str = {outcome}"
    )
    executor = Executor()
    response = executor.get_superforecast(
        event_title=event_title, market_question=market_question, outcome=outcome
    )
    print(f"Response:{response}")


@app.command()
def create_market() -> None:
    """
    Format a request to create a market on Polymarket
    """
    c = Creator()
    market_description = c.one_best_market()
    print(f"market_description: str = {market_description}")


@app.command()
def ask_llm(user_input: str) -> None:
    """
    Ask a question to the LLM and get a response.
    """
    executor = Executor()
    response = executor.get_llm_response(user_input)
    print(f"LLM Response: {response}")


@app.command()
def ask_polymarket_llm(user_input: str) -> None:
    """
    What types of markets do you want trade?
    """
    executor = Executor()
    response = executor.get_polymarket_llm(user_input=user_input)
    print(f"LLM + current markets&events response: {response}")


@app.command()
def run_autonomous_trader() -> None:
    """
    Let an autonomous system trade for you.
    """
    trader = Trader()
    trader.one_best_trade()


@app.command()
def set_approvals() -> None:
    """
    Set the on-chain approvals the exchanges need (one-off, costs MATIC gas).

    A fresh wallet has no USDC allowance and no CTF approval, so its orders
    cannot settle. Run this once before enabling live trading.
    """
    polymarket().set_approvals()


@app.command()
def journal_report(path: str = "trade_journal.jsonl") -> None:
    """
    Summarise the decision journal: calibration and committed notional.

    Run the agent in dry-run mode across a few dozen markets first, wait for
    them to resolve, then use this to see whether the forecasts were actually
    calibrated before putting money behind them.
    """
    from agents.application.report import summarise

    print(summarise(path))


@app.command()
def check_setup() -> None:
    """
    Check every external dependency before risking money.

    Verifies the Gamma API still returns the fields this code reads, that the
    CLOB and orderbook answer, that the wallet resolves to a balance, and that
    the research sources work. Run this first on the machine that will trade.
    """
    from agents.application.preflight import format_results, run_checks

    print(format_results(run_checks()))


if __name__ == "__main__":
    app()
