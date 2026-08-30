"""Versionierte SQLite-Migrationen und ihre Ausfallgrenzen."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from typing import Callable

import pytest

from icarus_memory import (
    EpisodeKind,
    EpisodeStore,
    Kind,
    ProposalKind,
    ProposalStore,
    Provenance,
    SelfModelStore,
    SourceType,
    SqliteBackend,
    Task,
    TaskStore,
    WorkspaceStore,
)
from icarus_memory.audit import AuditLog
from icarus_memory.migrations import (
    IncompatibleLegacySchema,
    InvalidDatabaseVersion,
    Migration,
    MigrationError,
    UnsupportedDatabaseVersion,
    current_version,
    run_migrations,
)
from icarus_memory.proposals import Evidence
from icarus_memory.regeln import RegelStore

T0 = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)


def _version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return current_version(connection)
    finally:
        connection.close()


def _tables(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {str(row[0]) for row in rows}
    finally:
        connection.close()


def _snapshot(path: Path) -> dict[str, list[tuple]]:
    connection = sqlite3.connect(path)
    try:
        result: dict[str, list[tuple]] = {}
        for table in sorted(_tables(path)):
            escaped = table.replace('"', '""')
            result[table] = connection.execute(
                f'SELECT * FROM "{escaped}" ORDER BY rowid'
            ).fetchall()
        return result
    finally:
        connection.close()


StoreFactory = Callable[[Path], object]


STORE_SPECS: tuple[tuple[str, StoreFactory, set[str]], ...] = (
    ("self_model", SqliteBackend, {"assertions"}),
    ("episodes", EpisodeStore, {"episodes"}),
    ("tasks", TaskStore, {"tasks"}),
    ("workspace", WorkspaceStore, {"projects", "notes"}),
    ("proposals", ProposalStore, {"proposals"}),
    ("audit", AuditLog, {"audit"}),
    ("rules", RegelStore, {"regeln"}),
)


@pytest.mark.parametrize(("name", "factory", "expected"), STORE_SPECS)
@pytest.mark.parametrize("preexisting_empty", [False, True])
def test_neue_datenbank_laeuft_ueber_migration_null_auf_eins(
    tmp_path: Path,
    name: str,
    factory: StoreFactory,
    expected: set[str],
    preexisting_empty: bool,
) -> None:
    path = tmp_path / f"{name}.sqlite3"
    if preexisting_empty:
        sqlite3.connect(path).close()

    store = factory(path)
    store.close()  # type: ignore[attr-defined]

    assert _version(path) == 1
    assert _tables(path) == expected


@pytest.mark.parametrize(("name", "factory", "_expected"), STORE_SPECS)
def test_future_version_wird_vor_jeder_aenderung_abgewiesen(
    tmp_path: Path,
    name: str,
    factory: StoreFactory,
    _expected: set[str],
) -> None:
    path = tmp_path / f"{name}.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
    connection.execute("INSERT INTO sentinel VALUES ('bleibt')")
    connection.execute("PRAGMA user_version = 999")
    connection.commit()
    connection.close()
    before = _snapshot(path)

    with pytest.raises(UnsupportedDatabaseVersion) as raised:
        factory(path)

    assert raised.value.code == "unsupported_database_version"
    assert raised.value.found == 999
    assert _version(path) == 999
    assert _snapshot(path) == before
    assert not path.with_name(path.name + "-wal").exists()


@pytest.mark.parametrize(("name", "factory", "_expected"), STORE_SPECS)
@pytest.mark.parametrize("invalid_version", [-1, -999])
def test_negative_version_wird_ohne_python_negativindex_abgewiesen(
    tmp_path: Path,
    name: str,
    factory: StoreFactory,
    _expected: set[str],
    invalid_version: int,
) -> None:
    path = tmp_path / f"{name}.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
    connection.execute("INSERT INTO sentinel VALUES ('bleibt')")
    connection.execute(f"PRAGMA user_version = {invalid_version}")
    connection.commit()
    connection.close()
    before = _snapshot(path)

    with pytest.raises(InvalidDatabaseVersion) as raised:
        factory(path)

    assert raised.value.code == "invalid_database_version"
    assert raised.value.found == invalid_version
    assert _version(path) == invalid_version
    assert _snapshot(path) == before
    assert not path.with_name(path.name + "-wal").exists()


@pytest.mark.parametrize(("name", "factory", "_expected"), STORE_SPECS)
def test_unbekanntes_legacy_schema_wird_nicht_markiert(
    tmp_path: Path,
    name: str,
    factory: StoreFactory,
    _expected: set[str],
) -> None:
    path = tmp_path / f"{name}.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE fremde_daten (id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO fremde_daten VALUES ('bleibt')")
    connection.commit()
    connection.close()

    with pytest.raises(IncompatibleLegacySchema):
        factory(path)

    assert _version(path) == 0
    assert _snapshot(path) == {"fremde_daten": [("bleibt",)]}


def test_runner_setzt_version_erst_nach_apply_und_verify(tmp_path: Path) -> None:
    path = tmp_path / "ordered.sqlite3"
    connection = sqlite3.connect(path)
    seen: list[tuple[str, int]] = []

    def apply_v1(conn: sqlite3.Connection) -> None:
        seen.append(("apply", current_version(conn)))
        conn.execute("CREATE TABLE items (id TEXT PRIMARY KEY)")

    def verify_v1(conn: sqlite3.Connection) -> None:
        seen.append(("verify", current_version(conn)))
        assert "items" in {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }

    migrations = (Migration(1, "items", apply_v1, verify_v1),)
    assert run_migrations(
        connection, store="test", path=path, migrations=migrations
    ) == 1
    assert seen == [("apply", 0), ("verify", 0), ("verify", 1)]

    # Reopen/idempotentes Starten prüft nur noch den aktuellen Vertrag.
    connection.close()
    connection = sqlite3.connect(path)
    seen.clear()
    assert run_migrations(
        connection, store="test", path=path, migrations=migrations
    ) == 1
    assert seen == [("verify", 1)]
    connection.close()


def test_spaetere_version_verwendet_nur_ihren_eigenen_schema_vertrag(
    tmp_path: Path,
) -> None:
    path = tmp_path / "versions.sqlite3"
    connection = sqlite3.connect(path)

    def tables(conn: sqlite3.Connection) -> set[str]:
        return {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    def verify_v1(conn: sqlite3.Connection) -> None:
        assert tables(conn) == {"eins"}

    def verify_v2(conn: sqlite3.Connection) -> None:
        assert tables(conn) == {"eins", "zwei"}

    migrations = (
        Migration(
            1,
            "eins",
            lambda conn: conn.execute("CREATE TABLE eins (id TEXT PRIMARY KEY)"),
            verify_v1,
        ),
        Migration(
            2,
            "zwei",
            lambda conn: conn.execute("CREATE TABLE zwei (id TEXT PRIMARY KEY)"),
            verify_v2,
        ),
    )

    assert run_migrations(
        connection, store="test", path=path, migrations=migrations
    ) == 2
    connection.close()

    reopened = sqlite3.connect(path)
    try:
        assert run_migrations(
            reopened, store="test", path=path, migrations=migrations
        ) == 2
    finally:
        reopened.close()


def test_fehlgeschlagene_migration_ist_atomar_und_wiederholbar(
    tmp_path: Path,
) -> None:
    path = tmp_path / "atomic.sqlite3"
    connection = sqlite3.connect(path)

    def apply_v1(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE bestand (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO bestand VALUES ('alt')")

    def verify_v1(conn: sqlite3.Connection) -> None:
        assert conn.execute("SELECT count(*) FROM bestand").fetchone()[0] == 1

    def apply_broken_v2(conn: sqlite3.Connection) -> None:
        assert current_version(conn) == 1
        conn.execute("CREATE TABLE halbfertig (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO halbfertig VALUES ('darf-nicht-bleiben')")
        raise RuntimeError("absichtlicher Abbruch")

    def verify_v2(conn: sqlite3.Connection) -> None:
        assert conn.execute("SELECT count(*) FROM halbfertig").fetchone()[0] == 1

    broken = (
        Migration(1, "bestand", apply_v1, verify_v1),
        Migration(2, "kaputt", apply_broken_v2, verify_v2),
    )
    with pytest.raises(MigrationError) as raised:
        run_migrations(connection, store="test", path=path, migrations=broken)

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert current_version(connection) == 1
    assert connection.execute("SELECT id FROM bestand").fetchall() == [("alt",)]
    assert (
        connection.execute(
            "SELECT count(*) FROM sqlite_schema "
            "WHERE type = 'table' AND name = 'halbfertig'"
        ).fetchone()[0]
        == 0
    )

    def apply_fixed_v2(conn: sqlite3.Connection) -> None:
        assert current_version(conn) == 1
        conn.execute("CREATE TABLE halbfertig (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO halbfertig VALUES ('jetzt-ganz')")

    fixed = (
        broken[0],
        Migration(2, "repariert", apply_fixed_v2, verify_v2),
    )
    assert run_migrations(
        connection, store="test", path=path, migrations=fixed
    ) == 2
    assert connection.execute("SELECT id FROM halbfertig").fetchall() == [
        ("jetzt-ganz",)
    ]
    connection.close()


def test_zwei_verbindungen_initialisieren_dieselbe_datei_sicher(
    tmp_path: Path,
) -> None:
    path = tmp_path / "parallel.sqlite3"
    barrier = Barrier(2)

    def open_store() -> int:
        barrier.wait()
        backend = SqliteBackend(path)
        backend.close()
        return _version(path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        versions = list(pool.map(lambda _: open_store(), range(2)))

    assert versions == [1, 1]
    assert _tables(path) == {"assertions"}


def test_legacy_bestand_aller_stores_bleibt_unveraendert(tmp_path: Path) -> None:
    provenance = Provenance(source_type=SourceType.CHAT, source_ref="chat:legacy")
    paths = {name: tmp_path / f"{name}.sqlite3" for name, _, _ in STORE_SPECS}

    backend = SqliteBackend(paths["self_model"])
    model = SelfModelStore(backend, subject_id="test")
    model.record("Bleibt erhalten.", Kind.STATE, provenance, at=T0)
    backend.close()

    episodes = EpisodeStore(paths["episodes"])
    episode, _ = episodes.record(
        EpisodeKind.DOCUMENT,
        "Legacy Episode",
        "Unveränderter Rohtext",
        provenance,
        at=T0,
    )
    episodes.close()

    tasks = TaskStore(paths["tasks"])
    tasks.add("Legacy Aufgabe", provenance, at=T0)
    tasks.close()

    workspace = WorkspaceStore(paths["workspace"])
    project = workspace.add_project("Legacy Projekt", provenance, at=T0)
    workspace.add_note(
        "Legacy Notiz", "Unveränderter Inhalt", provenance, project_id=project.id, at=T0
    )
    workspace.close()

    proposals = ProposalStore(paths["proposals"])
    proposals.propose(
        ProposalKind.ASSERTION,
        "Legacy Vorschlag",
        "Belegter Vorschlag",
        assertion_kind=Kind.STATE,
        evidence=[
            Evidence(episode_id=episode.id, quote="Rohtext", digest=episode.digest)
        ],
        at=T0,
    )
    proposals.close()

    audit = AuditLog(paths["audit"])
    audit.record("test", "read", "auto", "executed", {"id": "legacy"}, at=T0)
    audit.close()

    rules = RegelStore(paths["rules"])
    rules.anlegen("Legacy Regel", "zeit", "notify", {"zone": "UTC"})
    rules.close()

    before: dict[str, dict[str, list[tuple]]] = {}
    for name, path in paths.items():
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA user_version = 0")
        connection.commit()
        connection.close()
        before[name] = _snapshot(path)

    for name, factory, _ in STORE_SPECS:
        store = factory(paths[name])
        store.close()  # type: ignore[attr-defined]
        assert _version(paths[name]) == 1
        assert _snapshot(paths[name]) == before[name]


def test_bekannte_alte_task_tabelle_wird_gezielt_migriert(tmp_path: Path) -> None:
    path = tmp_path / "old-tasks.sqlite3"
    provenance = Provenance(source_type=SourceType.CHAT, source_ref="chat:alt")
    task = Task(id="t-alt", title="Alte Aufgabe", provenance=provenance, created_at=T0)
    document = json.dumps(task.to_dict(), ensure_ascii=False)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
        "status TEXT NOT NULL, due TEXT, title TEXT NOT NULL, document TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO tasks (id, created_at, status, due, title, document) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("t-alt", T0.isoformat(), "open", None, "Alte Aufgabe", document),
    )
    connection.commit()
    connection.close()

    store = TaskStore(path)
    migrated = store.all_tasks()
    store.close()

    assert _version(path) == 1
    assert len(migrated) == 1
    assert migrated[0].id == "t-alt"
    assert migrated[0].title == "Alte Aufgabe"
    connection = sqlite3.connect(path)
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        assert "project_id" in columns
        assert connection.execute(
            "SELECT document, project_id FROM tasks WHERE id = 't-alt'"
        ).fetchone() == (document, None)
    finally:
        connection.close()


def test_legacy_index_mit_richtigem_namen_aber_falschem_vertrag_wird_abgewiesen(
    tmp_path: Path,
) -> None:
    path = tmp_path / "episodes-wrong-index.sqlite3"
    store = EpisodeStore(path)
    store.close()
    connection = sqlite3.connect(path)
    connection.execute("DROP INDEX idx_episodes_digest")
    connection.execute("CREATE INDEX idx_episodes_digest ON episodes(state)")
    connection.execute("PRAGMA user_version = 0")
    connection.commit()
    connection.close()

    with pytest.raises(IncompatibleLegacySchema):
        EpisodeStore(path)

    assert _version(path) == 0
    connection = sqlite3.connect(path)
    try:
        index = connection.execute(
            "PRAGMA index_list(episodes)"
        ).fetchall()
        wrong = next(row for row in index if row[1] == "idx_episodes_digest")
        assert not bool(wrong[2])
        assert connection.execute(
            "PRAGMA index_info(idx_episodes_digest)"
        ).fetchall()[0][2] == "state"
    finally:
        connection.close()


def test_partieller_unique_index_wird_nicht_als_voller_vertrag_akzeptiert(
    tmp_path: Path,
) -> None:
    path = tmp_path / "episodes-partial-index.sqlite3"
    store = EpisodeStore(path)
    store.close()
    connection = sqlite3.connect(path)
    connection.execute("DROP INDEX idx_episodes_digest")
    connection.execute(
        "CREATE UNIQUE INDEX idx_episodes_digest ON episodes(digest) "
        "WHERE state = 'pending'"
    )
    connection.execute("PRAGMA user_version = 0")
    connection.commit()
    connection.close()

    with pytest.raises(IncompatibleLegacySchema):
        EpisodeStore(path)

    assert _version(path) == 0


def test_unbekannte_pflichtspalte_im_legacy_schema_wird_abgewiesen(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tasks-alien-column.sqlite3"
    store = TaskStore(path)
    store.close()
    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE tasks ADD COLUMN alien TEXT NOT NULL DEFAULT 'x'")
    connection.execute("PRAGMA user_version = 0")
    connection.commit()
    connection.close()

    with pytest.raises(IncompatibleLegacySchema):
        TaskStore(path)

    assert _version(path) == 0


def test_unbekannter_unique_index_im_legacy_schema_wird_abgewiesen(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tasks-alien-index.sqlite3"
    store = TaskStore(path)
    store.close()
    connection = sqlite3.connect(path)
    connection.execute("CREATE UNIQUE INDEX alien_unique ON tasks(title)")
    connection.execute("PRAGMA user_version = 0")
    connection.commit()
    connection.close()

    with pytest.raises(IncompatibleLegacySchema):
        TaskStore(path)

    assert _version(path) == 0
