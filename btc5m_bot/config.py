"""Zentrale Konfiguration des Polymarket-BTC-5m-Bots.

* Alle Parameter haben hier einen Standardwert (diese Datei ist die "Single Source of Truth").
* Jeder Parameter kann ohne Code-Änderung per Umgebungsvariable ``BOT_<NAME>`` überschrieben
  werden (z. B. ``BOT_MODE=LIVE`` oder ``BOT_MAX_POSITION_USD=5``) - praktisch für tmux/nohup.
* Zugangsdaten (Private Key, API-Keys, RPC) stehen NUR in ``.env`` und werden in ``Secrets``
  geladen. Sie landen nie in Logs (``Secrets.__repr__`` maskiert).

Der Standardmodus ist ``PAPER`` (kein echtes Geld). ``LIVE`` muss bewusst aktiviert werden.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import List

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# .env im Bot-Verzeichnis laden (und zusätzlich ein evtl. .env im aktuellen Verzeichnis).
load_dotenv(BASE_DIR / ".env")
load_dotenv()

PAPER = "PAPER"
LIVE = "LIVE"


@dataclass
class Config:
    # ------------------------------------------------------------------ Modus
    MODE: str = PAPER                 # "PAPER" (Standard, simuliert) oder "LIVE" (echtes Geld!)
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = str(BASE_DIR / "logs")
    DATA_DIR: str = str(BASE_DIR / "data")

    # --------------------------------------------------------------- Kapital
    BANKROLL_USD: float = 100.0       # Kapital, das der Bot als "sein" Budget betrachtet (Kelly, Drawdown)
    MAX_POSITION_USD: float = 10.0    # max. Einsatz pro einzelner Order
    MAX_USD_PER_WINDOW: float = 20.0  # max. Gesamteinsatz (inkl. offener Orders) pro 5-Minuten-Fenster
    KELLY_FRACTION: float = 0.25      # Bruchteil des Kelly-Einsatzes (0.25 = Quarter-Kelly)

    # ------------------------------------------------------- Gebühren/Execution
    TAKER_FEE_RATE: float = 0.0175    # Taker-Gebühr (flach auf den Notional gerechnet, konservativ)
    SLIPPAGE: float = 0.01            # max. Aufschlag auf den besten Ask/Abschlag auf den besten Bid
    USE_MAKER_ORDERS: bool = True     # Post-only-Limit-Orders, wenn kein Taker-Edge vorhanden ist
    ORDER_TTL_SEC: float = 8.0        # Maker-Orders älter als das werden storniert/ersetzt
    ORDER_COOLDOWN_SEC: float = 3.0   # Mindestabstand zwischen zwei neuen Orders
    CANCEL_BEFORE_END_SEC: float = 8.0  # alle offenen Orders so kurz vor Fensterende stornieren

    # ---------------------------------------------------------------- Strategie
    MIN_EDGE: float = 0.03            # Mindest-Edge pro Share (USD) nach Gebühren, Taker
    MAKER_MIN_EDGE: float = 0.04      # Mindest-Edge für Maker-Orders (Fill ist unsicher -> höhere Hürde)
    MODEL_SHRINKAGE: float = 0.20     # Gewicht des Marktpreises in der fairen Wahrscheinlichkeit (0..1)
    SIGNAL_LOOKBACK_SEC: int = 120    # "BTC der letzten 2 Minuten"
    REQUIRE_MOMENTUM_AGREEMENT: bool = True   # 2-min-Bewegung muss in Richtung der Bewegung seit Start zeigen
    MIN_MOVE_BPS: float = 4.0         # min. Bewegung seit Fensterstart (Basispunkte), sonst kein Signal
    MIN_ELAPSED_SEC: float = 45.0     # frühestens so viele Sekunden nach Fensterstart handeln
    ENTRY_CUTOFF_REMAINING_SEC: float = 25.0  # keine neuen Einstiege in den letzten N Sekunden
    MIN_ENTRY_PRICE: float = 0.10     # keine Lotterie-Tickets
    MAX_ENTRY_PRICE: float = 0.90     # keine Käufe nahe 1.00 (kaum Upside, hohes Tail-Risiko)
    MAX_SPREAD: float = 0.08          # Orderbuch mit größerem Spread wird ignoriert
    VOL_WINDOW_SEC: int = 300         # Fenster für die realisierte Volatilität
    MIN_ANNUAL_VOL: float = 0.25      # Untergrenze der angenommenen Volatilität (annualisiert)
    BASIS_NOISE_BPS: float = 1.5      # Unsicherheit Binance/Coinbase vs. Chainlink-Referenz (Basispunkte)
    ENABLE_EV_EXIT: bool = True       # Position verkaufen, wenn der Bid mehr zahlt als der Erwartungswert
    EXIT_MARGIN: float = 0.02         # Sicherheitsmarge für den EV-Exit
    EXIT_MIN_REMAINING_SEC: float = 6.0  # kurz vor Ende nicht mehr verkaufen (Spread/Latenz)

    # --------------------------------------------------------------------- Risiko
    MAX_DRAWDOWN_USD: float = 25.0    # Halt, wenn realisierter PnL um so viel unter sein Hoch fällt
    MAX_DAILY_LOSS_USD: float = 30.0  # Halt bis zum nächsten UTC-Tag
    MAX_CONSECUTIVE_LOSSES: int = 5   # danach Pause
    LOSS_STREAK_PAUSE_SEC: float = 900.0
    API_LATENCY_LIMIT_MS: float = 1500.0   # Median der letzten Messungen darüber -> Circuit Breaker
    LATENCY_SAMPLES: int = 5
    MAX_CONSECUTIVE_API_ERRORS: int = 5
    BREAKER_COOLDOWN_SEC: float = 30.0     # verdoppelt sich bei wiederholtem Auslösen (max. 5 min)
    BREAKER_MAX_COOLDOWN_SEC: float = 300.0
    MAX_SPOT_STALENESS_SEC: float = 3.0    # Spot-Tick älter -> kein Handel
    MAX_BOOK_STALENESS_SEC: float = 4.0    # Orderbuch älter -> kein Handel

    # --------------------------------------------------------------- Timing der Loops
    STRATEGY_INTERVAL_SEC: float = 0.5
    BOOK_POLL_SEC: float = 0.7
    MARKET_REFRESH_SEC: float = 15.0
    SETTLE_INTERVAL_SEC: float = 20.0
    STATUS_INTERVAL_SEC: float = 30.0
    SETTLE_DELAY_SEC: float = 20.0         # so lange nach Fensterende warten, bevor Abrechnung versucht wird
    PAPER_SPOT_FALLBACK_SEC: float = 180.0  # Paper: Abrechnung per Spot, falls Gamma so lange nichts liefert
    MARKETS_AHEAD: int = 2                 # so viele zukünftige Fenster vorab laden

    # ------------------------------------------------------------ Märkte/Endpunkte
    SLUG_PREFIX: str = "btc-updown-5m-"    # Slug = Prefix + Unix-Startzeit des Fensters
    WINDOW_SEC: int = 300
    GAMMA_URL: str = "https://gamma-api.polymarket.com"
    CLOB_URL: str = "https://clob.polymarket.com"
    CHAIN_ID: int = 137
    BINANCE_WS_URL: str = "wss://stream.binance.com:9443/ws/btcusdt@trade"
    BINANCE_REST_URL: str = "https://api.binance.com"
    BINANCE_SYMBOL: str = "BTCUSDT"
    COINBASE_WS_URL: str = "wss://ws-feed.exchange.coinbase.com"
    COINBASE_REST_URL: str = "https://api.exchange.coinbase.com"
    COINBASE_PRODUCT: str = "BTC-USD"
    ENABLE_BINANCE: bool = True
    ENABLE_COINBASE: bool = True
    # Experimentell: Chainlink-Feed über Polymarkets RTDS-Websocket (= Abrechnungsquelle).
    ENABLE_RTDS_CHAINLINK: bool = False
    RTDS_WS_URL: str = "wss://ws-live-data.polymarket.com"
    PRICE_HISTORY_SEC: int = 1800

    # ----------------------------------------------------------- On-chain (LIVE)
    CTF_ADDRESS: str = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"          # Conditional Tokens (Polygon)
    COLLATERAL_ADDRESS: str = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"   # USDC.e (Polygon)
    EXCHANGE_ADDRESS: str = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"     # CTF Exchange (Polygon)
    AUTO_REDEEM: bool = True
    USE_HEARTBEAT: bool = True        # Dead-Man's-Switch: Server storniert Orders, wenn der Bot verstummt
    ENFORCE_GEOBLOCK_CHECK: bool = True   # LIVE nicht starten, wenn Polymarket die IP als blockiert meldet

    def validate(self) -> None:
        self.MODE = str(self.MODE).strip().upper()
        if self.MODE not in (PAPER, LIVE):
            raise ValueError(f"MODE muss PAPER oder LIVE sein, nicht {self.MODE!r}")
        if self.MAX_POSITION_USD <= 0 or self.MAX_USD_PER_WINDOW <= 0 or self.BANKROLL_USD <= 0:
            raise ValueError("BANKROLL_USD, MAX_POSITION_USD und MAX_USD_PER_WINDOW müssen > 0 sein")
        if self.MAX_USD_PER_WINDOW < self.MAX_POSITION_USD:
            self.MAX_USD_PER_WINDOW = self.MAX_POSITION_USD
        if not 0 <= self.TAKER_FEE_RATE < 0.2:
            raise ValueError("TAKER_FEE_RATE muss in [0, 0.2) liegen (z. B. 0.0175 für 1,75 %)")
        if not 0 <= self.MODEL_SHRINKAGE <= 1:
            raise ValueError("MODEL_SHRINKAGE muss in [0, 1] liegen")
        if self.SLIPPAGE < 0 or self.MIN_EDGE < 0 or self.MAKER_MIN_EDGE < 0:
            raise ValueError("SLIPPAGE, MIN_EDGE und MAKER_MIN_EDGE dürfen nicht negativ sein")
        if not 0 < self.MIN_ENTRY_PRICE < self.MAX_ENTRY_PRICE < 1:
            raise ValueError("0 < MIN_ENTRY_PRICE < MAX_ENTRY_PRICE < 1 erforderlich")
        if self.WINDOW_SEC <= 0:
            raise ValueError("WINDOW_SEC muss > 0 sein")
        if not (self.ENABLE_BINANCE or self.ENABLE_COINBASE or self.ENABLE_RTDS_CHAINLINK):
            raise ValueError("Mindestens eine Preisquelle (ENABLE_BINANCE/ENABLE_COINBASE/ENABLE_RTDS_CHAINLINK) nötig")

    @property
    def is_live(self) -> bool:
        return self.MODE == LIVE


def _cast(raw: str, template):
    if isinstance(template, bool):  # bool vor int prüfen (bool ist Unterklasse von int)
        return raw.strip().lower() in ("1", "true", "yes", "on", "ja")
    if isinstance(template, int):
        return int(float(raw))
    if isinstance(template, float):
        return float(raw)
    return raw


def load_config() -> Config:
    """Standardwerte + Überschreibungen aus ``BOT_<NAME>``-Umgebungsvariablen."""
    cfg = Config()
    for f in fields(Config):
        raw = os.getenv(f"BOT_{f.name}")
        if raw is None or raw.strip() == "":
            continue
        setattr(cfg, f.name, _cast(raw, getattr(cfg, f.name)))
    cfg.validate()
    return cfg


@dataclass
class Secrets:
    """Zugangsdaten aus ``.env``. ``repr`` zeigt niemals Werte."""

    private_key: str = ""
    api_key: str = ""
    api_secret: str = ""
    passphrase: str = ""
    rpc_url: str = ""
    funder: str = ""            # Polymarket-Profiladresse (Proxy/Safe); leer bei reinem EOA
    signature_type: int = 0     # 0 = EOA, 1 = Polymarket-Proxy (E-Mail/Magic), 2 = Gnosis-Safe (Browser-Wallet)

    @classmethod
    def from_env(cls) -> "Secrets":
        def g(name: str) -> str:
            return (os.getenv(name) or "").strip()

        try:
            sig = int(g("SIGNATURE_TYPE") or 0)
        except ValueError:
            sig = 0
        pk = g("PRIVATE_KEY")
        if pk and not pk.startswith("0x"):
            pk = "0x" + pk
        return cls(
            private_key=pk,
            api_key=g("POLYMARKET_API_KEY"),
            api_secret=g("API_SECRET"),
            passphrase=g("PASSPHRASE"),
            rpc_url=g("RPC_URL"),
            funder=g("FUNDER_ADDRESS"),
            signature_type=sig,
        )

    def missing_for_live(self) -> List[str]:
        missing = []
        if not self.private_key:
            missing.append("PRIVATE_KEY")
        if self.signature_type in (1, 2) and not self.funder:
            missing.append("FUNDER_ADDRESS (nötig bei SIGNATURE_TYPE 1/2)")
        return missing

    def __repr__(self) -> str:  # niemals Werte ausgeben
        def flag(v) -> str:
            return "set" if v else "missing"

        return (
            f"Secrets(private_key={flag(self.private_key)}, api_key={flag(self.api_key)}, "
            f"api_secret={flag(self.api_secret)}, passphrase={flag(self.passphrase)}, "
            f"rpc_url={flag(self.rpc_url)}, funder={flag(self.funder)}, signature_type={self.signature_type})"
        )

    __str__ = __repr__
