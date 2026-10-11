import os
import shutil
import time

from dotenv import load_dotenv

from agents.application.executor import Executor as Agent
from agents.application.journal import Journal
from agents.application.strategy import select_best
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
        self.journal = Journal()

        # Live trading is opt-in. Without this the run forecasts, scores,
        # prices and journals the order, and sends nothing.
        self.dry_run = _env_flag("POLYMARKET_DRY_RUN", True)
        self.max_order_usdc = float(os.getenv("POLYMARKET_MAX_ORDER_USDC", "5"))
        self.max_daily_usdc = float(os.getenv("POLYMARKET_MAX_DAILY_USDC", "20"))

        # Minimum probability edge over the live ask before a trade is worth
        # making. The spread and the agent's own forecast error both eat into
        # this, so a small number here is not a small risk.
        self.min_edge = float(os.getenv("POLYMARKET_MIN_EDGE", "0.10"))
        # Fraction of full Kelly. Full Kelly assumes the probability is
        # correct; it is not, so stake well below it.
        self.kelly_multiplier = float(os.getenv("POLYMARKET_KELLY_FRACTION", "0.25"))
        self.max_fraction = float(os.getenv("POLYMARKET_MAX_BANKROLL_FRACTION", "0.05"))
        # How many markets to forecast per run. Each one costs an LLM call.
        self.max_candidates = int(os.getenv("POLYMARKET_MAX_CANDIDATES", "5"))

    def pre_trade_logic(self) -> None:
        self.clear_local_dbs()

    def clear_local_dbs(self) -> None:
        for directory in ("local_db_events", "local_db_markets"):
            shutil.rmtree(directory, ignore_errors=True)

    def one_best_trade(self) -> None:
        """Forecast several markets, score them by edge, trade the best one.

        Set POLYMARKET_DRY_RUN=false to actually send the order. Read
        PRE-FLIGHT-AUDIT.md first. Edge is measured against the agent's own
        probability, so it is only as good as that probability; run in dry-run
        mode and check the journal for realized calibration before funding
        this.
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

    def _check_daily_cap(self, amount: float) -> None:
        if self.dry_run:
            return
        spent = self.journal.spent_today()
        if spent + amount > self.max_daily_usdc:
            raise RuntimeError(
                f"daily cap reached: {spent:.2f} USDC already committed today, "
                f"this order would add {amount:.2f}, cap is "
                f"{self.max_daily_usdc:.2f}"
            )

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

        # The vector search is a cheap way to narrow the field, nothing more:
        # similarity to a generic prompt is not a measure of edge. The ranking
        # that decides the trade happens below, on computed expected value.
        shortlist = self.agent.filter_markets(tradeable)[: self.max_candidates]
        print(f"5. SHORTLISTED {len(shortlist)} MARKETS FOR FORECASTING")
        if not shortlist:
            raise RuntimeError("market filter returned nothing")

        bankroll = self.polymarket.get_usdc_balance()
        if bankroll <= 0:
            raise RuntimeError(
                "USDC balance is 0 for the key's own address. If funds were "
                "deposited through polymarket.com they sit in a proxy wallet; "
                "set POLYMARKET_SIGNATURE_TYPE and POLYMARKET_FUNDER."
            )
        print(f"6. BANKROLL {bankroll:.2f} USDC")

        candidates = []
        for market in shortlist:
            question = market[0].dict()["metadata"].get("question", "?")
            try:
                # Both sides of the market are scored against one forecast.
                scored = self.agent.score_market(
                    market,
                    available_notional=bankroll,
                    min_edge=self.min_edge,
                    kelly_multiplier=self.kelly_multiplier,
                    max_fraction=self.max_fraction,
                )
            except Exception as e:
                # One unscoreable market must not abort the run.
                print(f"   skipped {question!r}: {e}")
                self.journal.record_skip(question, str(e))
                continue
            if not scored:
                print(f"   no edge on either side: {question!r}")
                self.journal.record_skip(question, "below min_edge or out of band")
                continue
            for candidate in scored:
                print(
                    f"   candidate {candidate.outcome!r} @ "
                    f"{candidate.ask_price:.3f} p={candidate.probability:.3f} "
                    f"edge={candidate.edge:+.3f} ev={candidate.expected_value:+.3f}"
                )
            candidates.extend(scored)

        print(f"7. {len(candidates)} CANDIDATES WITH EDGE")
        best = select_best(candidates)
        for candidate in candidates:
            self.journal.record_candidate(candidate, selected=candidate is best)
        if best is None:
            print("No market cleared the edge threshold. Nothing traded.")
            return

        plan = self.agent.build_order_plan(best, self.max_order_usdc)
        print(f"8. ORDER PLAN {plan.model_dump()}")

        self._check_daily_cap(plan.amount_usdc)

        # Please refer to TOS before enabling live trading: polymarket.com/tos
        result = self.polymarket.execute_market_order(
            token_id=plan.token_id,
            amount_usdc=plan.amount_usdc,
            max_price=plan.limit_price,
            dry_run=self.dry_run,
        )
        self.journal.record_order(plan, result, self.dry_run)
        print(f"9. RESULT {result}")

    def maintain_positions(self):
        pass

    def incentive_farm(self):
        pass


if __name__ == "__main__":
    t = Trader()
    t.one_best_trade()
