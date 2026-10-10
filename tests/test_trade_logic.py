"""Tests for the order path: decision parsing, token resolution, slippage.

These cover the defects recorded in PRE-FLIGHT-AUDIT.md. Nothing here touches
the network or a wallet.
"""

import unittest
from unittest.mock import MagicMock

from agents.application.executor import Executor
from agents.polymarket.polymarket import Polymarket
from agents.utils.objects import TradeDecision


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


class TestParseTradeDecision(unittest.TestCase):
    outcomes = ["Yes", "No"]

    def parse(self, content):
        return Executor.parse_trade_decision(content, self.outcomes)

    def test_plain_json(self):
        d = self.parse(
            '{"outcome":"Yes","side":"BUY","probability":0.72,'
            '"limit_price":0.61,"size_fraction":0.05}'
        )
        self.assertEqual(d.outcome, "Yes")
        self.assertEqual(d.side, "BUY")
        self.assertAlmostEqual(d.size_fraction, 0.05)

    def test_json_in_code_fence(self):
        d = self.parse(
            '```json\n{"outcome":"No","side":"BUY","probability":0.8,'
            '"limit_price":0.55,"size_fraction":0.1}\n```'
        )
        self.assertEqual(d.outcome, "No")

    def test_json_with_prose_preamble(self):
        """The old regex shifted onto `price` here and tripled the stake."""
        d = self.parse(
            'Based on my analysis: {"outcome":"Yes","side":"BUY",'
            '"probability":0.9,"limit_price":0.62,"size_fraction":0.2}'
        )
        self.assertAlmostEqual(d.size_fraction, 0.2)
        self.assertAlmostEqual(d.limit_price, 0.62)

    def test_rejects_unknown_outcome(self):
        with self.assertRaisesRegex(ValueError, "not one of the market outcomes"):
            self.parse(
                '{"outcome":"Maybe","side":"BUY","probability":0.7,'
                '"limit_price":0.5,"size_fraction":0.1}'
            )

    def test_rejects_sell_instead_of_silently_buying(self):
        with self.assertRaisesRegex(ValueError, "selling is not implemented"):
            self.parse(
                '{"outcome":"Yes","side":"SELL","probability":0.7,'
                '"limit_price":0.5,"size_fraction":0.1}'
            )

    def test_rejects_size_above_full_balance(self):
        with self.assertRaises(ValueError):
            self.parse(
                '{"outcome":"Yes","side":"BUY","probability":0.7,'
                '"limit_price":0.5,"size_fraction":1.5}'
            )

    def test_rejects_trade_with_no_edge(self):
        with self.assertRaisesRegex(ValueError, "no edge"):
            self.parse(
                '{"outcome":"Yes","side":"BUY","probability":0.5,'
                '"limit_price":0.7,"size_fraction":0.1}'
            )

    def test_rejects_non_json(self):
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            self.parse("price:0.5,size:0.1,side:BUY,")

    def test_rejects_price_outside_unit_interval(self):
        with self.assertRaises(ValueError):
            self.parse(
                '{"outcome":"Yes","side":"BUY","probability":0.7,'
                '"limit_price":1.4,"size_fraction":0.1}'
            )


class TestBuildOrderPlan(unittest.TestCase):
    def executor(self):
        ex = Executor.__new__(Executor)  # skip __init__: no API keys needed
        ex.polymarket = MagicMock()
        return ex

    def decision(self, outcome="Yes", size_fraction=0.1):
        return TradeDecision(
            outcome=outcome,
            side="BUY",
            probability=0.8,
            limit_price=0.6,
            size_fraction=size_fraction,
        )

    def test_buys_the_outcome_the_model_chose(self):
        """The old code hardcoded index [1] and always bought 'No'."""
        plan = self.executor().build_order_plan(
            market_doc(), self.decision("Yes"), max_order_usdc=100, usdc_balance=100
        )
        self.assertEqual(plan.token_id, "tok_yes")
        self.assertEqual(plan.outcome, "Yes")

    def test_buys_no_when_no_was_chosen(self):
        plan = self.executor().build_order_plan(
            market_doc(), self.decision("No"), max_order_usdc=100, usdc_balance=100
        )
        self.assertEqual(plan.token_id, "tok_no")

    def test_size_fraction_applies_to_balance(self):
        plan = self.executor().build_order_plan(
            market_doc(), self.decision(size_fraction=0.1), 100, usdc_balance=250
        )
        self.assertAlmostEqual(plan.amount_usdc, 25.0)

    def test_max_order_caps_the_stake(self):
        plan = self.executor().build_order_plan(
            market_doc(), self.decision(size_fraction=0.9), 5, usdc_balance=1000
        )
        self.assertAlmostEqual(plan.amount_usdc, 5.0)

    def test_zero_balance_names_the_proxy_wallet_cause(self):
        with self.assertRaisesRegex(RuntimeError, "proxy wallet"):
            self.executor().build_order_plan(
                market_doc(), self.decision(), 100, usdc_balance=0
            )

    def test_mismatched_token_count_is_refused(self):
        with self.assertRaisesRegex(ValueError, "cannot resolve the token safely"):
            self.executor().build_order_plan(
                market_doc(token_ids='["only_one"]'),
                self.decision(),
                100,
                usdc_balance=100,
            )


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
        # $10 at 0.50 (20 shares), then the rest at 0.60
        q = self.poly([(0.50, 20), (0.60, 1000)]).quote_buy("tok", 20.0)
        self.assertAlmostEqual(q["best_ask"], 0.50)
        self.assertAlmostEqual(q["worst_price"], 0.60)
        self.assertLess(q["vwap"], 0.60)
        self.assertGreater(q["vwap"], 0.50)

    def test_book_order_does_not_matter(self):
        """Asks are sorted here, so a worst-first book gives the same quote."""
        ascending = self.poly([(0.50, 20), (0.60, 1000)]).quote_buy("tok", 20.0)
        descending = self.poly([(0.60, 1000), (0.50, 20)]).quote_buy("tok", 20.0)
        self.assertAlmostEqual(ascending["worst_price"], descending["worst_price"])
        self.assertAlmostEqual(ascending["vwap"], descending["vwap"])

    def test_thin_book_is_refused_not_filled_at_any_price(self):
        with self.assertRaisesRegex(RuntimeError, "insufficient liquidity"):
            self.poly([(0.50, 2)]).quote_buy("tok", 50.0)

    def test_empty_book_is_refused(self):
        p = Polymarket.__new__(Polymarket)
        p.get_orderbook = MagicMock(return_value=book([]))
        with self.assertRaisesRegex(RuntimeError, "no asks"):
            p.quote_buy("tok", 10.0)

    def test_order_aborts_when_fill_would_exceed_limit(self):
        """The audited case: best ask 0.50, then a wall at 0.95."""
        p = self.poly([(0.50, 20), (0.95, 5000)])
        with self.assertRaisesRegex(RuntimeError, "above the"):
            p.execute_market_order("tok", 50.0, max_price=0.60, dry_run=True)

    def test_dry_run_sends_nothing(self):
        p = self.poly([(0.50, 1000)])
        p.client = MagicMock()
        result = p.execute_market_order("tok", 10.0, max_price=0.60, dry_run=True)
        self.assertEqual(result, "dry-run")
        p.client.post_order.assert_not_called()

    def test_below_minimum_order_is_refused(self):
        p = self.poly([(0.50, 1000)])
        with self.assertRaisesRegex(ValueError, "below the"):
            p.execute_market_order("tok", 0.50, max_price=0.60, dry_run=True)

    def test_invalid_max_price_is_refused(self):
        p = self.poly([(0.50, 1000)])
        with self.assertRaisesRegex(ValueError, "max_price must be"):
            p.execute_market_order("tok", 10.0, max_price=1.5, dry_run=True)


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


if __name__ == "__main__":
    unittest.main()
