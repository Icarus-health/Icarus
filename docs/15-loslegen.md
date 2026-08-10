# Loslegen

Für Sören, am Mac, beim ersten Mal. Stand 2026-08-02.

Das hier ist kein Optionsvergleich, sondern der eine Weg, der funktioniert.
Warum es den Containerweg überhaupt gibt und wo seine Grenzen liegen, steht in
[`adr/0007-docker-als-zweiter-weg.md`](adr/0007-docker-als-zweiter-weg.md).

## Einmal vorher

Docker Desktop muss laufen. Das Wal-Symbol oben in der Menüleiste muss da sein —
wenn nicht, Docker Desktop öffnen und warten, bis es aufhört zu blinken. Sonst
sagt der nächste Schritt nur, er finde keinen Docker-Daemon.

## Was du tippst

Im Terminal, im Ordner mit dem Projekt:

```bash
make start NOTIZEN=~/Documents/Obsidian
```

Den Pfad hinter `NOTIZEN=` durch deinen Notizordner ersetzen. Wenn du gar keinen
freigeben willst, reicht:

```bash
make start
```

Dann läuft Icarus, kann aber keine Dateien lesen — und sagt das auch. Der Ordner
lässt sich jederzeit nachreichen: `make stop`, dann `make start NOTIZEN=…`.

## Was dann passiert

Beim allerersten Mal baut Docker das Bild. Das dauert ein paar Minuten und zieht
etwa ein halbes Gigabyte — einmal. Jeder weitere Start ist eine Sache von
Sekunden.

Danach steht am Ende so etwas:

```
Icarus läuft. Diese Adresse im Browser öffnen:

  http://127.0.0.1:8765/?token=9f3c…
```

Diese Adresse anklicken oder kopieren. Das lange Zeichensalat am Ende ist der
Schlüssel zu deinem Gedächtnis — ohne ihn antwortet Icarus nicht. Er wird beim
ersten `make start` erzeugt und bleibt danach gleich, du musst ihn dir also nicht
merken. Lesezeichen setzen lohnt sich; falls du es doch verlierst:

```bash
make url
```

## Die Reihenfolge beim Einrichten

Beim ersten Öffnen läuft ein Assistent in fünf Schritten. **Jeder ist
überspringbar**, und Icarus funktioniert auch, wenn du alle überspringst. Wenn du
ihn wegklickst oder später etwas ändern willst: Reiter „Einrichtung“.

Die Reihenfolge, die sich lohnt:

**1. Modell.** Anbieter und API-Schlüssel. Ohne das funktioniert alles außer dem
Gespräch — Aussagen aufnehmen, Notizen einlesen, exportieren geht offline. Wenn
du noch keinen Schlüssel zur Hand hast, überspring den Schritt und hol ihn
später nach; Icarus wird davon nicht kaputt. Der Knopf „Verbindung prüfen“
probiert es wirklich aus und zeigt den echten Fehler, nicht ein „gespeichert“.

**2. Ordner.** Hier steht `/notizen` schon drin, wenn du oben `NOTIZEN=` gesetzt
hast — nichts abzutippen. Wichtig zu wissen: Im Container heißt dein Ordner
`/notizen`, nicht `/Users/soeren/Documents/Obsidian`. Er ist **nur lesbar**
eingebunden, Icarus kann dort nichts ändern oder löschen.

Danach im Reiter „Rohmaterial“ einmal einlesen. `/notizen` steht in der
Auswahlliste. Das dauert bei einem gewachsenen Vault eine Weile.

**3. Mail und Kalender.** Kann warten. Der Assistent weist nur darauf hin, dass
es das gibt; eingerichtet wird es unter „Einrichtung“, wenn du es brauchst.

**4. Zeitplan.** Unter „Einrichtung“, Abschnitt „Mitlaufen“. Standardmäßig aus,
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
  entsteht, landet unter „Vorschläge“ und wartet dort auf dich. Das ist die
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

## Privater Betakanal

Wenn du neue Icarus-Funktionen vor dem normalen Containerkanal ausprobieren
willst, gibt es einen zweiten, absichtlich getrennten Weg:

```bash
make beta-start
```

Er lädt ausschließlich das von GitHub CI veröffentlichte Image `:beta` und
öffnet Icarus auf Port **8877**. Der Betakanal hat ein eigenes Docker-Volume,
eigenes Token und eine eigene Schlüsseldatei (`.icarus-beta.env`). Er kann daher
weder dein Gedächtnis noch deine Zugangsdaten aus dem normalen Kanal auf Port
8765 lesen oder verändern.

```bash
make beta-url    # Adresse noch einmal ausgeben
make beta-logs   # Meldungen mitlesen
make beta-stop   # nur Beta anhalten, Daten bleiben erhalten
```

Ein neues Beta-Image wird erst nach erfolgreich durchgelaufener CI gebaut. Das
Update selbst startest du bewusst mit `make beta-start`; Icarus aktualisiert
einen persönlichen Arbeitsbestand nicht still im Hintergrund.

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

Das `-v` löscht das Volume. Es gibt kein Zurück, also vorher unter „Gedächtnis“
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
