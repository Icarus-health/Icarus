## Aufgabenpaket

- Paket: `tasks/...`
- Risiko: A | B | C
- Umsetzung: Frontier-Modell | qualifiziertes Ausführungsmodell | Mensch
- Unabhängiger Review: ausstehend | erfolgt

## Ergebnis

Was ist für den Nutzer oder Betrieb jetzt beobachtbar anders?

## Scope

- Geänderte erlaubte Pfade:
- Bewusst nicht geändert:
- Abweichungen vom Paket: keine | begründet und neu geprüft

## Nachweis

```text
make verify
<echte Ausgabe oder Link zum CI-Lauf>
```

- Zusatzgates des Pakets:
- Sabotageprobe und erwarteter Fehlschlag:
- Echter Browser-/Restore-/Paketlauf, falls erforderlich:

## Risiken und Rückrollweg

- Verbleibendes Risiko:
- Rückrollweg:
- Datenmigration oder Außenwirkung: keine | Beschreibung

## Checkliste

- [ ] Ein Aufgabenpaket, ein Ziel, ein PR.
- [ ] Keine realen Schlüssel, Konten, Nutzerdaten oder Provideraufrufe in Tests.
- [ ] Neue Zusicherungen besitzen einen Negativtest.
- [ ] Dokumentation und Code stimmen überein.
- [ ] Testausgaben stammen vom finalen Commit.
