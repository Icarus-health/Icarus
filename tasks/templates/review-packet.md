# Unabhängiger Review

Der Reviewer erhält Aufgabenpaket, Diff und Testausgabe. Er übernimmt keine
Behauptung des Implementierers ungeprüft.

## Auftragstreue

- [ ] Das beobachtbare Ziel ist vollständig erreicht.
- [ ] Nur erlaubte Pfade wurden geändert.
- [ ] Nicht-Ziele und Produktgrenzen wurden eingehalten.
- [ ] Der Diff enthält keine versteckte Erweiterung oder breite Bereinigung.

## Korrektheit

- [ ] Fehler-, Leer-, Neustart- und Parallelitätsfälle sind berücksichtigt.
- [ ] Tests prüfen Verhalten statt Implementierungsdetails.
- [ ] Die Sabotageprobe würde die neue Zusicherung tatsächlich brechen.
- [ ] Testausgaben stammen vom finalen Commit.

## Icarus-Grenzen

- [ ] Gedächtnis, Provenienz und Löschung bleiben korrekt.
- [ ] Keine zweite Policy-, Freigabe-, Identitäts- oder Speicherstrecke.
- [ ] Fremder Inhalt bleibt untrusted.
- [ ] Keine Außenwirkung ohne den produktiven Freigabeweg.
- [ ] Tests berühren weder reale Keychains noch externe Provider.

## Nutzer und Betrieb

- [ ] Ein normaler Nutzer versteht Ergebnis und Fehler.
- [ ] Ressourcen und Anfragen sind begrenzt.
- [ ] Rückroll- und Wiederherstellungsweg sind glaubwürdig.
- [ ] Dokumentation entspricht dem finalen Verhalten.

## Ergebnis

`freigeben`, `Änderungen erforderlich` oder `Risikoklasse neu bewerten` – mit
konkreter Datei, Beobachtung und erwarteter Korrektur.
