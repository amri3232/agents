import logging
import time
from datetime import timedelta

from scheduler import Scheduler

from agents.application.automation import AutomatedTrader
from agents.application.trade import Trader


class TradingAgent:
    """
    Runs the autonomous trader on a fixed interval.

    Trades are dry runs unless live=True and ALLOW_LIVE_TRADING=true is set,
    see automation.py for the risk limits that apply either way.
    """

    def __init__(
        self,
        interval: timedelta = timedelta(days=1),
        live: bool = False,
        max_attempts: int = 3,
    ) -> None:
        self.automated = AutomatedTrader(Trader(), live=live, max_attempts=max_attempts)
        self.schedule = Scheduler(n_threads=0)
        self.schedule.cyclic(interval, self.automated.run_once)

    def start(self, run_now: bool = True) -> None:
        logging.info("trading agent started, live=%s", self.automated.live)
        if run_now:
            # cyclic jobs only fire after a full interval
            self.automated.run_once()
        while True:
            self.schedule.exec_jobs()
            time.sleep(1)
