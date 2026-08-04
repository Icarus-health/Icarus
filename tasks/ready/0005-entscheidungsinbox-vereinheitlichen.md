# 0005 – Freigaben und Gedächtnisentscheidungen in einer Inbox bündeln

> **Status:** geprüft
> **Risiko:** A
> **Planung:** Frontier-Modell
> **Umsetzung:** Frontier-Modell
> **Review:** unabhängiges Frontier-Modell
> **Abhängigkeiten:** PR #37 „Freigabe atomar mit Workflow verbinden“ muss integriert sein

## Problembeleg

Die verbindliche erste Produktstufe nennt vier Alltagseinstiege: „Heute“,
„Arbeit“, „Gespräch“ und „Entscheiden“. Die aktuelle Oberfläche zeigt dagegen
„Vorschläge“ als eigenen Hauptbereich, während Freigaben ausschließlich im
Gespräch erscheinen.

Damit muss der Nutzer zwei Stellen beobachten, obwohl beide denselben Vorgang
abbilden: Eine offene Entscheidung wartet auf menschliche Verantwortung.
Zusätzlich liegt der einzige produktive Freigabeweg in einem Bereich, dessen
primärer Zweck das Gespräch ist. Nach einem Seitenwechsel ist nicht zuverlässig
sichtbar, dass noch eine Aktion auf Freigabe wartet.

Die bestehenden Endpunkte und Sicherheitswege sind bereits vorhanden:

- `GET /approvals` und `POST /approvals/{approval_id}` für Aktionen;
- `GET /proposals`, `GET /proposals/counts` und die bestehenden Annahme- und
  Ablehnungsendpunkte für Gedächtnisvorschläge;
- Konfliktvorschläge werden bereits als eigener Vorschlagstyp dargestellt.

Es fehlt ausschließlich ein gemeinsamer, eindeutiger Nutzerweg. Dabei darf
keine zweite Freigabelogik entstehen und keine Approval-ID anders zugeordnet
werden als im bestehenden produktiven Pfad.

## Nutzerwirkung

Ein normaler Nutzer sieht künftig an genau einer Stelle, welche Entscheidungen
auf ihn warten:

- eine vorbereitete außenwirksame Aktion freigeben oder ablehnen;
- eine vorgeschlagene Gedächtnisaussage übernehmen oder verwerfen;
- einen möglichen Widerspruch als strittig markieren oder ignorieren.

Der Bereich „Gespräch“ bleibt zum Schreiben und Planen da. Er weist nach einem
entstandenen Antrag verständlich auf „Entscheiden“ hin, zeigt aber keine zweite
Freigabekarte. Dadurch kann eine offene Entscheidung nicht mehr zwischen
Chatnachrichten verschwinden oder an zwei Stellen unterschiedlich wirken.

## Ziel

Die Hauptnavigation enthält einen Bereich **„Entscheiden“** mit einer gemeinsamen
Zahl aller offenen Freigaben und Gedächtnisvorschläge. In diesem Bereich werden
beide Typen klar getrennt, aber über ihre bestehenden produktiven Endpunkte
bearbeitet.

Es gibt genau eine sichtbare Freigabekarte je Approval-ID und genau einen
produktiven UI-Weg zum Einlösen. Nach Annahme, Ablehnung, Fehler oder erneutem
Laden stimmen Karten, Leerzustände und Gesamtzähler mit dem Sidecar überein.

## Nicht-Ziele

- Keine Änderung an Policy, Approval-ID-Zuordnung, Workflow-Zuständen,
  Audit-Log oder Ausführungsreihenfolge.
- Keine neuen API-Endpunkte und kein aggregierender Entscheidungsspeicher.
- Keine automatische Annahme, Priorisierung oder Zusammenfassung von
  Entscheidungen durch ein Modell.
- Keine Auflösung bereits strittiger Aussagen; dieses Paket bündelt nur die
  heute vorhandenen Entscheidungstypen.
- Keine Neugestaltung der übrigen Hauptnavigation oder des Onboardings.
- Keine parallele Freigabekarte im Gespräch, Dashboard oder Systembereich.

## Erlaubte Pfade

- `app/src/index.html`
- `app/src/main.js`
- `app/src/style.css`
- `app/src/product-shell.css`
- `app/e2e/private-beta-runtime.mjs`
- `app/e2e/consumer-chief-of-staff.mjs`
- `sidecar/tests/test_private_beta_runtime.py`
- `sidecar/tests/test_private_beta_regressions.py`
- `tasks/ready/0005-entscheidungsinbox-vereinheitlichen.md`

## Verbotene Änderungen

- Keine Änderung unter `sidecar/icarus_memory/`.
- Keine neue Architektur-, Speicher-, Policy-, Identitäts- oder
  Berechtigungsgrenze.
- `Agent.resolve` und `POST /approvals/{approval_id}` bleiben der einzige
  produktive Ausführungsweg.
- Approval-IDs werden weder über Werkzeugname, Trockenlauftext, Zeitstempel
  noch UI-Reihenfolge zugeordnet.
- Keine doppelte Darstellung derselben Approval-ID in mehreren Ansichten.
- Keine direkte API-Ausführung aus dem Browser-E2E an der sichtbaren Karte
  vorbei.
- Keine realen Schlüssel, Konten, Nutzerdaten oder Provideraufrufe in Tests.
- Keine konkreten Modellnamen in Dokumentation, Nutzertext oder PR.

## Akzeptanzkriterien

- [ ] Die Hauptnavigation zeigt „Entscheiden“ statt „Vorschläge“ und besitzt
  einen Badge mit der Summe aus offenen Approvals und offenen Proposals.
- [ ] Der neue Bereich enthält zwei verständlich benannte Abschnitte:
  „Aktionen freigeben“ und „Gedächtnis prüfen“.
- [ ] Jede offene Approval-ID erscheint im gesamten DOM höchstens einmal und
  ausschließlich unter „Entscheiden“.
- [ ] Das Gespräch rendert nach einem neuen Antrag keine Freigabekarte mehr,
  sondern einen verständlichen Hinweis mit direktem Wechsel zu „Entscheiden“.
- [ ] Die bestehende Freigabekarte zeigt weiterhin vollständigen Trockenlauf,
  Gründe, Bestätigungsphrase, „Ausführen“ und „Ablehnen“.
- [ ] Ein Grant verwendet unverändert `POST /approvals/{approval_id}` und kann
  dieselbe Approval-ID nur einmal einlösen.
- [ ] Ablehnung, falsche Bestätigungsphrase und Fehler führen nichts aus und
  lassen den sichtbaren Zustand mit der Serverantwort übereinstimmen.
- [ ] Vorschläge verwenden unverändert ihre bestehenden Accept-/Reject-Endpunkte
  und behalten Beleg, Herkunft sowie typspezifische Nutzertexte.
- [ ] Ein Fehler beim Laden eines Abschnitts verdeckt den anderen Abschnitt
  nicht. Beide besitzen einen verständlichen Leer- und Wiederholungszustand.
- [ ] Nach jeder Entscheidung werden betroffener Abschnitt und Gesamtbadge aus
  dem Sidecar neu geladen; lokale Zähler werden nicht als Wahrheit fortgeführt.
- [ ] Der echte Browser-E2E erzeugt über den normalen Nutzerweg eine
  außenwirksame Freigabe, wechselt zu „Entscheiden“, löst sie dort einmal ein
  und belegt, dass im Gespräch keine zweite Karte existiert.
- [ ] Der Browser-E2E prüft zusätzlich mindestens einen Gedächtnisvorschlag im
  selben Bereich und bestätigt den korrekten Gesamtbadge.
- [ ] Tastaturfokus, Überschriftenstruktur und Statusmeldungen bleiben für beide
  Abschnitte verständlich.

## Sabotageprobe

1. Die Freigabekarte zusätzlich wieder im Gespräch rendern. Der Browsertest muss
   wegen zweier Karten beziehungsweise eines falschen Ablageorts fehlschlagen.
2. Den Gesamtbadge nach einer Entscheidung nur lokal herunterzählen, statt ihn
   aus `GET /approvals` und `GET /proposals/counts` neu zu laden. Ein Test mit
   serverseitig unverändertem oder neu hinzugekommenem Eintrag muss die Drift
   erkennen.
3. Im Browser-E2E die Approval-Route direkt aufrufen und die sichtbare Karte
   überspringen. Der Test muss ausdrücklich nachweisen, dass die Aktion über
   „Entscheiden“, Bestätigungsfeld und „Ausführen“-Button erfolgt.

## Prüfkommandos

```bash
make verify
python -m pytest sidecar/tests/test_private_beta_runtime.py -q
python -m pytest sidecar/tests/test_private_beta_regressions.py -q
node app/e2e/private-beta-runtime.mjs
node app/e2e/consumer-chief-of-staff.mjs
```

Zusätzlich müssen die regulären Container- und Plattform-Gates grün sein, weil
der einzige produktive Freigabeweg verändert wird.

## Rückrollweg

Der Folge-PR ändert keine Persistenz und keine Serververträge. Ein Revert stellt
die bisherigen Ansichten „Gespräch“ und „Vorschläge“ wieder her. Bereits offene
Approvals und Proposals bleiben im Sidecar erhalten und werden nach dem Revert
über die bisherigen Endpunkte wieder sichtbar; es darf weder automatische
Ausführung noch Datenmigration geben.

## Offene Entscheidungen

Keine.
