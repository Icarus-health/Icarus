# Für Claude Code in diesem Projekt

## Der oberste Grundsatz

> **Immer die für den Nutzer einfachste Lösung.**

Nicht die einfachste zu bauende. Nicht die technisch sauberste. Die, bei der der
Mensch vor dem Bildschirm am wenigsten tun, wissen und entscheiden muss.

Wo Aufwand unvermeidbar ist, trägt ihn das Programm, nicht der Nutzer. Ein
Programm, das etwas selbst herausfinden kann, fragt nicht danach.

### Woran man das prüft

Bei jeder Änderung an der Oberfläche und an jedem neuen Einrichtungsschritt:

1. **Muss der Nutzer etwas wissen, das er nicht wissen kann?**
   Ein Feld „IMAP-Host" setzt Wissen voraus, das niemand außerhalb der IT hat.
   Richtig ist: Anbieter auswählen, den Rest füllt das Programm.

2. **Muss er dasselbe zweimal eingeben?**
   Dann ist einmal zu viel. Was schon dasteht, wird angeboten, nicht abgefragt.

3. **Muss er tippen, was er zeigen könnte?**
   Pfade tippt niemand fehlerfrei. Ein Auswahldialog, eine Liste, ein Klick.

4. **Sieht er, was passiert ist?**
   Ein Knopf, der drückt und nichts sagt, ist schlimmer als keiner. Jede
   Aktion antwortet — mit Ergebnis oder mit Grund.

5. **Kann er es rückgängig machen?**
   Wo nein, muss vorher gefragt werden. Wo ja, braucht es keine Rückfrage.

6. **Ist die Vorgabe die richtige?**
   Die meisten ändern nichts. Was voreingestellt ist, ist damit die Entscheidung
   für fast alle — und muss entsprechend sorgfältig gewählt sein.

### Wo der Grundsatz endet

Er hebt die Sicherheitszusagen **nicht** auf. „Einfach" heißt nie: ungefragt
etwas Außenwirksames tun, stillschweigend Fakten in den Bestand schreiben, oder
einen Ordner freigeben, den niemand freigegeben hat.

Wenn Einfachheit und Kontrolle sich widersprechen, gewinnt Kontrolle — aber
dann ist die Aufgabe, den kontrollierten Weg *einfacher zu machen*, nicht ihn
aufzugeben. Eine Rückfrage, die sein muss, soll in einem Satz verständlich sein
und mit einem Klick beantwortbar.

## Sprache

Code und Kommentare auf Deutsch, wie im Bestand. Bezeichner im Code englisch,
wo es die Konvention der Sprache verlangt (`def run`, `class Episode`), sonst
deutsch. Dokumentation unter `docs/` auf Deutsch, `README.md` auf Englisch.

**Nie `„…"` in einer Zeichenkette** — gleich in welcher Sprache. Das schließende
`"` beendet die Zeichenkette, und je nachdem, was danach steht, ist der Fehler
ein Syntaxfehler oder, schlimmer, keiner. Immer `„…“`. Das ist bisher dreimal
passiert, zweimal in JavaScript und einmal in Python.

Nach jeder Änderung an `app/src/main.js`: `node --check app/src/main.js`.

## Arbeitsweise

**Prüfen, nicht behaupten.** Eine Änderung an der Oberfläche gilt erst als
fertig, wenn sie im echten Browser lief. Chromium liegt unter
`/opt/pw-browsers/chromium-1194/chrome-linux/chrome`, Playwright ist im venv.
Zwei Fehler, die die App im Container unbenutzbar machten, waren beim Lesen des
Codes nicht zu sehen.

**Sabotageproben.** Nach einer neuen Zusicherung: die Zusicherung absichtlich
brechen und nachsehen, ob die richtigen Tests fehlschlagen. Ein Test, der nichts
fängt, ist schlimmer als keiner.

**Die Regel des Gedächtnisses.** Verdichtung schlägt vor, sie schreibt nicht.
Alles, was einen Fakt in den Bestand bringt, geht über einen Vorschlag und
dessen Annahme durch einen Menschen. Siehe `docs/10-verdichtung.md`.
