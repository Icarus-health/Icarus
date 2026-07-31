# ADR 0007: Docker als zweiter Auslieferungsweg

**Status:** akzeptiert · **Datum:** 2026-07-31

## Kontext

[ADR 0006](0006-tauri-desktop.md) hat entschieden, dass Icarus eine eigene Tauri-App wird statt eines Docker-Stacks mit Open WebUI als Oberfläche. Das ist keine andere Frage als die hier: ADR 0006 hat verworfen, dass Docker die **Oberfläche** stellt, weil das überprüfbare Selbstmodell und der Freigabe-Layer keine Plugins in einer fremden Web-Oberfläche sein dürfen. Diese Entscheidung bleibt unberührt.

Die Frage hier ist eine andere: wie kommt der Sidecar — der Gedächtniskern ohne Oberfläche — zu jemandem, der ihn ausprobieren will, aber (noch) keine signierte macOS-App bekommen kann? Ein Nutzer ohne Apple-Developer-Konto kann derzeit keine verteilbare Mac-App bauen — `make app-build` läuft nur auf macOS und muss signiert und notarisiert werden, sonst blockt Gatekeeper (ADR 0006, Konsequenzen). Bis dieses Konto existiert, gibt es keinen Weg, den ein Dritter ohne Xcode und eigenes Zertifikat gehen kann.

Docker schließt genau diese Lücke, ohne die App-Entscheidung zu berühren: ein Image lässt sich bauen, veröffentlichen und ausführen, ohne dass irgendetwas signiert werden muss.

## Entscheidung

Der Sidecar bekommt einen zweiten, **zusätzlichen** Auslieferungsweg über ein Container-Image, veröffentlicht nach GHCR (`ghcr.io/icarus-health/icarus`). Das ersetzt die App nicht und ist nicht als gleichwertige Betriebsform gedacht — es ist der Weg für alle, die den Gedächtniskern anfassen wollen, ohne auf ein signiertes Bundle zu warten oder Rust/Node/Python lokal einzurichten.

Die Tauri-App bleibt der primäre Weg für den eigentlichen Nutzer: Computer-Use, OS-Schlüsselbund und die volle Oberfläche gibt es nur dort (siehe Konsequenzen).

## Aufbau

- **Multi-Stage-Dockerfile** (`Dockerfile`, Repo-Wurzel): eine Build-Stufe installiert den Sidecar mit `pip install ./sidecar` — bewusst ohne das Extra `cognee`, das laut ADR 0005 rund 950 MB nachzieht und für einen ersten Testlauf nicht nötig ist. Die Laufzeit-Stufe enthält nur das installierte Paket, läuft als eigener, unprivilegierter Benutzer und besitzt das Datenverzeichnis `/daten` (Standard für `ICARUS_DATA_DIR`).
- **`compose.yaml`**: bindet den Port zwingend an `127.0.0.1`, nutzt ein Named Volume für `/daten` und reicht `ICARUS_SIDECAR_TOKEN` aus der Umgebung durch. Ein Bind-Mount für Notizen ist als Beispiel einkommentiert, nicht aktiv.
- **`.github/workflows/container.yml`**: baut bei jedem Pull Request (ohne zu veröffentlichen), veröffentlicht bei Push auf `main` (Tag `latest`) und bei Tags `v*` (Semver-Tags) nach GHCR, für `linux/amd64` und `linux/arm64` — Apple Silicon läuft damit ebenfalls, nur eben in einem Linux-Container statt der nativen App.
- **Die Oberfläche liegt mit im Bild.** Im Container gibt es keine Tauri-Hülle, also liefert der Sidecar die HTML/JS-Dateien selbst aus (`ICARUS_UI_DIR`), und man öffnet ihn im Browser. Die Seite selbst ist nicht durch das Token geschützt — sie enthält kein Nutzerdatum, und wäre sie es, könnte der Browser sie nie laden, ohne das Token schon zu kennen. Geschützt ist alles darunter. Das Token kommt als `?token=` in die URL, wie bei Jupyter und aus demselben Grund.
- Ein Rauchtest im Workflow startet den Container und prüft vier Dinge: `/health` antwortet, ein Datenzugriff **ohne** Token wird mit 401 abgewiesen, **mit** Token gelingt er, und die Oberfläche wird ausgeliefert. Die zweite Prüfung ist die wichtigste — ein Bild, das versehentlich offen ausliefert, legt das gesamte Gedächtnis frei, und das darf nie unbemerkt durch die CI kommen.

## Konsequenzen

**Die Portfreigabe auf `127.0.0.1` ist Bedingung, keine Empfehlung.** Der Sidecar bindet im Container an `0.0.0.0` (`ICARUS_SIDECAR_HOST`; Standard bleibt überall sonst `127.0.0.1`), weil sonst die Portfreigabe von Docker ins Leere liefe — `127.0.0.1` wäre dort das Loopback des Containers, nicht das des Hosts. Eine Freigabe wie `8765:8765` ohne Host-Adresse bindet Docker aber standardmäßig an alle Interfaces — dann ist das gesamte persönliche Gedächtnis (Selbstmodell, Mail- und Kalenderdaten, Notizen) für jeden im selben lokalen Netz erreichbar. `compose.yaml` schreibt deshalb `127.0.0.1:8765:8765` fest vor, mit einem Kommentar direkt davor, der das erklärt — nicht als Stilfrage, sondern weil ein falsch gesetzter Doppelpunkt hier die gesamte Schutzwirkung des Loopback-Bindings aus ADR 0006 aufheben würde.

**Kein Betriebssystem-Schlüsselbund im Container.** ADR 0006 verlässt sich für Geheimnisse (API-Schlüssel, Mail- und CalDAV-Passwörter) auf den Schlüsselbund des Betriebssystems — `secret-tool`, macOS Keychain, Windows DPAPI. Ein Container hat keinen davon.

Deshalb gibt es jetzt einen vierten Speicher: `schluessel.icarus` im Datenverzeichnis, verschlüsselt mit einer Passphrase aus `ICARUS_SECRETS_PASSPHRASE` (`secrets.py`, Verfahren in `crypto.py`). `compose.yaml` verlangt sie, sonst startet der Stapel nicht.

Was das bringt und was nicht, ehrlich: Das Datenverzeichnis ist das, was gesichert, kopiert und in Snapshots gezogen wird — nach dieser Änderung enthält keine dieser Kopien lesbare Schlüssel. Die Passphrase lebt in der Orchestrierungsschicht, einem anderen Artefakt mit anderem Lebenszyklus. Wer **beides** hat, hat die Schlüssel. Das ist ein echter Schutz gegen ausgelaufene Sicherungen und keiner gegen jemanden, der ohnehin auf dem Rechner sitzt. Der Schlüsselbund des Betriebssystems bleibt die bessere Lösung, wo es einen gibt.

**Dateizugriff nur über Bind-Mounts.** `ICARUS_FILE_ROOTS` verweist im Container auf Pfade *innerhalb* des Containers, nicht auf die Pfade des Host-Systems — der Ordner muss erst per Bind-Mount hineingereicht werden (Beispiel in `compose.yaml`), und der intern gültige Pfad unterscheidet sich vom Pfad auf dem Host. Das ist eine zusätzliche Stufe Indirektion gegenüber der nativen App, wo `ICARUS_FILE_ROOTS` einfach auf echte Host-Pfade zeigt.

**Computer-Use ist im Container prinzipiell ausgeschlossen.** Ein Container hat keine Oberfläche, die er bedienen könnte — computer-use setzt voraus, dass ein Prozess auf demselben Rechner sitzt wie der Bildschirm, die Maus und die Tastatur, die er steuern soll. Das ist keine fehlende Funktion, die noch nachgerüstet wird, sondern eine Grenze der Betriebsform selbst. Für diesen Anwendungsfall bleibt die native App der einzige Weg.

**Ohne Token startet nichts.** `compose.yaml` verlangt `ICARUS_SIDECAR_TOKEN` mit `:?`, nicht mit einem leeren Vorgabewert. Ein Vorgabewert wäre bequemer und falsch: Der Sidecar liefe dann ohne jeden Zugriffsschutz, und zwar still. Ein Fehlschlag beim Start ist die einzige Meldung, die niemand überliest.

**Zwei Wege zu pflegen.** Ein zusätzliches Dockerfile, ein zusätzlicher Workflow, eine zusätzliche Stelle, an der `ICARUS_SIDECAR_PORT`, `ICARUS_DATA_DIR` und `ICARUS_FILE_ROOTS` korrekt zusammenspielen müssen. Der Sidecar-Code selbst bleibt aber derselbe — es kommt keine Verzweigung in `icarus_memory` dazu, nur eine andere Startumgebung drumherum.
