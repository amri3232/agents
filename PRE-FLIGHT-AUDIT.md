# Pre-Flight-Audit: Polymarket Agents — vor dem Einsatz von echtem Geld

**Datum:** 2026-10-10 · **Branch:** `claude/youthful-hamilton-wcg6el` · **Stand:** Commit `081f2b5`

## Verdikt: NICHT einsatzbereit. Kein echtes Geld einsetzen.

> **Status-Update (Commit `af3d754`).** Ein Teil der Blocker ist behoben; der
> Rest steht noch. Live-Handel ist jetzt Opt-in: `POLYMARKET_DRY_RUN` ist
> standardmäßig `true`, ein Lauf bepreist und validiert die Order, gibt sie
> aus und sendet nichts.
>
> | Punkt | Status |
> |---|---|
> | A1 Outcome-Index hardcodiert | behoben |
> | A2 `side` verworfen / SELL unmöglich | behoben (SELL wird explizit abgelehnt) |
> | A3 Größe per Regex aus Freitext | behoben (JSON + Validierung) |
> | A4 Kein Slippage-Schutz | behoben (Cap, Mindestliquidität, Mindestgröße, Max-Order) |
> | A5 Kein Check auf geschlossene Märkte | behoben (Markt-Ebene) |
> | A6 Marktauswahl ohne Bewertung | Edge/Kelly statt Ähnlichkeit — aber **Kalibrierung unbewiesen** |
> | A7 Unbegrenzte Rekursion | behoben (3 Versuche, Backoff) |
> | B1 `requirements.txt` nicht installierbar | behoben (verifiziert auf 3.11) |
> | B2 Keine On-Chain-Approvals | behoben (`cli.py set-approvals`, Einmal-Lauf) |
> | B3 Proxy-Wallet nicht unterstützt | behoben (`POLYMARKET_SIGNATURE_TYPE`/`_FUNDER`) |
> | B4 Trade auskommentiert | ersetzt durch Dry-Run-Schalter |
> | C1 Prompt-f-String-Bug | behoben (beide Stellen) |
> | C2 Keine Tests | 45 Tests (Order-Pfad, Edge/Kelly, Journal) |
> | C3 `cron.py` nicht lauffähig | behoben |
> | C4 `build_order` ignoriert Preis | **offen** (von diesem Pfad unbenutzt) |
> | C5 Keine Paginierung | **offen** (`pdb`-Import entfernt) |
> | C6 Keine Risikolimits | behoben (Max-Order, Tageslimit, Bankroll-Cap, Journal) |
>
> Die Abschnitte darunter beschreiben den Zustand **vor** den Fixes und
> bleiben als Begründung stehen.
>
> **Der entscheidende Punkt ist jetzt die Kalibrierung, nicht mehr der Code.**
> Die Order wird korrekt gebaut, begrenzt und nur bei positivem Erwartungswert
> ausgelöst. Aber dieser Erwartungswert wird gegen die *eigene*
> Wahrscheinlichkeit des Modells gerechnet. Ein überkonfidenter Forecaster
> erzeugt große scheinbare Edge und verliert trotzdem Geld. Nichts in diesem
> Repo belegt, dass der Forecaster kalibriert ist.
>
> Deshalb schreibt der Agent jedes Urteil nach `trade_journal.jsonl`. Nach ein
> paar Dutzend aufgelösten Märkten zeigt `cli.py journal-report`, ob die
> Prognosen trafen — inklusive realisiertem P&L. Erst diese Zahl entscheidet,
> ob echtes Geld sinnvoll ist. Bis dahin: `POLYMARKET_DRY_RUN=true`.

Der autonome Handelspfad (`agents/application/trade.py` → `executor.py` → `polymarket.py`)
enthält mehrere Fehler, die unabhängig von der Qualität der KI-Prognose zu Geldverlust führen.
Die Prognose wird teilweise schlicht verworfen, bevor die Order gebaut wird.

---

## A. Blocker — Geldverlust unabhängig von der Prognosequalität

### A1. Die Richtung der Wette wird ignoriert — es wird immer Outcome-Index `[1]` gekauft
`agents/polymarket/polymarket.py:342`
```python
token_id = ast.literal_eval(market[0].dict()["metadata"]["clob_token_ids"])[1]
```
Der Index `[1]` ist fest verdrahtet. Bei `outcomes: ["Yes","No"]` kauft der Bot **immer "No"**.
Kommt der Superforecaster zu "Yes", wird trotzdem "No" gekauft — also systematisch die
Gegenposition zur eigenen Analyse. Reproduziert: siehe A2-Testlauf.

### A2. `side: BUY/SELL` aus der Modellantwort wird verworfen — und ist technisch unmöglich
`agents/application/executor.py:184-189` extrahiert **nur** `size`; `price` und `side` werden
weggeworfen. Zusätzlich hat `MarketOrderArgs` in `py_clob_client==0.17.5` gar kein `side`-Feld:
```
MarketOrderArgs(token_id, amount, price=0, fee_rate_bps=0, nonce=0, taker=...)
```
und intern wird `calculate_market_price(token_id, "BUY", amount)` aufgerufen. `execute_market_order`
kann also **ausschließlich kaufen**. Ein "SELL" des Modells ist strukturell nicht umsetzbar.

### A3. Positionsgröße wird per Regex aus Freitext geraten — stiller 3x-Fehler
`agents/application/executor.py:185-187`
```python
data = best_trade.split(",")
size = re.findall("\d+\.\d+", data[1])[0]
```
`data[1]` setzt voraus, dass das Modell *exakt* `price:X,size:Y,side:Z` ohne jede Einleitung liefert.
Verifizierter Testlauf (Balance 100 USDC):

| Modellantwort | resultierende Order |
|---|---|
| `price:0.5,size:0.1,side:BUY,` | 10.0 USDC — korrekt |
| mit Markdown-Fence ```` ``` ```` | 10.0 USDC — korrekt |
| `price:0.5,size:1,side:BUY,` (Integer) | **CRASH** IndexError |
| `price:0.5,size:0.1,side:SELL,` | 10.0 USDC **als BUY** |
| `Based on my analysis, price:0.62, size:0.2, side:BUY` | **62.0 USDC statt 20.0** |

Die letzte Zeile ist der gefährlichste Fall: eine kurze Einleitung verschiebt `data[1]` von `size`
auf `price` — aus 20 % Einsatz werden 62 %. Kein Fehler, keine Warnung, dreifache Position.

### A4. Kein Slippage-Schutz — der Worst-Case-Preis gilt für die *gesamte* Order
`price=0` ⇒ der Client ermittelt den Preis selbst (Quellcode `OrderBuilder.calculate_market_price`):
```python
for p in positions:
    sum += float(p.size) * float(p.price)
    if sum >= amount_to_match:
        return float(p.price)   # <- schlechtestes benötigtes Level
```
Dieser *eine* Preis wird als Limit für die komplette FOK-Order verwendet — kein Durchschnitt.
Verifiziert: Book `asks=[(0.50, 20), (0.95, 5000)]`, Order 50 USDC ⇒ Limit **0.95** für alles,
obwohl der beste Ask 0.50 war. Polymarket-Bücher jenseits der Top-Märkte sind genau so dünn.
Es gibt keinen Maximalpreis, keine Mindestgröße-Prüfung und keine Abbruchbedingung.

### A5. Keine Prüfung auf geschlossene/aufgelöste Märkte
`closed` und `archived` werden **nur auf Event-Ebene** geprüft (`polymarket.py:279-280`).
Auf Markt-Ebene gibt es keinen einzigen Check: `map_filtered_events_to_markets`
(`executor.py:137-148`) holt Märkte per ID und filtert nichts. Das Modell `SimpleMarket`
(`agents/utils/objects.py`) besitzt die Felder `closed`/`archived` gar nicht, und
`filter_markets_for_trading` prüft nur `active` — das bleibt bei Polymarket auch nach
Marktschluss `true`. Ein bereits aufgelöster Markt kann also gehandelt werden.

### A6. "Marktauswahl" ist reine Embedding-Ähnlichkeit, keine Bewertung
`filter_markets` (`executor.py:150-155`) → `chroma.markets()` → `similarity_search_with_score`
gegen den generischen Prompt *"Filter these markets for the ones you will be best at trading
on profitably."* Das ist semantische Textähnlichkeit zur Marktbeschreibung — es bewertet weder
Preis noch Volumen, Spread oder Edge. `trade.py:54` nimmt anschließend blind `[0]`.
Es gibt keine Kante gegenüber dem Markt, nur eine Vektorsuche.

### A7. Unbegrenzte Rekursion im Fehlerfall
`trade.py:63-65` und `creator.py:41-43`
```python
except Exception as e:
    print(f"Error {e} \n \n Retrying")
    self.one_best_trade()      # ruft sich selbst im except-Block auf
```
Jeder Fehler — auch der IndexError aus A3 — löst einen Selbstaufruf ohne Abbruchkriterium,
Backoff oder Zähler aus. Das endet im RecursionError, und bei zeitweiligen Fehlern werden
beliebig viele LLM-Calls und potenziell wiederholte Orderversuche erzeugt.

---

## B. Blocker — Setup schlägt fehl oder Wallet wird nicht gefunden

### B1. `requirements.txt` ist auf modernem Python nicht installierbar (verifiziert)
`eip712-structs==1.1.0` (im Repo **nirgends importiert** — `grep` über alle `.py`: 0 Treffer)
zieht `pysha3==1.0.2` nach. `pysha3` hat Wheels nur bis cp38 und bricht ab Python 3.10 beim
Kompilieren:
```
Modules/_sha3/backport.inc:78:10: fatal error: pystrhex.h: No such file or directory
```
Auch das direkt gepinnte `pysha3==1.0.2` ist unbenutzt. Nach Entfernen **beider** Zeilen läuft
die Installation durch (verifiziert auf Python 3.11).
Zusätzlich: `web3==6.11.0` importiert `pkg_resources` und braucht daher `setuptools<81`.
`README`/`Dockerfile` nennen Python 3.9 — seit Oktober 2025 End-of-Life.

### B2. Keine On-Chain-Approvals — die erste echte Order kann nicht settlen
`polymarket.py:70` ruft `self._init_approvals(False)` auf, und die Funktion steigt sofort aus:
```python
def _init_approvals(self, run: bool = False) -> None:
    if not run:
        return
```
USDC-Allowance und CTF-`setApprovalForAll` werden also nie gesetzt. Für ein frisches Wallet
heißt das: Orders werden nicht handelbar. Das muss einmalig bewusst ausgeführt werden
(und setzt MATIC für Gas voraus — nirgends geprüft).

### B3. Proxy-Wallet wird nicht unterstützt — Guthaben erscheint als 0
`polymarket.py:73-76` erzeugt den Client ohne `signature_type` und ohne `funder`:
```python
ClobClient(self.clob_url, key=self.private_key, chain_id=self.chain_id)
```
Damit wird vom **EOA** des Private Keys gehandelt. Wer über polymarket.com einzahlt, hat das
USDC in einem Proxy-/Safe-Wallet. `get_usdc_balance()` liest den EOA-Saldo ⇒ `0.0` ⇒
`format_trade_prompt_for_execution` liefert `size * 0 = 0` ⇒ Order über 0 USDC.

### B4. Der Trade ist im Repo auskommentiert
`trade.py:59-61` — die Ausführung ist deaktiviert:
```python
# Please refer to TOS before uncommenting: polymarket.com/tos
# trade = self.polymarket.execute_market_order(market, amount)
```
Out of the box passiert also *nichts*. Der gesamte Geldpfad oben ist **ungetestet im Live-Betrieb** —
es gibt keinen Beleg, dass er je Geld bewegt hat.

---

## C. Weitere bestätigte Mängel

- **C1 — Prompt-Bug:** `prompts.py:143` nutzt in einem f-String `{float}` und `{str}`. Ausgegeben wird
  wörtlich: ``I believe ... has a likelihood `<class 'float'>` for outcome of `<class 'str'>`.``
  Das Ausgabeformat ist also kaputt und erklärt die instabilen Antworten aus A3.
- **C2 — Keine Tests:** `tests/test.py` prüft `"foo".upper()`. Null Abdeckung für Orderbau,
  Parsing oder Risikologik.
- **C3 — `cron.py` ist nicht lauffähig:** `class Scheduler` überschreibt den importierten
  `Scheduler`; `self.schedule = Scheduler()` rekursiert endlos. `super()` wird nicht aufgerufen,
  und `self.weekly(...)` existiert nicht.
- **C4 — `build_order()` ignoriert den Preis:** `polymarket.py:321-322` setzt
  `maker_amount = amount`, `taker_amount = 0` — ein Kauf gegen 0 erhaltene Token. Aktuell
  im Live-Pfad unbenutzt, aber eine scharfe Falle.
- **C5 — Keine Paginierung/Limits:** `get_all_markets`/`get_all_events` rufen die Gamma-Endpunkte
  ohne Parameter auf und verarbeiten nur die Default-Seite. `pdb` ist in `polymarket.py:5` importiert.
- **C6 — Kein Risikolimit:** nirgends ein Max-Einsatz, Tageslimit, Mindestliquiditäts-Check,
  Kill-Switch oder Logging der ausgeführten Orders.

---

## Nicht verifizierbar in dieser Umgebung

Die Netzwerk-Policy dieser Session hat `gamma-api.polymarket.com` blockiert (403 beim CONNECT).
Daher **nicht** geprüft: ob die Gamma-API 2026 noch alle Felder liefert, die
`map_api_to_market` fordert (`funded`, `rewardsMinSize`, `rewardsMaxSpread`, `spread`,
`outcomePrices`, `clobTokenIds`). Fehlt eines davon, wirft `map_api_to_market` einen KeyError —
in `get_all_markets` still geschluckt (`except: pass`), im Trade-Pfad dagegen in A7s
Endlosrekursion. Da die Pins von Mitte 2024 sind, ist Drift wahrscheinlich und sollte zuerst
gegen die Live-API geprüft werden.

---

## Minimale Reihenfolge bis "echtes Geld vertretbar"

1. Gamma-API-Felder gegen die Live-API prüfen (B-Vorbedingung, s. o.).
2. `eip712-structs` + `pysha3` aus `requirements.txt` entfernen, `setuptools<81` ergänzen,
   auf Python 3.11+ heben.
3. A1/A2 beheben: Outcome-Token und Seite aus der Modellentscheidung ableiten, nicht hardcoden.
4. A3 beheben: strukturierte Ausgabe (JSON / Structured Output) statt Regex auf Freitext;
   `size` validieren (0 < size ≤ Limit).
5. A4 beheben: explizites Limit mit maximalem Slippage, Mindestliquidität, Mindestordergröße.
6. A5 beheben: `closed`/`archived`/`endDate` auf Markt-Ebene prüfen.
7. A7 beheben: Rekursion durch begrenzte Retry-Schleife mit Backoff ersetzen.
8. B2/B3 klären: Approvals einmalig bewusst ausführen; Proxy-Wallet via `signature_type`/`funder`
   unterstützen oder dokumentieren, dass das USDC im EOA liegen muss.
9. Risikolimits + Order-Logging ergänzen (C6).
10. Erst dann: Trockenlauf mit auskommentierter Order, Logs prüfen — danach ein einzelner
    Live-Trade mit Kleinstbetrag in einem liquiden Markt.

Rechtlicher Hinweis: Die Polymarket-TOS schließen Personen aus den USA und weiteren
Jurisdiktionen vom Handel aus — ausdrücklich auch per API und über Agenten. Das ist der Grund
für das Kommentar an `trade.py:59`.
