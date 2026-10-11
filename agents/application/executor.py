import os
import json
import ast
import re
from typing import List, Dict, Any

import math

# Headroom allowed above the best ask when setting the limit, so a tick of
# movement between quote and fill does not kill an otherwise good order.
MAX_SLIPPAGE = 0.02

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from agents.polymarket.gamma import GammaMarketClient as Gamma
from agents.connectors.chroma import PolymarketRAG as Chroma
from agents.utils.objects import (
    SimpleEvent,
    SimpleMarket,
    MarketForecast,
    Candidate,
    OrderPlan,
)
from agents.application.strategy import evaluate_both_sides
from agents.application.prompts import Prompter
from agents.polymarket.polymarket import Polymarket


def retain_keys(data, keys_to_retain):
    if isinstance(data, dict):
        return {
            key: retain_keys(value, keys_to_retain)
            for key, value in data.items()
            if key in keys_to_retain
        }
    elif isinstance(data, list):
        return [retain_keys(item, keys_to_retain) for item in data]
    else:
        return data


class Executor:
    def __init__(self, default_model="gpt-3.5-turbo-16k") -> None:
        load_dotenv()
        max_token_model = {"gpt-3.5-turbo-16k": 15000, "gpt-4-1106-preview": 95000}
        self.token_limit = max_token_model.get(default_model)
        self.prompter = Prompter()
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.llm = ChatOpenAI(
            model=default_model,  # gpt-3.5-turbo"
            temperature=0,
        )
        self.gamma = Gamma()
        self.chroma = Chroma()
        self.polymarket = Polymarket()

    def get_llm_response(self, user_input: str) -> str:
        system_message = SystemMessage(content=str(self.prompter.market_analyst()))
        human_message = HumanMessage(content=user_input)
        messages = [system_message, human_message]
        result = self.llm.invoke(messages)
        return result.content

    def get_superforecast(
        self, event_title: str, market_question: str, outcome: str
    ) -> str:
        messages = self.prompter.superforecaster(
            description=event_title, question=market_question, outcome=outcome
        )
        result = self.llm.invoke(messages)
        return result.content

    def estimate_tokens(self, text: str) -> int:
        # This is a rough estimate. For more accurate results, consider using a tokenizer.
        return len(text) // 4  # Assuming average of 4 characters per token

    def process_data_chunk(
        self, data1: List[Dict[Any, Any]], data2: List[Dict[Any, Any]], user_input: str
    ) -> str:
        system_message = SystemMessage(
            content=str(self.prompter.prompts_polymarket(data1=data1, data2=data2))
        )
        human_message = HumanMessage(content=user_input)
        messages = [system_message, human_message]
        result = self.llm.invoke(messages)
        return result.content

    def divide_list(self, original_list, i):
        # Calculate the size of each sublist
        sublist_size = math.ceil(len(original_list) / i)

        # Use list comprehension to create sublists
        return [
            original_list[j : j + sublist_size]
            for j in range(0, len(original_list), sublist_size)
        ]

    def get_polymarket_llm(self, user_input: str) -> str:
        data1 = self.gamma.get_current_events()
        data2 = self.gamma.get_current_markets()

        combined_data = str(self.prompter.prompts_polymarket(data1=data1, data2=data2))

        # Estimate total tokens
        total_tokens = self.estimate_tokens(combined_data)

        # Set a token limit (adjust as needed, leaving room for system and user messages)
        token_limit = self.token_limit
        if total_tokens <= token_limit:
            # If within limit, process normally
            return self.process_data_chunk(data1, data2, user_input)
        else:
            # If exceeding limit, process in chunks
            chunk_size = len(combined_data) // ((total_tokens // token_limit) + 1)
            print(
                f"total tokens {total_tokens} exceeding llm capacity, now will split and answer"
            )
            group_size = (total_tokens // token_limit) + 1  # 3 is safe factor
            keys_no_meaning = [
                "image",
                "pagerDutyNotificationEnabled",
                "resolvedBy",
                "endDate",
                "clobTokenIds",
                "negRiskMarketID",
                "conditionId",
                "updatedAt",
                "startDate",
            ]
            useful_keys = [
                "id",
                "questionID",
                "description",
                "liquidity",
                "clobTokenIds",
                "outcomes",
                "outcomePrices",
                "volume",
                "startDate",
                "endDate",
                "question",
                "questionID",
                "events",
            ]
            data1 = retain_keys(data1, useful_keys)
            cut_1 = self.divide_list(data1, group_size)
            cut_2 = self.divide_list(data2, group_size)
            cut_data_12 = zip(cut_1, cut_2)

            results = []

            for cut_data in cut_data_12:
                sub_data1 = cut_data[0]
                sub_data2 = cut_data[1]
                sub_tokens = self.estimate_tokens(
                    str(
                        self.prompter.prompts_polymarket(
                            data1=sub_data1, data2=sub_data2
                        )
                    )
                )

                result = self.process_data_chunk(sub_data1, sub_data2, user_input)
                results.append(result)

            combined_result = " ".join(results)

            return combined_result

    def filter_events(self, events: "list[SimpleEvent]") -> str:
        prompt = self.prompter.filter_events(events)
        result = self.llm.invoke(prompt)
        return result.content

    def filter_events_with_rag(self, events: "list[SimpleEvent]") -> str:
        prompt = self.prompter.filter_events()
        print()
        print("... prompting ... ", prompt)
        print()
        return self.chroma.events(events, prompt)

    def map_filtered_events_to_markets(
        self, filtered_events: "list[SimpleEvent]"
    ) -> "list[SimpleMarket]":
        markets = []
        for e in filtered_events:
            data = json.loads(e[0].json())
            market_ids = data["metadata"]["markets"].split(",")
            for market_id in market_ids:
                market_data = self.gamma.get_market(market_id)
                formatted_market_data = self.polymarket.map_api_to_market(market_data)
                markets.append(formatted_market_data)
        return markets

    def filter_markets(self, markets) -> "list[tuple]":
        prompt = self.prompter.filter_markets()
        print()
        print("... prompting ... ", prompt)
        print()
        return self.chroma.markets(markets, prompt)

    @staticmethod
    def parse_probability(content: str) -> float:
        """Read the single decimal the model was told to return, and nothing else.

        The output rule asks for a bare number, but models append labels,
        percent signs and stray sentences anyway, so this is tolerant about
        surrounding text while refusing anything ambiguous. It deliberately
        does not fall back to "take some number from the reply": a reply
        containing several numbers is a reply that did not follow the rule,
        and guessing which one is the forecast is how the old regex produced
        confidently wrong orders.
        """
        text = content.strip()

        # A bare number, optionally wrapped in fences, quotes or a label.
        direct = re.fullmatch(
            r"[`'\"]*\s*(?:probability\s*[:=]\s*)?(\d*\.?\d+)\s*%?[`'\"]*",
            text,
            re.IGNORECASE,
        )
        if direct:
            value = float(direct.group(1))
        else:
            numbers = re.findall(r"\d*\.\d+|\b[01]\b", text)
            if len(numbers) != 1:
                raise ValueError(
                    f"expected exactly one probability in the reply, found "
                    f"{len(numbers)}; got: {content!r}"
                )
            value = float(numbers[0])

        # A model that answered in percent despite the instruction.
        if value > 1.0:
            if value <= 100.0:
                value = value / 100.0
            else:
                raise ValueError(f"probability out of range: {value}")

        if not 0.0 < value < 1.0:
            raise ValueError(
                f"probability must be strictly between 0 and 1, got {value}; "
                f"a calibrated forecast leaves residual uncertainty"
            )
        return value

    def forecast_market(self, market_object) -> MarketForecast:
        """Analyse one market in writing, then extract a single probability."""
        market_document = market_object[0].dict()
        market = market_document["metadata"]
        outcomes = ast.literal_eval(market["outcomes"])
        question = market["question"]
        description = market_document["page_content"]

        prompt = self.prompter.superforecaster(
            question, description, outcomes, market["outcome_prices"]
        )
        analysis = self.llm.invoke(prompt).content

        prompt = self.prompter.probability_only(question, analysis, outcomes)
        probability = self.parse_probability(self.llm.invoke(prompt).content)

        return MarketForecast(probability_yes=probability, reasoning=analysis[:500])

    def score_market(
        self,
        market_object,
        available_notional: float,
        min_edge: float,
        kelly_multiplier: float,
        max_fraction: float,
    ) -> "list[Candidate]":
        """Forecast one market and score both of its sides.

        The asks come from the orderbook rather than from the market metadata:
        the cached `outcome_prices` can be stale, and it is not what an order
        would actually pay.
        """
        market = market_object[0].dict()["metadata"]
        outcomes = ast.literal_eval(market["outcomes"])
        token_ids = ast.literal_eval(market["clob_token_ids"])
        if len(token_ids) != len(outcomes):
            raise ValueError(
                f"market has {len(outcomes)} outcomes but "
                f"{len(token_ids)} token ids; cannot resolve the token safely"
            )
        if len(outcomes) != 2:
            raise ValueError(
                f"market has {len(outcomes)} outcomes; this strategy prices "
                f"two-outcome markets only"
            )

        forecast = self.forecast_market(market_object)

        asks = [
            self.polymarket.quote_buy(str(t), self.polymarket.MIN_ORDER_USDC)[
                "best_ask"
            ]
            for t in token_ids
        ]

        return evaluate_both_sides(
            forecast=forecast,
            outcomes=outcomes,
            token_ids=token_ids,
            asks=asks,
            available_notional=available_notional,
            market_question=market["question"],
            min_edge=min_edge,
            kelly_multiplier=kelly_multiplier,
            max_fraction=max_fraction,
        )

    def build_order_plan(self, candidate, max_order_usdc: float) -> OrderPlan:
        """Turn a scored candidate into a concrete order.

        The outcome's token was resolved from the market's own outcome list in
        score_market. The old code indexed `clob_token_ids` at a hardcoded [1],
        so it always bought the second outcome regardless of the forecast.
        """
        amount_usdc = min(
            candidate.size_fraction * candidate.available_notional, max_order_usdc
        )
        return OrderPlan(
            token_id=candidate.token_id,
            outcome=candidate.outcome,
            side="BUY",
            amount_usdc=round(amount_usdc, 2),
            # Pay up to the forecast probability at most: above it the trade is
            # negative-EV on the agent's own numbers.
            limit_price=round(
                min(candidate.ask_price * (1 + MAX_SLIPPAGE), candidate.probability), 4
            ),
            expected_shares=round(amount_usdc / candidate.ask_price, 2),
            market_question=candidate.market_question,
            probability=candidate.probability,
            edge=candidate.edge,
            expected_value=candidate.expected_value,
        )

    def source_best_market_to_create(self, filtered_markets) -> str:
        prompt = self.prompter.create_new_market(filtered_markets)
        print()
        print("... prompting ... ", prompt)
        print()
        result = self.llm.invoke(prompt)
        content = result.content
        return content
