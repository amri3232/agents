from typing import List
from datetime import datetime


class Prompter:

    def generate_simple_ai_trader(market_description: str, relevant_info: str) -> str:
        return f"""
            
        You are a trader.
        
        Here is a market description: {market_description}.

        Here is relevant information: {relevant_info}.

        Do you buy or sell? How much?
        """

    def market_analyst(self) -> str:
        return f"""
        You are a market analyst that takes a description of an event and produces a market forecast. 
        Assign a probability estimate to the event occurring described by the user
        """

    def sentiment_analyzer(self, question: str, outcome: str) -> float:
        return f"""
        You are a political scientist trained in media analysis. 
        You are given a question: {question}.
        and an outcome of yes or no: {outcome}.
        
        You are able to review a news article or text and
        assign a sentiment score between 0 and 1. 
        
        """

    def prompts_polymarket(
        self, data1: str, data2: str, market_question: str, outcome: str
    ) -> str:
        current_market_data = str(data1)
        current_event_data = str(data2)
        return f"""
        You are an AI assistant for users of a prediction market called Polymarket.
        Users want to place bets based on their beliefs of market outcomes such as political or sports events.
        
        Here is data for current Polymarket markets {current_market_data} and 
        current Polymarket events {current_event_data}.

        Help users identify markets to trade based on their interests or queries.
        Provide specific information for markets including probabilities of outcomes.
        Give your response in the following format:

        I believe {market_question} has a likelihood <probability as a decimal
        between 0 and 1> for outcome of {outcome}.
        """

    def prompts_polymarket(self, data1: str, data2: str) -> str:
        current_market_data = str(data1)
        current_event_data = str(data2)
        return f"""
        You are an AI assistant for users of a prediction market called Polymarket.
        Users want to place bets based on their beliefs of market outcomes such as political or sports events.

        Here is data for current Polymarket markets {current_market_data} and 
        current Polymarket events {current_event_data}.
        Help users identify markets to trade based on their interests or queries.
        Provide specific information for markets including probabilities of outcomes.
        """

    def routing(self, system_message: str) -> str:
        return f"""You are an expert at routing a user question to the appropriate data source. System message: ${system_message}"""

    def multiquery(self, question: str) -> str:
        return f"""
        You're an AI assistant. Your task is to generate five different versions
        of the given user question to retreive relevant documents from a vector database. By generating
        multiple perspectives on the user question, your goal is to help the user overcome some of the limitations
        of the distance-based similarity search.
        Provide these alternative questions separated by newlines. Original question: {question}

        """

    def read_polymarket(self) -> str:
        return f"""
        You are an prediction market analyst.
        """

    def polymarket_analyst_api(self) -> str:
        return f"""You are an AI assistant for analyzing prediction markets.
                You will be provided with json output for api data from Polymarket.
                Polymarket is an online prediction market that lets users Bet on the outcome of future events in a wide range of topics, like sports, politics, and pop culture. 
                Get accurate real-time probabilities of the events that matter most to you. """

    def filter_events(self) -> str:
        return (
            self.polymarket_analyst_api()
            + f"""
        
        Filter these events for the ones you will be best at trading on profitably.

        """
        )

    def filter_markets(self) -> str:
        return (
            self.polymarket_analyst_api()
            + f"""
        
        Filter these markets for the ones you will be best at trading on profitably.

        """
        )

    def superforecaster(
        self,
        question: str,
        description: str,
        outcomes: List[str],
        outcome_prices: str = "",
        research: str = "",
        has_research: bool = False,
    ) -> str:
        """Work the analysis out in writing, before committing to a number.

        The market price is included deliberately: the market-calibration step
        asks the model to argue why the price might be right and it wrong,
        which it cannot do without seeing the price.

        `has_research` is passed separately from `research` so the blind case
        is explicit rather than inferred from an empty string: a forecaster
        with no current information must be told to stay near the market
        price, not left to improvise confidence.
        """
        if has_research:
            evidence_block = f"""
        Current information gathered for this market:

        {research}

        Treat this as raw input, not as truth. Check dates: a headline from
        before a decisive event is not current. Note what it does NOT tell
        you.
        """
        else:
            evidence_block = """
        NO current information was available for this market. You are
        forecasting from the description above and your own prior knowledge
        only, which may be out of date.

        This is a severe limitation and it must show in your answer. Traders
        setting the current price can read today's news and you cannot. Unless
        the description alone settles the question, you have no basis for
        disagreeing with the price, so your probability should stay close to
        it.
        """

        return f"""
        You are a highly specialised analyst for prediction markets. Your goal
        is to find the mathematically most precise probability for the outcome
        of an event - not to be right, and not to find a trade.

        Question: `{question}`
        Description: `{description}`
        Market outcomes: {outcomes}
        Current market prices for those outcomes, in the same order:
        {outcome_prices}

        On a prediction market the price of an outcome IS the market's implied
        probability of it. A price of 0.65 means the market collectively
        believes there is a 65% chance.
        {evidence_block}
        Work through all four rules in writing. Be specific: cite the concrete
        facts, dates and base rates you are reasoning from.

        1. Data-based evidence FOR yes:
            - Hard facts and current data supporting a positive outcome.
            - Name the source and date of each. "It seems likely" is not
              evidence.

        2. The advocatus diaboli principle:
            - Actively search for every reason the event does NOT happen.
            - Question your own assumptions from step 1. Which of them is
              weakest, and what happens to your estimate if it is wrong?
            - If you cannot argue the "no" case convincingly, you do not
              understand the question well enough to put a number on it.

        3. Market calibration:
            - If the current price differs markedly from your view, ask: what
              information does the market have that I have overlooked?
            - Resolution criteria are a common answer. Re-read the description
              and check that you are pricing the question that will actually
              be settled, not the one you assume.
            - Name your blind spot explicitly, then decide whether you still
              disagree.

        4. No extremes:
            - Be extremely careful with 0% and 100%. In the real world there
              is almost always residual uncertainty, so never use 0.0 or 1.0.
            - Disagreeing with the market is a claim that requires evidence.
              Absent such evidence, land close to the market price.

        End with one sentence stating your probability for `{outcomes[0]}`.
        """

    def probability_only(
        self,
        question: str,
        analysis: str,
        outcomes: List[str],
    ) -> str:
        """Extract the single number, with no room for anything else.

        Kept as a separate call so the analysis above is written out in full
        first: the reasoning is what makes the number worth anything, and a
        model asked for a bare number up front skips it.
        """
        return f"""
        Question: `{question}`

        Your analysis:
        {analysis}

        Output rule: give ONLY a single decimal number between 0.0 and 1.0,
        representing your estimated probability that `{outcomes[0]}` occurs.
        No explanation, no text, no percent sign, no label. Just the number.

        Example of a valid response:
        0.63
        """

    def one_best_trade(
        self,
        prediction: str,
        outcomes: List[str],
        outcome_prices: str,
        question: str = "",
    ) -> str:
        return (
            self.polymarket_analyst_api()
            + f"""

        The market question is: {question}

        You made the following analysis of it: {prediction}

        The market outcomes are: {outcomes}
        The last traded prices for those outcomes, in the same order, are:
        {outcome_prices}

        On Polymarket each outcome is a separate token priced between 0 and 1,
        and that price is the market's implied probability of the outcome.

        Commit to a probability for exactly one outcome. Do not pick a position
        size, a limit price or a side: those are computed from your probability
        and the live orderbook, not chosen by you.

        Your probability should be your honest belief, not a number chosen to
        justify a trade. The prices above already aggregate the views of
        informed traders, so a large disagreement with them needs a specific
        reason you can state. If you have no such reason, say so in your
        reasoning and give a probability close to the market price.

        Respond with ONLY a JSON object, no prose and no code fence:

          "outcome":     one of {outcomes}, spelled exactly as given
          "probability": your probability for that outcome, strictly between
                         0 and 1
          "reasoning":   one sentence on what specifically you know or have
                         reasoned that the market price does not reflect

        Example of the exact expected shape:

        {{"outcome": "Yes", "probability": 0.72, "reasoning": "The polling trend over the last three weeks has moved well past what the implied price reflects."}}

        """
        )

    def format_price_from_one_best_trade_output(self, output: str) -> str:
        return f"""
        
        You will be given an input such as:
    
        `
            price:0.5,
            size:0.1,
            side:BUY,
        `

        Please extract only the value associated with price.
        In this case, you would return "0.5".

        Only return the number after price:
        
        """

    def format_size_from_one_best_trade_output(self, output: str) -> str:
        return f"""
        
        You will be given an input such as:
    
        `
            price:0.5,
            size:0.1,
            side:BUY,
        `

        Please extract only the value associated with price.
        In this case, you would return "0.1".

        Only return the number after size:
        
        """

    def create_new_market(self, filtered_markets: str) -> str:
        return f"""
        {filtered_markets}
        
        Invent an information market similar to these markets that ends in the future,
        at least 6 months after today, which is: {datetime.today().strftime('%Y-%m-%d')},
        so this date plus 6 months at least.

        Output your format in:
        
        Question: "..."?
        Outcomes: A or B

        With ... filled in and A or B options being the potential results.
        For example:

        Question: "Will Kamala win"
        Outcomes: Yes or No
        
        """
