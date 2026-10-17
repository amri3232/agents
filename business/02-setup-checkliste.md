# Setup-Checkliste: Was nur du tun kannst

Stand: 2026-10-17. Alles, was ich ohne Konten, Zahlungsdaten und persönliche Angaben bauen konnte, ist fertig. Hier steht der Rest, in der Reihenfolge, in der er Sinn ergibt.

**Hinweis zur Netzwerkfreigabe:** Die Umgebung blockiert aktuell `lemonsqueezy.com`, `polar.sh` und `gumroad.com`. Die Freigabe ändert nur die Netzwerkeinstellung der Cloud-Umgebung (Titelleiste der Session → Umgebung → Edit → Network access). Ohne sie kann ich Preisseiten nicht prüfen. Für den Rest dieser Liste brauche ich sie nicht.

## 1. Rechtliche und steuerliche Grundlagen (vor dem ersten Verkauf)

- [ ] **Gewerbe bzw. Steuernummer klären.** Wer regelmäßig verkauft, muss das beim Finanzamt anmelden (Fragebogen zur steuerlichen Erfassung) und ggf. ein Gewerbe anmelden. Das klärst du mit einem Steuerberater oder beim Finanzamt, ich kann das nicht ersetzen.
- [ ] **Umsatzsteuer-Status festlegen** (Kleinunternehmer ja/nein). Bei Verkäufen über eine Merchant-of-Record-Plattform gelten Sonderregeln. Auch das gehört zum Steuerberater.
- [ ] **Impressum prüfen.** Es braucht eine ladungsfähige Anschrift (kein Postfach) und eine E-Mail-Adresse. Wenn du deine Privatadresse nicht veröffentlichen willst, gibt es Impressum-Services mit Geschäftsadresse. Kläre das vor dem Livegang.
- [ ] **Datenschutzerklärung prüfen** (`site/src/datenschutz/index.html`). Sie ist ein von mir verfasster Entwurf und kein geprüftes Rechtsdokument. Sie stimmt mit dem Code überein (keine Cookies, keine Drittanbieter-Ressourcen), das erzwingt der Build. Eine anwaltliche oder Generator-Prüfung ist trotzdem sinnvoll.
- [ ] **Nutzungsbedingungen im Kit prüfen** (`product/build.py`, Abschnitt „Nutzungsrechte“ in `00_START-HIER.pdf`). Das ist mein Entwurf einer einfachen Lizenz. Passe sie an, wenn du es anders willst.
- [ ] **Satz zur Verbraucherstreitbeilegung im Impressum** („nicht bereit und nicht verpflichtet“) ist eine Entscheidung von dir. Ändere sie, wenn du teilnehmen möchtest.

## 2. Konfiguration ausfüllen

Trage alles in `business/config.json` ein, oder schreib mir die Werte hier in den Chat. Achtung: Die Datei wird committet. Ist das Repo öffentlich, sind die Angaben öffentlich. Für Impressumsdaten ist das ohnehin der Zweck.

| Feld | Bedeutung |
|---|---|
| `SELLER_NAME`, `SELLER_STREET`, `SELLER_ZIP_CITY` | Anbieterkennzeichnung für Impressum, Datenschutz, Kit-PDF |
| `CONTACT_EMAIL` | Support- und Impressums-E-Mail |
| `CONTACT_PHONE`, `USTID` | optional, erscheinen nur, wenn gefüllt |
| `SITE_URL` | Endadresse der Website, `https://…` ohne Slash am Ende |
| `CHECKOUT_URL` | Checkout-Link deines Produkts (Schritt 3) |
| `CHECKOUT_PROVIDER_NAME`, `CHECKOUT_PRIVACY_URL` | Zahlungsanbieter und dessen Datenschutzseite |
| `HOSTING_NAME`, `HOSTING_PRIVACY_URL` | Hoster der Website und dessen Datenschutzseite |

Sobald die Datei vollständig ist, führe ich die beiden Verkaufs-Builds aus (`--strict`). Sie brechen ab, solange etwas fehlt oder ein Link tot ist.

## 3. Zahlungsplattform (Merchant of Record)

Ich empfehle eine Merchant-of-Record-Plattform, damit du EU-Umsatzsteuer und Rechnungsstellung gegenüber Kunden nicht selbst abwickelst. Kandidaten laut Recherche: Lemon Squeezy, Polar, Gumroad. **Die Gebühren in Blogs widersprechen sich, ich konnte keine offizielle Preisseite prüfen.** Prüfe vor der Anmeldung selbst:

- [ ] Transaktionsgebühr laut **offizieller** Preisseite (plus Gebühren für internationale Karten, PayPal, Auszahlung)
- [ ] Auszahlung auf ein deutsches Konto möglich (SEPA, PayPal oder Stripe)
- [ ] Preisanzeige **inklusive** Umsatzsteuer für Verbraucher in der EU (die Seite behauptet „29 € inkl. MwSt.“)
- [ ] Versand des Download-Links per E-Mail nach dem Kauf (die FAQ behauptet das)

Dann:

- [ ] Konto anlegen und Auszahlungsdaten hinterlegen (nur du kannst das)
- [ ] Produkt anlegen: Name „Freelancer-Starter-Kit“, Preis 29 €, Datei = `product/dist/Freelancer-Starter-Kit_v1.0.zip` (nach dem Strict-Build, **nicht** die `_VORSCHAU`-Datei)
- [ ] Rückerstattungsregel entscheiden und in der Plattform eintragen
- [ ] **Testkauf** durchführen (die meisten Plattformen bieten einen Testmodus): Kommt die Datei an? Öffnen sich alle Dateien? Stimmt die Rechnung?
- [ ] Checkout-Link in `CHECKOUT_URL` eintragen

Der Stripe-Connector ist in dieser Session inzwischen verfügbar. Ich habe ihn bewusst nicht genutzt: Direkt über Stripe wärst du selbst für Umsatzsteuer (OSS) und Rechnungen zuständig.

## 4. Hosting der Website

Die Seite besteht aus statischen Dateien (`business/site/public/`) und braucht keinen Server. Einfachste Wege ohne Programmieren:

- **Cloudflare Pages** (Direct Upload), **Netlify** (Drag & Drop des Ordners) oder **GitHub Pages**.
- [ ] Konto beim gewählten Hoster anlegen und mir den Namen für `HOSTING_NAME` nennen.
- [ ] Nach dem Livegang im Browser (Entwicklertools → Anwendung → Cookies) prüfen, dass **keine Cookies** gesetzt werden. Die Datenschutzerklärung behauptet das. Manche Hoster setzen eigene Cookies.
- [ ] Eigene Domain optional (~10 €/Jahr). Ohne Domain nutzt du die Adresse des Hosters.

## 5. Vor dem Livegang: Selbsttest

- [ ] Landingpage auf dem Handy öffnen: Texte lesbar, Kaufen-Button führt zum richtigen Checkout?
- [ ] Impressum und Datenschutz auf der echten Seite lesen: Alle Angaben korrekt?
- [ ] Stundensatz-Rechner mit eigenen Zahlen testen.
- [ ] Alle sieben Dateien aus dem gekauften ZIP in Word/Excel öffnen.

## Ablauf, sobald du fertig bist

1. Du trägst die Werte ein (oder schickst sie mir).
2. Ich baue beide Strict-Builds und liefere dir das fertige Kit-ZIP und die Website als ZIP.
3. Du lädst das Kit-ZIP bei der Zahlungsplattform hoch und die Website beim Hoster.
4. Ich bereite parallel Schritt 4 vor: SEO-Seiten und Social-Media-Entwürfe.
