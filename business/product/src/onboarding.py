# -*- coding: utf-8 -*-
"""Fragen für den Kunden-Onboarding-Fragebogen."""

SECTIONS = [
    ("1. Zu Ihrem Unternehmen", [
        "Wie heißt Ihr Unternehmen, und was genau bieten Sie an?",
        "Wer ist bei Ihnen Ansprechperson für dieses Projekt (Name, Rolle, Erreichbarkeit)?",
        "Wer entscheidet am Ende über Freigaben, und wer muss sonst noch einbezogen werden?",
        "Wie viele Personen arbeiten im Unternehmen, und in welcher Branche sind Sie tätig?",
        "Was unterscheidet Sie von Mitbewerbern, aus Ihrer Sicht und aus Sicht Ihrer Kunden?",
    ]),
    ("2. Ziel des Projekts", [
        "Was soll sich durch dieses Projekt konkret verändern?",
        "Woran erkennen Sie nach dem Projekt, dass es erfolgreich war (zum Beispiel Zahlen, Rückmeldungen, gesparte Zeit)?",
        "Warum ist dieses Projekt gerade jetzt wichtig?",
        "Was wurde bisher unternommen, und was hat dabei nicht funktioniert?",
        "Gibt es Vorstellungen oder Beispiele, die Sie gut finden (Links, Dokumente)? Und was gefällt Ihnen ausdrücklich nicht?",
        "Was darf auf keinen Fall passieren?",
    ]),
    ("3. Zielgruppe", [
        "Wer sind Ihre wichtigsten Kunden oder Nutzer (Branche, Rolle, Alter, Vorwissen)?",
        "Welche Probleme haben diese Menschen, die Sie lösen?",
        "Welche Fragen stellen Kundinnen und Kunden vor einem Kauf am häufigsten?",
        "Welche Sprache und Anrede nutzen Sie gegenüber Ihrer Zielgruppe (Du oder Sie, locker oder förmlich)?",
    ]),
    ("4. Umfang und Inhalte", [
        "Was genau soll geliefert werden (Ergebnisse, Formate, Mengen)?",
        "Welche Inhalte, Texte, Bilder, Daten oder Unterlagen stellen Sie bereit, und bis wann?",
        "Was ist ausdrücklich nicht Teil des Projekts?",
        "Gibt es bestehende Vorgaben (Corporate Design, Tonalität, Richtlinien, Datenschutzvorgaben)?",
        "Welche Teile sind unbedingt nötig („Muss“) und welche wünschenswert („Kann“)?",
    ]),
    ("5. Zeitplan und Budget", [
        "Bis wann soll das Ergebnis vorliegen, und gibt es feste Termine, die nicht verschoben werden können?",
        "Was steckt hinter dem Termin (Messe, Kampagne, Budgetjahr)?",
        "Wie schnell können Sie bei Rückfragen und Freigaben reagieren (in Werktagen)?",
        "Welchen Budgetrahmen haben Sie vorgesehen?",
        "Gibt es interne Abstimmungsschleifen, die wir im Zeitplan berücksichtigen sollten?",
    ]),
    ("6. Zusammenarbeit", [
        "Welchen Kommunikationsweg bevorzugen Sie (E-Mail, Telefon, Chat, Videocall)?",
        "Wie oft möchten Sie über den Stand informiert werden?",
        "Gibt es Zeiten, zu denen Sie nicht erreichbar sind (Urlaub, Messen, Betriebsferien)?",
        "Wie sieht aus Ihrer Sicht eine gute Zusammenarbeit aus? Was war bei früheren Dienstleistern gut oder schlecht?",
        "Gibt es Vertraulichkeitsanforderungen, die ich beachten muss?",
    ]),
    ("7. Technik und Zugänge", [
        "Welche Systeme, Tools oder Plattformen sind betroffen (Name, Version, wer verwaltet sie)?",
        "Welche Zugänge benötige ich, und wer richtet sie ein?",
        "Gibt es technische Einschränkungen oder interne IT-Vorgaben?",
        "Wer ist technischer Ansprechpartner bei Rückfragen?",
    ]),
]


def count():
    return sum(len(q) for _, q in SECTIONS)


if __name__ == "__main__":
    print(count(), "Fragen")
