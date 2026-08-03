# Systemzentrale und sichere Graphsuche

## Ziel

Die Systemzentrale übersetzt die Private-Beta-Architektur in vier verständliche Fragen:

1. Was läuft gerade?
2. Was weiß Icarus über Zusammenhänge?
3. Was wartet auf einen Zeitpunkt, eine Bedingung oder eine Freigabe?
4. Welche Modelle und Connectoren sind tatsächlich aktiv?

Sie liegt unter **Mehr → System**. Die tägliche Hauptnavigation bleibt dadurch ruhig.

## Keine technische Statuswand

Die Oberfläche zeigt:

- Zahl der sichtbaren Entitäten, Beziehungen und Quellen,
- Automationen mit einem verständlichen Zustand,
- Modellsteuerung als automatisch, Einzelmodell oder nicht verbunden,
- aktive Connectoren und deren kontrollierte Fähigkeiten,
- Identitätskonflikte, die Icarus nicht still auflöst.

Interne Datenbanknamen, Modellregistry-Rohdaten, technische Aktionsklassen und Stacktraces gehören nicht in diese Ansicht.

## Graphsuche

Die Graphsuche unterstützt Personen, Organisationen, Rollen, Projekte, Ziele, Entscheidungen, Ereignisse und Orte. Ein Treffer zeigt:

- Art des Zusammenhangs,
- Zahl sichtbarer Beziehungen,
- Zahl der Quellen,
- Nachbarschaft,
- Status strittiger Beziehungen,
- Zeitbezug,
- quellennachweisbare Attribute.

Der Graph bleibt eine Projektion. „Neu aufbauen“ löscht und erzeugt nur die Projektion; der verbindliche Bestand wird nicht verändert.

## Datenschutz für Entitäten

Sensible Kanten zu verbergen reicht nicht. Bereits der Name eines Ziels oder einer Person kann sensible Information sein.

Deshalb wird bei jedem Graph-Neuaufbau zusätzlich `entity_privacy` abgeleitet:

- jede Entität wird ihren Quellschlüsseln und deren Schutzbedarf zugeordnet,
- eine Entität ist standardmäßig sichtbar, wenn mindestens eine normale Quelle sie trägt,
- ausschließlich sensible Entitäten erscheinen weder in Suche noch Direktaufruf,
- sensible Kanten, Quellen und Zeitachseneinträge bleiben standardmäßig verborgen,
- Identitätskonflikte werden nur gezeigt, wenn alle Kandidaten sichtbar sind.

Die Metadaten sind vollständig ableitbar und gehören nicht in das Installationsbackup.

## Bewusste sensible Ansicht

Der Nutzer kann sensible Zusammenhänge in der Systemzentrale für die aktuelle Sitzung sichtbar machen. Diese Umschaltung:

- ist standardmäßig aus,
- wird nicht dauerhaft gespeichert,
- verändert keine Daten,
- erweitert ausschließlich authentifizierte Graphabfragen,
- umgeht weder Betriebssystemschutz noch Egress-Regeln.

## Automationen

Die Systemzentrale kann:

- Workflowzustände anzeigen,
- fällige Schritte prüfen,
- Workflows abbrechen,
- wartende Freigaben zum Gespräch öffnen,
- unklare Außenwirkungen ausdrücklich als ausgeführt oder nicht ausgeführt klären.

Sie ruft dafür ausschließlich die bestehenden Workflow-Endpunkte auf. Kein UI-Code führt ein Werkzeug direkt aus.

## Release-Gates

- sensible Entität standardmäßig nicht suchbar und nicht direkt abrufbar,
- sensible Beziehung fehlt in der normalen Zeitachse,
- bewusste Umschaltung zeigt die Daten nur authentifiziert,
- Browser-End-to-End-Test für Projektgraph, Automation und sensible Umschaltung,
- responsive und tastaturbedienbare Ansicht,
- bestehende Consumer-, Backup-, Browser- und Packaging-Gates bleiben grün.
