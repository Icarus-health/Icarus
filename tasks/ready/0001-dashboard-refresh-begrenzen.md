# 0001 – Dashboard-Refresh begrenzen

> **Status:** geprüft
> **Risiko:** A
> **Planung:** Frontier-Modell
> **Umsetzung:** Frontier-Modell
> **Review:** unabhängiges Frontier-Modell
> **Abhängigkeiten:** keine

## Problembeleg

`app/src/chief-of-staff-shell.js` beobachtet Änderungen am gesamten
`document.body`. `renderFocus()` ersetzt selbst Kinder und Attribute unterhalb
dieses Bereichs. Diese Mutation plant den nächsten Render und damit einen neuen
Aufruf von `/dashboard`. Der bestehende In-Flight-Schutz verhindert parallele,
nicht aber unmittelbar aufeinanderfolgende Aufrufe.

## Nutzerwirkung

Im geöffneten Bereich „Heute“ kann Icarus den lokalen Server und dahinter
liegende Mail-/Kalenderzugänge ohne Nutzereingabe dauerhaft abfragen. Das kostet
Ressourcen, kann Limits erreichen und macht den Alltag unzuverlässig.

## Ziel

Der Tagesfokus lädt beim Eintritt und bei einem ausdrücklich definierten
Refresh-Ereignis. Eigene DOM-Änderungen lösen keinen weiteren Dashboard-Abruf
aus. Der bestehende 60-Sekunden-Refresh bleibt höchstens einmal pro Intervall.

## Nicht-Ziele

- Keine neue State-Management-Bibliothek.
- Keine Änderung an Dashboard-API, Priorisierungslogik oder Connectoren.
- Kein visuelles Redesign.

## Erlaubte Pfade

- `app/src/chief-of-staff-shell.js`
- `app/e2e/consumer-chief-of-staff.mjs`
- eine neue gezielte Browser-Testdatei unter `app/e2e/`, falls der bestehende
  Test dadurch unübersichtlich würde
- `.github/workflows/ci.yml` nur zum Registrieren einer neuen Syntaxprüfung

## Verbotene Änderungen

- Keine Abschwächung des periodischen Refreshs durch einen schnelleren Timer.
- Keine globalen Request-Deduplizierer als Umgehung des Auslösers.
- Keine Server- oder Connectoränderung.

## Akzeptanzkriterien

- [ ] Nach dem ersten fertigen Tagesfokus bleibt ein Browser-Quiet-Window von
  mindestens zwei Sekunden ohne weiteren `/dashboard`-Request.
- [ ] Eine Mutation, die `renderFocus()` selbst erzeugt, startet keinen Request.
- [ ] Wechsel zu „Heute“ aktualisiert genau einmal; schnelles mehrfaches
  Klicken erzeugt keine Request-Kaskade.
- [ ] Projektbriefing und 60-Sekunden-Refresh funktionieren weiter.
- [ ] Der Browser-Regressionsfall scheitert auf dem heutigen Code.

## Sabotageprobe

Den bisherigen globalen `MutationObserver(schedule)` testweise wiederherstellen.
Der Browser-Regressionsfall muss wegen wiederholter `/dashboard`-Requests
fehlschlagen.

## Prüfkommandos

```bash
node --check app/src/chief-of-staff-shell.js
node --check app/e2e/consumer-chief-of-staff.mjs
make verify
```

Zusätzlich den echten Consumer-Browserflow aus der Container-CI ausführen.

## Rückrollweg

Der PR verändert keinen dauerhaften Zustand und ist vollständig revertierbar.

## Offene Entscheidungen

Keine.
