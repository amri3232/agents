from agents.application.automation import (
    AutomatedTrader,
    NoTradeFound,
    TradeProposal,
)
from agents.application.executor import Executor as Agent
from agents.polymarket.gamma import GammaMarketClient as Gamma
from agents.polymarket.polymarket import Polymarket

import ast
import shutil


class Trader:
    def __init__(self):
        self.polymarket = Polymarket()
        self.gamma = Gamma()
        self.agent = Agent()

    def pre_trade_logic(self) -> None:
        self.clear_local_dbs()

    def clear_local_dbs(self) -> None:
        try:
            shutil.rmtree("local_db_events")
        except:
            pass
        try:
            shutil.rmtree("local_db_markets")
        except:
            pass

    def find_best_trade(self) -> TradeProposal:
        """

        find_best_trade evaluates all events, markets, and orderbooks

        leverages all available information sources accessible to the autonomous agent

        and returns the trade the agent would make, without placing it

        """
        self.pre_trade_logic()

        events = self.polymarket.get_all_tradeable_events()
        print(f"1. FOUND {len(events)} EVENTS")

        filtered_events = self.agent.filter_events_with_rag(events)
        print(f"2. FILTERED {len(filtered_events)} EVENTS")

        markets = self.agent.map_filtered_events_to_markets(filtered_events)
        print()
        print(f"3. FOUND {len(markets)} MARKETS")

        print()
        filtered_markets = self.agent.filter_markets(markets)
        print(f"4. FILTERED {len(filtered_markets)} MARKETS")
        if not filtered_markets:
            raise NoTradeFound("no market passed the filters")

        market = filtered_markets[0]
        best_trade = self.agent.source_best_trade(market)
        print(f"5. CALCULATED TRADE {best_trade}")

        metadata = market[0].dict()["metadata"]
        return TradeProposal(
            question=metadata["question"],
            outcomes=ast.literal_eval(metadata["outcomes"]),
            outcome_prices=[
                float(p) for p in ast.literal_eval(metadata["outcome_prices"])
            ],
            raw_trade=best_trade,
            market=market,
        )

    def get_balance(self) -> float:
        return self.polymarket.get_usdc_balance()

    def execute(self, proposal: TradeProposal, amount: float, outcome_index: int):
        # Please refer to TOS before trading: polymarket.com/tos
        return self.polymarket.execute_market_order(
            proposal.market, amount, outcome_index
        )

    def one_best_trade(self, live: bool = False) -> dict:
        """

        one_best_trade is a strategy that evaluates all events, markets, and orderbooks

        leverages all available information sources accessible to the autonomous agent

        then, in live mode only, executes that trade without any human intervention.
        Live mode additionally requires ALLOW_LIVE_TRADING=true, see automation.py

        """
        return AutomatedTrader(self, live=live).run_once()

    def maintain_positions(self):
        pass

    def incentive_farm(self):
        pass


if __name__ == "__main__":
    t = Trader()
    t.one_best_trade()
