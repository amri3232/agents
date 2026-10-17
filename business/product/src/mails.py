# -*- coding: utf-8 -*-
"""Mail-Textbausteine. Platzhalter in [ECKIGEN KLAMMERN]."""

# (Gruppe, Titel, Wann nutzen, Betreff, Text)
MAILS = [
    # --- Akquise ---
    ("Akquise", "Erstkontakt – mit konkretem Anlass",
     "Du hast einen echten Anknüpfungspunkt (Beitrag, Projekt, Stellenausschreibung, Empfehlung).",
     "[ANKNÜPFUNGSPUNKT] – eine Idee für [UNTERNEHMEN]",
     "Hallo [VORNAME],\n\n"
     "ich bin auf [ANKNÜPFUNGSPUNKT] gestoßen und habe mich gefragt, wie ihr [KONKRETES THEMA] angeht.\n\n"
     "Ich unterstütze [ZIELGRUPPE] bei [LEISTUNG in einem Satz]. Bei [BEISPIEL-KUNDE ODER -SITUATION] hat das zu [ERGEBNIS] geführt.\n\n"
     "Wäre es für euch hilfreich, wenn ich dir in 3 Sätzen skizziere, was ich bei [UNTERNEHMEN] als ersten Schritt sehen würde?\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Akquise", "Erstkontakt – über eine Empfehlung",
     "Eine gemeinsame Kontaktperson hat dich genannt (und der Name darf verwendet werden).",
     "Empfehlung von [NAME DER EMPFEHLENDEN PERSON]",
     "Hallo [VORNAME],\n\n"
     "[NAME DER EMPFEHLENDEN PERSON] hat mich auf dich aufmerksam gemacht, weil du gerade [THEMA / BEDARF] angehst.\n\n"
     "Kurz zu mir: Ich bin [BERUF] und helfe [ZIELGRUPPE] bei [LEISTUNG]. Wenn es für dich passt, erzähle ich dir in 15 Minuten, wie das konkret aussehen könnte, und du entscheidest, ob es sich lohnt, weiterzusprechen.\n\n"
     "Hättest du diese oder nächste Woche Zeit für ein kurzes Telefonat?\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Akquise", "Reaktivierung eines alten Kontakts",
     "Ehemaliger Kunde oder Kontakt, mit dem länger nicht gesprochen wurde.",
     "Lange nichts gehört – wie läuft [PROJEKT / THEMA]?",
     "Hallo [VORNAME],\n\n"
     "wir haben zuletzt zu [PROJEKT / THEMA] zusammengearbeitet, und ich habe an dich gedacht, weil [AKTUELLER ANLASS].\n\n"
     "Ich habe aktuell wieder Kapazität für [ART VON AUFTRÄGEN]. Falls bei dir etwas ansteht, melde dich gern. Und falls nicht, freue ich mich trotzdem über ein Lebenszeichen.\n\n"
     "Beste Grüße\n[DEIN NAME]"),
    # --- Angebot ---
    ("Angebot", "Rückfragen vor dem Angebot",
     "Anfrage ist zu vage für ein belastbares Angebot.",
     "Rückfragen zu deiner Anfrage [PROJEKT]",
     "Hallo [VORNAME],\n\n"
     "danke für deine Anfrage zu [PROJEKT]. Damit ich dir ein passendes Angebot machen kann, brauche ich noch ein paar Angaben:\n\n"
     "1. Was soll am Ende erreicht sein (Ziel / Ergebnis)?\n"
     "2. Bis wann brauchst du das Ergebnis, und gibt es feste Termine?\n"
     "3. Gibt es einen Budgetrahmen, an dem ich mich orientieren soll?\n"
     "4. Wer entscheidet über die Beauftragung, und wer gibt Feedback?\n"
     "5. Welche Unterlagen oder Zugänge sind vorhanden?\n\n"
     "Wenn dir das lieber im Gespräch ist, können wir auch 20 Minuten telefonieren: [TERMINVORSCHLÄGE].\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Angebot", "Angebot versenden",
     "Du schickst das fertige Angebot.",
     "Angebot [PROJEKT] – [ANGEBOTSNUMMER]",
     "Hallo [VORNAME],\n\n"
     "danke für das Gespräch am [DATUM]. Im Anhang findest du mein Angebot für [PROJEKT].\n\n"
     "Das Wichtigste in Kürze:\n"
     "• Ziel: [ZIEL]\n"
     "• Umfang: [KURZE ZUSAMMENFASSUNG]\n"
     "• Zeitrahmen: [ZEITRAHMEN]\n"
     "• Investition: [PREIS] [netto / zzgl. USt]\n\n"
     "Das Angebot gilt bis [DATUM]. Wenn du Fragen hast oder etwas anpassen möchtest, melde dich gern. Ich schlage vor, dass wir am [DATUM] kurz darüber sprechen.\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Angebot", "Nachfassen nach dem Angebot (1. Nachfrage)",
     "5 bis 7 Werktage nach dem Angebot ohne Antwort.",
     "Kurze Nachfrage zu meinem Angebot [PROJEKT]",
     "Hallo [VORNAME],\n\n"
     "ich wollte kurz nachfragen, ob du mein Angebot für [PROJEKT] erhalten hast und ob noch Fragen offen sind.\n\n"
     "Falls es sich anbietet, können wir den Umfang gern noch anpassen. Sag mir einfach, was dir wichtig ist.\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Angebot", "Nachfassen mit Mehrwert (2. Nachfrage)",
     "Zweite Nachfrage nach etwa zwei weiteren Wochen, mit einem hilfreichen Impuls.",
     "[THEMA]: eine Idee zu [PROJEKT]",
     "Hallo [VORNAME],\n\n"
     "beim Nachdenken über [PROJEKT] ist mir eingefallen: [KONKRETER, HILFREICHER IMPULS IN 2–3 SÄTZEN, ZUM BEISPIEL EINE ERFAHRUNG, EIN LINK, EINE ABKÜRZUNG].\n\n"
     "Falls das Projekt aktuell hinten ansteht, ist das völlig in Ordnung. Sag mir einfach Bescheid, ab wann es für dich wieder relevant wird.\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Angebot", "Abschluss-Nachfrage (3. Nachfrage)",
     "Letzte, freundliche Nachricht, um die Sache sauber zu beenden.",
     "Soll ich das Angebot [PROJEKT] ablegen?",
     "Hallo [VORNAME],\n\n"
     "ich habe von dir zu meinem Angebot für [PROJEKT] nichts mehr gehört und gehe davon aus, dass sich die Prioritäten verschoben haben. Das ist völlig in Ordnung.\n\n"
     "Ich lege das Angebot jetzt ab. Falls das Thema später wieder aktuell wird, melde dich gern jederzeit.\n\n"
     "Alles Gute\n[DEIN NAME]"),
    ("Angebot", "Auftrag annehmen – Bestätigung und nächste Schritte",
     "Der Kunde hat zugesagt.",
     "Danke für den Auftrag – so geht es weiter",
     "Hallo [VORNAME],\n\n"
     "vielen Dank für dein Vertrauen. Ich freue mich auf die Zusammenarbeit an [PROJEKT].\n\n"
     "So geht es weiter:\n"
     "1. [NÄCHSTER SCHRITT, ZUM BEISPIEL: Du schickst mir die Unterschrift / Auftragsbestätigung bis [DATUM]]\n"
     "2. Kickoff-Termin: [TERMINVORSCHLÄGE]\n"
     "3. Ich brauche von dir bis [DATUM]: [MATERIAL / ZUGÄNGE] (siehe Anhang)\n\n"
     "Startdatum: [DATUM]. Bei Fragen melde dich jederzeit.\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    # --- Projekt ---
    ("Projekt", "Terminbestätigung",
     "Nach der Vereinbarung eines Gesprächs.",
     "Bestätigung: [TERMIN] am [DATUM], [UHRZEIT]",
     "Hallo [VORNAME],\n\n"
     "ich bestätige unseren Termin am [DATUM] um [UHRZEIT] ([ORT / LINK]).\n\n"
     "Ziel des Gesprächs: [ZIEL].\n"
     "Bitte bereite vor: [VORBEREITUNG].\n\n"
     "Bis dann!\n[DEIN NAME]"),
    ("Projekt", "Gesprächszusammenfassung",
     "Direkt nach einem Kundentermin.",
     "Zusammenfassung unseres Gesprächs vom [DATUM]",
     "Hallo [VORNAME],\n\n"
     "danke für das Gespräch. Damit wir dasselbe Bild haben, hier die Zusammenfassung:\n\n"
     "Entschieden:\n• [ENTSCHEIDUNG 1]\n• [ENTSCHEIDUNG 2]\n\n"
     "Offen:\n• [OFFENE FRAGE] – bei [WER] bis [DATUM]\n\n"
     "Nächste Schritte:\n• [AUFGABE] – [WER] bis [DATUM]\n\n"
     "Falls ich etwas falsch verstanden habe, gib mir bitte kurz Bescheid.\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Projekt", "Statusupdate",
     "Regelmäßig, zum Beispiel wöchentlich oder zu Meilensteinen.",
     "Status [PROJEKT] – KW [NUMMER]",
     "Hallo [VORNAME],\n\n"
     "kurzer Stand zu [PROJEKT]:\n\n"
     "Gesamtstatus: [GRÜN / GELB / ROT]\n\n"
     "Erledigt:\n• [PUNKT]\n\n"
     "Als Nächstes:\n• [PUNKT] (bis [DATUM])\n\n"
     "Von dir brauche ich:\n• [PUNKT] bis [DATUM]\n\n"
     "Risiken / Hinweise: [PUNKT ODER „keine“]\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Projekt", "Feedback anfordern (Zwischenergebnis)",
     "Du lieferst einen Entwurf oder Zwischenstand und brauchst gezielte Rückmeldung.",
     "Zwischenstand [PROJEKT] – bitte um Feedback bis [DATUM]",
     "Hallo [VORNAME],\n\n"
     "anbei der Zwischenstand zu [PROJEKT]. Für mich sind diese drei Fragen am wichtigsten:\n\n"
     "1. [FRAGE 1]\n2. [FRAGE 2]\n3. [FRAGE 3]\n\n"
     "Bitte gib mir bis [DATUM] Rückmeldung, damit wir den Zeitplan halten können. Die [ANZAHL]. von [GESAMTZAHL] vereinbarten Korrekturrunden beginnt damit.\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Projekt", "Zusatzwunsch außerhalb des Umfangs",
     "Der Kunde wünscht etwas, das nicht im Angebot steht.",
     "Dein Zusatzwunsch zu [PROJEKT]",
     "Hallo [VORNAME],\n\n"
     "danke für den Hinweis, [ZUSATZWUNSCH] ist eine sinnvolle Idee. Das war im ursprünglichen Angebot allerdings nicht enthalten.\n\n"
     "Du hast zwei Möglichkeiten:\n"
     "A) Ich nehme es als Erweiterung auf: Aufwand ca. [STUNDEN / TAGE], Mehrkosten [BETRAG], Auswirkung auf den Termin: [AUSWIRKUNG].\n"
     "B) Wir tauschen es gegen [ENTFALLENDER PUNKT] aus, ohne Mehrkosten.\n\n"
     "Sag mir, wie du es haben möchtest, dann halte ich es schriftlich fest.\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Projekt", "Verzögerung beim Kunden",
     "Material oder Feedback fehlt, und der Zeitplan wackelt.",
     "[PROJEKT]: Wir warten auf [MATERIAL / FEEDBACK]",
     "Hallo [VORNAME],\n\n"
     "für den nächsten Schritt bei [PROJEKT] fehlt mir noch [MATERIAL / FEEDBACK], vereinbart war es bis [DATUM].\n\n"
     "Wenn ich es bis [NEUES DATUM] bekomme, kann ich den Termin [TERMIN] halten. Darüber hinaus würde sich die Lieferung auf [NEUER TERMIN] verschieben.\n\n"
     "Kann ich dir helfen, damit es schneller geht? Zum Beispiel mit einem kurzen Telefonat?\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Projekt", "Eigene Verzögerung offen kommunizieren",
     "Du wirst einen Termin nicht halten.",
     "Verschiebung: [PROJEKT] kommt am [NEUER TERMIN]",
     "Hallo [VORNAME],\n\n"
     "ich muss dir leider mitteilen, dass ich [ERGEBNIS] nicht wie vereinbart am [ALTER TERMIN], sondern erst am [NEUER TERMIN] liefern kann. Grund: [KURZER, EHRLICHER GRUND].\n\n"
     "Was ich tue, damit es nicht noch länger dauert: [MASSNAHME].\n"
     "Was das für dich bedeutet: [AUSWIRKUNG, ZUM BEISPIEL: nichts weiter / ein Teilergebnis liegt schon am [DATUM] vor].\n\n"
     "Danke für dein Verständnis. Bei Fragen rufe ich dich gern an.\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Projekt", "Projektabschluss und Übergabe",
     "Das Projekt ist geliefert.",
     "Abschluss [PROJEKT] – Übergabe und nächste Schritte",
     "Hallo [VORNAME],\n\n"
     "[PROJEKT] ist abgeschlossen. Alles Wichtige im Überblick:\n\n"
     "• Geliefert: [ERGEBNISSE]\n"
     "• Dateien / Zugänge: [ORT]\n"
     "• Bitte bei dir prüfen: [PUNKTE] bis [DATUM]\n"
     "• Nicht Teil des Auftrags (aber möglich): [OPTIONALE FOLGELEISTUNGEN]\n\n"
     "Danke für die gute Zusammenarbeit. Wenn du magst, gib mir kurz Feedback: Was lief gut, was könnte ich besser machen?\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    # --- Geld ---
    ("Geld", "Rechnung versenden",
     "Rechnung geht raus.",
     "Rechnung [RECHNUNGSNUMMER] zu [PROJEKT]",
     "Hallo [VORNAME],\n\n"
     "anbei die Rechnung [RECHNUNGSNUMMER] über [BETRAG] für [LEISTUNG].\n\n"
     "Zahlungsziel: [DATUM]. Die Bankverbindung findest du auf der Rechnung.\n\n"
     "Bei Fragen melde dich gern. Danke für die Zusammenarbeit!\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Geld", "Zahlungserinnerung – freundlich",
     "Rechnung ist wenige Tage überfällig.",
     "Zahlungserinnerung: Rechnung [RECHNUNGSNUMMER]",
     "Hallo [VORNAME],\n\n"
     "bei der Durchsicht meiner Buchhaltung ist mir aufgefallen, dass die Rechnung [RECHNUNGSNUMMER] vom [DATUM] über [BETRAG] noch offen ist. Fällig war sie am [DATUM].\n\n"
     "Vielleicht ist sie im Alltag untergegangen. Könntest du die Zahlung bitte bis [DATUM] veranlassen? Falls es Rückfragen zur Rechnung gibt, melde dich gern.\n\n"
     "Danke und viele Grüße\n[DEIN NAME]"),
    ("Geld", "Zahlungserinnerung – bestimmt",
     "Die freundliche Erinnerung blieb ohne Reaktion.",
     "2. Erinnerung: Rechnung [RECHNUNGSNUMMER], bitte bis [DATUM]",
     "Hallo [VORNAME],\n\n"
     "zu meiner Erinnerung vom [DATUM] habe ich leider keine Rückmeldung erhalten. Die Rechnung [RECHNUNGSNUMMER] über [BETRAG] ist weiterhin offen.\n\n"
     "Bitte überweise den Betrag bis spätestens [DATUM]. Sollte es einen Grund für die Verzögerung geben, lass es mich bitte wissen, dann finden wir eine Lösung.\n\n"
     "Wenn bis dahin keine Zahlung eingeht, prüfe ich die weiteren Schritte.\n\n"
     "Viele Grüße\n[DEIN NAME]\n\n"
     "(Hinweis für dich: Mahnstufen, Verzugszinsen und Gebühren richten sich nach Vertrag und Gesetz. Bei Unsicherheit bitte rechtlich prüfen lassen.)"),
    ("Geld", "Preiserhöhung bei Bestandskunden",
     "Du erhöhst deine Preise für laufende Kunden.",
     "Anpassung meiner Preise ab [DATUM]",
     "Hallo [VORNAME],\n\n"
     "seit [JAHR] arbeiten wir zusammen, und ich danke dir für das Vertrauen.\n\n"
     "Ab dem [DATUM] passe ich meine Preise an: Mein Stundensatz steigt von [ALT] auf [NEU] ([PROZENT] Prozent). Der Grund: [KURZE, EHRLICHE BEGRÜNDUNG, ZUM BEISPIEL gestiegene Kosten und mehr Erfahrung].\n\n"
     "Für bereits beauftragte Projekte gilt weiterhin der bisherige Preis: [REGELUNG]. Wenn du dazu Fragen hast, sprechen wir gern kurz.\n\n"
     "Viele Grüße\n[DEIN NAME]\n\n"
     "(Hinweis für dich: Prüfe vorab, ob bestehende Verträge Ankündigungsfristen vorsehen.)"),
    # --- Beziehung ---
    ("Beziehung", "Um eine Kundenstimme bitten",
     "Nach erfolgreichem Projekt, wenn der Kunde zufrieden war.",
     "Darf ich dich um zwei Sätze bitten?",
     "Hallo [VORNAME],\n\n"
     "es hat mir viel Freude gemacht, [PROJEKT] mit dir umzusetzen. Wenn du magst, würde ich mich über eine kurze Rückmeldung freuen, die ich auf meiner Website verwenden darf, nur mit deiner ausdrücklichen Zustimmung.\n\n"
     "Dazu drei Leitfragen:\n"
     "1. Was war die Ausgangssituation?\n2. Was hat sich durch die Zusammenarbeit verändert?\n3. Was würdest du anderen sagen, die über eine Zusammenarbeit nachdenken?\n\n"
     "Ehrliche Kritik ist ebenso willkommen. Danke dir!\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Beziehung", "Um eine Empfehlung bitten",
     "Zufriedener Kunde, der passende Kontakte haben könnte.",
     "Kennst du jemanden, dem ich helfen kann?",
     "Hallo [VORNAME],\n\n"
     "danke nochmals für die gute Zusammenarbeit. Ich suche aktuell weitere Kunden wie dich: [BESCHREIBUNG DES IDEALEN KUNDEN].\n\n"
     "Falls dir jemand einfällt, der gerade [PROBLEM] hat, würde ich mich über eine Vorstellung freuen. Wenn es hilft, formuliere ich dir zwei Sätze zum Weiterleiten.\n\n"
     "Kein Druck, falls dir spontan niemand einfällt.\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Beziehung", "Auftrag ablehnen (Kapazität)",
     "Du hast keine Zeit für den Auftrag.",
     "Deine Anfrage [PROJEKT]",
     "Hallo [VORNAME],\n\n"
     "danke für dein Vertrauen und die Anfrage zu [PROJEKT]. Leider bin ich bis [DATUM] voll ausgelastet und könnte das Projekt nicht in der Qualität umsetzen, die ich mir selbst zum Anspruch mache.\n\n"
     "Falls der Zeitpunkt [NACH DATUM] für dich passt, melde dich gern wieder.\n\n"
     "Viele Grüße\n[DEIN NAME]"),
    ("Beziehung", "Retainer anbieten",
     "Der Kunde braucht regelmäßig Unterstützung.",
     "Vorschlag: laufende Zusammenarbeit statt Einzelaufträge",
     "Hallo [VORNAME],\n\n"
     "in den letzten Monaten haben wir mehrere kleinere Aufgaben zu [THEMA] umgesetzt. Ich schlage vor, das zu bündeln:\n\n"
     "• Monatliches Kontingent: [STUNDEN] Stunden\n"
     "• Reaktionszeit: [ZEIT]\n"
     "• Inhalt: [LEISTUNGEN]\n"
     "• Preis: [BETRAG] pro Monat\n"
     "• Laufzeit / Kündigung: [REGELUNG]\n\n"
     "Vorteil für dich: feste Ansprechperson, schnellere Reaktion, planbare Kosten. Wenn du magst, besprechen wir die Details in einem kurzen Telefonat.\n\n"
     "Viele Grüße\n[DEIN NAME]"),
]


def groups():
    seen = []
    for g, *_ in MAILS:
        if g not in seen:
            seen.append(g)
    return seen


if __name__ == "__main__":
    print(len(MAILS), "Mail-Bausteine")
    for g in groups():
        print(" ", g, sum(1 for m in MAILS if m[0] == g))
