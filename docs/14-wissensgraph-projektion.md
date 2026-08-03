# Wissensgraph als ableitbare Projektion

## Status

Implementierter technischer Vertrag für Version 0.4. Der Graph ist **nicht** der verbindliche Bestand. SQLite-Stores für Selbstmodell, Projekte, Aufgaben, Notizen und Episoden bleiben maßgeblich.

## Garantien

1. Die Graphdatenbank kann vollständig gelöscht und aus den verbindlichen Stores neu aufgebaut werden.
2. Jede Kante besitzt mindestens einen `SourceRef` mit Quelltyp, Quell-ID und optionaler Version.
3. Widerruf oder Ersetzung einer Quelle entfernt ihre Quellenbindung. Kanten ohne verbleibende Quelle und anschließend verwaiste Entitäten werden gelöscht.
4. Entitäten verschiedener Typen werden niemals allein wegen eines gleichen Namens vereinigt.
5. Mehrdeutige Aliase werden als Identitätskonflikt gespeichert und müssen explizit aufgelöst werden.
6. Sensible Kanten werden in der Standardabfrage nicht ausgegeben.
7. Zeitbezug und Status (`active`, `disputed`, `superseded`, `revoked`) gehören zur Kante, nicht nur zur Anzeige.

## Entitätstypen

- Person
- Organisation
- Rolle
- Projekt
- Ziel
- Entscheidung
- Ereignis
- Ort

## Identität

Eine externe ID gewinnt, wenn der verbindliche Store sie liefert. Andernfalls entsteht die interne ID deterministisch aus Entitätstyp und normalisiertem Namen. Dadurch bleibt ein Neuaufbau stabil, ohne Personen, Orte oder Organisationen gleichen Namens still zusammenzuführen.

Zusammenführungen sind nur innerhalb desselben Typs erlaubt und werden mit einem reversiblen Snapshot protokolliert. Eine automatische Zusammenführung aufgrund semantischer Ähnlichkeit ist ausdrücklich nicht vorgesehen.

## Projektion

`knowledge_graph_projection.py` enthält konservative Adapter:

- Projekte werden Projektentitäten.
- Projektfristen werden Ereignisse mit `has_deadline`.
- Aufgaben werden Ziele bzw. nächste Schritte mit `has_next_step`.
- Entscheidungsnotizen werden Entscheidungen mit `has_decision`.
- Episoden werden Ereignisse; ausschließlich explizit vorhandene Teilnehmer werden Personen.
- Aussagen werden nur als Ziele oder über explizite Entitätstags projiziert.

Freitext wird nicht heimlich als Beziehung interpretiert. Eine spätere modellgestützte Extraktion muss Vorschläge mit wörtlichem Quellenbeleg erzeugen und denselben Zustimmungsweg wie die bestehende Verdichtung verwenden.

## Abfragen

Der Router stellt bereit:

- Entität
- Nachbarschaft
- kürzester Pfad
- Zeitachse
- Quellen einer Kante
- Identitätskonflikte
- Projektionsstatistik

`graph_router(...)` verlangt, dass der Hauptserver seine bestehenden Authentifizierungs- und Wartungsabhängigkeiten übergibt. Der Router schafft keinen alternativen ungeschützten Weg.

## Betrieb

Empfohlener Dateiname: `knowledge-graph.sqlite3`. Die Datei gehört **nicht** in das verbindliche Installationsbackup: Nach einem Restore wird sie aus den restaurierten Stores neu aufgebaut. Das verhindert, dass eine alte Projektion neben einem neueren Bestand weiterlebt.
