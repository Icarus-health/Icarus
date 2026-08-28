# Gestaltung

Wonach die Oberfläche aussieht und warum. Wer etwas Neues baut, richtet sich
danach; wer davon abweicht, sagt hier warum.

## Das Gefühl

> **Alter Adel mit modernem Wikinger.**

Sörens Formulierung, und sie trifft es genauer als jede Fachbeschreibung. Was
sie konkret heißt:

**Alter Adel** ist Zurückhaltung als Zeichen von Wert. Nichts ruft. Die Qualität
steckt im Material, im Abstand, im Satz — nicht in einem Effekt. Was teuer ist,
sagt es nicht.

**Moderner Wikinger** ist nicht Runenschrift und Hörner, sondern das nordische
Handwerk von heute: kühl, sachlich, aus wenigen guten Teilen, karg ohne arm zu
sein. Eiche, Wolle, Eisen, nasser Stein.

**Zusammen:** kühles Leinen statt warmem Pergament. Tinte statt Braun. Ein
einziger gedeckter Akzent statt einer Palette. Und immer: **make it simple.**

## Woran man das prüft

**Ist der Grund kühl?** Warmes Papier wirkt freundlich. Das hier soll nicht
freundlich wirken, sondern wertig. Der Ton zieht ins Grau-Grüne, nicht ins
Beige.

**Ruft irgendetwas?** Wenn ein Element auffällt, ohne dass es der wichtigste
Gegenstand der Ansicht ist, ist es zu laut. Genau ein gefüllter Knopf je
Ansicht. Die Akzentfarbe erscheint ein- bis zweimal auf dem Schirm, nicht
fünfmal.

**Ist es Material oder Farbe?** Eine Fläche liegt auf einem Grund: Lichtkante
oben, haarfeine Fassung, zwei Schatten — einer eng für die Kante, einer weit
für den Raum. Eine flache Farbe mit Rahmen sieht nach Formular aus.

**Trägt die Schrift?** Hierarchie entsteht über Größe und Strichstärke, nie
über einen Schriftwechsel. Eine zweite Familie in einer Anwendung sieht nach
Textverarbeitung aus. (Für Dokumente gilt das nicht — dort darf eine Serife
sprechen.)

**Steht Technik vorne?** Nie `state`, nie `0.72`, nie `confirm_strict`. Immer
der Satz, den ein Mensch dazu sagen würde.

## Die Werte

Alles steht in `app/src/style.css` als Token. Keine Farbe, kein Abstand, keine
Schriftgröße außerhalb davon — ein Festwert im Verlauf ist genau die Stelle,
an der eine Palette beim nächsten Wechsel auseinanderfällt und nachts hell aus
einer dunklen Fläche leuchtet.

| | Tag | Nacht |
| --- | --- | --- |
| Grund | `#f4f4f1` → `#e7e8e3` | `#16191a` → `#101314` |
| Fläche | `#fcfcfa` | `#212527` |
| Tinte | `#171a1a` | `#e9ecea` |
| Akzent | `#2f5069` | `#7ba7c4` |
| Warnung | `#8a6a2c` | `#c2a061` |
| Gefahr | `#8f403a` | `#cc8078` |
| Bestätigt | `#3d6b52` | `#6ea88a` |

Der Akzent ist **Eisen und kaltes Wasser**, kein Weblau. Er soll nach Tinte
aussehen und nicht nach Verweis. Warnung, Gefahr und Bestätigt sind Ocker,
Ochsenblut und Flechte — gedeckt, keine Signalfarben.

**Neun Schriftstufen**, benannt, keine dazwischen. **Vier Radien.** Abstände
auf einem 4-Punkt-Raster. **44 px** ist die kleinste Fläche, die ein Finger
sicher trifft.

## Wo der Grundsatz vorgeht

Diese Seite steht unter `CLAUDE.md`, nicht daneben. Wenn Eleganz und
Verständlichkeit sich widersprechen, gewinnt Verständlichkeit — aber dann ist
die Aufgabe, das Verständliche schön zu machen, nicht das Schöne aufzugeben.

Und keine dieser Regeln hebt eine Sicherheitszusage auf. Eine Freigabekarte
darf ruhig unschön sein, solange sie vollständig ist.
