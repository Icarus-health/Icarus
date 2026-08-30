"""Kleine, gemeinsame SQLite-Migrationsschicht.

Version 0 bedeutet ausschließlich: Die Datei trägt noch keinen expliziten
Versionsvertrag. Sie kann leer sein oder einem bekannten Legacy-Schema
entsprechen. Ein beliebiges Schema wird niemals allein wegen ``user_version=0``
als ICARUS-Datenbank akzeptiert.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

MigrationStep = Callable[[sqlite3.Connection], None]


class MigrationError(RuntimeError):
    """Eine Datenbank konnte nicht sicher migriert oder geprüft werden."""

    code = "database_migration_failed"

    def __init__(self, message: str, *, store: str, path: str | Path) -> None:
        super().__init__(message)
        self.store = store
        self.path = Path(path)


class UnsupportedDatabaseVersion(MigrationError):
    """Die Datei stammt aus einer neueren, nicht unterstützten Version."""

    code = "unsupported_database_version"

    def __init__(
        self,
        *,
        store: str,
        path: str | Path,
        found: int,
        supported: int,
    ) -> None:
        super().__init__(
            "Diese ICARUS-Datenbank wurde mit einer neueren Version erstellt "
            "und kann von dieser Version nicht sicher geöffnet werden "
            f"(gefunden: {found}, unterstützt: {supported}).",
            store=store,
            path=path,
        )
        self.found = found
        self.supported = supported


class IncompatibleLegacySchema(MigrationError):
    """Eine unversionierte Datei entspricht keinem bekannten Legacy-Schema."""

    code = "incompatible_legacy_schema"


@dataclass(frozen=True)
class Migration:
    """Ein deterministischer, aufsteigend nummerierter Migrationsschritt."""

    version: int
    name: str
    apply: MigrationStep
    verify: MigrationStep


@dataclass(frozen=True)
class IndexContract:
    """Der fachlich relevante Vertrag eines benannten SQLite-Indexes."""

    table: str
    columns: tuple[str, ...]
    unique: bool = False


def current_version(connection: sqlite3.Connection) -> int:
    """Liest den SQLite-Versionsvertrag der geöffneten Datei."""

    row = connection.execute("PRAGMA user_version").fetchone()
    if row is None:
        raise sqlite3.DatabaseError("PRAGMA user_version lieferte kein Ergebnis")
    return int(row[0])


def pending_migrations(
    migrations: Iterable[Migration], version: int
) -> tuple[Migration, ...]:
    """Gibt die nach ``version`` noch ausstehenden Schritte zurück."""

    ordered = _ordered(migrations)
    return tuple(migration for migration in ordered if migration.version > version)


def run_migrations(
    connection: sqlite3.Connection,
    *,
    store: str,
    path: str | Path,
    migrations: Iterable[Migration],
) -> int:
    """Prüft und aktualisiert eine SQLite-Datei bis zur Zielversion.

    Jeder Schritt läuft in einer eigenen expliziten Transaktion. Die Version
    wird erst nach ``apply`` und ``verify`` geschrieben. ``BEGIN IMMEDIATE``
    serialisiert zwei gleichzeitig startende ICARUS-Prozesse; nach dem Lock
    wird die Version erneut gelesen, damit kein Schritt doppelt läuft.
    """

    ordered = _ordered(migrations)
    target = ordered[-1].version
    db_path = Path(path)

    try:
        version = current_version(connection)
    except sqlite3.DatabaseError as exc:
        raise MigrationError(
            "Die SQLite-Schemaversion konnte nicht gelesen werden.",
            store=store,
            path=db_path,
        ) from exc
    _guard_future(store, db_path, version, target)

    while version < target:
        migration = ordered[version]
        before = version
        try:
            connection.execute("BEGIN IMMEDIATE")
            locked_version = current_version(connection)
            _guard_future(store, db_path, locked_version, target)

            # Eine andere Verbindung kann den Schritt während unserer
            # Wartezeit bereits abgeschlossen haben.
            if locked_version != before:
                connection.rollback()
                version = locked_version
                continue

            # Der Verifier der aktuell geöffneten Version ist die Vorbedingung
            # für den nächsten Schritt. Frühere Verifier sind keine Invarianten
            # aller späteren Versionen: Eine v2 darf den exakten v1-Vertrag
            # beispielsweise durch eine neue Tabelle bewusst erweitern.
            if locked_version > 0:
                ordered[locked_version - 1].verify(connection)

            logger.info(
                "SQLite-Migration startet: store=%s path=%s version_before=%d "
                "migration=%s",
                store,
                db_path,
                before,
                migration.name,
            )
            migration.apply(connection)
            migration.verify(connection)
            connection.execute(f"PRAGMA user_version = {migration.version}")
            if current_version(connection) != migration.version:
                raise sqlite3.DatabaseError("user_version wurde nicht übernommen")
            connection.commit()
        except MigrationError:
            connection.rollback()
            logger.exception(
                "SQLite-Migration fehlgeschlagen: store=%s path=%s "
                "version_before=%d migration=%s",
                store,
                db_path,
                before,
                migration.name,
            )
            raise
        except Exception as exc:
            connection.rollback()
            logger.exception(
                "SQLite-Migration fehlgeschlagen: store=%s path=%s "
                "version_before=%d migration=%s",
                store,
                db_path,
                before,
                migration.name,
            )
            raise MigrationError(
                f"Migration {migration.version} ({migration.name}) ist fehlgeschlagen.",
                store=store,
                path=db_path,
            ) from exc

        version = migration.version
        logger.info(
            "SQLite-Migration abgeschlossen: store=%s path=%s "
            "version_before=%d migration=%s version_after=%d",
            store,
            db_path,
            before,
            migration.name,
            version,
        )

    # Auch eine bereits aktuelle Datei wird gegen ihren Vertrag geprüft. Eine
    # gesetzte Versionsnummer allein darf kein beschädigtes Schema legitimieren.
    try:
        ordered[version - 1].verify(connection)
    except MigrationError:
        raise
    except Exception as exc:
        logger.exception(
            "SQLite-Schemaverifikation fehlgeschlagen: store=%s path=%s "
            "version=%d",
            store,
            db_path,
            version,
        )
        raise MigrationError(
            f"Das Schema von {store} entspricht nicht Version {version}.",
            store=store,
            path=db_path,
        ) from exc
    return version


def validate_legacy_or_empty(
    connection: sqlite3.Connection,
    *,
    store: str,
    path: str | Path,
    expected_tables: Mapping[str, set[str]],
    allowed_column_sets: Mapping[str, Iterable[set[str]]] | None = None,
    expected_indexes: Mapping[str, IndexContract] | None = None,
    expected_triggers: Mapping[str, str] | None = None,
    expected_primary_keys: Mapping[str, set[str]] | None = None,
) -> None:
    """Akzeptiert bei Version 0 nur leer oder einen bekannten Bestand."""

    actual_tables = _user_tables(connection)
    if not actual_tables:
        if _user_schema_objects(connection):
            raise IncompatibleLegacySchema(
                f"Die unversionierte Datei für {store} ist nicht leer und "
                "entspricht keinem bekannten Legacy-Schema.",
                store=store,
                path=path,
            )
        return
    expected_names = set(expected_tables)
    if actual_tables != expected_names:
        raise IncompatibleLegacySchema(
            f"Die unversionierte Datei für {store} enthält nicht das erwartete "
            "Legacy-Schema.",
            store=store,
            path=path,
        )
    for table, columns in expected_tables.items():
        actual_columns = table_columns(connection, table)
        allowed = tuple((allowed_column_sets or {}).get(table, (columns,)))
        if actual_columns not in allowed:
            raise IncompatibleLegacySchema(
                f"Die unversionierte Tabelle {table} besitzt nicht den "
                "erwarteten Spaltenvertrag.",
                store=store,
                path=path,
            )
    try:
        _verify_primary_keys(connection, expected_primary_keys or {})
        _verify_legacy_objects(
            connection,
            expected_indexes or {},
            expected_triggers or {},
        )
    except sqlite3.DatabaseError as exc:
        raise IncompatibleLegacySchema(
            f"Die unversionierte Datei für {store} besitzt nicht den "
            "erwarteten Schema-Vertrag.",
            store=store,
            path=path,
        ) from exc


def verify_schema(
    connection: sqlite3.Connection,
    *,
    expected_tables: Mapping[str, set[str]],
    expected_indexes: Mapping[str, IndexContract] | None = None,
    expected_triggers: Mapping[str, str] | None = None,
    expected_primary_keys: Mapping[str, set[str]] | None = None,
) -> None:
    """Prüft den für einen Store zugesagten Mindestvertrag."""

    actual_tables = _user_tables(connection)
    if actual_tables != set(expected_tables):
        raise sqlite3.DatabaseError(
            f"Tabellen weichen ab: erwartet {sorted(expected_tables)}, "
            f"gefunden {sorted(actual_tables)}"
        )
    for table, columns in expected_tables.items():
        actual_columns = table_columns(connection, table)
        if actual_columns != columns:
            raise sqlite3.DatabaseError(
                f"Spalten von {table} weichen ab: erwartet {sorted(columns)}, "
                f"gefunden {sorted(actual_columns)}"
            )
    _verify_indexes(connection, expected_indexes or {}, require_all=True)
    _verify_triggers(connection, expected_triggers or {}, require_all=True)
    _verify_primary_keys(connection, expected_primary_keys or {})


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    """Liest Spaltennamen einer bereits validierten internen Tabelle."""

    escaped = table.replace('"', '""')
    rows = connection.execute(f'PRAGMA table_info("{escaped}")').fetchall()
    return {str(row[1]) for row in rows}


def primary_key_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    """Liest die Primärschlüsselspalten eines internen Schemas."""

    escaped = table.replace('"', '""')
    rows = connection.execute(f'PRAGMA table_info("{escaped}")').fetchall()
    return {str(row[1]) for row in rows if int(row[5]) > 0}


def _ordered(migrations: Iterable[Migration]) -> tuple[Migration, ...]:
    ordered = tuple(sorted(migrations, key=lambda item: item.version))
    if not ordered:
        raise ValueError("Mindestens eine Migration ist erforderlich.")
    versions = [migration.version for migration in ordered]
    expected = list(range(1, len(ordered) + 1))
    if versions != expected:
        raise ValueError(
            f"Migrationen müssen lückenlos bei 1 beginnen: {versions!r}."
        )
    if any(not migration.name.strip() for migration in ordered):
        raise ValueError("Jede Migration benötigt einen Namen.")
    return ordered


def _guard_future(store: str, path: Path, found: int, supported: int) -> None:
    if found > supported:
        logger.error(
            "SQLite-Future-Version abgewiesen: store=%s path=%s "
            "found=%d supported=%d",
            store,
            path,
            found,
            supported,
        )
        raise UnsupportedDatabaseVersion(
            store=store,
            path=path,
            found=found,
            supported=supported,
        )


def _user_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_schema "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _user_schema_objects(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    rows = connection.execute(
        "SELECT type, name FROM sqlite_schema "
        "WHERE name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {(str(row[0]), str(row[1])) for row in rows}


def _verify_legacy_objects(
    connection: sqlite3.Connection,
    expected_indexes: Mapping[str, IndexContract],
    expected_triggers: Mapping[str, str],
) -> None:
    """Prüft vorhandene Legacy-Objekte, bevor ``IF NOT EXISTS`` sie verdeckt."""

    views = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type = 'view'"
    ).fetchall()
    if views:
        raise sqlite3.DatabaseError(
            f"Unerwartete Views: {sorted(str(row[0]) for row in views)}"
        )
    _verify_indexes(connection, expected_indexes, require_all=False)
    _verify_triggers(connection, expected_triggers, require_all=False)


def _verify_indexes(
    connection: sqlite3.Connection,
    expected: Mapping[str, IndexContract],
    *,
    require_all: bool,
) -> None:
    rows = connection.execute(
        "SELECT name FROM sqlite_schema "
        "WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    actual_names = {str(row[0]) for row in rows}
    unexpected = actual_names - set(expected)
    if unexpected:
        raise sqlite3.DatabaseError(f"Unerwartete Indexe: {sorted(unexpected)}")
    if require_all:
        missing = set(expected) - actual_names
        if missing:
            raise sqlite3.DatabaseError(f"Fehlende Indexe: {sorted(missing)}")

    for name in set(expected) & actual_names:
        contract = expected[name]
        escaped = name.replace('"', '""')
        metadata = connection.execute(
            f'PRAGMA index_list("{contract.table}")'
        ).fetchall()
        matching = [row for row in metadata if str(row[1]) == name]
        if len(matching) != 1:
            raise sqlite3.DatabaseError(
                f"Index {name} gehört nicht zur erwarteten Tabelle {contract.table}."
            )
        unique = bool(matching[0][2])
        partial = bool(matching[0][4])
        columns = tuple(
            str(row[2])
            for row in connection.execute(f'PRAGMA index_info("{escaped}")').fetchall()
        )
        if partial or unique != contract.unique or columns != contract.columns:
            raise sqlite3.DatabaseError(
                f"Index {name} weicht vom erwarteten Vertrag ab."
            )


def _verify_triggers(
    connection: sqlite3.Connection,
    expected: Mapping[str, str],
    *,
    require_all: bool,
) -> None:
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_schema WHERE type = 'trigger'"
    ).fetchall()
    actual = {str(row[0]): str(row[1]) for row in rows}
    unexpected = set(actual) - set(expected)
    if unexpected:
        raise sqlite3.DatabaseError(
            f"Unerwartete Trigger: {sorted(unexpected)}"
        )
    if require_all:
        missing = set(expected) - set(actual)
        if missing:
            raise sqlite3.DatabaseError(f"Fehlende Trigger: {sorted(missing)}")
    for name in set(expected) & set(actual):
        if _normalized_sql(actual[name]) != _normalized_sql(expected[name]):
            raise sqlite3.DatabaseError(
                f"Trigger {name} weicht vom erwarteten Vertrag ab."
            )


def _normalized_sql(statement: str) -> tuple[str, ...]:
    # sqlite_schema entfernt das optionale ``IF NOT EXISTS``. Für den
    # semantischen Vergleich normalisieren wir ausschließlich diesen DDL-Zusatz
    # sowie Whitespace und Groß-/Kleinschreibung; der Trigger-Body bleibt exakt.
    tokens: Sequence[str] = statement.replace(";", " ; ").split()
    normalized = [token.casefold() for token in tokens]
    for start in range(max(0, len(normalized) - 2)):
        if normalized[start : start + 3] == ["if", "not", "exists"]:
            del normalized[start : start + 3]
            break
    if normalized and normalized[-1] == ";":
        normalized.pop()
    return tuple(normalized)


def _verify_primary_keys(
    connection: sqlite3.Connection, expected: Mapping[str, set[str]]
) -> None:
    for table, columns in expected.items():
        actual = primary_key_columns(connection, table)
        if actual != columns:
            raise sqlite3.DatabaseError(
                f"Primärschlüssel von {table} weicht ab: {sorted(actual)}"
            )
