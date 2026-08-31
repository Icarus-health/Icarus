# Versionierte SQLite-Migrationen

Stand: 2026-08-31. Verbindlich für die von `sidecar/icarus_memory` verwalteten
SQLite-Dateien.

## Vertrag

Jeder operative Store speichert seine Schemaversion in
`PRAGMA user_version`. Aktuell ist die Baseline Version 1.

- **Version 0** bedeutet nur „noch kein expliziter Versionsvertrag“.
- Eine leere Datei wird über Migration 1 initialisiert.
- Eine nicht leere Version-0-Datei muss dem bekannten Legacy-Schema ihres
  Stores entsprechen. Tabellen, bekannte Spaltenvarianten, Primärschlüssel,
  Indexverträge und Trigger werden vor jeder Änderung geprüft. Unerwartete
  Views, Trigger, Indexe oder Spalten werden nicht still übernommen.
- Eine Version über der von dieser ICARUS-Version unterstützten Zielversion
  wird mit `UnsupportedDatabaseVersion` abgewiesen, bevor Schema, Trigger oder
  Journal-Modus verändert werden.
- Ein negativer Versionswert wird mit `InvalidDatabaseVersion` als ungültiger
  Datenbankvertrag abgewiesen. Der gültige Bereich ist ausschließlich
  `0 <= user_version <= target_version`.

Eine fehlende Versionsnummer ist damit weder ein Grund, Nutzerdaten zu
überschreiben, noch ein Freibrief, eine beliebige SQLite-Datei als ICARUS-Datei
zu markieren.

## Runner und Transaktion

Der gemeinsame Runner liegt in `icarus_memory/migrations.py`. Jeder Store
registriert seine Schritte ausdrücklich als `Migration(version, name, apply,
verify)`.

Eine Migration läuft in dieser Reihenfolge:

1. `BEGIN IMMEDIATE` und erneutes Lesen der Version unter dem Schreib-Lock;
2. `apply` mit einzelnen, deterministischen SQLite-Statements;
3. `verify` des neuen Schemavertrags;
4. Setzen von `PRAGMA user_version`;
5. Commit.

Bei jedem Fehler erfolgt ein Rollback. `user_version` wird erst nach
erfolgreichem Apply und Verify gesetzt. Die Migrationen verwenden bewusst kein
`sqlite3.executescript()`: Diese Funktion kann eine bereits offene Transaktion
vor dem Script implizit committen und damit atomare DDL-Rollbacks aushebeln.

Zwei gleichzeitig startende Prozesse werden durch `BEGIN IMMEDIATE`
serialisiert. Die wartende Verbindung liest die Version danach erneut und
führt einen inzwischen abgeschlossenen Schritt nicht ein zweites Mal aus.

## Store-Inventar und Baseline

| Datei | Store | Tabellen | Persistenzbesonderheit |
| --- | --- | --- | --- |
| `self-model.sqlite3` | `SqliteBackend` | `assertions` | vollständige Assertion als JSON; zwei Append-only-/Redaction-Trigger |
| `episodes.sqlite3` | `EpisodeStore` | `episodes`, `episode_revisions` | vollständige Episode als JSON; Schema v2: Quellidentität ist eindeutig, Digest ist Inhaltsmerkmal und Revisionen erhalten Source-Updates |
| `tasks.sqlite3` | `TaskStore` | `tasks` | vollständige Task als JSON; bekanntes Legacy-Schema ohne `project_id` |
| `workspace.sqlite3` | `WorkspaceStore` | `projects`, `notes` | vollständige Project-/Note-Dokumente als JSON |
| `proposals.sqlite3` | `ProposalStore` | `proposals` | vollständiger Vorschlag samt Evidence als JSON |
| `audit.sqlite3` | `AuditLog` | `audit` | anhängend durch No-Update-/No-Delete-Trigger |
| `regeln.sqlite3` | `RegelStore` | `regeln` | `passt_auf` als JSON; bestehender WAL-Modus bleibt erhalten |

P2a führt diese Dateien nicht zusammen. Es gibt keine SQL-Foreign-Keys und
keine storeübergreifende Migrationstransaktion. IDs, JSON-Dokumente und
fachliche Beziehungen werden in der Baseline nicht umgeschrieben.

Der einzige bekannte strukturelle Legacy-Unterschied ist die ältere
`tasks`-Tabelle ohne `project_id`. Migration 1 ergänzt diese Spalte gezielt und
legt anschließend den Index an. Andere SQLite-Fehler werden nicht mehr als
vermeintlich bereits vorhandene Spalte verschluckt.

## Eine neue Migration registrieren

Eine Folgemigration erhält im betroffenen Store die nächste lückenlose Nummer:

```python
def _migrate_v2(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE beispiel ADD COLUMN scope TEXT")


def _verify_v2(connection: sqlite3.Connection) -> None:
    if "scope" not in table_columns(connection, "beispiel"):
        raise sqlite3.DatabaseError("scope fehlt")


_MIGRATIONS = (
    Migration(1, "initial_explicit_version", _migrate_v1, _verify_v1),
    Migration(2, "add_scope", _migrate_v2, _verify_v2),
)
```

Migrationen müssen ohne Datum, Netzwerk, Modell, Zufall oder externen Dienst
reproduzierbar sein. `apply` und `verify` dürfen nicht selbst committen oder
Transaktionen öffnen. Vor einem Folgeschritt und beim idempotenten Reopen prüft
der Runner den Vertrag der jeweils aktuellen Version. Frühere Verifier werden
nicht als ewige Invarianten ausgeführt, damit eine spätere Version ihr Schema
kontrolliert erweitern kann.

## Fehler, Diagnose und Recovery

- `MigrationError`: Migration oder Verifikation ist fehlgeschlagen.
- `InvalidDatabaseVersion`: Der gespeicherte Versionswert ist negativ.
- `IncompatibleLegacySchema`: Version 0 ist weder leer noch ein bekannter
  Legacy-Bestand.
- `UnsupportedDatabaseVersion`: Die Datei stammt aus einer neueren Version.

Die Fehler tragen stabile technische Codes sowie Store und Pfad. Logs nennen
nur Store, Pfad, Versionen und Migrationsnamen; Inhalte aus Assertions,
Episodes, Aufgaben oder Kommunikation werden nicht protokolliert.

Unterstützt werden ausschließlich Vorwärtsmigrationen. Es gibt keine
automatischen Downgrades. Recovery erfolgt mit einer kompatibleren neueren
ICARUS-Version oder über Backup/Restore. P2b verwendet denselben Vertrag vor
jeder Aktivierung: Future- und negative Versionen werden im Preflight
abgewiesen; unterstützte ältere Versionen werden ausschließlich auf temporären
Kopien migriert. Das unveränderte Backup-Original bleibt erhalten. Details
stehen in `docs/22-backup-restore.md`.

## Tests

`tests/test_migrations.py` prüft insbesondere:

- leere und neu angelegte Dateien von 0 auf aktuell;
- bekannte Legacy-Bestände aller sieben Stores ohne Datenänderung;
- das alte Task-Schema ohne `project_id`;
- geordnete, einmalige Ausführung und Reopen;
- Rollback von DDL, Rows und Version nach künstlichem Abbruch;
- erfolgreichen Retry nach einem Abbruch;
- Future-Versionen ohne Dateiveränderung;
- negative Versionswerte ohne Dateiveränderung oder Negativindizierung;
- Ablehnung unbekannter Legacy-Schemata, Spalten, Indexe und Triggerverträge;
- den Wechsel auf einen eigenständigen Schemavertrag einer späteren Version;
- gleichzeitige Initialisierung über zwei Verbindungen.

Neue Migrationen erweitern diese Tests um ein repräsentatives altes Schema und
mindestens einen fachlichen Datensatz, dessen ID und Inhalt erhalten bleiben.
