# Zweiter Entwurf — Icarus als Mac-Anwendung

Der erste Entwurf unter `../entwurf/` hat ein Web-Dashboard aufgeräumt. Ein
aufgeräumtes Dashboard ist immer noch ein Dashboard: Reiterleiste oben,
Karten im Raster, neben jeder Zeile ein Bedienelement. Das ist eine
Verwaltungsoberfläche mit besseren Abständen, keine Anwendung.

Dieser Entwurf beginnt neu. Die Beschriftungen sind englisch, weil sie so
angefragt wurden; die ausgelieferte Anwendung bleibt deutsch. Die
Übersetzung ändert keine der Entscheidungen.

## Die Flächen

| Datei | Was sie zeigt |
| --- | --- |
| `Main.dc.html` | Today — der Startbildschirm. Drei Sätze, sonst nichts. |
| `Reveal.dc.html` | Derselbe Bildschirm unter dem Zeiger: wo die Knöpfe sind. |
| `Approval.dc.html` | Der Moment, bevor etwas den Rechner verlässt. |
| `Ask.dc.html` | Gespräch, mit abgesetztem fremdem Inhalt. |
| `Library.dc.html` | Der Bestand als eine Liste. |
| `Welcome.dc.html` | Erster Start — eine Frage. |

## Die Entscheidungen

**Der Startbildschirm ist drei Sätze.** Nicht drei Karten, die drei Sätze
enthalten. Keine Zähler, keine Etiketten, keine Werkzeugleiste, kein Raster.

**Nichts ist ein Knopf, bis jemand danach greift.** Weniger Knöpfe heißt
nicht weniger Handlungen — es heißt, dass die Handlung dort sitzt, wo die
Sache steht, und unsichtbar bleibt, bis der Zeiger da ist. `Reveal.dc.html`
zeigt diesen Zustand, weil ein Entwurf, der seine Bedienelemente versteckt,
ein Bild davon schuldet, wohin sie gegangen sind.

**Die Akzentfarbe erscheint einmal.** Im ersten Wurf stand „drei Mal“ in der
Notiz und fünf Mal auf dem Schirm. Begriffe im Lesetext tragen jetzt eine
feine Grundlinie und färben sich erst unter der Hand.

**Die Seitenleiste trägt die Struktur, die Inhaltsfläche trägt nichts.** Drei
Orte, Einstellungen unten angeheftet. Unterteilungen stehen ebenfalls in der
Seitenleiste — keine zweite Reiterreihe über dem Inhalt.

**Schriftstufen:** 40 → 21 → 15 → 11,5. Vier Stufen, große Sprünge, eine
Familie. `-apple-system` zuerst, damit auf dem Mac die echte SF steht.

## Weiterhin: erkennen statt abtippen

`confirm_strict` verlangt heute den Empfänger als getippte Phrase — und der
steht direkt darüber und wird kopiert, ohne gelesen zu werden. Drei Adressen,
eine ist richtig. Gegen den Angriff, für den die Stufe gedacht ist, ist das
stärker.
