# Connectoren, Browser und dauerhafte Workflows

## Grundsatz

Ein Connector erweitert Reichweite, nicht Vertrauen. Jede Operation wird zu einem normalen Icarus-Werkzeug und läuft ausschließlich über `Agent.invoke()`, Policy, Trockenlauf, Einmalfreigabe und Audit.

Die Workflow-Laufzeit erhält im Produktionsweg nur `Agent.invoke` als Gateway. Sie kennt keine Werkzeugfunktionen und kann `Tool.run` nicht direkt aufrufen.

## Wirkungsmanifest

Jede Operation deklariert maschinenlesbar:

- gelesene Daten
- veränderte Daten
- Löschungen
- Empfängerfelder
- Sichtbarkeit: privat, benannte Empfänger, Organisation oder öffentlich
- Umkehrbarkeit
- finanzielle Wirkung
- rechtliche Wirkung
- Veröffentlichung
- Upload oder Download
- benötigte Geheimnisfähigkeiten
- ob die Antwort fremden Inhalt enthält

Aus dem Manifest wird die Aktionsklasse abgeleitet:

- nur lesen → `read`
- ausschließlich lokale Änderung oder Download → `write_local`
- neue Empfänger, externe Sichtbarkeit, Veröffentlichung, Upload, Geld, Recht oder irreversible Wirkung → `outward`

`outward` verlangt im bestehenden Policy-Modell eine strenge Einmalfreigabe. Der vollständige Trockenlauf enthält Handlung und Folgen.

Geheimnisse werden im Manifest nur über Namen und Zweck referenziert. Werte gehören ausschließlich in den bestehenden Schlüsselbund.

## Browsergrenze

Der Browserconnector bietet:

- Navigation
- sichtbaren Text lesen
- Formulare absenden
- Downloads in freigegebene Ordner
- Uploads aus freigegebenen Ordnern

Garantien:

1. Seiteninhalt und Downloads werden immer als fremder Inhalt gerahmt.
2. Nach fremdem Inhalt hebt die bestehende Agent-Policy jede wirksame Folgeaktion an.
3. Formulare und Uploads sind außenwirksam und benötigen Freigabe.
4. Download- und Uploadpfade bleiben auf explizit freigegebene Ordner begrenzt.
5. Passwort-, Token-, PIN-, API-Key- und Kreditkartenfelder sind in Browserplänen verboten.
6. Der Browser erhält keinen direkten Schlüsselbundzugriff. Authentifizierung benötigt später einen domänenspezifischen Credential-Broker, der Werte nur in dafür vorgesehene Eingabefelder injiziert und sie niemals an das Modell oder Seiteninhalte ausgibt.
7. URL-Aufrufe verwenden dieselbe SSRF-Sperre wie der bestehende Webabruf.

## Dauerhafte Workflows

Ein Workflow besteht aus geordneten Schritten:

- Werkzeugaufruf
- Warten bis zu einem Zeitpunkt
- Warten auf eine benannte Bedingung

Jeder Schritt besitzt:

- stabile ID und Invocation-Key
- Aktionsklasse
- Argumente
- Versuchszähler
- Retry-Grenze und Verzögerung
- Zustand
- Freigabe-IDs
- Ergebnis oder Fehler
- Start- und Abschlusszeit

Der Workflow besitzt Zustand, aktuellen Schritt, Kontext und eine append-only Ereigniszeitleiste.

### Restart-Sicherheit

Vor einem Werkzeugaufruf wird `started` dauerhaft geschrieben.

- Ein unterbrochener Lesezugriff darf nach Neustart wiederholt werden.
- Ein unterbrochener lokaler oder außenwirksamer Aufruf wird **nicht** automatisch wiederholt. Er wechselt zu `needs_reconciliation`.
- Die Bedienoberfläche bzw. ein Operator muss angeben, ob die Aktion bereits ausgeführt wurde. Erst danach wird fortgesetzt oder einmal neu gestartet.

Damit ist ein doppeltes Senden, Veröffentlichen oder Zahlen ausgeschlossen, auch wenn der Preis dafür in einer seltenen Absturzlücke eine manuelle Klärung ist.

### Freigaben

Erzeugt `Agent.invoke()` Freigaben, speichert der Workflow deren IDs und wartet. Nach der normalen Auflösung durch den Nutzer übergibt die Anwendung nur das Ergebnis an `approval_resolved`. Der Werkzeugaufruf wird nicht erneut ausgeführt.

### Retry

Automatische Wiederholungen erfolgen nur innerhalb der konfigurierten Versuchszahl und erst nach der gespeicherten Verzögerung. Endgültig fehlgeschlagene Schritte werden nicht still weiter ausgeführt.

## API

`workflow_router(...)` stellt bereit:

- Workflows auflisten und filtern
- Workflow mit Schritten und Ereignissen anzeigen
- Workflow anlegen
- fälligen Schritt ausführen
- abbrechen
- Freigabeergebnis übernehmen
- unklaren Absturzzustand versöhnen

Der Hauptserver muss seine bestehenden Authentifizierungs- und Wartungsabhängigkeiten übergeben.

## Offene Integrationsgrenzen

- Ein konkreter Playwright- oder WebDriver-Adapter implementiert `BrowserSession`; der Sicherheitsvertrag bleibt unabhängig vom Browserprodukt.
- Neue Connectoren brauchen reale Sandbox- oder Stagingtests des jeweiligen Anbieters.
- Finanzielle und rechtliche Connectoren bleiben selbst mit Manifest und Freigabe domänenspezifisch zu prüfen.
