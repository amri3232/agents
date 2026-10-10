"""Tests for the order path: forecast parsing, edge, sizing, slippage.

These cover the defects recorded in PRE-FLIGHT-AUDIT.md. Nothing here touches
the network or a wallet.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from agents.application.executor import Executor
from agents.application.journal import Journal
from agents.application.strategy import (
    evaluate_candidate,
    expected_value_per_dollar,
    kelly_fraction,
    select_best,
)
from agents.polymarket.polymarket import Polymarket
from agents.utils.objects import Candidate, MarketForecast


def market_doc(
    outcomes='["Yes", "No"]',
    token_ids='["tok_yes", "tok_no"]',
    question="Will it happen?",
):
    """Mimic the (Document, score) tuple the chroma filter returns."""
    doc = MagicMock()
    doc.dict.return_value = {
        "page_content": "a description",
        "metadata": {
            "outcomes": outcomes,
            "clob_token_ids": token_ids,
            "outcome_prices": '["0.4", "0.6"]',
            "question": question,
        },
    }
    return (doc, 0.1)


def candidate(**kw):
    base = dict(
        token_id="tok_yes",
        outcome="Yes",
        market_question="Q?",
        probability=0.7,
        ask_price=0.5,
        edge=0.2,
        expected_value=0.4,
        full_kelly=0.4,
        size_fraction=0.05,
        available_notional=100.0,
    )
    base.update(kw)
    return Candidate(**base)


class TestParseForecast(unittest.TestCase):
    outcomes = ["Yes", "No"]

    def parse(self, content):
        return Executor.parse_forecast(content, self.outcomes)

    def test_plain_json(self):
        f = self.parse('{"outcome":"Yes","probability":0.72}')
        self.assertEqual(f.outcome, "Yes")
        self.assertAlmostEqual(f.probability, 0.72)

    def test_json_in_code_fence(self):
        f = self.parse('```json\n{"outcome":"No","probability":0.8}\n```')
        self.assertEqual(f.outcome, "No")

    def test_json_with_prose_preamble(self):
        """The old regex shifted onto `price` here and tripled the stake."""
        f = self.parse(
            'After analysis: {"outcome":"Yes","probability":0.9,"reasoning":"x"}'
        )
        self.assertAlmostEqual(f.probability, 0.9)

    def test_rejects_unknown_outcome(self):
        with self.assertRaisesRegex(ValueError, "not one of the market outcomes"):
            self.parse('{"outcome":"Maybe","probability":0.7}')

    def test_rejects_probability_outside_unit_interval(self):
        with self.assertRaises(ValueError):
            self.parse('{"outcome":"Yes","probability":1.4}')

    def test_rejects_non_json(self):
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            self.parse("price:0.5,size:0.1,side:BUY,")

    def test_rejects_missing_probability(self):
        with self.assertRaises(ValueError):
            self.parse('{"outcome":"Yes"}')


class TestStrategyMath(unittest.TestCase):
    def test_expected_value_is_zero_at_fair_price(self):
        self.assertAlmostEqual(expected_value_per_dollar(0.5, 0.5), 0.0)

    def test_expected_value_positive_when_underpriced(self):
        # 60% chance bought at 0.50 pays 1.2x per dollar
        self.assertAlmostEqual(expected_value_per_dollar(0.6, 0.5), 0.2)

    def test_expected_value_negative_when_overpriced(self):
        self.assertLess(expected_value_per_dollar(0.4, 0.5), 0)

    def test_kelly_zero_without_edge(self):
        self.assertEqual(kelly_fraction(0.5, 0.5), 0.0)
        self.assertEqual(kelly_fraction(0.4, 0.5), 0.0)

    def test_kelly_matches_closed_form(self):
        # (p - price) / (1 - price)
        self.assertAlmostEqual(kelly_fraction(0.7, 0.5), 0.4)
        self.assertAlmostEqual(kelly_fraction(0.9, 0.5), 0.8)

    def test_kelly_rejects_impossible_price(self):
        for bad in (0.0, 1.0, -0.1, 1.2):
            with self.assertRaises(ValueError):
                kelly_fraction(0.7, bad)

    def test_select_best_ranks_by_expected_value(self):
        # Same absolute edge, but the cheaper outcome pays far more per dollar.
        cheap = candidate(
            ask_price=0.10,
            probability=0.20,
            edge=0.10,
            expected_value=expected_value_per_dollar(0.20, 0.10),
        )
        pricey = candidate(
            ask_price=0.80,
            probability=0.90,
            edge=0.10,
            expected_value=expected_value_per_dollar(0.90, 0.80),
        )
        self.assertIs(select_best([pricey, cheap]), cheap)

    def test_select_best_of_empty_is_none(self):
        self.assertIsNone(select_best([]))


class TestEvaluateCandidate(unittest.TestCase):
    def evaluate(self, probability, ask_price, min_edge=0.10, max_fraction=0.05):
        return evaluate_candidate(
            forecast=MarketForecast(outcome="Yes", probability=probability),
            token_id="tok_yes",
            ask_price=ask_price,
            available_notional=100.0,
            market_question="Q?",
            min_edge=min_edge,
            kelly_multiplier=0.25,
            max_fraction=max_fraction,
        )

    def test_accepts_a_real_edge(self):
        c = self.evaluate(0.70, 0.50)
        self.assertIsNotNone(c)
        self.assertAlmostEqual(c.edge, 0.20)
        self.assertAlmostEqual(c.full_kelly, 0.4)
        # quarter Kelly is 0.10, but max_fraction caps at 0.05
        self.assertAlmostEqual(c.size_fraction, 0.05)

    def test_quarter_kelly_applies_below_the_cap(self):
        c = self.evaluate(0.62, 0.50, max_fraction=1.0)
        self.assertAlmostEqual(c.full_kelly, 0.24)
        self.assertAlmostEqual(c.size_fraction, 0.06)

    def test_edge_exactly_at_threshold_is_accepted(self):
        """0.60 - 0.50 is 0.0999... in binary; the threshold tolerates that."""
        self.assertIsNotNone(self.evaluate(0.60, 0.50, min_edge=0.10))

    def test_rejects_edge_below_threshold(self):
        self.assertIsNone(self.evaluate(0.55, 0.50))

    def test_rejects_negative_edge(self):
        self.assertIsNone(self.evaluate(0.40, 0.50))

    def test_rejects_the_tails(self):
        """Extreme prices are where costs swamp any modelled edge."""
        self.assertIsNone(self.evaluate(0.99, 0.97))
        self.assertIsNone(self.evaluate(0.20, 0.02))

    def test_expected_value_is_populated(self):
        c = self.evaluate(0.70, 0.50)
        self.assertAlmostEqual(c.expected_value, 0.4)


class TestBuildOrderPlan(unittest.TestCase):
    def executor(self):
        ex = Executor.__new__(Executor)  # skip __init__: no API keys needed
        ex.polymarket = MagicMock()
        return ex

    def test_buys_the_outcome_the_forecast_chose(self):
        """The old code hardcoded index [1] and always bought 'No'."""
        plan = self.executor().build_order_plan(
            candidate(token_id="tok_yes", outcome="Yes"), max_order_usdc=100
        )
        self.assertEqual(plan.token_id, "tok_yes")
        self.assertEqual(plan.outcome, "Yes")
        self.assertEqual(plan.side, "BUY")

    def test_size_fraction_applies_to_bankroll(self):
        plan = self.executor().build_order_plan(
            candidate(size_fraction=0.1, available_notional=250.0), max_order_usdc=100
        )
        self.assertAlmostEqual(plan.amount_usdc, 25.0)

    def test_max_order_caps_the_stake(self):
        plan = self.executor().build_order_plan(
            candidate(size_fraction=0.9, available_notional=1000.0), max_order_usdc=5
        )
        self.assertAlmostEqual(plan.amount_usdc, 5.0)

    def test_limit_never_exceeds_own_probability(self):
        """Paying above your own probability is negative-EV by construction."""
        plan = self.executor().build_order_plan(
            candidate(ask_price=0.70, probability=0.705), max_order_usdc=100
        )
        self.assertLessEqual(plan.limit_price, 0.705)

    def test_limit_allows_small_slippage_headroom(self):
        plan = self.executor().build_order_plan(
            candidate(ask_price=0.50, probability=0.90), max_order_usdc=100
        )
        self.assertGreater(plan.limit_price, 0.50)
        self.assertLess(plan.limit_price, 0.55)


def book(levels):
    b = MagicMock()
    b.asks = [MagicMock(price=str(p), size=str(s)) for p, s in levels]
    return b


class TestQuoteAndSlippage(unittest.TestCase):
    def poly(self, levels):
        p = Polymarket.__new__(Polymarket)
        p.get_orderbook = MagicMock(return_value=book(levels))
        return p

    def test_vwap_and_worst_price_across_levels(self):
        q = self.poly([(0.50, 20), (0.60, 1000)]).quote_buy("tok", 20.0)
        self.assertAlmostEqual(q["best_ask"], 0.50)
        self.assertAlmostEqual(q["worst_price"], 0.60)
        self.assertLess(q["vwap"], 0.60)
        self.assertGreater(q["vwap"], 0.50)

    def test_book_order_does_not_matter(self):
        """Asks are sorted here, so a worst-first book gives the same quote."""
        asc = self.poly([(0.50, 20), (0.60, 1000)]).quote_buy("tok", 20.0)
        desc = self.poly([(0.60, 1000), (0.50, 20)]).quote_buy("tok", 20.0)
        self.assertAlmostEqual(asc["worst_price"], desc["worst_price"])
        self.assertAlmostEqual(asc["vwap"], desc["vwap"])

    def test_zero_priced_level_is_ignored(self):
        q = self.poly([(0.0, 100), (0.50, 1000)]).quote_buy("tok", 10.0)
        self.assertAlmostEqual(q["best_ask"], 0.50)

    def test_thin_book_is_refused_not_filled_at_any_price(self):
        with self.assertRaisesRegex(RuntimeError, "insufficient liquidity"):
            self.poly([(0.50, 2)]).quote_buy("tok", 50.0)

    def test_empty_book_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "no asks"):
            self.poly([]).quote_buy("tok", 10.0)

    def test_order_aborts_when_fill_would_exceed_limit(self):
        """The audited case: best ask 0.50, then a wall at 0.95."""
        p = self.poly([(0.50, 20), (0.95, 5000)])
        with self.assertRaisesRegex(RuntimeError, "above the"):
            p.execute_market_order("tok", 50.0, max_price=0.60, dry_run=True)

    def test_dry_run_sends_nothing(self):
        p = self.poly([(0.50, 1000)])
        p.client = MagicMock()
        self.assertEqual(
            p.execute_market_order("tok", 10.0, max_price=0.60, dry_run=True),
            "dry-run",
        )
        p.client.post_order.assert_not_called()

    def test_below_minimum_order_is_refused(self):
        with self.assertRaisesRegex(ValueError, "below the"):
            self.poly([(0.50, 1000)]).execute_market_order(
                "tok", 0.50, max_price=0.60, dry_run=True
            )

    def test_invalid_max_price_is_refused(self):
        with self.assertRaisesRegex(ValueError, "max_price must be"):
            self.poly([(0.50, 1000)]).execute_market_order(
                "tok", 10.0, max_price=1.5, dry_run=True
            )


class TestMarketTradeable(unittest.TestCase):
    def test_closed_market_is_not_tradeable(self):
        self.assertFalse(
            Polymarket.is_market_tradeable({"active": True, "closed": True})
        )

    def test_archived_market_is_not_tradeable(self):
        self.assertFalse(
            Polymarket.is_market_tradeable({"active": True, "archived": True})
        )

    def test_past_end_date_is_not_tradeable(self):
        self.assertFalse(
            Polymarket.is_market_tradeable(
                {"active": True, "end": "2020-01-01T00:00:00Z"}
            )
        )

    def test_open_market_is_tradeable(self):
        self.assertTrue(
            Polymarket.is_market_tradeable(
                {"active": True, "end": "2099-01-01T00:00:00Z"}
            )
        )

    def test_inactive_market_is_not_tradeable(self):
        self.assertFalse(Polymarket.is_market_tradeable({"active": False}))


class TestJournal(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.unlink(self.path)
        self.journal = Journal(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def rows(self):
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]

    def test_candidate_is_recorded_with_selection_flag(self):
        self.journal.record_candidate(candidate(), selected=True)
        self.journal.record_candidate(candidate(outcome="No"), selected=False)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["selected"])
        self.assertFalse(rows[1]["selected"])
        self.assertIn("ts", rows[0])

    def test_dry_run_orders_do_not_count_toward_the_daily_cap(self):
        plan = MagicMock()
        plan.model_dump.return_value = {"amount_usdc": 5.0}
        self.journal.record_order(plan, "dry-run", dry_run=True)
        self.assertEqual(self.journal.spent_today(), 0.0)

    def test_live_orders_count_toward_the_daily_cap(self):
        plan = MagicMock()
        plan.model_dump.return_value = {"amount_usdc": 5.0}
        self.journal.record_order(plan, "ok", dry_run=False)
        self.journal.record_order(plan, "ok", dry_run=False)
        self.assertAlmostEqual(self.journal.spent_today(), 10.0)

    def test_missing_journal_reports_zero_spend(self):
        self.assertEqual(Journal(self.path + ".nope").spent_today(), 0.0)


if __name__ == "__main__":
    unittest.main()
