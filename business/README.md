# Freelancer-Starter-Kit: Business-Ordner

Ein digitales Produkt (Vorlagen, Kalkulation, Prompts) mit kostenlosem Stundensatz-Rechner als Traffic-Magnet. Hintergrund und Modellwahl: [01-modellauswahl.md](01-modellauswahl.md). Was noch von dir kommt: [02-setup-checkliste.md](02-setup-checkliste.md).

Zielmarkt zunächst **Deutschland** (Vorlagen nennen 19 % USt und § 19 UStG).

## Struktur

```
business/
├── config.json            Zentrale Konfiguration (Impressum, Links, Preis)
├── product/               Das Produkt
│   ├── src/               Inhalte: prompts.py, mails.py, onboarding.py
│   ├── build.py           Baut DOCX, XLSX, PDF, CSV und das ZIP
│   └── dist/              Fertiges ZIP (Vorschau-Version) und manifest.json
├── site/                  Die Website (statisch, ohne externe Ressourcen)
│   ├── src/               Seiten, Teilvorlagen, CSS, Rechner-Skript
│   ├── build.mjs          Baut nach site/public/ und prüft Links und Datenschutz-Zusagen
│   └── test/              Unit-Tests für den Rechner
└── preview/               Screenshots der aktuellen Vorschau
```

## Befehle (aus dem Repo-Root)

```bash
python3 business/product/build.py             # Vorschau-Build (Platzhalter erlaubt, ZIP heißt …_VORSCHAU.zip)
python3 business/product/build.py --strict    # Verkaufs-Build, bricht ab, wenn config.json unvollständig ist
node business/site/build.mjs                  # Vorschau-Build der Website
node business/site/build.mjs --strict         # Verkaufs-Build, bricht bei fehlender Konfiguration oder Prüffehlern ab
node --test business/site/test/rechner.test.mjs
```

Anforderungen: Python 3 mit `python-docx`, `openpyxl`; LibreOffice (`soffice`) für PDF und Neuberechnung der Tabelle; Node 22.

## Was die Builds absichern

- **Kein Verkauf mit Platzhaltern:** Strict-Builds scheitern, solange Impressums- oder Checkout-Angaben fehlen.
- **Datenschutz-Zusagen der Seite:** Der Site-Build schlägt an, wenn Seiten externe Skripte, Schriften oder Bilder laden oder wenn Skripte Netzwerkaufrufe enthalten.
- **Links:** Tote interne Links und fehlende Anker werden gemeldet.
- **Rechner = Tabelle:** Der Unit-Test prüft den Stundensatz-Rechner gegen die Excel-Tabelle (59,38 €/h für die Beispielwerte).
- **Mengenangaben** auf der Landingpage (Prompts, Mails, Fragen) kommen aus `product/dist/manifest.json` und können nicht von der Realität abweichen.
