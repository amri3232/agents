# Schritt 1 – Validierung & Modellauswahl

Stand: 2026-10-17 · Status: **Vorschlag, wartet auf dein Go**

## Entscheidung in einem Satz

**Digitales Produkt-Bundle in einer klaren Nische, verkauft über eine Merchant-of-Record-Plattform, mit einem kostenlosen Web-Tool als Traffic-Magnet.** Programmatic SEO und Micro-SaaS sind verworfen.

## Die drei Kandidaten

| Kriterium | A) Digitales Produkt + Gratis-Tool | B) Programmatic SEO + Affiliate/Leads | C) Micro-SaaS / API |
|---|---|---|---|
| Time-to-Market | Tage | Wochen bis Monate (Indexierung) | Wochen bis Monate |
| Fixkosten | ~0 € (Gratis-Hosting; Domain optional ~10 €/Jahr) | ~0–20 €/Monat | Hosting + APIs laufend |
| Wartung | sehr gering | gering, aber Rankings schwanken | Support, Bugs, Updates |
| Auslieferung automatisch | ja (Plattform liefert Download) | nicht relevant | ja, aber mit Accounts/Billing |
| Hauptrisiko | Nachfrage und Reichweite | Google-Abstrafung | Kaum Umsatz, Support-Last |
| Urteil | **gewählt** | nur als Traffic-Kanal für A, in klein | verworfen |

### Warum B nicht das Hauptmodell ist
Quellen aus der Recherche berichten, dass Google 2026 (Update im März, Spam-Update im August) massenhaft erzeugte, templatebasierte Seiten stark abgestraft hat. Teils ist von 50–80 % Traffic-Verlust die Rede. Diese Zahlen stammen aus SEO-Blogs, nicht aus Googles Dokumentation. Die Tendenz ist aber über mehrere Quellen konsistent. Ich nutze SEO deshalb nur mit wenigen, handverlesenen Seiten.

### Warum C nicht
Die einzige gefundene Datenbasis nennt einen Median von ca. 145 $ MRR, bei etwa 70 % der Produkte unter 1.000 $ MRR. Das ist ein einzelner Datensatz und nur grob belastbar. Dazu kommen laufende Kosten, Support und Haftung. Das passt nicht zu „minimaler Wartungsaufwand".

## Das gewählte Modell konkret

**Arbeitstitel: Freelancer-Starter-Kit (zunächst Deutschland, deutschsprachig).** Die Nische ist ein Vorschlag und leicht austauschbar. Produktion, Seite und Funnel bleiben bei einem Nischenwechsel wiederverwendbar.

- **Gratis-Tool (Lead-Magnet):** Stundensatz-Rechner für Selbstständige, statisch in HTML/JS, ohne Backend. Er dient als SEO-Magnet für Suchen wie „Stundensatz berechnen Freelancer".
- **Bezahlprodukt (Startpreis 29 €, wird getestet):** Angebots- und Projektvorlagen (Docs/Sheets/PDF), Onboarding-Fragebogen für Kunden, Kalkulations-Sheet, Mail-Textbausteine für Akquise und Nachfassen, 100+ KI-Prompts für Angebote und Kundenkommunikation.
- **Bewusst nicht enthalten:** rechtsverbindliche Verträge sowie Steuer- und Rechnungsvorlagen mit Rechtsanspruch. Das spart Haftungsrisiko (Rechtsdienstleistung, Steuerrecht). Das Kit liefert Textbausteine und Checklisten, keine Rechtsberatung.

### Ablauf

1. Besucher kommt über eine SEO-Seite oder einen Social-Post.
2. Er nutzt den kostenlosen Stundensatz-Rechner.
3. Auf der Ergebnisseite folgt der Hinweis aufs Kit mit Link zur Landingpage.
4. Der Checkout läuft über die Plattform. Sie berechnet und führt die EU-Umsatzsteuer ab (Merchant of Record).
5. Die Auslieferung des Downloads erfolgt automatisch durch die Plattform.
6. Ein E-Mail-Newsletter ist eine optionale Phase 2. Version 1 kommt ohne aus, um Konten und Komplexität zu sparen.

### Warum eine Merchant-of-Record-Plattform statt direkt Stripe
Bei direktem Stripe-Verkauf bist du selbst für EU-Umsatzsteuer (OSS-Verfahren) und die Rechnungsstellung zuständig. Das ist genau der laufende Verwaltungsaufwand, den du vermeiden willst. Eine MoR-Plattform übernimmt das gegen eine höhere Gebühr.

**Gebühren (nicht offiziell geprüft):** Laut Vergleichsblogs liegen Lemon Squeezy und Polar bei ca. 5 % + 0,50 $ pro Verkauf. Gumroad liegt bei ca. 10 % + 0,50 $, und über seinen Marktplatz „Discover" kommen 30 % dazu. Die Angaben widersprechen sich teilweise. Die offiziellen Preisseiten waren aus dieser Umgebung nicht erreichbar. Ich prüfe sie in Schritt 2, bevor wir uns festlegen.

## Ehrliche Einschätzung

- **Automatisierbar:** Produkterstellung, Landingpage, Checkout-Anbindung, Auslieferung und das Erzeugen von Content- und Social-Entwürfen.
- **Nicht automatisierbar und der eigentliche Engpass:** Reichweite. Ohne Traffic gibt es keine Verkäufe, egal wie gut das Produkt ist.
- **Keine Umsatzgarantie.** Die Zahlen aus den Quellen (z. B. 500–5.000 $/Monat für aktive Template-Verkäufer) stammen aus Verkäufer-Ratgebern ohne belegte Daten.
- **Vorschlag für den Test:** 60 Tage, dann entscheiden. Sind es bis dahin kaum Besucher oder keine Verkäufe, wechseln wir die Nische und behalten das Gerüst.

## Was ich von dir brauche (erst ab Schritt 2)

1. **Dein Go** für Modell und Nische, oder ein Gegenvorschlag.
2. **Konten, die nur du anlegen kannst:** eine MoR-Plattform (Lemon Squeezy, Polar oder Gumroad). Fürs Hosting reicht GitHub Pages, dafür ist kein neues Konto nötig.
3. **Impressums-Daten** (Name, Anschrift, E-Mail). Sie sind in Deutschland Pflicht für die Landingpage.
4. **Steuern und Gewerbe:** bitte vorher mit einem Steuerberater klären. Das kann und soll ich nicht ersetzen.
5. **Netzwerkfreigabe:** Die Umgebung sperrt aktuell externe Hosts. Für Schritt 2 müssen die Hosts der gewählten Plattformen freigegeben werden, sonst kann ich Preise nicht prüfen und Setups nicht testen.

## Quellen (Vergleichs- und Ratgeber-Blogs, Eigeninteresse möglich)

- [Gumroad Fees 2026](https://www.wearefounders.uk/gumroad-fees-2026-what-sellers-actually-pay-per-sale/)
- [Payment Processor Fees Compared: Stripe, Polar, Lemon Squeezy, Gumroad](https://userjot.com/blog/stripe-polar-lemon-squeezy-gumroad-transaction-fees)
- [Gumroad vs Payhip vs Lemon Squeezy 2026](https://www.wearefounders.uk/best-platforms-for-selling-digital-products-in-2026/)
- [Best Selling Digital Products on Gumroad (2026 Guide)](https://conversionproplus.com/blog/gumroad-trends-2026-what-s-selling-right-now)
- [Programmatic SEO in 2026: What Google Killed in March](https://alexcloudstar.com/blog/programmatic-seo-2026-indie-hackers/)
- [August 2026 Google Spam Update Case Studies](https://gsqi.com/marketing-blog/august-2026-google-spam-update-case-studies/)
- [Micro-SaaS Revenue Statistics (2026)](https://blog.mean.ceo/?p=11213)
