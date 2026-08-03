# Private-Beta-Runtime

## Status

Implementierter Integrationsvertrag für die erste geschlossene Icarus-Beta. Dieses Dokument beschreibt den gemeinsamen Laufweg von Consumer-Oberfläche, Wissensgraph, Modell-Harness, Connectoren und dauerhaften Workflows.

Die Private Beta ist **keine öffentliche Freigabe**. Signierung, Notarisierung, reale Nutzertests, Modellbenchmarks und dienstspezifische Connector-Sandboxes bleiben eigene Release-Gates.

## Ein Produktionsweg

Container, Konsolenstart und die gebündelte Desktop-App starten `icarus_memory.runtime`. Dieser Einstieg:

1. baut den bestehenden Sidecar,
2. montiert die Private-Beta-Fähigkeiten,
3. legt die Wartungsschranke um sämtliche Routen,
4. startet denselben Scheduler und dieselbe Policy wie bisher.

Es existiert kein alternativer Graph-, Browser- oder Workflow-Server.

## Wissensgraph

`knowledge-graph.sqlite3` ist eine löschbare Projektion. Beim ersten Graphzugriff und nach relevanten Änderungen wird sie deterministisch neu aus folgenden verbindlichen Stores aufgebaut:

- Selbstmodell
- Projekte
- Aufgaben
- Entscheidungsnotizen
- Episoden

Die Graphdatei wird nicht als verbindlicher Bestandteil gesichert. Nach einem Restore wird sie aus dem restaurierten Bestand neu erzeugt. Jede Kante bleibt auf ihre Quelle zurückführbar.

## Dauerhafte Workflows

Workflow-Tabellen liegen in `workspace.sqlite3`. Dadurch gelten automatisch dieselben Sicherungs-, Integritäts- und Wiederherstellungszusagen wie für Projekte und Notizen.

Alle Workflow-Aktionen laufen ausschließlich über `Agent.invoke()`:

- Policy und Trockenlauf bleiben bindend,
- Freigaben pausieren den Ablauf,
- unklare wirksame Aktionen werden nicht wiederholt,
- nach einem Neustart wird entweder sicher fortgesetzt oder eine manuelle Klärung verlangt.

Die Workflow-Verbindung wird vor einer vollständigen Wiederherstellung geschlossen und anschließend auf dem restaurierten Datenstand neu geöffnet.

## Modell-Harness und Audit

Ist ein RoutingProvider aktiv, bindet die Private-Beta-Runtime dessen Ereignisse an das bestehende append-only Audit-Log. Gespeichert werden ausschließlich Betriebsmetadaten wie Modell-ID, Auswahlgrund, Fallbackrang, geschätzte Kosten und Latenz.

Gesprächsinhalte, Nachrichten und Prompts gehören nicht in diese Routingereignisse.

## Browser

Browserwerkzeuge werden nur aktiviert, wenn `ICARUS_BROWSER_WORKER` auf einen vorhandenen Worker zeigt und die konfigurierte Node-Laufzeit verfügbar ist. Ohne diesen Nachweis meldet `/private-beta/status` den Browser transparent als inaktiv.

Ein aktiver Browserconnector:

- läuft in einem getrennten Playwright-Prozess,
- behandelt Seiteninhalt als fremde Daten,
- nutzt die produktive SSRF-Sperre,
- darf keine Geheimnisse in Formularplänen erhalten,
- führt Formulare und Uploads nur über Policy und Freigabe aus,
- beschränkt Dateioperationen auf ausdrücklich freigegebene Wurzeln.

## HTTP-Schnittstellen

Alle folgenden Routen verlangen dasselbe `x-icarus-token` wie der übrige Sidecar:

- `/private-beta/status`
- `/graph/*`
- `/workflows/*`
- `/connectors`

Die statische Oberfläche bleibt öffentlich ladbar, enthält aber keine Nutzerdaten. Sämtliche Datenendpunkte bleiben geschützt.

## Wiederherstellung

Während Backup und Restore blockiert die Wartungsschranke parallele Nutzdatenzugriffe. Für den Private-Beta-Bestand gilt zusätzlich:

1. Workflow-Verbindung vor Restore schließen.
2. Verbindliche Datenbanken restaurieren.
3. Workflow-Verbindung auf dem restaurierten Stand neu öffnen.
4. Agent-, Modellrouting- und Browserbindung erneuern.
5. Graph als veraltet markieren und beim nächsten Zugriff neu aufbauen.

## Release-Gates

Vor einer öffentlichen Beta bleiben erforderlich:

- moderierter Test mit mindestens fünf Personen,
- Apple-Signierung und Notarisierung,
- eigener versionierter Modell-Evaluationsdatensatz,
- Sandbox-Tests je Drittanbieter-Connector,
- mitgelieferter und plattformübergreifend geprüfter Browser-Worker,
- abschließender Gesamt-Roundtrip auf heruntergeladenen Nutzerartefakten.
