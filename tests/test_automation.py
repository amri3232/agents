import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from agents.application.automation import (
    AutomatedTrader,
    LiveTradingNotEnabled,
    NoTradeFound,
    RiskConfig,
    TradeDecision,
    TradeLedger,
    TradeParseError,
    TradeProposal,
    TradeRejected,
    parse_trade,
)


class ParseTradeTest(unittest.TestCase):
    def test_example_from_prompt(self):
        d = parse_trade(
            "RESPONSE```\n price:0.5,\n size:0.1,\n side:BUY,\n outcome:Yes,\n```"
        )
        self.assertEqual((d.price, d.size, d.side, d.outcome), (0.5, 0.1, "BUY", "Yes"))

    def test_quotes_case_and_whole_numbers(self):
        d = parse_trade("Price: '0.62', Size: '1', SIDE: 'buy', Outcome: 'No'")
        self.assertEqual((d.price, d.size, d.side, d.outcome), (0.62, 1.0, "BUY", "No"))

    def test_leading_dot_and_percent(self):
        self.assertEqual(parse_trade("size:.25, side:BUY").size, 0.25)
        self.assertEqual(parse_trade("size: 10%, side:BUY").size, 0.1)

    def test_price_and_outcome_are_optional(self):
        d = parse_trade("size:0.1, side:SELL")
        self.assertIsNone(d.price)
        self.assertIsNone(d.outcome)

    def test_rejects_unusable_answers(self):
        for text in [
            "size:'percentage_of_total_funds', side:BUY",
            "price:0.5, side:BUY",
            "size:0.1",
            "size:0, side:BUY",
            "size:1.5, side:BUY",
            "size:0.1, side:HOLD",
            "price:1.2, size:0.1, side:BUY",
            "",
        ]:
            with self.subTest(text=text):
                with self.assertRaises(TradeParseError):
                    parse_trade(text)


class RiskConfigTest(unittest.TestCase):
    def setUp(self):
        self.risk = RiskConfig(
            max_order_usdc=5, max_daily_usdc=20, max_size_fraction=0.1, min_order_usdc=1
        )

    def buy(self, size):
        return TradeDecision(size=size, side="BUY")

    def test_caps_fraction_then_order_size(self):
        # 100% asked, capped to 10% of 30 = 3
        self.assertEqual(self.risk.size_order(self.buy(1.0), 30, 0), 3.0)
        # 10% of 1000 = 100, capped to the 5 USDC order limit
        self.assertEqual(self.risk.size_order(self.buy(0.1), 1000, 0), 5.0)

    def test_daily_limit_shrinks_then_blocks(self):
        self.assertEqual(self.risk.size_order(self.buy(0.1), 1000, 17), 3.0)
        with self.assertRaises(TradeRejected):
            self.risk.size_order(self.buy(0.1), 1000, 19.5)

    def test_rounds_down_to_cents(self):
        self.assertEqual(self.risk.size_order(self.buy(0.1), 33.339, 0), 3.33)

    def test_below_minimum_and_sells_are_rejected(self):
        with self.assertRaises(TradeRejected):
            self.risk.size_order(self.buy(0.1), 5, 0)  # 0.50 USDC
        with self.assertRaises(TradeRejected):
            self.risk.size_order(TradeDecision(size=0.1, side="SELL"), 1000, 0)

    def test_from_env(self):
        risk = RiskConfig.from_env(
            {
                "ALLOW_LIVE_TRADING": "True",
                "MAX_ORDER_USDC": "2.5",
                "MAX_DAILY_USDC": "",
            }
        )
        self.assertTrue(risk.allow_live)
        self.assertEqual(risk.max_order_usdc, 2.5)
        self.assertEqual(risk.max_daily_usdc, RiskConfig().max_daily_usdc)
        self.assertFalse(RiskConfig.from_env({}).allow_live)
        with self.assertRaises(ValueError):
            RiskConfig.from_env({"MAX_ORDER_USDC": "lots"})


class TradeLedgerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ledger = TradeLedger(os.path.join(self.dir.name, "log.jsonl"))

    def test_empty_ledger(self):
        self.assertEqual(self.ledger.read(), [])
        self.assertEqual(self.ledger.spent_today(), 0)

    def test_spent_today_counts_only_live_money_from_today(self):
        now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
        stamp = "2026-10-17T08:00:00+00:00"
        for mode, status, amount, ts in [
            ("live", "executed", 3, stamp),
            ("live", "unconfirmed", 2, stamp),
            ("live", "exchange_rejected", 9, stamp),
            ("dry_run", "dry_run", 9, stamp),
            ("live", "executed", 9, "2026-10-16T23:59:00+00:00"),
        ]:
            self.ledger.append(
                {"mode": mode, "status": status, "amount_usdc": amount, "timestamp": ts}
            )
        self.assertEqual(self.ledger.spent_today(now), 5)

    def test_skips_corrupt_lines(self):
        self.ledger.append({"status": "ok"})
        with open(self.ledger.path, "a") as f:
            f.write("{not json\n")
        self.assertEqual(len(self.ledger.read()), 1)


class FakeTrader:
    def __init__(self, proposals=None, balance=100.0, order_response=None):
        self.proposals = list(proposals or [])
        self.balance = balance
        self.order_response = order_response or {"success": True}
        self.find_calls = 0
        self.orders = []

    def find_best_trade(self):
        self.find_calls += 1
        item = self.proposals.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get_balance(self):
        return self.balance

    def execute(self, proposal, amount, outcome_index):
        self.orders.append((amount, outcome_index))
        if isinstance(self.order_response, Exception):
            raise self.order_response
        return self.order_response


def proposal(raw="price:0.4, size:0.5, side:BUY, outcome:No"):
    return TradeProposal(
        question="Will it rain?",
        outcomes=["Yes", "No"],
        outcome_prices=[0.6, 0.4],
        raw_trade=raw,
    )


class AutomatedTraderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ledger = TradeLedger(os.path.join(self.dir.name, "log.jsonl"))
        self.risk = RiskConfig(allow_live=True)
        self.sleeps = []

    def agent(self, trader, live=False, **kwargs):
        return AutomatedTrader(
            trader,
            risk=kwargs.pop("risk", self.risk),
            ledger=self.ledger,
            live=live,
            retry_delay=10,
            sleep=self.sleeps.append,
            **kwargs,
        )

    def test_dry_run_never_places_an_order(self):
        trader = FakeTrader([proposal()])
        result = self.agent(trader).run_once()
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["amount_usdc"], 5.0)
        self.assertEqual(result["outcome"], "No")
        self.assertEqual(trader.orders, [])
        self.assertEqual(self.ledger.read()[0]["status"], "dry_run")
        self.assertEqual(self.ledger.spent_today(), 0)

    def test_live_needs_the_environment_opt_in(self):
        with self.assertRaises(LiveTradingNotEnabled):
            self.agent(FakeTrader(), live=True, risk=RiskConfig(allow_live=False))

    def test_live_places_one_order_on_the_chosen_outcome(self):
        trader = FakeTrader([proposal()])
        result = self.agent(trader, live=True).run_once()
        self.assertEqual(result["status"], "executed")
        self.assertEqual(trader.orders, [(5.0, 1)])  # "No" is index 1
        self.assertEqual(self.ledger.spent_today(), 5.0)

    def test_daily_limit_holds_across_runs(self):
        risk = RiskConfig(allow_live=True, max_order_usdc=5, max_daily_usdc=8)
        trader = FakeTrader([proposal(), proposal(), proposal()])
        agent = self.agent(trader, live=True, risk=risk)
        statuses = [agent.run_once()["status"] for _ in range(3)]
        self.assertEqual(statuses, ["executed", "executed", "rejected"])
        self.assertEqual([o[0] for o in trader.orders], [5.0, 3.0])

    def test_outcome_is_never_guessed(self):
        for raw in [
            "price:0.4, size:0.5, side:BUY",
            "price:0.4, size:0.5, side:BUY, outcome:Maybe",
        ]:
            with self.subTest(raw=raw):
                trader = FakeTrader([proposal(raw)])
                result = self.agent(trader, live=True).run_once()
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(trader.orders, [])

    def test_garbage_answer_is_rejected_and_logged(self):
        trader = FakeTrader([proposal("I think it will rain")])
        result = self.agent(trader, live=True).run_once()
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(trader.orders, [])
        self.assertEqual(len(self.ledger.read()), 1)

    def test_analysis_is_retried_with_backoff_then_succeeds(self):
        trader = FakeTrader([RuntimeError("api"), RuntimeError("api"), proposal()])
        result = self.agent(trader).run_once()
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(trader.find_calls, 3)
        self.assertEqual(self.sleeps, [10, 20])

    def test_retries_are_bounded(self):
        trader = FakeTrader([RuntimeError("api")] * 5)
        result = self.agent(trader, max_attempts=2).run_once()
        self.assertEqual(result["status"], "error")
        self.assertIn("RuntimeError: api", result["detail"])
        self.assertEqual(trader.find_calls, 2)

    def test_no_trade_found_is_not_retried(self):
        trader = FakeTrader([NoTradeFound("nothing")])
        result = self.agent(trader).run_once()
        self.assertEqual(result["status"], "no_trade")
        self.assertEqual(trader.find_calls, 1)

    def test_exchange_rejection_does_not_count_as_spend(self):
        trader = FakeTrader([proposal()], order_response={"success": False})
        result = self.agent(trader, live=True).run_once()
        self.assertEqual(result["status"], "exchange_rejected")
        self.assertEqual(self.ledger.spent_today(), 0)

    def test_failed_order_call_is_not_retried_and_counts_as_spend(self):
        trader = FakeTrader([proposal()], order_response=TimeoutError("slow"))
        result = self.agent(trader, live=True).run_once()
        self.assertEqual(result["status"], "unconfirmed")
        self.assertEqual(len(trader.orders), 1)
        self.assertEqual(self.ledger.spent_today(), 5.0)


class _Doc:
    def __init__(self, metadata):
        self._metadata = metadata

    def dict(self):
        return {"metadata": self._metadata}


class TraderPipelineTest(unittest.TestCase):
    """Trader.find_best_trade with the heavy collaborators stubbed out."""

    def setUp(self):
        stubs = {
            name: mock.MagicMock()
            for name in (
                "agents.application.executor",
                "agents.polymarket.gamma",
                "agents.polymarket.polymarket",
            )
        }
        patcher = mock.patch.dict(sys.modules, stubs)
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop("agents.application.trade", None)
        self.addCleanup(sys.modules.pop, "agents.application.trade", None)
        from agents.application.trade import Trader

        self.trader = Trader()
        self.trader.clear_local_dbs = mock.Mock()

    def test_builds_a_proposal_from_the_top_market(self):
        market = (
            _Doc(
                {
                    "question": "Will it rain?",
                    "outcomes": "['Yes', 'No']",
                    "outcome_prices": "['0.6', '0.4']",
                }
            ),
            0.1,
        )
        self.trader.agent.filter_markets.return_value = [market]
        self.trader.agent.source_best_trade.return_value = "size:0.1, side:BUY"
        p = self.trader.find_best_trade()
        self.assertEqual(p.question, "Will it rain?")
        self.assertEqual(p.outcomes, ["Yes", "No"])
        self.assertEqual(p.outcome_prices, [0.6, 0.4])
        self.assertIs(p.market, market)

    def test_no_markets_means_no_trade_instead_of_a_crash(self):
        self.trader.agent.filter_markets.return_value = []
        with self.assertRaises(NoTradeFound):
            self.trader.find_best_trade()

    def test_execute_forwards_the_outcome_index(self):
        p = proposal()
        p.market = object()
        self.trader.execute(p, 3.0, 1)
        self.trader.polymarket.execute_market_order.assert_called_once_with(
            p.market, 3.0, 1
        )


try:
    import scheduler  # noqa: F401
except ImportError:
    scheduler = None


@unittest.skipIf(scheduler is None, "scheduler is not installed")
class TradingAgentTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(
            sys.modules, {"agents.application.trade": mock.MagicMock()}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop("agents.application.cron", None)
        self.addCleanup(sys.modules.pop, "agents.application.cron", None)
        import agents.application.cron as cron

        self.cron = cron

    def test_registers_one_job_and_defaults_to_dry_run(self):
        agent = self.cron.TradingAgent()
        self.assertEqual(len(agent.schedule.jobs), 1)
        self.assertFalse(agent.automated.live)

    def test_live_still_needs_the_environment_opt_in(self):
        with mock.patch.dict(os.environ, {"ALLOW_LIVE_TRADING": ""}):
            with self.assertRaises(LiveTradingNotEnabled):
                self.cron.TradingAgent(live=True)

    def test_start_runs_immediately_then_keeps_ticking(self):
        agent = self.cron.TradingAgent()
        agent.automated.run_once = mock.Mock()
        with mock.patch.object(
            self.cron.time, "sleep", side_effect=[None, KeyboardInterrupt]
        ):
            with self.assertRaises(KeyboardInterrupt):
                agent.start()
        agent.automated.run_once.assert_called_once()


if __name__ == "__main__":
    unittest.main()
