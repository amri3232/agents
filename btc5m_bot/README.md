# Polymarket BTC-5-Minuten-Bot

Autonomer Trading-Bot für Polymarkets **„Bitcoin Up or Down – 5 Minutes"**-Märkte. Er erkennt jedes neue
5-Minuten-Fenster, vergleicht den Echtzeit-BTC-Kurs (Binance/Coinbase) mit den Quotes auf Polymarket, platziert
bei Mispricing **ausschließlich Limit-Orders**, sichert Positionen per EV-Exit ab und löst Gewinne automatisch ein.

> **Standardmäßig läuft der Bot im PAPER-Modus** (simuliert, kein echtes Geld). `LIVE` muss bewusst aktiviert werden.

> ⚠️ **Wichtig vorab**
> * Kein Gewinnversprechen. Kurzfrist-Arbitrage auf Polymarket ist ein umkämpfter Markt (schnellere Feeds, Market
>   Maker). Der Bot enthält ein Modell mit eingebauten Schutzmechanismen, aber **keinen belegten Edge** – das
>   zeigt erst deine eigene Paper-Phase (`python main.py --report`, siehe [Kalibrierung](#4-paper-phase-auswerten)).
> * Du kannst im LIVE-Modus Geld verlieren – setze nur Beträge ein, deren Verlust du verkraftest.
> * Polymarket sperrt das Handeln aus mehreren Ländern. Prüfe die aktuelle Liste in Polymarkets Dokumentation,
>   ob dein Standort betroffen ist. Der Bot bricht im LIVE-Modus ab, wenn Polymarket die IP als blockiert meldet;
>   eine Umgehung (VPN o. Ä.) ist **nicht** eingebaut und verstößt gegen die Nutzungsbedingungen.
> * Dieses Verzeichnis ist unabhängig vom restlichen Repo (`agents/` = Polymarket-Agents-Framework). Es ändert dort nichts.

---

## 1. Dateistruktur

| Datei | Aufgabe |
|---|---|
| `config.py` | Alle Parameter (Modus, Positionsgrößen, Risiko-Schwellen, Gebühren …). Überschreibbar per `BOT_<NAME>` |
| `market_finder.py` | Findet aktuelle/kommende 5-Min-Märkte (Gamma-API, Slug `btc-updown-5m-<Startzeit>`), liest Orderbücher (CLOB) |
| `price_feed.py` | BTC-Spot per WebSocket (Binance, Coinbase, optional Chainlink via Polymarket-RTDS), Vol-Schätzung, Startpreis |
| `strategy.py` | Signal-Engine: faire Wahrscheinlichkeit vs. Polymarket-Quote, Edge nach Gebühren, Sizing, Exit |
| `execution.py` | Orders (PAPER-Simulation / LIVE über `py-clob-client`), Cancel/Replace, Abrechnung, Auto-Redeem (web3) |
| `risk.py` | Limits, Drawdown-Halt, Circuit Breaker bei API-Latenz/-Fehlern, Budget pro Fenster |
| `state.py` | Portfolio-Buchhaltung, Persistenz (`data/state.json`), Trade-Journal (`data/trades.csv`) |
| `main.py` | Haupt-Loop (supervidierte Tasks, Auto-Reconnect, Logs) + CLI |
| `diagnostics.py`, `report.py` | `--check` (Verbindungstest), `--report` (Auswertung) |
| `.env.example`, `requirements.txt` | Vorlage für Zugangsdaten, Abhängigkeiten |
| `tests/` | 100+ Tests (`pytest`) |

## 2. Installation

Voraussetzung: Python ≥ 3.10 (getestet mit 3.13). Aus dem Repo-Root:

```bash
cd btc5m_bot
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> Nutze ein **eigenes venv**: das `requirements.txt` im Repo-Root gehört zum Polymarket-Agents-Framework und pinnt
> ältere, inkompatible Versionen.

## 3. `.env` erstellen

```bash
cp .env.example .env
nano .env        # oder ein anderer Editor
```

| Variable | Bedeutung | Wann nötig |
|---|---|---|
| `PRIVATE_KEY` | Private Key der signierenden Wallet (`0x…`) | nur LIVE |
| `POLYMARKET_API_KEY`, `API_SECRET`, `PASSPHRASE` | CLOB-API-Zugang. **Leer lassen** → der Bot leitet ihn aus dem Private Key ab | optional |
| `RPC_URL` | Polygon-RPC (Alchemy/Infura/QuickNode …) für Auto-Redeem & Gas-Check | LIVE (empfohlen) |
| `SIGNATURE_TYPE` | `0` = normale Wallet (EOA), `1` = Polymarket-Konto mit E-Mail/Magic-Login, `2` = Browser-Wallet-Konto (Safe) | LIVE |
| `FUNDER_ADDRESS` | Adresse deines Polymarket-Profils; **Pflicht bei Typ 1/2** (dort liegt das Geld) | LIVE bei Typ 1/2 |

Hinweise:
* **PAPER braucht keine einzige dieser Angaben.**
* Nutze eine **eigene Bot-Wallet** mit begrenztem Guthaben statt deiner Hauptwallet.
* `.env` ist per `.gitignore` ausgeschlossen. Der Bot gibt Secrets nie in Logs aus.
* **EOA-Wallet (Typ 0):** einmalig `python main.py --approve` ausführen (setzt die Token-Freigaben für den Exchange;
  braucht etwas POL für Gas). Konten aus der Polymarket-Oberfläche (Typ 1/2) haben diese Freigaben bereits.
* **Redeem:** Auto-Redeem funktioniert bei Typ 0 (die Tokens liegen in der Signer-Wallet). Bei Typ 1/2 rechnet der
  Bot Gewinne korrekt ab, du musst sie aber im Polymarket-UI per „Claim" einlösen (der Bot weist im Log darauf hin).

## 4. Erster Start: prüfen → Paper → auswerten

```bash
python main.py --check      # testet Gamma/CLOB/Preisfeeds (und bei gesetztem Key: Wallet, API, RPC); sendet keine Orders
python main.py              # startet im PAPER-Modus (Standard)
python main.py --report     # Auswertung der Paper-Trades
```

`--check` ist wichtig: Er bestätigt auf **deinem** Rechner, dass das Slug-Schema der 5-Min-Märkte stimmt, die
Orderbücher lesbar sind und deine Region die Preisfeeds nicht blockiert (z. B. Binance.com liefert aus manchen
Ländern HTTP 451 – dann übernimmt Coinbase automatisch; alternativ `BOT_BINANCE_WS_URL` auf einen anderen Endpunkt setzen).

**Paper-Phase auswerten.** `--report` zeigt Gesamt-PnL, Trefferquote und die **Kalibrierung**: Ø Modell-Wahrscheinlichkeit
vs. Ø Einstiegspreis vs. tatsächliche Trefferquote sowie den Brier-Score von Modell und Markt. Nur wenn das Modell über
mehrere hundert Trades *besser* kalibriert ist als der Marktpreis und die Trefferquote den Einstiegspreis plus Gebühren
übertrifft, gibt es einen Edge. Lass den Paper-Modus mindestens mehrere Tage laufen.
Beachte: Paper ist optimistischer als die Realität (keine Warteschlangenposition bei Maker-Orders, keine
Order-Latenz, kein Marktimpact).

## 5. Strategie in Kürze

1. **Referenz:** Der Bot merkt sich den BTC-Preis `S₀` bei Fensterstart (bei Start mitten im Fenster per REST nachgeladen).
2. **Faire Wahrscheinlichkeit** (digitale Option, driftlos): `P(Up) = Φ( ln(S/S₀) / √(σ²·τ + b²) )` mit realisierter
   Vol `σ` (letzte 5 min), Restzeit `τ` und Basis-Unsicherheit `b` (Börsenkurs vs. Chainlink-Abrechnungskurs).
3. **Schutz vor Modellfehlern:** Mischung mit dem Marktpreis (`MODEL_SHRINKAGE`), nur Handel *in Richtung* der Bewegung,
   Mindestbewegung (`MIN_MOVE_BPS`), Bestätigung durch die 2-Minuten-Bewegung (`SIGNAL_LOOKBACK_SEC`), Plausibilitätsgrenze
   für zu große Edges, kein Handel in den ersten/letzten Sekunden, nur bei frischen Daten und engem Spread.
4. **Edge:** `P − Ask·(1 + Taker-Gebühr)` muss ≥ `MIN_EDGE` sein. Dann **aggressives Limit** (Preisdeckel = Ask + `SLIPPAGE`,
   nie über „fair − MIN_EDGE"). Sonst optional **Post-only-Maker-Gebot** am Bid (höhere Hürde `MAKER_MIN_EDGE`).
   Es gibt **keinen Code-Pfad für Market-Orders**.
5. **Größe:** Bruchteil-Kelly (`KELLY_FRACTION`), gedeckelt durch `MAX_POSITION_USD` (zum *schlechtesten* Fill-Preis gerechnet)
   und `MAX_USD_PER_WINDOW`.
6. **Absicherung:** EV-Exit – eine Position wird per Limit-Verkauf geschlossen, wenn der Bid (netto nach Gebühr) mehr zahlt,
   als sie laut Modell erwartbar wert ist. Offene Orders werden bei Signalverlust, nach `ORDER_TTL_SEC` und
   `CANCEL_BEFORE_END_SEC` vor Fensterende storniert (Cancel/Replace).
7. **Abrechnung/Redeem:** Nach Fensterende fragt der Bot das Ergebnis on-chain ab (`payoutNumerators`) bzw. über Gamma und
   löst Gewinner-Tokens per `redeemPositions` ein (mit Retry/Backoff).

Gebühren: Das Modell rechnet **flach `TAKER_FEE_RATE` (1,75 %) auf den Notional** – konservativ. Die tatsächliche
Polymarket-Gebühr kann preisabhängig sein; passe `TAKER_FEE_RATE` an die aktuelle Gebührentabelle an. Maker zahlen keine Gebühr.

## 6. Sicherheitsmechanismen

* **PAPER als Standard**; LIVE nur mit `BOT_MODE=LIVE` (oder `MODE` in `config.py`) und vollständiger `.env`.
* **Drawdown-Halt** (`MAX_DRAWDOWN_USD`) und **Tagesverlust-Limit** (`MAX_DAILY_LOSS_USD`); der Halt überlebt Neustarts
  (aufheben mit `python main.py --reset-halt`). Pause nach `MAX_CONSECUTIVE_LOSSES` Verlusten in Folge.
* **Circuit Breaker:** Median-Latenz der Orderbuch-/Order-Aufrufe über `API_LATENCY_LIMIT_MS` oder `MAX_CONSECUTIVE_API_ERRORS`
  Fehler in Folge → keine neuen Orders, offene Orders werden storniert, Cooldown verdoppelt sich bei Wiederholung.
* **Datenfrische:** veraltete Spot-Ticks oder Orderbücher blockieren neue Einstiege.
* **Dead-Man's-Switch** (LIVE): Heartbeat an den CLOB – verstummt der Bot, storniert der Server offene Orders.
* **Nie abstürzen:** jeder Loop läuft unter einem Supervisor mit Backoff; HTTP 429 wird mit `Retry-After`/Backoff behandelt;
  WebSockets reconnecten automatisch; äußerer Neustart-Wrapper. Bei unklarem Order-Ausgang (Timeout) wird **nie blind
  wiederholt**, sondern erst mit dem Server abgeglichen (unbekannte Orders werden storniert).
* **Sauberer Shutdown** (Ctrl+C / SIGTERM): offene Orders werden storniert, der Stand gespeichert. Offene Positionen werden
  beim nächsten Start weiter abgerechnet.

## 7. Im Hintergrund betreiben (24/7)

**tmux** (empfohlen – du kannst jederzeit wieder reinschauen):

```bash
tmux new -s btc5m
cd ~/agents/btc5m_bot && source .venv/bin/activate && python main.py
# Abkoppeln: Strg+B, dann D          Wieder anhängen: tmux attach -t btc5m
```

**nohup:**

```bash
cd ~/agents/btc5m_bot && source .venv/bin/activate
nohup python main.py > logs/nohup.out 2>&1 &
echo $! > bot.pid
tail -f logs/bot.log                 # Live-Log (rotiert automatisch)
kill -TERM "$(cat bot.pid)"          # sauber beenden (storniert Orders, speichert State)
```

**systemd** (startet nach Reboot/Absturz neu), `/etc/systemd/system/btc5m.service`:

```ini
[Unit]
Description=Polymarket BTC 5m Bot
After=network-online.target
Wants=network-online.target

[Service]
User=DEINUSER
WorkingDirectory=/home/DEINUSER/agents/btc5m_bot
ExecStart=/home/DEINUSER/agents/btc5m_bot/.venv/bin/python main.py
Restart=always
RestartSec=10
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

`sudo systemctl enable --now btc5m` · Logs: `journalctl -u btc5m -f`

Die Systemuhr muss synchron sein (`timedatectl`), die Fenster hängen an der Unix-Zeit – der Bot warnt bei >2 s Abweichung.

## 8. Auf LIVE umstellen – Checkliste

1. Paper-Phase ausgewertet (`--report`), Kalibrierung überzeugt.
2. `.env` ausgefüllt, `python main.py --check` ohne `[FAIL]`.
3. EOA: `python main.py --approve`. Wallet hat USDC.e (Guthaben bei Polymarket) und etwas POL für Gas.
4. Zunächst **kleine** Limits, z. B. in `.env`: `BOT_MODE=LIVE`, `BOT_BANKROLL_USD=50`, `BOT_MAX_POSITION_USD=5`,
   `BOT_MAX_USD_PER_WINDOW=10`, `BOT_MAX_DRAWDOWN_USD=15`.
5. Die ersten Stunden beobachten (`tail -f logs/bot.log`) und Fills/Redeems mit dem Polymarket-UI abgleichen.

Das Konto sollte dem Bot allein gehören: unbekannte offene Orders auf den BTC-5m-Tokens werden vom Bot storniert.
Positionen, die nicht vom Bot erzeugt wurden, verfolgt er nicht.

## 9. Wichtige Parameter (`config.py`)

| Parameter | Standard | Wirkung |
|---|---|---|
| `MODE` | `PAPER` | `PAPER` / `LIVE` |
| `BANKROLL_USD` | 100 | Kapital für Kelly-Sizing & Cash-Limit |
| `MAX_POSITION_USD` / `MAX_USD_PER_WINDOW` | 10 / 20 | Einsatz je Order / je 5-Min-Fenster |
| `MIN_EDGE` / `MAKER_MIN_EDGE` | 0.03 / 0.04 | Mindest-Edge je Share (USD) nach Gebühren |
| `TAKER_FEE_RATE` / `SLIPPAGE` | 0.0175 / 0.01 | Gebühr (flach) / max. Preisaufschlag |
| `MODEL_SHRINKAGE` | 0.20 | Gewicht des Marktpreises in der Wahrscheinlichkeit |
| `MIN_MOVE_BPS` | 4 | Mindestbewegung seit Fensterstart |
| `MIN_ELAPSED_SEC` / `ENTRY_CUTOFF_REMAINING_SEC` | 45 / 25 | Handelsfenster innerhalb der 300 s |
| `MAX_DRAWDOWN_USD` / `MAX_DAILY_LOSS_USD` | 25 / 30 | harte Stopps |
| `API_LATENCY_LIMIT_MS` | 1500 | Circuit-Breaker-Schwelle |

Jeder Parameter lässt sich ohne Code-Änderung per `BOT_<NAME>=…` (Umgebung oder `.env`) überschreiben.

## 10. Fehlersuche

| Symptom | Ursache / Lösung |
|---|---|
| `[FAIL] Gamma: kein Markt für Slug …` | Slug-Schema geändert oder Region blockiert → `SLUG_PREFIX` prüfen |
| `binance: Websocket-Handshake abgelehnt (HTTP 451)` | Binance.com in deiner Region gesperrt → Coinbase läuft weiter; ggf. `BOT_BINANCE_WS_URL` ändern |
| `CLOB Rate-Limit (429)` | wird automatisch mit Backoff behandelt; bei Dauerzustand `BOT_BOOK_POLL_SEC` erhöhen |
| `Order abgelehnt: not enough balance / allowance` | EOA: `--approve`; Guthaben/Funder/`SIGNATURE_TYPE` prüfen |
| `CIRCUIT BREAKER offen` | Netz/API langsam – öffnet sich nach dem Cooldown von selbst wieder |
| `TRADING GESTOPPT: Max. Drawdown` | Limit erreicht; nach Prüfung `python main.py --reset-halt` |
| `Bitte … manuell einlösen` | Proxy/Safe-Wallet oder kein `RPC_URL` → im Polymarket-UI „Claim" |

## 11. Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Die Tests decken Strategie, Risiko, Buchhaltung, Paper-Execution, Markt-Parsing, Feeds, den HTTP-Client (inkl. 429-Retry)
und den `LiveExecutor` (gegen einen Fake-CLOB-Client) sowie einen kompletten simulierten Handelsdurchlauf ab.

## 12. Ehrliche Grenzen

* Die externen Schnittstellen (Gamma-Slug-Schema, Antwortformate, Heartbeat, RTDS-Chainlink-Feed) sind nach Polymarkets
  Dokumentation und dem `py-clob-client`-Quellcode implementiert, wurden aber beim Bau **nicht gegen die Live-Server**
  getestet (die Entwicklungsumgebung hatte keinen Zugriff). Darum gibt es `--check`: Führe es vor dem ersten Start aus.
* Der Chainlink-Feed (`BOT_ENABLE_RTDS_CHAINLINK=true`) ist experimentell und standardmäßig aus.
* Binance/Coinbase sind nur ein Proxy für den Chainlink-Abrechnungspreis; die Differenz („Basis") wird über
  `BASIS_NOISE_BPS` berücksichtigt, kann aber in volatilen Minuten größer sein.
* Im LIVE-Modus schätzt der Bot Gebühren pauschal; maßgeblich ist der tatsächliche Kontostand bei Polymarket.
