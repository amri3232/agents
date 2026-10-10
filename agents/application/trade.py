import os
import shutil
import time

from dotenv import load_dotenv

from agents.application.executor import Executor as Agent
from agents.polymarket.gamma import GammaMarketClient as Gamma
from agents.polymarket.polymarket import Polymarket

load_dotenv()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class Trader:
    # Retries are bounded. The previous version called one_best_trade() from
    # inside its own `except` block, so any failure recursed with no limit and
    # no backoff, issuing fresh LLM calls and order attempts until the stack
    # ran out.
    MAX_ATTEMPTS = 3
    BACKOFF_SECONDS = 5

    def __init__(self):
        self.polymarket = Polymarket()
        self.gamma = Gamma()
        self.agent = Agent()
        # Live trading is opt-in. Without this the run prices and validates the
        # order, prints it, and sends nothing.
        self.dry_run = _env_flag("POLYMARKET_DRY_RUN", True)
        self.max_order_usdc = float(os.getenv("POLYMARKET_MAX_ORDER_USDC", "5"))

    def pre_trade_logic(self) -> None:
        self.clear_local_dbs()

    def clear_local_dbs(self) -> None:
        for directory in ("local_db_events", "local_db_markets"):
            shutil.rmtree(directory, ignore_errors=True)

    def one_best_trade(self) -> None:
        """Evaluate events, markets and orderbooks, then place one trade.

        Set POLYMARKET_DRY_RUN=false to actually send the order. Read
        PRE-FLIGHT-AUDIT.md first: market selection is still embedding
        similarity with no profitability assessment, so a validated order is
        not the same as a good one.
        """
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                self._run_once()
                return
            except Exception as e:
                print(f"Attempt {attempt}/{self.MAX_ATTEMPTS} failed: {e!r}")
                if attempt == self.MAX_ATTEMPTS:
                    print("Giving up; no order was placed.")
                    raise
                time.sleep(self.BACKOFF_SECONDS * attempt)

    def _run_once(self) -> None:
        self.pre_trade_logic()

        events = self.polymarket.get_all_tradeable_events()
        print(f"1. FOUND {len(events)} EVENTS")

        filtered_events = self.agent.filter_events_with_rag(events)
        print(f"2. FILTERED {len(filtered_events)} EVENTS")

        markets = self.agent.map_filtered_events_to_markets(filtered_events)
        print(f"3. FOUND {len(markets)} MARKETS")

        # Event-level flags do not say whether an individual market inside the
        # event still trades, so each market is checked on its own.
        tradeable = [m for m in markets if self.polymarket.is_market_tradeable(m)]
        print(f"4. {len(tradeable)} MARKETS STILL TRADEABLE")
        if not tradeable:
            raise RuntimeError("no tradeable markets after filtering")

        filtered_markets = self.agent.filter_markets(tradeable)
        print(f"5. FILTERED {len(filtered_markets)} MARKETS")
        if not filtered_markets:
            raise RuntimeError("market filter returned nothing")

        market = filtered_markets[0]
        decision = self.agent.source_best_trade(market)
        print(f"6. DECISION {decision.model_dump()}")

        plan = self.agent.build_order_plan(market, decision, self.max_order_usdc)
        print(f"7. ORDER PLAN {plan.model_dump()}")

        # Please refer to TOS before enabling live trading: polymarket.com/tos
        result = self.polymarket.execute_market_order(
            token_id=plan.token_id,
            amount_usdc=plan.amount_usdc,
            max_price=plan.limit_price,
            dry_run=self.dry_run,
        )
        print(f"8. RESULT {result}")

    def maintain_positions(self):
        pass

    def incentive_farm(self):
        pass


if __name__ == "__main__":
    t = Trader()
    t.one_best_trade()
