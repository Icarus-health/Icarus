# 0004 – Eignungstest für Ausführungsmodelle anlegen

> **Status:** geprüft
> **Risiko:** B
> **Planung:** Frontier-Modell
> **Umsetzung:** qualifiziertes Ausführungsmodell
> **Review:** unabhängiges Frontier-Modell
> **Abhängigkeiten:** 0001 bis 0003 liefern reale Grenzfälle

## Problembeleg

Die Rollen im Entwicklungsvertrag sind messbar definiert, aber es fehlt noch
ein versionierter Icarus-spezifischer Test, der neue Modelle anhand derselben
kleinen Aufgaben vergleicht. Ohne ihn würde die Rollenzuweisung wieder auf Ruf,
Preis oder Einzelfallgefühl beruhen.

## Nutzerwirkung

Ein ungeeignetes Modell erzeugt Nacharbeit oder Fehler, ein unnötig teures Modell
verbraucht das begrenzte Entwicklungsbudget. Beides verzögert eine nutzbare,
stabile Icarus-Version.

## Ziel

Eine vollständig synthetische Suite aus zehn eingefrorenen Mini-Aufgaben misst
Scope-Treue, Korrektheit, Tests, Sicherheit, Verständlichkeit und Kosten. Ihr
Bericht weist ausschließlich Rollenfähigkeit aus, keine dauerhafte Rangliste.

## Nicht-Ziele

- Keine Anbieter- oder Modellnamen in dauerhaften Dokumenten.
- Kein automatisches Merge durch ein Testergebnis.
- Keine echten Nutzer-, Mail-, Kalender- oder Gedächtnisdaten.

## Erlaubte Pfade

- `tasks/qualification/`
- ein kleines Testwerkzeug unter `tools/qualification/`
- `docs/20-entwicklungsvertrag.md`
- `docs/21-qualitaetsgates.md`
- `Makefile`

## Verbotene Änderungen

- Keine Aufgaben aus aktuellem Produktivcode kopieren, deren erwartete Lösung
  bereits öffentlich im selben Paket steht.
- Keine Bewertung durch das zu prüfende Modell selbst.
- Keine reine Textstilbewertung ohne ausführbare Tests.

## Akzeptanzkriterien

- [ ] Zehn kleine, eingefrorene Aufgaben decken Python, JavaScript, Persistenz,
  API, UI, Scope-Treue, Negativtest und Dokumentation ab.
- [ ] Jede Aufgabe besitzt versteckte oder getrennt gehaltene deterministische
  Tests und eine maximale Laufzeit.
- [ ] Bewertung: Korrektheit 50 %, Testqualität 20 %, Scope-Treue 15 %,
  Sicherheit 10 %, Dokumentation 5 %.
- [ ] Ein kritischer Sicherheits- oder Scope-Verstoß führt unabhängig von der
  Punktzahl zum Nichtbestehen.
- [ ] Der Bericht enthält Commit, Datum, Rollenklasse, Laufzeit und Kosten, aber
  dauerhafte Verträge referenzieren keinen Modellnamen.
- [ ] Die Suite läuft ausschließlich mit synthetischen Daten und ohne Netzwerk.

## Sabotageprobe

In einer Referenzlösung absichtlich einen verbotenen Pfad ändern und in einer
zweiten einen Negativtest entfernen. Beide Einreichungen müssen trotz grüner
Happy-Path-Tests durchfallen.

## Prüfkommandos

```bash
make verify
make qualify-execution-model
```

## Rückrollweg

Suite und Werkzeug sind vom Produktlauf getrennt und vollständig revertierbar.

## Offene Entscheidungen

Keine.
