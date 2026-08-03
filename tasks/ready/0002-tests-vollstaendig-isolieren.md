# 0002 – Tests vollständig von Keychain und Providern isolieren

> **Status:** geprüft
> **Risiko:** A
> **Planung:** Frontier-Modell
> **Umsetzung:** Frontier-Modell
> **Review:** unabhängiges Frontier-Modell
> **Abhängigkeiten:** keine

## Problembeleg

Die Testmodule räumen Umgebungsvariablen uneinheitlich auf. Nicht jeder
`create_app()`-Pfad setzt `ICARUS_KEYCHAIN_BACKEND=none`. Auf einem Entwickler-
Mac kann der produktive Schlüsselbund deshalb einen echten Provider-Schlüssel
liefern und einen unbeabsichtigten Netzwerkaufruf ermöglichen.

## Nutzerwirkung

Lokale Tests können private Schlüssel verwenden, Kosten erzeugen, Daten an
einen Provider senden und je nach Rechner unterschiedlich bestehen oder
scheitern. Eine Testsuite darf niemals den echten Icarus-Bestand berühren.

## Ziel

Alle Tests laufen standardmäßig mit deaktiviertem Betriebssystem-Keychain,
leerem Provider-/Connector-Umfeld, temporärem Datenverzeichnis und gesperrten
echten Provideraufrufen. Ein expliziter Integrationstest muss sich bewusst aus
dieser Isolation herausbewegen und darf nicht Teil des normalen Testziels sein.

## Nicht-Ziele

- Keine Änderung des produktiven Keychain- oder Providerverhaltens.
- Kein echter Provider-Integrationstest.
- Keine Umbenennung aller vorhandenen Fixtures.

## Erlaubte Pfade

- `sidecar/tests/conftest.py`
- betroffene Dateien unter `sidecar/tests/`
- minimale Testseams unter `sidecar/icarus_memory/secrets.py` oder
  `sidecar/icarus_memory/providers.py`, nur wenn die Isolation ohne Änderung
  produktiven Verhaltens sonst nicht nachweisbar ist
- `sidecar/pyproject.toml`, falls ein kleines reines Testwerkzeug nötig ist

## Verbotene Änderungen

- Keine globalen Fakes im Produktionscode.
- Keine realen Schlüssel, Provider-Endpunkte oder Nutzerverzeichnisse.
- Kein Überspringen von Tests aufgrund des Betriebssystems.

## Akzeptanzkriterien

- [ ] Ein globales Autouse-Fixture setzt den Test-Keychain auf `none` und stellt
  die ursprüngliche Umgebung nach jedem Test wieder her.
- [ ] Bekannte Provider-, Mail-, Kalender-, Token- und Datenpfadvariablen sind
  im Standardtest leer oder synthetisch.
- [ ] Ein Regressionstest beweist, dass der OS-Keychain nicht aufgerufen wird.
- [ ] Ein Regressionstest beweist, dass ein echter Providertransport im
  Standardtest scheitert, bevor Netzwerkverkehr entsteht.
- [ ] Die gesamte Sidecar-Suite läuft auf einem Mac mit vorhandenen echten
  Schlüsseln identisch wie in CI.

## Sabotageprobe

Die globale Vorgabe `ICARUS_KEYCHAIN_BACKEND=none` testweise entfernen. Der
Isolationstest muss den versuchten Zugriff erkennen und fehlschlagen, auch wenn
auf dem Rechner ein Keychain vorhanden ist.

## Prüfkommandos

```bash
make sidecar-dev
make verify
```

Zusätzlich einmal mit absichtlich gesetzten Dummy-Provider-Variablen starten;
die Tests müssen diese Werte isolieren und dürfen kein Netzwerk öffnen.

## Rückrollweg

Der PR verändert nur Testgrenzen. Revert stellt den vorherigen Testlauf wieder
her und berührt keine Nutzerdaten.

## Offene Entscheidungen

Keine.
