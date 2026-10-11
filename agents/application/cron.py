"""Scheduled runs of the trading agent.

The previous version could not run at all: a local class named `Scheduler`
shadowed the imported one, so `Scheduler()` inside `__init__` recursed into
itself; `super()` was never called; and `self.weekly(...)` did not exist.
"""

import time

from scheduler import Scheduler as JobScheduler
from scheduler.trigger import Monday

from agents.application.trade import Trader


class TradingAgent:
    """Runs one_best_trade on a schedule.

    Honours the same POLYMARKET_DRY_RUN default as a manual run, so scheduling
    this does not by itself start spending money.
    """

    def __init__(self) -> None:
        self.trader = Trader()
        self.schedule = JobScheduler()

    def schedule_weekly(self, trigger=Monday()) -> None:
        self.schedule.weekly(trigger, self.trader.one_best_trade)

    def start(self) -> None:
        while True:
            self.schedule.exec_jobs()
            time.sleep(1)


if __name__ == "__main__":
    agent = TradingAgent()
    agent.schedule_weekly()
    agent.start()
