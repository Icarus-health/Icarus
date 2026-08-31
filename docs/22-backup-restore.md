# Vollständige lokale Backup- und Restore-Sätze

Stand: 2026-08-30. Verbindlich für Backup-Format 1.

## Umfang

Ein operativer Backup-Satz enthält exakt die sieben autoritativen SQLite-Stores:

- `self-model.sqlite3`
- `episodes.sqlite3`
- `tasks.sqlite3`
- `workspace.sqlite3`
- `proposals.sqlite3`
- `audit.sqlite3`
- `regeln.sqlite3`

Nicht enthalten sind Credentials und flüchtige oder ableitbare Zustände:

- OS-Keychain, `schluessel.icarus`, `.env` und Prozessumgebung;
- `verbindung.json` mit lokalem Session-Token;
- Cognee als optionaler, aus SQLite ableitbarer semantischer Index;
- manuelle Exporte, ältere Backups und bisherige Restore-Rückfallkopien;
- externe freigegebene Quellordner;
- Pending Approvals und Gesprächsverlauf, die derzeit nur im RAM existieren.

Auch `einstellungen.json` ist in Format 1 bewusst ausgeschlossen. Obwohl die
Datei als nicht geheim konzipiert wurde, kann `mcp_server[].umgebung` heute
beliebige Zugangsdaten enthalten. Ein roher Export würde den Secret-Vertrag
brechen. Dauerfreigaben gehen nicht verloren: Sie liegen im enthaltenen
`regeln.sqlite3`. Ein künftiges Konfigurationsbackup benötigt zuerst eine
verlässlich secret-freie Projektion.

## Format und Manifest

Ein Satz ist ein Verzeichnis. Das hält den Vertrag prüfbar und vermeidet eine
zusätzliche Archivbibliothek:

```text
icarus-backup-20260831T081530Z-a1b2c3d4/
  manifest.json
  stores/
    self-model.sqlite3
    episodes.sqlite3
    tasks.sqlite3
    workspace.sqlite3
    proposals.sqlite3
    audit.sqlite3
    regeln.sqlite3
```

Dateinamen sind UTC-basiert, sortierbar, plattformkompatibel und durch acht
zufällige Hex-Zeichen auch bei zwei Sicherungen in derselben Sekunde eindeutig.

`manifest.json` enthält:

- `format_version`: Version des Backup-Vertrags, aktuell `1`;
- `backup_id`, `created_at` in UTC und `icarus_version`;
- pro Store den stabilen Namen, den fest registrierten relativen Pfad,
  `schema_version`, Byte-Größe, SHA-256 und das Ergebnis `integrity_check=ok`.

Backup-Format-Version und SQLite-Schema-Version sind unabhängig. Eine Änderung
an einer Store-Tabelle erhöht nicht automatisch das Backup-Format.

## Erzeugung und Konsistenz

Jeder Store wird über `sqlite3.Connection.backup()` in fester Registry-Reihenfolge
gesichert. Damit enthält auch eine Datenbank im WAL-Modus ihren vollständigen
committeten Zustand; rohe Dateikopien während laufender Writes finden nicht
statt. Globale Journal- oder WAL-Einstellungen werden nicht verändert.

SQLite bietet keine gemeinsame Transaktion über sieben getrennte Dateien.
Format 1 verspricht daher ehrlich keinen theoretisch global atomaren Zeitpunkt.
Es verspricht einen kontrollierten Satz mit gemeinsamer Backup-ID und Zeit:

1. Alle sieben Quellen müssen vorhanden und reguläre Dateien sein.
2. Jede Snapshot-Datei läuft durch den echten P2a-Store-Verifier.
3. `PRAGMA integrity_check`, Schema-Version, SHA-256 und Größe werden geprüft.
4. Erst dann wird das Manifest geschrieben und erneut gegen den Satz geprüft.
5. Store-Dateien, Manifest und Verzeichniseinträge werden vor der Publikation
   mit `fsync` persistiert, soweit die Plattform dies unterstützt.
6. Ein temporäres Geschwisterverzeichnis wird mit `os.replace` veröffentlicht
   und das Zielverzeichnis erneut synchronisiert.

Scheitert ein Store, wird das temporäre Verzeichnis entfernt. Ein unvollständiger
Satz erhält nie einen erfolgreichen Namen oder eine Erfolgsmeldung.

## Restore-Preflight

`inspect_backup()` und `restore_backup()` behandeln den Satz fail-closed:

- ausschließlich Format 1 und exakt bekannte Manifestfelder;
- exakt sieben eindeutige Store-Namen und feste Name/Pfad-Zuordnungen;
- keine absoluten Pfade, `..`, Symlinks, fehlenden oder zusätzlichen Dateien;
- Byte-Größe und SHA-256 vor jeder aktiven Änderung;
- SQLite-Integrität und Gleichheit von Manifest- und `PRAGMA user_version`;
- Ablehnung negativer und zukünftiger Store-Versionen;
- storespezifische Schema-Verifikation über den echten P2a-Öffnungspfad.

Für die Kompatibilitätsprüfung entsteht eine isolierte Kopie. Version 0 oder
eine andere unterstützte ältere Version wird nur dort bis zur aktuellen Version
migriert. Das Backup-Original wird weder geöffnet noch durch WAL-Sidecars oder
Migrationen verändert. Jede isolierte Kopie wird über einen einmal geöffneten,
nach Möglichkeit `O_NOFOLLOW`-geschützten Dateideskriptor gelesen und während
des Kopierens erneut gegen Manifest-Hash und -Größe geprüft. Damit können
zwischen Preflight und Kopie ausgetauschte Bytes nicht aktiviert werden. Erst
wenn alle sieben vorbereiteten Stores aktuell und integer sind, beginnt die
Aktivierung.

## Aktivierung und Rollback

Im laufenden Sidecar ist Restore ein exklusiver Maintenance-Vorgang. Das
Request-Gate lässt bestehende HTTP-Aufrufe enden und blockiert neue. Danach
werden Scheduler und MCP-Prozesse angehalten und alle sieben Store-Verbindungen
geschlossen.

Vor dieser Quiescence wird der tatsächliche Scheduler-Laufzustand festgehalten.
Der anschließende normale Agent-Rebuild verdrahtet Stores, Werkzeuge und MCP
erneut aus den weiterhin autoritativen Einstellungen; ob der Scheduler-Thread
danach startet, folgt jedoch dem festgehaltenen Vorzustand. So bleibt ein zuvor
laufender Scheduler nach Restore oder Rollback aktiv, während ein bewusst
gestoppter Thread auch bei aktiviertem Zeitplan nicht unerwartet anspringt.
`Scheduler.start()` bleibt idempotent, sodass kein zweiter Thread entsteht.

Vor dem Austausch entsteht zusätzlich ein normaler vollständiger Recovery-Satz
des aktiven Zustands. Während der Aktivierung werden die bekannten aktiven
Dateien in ein privates Rollback-Verzeichnis verschoben und die sieben
vorbereiteten Dateien eingesetzt. Anschließend werden sämtliche Store-Verträge
und Integritätsprüfungen erneut ausgeführt und alle Live-Stores, Agentenwerkzeuge
und Scheduler-Referenzen neu aufgebaut.

Scheitern Austausch, Prüfung oder Reopen, werden die neuen Dateien entfernt,
die vorherigen Dateien vollständig zurückverschoben und die alten Stores neu
geöffnet. Unbekannte Dateien im Datenverzeichnis werden nie bereinigt oder
gelöscht. Falls selbst der Rollback fehlschlägt, nennt der interne Fehler den
erhaltenen Recovery-Pfad. Der HTTP-Server bleibt dann fail-closed im
Wartungszustand und nimmt bis zu einem kontrollierten Neustart keine normalen
Anfragen mehr an. Ein Scheduler, der nicht innerhalb der Frist anhält, bricht
den Restore vor dem Schließen irgendeines Stores ab und läuft weiter.

Der historische `audit.sqlite3`-Bestand wird exakt restauriert. P2b schreibt
kein Restore-Ereignis nachträglich in diesen Bestand; Metadaten des Vorgangs
gehen nur in inhaltsfreie Diagnoselogs. So bleibt „Zustand A“ tatsächlich
Zustand A und der Append-only-Vertrag wird nicht umgangen.

## Interne API und Fehlervertrag

- `create_backup(data_dir, target_dir)` erzeugt einen `BackupResult`.
- `inspect_backup(path)` prüft einen Satz ohne aktive Daten zu verändern.
- `restore_backup(path, data_dir, ...)` liefert `RestoreResult` inklusive
  durchgeführter Versionsschritte und optionalem Recovery-Pfad.
- `list_backups()` listet nur veröffentlichte Verzeichnissätze. Alte einzelne
  `self-model-*.sqlite3`-Snapshots sind kein konkurrierender Restore-Vertrag.

Stabile Fehlerklassen sind `BackupError`, `BackupIntegrityError`,
`UnsupportedBackupFormat`, `IncompleteBackup`, `RestoreError` und
`RestoreCompatibilityError`; ein gescheiterter Rollback wird gesondert als
`RestoreRollbackError` signalisiert. SQLite-Ausnahmen bleiben intern als Cause
erhalten; Logs nennen Backup-ID, Pfad, Store und Version, aber keine
Nutzerinhalte.

## Tests und Sabotageproben

Die Regressionstests erzeugen Daten in allen sieben echten Stores und prüfen
Backup, Restore in leere und veränderte Umgebungen, WAL, ältere Migrationen,
Future-/Negativversionen, Hash- und Manifestmanipulation, fehlende/zusätzliche
Dateien, Pfadtraversal, Symlinks, eine Änderung zwischen Preflight und Kopie,
Scheduler-Timeout, Rotation bei identischem Zeitstempel sowie erfolgreichen
und gescheiterten Rollback nach finalem Fehler.

Drei temporäre Mutationen wurden gezielt gegengeprüft und anschließend
zurückgenommen:

1. Ohne SHA-256-Vergleich wird der Hash-Manipulationstest rot.
2. Ohne Future-Version-Preflight und temporären P2a-Verifier wird der Test rot,
   der jede Aktivierung einer Version 999 verbietet.
3. Wird ein abgebrochener Teilsatz trotzdem veröffentlicht, wird der
   All-or-none-Test rot.

Keine dieser Mutationen ist Teil des eingecheckten Codes.

## Grenzen und nächste Erweiterungen

- Der Satz ist kontrolliert und vollständig, aber nicht über alle Dateien
  gleichzeitig transaktional.
- Credentials müssen nach einem Rechnerwechsel separat neu eingerichtet werden.
- Cloud- und Multi-Device-Backup sind nicht Teil von P2b.
- Große künftige Canonical-Event-Stores können später inkrementelle Verfahren
  erfordern; Format 1 optimiert bewusst zuerst auf korrekte Semantik.

Neue operative SQLite-Domains müssen in derselben `STORE_SPECS`-Registry, im
Manifestvertrag sowie in Backup-, Restore-, Manipulations- und Rollbacktests
ergänzt werden. P2c hat den bestehenden Episode-Store auf Schema v2 erweitert;
Format 1 bleibt unverändert, weil Backup-Format und Store-Schema-Version
getrennte Verträge sind. Ein Episode-v1-Backup wird weiterhin nur auf einer
temporären Restore-Kopie nach v2 migriert.
