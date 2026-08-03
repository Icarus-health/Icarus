# NNNN – Kurzer Titel

> **Status:** Entwurf | geprüft | in Arbeit | blockiert | abgeschlossen
> **Risiko:** A | B | C
> **Planung:** Frontier-Modell | nicht erforderlich
> **Umsetzung:** Frontier-Modell | qualifiziertes Ausführungsmodell
> **Review:** Frontier-Modell | unabhängiger Review | Stichprobe
> **Abhängigkeiten:** keine | Paket/PR

## Problembeleg

Reproduktion, Messwert, Testfehler oder konkrete Codegrenze.

## Nutzerwirkung

Was merkt ein normaler Nutzer davon?

## Ziel

Ein beobachtbares Ergebnis.

## Nicht-Ziele

- Was ausdrücklich nicht mitgebaut oder umstrukturiert wird.

## Erlaubte Pfade

- `pfad/zur/datei`

## Verbotene Änderungen

- Keine neue Architektur-, Speicher-, Policy- oder Berechtigungsgrenze.
- Keine realen Schlüssel, Konten, Nutzerdaten oder Provideraufrufe in Tests.

## Akzeptanzkriterien

- [ ] Positiver Nutzerweg.
- [ ] Verständlicher Fehlerweg.
- [ ] Bestehende Sicherheitszusagen bleiben erhalten.
- [ ] Dokumentation und Code beschreiben denselben Stand.

## Sabotageprobe

Welche kleine absichtliche Beschädigung muss welchen Test fehlschlagen lassen?

## Prüfkommandos

```bash
make verify
```

## Rückrollweg

Wie wird genau dieser PR ohne Datenverlust zurückgenommen?

## Offene Entscheidungen

Keine, sobald der Status `geprüft` lautet.
