# Entwurf der Oberfläche

Die Quelldateien der Entwurfsleinwand — je eine Fläche pro `.dc.html`,
`canvas.json` legt sie an ihren Platz. Die zusammengebaute Leinwand selbst
steht nicht hier (siehe `.gitignore`); sie wird aus diesen Dateien erzeugt.

## Die These

Icarus ist kein Dashboard, sondern ein **Briefing**. Der Startbildschirm ist
ein kurzer Text, der sagt, was heute zählt — kein Raster aus Kacheln.

Aus acht Reitern werden drei Orte: **Heute, Gespräch, Ablage**. „Rohmaterial“,
„Vorschläge“, „Gedächtnis“ und „Protokoll“ sind keine Orte mehr. Das sind
Namen für Zustände der Maschine, nicht für etwas, das ein Mensch tun will.
Sie werden zur zweiten Ebene: hinter jedem Satz liegt „Woher ich das weiß“,
und das geht auf, wenn jemand fragt.

## Die Flächen

| Datei | Was sie zeigt |
| --- | --- |
| `Main.dc.html` | Heute — das Briefing. Der Kern des Entwurfs. |
| `Freigabe.dc.html` | Das Blatt, das eine außenwirksame Handlung vorlegt. |
| `Gespraech.dc.html` | Gespräch, mit gerahmtem fremdem Inhalt und Vorschlag im Fluss. |
| `Projekt.dc.html` | Ein Projekt: Stand in einem Satz, offene Punkte, Entschiedenes. |
| `Kontakt.dc.html` | Ein Mensch. Gibt es im Bestand noch nicht. |
| `Herkunft.dc.html` | Die zweite Ebene: Kette, Wortlaut, Aktualität, Abhängiges. |
| `Einrichtung.dc.html` | Erster Start — erst der Nutzen, dann die Technik. |
| `Bausteine.dc.html` | Farben, Schriften, Etiketten, Ablehnung, Nachtansicht. |

## Drei Änderungen an der Produktvision

1. **Freigaben sind kein Ort.** `docs/00-produktvision.md` §5.1 führt sie als
   sechsten Reiter der Alltagsebene. Was in einem Reiter wartet, wird
   vergessen. Eine Freigabe muss kommen, nicht abgeholt werden.

2. **Erkennen statt abtippen.** `confirm_strict` verlangt heute den Empfänger
   als getippte Phrase — und der steht direkt darüber und wird kopiert, ohne
   gelesen zu werden. Stattdessen drei Adressen, eine ist richtig: ein Klick
   statt einer Zeile, und geprüft wird, was zählt. Gegen den Angriff, gegen
   den die Stufe gedacht ist — eine untergeschobene Anweisung setzt eine
   fremde Adresse — ist Erkennen stärker als Abtippen.

3. **Menschen fehlen ganz.** Die Vision verspricht ein Beziehungsmodell;
   gebaut ist nur `Episode.participants` als Namensliste. Genau daraus lässt
   sich `Kontakt.dc.html` bauen, ohne ein neues Datenmodell.

Dazu: der gebaute Assistent fragt nach Modellanbieter, Ordnerpfad und IMAP.
Die Vision fragt zuerst „Wobei soll Icarus dir zuerst helfen?“ (§10.2). Der
Entwurf tut, was das Papier sagt.

## Gestaltung

Die Farbwerte sind unverändert die aus `app/src/style.css` — Papier `#fbfaf8`,
Blau `#3b6ea5`, `#a8621f`, `#b4483c`, dieselben Radien. Neu sind drei Dinge:

- **Newsreader** für alles, was Icarus *sagt*
- **Instrument Sans** für die Bedienung
- **`#2f7a55`** für Bestätigtes — die Palette hatte kein Grün

Und eine Regel: nie `state`, nie `0.72`, nie `confirm_strict`. Immer der Satz,
den ein Mensch dazu sagen würde.
