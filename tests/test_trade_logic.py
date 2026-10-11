"""Tests for the order path: forecast parsing, edge, sizing, slippage.

These cover the defects recorded in PRE-FLIGHT-AUDIT.md. Nothing here touches
the network or a wallet.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agents.application.executor import Executor
from agents.application.journal import Journal
from agents.application.preflight import (
    REQUIRED_MARKET_FIELDS,
    _check_required_env,
    _check_research_env,
    _check_safety,
    format_results,
    run_checks,
)
from agents.application.prompts import Prompter
from agents.application.research import Research
from agents.application.strategy import (
    evaluate_both_sides,
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


class TestParseProbability(unittest.TestCase):
    """The output rule asks for one bare decimal; models disobey in known ways."""

    def parse(self, content):
        return Executor.parse_probability(content)

    def test_bare_decimal(self):
        self.assertAlmostEqual(self.parse("0.63"), 0.63)

    def test_surrounding_whitespace(self):
        self.assertAlmostEqual(self.parse("  0.42\n"), 0.42)

    def test_leading_dot(self):
        self.assertAlmostEqual(self.parse(".75"), 0.75)

    def test_code_fence(self):
        self.assertAlmostEqual(self.parse("`0.55`"), 0.55)

    def test_labelled_despite_the_rule(self):
        self.assertAlmostEqual(self.parse("probability: 0.31"), 0.31)

    def test_percent_is_converted(self):
        self.assertAlmostEqual(self.parse("63%"), 0.63)

    def test_percent_without_sign_is_converted(self):
        self.assertAlmostEqual(self.parse("63"), 0.63)

    def test_single_number_inside_a_stray_sentence(self):
        self.assertAlmostEqual(
            self.parse("My estimate is 0.58 based on the base rate."), 0.58
        )

    def test_several_numbers_is_refused_not_guessed(self):
        """The old regex guessed here, and guessed wrong."""
        with self.assertRaisesRegex(ValueError, "exactly one probability"):
            self.parse("Between 0.30 and 0.45, I would say 0.38.")

    def test_zero_is_refused(self):
        """Calibration: reality leaves residual uncertainty."""
        with self.assertRaisesRegex(ValueError, "strictly between"):
            self.parse("0.0")

    def test_one_is_refused(self):
        with self.assertRaisesRegex(ValueError, "strictly between"):
            self.parse("1.0")

    def test_out_of_range_is_refused(self):
        with self.assertRaises(ValueError):
            self.parse("420")

    def test_no_number_is_refused(self):
        with self.assertRaises(ValueError):
            self.parse("I cannot say.")


class TestForecastComplement(unittest.TestCase):
    def test_second_outcome_is_the_complement(self):
        f = MarketForecast(probability_yes=0.7)
        self.assertAlmostEqual(f.probability_for(0), 0.7)
        self.assertAlmostEqual(f.probability_for(1), 0.3)

    def test_third_outcome_is_refused(self):
        with self.assertRaises(ValueError):
            MarketForecast(probability_yes=0.7).probability_for(2)

    def test_extremes_are_refused(self):
        for bad in (0.0, 1.0, -0.2, 1.5):
            with self.assertRaises(ValueError):
                MarketForecast(probability_yes=bad)


class TestEvaluateBothSides(unittest.TestCase):
    def sides(self, probability_yes, asks, min_edge=0.10):
        return evaluate_both_sides(
            forecast=MarketForecast(probability_yes=probability_yes),
            outcomes=["Yes", "No"],
            token_ids=["tok_yes", "tok_no"],
            asks=asks,
            available_notional=100.0,
            market_question="Q?",
            min_edge=min_edge,
            kelly_multiplier=0.25,
            max_fraction=0.05,
        )

    def test_finds_edge_on_the_yes_side(self):
        got = self.sides(0.80, [0.50, 0.50])
        self.assertEqual([c.outcome for c in got], ["Yes"])

    def test_finds_edge_on_the_no_side(self):
        """P(yes)=0.20 means P(no)=0.80; the cheap No is the mispriced side."""
        got = self.sides(0.20, [0.50, 0.50])
        self.assertEqual([c.outcome for c in got], ["No"])
        self.assertAlmostEqual(got[0].probability, 0.80)
        self.assertEqual(got[0].token_id, "tok_no")

    def test_no_edge_on_either_side(self):
        self.assertEqual(self.sides(0.50, [0.52, 0.52]), [])

    def test_both_sides_can_qualify_when_the_book_is_wide(self):
        # A wide spread can leave both sides cheap relative to the forecast.
        got = self.sides(0.50, [0.30, 0.30])
        self.assertEqual(sorted(c.outcome for c in got), ["No", "Yes"])

    def test_non_binary_market_is_refused(self):
        with self.assertRaisesRegex(ValueError, "two-outcome markets only"):
            evaluate_both_sides(
                forecast=MarketForecast(probability_yes=0.6),
                outcomes=["A", "B", "C"],
                token_ids=["a", "b", "c"],
                asks=[0.3, 0.3, 0.3],
                available_notional=100.0,
                market_question="Q?",
                min_edge=0.1,
                kelly_multiplier=0.25,
                max_fraction=0.05,
            )


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
            probability=probability,
            outcome="Yes",
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


class TestResearch(unittest.TestCase):
    """Research is best-effort: a source outage downgrades, never aborts."""

    def test_no_sources_configured_is_blind_not_an_error(self):
        got = Research().gather("Will it happen?")
        self.assertEqual(got["sources"], [])
        self.assertEqual(got["context"], "")

    def test_unavailable_sources_are_skipped(self):
        news, search = MagicMock(), MagicMock()
        news.available = False
        search.available = False
        got = Research(news=news, search=search).gather("Q?")
        self.assertEqual(got["sources"], [])
        search.get_context.assert_not_called()
        news.get_articles_for_question.assert_not_called()

    def test_search_context_is_collected(self):
        search = MagicMock()
        search.available = True
        search.get_context.return_value = "some web context"
        got = Research(search=search).gather("Q?")
        self.assertEqual(got["sources"], ["tavily"])
        self.assertIn("some web context", got["context"])

    def test_news_headlines_are_formatted_with_dates(self):
        news = MagicMock()
        news.available = True
        news.get_articles_for_question.return_value = [
            {
                "title": "Something happened",
                "publishedAt": "2026-10-09T12:00:00Z",
                "source": {"name": "Reuters"},
                "description": "Details here",
            }
        ]
        got = Research(news=news).gather("Q?")
        self.assertEqual(got["sources"], ["newsapi"])
        self.assertIn("2026-10-09", got["context"])
        self.assertIn("Reuters", got["context"])

    def test_a_raising_source_does_not_abort(self):
        search = MagicMock()
        search.available = True
        search.get_context.side_effect = RuntimeError("rate limited")
        got = Research(search=search).gather("Q?")
        self.assertEqual(got["sources"], [])
        self.assertIn("unavailable", got["context"])

    def test_empty_results_do_not_count_as_a_source(self):
        search = MagicMock()
        search.available = True
        search.get_context.return_value = "   "
        self.assertEqual(Research(search=search).gather("Q?")["sources"], [])

    def test_articles_without_a_title_are_dropped(self):
        news = MagicMock()
        news.available = True
        news.get_articles_for_question.return_value = [{"description": "orphan"}]
        self.assertEqual(Research(news=news).gather("Q?")["sources"], [])

    def test_context_is_truncated(self):
        search = MagicMock()
        search.available = True
        search.get_context.return_value = "x" * 10000
        got = Research(search=search).gather("Q?")
        self.assertLessEqual(len(got["context"]), 4000)


class TestBlindForecastPrompt(unittest.TestCase):
    """A forecaster with no current data must be told to defer to the price."""

    @staticmethod
    def _flat(text):
        """Collapse whitespace: the prompt wraps, assertions should not care."""
        return " ".join(text.split())

    def prompt(self, has_research):
        return Prompter().superforecaster(
            "Will it happen?",
            "a description",
            ["Yes", "No"],
            '["0.40","0.60"]',
            research="some context" if has_research else "",
            has_research=has_research,
        )

    def test_blind_prompt_says_so_and_defers_to_the_market(self):
        p = self._flat(self.prompt(False))
        self.assertIn("NO current information was available", p)
        self.assertIn("probability should stay close to it", p)
        self.assertIn("severe limitation", p)

    def test_researched_prompt_includes_the_context(self):
        p = self._flat(self.prompt(True))
        self.assertIn("some context", p)
        self.assertNotIn("NO current information was available", p)

    def test_price_is_always_present_for_the_calibration_step(self):
        for has in (True, False):
            self.assertIn("0.40", self.prompt(has))


class TestPreflight(unittest.TestCase):
    """The checker must survive every dependency being broken at once."""

    def test_a_raising_check_is_reported_not_propagated(self):
        def boom():
            raise ConnectionError("network unreachable")

        results = run_checks([("gamma", boom)])
        self.assertEqual(results[0][1], "fail")
        self.assertIn("ConnectionError", results[0][2])

    def test_one_broken_check_does_not_hide_the_others(self):
        def boom():
            raise RuntimeError("down")

        results = run_checks([("a", boom), ("b", lambda: ("ok", "fine")), ("c", boom)])
        self.assertEqual([r[1] for r in results], ["fail", "ok", "fail"])

    def test_missing_required_env_fails(self):
        with patch.dict(os.environ, {}, clear=True):
            status, detail = _check_required_env()
        self.assertEqual(status, "fail")
        self.assertIn("OPENAI_API_KEY", detail)

    def test_missing_research_env_warns_about_being_blind(self):
        with patch.dict(os.environ, {}, clear=True):
            status, detail = _check_research_env()
        self.assertEqual(status, "warn")
        self.assertIn("blind", detail)

    def test_research_env_present_passes(self):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "x"}, clear=True):
            status, detail = _check_research_env()
        self.assertEqual(status, "ok")
        self.assertIn("TAVILY_API_KEY", detail)

    def test_live_trading_is_flagged_loudly(self):
        with patch.dict(os.environ, {"POLYMARKET_DRY_RUN": "false"}, clear=True):
            status, detail = _check_safety()
        self.assertEqual(status, "warn")
        self.assertIn("LIVE TRADING IS ON", detail)

    def test_dry_run_is_the_default_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            status, detail = _check_safety()
        self.assertEqual(status, "ok")
        self.assertIn("dry run", detail)

    def test_summary_calls_out_failures(self):
        out = format_results([("a", "fail", "broken")])
        self.assertIn("FAILED", out)
        self.assertIn("will not work", out)

    def test_all_passing_does_not_claim_profitability(self):
        out = format_results([("a", "ok", "fine")])
        self.assertIn("does NOT", out)
        self.assertIn("journal-report", out)

    def test_required_market_fields_match_the_parser(self):
        """If map_api_to_market gains a field, this check must gain it too."""
        import inspect

        from agents.polymarket.polymarket import Polymarket

        source = inspect.getsource(Polymarket.map_api_to_market)
        for field in REQUIRED_MARKET_FIELDS:
            self.assertIn(f'"{field}"', source, f"{field} is checked but not parsed")


if __name__ == "__main__":
    unittest.main()
