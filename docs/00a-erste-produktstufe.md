# Erste Produktstufe: Icarus als täglicher Chief of Staff

> **Status:** verbindliche Produktfokussierung  
> **Gültig seit:** 2026-08-02  
> **Zuletzt gegen Vision und Code geprüft:** 2026-08-02  
> **Ergänzt:** [`00-produktvision.md`](00-produktvision.md)

Die Vollvision von Icarus ist ein persönliches, langfristiges und
modellunabhängiges KI-Betriebssystem. Dieses Dokument legt fest, welches erste
Produkt daraus entstehen muss, bevor Reichweite, Browsersteuerung,
Multi-Agenten oder mehrere Geräte die Komplexität erhöhen.

## 1. Der erste Nutzer

Die erste Produktstufe richtet sich nicht an „alle Menschen“, sondern an einen
klaren Ausgangspunkt:

> **Stark ausgelastete Wissensarbeiter, Unternehmer und Projektverantwortliche,
> die viele parallele Projekte, Termine, Nachrichten und offene Schleifen
> koordinieren.**

Diese Nutzer haben keinen Mangel an einzelnen Werkzeugen. Ihr Problem ist die
Koordination zwischen Werkzeugen:

- Informationen liegen in Mails, Kalendern, Notizen und Projekten.
- Verpflichtungen verlieren ihren Zusammenhang.
- Vor Terminen muss Kontext zusammengesucht werden.
- Antworten und Entscheidungen bleiben offen.
- Jede Anwendung kennt nur ihren eigenen Ausschnitt.

Icarus soll diese Koordinationsarbeit übernehmen, ohne dafür technisches Wissen
zu verlangen.

## 2. Die drei Kernaufgaben

### 2.1 Den Tag verstehen

Beim Öffnen beantwortet Icarus:

- Was ist heute wichtig?
- Was ist überfällig?
- Welche Frist oder Abhängigkeit ist gefährdet?
- Welche Entscheidung wartet auf mich?
- Was kann ignoriert werden?

Das Ergebnis ist keine Sammlung von Widgets, sondern eine priorisierte,
begründete Tagesübersicht.

### 2.2 Auf Termine vorbereitet sein

Vor einem Termin verbindet Icarus:

- Kalenderereignis,
- beteiligte Personen,
- zugehörige Projekte,
- letzte Kommunikation,
- offene Aufgaben,
- frühere Entscheidungen,
- relevante Dokumente und Risiken.

Der Nutzer soll nicht mehr fünf Programme öffnen müssen, um ein Gespräch zu
verstehen.

### 2.3 Offene Schleifen schließen

Icarus erkennt:

- ausstehende Antworten,
- blockierte Aufgaben,
- fehlende Entscheidungen,
- Zusagen ohne Folgeschritt,
- Projekte ohne nächste Handlung.

Es bereitet die passende Aktion vor. Außenwirksame Schritte bleiben unter der
bestehenden Freigabepolicy.

## 3. Das erste Produkterlebnis

Die Hauptoberfläche der ersten Stufe braucht nur wenige Alltagseinstiege:

1. **Heute** – Prioritäten, Termine, offene Schleifen und vorbereitete Arbeit.
2. **Arbeit** – Projekte, Aufgaben, Entscheidungen und Notizen.
3. **Gespräch** – Fragen, Planung und natürliche Steuerung.
4. **Entscheiden** – Freigaben, Gedächtnisvorschläge und ungeklärte Konflikte.

Rohmaterial, Audit, Modellanbieter, technische Konnektoren und Speicherzustände
bleiben erreichbar, gehören aber nicht in die erste Navigationsebene.

## 4. Onboarding

Der erste Start fragt zuerst nach dem gewünschten Nutzen:

> Wobei soll Icarus dir zuerst helfen?

Mögliche Antworten:

- meinen Arbeitstag ordnen,
- Projekte und Aufgaben zusammenhalten,
- Termine vorbereiten,
- offene Nachrichten und Zusagen verfolgen.

Erst danach werden genau die Verbindungen angeboten, die für diesen Nutzen
notwendig sind. Jeder Schritt bleibt überspringbar.

Der erste erkennbare Nutzen muss innerhalb weniger Minuten entstehen. Eine
Installation, die erst nach umfangreicher Konfiguration sinnvoll wird, verfehlt
das Produktziel.

## 5. Nicht-Ziele der ersten Stufe

Noch nicht ausschlaggebend sind:

- möglichst viele Modelle,
- möglichst viele Konnektoren,
- allgemeines Computer-Use,
- ein sichtbares Multi-Agenten-Organigramm,
- mobile Vollständigkeit,
- ein dekorativer Wissensgraph ohne belastbare Semantik.

Diese Fähigkeiten bleiben Teil der Vollvision, werden aber erst ausgebaut, wenn
der tägliche Chief-of-Staff-Kreislauf nachweislich trägt.

## 6. Messbare Erfolgskriterien

Die erste Produktstufe gilt als erfolgreich, wenn reale Nutzer:

- Icarus freiwillig an mindestens vier Arbeitstagen pro Woche öffnen,
- den Tagesüberblick überwiegend als relevant bewerten,
- vor Terminen weniger manuell suchen,
- offene Schleifen früher erkennen,
- weniger als eine unnötige Freigabe pro sinnvoll ausgeführter Aktion erleben,
- Gedächtnisvorschläge zuverlässig verstehen und korrigieren können,
- eine vollständige Sicherung wiederherstellen können.

Zusätzliche technische Kennzahlen:

- Zeit bis zum ersten Nutzen,
- Anteil angenommener gegenüber verworfenen Hinweisen,
- falsche oder unbelegte Gedächtnisvorschläge,
- Wiederherstellungsquote im automatisierten Roundtrip,
- Anteil erfolgreicher Connector-Läufe,
- Fehlerrate je Kernablauf.

## 7. Priorisierungsregel

Eine neue Funktion kommt vor der ersten Produktstufe nur dann, wenn sie
mindestens eine der drei Kernaufgaben messbar verbessert und dabei:

- keine zweite Wahrheit neben dem Kern erzeugt,
- keine technische Pflichtentscheidung an den Nutzer weitergibt,
- Herkunft, Sicherheit und Rückgängigkeit erhält,
- durch einen realen Nutzerablauf geprüft werden kann.

Der erste Sieg von Icarus ist nicht, alles Digitale bedienen zu können. Der
erste Sieg ist, dass ein Mensch seinen Arbeitstag nicht mehr selbst aus fünf
Programmen zusammensetzen muss.
