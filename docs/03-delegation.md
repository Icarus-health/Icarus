# Kontrollierte Delegation und Ausführung

> **Status:** aktueller Systemvertrag  
> **Gültig seit:** 2026-08-02  
> **Verbindlich für:** `policy.py`, `agent.py`, `tools.py`, `audit.py`, MCP  
> **Zuletzt gegen den Code geprüft:** 2026-08-02

## Grundsatz

> **Das Modell beantragt. Die Policy entscheidet. Das Werkzeug führt aus.**

Es gibt keinen vorgesehenen direkten Weg vom Modell zu einer Außenwirkung.
Mail, Kalender, Dateien, Web und MCP verwenden dieselbe Policy und dasselbe
Audit-Log.

## Aktionsklassen

| Klasse | Bedeutung | Beispiele |
|---|---|---|
| `read` | verändert nichts | Kalender lesen, Web abrufen, Datei öffnen |
| `write_local` | verändert eigene lokale Daten | Aufgabe, Notiz oder Projekt anlegen |
| `outward` | wirkt bei Dritten oder außerhalb des Systems | Mail senden, Gäste einladen |

Die aktuelle Einteilung ist bewusst klein. Für Browser- und Computersteuerung
wird sie später durch deklarierte Wirkungsmerkmale ergänzt, etwa finanziell,
rechtlich, öffentlich, reversibel oder neuer Empfänger.

## Freigabestufen

| Stufe | Verhalten |
|---|---|
| `auto` | sofort ausführen und protokollieren |
| `notify` | ausführen und sichtbar melden |
| `confirm` | vollständigen Trockenlauf vorlegen |
| `confirm_strict` | Trockenlauf plus Wiederholung des Kerninhalts |
| `deny` | nicht ausführen |

Voreinstellung:

- Lesen: `auto`
- lokale Änderung: `notify`
- Außenwirkung: `confirm_strict`

Eine Freigabe gilt einmal und läuft ab. Dauerregeln müssen später ausdrücklich
benannt, begrenzt und widerrufbar sein.

## Fremde Inhalte

Webseiten, Mails, Kalender, Dateien und importierte Notizen sind Daten, keine
Autorität. Liefert ein Werkzeug fremden Text, gilt die Runde als kontaminiert.
Jede anschließende wirksame Aktion wird angehoben.

Diese Sperre hängt nicht davon ab, ob das Modell eine Prompt Injection erkennt.

## Grenzen aus dem Selbstmodell

Aktive Aussagen der Art `constraint` sind bindend. Sie schlagen Modellwünsche
und Werkzeugvoreinstellungen. Die heutige Prüfung ist absichtlich
deterministisch und wortbasiert; semantische Auslegung harter Verbote wäre
schwer überprüfbar.

## Audit

Jeder Ausgang wird append-only festgehalten:

- ausgeführt,
- abgelehnt,
- verweigert,
- fehlgeschlagen,
- auf Freigabe wartend.

Gespeichert werden Werkzeug, Klasse, Freigabestufe, Parameter, Modell,
Zeitpunkt, Ergebnis und Begründung. Das Audit ist Teil des Produktversprechens,
nicht nur Debugging.

## Bereits angebundene reale Kanäle

- Mail lesen über IMAP,
- Mail senden über SMTP nach Freigabe,
- Kalender lesen und Termine über CalDAV,
- lokale Projekte, Aufgaben, Notizen und Gedächtnis,
- Dateien und Webabruf innerhalb der Sicherheitsgrenzen,
- MCP als zweite Oberfläche auf denselben Kern.

Mail und Kalender sind gegen Fakes getestet; systematische Matrix-Tests gegen
reale Anbieter bleiben offen.

## Geheimnisse

Geheimnisse gelangen nicht in Prompts oder das Selbstmodell. Sie liegen im
Betriebssystem-Schlüsselbund oder, wenn dieser fehlt, in
`schluessel.icarus`, verschlüsselt mit einer außerhalb des Datenverzeichnisses
gehaltenen Passphrase.

## Nächste Erweiterung des Policy-Vertrags

Vor allgemeinem Computer-Use braucht jedes Werkzeug ein maschinenlesbares
Wirkungsmanifest:

- gelesene und veränderte Daten,
- Außenwirkung,
- Rückgängigkeit,
- Empfänger beziehungsweise Ziel,
- finanzieller oder rechtlicher Effekt,
- benötigte Geheimnisse,
- erforderliche Freigabe.

Computer-Use und Browserautomation werden hinter diese Schicht gesetzt, nie
parallel dazu.
