# Aufgabenpakete

Aufgabenpakete sind die Übergabeschicht zwischen Frontier-Planung,
Ausführungsmodellen, menschlicher Entscheidung und CI. Ein Paket muss ohne
versteckten Chatverlauf verständlich sein.

## Verzeichnisse

- `templates/` enthält die verbindlichen Vorlagen.
- `ready/` enthält geprüfte, noch nicht begonnene Pakete.
- Ein begonnenes Paket wird im PR verlinkt. Sein Status wird dort gepflegt;
  parallele Kopien mit unterschiedlichem Inhalt sind verboten.

## Nutzung mit Chat-Coding

1. Das vollständige Paket in einen neuen Coding-Chat geben.
2. Dazu nur sagen: „Setze genau dieses Paket um. Ändere keine anderen Pfade.
   Zeige am Ende Diff, Testbefehle und Ergebnisse.“
3. Code auf einem eigenen Branch erzeugen lassen.
4. Diff und echte Testausgabe in den Draft-PR übernehmen.
5. Review nach `templates/review-packet.md` durchführen lassen.

Ein Chat darf Code erzeugen. Er darf weder seinen eigenen Scope erweitern noch
seine eigene Qualitätsstufe bestätigen.

## Namensschema

`NNNN-kurzer-zweck.md`, zum Beispiel `0001-dashboard-refresh-begrenzen.md`.

## Mindestinhalt

Problembeleg, Nutzerwirkung, Risikoklasse, Rollen, Ziel, Nicht-Ziele, erlaubte
Pfade, verbotene Änderungen, Akzeptanzkriterien, Sabotageprobe, Prüfkommandos,
Rückrollweg und offene Entscheidungen.
