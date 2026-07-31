# Einrichtung und Erststart

Stand 2026-07-31. Verbindlich für `sidecar/icarus_memory/config.py` und den
Assistenten in `app/src/main.js`.

Bis hierher kam alles aus Umgebungsvariablen: Anbieter, Schlüssel, Mailserver,
freigegebene Ordner. Für eine Entwicklungsumgebung ist das richtig. Für eine App,
die jemand herunterlädt, ist es das Ende — **niemand legt eine `.env` an, bevor
er ein Programm zum ersten Mal öffnet.**

## Es gibt kein Konto

Icarus kennt keinen Server, bei dem man sich anmelden könnte. Das ist keine
fehlende Funktion, sondern die Prämisse des Projekts: Der Bestand liegt auf dem
Rechner der Person.

Was beim Einrichten passiert, ist deshalb kein Login, sondern eine einzige
Frage — **welchem Anbieter wird das Gespräch anvertraut?** Und die Antwort darf
„keinem" sein. Ohne Modell bleibt das Gedächtnis vollständig nutzbar: Aussagen
aufnehmen, ersetzen, widerrufen, exportieren und Notizen einlesen funktioniert
offline.

## Zwei Ablagen, klar getrennt

| Wohin | Was | Warum |
| --- | --- | --- |
| `einstellungen.json` (0600) | Anbieter, Modell, Serveradressen, freigegebene Ordner | Muss lesbar und sicherbar sein |
| Schlüsselbund | API-Schlüssel, Mail- und CalDAV-Passwort | Landet nie auf der Platte |

Die Trennung ist keine Förmlichkeit. Die Einstellungsdatei landet in Backups, in
Sicherungen des Datenverzeichnisses, womöglich in einem Cloud-Ordner. Ein
Schlüssel darin wäre genau der Klartext, den [`secrets.py`](../sidecar/icarus_memory/secrets.py)
vermeiden soll.

**Ohne Schlüsselspeicher** — Linux ohne `secret-tool`, manche Serverumgebungen —
gilt ein eingetragener Schlüssel nur für die laufende Sitzung. Er wird
ausdrücklich **nicht** ersatzweise in die Einstellungsdatei geschrieben, und die
Oberfläche sagt das beim Öffnen. Lieber unbequem als Klartext auf der Platte.

## Vorrang

Umgebungsvariablen schlagen die Datei. Wer `ICARUS_PROVIDER=ollama` vor den
Start setzt, bekommt Ollama, egal was eingestellt ist — der Weg, einen Testlauf
zu fahren, ohne die Einstellungen des Nutzers anzufassen. Dieselbe Regel wie in
`secrets.load_into_env()`.

## Ohne Neustart

`PUT /setup` baut Konnektoren, Werkzeuge und Agent neu (`_build_agent`). Wer
einen Schlüssel einträgt, kann unmittelbar danach sprechen; wer einen Ordner
freigibt, kann unmittelbar danach einlesen.

Das ist kein Komfortdetail. Ein Programm, das nach jeder Einstellung einen
Neustart verlangt, wird beim ersten Versuch weggelegt — und der erste Versuch
ist der einzige, den die meisten Menschen unternehmen.

Der Gesprächsverlauf geht dabei absichtlich verloren: Ein Verlauf, der vor einem
Anbieterwechsel entstand, gehört einem anderen Modell.

## Der Assistent beim Erststart

Fünf Schritte, **jeder überspringbar**:

1. **Willkommen** — was Icarus ist, dass nichts den Rechner verlässt
2. **Modell** — Anbieter und Schlüssel, oder keins
3. **Ordnerzugriff** — welche Ordner gelesen werden dürfen
4. **Vorhandene Notizen** — Obsidian, Notion oder Textdateien einlesen
5. **Mail und Kalender** — Hinweis, dass das später geht

Danach nie wieder von selbst. Wer ihn erneut will, geht auf „Einrichtung".

Zwei Entscheidungen daran sind Absicht:

**Jeder Schritt ist überspringbar, und am Ende funktioniert Icarus.** Ein
Assistent mit Pflichtfeldern erzeugt Abbrüche an genau der Stelle, an der jemand
das Programm noch nicht kennt und deshalb nichts eintragen *kann*.

**Ein fehlgeschlagener Schritt bleibt stehen.** Weiterzuspringen würde
suggerieren, es habe geklappt — und der Nutzer sucht den Fehler später an der
falschen Stelle.

## Verbindungen werden geprüft, nicht behauptet

`POST /setup/test/{modell|mail|kalender}` probiert die Verbindung wirklich aus
und gibt den **echten** Fehler zurück. Ein Einrichtungsassistent, der
„gespeichert" sagt und beim ersten Gebrauch scheitert, ist schlimmer als keiner.

## Kein Vorgabeordner

`file_roots` ist leer und bleibt leer, bis jemand etwas einträgt. Weder der Code
noch der Assistent schlagen einen vor. Ein voreingestelltes Home-Verzeichnis
wäre die Bequemlichkeit, die den Schutz aufhebt — dieselbe Begründung wie in
[`05-sicherheit.md`](05-sicherheit.md).

## Was offen ist

**Kein Dateiauswahldialog.** Ordner werden getippt. Tauri kann einen nativen
Dialog öffnen; das ist der nächste Schritt, weil einen Pfad abzutippen genau die
Art Reibung ist, die dieses Dokument beseitigen soll.

**Der Assistent fragt nicht nach der Person.** Er richtet Technik ein, aber
Icarus weiß danach immer noch nichts über den Nutzer. Sobald die Verdichtung
steht, gehört ein Schritt dazu, der aus dem eingelesenen Material die ersten
Aussagen **vorschlägt** — vorlegen, nicht schreiben.

**Kein Zurück im Assistenten.** Wer sich vertippt, geht danach auf
„Einrichtung". Vertretbar, aber nicht schön.

## Verwandte Dokumente

- [`05-sicherheit.md`](05-sicherheit.md) — warum es keinen Vorgabeordner gibt
- [`08-gedaechtnisschichten.md`](08-gedaechtnisschichten.md) — was beim Einlesen passiert
- [`07-mcp-tuer.md`](07-mcp-tuer.md) — dasselbe Gedächtnis für andere Assistenten
