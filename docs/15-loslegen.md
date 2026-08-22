# Loslegen

Für Sören, am Mac, beim ersten Mal. Stand 2026-08-22.

Zwei Wege, und der erste ist der kurze: er braucht nichts außer Python und ist
in Sekunden da. Der zweite ist der Container — mehr Trennung, mehr Aufwand.
Wenn du nicht weißt, welchen du willst, nimm den ersten.

## Der kurze Weg

Im Terminal, im Ordner mit dem Projekt:

```bash
make lokal NOTIZEN=~/Documents/Obsidian
```

Den Pfad hinter `NOTIZEN=` durch deinen Notizordner ersetzen. Wenn du gar
keinen freigeben willst, reicht `make lokal` — dann läuft Icarus, kann aber
keine Dateien lesen, und sagt das auch.

Beim allerersten Mal legt der Befehl eine Python-Umgebung an und installiert
den Sidecar. Das dauert einen Moment. Jeder weitere Start ist eine Sache von
Sekunden. Am Ende steht:

```
Icarus läuft:  http://127.0.0.1:8765/?token=9f3c…
```

Diese Adresse anklicken. Der Zeichensalat am Ende ist der Schlüssel zu deinem
Gedächtnis — ohne ihn antwortet Icarus nicht. Er wird einmal erzeugt und bleibt
danach gleich; ein Lesezeichen lohnt sich. Falls du ihn verlierst, steht er in
`.icarus.env` neben dem Projekt.

Beenden: Strg-C im selben Fenster.

## Der andere Weg: im Container

`make lokal` startet Icarus direkt auf dem Rechner. Das ist der kürzeste Weg
und braucht nichts außer Python. Was ihm fehlt, ist eine Prozessgrenze zwischen
Icarus und dem übrigen System.

Wer die will, nimmt den Container. Dafür muss **Docker Desktop laufen** — das
Wal-Symbol oben in der Menüleiste muss da sein. Dann:

```bash
make start NOTIZEN=~/Documents/Obsidian
```

Beim ersten Mal baut Docker das Bild: ein paar Minuten, etwa ein halbes
Gigabyte, einmal. Danach dasselbe Bild: eine Adresse mit Token. Anhalten mit
`make stop`, Adresse nachschlagen mit `make url`, mitlesen mit `make logs`.

Warum es beide Wege gibt, steht in
[`adr/0007-docker-als-zweiter-weg.md`](adr/0007-docker-als-zweiter-weg.md).

## Ein Modell anschließen

Ohne Modell funktioniert das Gedächtnis vollständig: Notizen einlesen,
Aussagen speichern, ansehen, widerrufen, exportieren. Nur Gespräche gehen
nicht.

Hinter dem Zahnrad oben rechts, Abschnitt „Modell". Vier Möglichkeiten:

- **OpenAI** oder **Anthropic** — Schlüssel eintragen, fertig.
- **Ollama** — läuft auf deinem Rechner, nichts geht nach draußen. Kein
  Schlüssel nötig.
- **Anderer Anbieter (OpenAI-kompatibel)** — für OpenRouter, Groq, Together,
  DeepSeek, Mistral, LM Studio, llama.cpp oder einen eigenen Server. Die
  Adresse steht als Vorschlag in der Liste.

Bei allen außer Anthropic holt „Modelle laden" die Liste beim Anbieter ab —
du musst keinen Modellnamen auswendig können. „Verbindung prüfen" schickt
wirklich eine Frage hin und zeigt die echte Antwort, nicht ein „gespeichert".

## Die Reihenfolge beim Einrichten

Beim ersten Öffnen läuft ein Assistent in fünf Schritten. **Jeder ist
überspringbar**, und Icarus funktioniert auch, wenn du alle überspringst. Wenn du
ihn wegklickst oder später etwas ändern willst: oben rechts das Zahnrad.

Die Reihenfolge, die sich lohnt:

**1. Modell.** Anbieter und API-Schlüssel. Ohne das funktioniert alles außer dem
Gespräch — Aussagen aufnehmen, Notizen einlesen, exportieren geht offline. Wenn
du noch keinen Schlüssel zur Hand hast, überspring den Schritt und hol ihn
später nach; Icarus wird davon nicht kaputt. Der Knopf „Verbindung prüfen“
probiert es wirklich aus und zeigt den echten Fehler, nicht ein „gespeichert“.

**2. Ordner.** Steht schon drin, wenn du oben `NOTIZEN=` gesetzt hast — nichts
abzutippen. Beim Containerweg heißt dein Ordner dort `/notizen`, nicht
`/Users/soeren/Documents/Obsidian`; er ist **nur lesbar** eingebunden, Icarus
kann dort nichts ändern oder löschen.

Danach in der **Ablage** unter „Eingelesenes“ einmal einlesen. Der Ordner steht
in der Auswahlliste. Das dauert bei einem gewachsenen Vault eine Weile.

**3. Mail und Kalender.** Kann warten. Der Assistent weist nur darauf hin, dass
es das gibt; eingerichtet wird es hinter dem Zahnrad, wenn du es brauchst.

**4. Zeitplan.** Hinter dem Zahnrad, Abschnitt „Mitlaufen“. Standardmäßig aus,
und das ist Absicht. Schalte ihn erst ein, **nachdem** du einmal etwas eingelesen
und die erste Runde Vorschläge angesehen hast — sonst hast du eine volle
Vorschlagsschlange, bevor du weißt, ob dir die Vorschläge gefallen. Vier Stunden
ist die Voreinstellung und für den Anfang richtig.

Das Häkchen für die Modellnutzung im Zeitplan ist getrennt. Ohne es bekommst du
Aufnahme, Fälligkeitsfragen und Sicherung, ohne dass ein einziger API-Aufruf
Geld kostet. Mehr dazu: [`11-zeitplan.md`](11-zeitplan.md).

## Was beim ersten Mal normal ist

- **Das Gedächtnis ist leer.** Es gibt kein Konto, aus dem etwas geladen würde.
  Der Bestand entsteht aus dem, was du einliest und annimmst.
- **Icarus schreibt nichts von selbst in den Bestand.** Was aus deinen Notizen
  entsteht, landet in der Ablage unter „Zu klären“ und wartet dort auf dich. Das ist die
  Grundregel und keine Einstellung, die man umlegen kann.
- **Die semantische Suche fehlt.** Das Container-Bild kommt bewusst ohne
  `cognee` (rund 950 MB). Gesucht wird solange über Teilzeichenketten. Für den
  Anfang reicht das.
- **Kein Computer-Use.** Ein Container hat keinen Bildschirm, den er bedienen
  könnte. Das kommt nie über diesen Weg, sondern nur über die Mac-App.
- **Einstellungen greifen sofort.** Kein Neustart nach dem Eintragen eines
  Schlüssels oder eines Ordners.

## Weitermachen und aufhören

```bash
make logs    # mitlesen, was Icarus tut; Strg-C beendet nur das Mitlesen
make stop    # anhalten
make start   # weitermachen, mit demselben Gedächtnis
```

`make stop` hält nur an. Das Gedächtnis liegt in einem Docker-Volume und bleibt,
die Schlüssel liegen in `.icarus.env` und bleiben auch. Nach einem Neustart des
Macs kommt Icarus von selbst wieder hoch, sobald Docker Desktop läuft.

**`.icarus.env` nicht löschen und nicht weitergeben.** Darin stehen zwei Dinge:
das Token aus der Adresse und die Passphrase, mit der deine API- und
Mailpasswörter verschlüsselt im Datenvolume liegen. Ist die Passphrase weg, sind
diese Schlüssel unlesbar und müssen neu eingetragen werden — das Gedächtnis
selbst bleibt davon unberührt. Die Datei ist von Git ausgeschlossen und nur für
dich lesbar.

Willst du wirklich alles loswerden, inklusive Gedächtnis:

```bash
docker compose --env-file .icarus.env down -v
```

Das `-v` löscht das Volume. Es gibt kein Zurück, also vorher unter „Was ich weiß“
exportieren.

## Wenn etwas klemmt

**„Cannot connect to the Docker daemon“** — Docker Desktop läuft nicht.

**Die Seite lädt nicht.** Erst `make logs` ansehen. Wenn dort nichts Auffälliges
steht, `make url` und die Adresse frisch kopieren — eine abgeschnittene URL ohne
vollständiges Token sieht aus wie ein kaputter Server.

**„Kein Zugriff“ oder eine leere Anzeige.** Meist ein unvollständiges Token in
der Adresszeile. `make url`.

**Der Notizordner ist leer, obwohl er es nicht ist.** Docker Desktop muss den
Ordner freigeben dürfen: Docker Desktop → Settings → Resources → File Sharing.
Alles unter `/Users` ist normalerweise schon dabei.

**Port 8765 ist belegt.** Meistens läuft Icarus schon. `make url` und öffnen.

## Verwandte Dokumente

- [`09-einrichtung.md`](09-einrichtung.md) — was der Assistent tut und warum
- [`11-zeitplan.md`](11-zeitplan.md) — was mitläuft und was nie ohne dich passiert
- [`10-verdichtung.md`](10-verdichtung.md) — warum Vorschläge und nicht Fakten
- [`adr/0007-docker-als-zweiter-weg.md`](adr/0007-docker-als-zweiter-weg.md) — die Grenzen dieses Wegs
