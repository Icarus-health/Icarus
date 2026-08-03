# Proaktiver Chief of Staff

## Ziel

Icarus soll relevante offene Schleifen erkennen, ohne den Nutzer mit einer zweiten Benachrichtigungszentrale zu belasten. Der proaktive Chief of Staff zeigt deshalb auf **Heute** höchstens fünf Hinweise.

Jeder Hinweis beantwortet:

- Was braucht Aufmerksamkeit?
- Warum jetzt?
- Was ist der nächste konkrete Schritt?
- Welche Folge kann Nichtstun haben?
- Woher stammt der Sachverhalt?

Ein Hinweis führt niemals selbst eine Aktion aus.

## Aufmerksamkeitsbudget

Standardmäßig erscheinen maximal fünf Signale, sortiert nach einem deterministischen Score. Berücksichtigt werden:

1. unklare oder freigabepflichtige Workflow-Aktionen,
2. überfällige und bald fällige Aufgaben,
3. bevorstehende Termine,
4. gefährdete oder blockierte Projekte,
5. offene Gedächtnisentscheidungen und neues Rohmaterial,
6. ungelesene Nachrichten.

Damit verdrängt ein voller Posteingang keine überfällige Verpflichtung oder unklare Außenwirkung.

Die Sortierung benötigt kein Modell. Identische Eingaben erzeugen dieselbe Reihenfolge.

## Nutzerkontrolle

Jeder Hinweis kann:

- geöffnet,
- bis morgen zurückgestellt,
- für den aktuellen Sachstand ausgeblendet werden.

Zurückstellen und Ausblenden werden mit einem Fingerabdruck des belegten Sachstands gespeichert. Ändert sich die Aufgabe, Frist, Zahl offener Vorschläge oder Workflowlage, entsteht ein neuer Fingerabdruck und Icarus darf erneut darauf hinweisen.

Die Bedienentscheidungen liegen in `workspace.sqlite3` und gehören damit zum vollständigen Backup.

## Terminvorbereitung

Ein Terminbriefing verbindet den Kalendertermin deterministisch mit:

- passenden Projekten,
- offenen Projektaufgaben,
- dokumentierten Entscheidungen,
- belegten Episoden und Beteiligten,
- relevanten Gedächtniseinträgen.

Daraus entstehen:

- ein vorgeschlagenes Terminergebnis,
- drei bis vier Vorbereitungsfragen,
- eine Quellenübersicht.

Die erste Fassung wird ausdrücklich **ohne Modell** erzeugt. Sie behauptet keine Beziehung ohne Tokenüberschneidung, direkte Projektbenennung oder vorhandene Beteiligte.

## Sicherheitsgrenzen

- alle Endpunkte verlangen das Sidecar-Token,
- Hinweise sind Projektionen und schreiben keine Fakten,
- Terminbriefings werden lokal zusammengestellt,
- „Öffnen“ wechselt nur in einen bestehenden Bereich,
- keine Mail, kein Termin und kein Workflow wird automatisch ausgeführt,
- Workflowfreigaben bleiben in der bestehenden Policy- und Auditkette,
- die Aufmerksamkeitstabelle benutzt kurzlebige SQLite-Verbindungen und blockiert keinen Restore.

## HTTP-Schnittstellen

- `GET /chief-of-staff/attention?limit=5`
- `POST /chief-of-staff/attention/{id}/snooze`
- `POST /chief-of-staff/attention/{id}/dismiss`
- `GET /chief-of-staff/meetings?days=3`
- `GET /chief-of-staff/meetings/{uid}/prep`

## Release-Gates

- maximal fünf sichtbare Hinweise,
- Grund, nächster Schritt und Konsequenz für jeden Hinweis,
- überfällige Verpflichtungen schlagen normale Inboxsignale,
- Zurückstellen entfernt exakt den aktuellen Fingerabdruck,
- geänderter Sachstand darf erneut erscheinen,
- Terminbriefing verbindet Kalender, Projekt, Aufgaben, Entscheidungen und Episoden,
- alle Routen sind tokenpflichtig,
- echter Chromium-Test für Priorisierung und Zurückstellen,
- bestehende Graph-, Backup-, Browser- und macOS-Gates bleiben grün.
