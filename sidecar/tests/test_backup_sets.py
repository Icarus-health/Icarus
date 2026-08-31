"""Vollstaendige, manipulationssichere Backup- und Restore-Saetze.

Die Tests pruefen bewusst den fachlichen Vertrag an echten Store-Dateien. Ein
Backup, das nur sieben Dateinamen enthaelt, aber IDs, JSON oder den letzten
committeten WAL-Stand verliert, waere fuer den Nutzer kein Backup.
"""

from __future__ import annotations

import hashlib
import errno
import json
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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
    TaskStore,
    WorkspaceStore,
)
from icarus_memory.audit import AuditLog
from icarus_memory.backup import (
    BACKUP_FORMAT_VERSION,
    STORE_SPECS,
    BackupError,
    BackupIntegrityError,
    IncompleteBackup,
    RestoreCompatibilityError,
    RestoreError,
    RestoreRollbackError,
    UnsupportedBackupFormat,
    create_backup,
    inspect_backup,
    list_backups,
    restore_backup,
)
from icarus_memory.proposals import Evidence
from icarus_memory.regeln import RegelStore

T0 = datetime(2026, 8, 31, 8, 15, 30, tzinfo=timezone.utc)

STORE_FILES = {
    "self_model": "self-model.sqlite3",
    "episodes": "episodes.sqlite3",
    "tasks": "tasks.sqlite3",
    "workspace": "workspace.sqlite3",
    "proposals": "proposals.sqlite3",
    "audit": "audit.sqlite3",
    "rules": "regeln.sqlite3",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


def _integrity(path: Path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    finally:
        connection.close()


def _raw_snapshot(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    connection = sqlite3.connect(path)
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        result: dict[str, list[tuple[Any, ...]]] = {}
        for table in tables:
            escaped = table.replace('"', '""')
            result[table] = connection.execute(
                f'SELECT * FROM "{escaped}" ORDER BY rowid'
            ).fetchall()
        return result
    finally:
        connection.close()


def _state(data_dir: Path) -> dict[str, dict[str, list[tuple[Any, ...]]]]:
    return {
        name: _raw_snapshot(data_dir / filename)
        for name, filename in STORE_FILES.items()
        if (data_dir / filename).is_file()
    }


def _file_hashes(data_dir: Path) -> dict[str, str]:
    return {
        name: _sha256(data_dir / filename)
        for name, filename in STORE_FILES.items()
        if (data_dir / filename).is_file()
    }


def _populate(data_dir: Path, marker: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    provenance = Provenance(
        source_type=SourceType.CHAT,
        source_ref=f"chat:{marker}",
    )

    backend = SqliteBackend(data_dir / STORE_FILES["self_model"])
    model = SelfModelStore(backend, subject_id="test")
    model.record(f"Aussage {marker}", Kind.STATE, provenance, at=T0)
    backend.close()

    episodes = EpisodeStore(data_dir / STORE_FILES["episodes"])
    episode, _ = episodes.record(
        EpisodeKind.DOCUMENT,
        f"Episode {marker}",
        f"Unveraenderter Rohtext {marker}",
        provenance,
        at=T0,
    )
    episodes.close()

    tasks = TaskStore(data_dir / STORE_FILES["tasks"])
    tasks.add(
        f"Aufgabe {marker}",
        provenance,
        notes=f"Notiz {marker}",
        tags=["backup", marker],
        at=T0,
    )
    tasks.close()

    workspace = WorkspaceStore(data_dir / STORE_FILES["workspace"])
    project = workspace.add_project(f"Projekt {marker}", provenance, at=T0)
    workspace.add_note(
        f"Projektnotiz {marker}",
        f"Inhalt {marker}",
        provenance,
        project_id=project.id,
        at=T0,
    )
    workspace.close()

    proposals = ProposalStore(data_dir / STORE_FILES["proposals"])
    proposals.propose(
        ProposalKind.ASSERTION,
        f"Vorschlag {marker}",
        f"Begruendung {marker}",
        assertion_kind=Kind.STATE,
        evidence=[
            Evidence(
                episode_id=episode.id,
                quote=f"Rohtext {marker}",
                digest=episode.digest,
            )
        ],
        at=T0,
    )
    proposals.close()

    audit = AuditLog(data_dir / STORE_FILES["audit"])
    audit.record(
        "test",
        "read",
        "auto",
        "executed",
        {"marker": marker},
        at=T0,
    )
    audit.close()

    rules = RegelStore(data_dir / STORE_FILES["rules"])
    rules.anlegen(
        f"Regel {marker}",
        "notify",
        "auto",
        {"marker": marker},
    )
    rules.close()


@pytest.fixture
def filled_state(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    _populate(data_dir, "A")
    return data_dir


@pytest.fixture
def backup_set(filled_state: Path, tmp_path: Path) -> Path:
    result = create_backup(
        filled_state,
        tmp_path / "backups",
        at=T0,
    )
    return result.path


def _manifest(backup_path: Path) -> dict[str, Any]:
    return json.loads((backup_path / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest(backup_path: Path, document: dict[str, Any]) -> None:
    (backup_path / "manifest.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _entry(document: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in document["stores"] if item["name"] == name)


def _refresh_entry(
    backup_path: Path,
    document: dict[str, Any],
    name: str,
    *,
    schema_version: int | None = None,
) -> Path:
    item = _entry(document, name)
    path = backup_path / item["file"]
    item["sha256"] = _sha256(path)
    if "bytes" in item:
        item["bytes"] = path.stat().st_size
    if "size" in item:
        item["size"] = path.stat().st_size
    if "size_bytes" in item:
        item["size_bytes"] = path.stat().st_size
    if schema_version is not None:
        item["schema_version"] = schema_version
    return path


def _backup_files(backup_path: Path) -> set[str]:
    return {
        path.relative_to(backup_path).as_posix()
        for path in backup_path.rglob("*")
        if path.is_file()
    }


def test_vollstaendiger_backup_satz_bewahrt_alle_store_daten(
    filled_state: Path,
    tmp_path: Path,
) -> None:
    before = _state(filled_state)

    result = create_backup(filled_state, tmp_path / "backups", at=T0)
    backup_hashes_before = {
        path.relative_to(result.path).as_posix(): _sha256(path)
        for path in result.path.rglob("*")
        if path.is_file()
    }
    inspection = inspect_backup(result.path)
    document = _manifest(result.path)

    assert result.status == "complete"
    assert result.format_version == BACKUP_FORMAT_VERSION == 1
    assert result.created_at.tzinfo is not None
    assert result.created_at.utcoffset().total_seconds() == 0
    assert set(result.stores) == set(STORE_FILES)
    assert inspection.backup_id == result.backup_id
    assert inspection.status == "valid"
    assert {
        path.relative_to(result.path).as_posix(): _sha256(path)
        for path in result.path.rglob("*")
        if path.is_file()
    } == backup_hashes_before
    assert document["format_version"] == 1
    assert document["backup_id"] == result.backup_id
    assert document["created_at"].endswith("+00:00") or document["created_at"].endswith("Z")
    assert _backup_files(result.path) == {
        "manifest.json",
        *(f"stores/{filename}" for filename in STORE_FILES.values()),
    }
    assert result.path.name.startswith("icarus-backup-20260831T081530Z-")
    assert ":" not in result.path.name

    entries = {item["name"]: item for item in document["stores"]}
    assert set(entries) == set(STORE_FILES)
    for name, filename in STORE_FILES.items():
        item = entries[name]
        path = result.path / "stores" / filename
        assert item["file"] == f"stores/{filename}"
        assert item["schema_version"] == _version(path) == 1
        assert item["sha256"] == _sha256(path)
        assert _integrity(path) == ["ok"]
        assert _raw_snapshot(path) == before[name]


def test_zwei_backups_im_selben_zeitfenster_bleiben_eindeutig(
    filled_state: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "backups"
    first = create_backup(filled_state, target, at=T0)
    second = create_backup(filled_state, target, at=T0)

    assert first.backup_id != second.backup_id
    assert first.path != second.path
    assert len(list_backups(target)) == 2


def test_rotation_behaelt_nur_die_neuesten_vollstaendigen_saetze(
    filled_state: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "backups"
    for _ in range(5):
        create_backup(filled_state, target, keep=3, at=T0)

    assert len(list_backups(target)) == 3
    assert all(entry["status"] == "complete" for entry in list_backups(target))


def test_rotation_loescht_nie_den_gerade_erfolgreich_gemeldeten_satz(
    filled_state: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import icarus_memory.backup as backup_module

    identities = iter(
        [
            ("icarus-backup-20260831T081530Z-ffffffff",) * 2,
            ("icarus-backup-20260831T081530Z-00000000",) * 2,
        ]
    )
    monkeypatch.setattr(backup_module, "_new_backup_identity", lambda _at: next(identities))
    target = tmp_path / "backups"

    first = create_backup(filled_state, target, keep=1, at=T0)
    second = create_backup(filled_state, target, keep=1, at=T0)

    assert second.path.is_dir()
    assert not first.path.exists()
    assert [entry["name"] for entry in list_backups(target)] == [second.path.name]


def test_beschaedigter_satz_zaehlt_nicht_gegen_backup_rotation(
    filled_state: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "backups"
    damaged = target / "icarus-backup-20990101T000000Z-ffffffff"
    damaged.mkdir(parents=True)
    (damaged / "manifest.json").write_text("{}", encoding="utf-8")

    current = create_backup(filled_state, target, keep=1, at=T0)

    assert current.path.is_dir()
    assert damaged.is_dir()


def test_credentials_und_fremde_zustandsdateien_werden_nicht_exportiert(
    filled_state: Path,
    tmp_path: Path,
) -> None:
    secret = "sk-darf-nicht-im-backup-stehen"
    (filled_state / ".env").write_text(
        f"OPENAI_API_KEY={secret}\n",
        encoding="utf-8",
    )
    (filled_state / "verbindung.json").write_text(
        json.dumps({"token": secret}),
        encoding="utf-8",
    )
    (filled_state / "schluessel.icarus").write_text(secret, encoding="utf-8")
    (filled_state / "einstellungen.json").write_text(
        json.dumps({"mcp_server": [{"umgebung": {"TOKEN": secret}}]}),
        encoding="utf-8",
    )

    result = create_backup(filled_state, tmp_path / "backups", at=T0)

    assert _backup_files(result.path) == {
        "manifest.json",
        *(f"stores/{filename}" for filename in STORE_FILES.values()),
    }
    assert all(
        secret.encode("utf-8") not in path.read_bytes()
        for path in result.path.rglob("*")
        if path.is_file()
    )


def test_restore_in_leeren_zustand_stellt_alle_stores_wieder_her(
    backup_set: Path,
    filled_state: Path,
    tmp_path: Path,
) -> None:
    expected = _state(filled_state)
    empty = tmp_path / "empty"

    result = restore_backup(backup_set, empty)

    assert result.status == "restored"
    assert set(result.stores) == set(STORE_FILES)
    assert _state(empty) == expected
    assert all(_version(empty / filename) == 1 for filename in STORE_FILES.values())


def test_restore_ersetzt_einen_spaeter_veraenderten_gesamtzustand(
    backup_set: Path,
    filled_state: Path,
) -> None:
    expected = _state(filled_state)
    _populate(filled_state, "B")
    assert _state(filled_state) != expected

    restore_backup(backup_set, filled_state)

    assert _state(filled_state) == expected


def test_restore_beruehrt_keine_unbekannten_dateien_im_datenordner(
    backup_set: Path,
    filled_state: Path,
) -> None:
    unrelated = filled_state / "eigene-notiz.txt"
    unrelated.write_text("gehoert nicht zum Restore", encoding="utf-8")

    restore_backup(backup_set, filled_state)

    assert unrelated.read_text(encoding="utf-8") == "gehoert nicht zum Restore"


@pytest.mark.parametrize("invalid_version", [999, -1, -999])
def test_ungueltige_store_version_stoppt_restore_vor_aktiver_aenderung(
    backup_set: Path,
    filled_state: Path,
    invalid_version: int,
) -> None:
    active_before = _file_hashes(filled_state)
    activated = False

    def mark_activation() -> None:
        nonlocal activated
        activated = True

    document = _manifest(backup_set)
    target = backup_set / _entry(document, "tasks")["file"]
    connection = sqlite3.connect(target)
    connection.execute(f"PRAGMA user_version = {invalid_version}")
    connection.commit()
    connection.close()
    _refresh_entry(
        backup_set,
        document,
        "tasks",
        schema_version=invalid_version,
    )
    _write_manifest(backup_set, document)

    with pytest.raises(RestoreCompatibilityError):
        restore_backup(backup_set, filled_state, before_activate=mark_activation)

    assert activated is False
    assert _file_hashes(filled_state) == active_before


def test_hash_manipulation_wird_vor_restore_erkannt(
    backup_set: Path,
    filled_state: Path,
) -> None:
    active_before = _file_hashes(filled_state)
    document = _manifest(backup_set)
    target = backup_set / _entry(document, "tasks")["file"]
    connection = sqlite3.connect(target)
    # Aendert gueltige SQLite-Metadaten. Integritaet und Store-Schema bleiben
    # gueltig, sodass ausschliesslich der fehlende Hashvergleich den Test rot
    # machen wuerde (Sabotageprobe A).
    connection.execute("PRAGMA application_id = 12345")
    connection.commit()
    connection.close()
    assert _integrity(target) == ["ok"]

    with pytest.raises(BackupIntegrityError):
        restore_backup(backup_set, filled_state)

    assert _file_hashes(filled_state) == active_before


def test_aenderung_zwischen_preflight_und_kopie_wird_nicht_aktiviert(
    backup_set: Path,
    filled_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die Restore-Kopie muss selbst gegen den Manifest-Hash gebunden sein."""

    import icarus_memory.backup as backup_module

    active_before = _file_hashes(filled_state)
    original: Callable[..., Any] = backup_module._validate_backup_tree
    validations = 0

    def mutate_after_second_validation(*args: Any, **kwargs: Any) -> Any:
        nonlocal validations
        result = original(*args, **kwargs)
        validations += 1
        if validations == 2:
            document = _manifest(backup_set)
            target = backup_set / _entry(document, "tasks")["file"]
            connection = sqlite3.connect(target)
            connection.execute("PRAGMA application_id = 54321")
            connection.commit()
            connection.close()
        return result

    monkeypatch.setattr(
        backup_module,
        "_validate_backup_tree",
        mutate_after_second_validation,
    )

    with pytest.raises(BackupIntegrityError):
        restore_backup(backup_set, filled_state)

    assert _file_hashes(filled_state) == active_before


def test_fehlender_store_macht_backup_unvollstaendig(
    backup_set: Path,
    filled_state: Path,
) -> None:
    active_before = _file_hashes(filled_state)
    document = _manifest(backup_set)
    (backup_set / _entry(document, "episodes")["file"]).unlink()

    with pytest.raises(IncompleteBackup):
        restore_backup(backup_set, filled_state)

    assert _file_hashes(filled_state) == active_before


def test_beschaedigte_sqlite_datei_mit_passendem_hash_wird_abgelehnt(
    backup_set: Path,
    filled_state: Path,
) -> None:
    active_before = _file_hashes(filled_state)
    document = _manifest(backup_set)
    target = backup_set / _entry(document, "episodes")["file"]
    target.write_bytes(b"das ist keine SQLite-Datenbank")
    _refresh_entry(backup_set, document, "episodes", schema_version=1)
    _write_manifest(backup_set, document)

    with pytest.raises((BackupIntegrityError, RestoreCompatibilityError)):
        restore_backup(backup_set, filled_state)

    assert _file_hashes(filled_state) == active_before


def test_ungueltiges_manifest_wird_abgelehnt(
    backup_set: Path,
    filled_state: Path,
) -> None:
    active_before = _file_hashes(filled_state)
    (backup_set / "manifest.json").write_text("{kein json", encoding="utf-8")

    with pytest.raises(BackupError):
        restore_backup(backup_set, filled_state)

    assert _file_hashes(filled_state) == active_before


def test_unbekannte_backup_format_version_wird_abgelehnt(
    backup_set: Path,
    filled_state: Path,
) -> None:
    active_before = _file_hashes(filled_state)
    document = _manifest(backup_set)
    document["format_version"] = BACKUP_FORMAT_VERSION + 1
    _write_manifest(backup_set, document)

    with pytest.raises(UnsupportedBackupFormat):
        restore_backup(backup_set, filled_state)

    assert _file_hashes(filled_state) == active_before


def test_doppelter_store_im_manifest_wird_abgelehnt(
    backup_set: Path,
    filled_state: Path,
) -> None:
    document = _manifest(backup_set)
    document["stores"].append(dict(document["stores"][0]))
    _write_manifest(backup_set, document)

    with pytest.raises(IncompleteBackup):
        restore_backup(backup_set, filled_state)


def test_falsche_store_zuordnung_wird_trotz_passender_hashes_erkannt(
    backup_set: Path,
    filled_state: Path,
) -> None:
    active_before = _file_hashes(filled_state)
    document = _manifest(backup_set)
    self_model = _entry(document, "self_model")
    episodes = _entry(document, "episodes")
    self_model["file"], episodes["file"] = episodes["file"], self_model["file"]
    self_model["sha256"], episodes["sha256"] = episodes["sha256"], self_model["sha256"]
    for size_key in ("bytes", "size", "size_bytes"):
        if size_key in self_model and size_key in episodes:
            self_model[size_key], episodes[size_key] = (
                episodes[size_key],
                self_model[size_key],
            )
    _write_manifest(backup_set, document)

    with pytest.raises((IncompleteBackup, RestoreCompatibilityError)):
        restore_backup(backup_set, filled_state)

    assert _file_hashes(filled_state) == active_before


def test_zusaetzliche_unerwartete_datei_wird_abgelehnt(
    backup_set: Path,
    filled_state: Path,
) -> None:
    active_before = _file_hashes(filled_state)
    (backup_set / "stores" / "nicht-icarus.sqlite3").write_bytes(b"fremd")

    with pytest.raises(IncompleteBackup):
        restore_backup(backup_set, filled_state)

    assert _file_hashes(filled_state) == active_before


@pytest.mark.parametrize("unsafe_file", ["../outside.sqlite3", "/tmp/outside.sqlite3"])
def test_unsicherer_manifest_pfad_wird_nicht_verfolgt(
    backup_set: Path,
    filled_state: Path,
    unsafe_file: str,
) -> None:
    active_before = _file_hashes(filled_state)
    document = _manifest(backup_set)
    _entry(document, "tasks")["file"] = unsafe_file
    _write_manifest(backup_set, document)

    with pytest.raises((IncompleteBackup, BackupIntegrityError)):
        restore_backup(backup_set, filled_state)

    assert _file_hashes(filled_state) == active_before


def test_symlink_im_backup_satz_wird_abgelehnt(
    backup_set: Path,
    filled_state: Path,
    tmp_path: Path,
) -> None:
    active_before = _file_hashes(filled_state)
    document = _manifest(backup_set)
    entry = _entry(document, "tasks")
    target = backup_set / entry["file"]
    outside = tmp_path / "outside.sqlite3"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(outside)

    with pytest.raises((IncompleteBackup, BackupIntegrityError)):
        restore_backup(backup_set, filled_state)

    assert _file_hashes(filled_state) == active_before
    assert outside.is_file()


def test_teilweiser_backup_fehler_veroeffentlicht_keinen_satz(
    filled_state: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import icarus_memory.backup as backup_module

    target = tmp_path / "backups"
    original: Callable[..., Any] = backup_module._backup_sqlite
    calls = 0

    def fail_third(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise sqlite3.OperationalError("absichtlicher Backup-Abbruch")
        return original(*args, **kwargs)

    monkeypatch.setattr(backup_module, "_backup_sqlite", fail_third)

    with pytest.raises(BackupError):
        create_backup(filled_state, target, at=T0)

    assert not list_backups(target)
    assert not target.exists() or not list(target.iterdir())


def test_echter_verzeichnis_fsync_fehler_wird_nicht_als_erfolg_gemeldet(
    filled_state: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = os.fsync

    def fail_directory(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(errno.EIO, "absichtlicher Persistenzfehler")
        original(descriptor)

    monkeypatch.setattr(os, "fsync", fail_directory)
    target = tmp_path / "backups"

    with pytest.raises(BackupError):
        create_backup(filled_state, target, at=T0)

    assert not list_backups(target)
    assert not target.exists() or not list(target.iterdir())


def test_fehlende_quelle_wird_beim_backup_nicht_still_angelegt(
    filled_state: Path,
    tmp_path: Path,
) -> None:
    missing = filled_state / STORE_FILES["proposals"]
    missing.unlink()

    with pytest.raises(IncompleteBackup):
        create_backup(filled_state, tmp_path / "backups", at=T0)

    assert not missing.exists()
    assert not list_backups(tmp_path / "backups")


def test_fehler_nach_aktivierung_rollt_auf_den_vorherigen_satz_zurueck(
    backup_set: Path,
    filled_state: Path,
) -> None:
    _populate(filled_state, "B")
    before = _state(filled_state)
    before_hashes = _file_hashes(filled_state)

    def final_failure(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("absichtlicher finaler Verifikationsfehler")

    with pytest.raises(RestoreError) as raised:
        restore_backup(backup_set, filled_state, final_verify=final_failure)

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert _state(filled_state) == before
    assert _file_hashes(filled_state) == before_hashes


def test_restore_fehler_in_leerer_umgebung_hinterlaesst_keinen_teilsatz(
    backup_set: Path,
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty-active"

    def final_failure() -> None:
        raise RuntimeError("absichtlicher finaler Verifikationsfehler")

    with pytest.raises(RestoreError):
        restore_backup(backup_set, empty, final_verify=final_failure)

    assert not any((empty / spec.filename).exists() for spec in STORE_SPECS)


def test_teilweiser_aktivierungsfehler_rollt_alle_stores_zurueck(
    backup_set: Path,
    filled_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import icarus_memory.backup as backup_module

    before = _state(filled_state)
    before_hashes = _file_hashes(filled_state)
    original: Callable[..., Any] = backup_module._replace_path
    activations = 0

    def fail_third_activation(source: Path, destination: Path) -> None:
        nonlocal activations
        if source.parent.name == "stores" and destination.parent == filled_state:
            activations += 1
            if activations == 3:
                raise OSError("absichtlicher Aktivierungsabbruch")
        original(source, destination)

    monkeypatch.setattr(backup_module, "_replace_path", fail_third_activation)

    with pytest.raises(RestoreError):
        restore_backup(backup_set, filled_state)

    assert _state(filled_state) == before
    assert _file_hashes(filled_state) == before_hashes


def test_gescheiterter_rollback_hat_eigenen_fatalen_fehlervertrag(
    backup_set: Path,
    filled_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import icarus_memory.backup as backup_module

    def final_failure() -> None:
        raise RuntimeError("finale Verifikation scheitert")

    def rollback_failure(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("Rollback scheitert")

    monkeypatch.setattr(backup_module, "_restore_rollback_files", rollback_failure)

    with pytest.raises(RestoreRollbackError) as raised:
        restore_backup(backup_set, filled_state, final_verify=final_failure)

    assert raised.value.code == "restore_rollback_failed"


def test_offener_wal_store_wird_mit_committetem_letzten_stand_gesichert(
    filled_state: Path,
    tmp_path: Path,
) -> None:
    rules_path = filled_state / STORE_FILES["rules"]
    rules = RegelStore(rules_path)
    rules._conn.execute("PRAGMA wal_autocheckpoint = 0")  # noqa: SLF001
    latest = rules.anlegen(
        "Noch im WAL",
        "notify",
        "auto",
        {"state": "committed"},
    )
    assert rules_path.with_name(rules_path.name + "-wal").is_file()

    result = create_backup(filled_state, tmp_path / "backups", at=T0)
    backup_rules = result.path / "stores" / STORE_FILES["rules"]
    copy = sqlite3.connect(backup_rules)
    try:
        assert copy.execute(
            "SELECT id FROM regeln WHERE id = ?", (latest.id,)
        ).fetchone() == (latest.id,)
    finally:
        copy.close()
        rules.close()
    assert _integrity(backup_rules) == ["ok"]


def test_restore_migriert_nur_eine_temporaere_kopie_des_backups(
    backup_set: Path,
    filled_state: Path,
) -> None:
    document = _manifest(backup_set)
    task_path = backup_set / _entry(document, "tasks")["file"]
    current = sqlite3.connect(task_path)
    row = current.execute(
        "SELECT id, created_at, status, due, title, document FROM tasks"
    ).fetchone()
    current.close()

    legacy = task_path.with_suffix(".legacy")
    connection = sqlite3.connect(legacy)
    connection.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
        "status TEXT NOT NULL, due TEXT, title TEXT NOT NULL, document TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO tasks (id, created_at, status, due, title, document) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        row,
    )
    connection.commit()
    connection.close()
    legacy.replace(task_path)
    _refresh_entry(backup_set, document, "tasks", schema_version=0)
    _write_manifest(backup_set, document)
    backup_bytes_before = task_path.read_bytes()

    result = restore_backup(backup_set, filled_state)

    assert result.migrations["tasks"] == (0, 1)
    assert _version(filled_state / STORE_FILES["tasks"]) == 1
    assert task_path.read_bytes() == backup_bytes_before
    assert _version(task_path) == 0
    restored = TaskStore(filled_state / STORE_FILES["tasks"])
    try:
        assert [task.id for task in restored.all_tasks()] == [str(row[0])]
    finally:
        restored.close()


def test_store_inventar_der_api_ist_exakt_der_p2a_vertrag() -> None:
    assert {spec.name: spec.filename for spec in STORE_SPECS} == STORE_FILES
