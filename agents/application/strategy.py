"""Position selection and sizing from an explicit edge calculation.

Replaces the previous approach, where the model was asked to invent a
`size_fraction` and markets were ranked by embedding similarity to a generic
prompt. Neither of those is a measure of edge.

Everything here is a pure function of a forecast and an orderbook price, so it
is testable without a network or a wallet.

A caveat that no amount of code removes: edge is computed against the model's
own probability, so it is only as good as that probability. A confidently
miscalibrated forecaster produces large apparent edge and loses money. Treat
the numbers here as a filter against obviously bad trades, not as evidence of
a profitable strategy. Measure realized calibration (see journal.py) before
trusting them.
"""

from typing import List, Optional

from agents.utils.objects import Candidate, MarketForecast

# A market priced at the extremes is where fees, the spread and resolution
# ambiguity dominate any modelled edge, so stay out of the tails.
MIN_TRADEABLE_PRICE = 0.05
MAX_TRADEABLE_PRICE = 0.95


def expected_value_per_dollar(probability: float, price: float) -> float:
    """EV of staking $1 on an outcome priced at `price`.

    A share costs `price` and pays 1 if the outcome happens, so $1 buys
    1/price shares: EV = probability / price - 1. Zero means break-even.
    """
    if not 0.0 < price < 1.0:
        raise ValueError(f"price must be in (0, 1), got {price}")
    return probability / price - 1.0


def kelly_fraction(probability: float, price: float) -> float:
    """Kelly-optimal fraction of the bankroll for a binary outcome.

    Buying at `price` risks `price` to win `1 - price`, so the net odds are
    b = (1 - price) / price and Kelly reduces to (p - price) / (1 - price).
    Returns 0 when there is no edge; never returns a negative fraction, since
    the short side is a different trade this client cannot place.
    """
    if not 0.0 < price < 1.0:
        raise ValueError(f"price must be in (0, 1), got {price}")
    if probability <= price:
        return 0.0
    return (probability - price) / (1.0 - price)


def evaluate_candidate(
    forecast: MarketForecast,
    token_id: str,
    ask_price: float,
    available_notional: float,
    market_question: str,
    min_edge: float,
    kelly_multiplier: float,
    max_fraction: float,
) -> Optional[Candidate]:
    """Score one outcome, or return None if it fails a gate.

    `ask_price` must come from the live orderbook, not from the market
    metadata: the cached outcome price can be stale and is not what an order
    would actually pay.
    """
    if not MIN_TRADEABLE_PRICE <= ask_price <= MAX_TRADEABLE_PRICE:
        return None

    edge = forecast.probability - ask_price
    # Compare with a small tolerance: an edge meant to sit exactly on the
    # threshold (0.60 - 0.50) is 0.09999999999999998 in binary and would
    # otherwise be rejected.
    if edge < min_edge - 1e-9:
        return None

    full_kelly = kelly_fraction(forecast.probability, ask_price)
    fraction = min(full_kelly * kelly_multiplier, max_fraction)
    if fraction <= 0:
        return None

    return Candidate(
        token_id=token_id,
        outcome=forecast.outcome,
        market_question=market_question,
        probability=forecast.probability,
        ask_price=ask_price,
        edge=edge,
        expected_value=expected_value_per_dollar(forecast.probability, ask_price),
        full_kelly=full_kelly,
        size_fraction=fraction,
        available_notional=available_notional,
        reasoning=forecast.reasoning,
    )


def select_best(candidates: List[Candidate]) -> Optional[Candidate]:
    """Pick the candidate with the highest expected value, or None if empty.

    Ranking by EV rather than by raw edge: a 5-point edge on a 0.10 outcome
    pays far more per dollar than the same edge on a 0.80 outcome.
    """
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.expected_value)
