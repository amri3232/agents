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
from agents.application.strategy import evaluate_candidate
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
    def parse_forecast(content: str, outcomes: "list[str]") -> MarketForecast:
        """Turn the model's reply into a validated MarketForecast.

        The previous approach split the reply on commas and regex-matched a
        float out of the second field. A one-line preamble shifted that match
        from `size` onto `price` (turning a 20% stake into 62%), an integer
        size raised IndexError, and `side` was dropped entirely. Parsing JSON
        and validating it means a malformed reply fails here instead of
        producing a plausible-looking wrong order.
        """
        text = content.strip()
        # Tolerate a ```json fence, since models add one even when told not to.
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        else:
            # Otherwise take the outermost JSON object in the reply.
            brace_start, brace_end = text.find("{"), text.rfind("}")
            if brace_start != -1 and brace_end > brace_start:
                text = text[brace_start : brace_end + 1]

        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"model reply was not valid JSON ({e}); got: {content!r}"
            ) from e
        if not isinstance(raw, dict):
            raise ValueError(f"expected a JSON object, got {type(raw).__name__}")

        forecast = MarketForecast(**raw)
        if forecast.outcome not in outcomes:
            raise ValueError(
                f"model chose outcome {forecast.outcome!r}, which is not one of "
                f"the market outcomes {outcomes}"
            )
        return forecast

    def forecast_market(self, market_object) -> MarketForecast:
        """Ask the model for a probability on one market."""
        market_document = market_object[0].dict()
        market = market_document["metadata"]
        outcomes = ast.literal_eval(market["outcomes"])
        question = market["question"]
        description = market_document["page_content"]

        prompt = self.prompter.superforecaster(question, description, outcomes)
        reasoning = self.llm.invoke(prompt).content

        prompt = self.prompter.one_best_trade(
            reasoning, outcomes, market["outcome_prices"], question
        )
        return self.parse_forecast(self.llm.invoke(prompt).content, outcomes)

    def score_market(
        self,
        market_object,
        available_notional: float,
        min_edge: float,
        kelly_multiplier: float,
        max_fraction: float,
    ) -> "Candidate | None":
        """Forecast one market and score it against the live orderbook.

        The ask comes from the orderbook rather than from the market metadata:
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

        forecast = self.forecast_market(market_object)
        token_id = str(token_ids[outcomes.index(forecast.outcome)])

        quote = self.polymarket.quote_buy(token_id, self.polymarket.MIN_ORDER_USDC)

        return evaluate_candidate(
            forecast=forecast,
            token_id=token_id,
            ask_price=quote["best_ask"],
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
