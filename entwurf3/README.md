# Dritter Entwurf — das Dashboard, wertig

Der zweite Entwurf (`../entwurf2/`) hat das Dashboard abgeschafft: eine
Lesespalte, keine sichtbaren Knöpfe. Das war zu radikal geschnitten. Ein
Chief of Staff braucht eine Übersicht, auf der alles Wichtige gesammelt ist
und von der aus fast alles geht.

Dieser Entwurf holt das Dashboard zurück — aber ohne die Kachelwand des
ersten Versuchs. Die Beschriftungen sind englisch; die ausgelieferte
Anwendung bleibt deutsch.

## Die Flächen

| Datei | Was sie zeigt |
| --- | --- |
| `Main.dc.html` | Das Dashboard. Drei Dinge, drei Karten, ein Feld. |
| `Prepare.dc.html` | Eine Sache geöffnet — alles zu einem Termin an einem Ort. |
| `Approval.dc.html` | Der Moment, bevor etwas den Rechner verlässt. |
| `Settings.dc.html` | Vier Zeilen und ein Schalter. |

## Was „wertig“ hier konkret heißt

**Material statt Farbe.** Der Grund trägt Licht — eine warme Quelle oben
links, eine kühle Spiegelung rechts — und darüber feines Korn. Eine flache
Farbe sieht nach Vorlage aus, das hier nach Fläche.

**Jede Karte hat Physik.** Eine Lichtkante oben, eine haarfeine Fassung und
zwei Schatten: einer eng für die Kante, einer weit für den Raum, in dem sie
liegt. Das ist der Unterschied zwischen „gebaut“ und „gezeichnet“.

**Knöpfe, die man drücken will.** Verlauf, Lichtkante oben, dunklere Lippe
unten, Fallschatten — der Knopf sitzt auf der Fläche, statt hineingezeichnet
zu sein. Einer je Ansicht ist gefüllt; alle anderen tragen dieselbe Physik
ohne Farbe.

**Eine Rundungsreihe:** 22px Fenster, 20px Karten, 14px Eingabefeld, 9px
Knöpfe. Kein Wert dazwischen.

## Die Struktur

Sechs Knöpfe auf dem ganzen Bildschirm. Drei Dinge heute mit je genau einem
Knopf, drei Karten für das Nächste, das Geschriebene und das Gelernte, und
unten ein Feld zum Fragen oder Merkenlassen. Alles Tiefere ist von hier
erreichbar, nichts Tieferes ist nötig.

Die Einstellungen sind vier Zeilen und ein Schalter; Modell, Konnektoren,
Protokoll und Sicherungen liegen hinter einem Wort. Die Leere darunter ist
die Botschaft.

## Offen

**Der Grundton.** Warmes Papier, weil das die bestehende Palette ist. Kühler
und grauer wäre technischer, wärmer und cremiger wohnlicher.

**Die Dichte.** Bei zwölf offenen Sachen statt drei wird das Dashboard lang.
Dann ist zu entscheiden: scrollen, oder bekommt die Karte einen Zustand
„mehr“.
