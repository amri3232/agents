"""
Automation layer for the autonomous trader.

Wraps the analysis pipeline in `Trader` with the pieces an unattended run needs:
a strict parser for the LLM's trade, hard risk limits, an append-only trade log
and bounded retries. It imports nothing heavy so it can be tested in isolation;
the `Trader` is injected.

Orders are only ever placed when the instance is created with `live=True` *and*
`ALLOW_LIVE_TRADING=true` is set in the environment. Everything else is a dry run.
"""

import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)


class TradeParseError(ValueError):
    """The LLM's trade could not be turned into a valid decision."""


class TradeRejected(Exception):
    """A risk limit stopped the trade."""


class NoTradeFound(Exception):
    """The pipeline finished without a market worth trading."""


class LiveTradingNotEnabled(RuntimeError):
    """Live trading was requested without the explicit opt-in."""


@dataclass
class TradeDecision:
    size: float  # fraction of funds, 0 < size <= 1
    side: str  # BUY or SELL
    price: Optional[float] = None
    outcome: Optional[str] = None


@dataclass
class TradeProposal:
    """What the pipeline hands to the automation layer."""

    question: str
    outcomes: List[str]
    outcome_prices: List[float]
    raw_trade: str
    market: Any = None


_NUMBER = r"(\d+(?:\.\d+)?|\.\d+)\s*(%)?"


def _number(text: str, name: str) -> Optional[float]:
    match = re.search(rf"\b{name}\s*[:=]\s*['\"]?{_NUMBER}", text, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    return value / 100 if match.group(2) else value


def parse_trade(text: str) -> TradeDecision:
    """
    Parse the model's answer, e.g. `price:0.5, size:0.1, side:BUY, outcome:Yes`.
    `price` and `outcome` are optional here; `size` and `side` are required.
    """
    size = _number(text, "size")
    if size is None:
        raise TradeParseError(f"no numeric size in trade: {text!r}")
    if not 0 < size <= 1:
        raise TradeParseError(f"size must be in (0, 1], got {size}")

    side = re.search(r"\bside\s*[:=]\s*['\"]?(BUY|SELL)\b", text, re.IGNORECASE)
    if not side:
        raise TradeParseError(f"no BUY/SELL side in trade: {text!r}")

    price = _number(text, "price")
    if price is not None and not 0 <= price <= 1:
        raise TradeParseError(f"price must be in [0, 1], got {price}")

    outcome = re.search(r"\boutcome\s*[:=]\s*['\"]?([^'\",`\n]+)", text, re.IGNORECASE)
    return TradeDecision(
        size=size,
        side=side.group(1).upper(),
        price=price,
        outcome=outcome.group(1).strip() if outcome else None,
    )


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}")


@dataclass(frozen=True)
class RiskConfig:
    max_order_usdc: float = 5.0
    max_daily_usdc: float = 20.0
    max_size_fraction: float = 0.05  # cap on the share of the balance per trade
    min_order_usdc: float = 1.0
    allow_live: bool = False

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "RiskConfig":
        env = os.environ if env is None else env
        defaults = cls()
        return cls(
            max_order_usdc=_env_float(env, "MAX_ORDER_USDC", defaults.max_order_usdc),
            max_daily_usdc=_env_float(env, "MAX_DAILY_USDC", defaults.max_daily_usdc),
            max_size_fraction=_env_float(
                env, "MAX_SIZE_FRACTION", defaults.max_size_fraction
            ),
            min_order_usdc=_env_float(env, "MIN_ORDER_USDC", defaults.min_order_usdc),
            allow_live=env.get("ALLOW_LIVE_TRADING", "").strip().lower()
            in ("1", "true", "yes"),
        )

    def size_order(
        self, decision: TradeDecision, balance: float, spent_today: float
    ) -> float:
        """Turn the model's fraction into a USDC amount within every limit."""
        if decision.side != "BUY":
            raise TradeRejected("only BUY orders are supported")

        remaining = self.max_daily_usdc - spent_today
        if remaining < self.min_order_usdc:
            raise TradeRejected(
                f"daily limit reached ({spent_today:.2f}/{self.max_daily_usdc:.2f} USDC)"
            )

        fraction = min(decision.size, self.max_size_fraction)
        amount = min(fraction * balance, self.max_order_usdc, remaining, balance)
        amount = math.floor(amount * 100) / 100  # whole cents, never round up
        if amount < self.min_order_usdc:
            raise TradeRejected(
                f"order of {amount:.2f} USDC is below the {self.min_order_usdc:.2f} "
                f"USDC minimum (balance {balance:.2f})"
            )
        return amount


class TradeLedger:
    """Append-only JSONL log of every run; also the source of truth for daily spend."""

    # statuses that count against the daily limit: "unconfirmed" means the order
    # call raised, so it may have gone through
    SPENT_STATUSES = ("executed", "unconfirmed")

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.getenv("TRADE_LOG_PATH", "trade_log.jsonl")

    def ensure_writable(self) -> None:
        with open(self.path, "a", encoding="utf-8"):
            pass

    def append(self, record: Dict[str, Any]) -> None:
        record = dict(record)
        record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def read(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        records = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
        return records

    def spent_today(self, now: Optional[datetime] = None) -> float:
        today = (now or datetime.now(timezone.utc)).date().isoformat()
        return sum(
            float(r.get("amount_usdc") or 0)
            for r in self.read()
            if r.get("mode") == "live"
            and r.get("status") in self.SPENT_STATUSES
            and str(r.get("timestamp", ""))[:10] == today
        )


def _order_failed(response: Any) -> bool:
    if isinstance(response, dict):
        return response.get("success") is False or bool(response.get("errorMsg"))
    return False


def _describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


class AutomatedTrader:
    """
    One unattended trading run: source a trade, check it against the risk limits,
    log it and, only in live mode, place it.

    Only the analysis step is retried. Order placement is never retried, so a
    flaky response cannot turn into a double order.
    """

    def __init__(
        self,
        trader: Any,
        risk: Optional[RiskConfig] = None,
        ledger: Optional[TradeLedger] = None,
        live: bool = False,
        max_attempts: int = 3,
        retry_delay: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.trader = trader
        self.risk = risk or RiskConfig.from_env()
        self.ledger = ledger or TradeLedger()
        self.live = live
        self.max_attempts = max(1, max_attempts)
        self.retry_delay = retry_delay
        self.sleep = sleep

        if live:
            if not self.risk.allow_live:
                raise LiveTradingNotEnabled(
                    "live trading needs ALLOW_LIVE_TRADING=true in addition to --live"
                )
            # without a writable log the daily limit would be blind
            self.ledger.ensure_writable()

    def run_once(self) -> Dict[str, Any]:
        record: Dict[str, Any] = {"mode": "live" if self.live else "dry_run"}

        try:
            proposal = self._source_proposal()
        except NoTradeFound as e:
            return self._finish(record, "no_trade", str(e))
        except Exception as e:
            return self._finish(record, "error", _describe(e))

        record["question"] = proposal.question
        record["llm_response"] = proposal.raw_trade

        try:
            decision = parse_trade(proposal.raw_trade)
            index = self._resolve_outcome(proposal, decision)
            record.update(
                side=decision.side,
                outcome=proposal.outcomes[index],
                outcome_price=proposal.outcome_prices[index],
                llm_price=decision.price,
                size_fraction=decision.size,
            )
            balance = self.trader.get_balance()
            record["balance_usdc"] = balance
            amount = self.risk.size_order(decision, balance, self.ledger.spent_today())
        except (TradeParseError, TradeRejected) as e:
            return self._finish(record, "rejected", str(e))
        except Exception as e:
            return self._finish(record, "error", _describe(e))

        record["amount_usdc"] = amount
        if not self.live:
            return self._finish(
                record, "dry_run", "live trading is off, no order placed"
            )

        try:
            response = self.trader.execute(proposal, amount, index)
        except Exception as e:
            return self._finish(record, "unconfirmed", _describe(e))
        record["response"] = response
        if _order_failed(response):
            return self._finish(record, "exchange_rejected", str(response))
        return self._finish(record, "executed", "order placed")

    def _source_proposal(self) -> TradeProposal:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self.trader.find_best_trade()
            except NoTradeFound:
                raise
            except Exception as e:
                last_error = e
                logger.warning(
                    "attempt %d/%d failed: %s", attempt, self.max_attempts, _describe(e)
                )
                if attempt < self.max_attempts:
                    self.sleep(self.retry_delay * attempt)
        raise last_error  # type: ignore[misc]

    @staticmethod
    def _resolve_outcome(proposal: TradeProposal, decision: TradeDecision) -> int:
        # guessing an outcome would mean trading the wrong side, so refuse instead
        if not decision.outcome:
            raise TradeRejected("the model did not name an outcome")
        wanted = decision.outcome.strip().lower()
        for index, outcome in enumerate(proposal.outcomes):
            if str(outcome).strip().lower() == wanted:
                return index
        raise TradeRejected(
            f"outcome {decision.outcome!r} is not one of {proposal.outcomes}"
        )

    def _finish(
        self, record: Dict[str, Any], status: str, detail: str
    ) -> Dict[str, Any]:
        record["status"] = status
        record["detail"] = detail
        try:
            self.ledger.append(record)
        except OSError as e:
            logger.error("could not write trade log: %s", e)
        logger.info("%s [%s]: %s", record["mode"], status, detail)
        return record
