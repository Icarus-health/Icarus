# 0003 – Freigabe und dauerhaften Workflow atomar verbinden

> **Status:** geprüft
> **Risiko:** A
> **Planung:** Frontier-Modell
> **Umsetzung:** Frontier-Modell
> **Review:** unabhängiges Frontier-Modell
> **Abhängigkeiten:** 0002 für einen vollständig isolierten Gesamttest

## Problembeleg

Ein Workflow speichert die vom Agent erzeugte Approval-ID und wechselt auf
`waiting_approval`. Der sichtbare Nutzerweg löst die Freigabe über
`POST /approvals/{approval_id}` im Agent auf. Der Workflow erhält dieses
Ergebnis nicht automatisch; sein separater Endpunkt
`POST /workflows/{workflow_id}/approval` wird von der normalen Freigabeansicht
nicht aufgerufen. Die Außenwirkung kann deshalb ausgeführt sein, während der
dauerhafte Workflow weiter wartet.

## Nutzerwirkung

Icarus zeigt eine erledigte Handlung weiterhin als offen. Ein späterer manueller
oder fehlerhafter Fortsetzungsweg kann Doppelarbeit, falsche Zustände oder eine
zweite Außenwirkung begünstigen.

## Ziel

Der einzige sichtbare Freigabeweg des Agenten löst nach erfolgreicher
Ausführung oder Ablehnung atomar den genau zugehörigen Workflow-Schritt auf.
Das Werkzeug wird höchstens einmal aufgerufen. Nach einem Neustart ist der
Workflowzustand weiterhin korrekt.

## Nicht-Ziele

- Keine zweite Freigabeoberfläche.
- Keine automatische Freigabe.
- Keine Abschwächung von Bestätigungsphrase, Ablauf oder One-shot-Semantik.
- Kein allgemeiner Umbau der Workflow-Zustandsmaschine.

## Erlaubte Pfade

- `sidecar/icarus_memory/server.py`
- `sidecar/icarus_memory/private_beta.py`
- `sidecar/icarus_memory/durable_workflows.py`
- `sidecar/icarus_memory/workflow_runtime.py`
- `sidecar/icarus_memory/workflow_api.py`
- gezielte Tests unter `sidecar/tests/`
- `app/e2e/private-beta-runtime.mjs`
- `app/e2e/system-cockpit.mjs`

## Verbotene Änderungen

- Keine Ausführung des Werkzeugs durch den Workflow nach erfolgter Agent-
  Freigabe.
- Keine Zuordnung über Werkzeugname oder lose Zeitnähe; die gespeicherte
  Approval-ID ist der verbindliche Schlüssel.
- Kein Erfolg im Workflow, wenn die Werkzeugausführung fehlgeschlagen oder das
  Ergebnis unklar ist.
- Kein stilles Auflösen mehrerer Workflows bei einer mehrdeutigen Zuordnung.

## Akzeptanzkriterien

- [ ] Grant über `/approvals/{id}` führt das Werkzeug genau einmal aus und setzt
  den passenden Workflow ohne zweiten API-Aufruf fort.
- [ ] Ablehnung setzt den passenden Schritt und Workflow sichtbar terminal;
  das Werkzeug wird nicht ausgeführt.
- [ ] Falsche Bestätigungsphrase oder abgelaufene Freigabe verändert den
  Workflow nicht.
- [ ] Ein Ausführungsfehler wird nicht als erfolgreicher Workflow-Schritt
  gespeichert.
- [ ] Nicht aus einem Workflow stammende Agent-Freigaben funktionieren
  unverändert.
- [ ] Neustart vor und nach der Nutzerentscheidung erhält Zuordnung und
  One-shot-Semantik.
- [ ] Der echte UI-E2E-Weg benutzt nur die normale Freigabekarte.

## Sabotageprobe

Die Übergabe des Agent-Ergebnisses an den Workflow testweise entfernen. Der
End-to-End-Test muss zeigen, dass die Außenwirkung zwar einmal auftrat, der
Workflow aber fälschlich auf `waiting_approval` blieb.

## Prüfkommandos

```bash
make verify
.venv/bin/python -m pytest sidecar/tests/test_connector_workflows.py sidecar/tests/test_private_beta_runtime.py -q
```

Zusätzlich den echten Private-Beta- und Systemzentralen-Browserflow ausführen.

## Rückrollweg

Der PR braucht keine Datenmigration. Ein Revert stellt die getrennten Endpunkte
wieder her; vor dem Revert wartende Workflows bleiben sichtbar und dürfen nicht
automatisch erneut ausgeführt werden.

## Offene Entscheidungen

Keine. Die Approval-ID ist bereits die gespeicherte, eindeutige Zuordnung.
