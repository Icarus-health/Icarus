"""Versionierte, vollständige lokale Sicherungssätze für ICARUS.

Die sieben autoritativen SQLite-Stores werden einzeln über SQLites Backup-API
gesichert und erst nach vollständiger Prüfung gemeinsam veröffentlicht. SQLite
kann keinen atomaren Snapshot über mehrere Dateien liefern; der gemeinsame
Vertrag ist deshalb: feste Reihenfolge, eine Backup-ID und entweder ein
vollständiger Satz oder kein erfolgreicher Satz.

Offene JSON-Exporte des Selbstmodells bleiben davon getrennt. Sie sind ein
portables Austauschformat, kein operativer Restore-Vertrag.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import importlib.metadata
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .audit import AuditLog
from .backends import SqliteBackend
from .crypto import KDF_ITERATIONS, DecryptionError, seal_json, unseal_json
from .episodes import EPISODES_SCHEMA_VERSION, EpisodeStore
from .migrations import MigrationError
from .proposals import ProposalStore
from .regeln import RegelStore
from .tasks import TaskStore
from .workspace import WorkspaceStore

logger = logging.getLogger(__name__)

BACKUP_FORMAT_VERSION = 1
BACKUP_PREFIX = "icarus-backup-"
EXPORT_MAGIC = "icarus-export-v1"
_BACKUP_NAME = re.compile(r"^icarus-backup-\d{8}T\d{6}Z-[0-9a-f]{8}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BackupError(RuntimeError):
    """Ein Sicherungs- oder Wiederherstellungsvorgang ist fehlgeschlagen."""

    code = "backup_failed"


class BackupIntegrityError(BackupError):
    """Ein Satz oder eine enthaltene Datei ist beschädigt/manipuliert."""

    code = "backup_integrity_failed"


class UnsupportedBackupFormat(BackupError):
    """Das Manifest verwendet ein nicht unterstütztes Backup-Format."""

    code = "unsupported_backup_format"


class IncompleteBackup(BackupError):
    """Ein verpflichtender Bestandteil des Satzes fehlt oder ist unerwartet."""

    code = "incomplete_backup"


class RestoreError(BackupError):
    """Der Restore konnte nicht vollständig aktiviert werden."""

    code = "restore_failed"


class RestoreCompatibilityError(RestoreError):
    """Mindestens ein Store kann von dieser ICARUS-Version nicht geöffnet werden."""

    code = "restore_incompatible"


class RestoreRollbackError(RestoreError):
    """Restore und Wiederherstellung des vorherigen Zustands sind gescheitert."""

    code = "restore_rollback_failed"


@dataclass(frozen=True)
class StoreSpec:
    name: str
    filename: str
    current_version: int
    opener: Callable[[Path], Any]

    @property
    def manifest_path(self) -> str:
        return f"stores/{self.filename}"


def _open_self_model(path: Path) -> SqliteBackend:
    return SqliteBackend(path)


STORE_SPECS = (
    StoreSpec("self_model", "self-model.sqlite3", 1, _open_self_model),
    StoreSpec("episodes", "episodes.sqlite3", EPISODES_SCHEMA_VERSION, EpisodeStore),
    StoreSpec("tasks", "tasks.sqlite3", 1, TaskStore),
    StoreSpec("workspace", "workspace.sqlite3", 1, WorkspaceStore),
    StoreSpec("proposals", "proposals.sqlite3", 1, ProposalStore),
    StoreSpec("audit", "audit.sqlite3", 1, AuditLog),
    StoreSpec("rules", "regeln.sqlite3", 1, RegelStore),
)


@dataclass(frozen=True)
class BackupResult:
    backup_id: str
    path: Path
    created_at: datetime
    stores: tuple[str, ...]
    format_version: int = BACKUP_FORMAT_VERSION
    status: str = "complete"

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "path": str(self.path),
            "name": self.path.name,
            "created_at": _utc_text(self.created_at),
            "created": _utc_text(self.created_at),
            "stores": list(self.stores),
            "format_version": self.format_version,
            "status": self.status,
        }


@dataclass(frozen=True)
class BackupInspection:
    backup_id: str
    path: Path
    created_at: datetime
    stores: tuple[str, ...]
    icarus_version: str
    format_version: int = BACKUP_FORMAT_VERSION
    status: str = "valid"


@dataclass(frozen=True)
class RestoreResult:
    backup_id: str
    stores: tuple[str, ...]
    migrations: dict[str, tuple[int, int]]
    recovery_path: Path | None
    status: str = "restored"

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "restored": self.backup_id,
            "stores": list(self.stores),
            "migrations": {
                name: {"from": versions[0], "to": versions[1]}
                for name, versions in self.migrations.items()
            },
            "recovery_path": str(self.recovery_path) if self.recovery_path else None,
            "status": self.status,
        }


def _application_version() -> str:
    try:
        return importlib.metadata.version("icarus-memory")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise BackupIntegrityError("created_at im Manifest ist ungültig.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BackupIntegrityError("created_at im Manifest ist ungültig.") from exc
    if parsed.tzinfo is None:
        raise BackupIntegrityError("created_at im Manifest muss eine Zeitzone tragen.")
    return parsed.astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    """Persistiert eine fertige Datei, bevor ihr Verzeichnis publiziert wird."""

    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    """Persistiert Verzeichniseinträge, soweit die Plattform das unterstützt."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        # Windows kann Verzeichnisse nicht wie POSIX öffnen. Dort bleibt
        # os.replace() die stärkste portable Zusicherung.
        if os.name == "nt" or exc.errno in {errno.EINVAL, errno.ENOTSUP, errno.EACCES}:
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError as exc:
        # Einige Dateisysteme unterstützen fsync() für Verzeichnisse nicht.
        if os.name == "nt" or exc.errno in {errno.EINVAL, errno.ENOTSUP, errno.EBADF}:
            return
        raise
    finally:
        os.close(descriptor)


def _write_json_durable(path: Path, document: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _copy_verified_store(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
) -> None:
    """Kopiert genau die Bytes, deren Manifest-Hash geprüft wird.

    Der Quelldeskriptor wird einmal mit ``O_NOFOLLOW`` (soweit verfügbar)
    geöffnet. Ein Austausch des Pfads nach dem Öffnen kann deshalb nicht dazu
    führen, dass andere als die während dieses Kopiervorgangs gehashten Bytes
    in den Restore gelangen.
    """

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    digest = hashlib.sha256()
    byte_count = 0
    try:
        descriptor = os.open(source, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BackupIntegrityError(f"Store ist keine reguläre Datei: {source.name}")
        with os.fdopen(descriptor, "rb", closefd=True) as reader:
            descriptor = None
            with destination.open("xb") as writer:
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    digest.update(chunk)
                    byte_count += len(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
    except OSError as exc:
        raise BackupIntegrityError(
            f"Store {source.name} konnte nicht sicher gelesen werden."
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)

    if byte_count != expected_bytes or not hmac.compare_digest(
        digest.hexdigest(), expected_sha256
    ):
        destination.unlink(missing_ok=True)
        raise BackupIntegrityError(
            f"Store {source.name} wurde während der Restore-Vorbereitung verändert."
        )


def _readonly_connection(
    path: Path,
    *,
    immutable: bool = False,
) -> sqlite3.Connection:
    option = "&immutable=1" if immutable else ""
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro{option}", uri=True)


def _integrity_and_version(path: Path) -> tuple[str, int]:
    connection: sqlite3.Connection | None = None
    try:
        # Backup-/Staging-Dateien sind zu diesem Zeitpunkt geschlossen und
        # unveränderlich. `immutable=1` verhindert, dass SQLite allein für
        # eine Prüfung neue WAL-/SHM-Sidecars neben dem Backup anlegt.
        connection = _readonly_connection(path, immutable=True)
        row = connection.execute("PRAGMA integrity_check").fetchone()
        integrity = str(row[0]) if row else "no result"
        version_row = connection.execute("PRAGMA user_version").fetchone()
        if version_row is None:
            raise sqlite3.DatabaseError("PRAGMA user_version ohne Ergebnis")
        return integrity, int(version_row[0])
    except sqlite3.DatabaseError as exc:
        raise BackupIntegrityError(f"SQLite-Datei nicht lesbar: {path.name}") from exc
    finally:
        if connection is not None:
            connection.close()


def _backup_sqlite(
    source_path: Path,
    destination_path: Path,
    *,
    source_immutable: bool = False,
) -> None:
    """Kopiert committed SQLite-Zustand einschließlich eines offenen WAL."""

    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    try:
        source = _readonly_connection(source_path, immutable=source_immutable)
        destination = sqlite3.connect(str(destination_path))
        source.backup(destination)
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"Store {source_path.name} konnte nicht gesichert werden.") from exc
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()


def _open_and_close(spec: StoreSpec, path: Path) -> None:
    handle = None
    try:
        handle = spec.opener(path)
    finally:
        if handle is not None:
            handle.close()
    # RegelStore nutzt WAL. Nach dem letzten Close ist der Hauptbestand
    # vollständig; übrig gebliebene leere Sidecars gehören weder in das
    # Manifest noch in einen portablen Backup-Satz.
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()


def _new_backup_identity(at: datetime) -> tuple[str, str]:
    stamp = _utc(at).strftime("%Y%m%dT%H%M%SZ")
    backup_id = f"{BACKUP_PREFIX}{stamp}-{uuid.uuid4().hex[:8]}"
    return backup_id, backup_id


def create_backup(
    data_dir: Path,
    target_dir: Path,
    keep: int | None = 14,
    at: datetime | None = None,
) -> BackupResult:
    """Erzeugt und veröffentlicht einen vollständigen Backup-Satz."""

    data_dir = Path(data_dir)
    target_dir = Path(target_dir)
    if keep is not None and keep < 1:
        raise ValueError("keep muss mindestens 1 sein")
    if target_dir.is_symlink():
        raise BackupIntegrityError("Das Backup-Ziel darf kein Symlink sein.")
    missing = [spec.filename for spec in STORE_SPECS if not (data_dir / spec.filename).is_file()]
    if missing:
        raise IncompleteBackup(
            "Vollständige Sicherung nicht möglich; fehlende Stores: "
            + ", ".join(missing)
        )
    for spec in STORE_SPECS:
        if (data_dir / spec.filename).is_symlink():
            raise BackupIntegrityError(f"Store darf kein Symlink sein: {spec.filename}")

    created_at = _utc(at)
    backup_id, name = _new_backup_identity(created_at)
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / name
    temp_path = Path(tempfile.mkdtemp(prefix=f".tmp-{name}-", dir=str(target_dir)))
    stores_dir = temp_path / "stores"
    stores_dir.mkdir()
    manifest_stores: list[dict[str, Any]] = []

    try:
        for spec in STORE_SPECS:
            source = data_dir / spec.filename
            destination = stores_dir / spec.filename
            _backup_sqlite(source, destination)
            try:
                _open_and_close(spec, destination)
            except MigrationError as exc:
                raise BackupIntegrityError(
                    f"Store {spec.name} entspricht keinem unterstützten Schema."
                ) from exc
            integrity, version = _integrity_and_version(destination)
            if integrity != "ok":
                raise BackupIntegrityError(
                    f"Integritätsprüfung für {spec.name} fehlgeschlagen: {integrity}"
                )
            if version != spec.current_version:
                raise BackupIntegrityError(
                    f"Store {spec.name} besitzt nach Prüfung Version {version}, "
                    f"erwartet ist {spec.current_version}."
                )
            _fsync_file(destination)
            manifest_stores.append(
                {
                    "name": spec.name,
                    "file": spec.manifest_path,
                    "schema_version": version,
                    "sha256": _sha256(destination),
                    "bytes": destination.stat().st_size,
                    "integrity_check": "ok",
                }
            )

        manifest = {
            "format_version": BACKUP_FORMAT_VERSION,
            "backup_id": backup_id,
            "created_at": _utc_text(created_at),
            "icarus_version": _application_version(),
            "stores": manifest_stores,
        }
        _write_json_durable(temp_path / "manifest.json", manifest)
        _validate_backup_tree(temp_path, require_directory_name=False)
        _fsync_directory(stores_dir)
        _fsync_directory(temp_path)
        _replace_path(temp_path, final_path)
        _fsync_directory(target_dir)
    except Exception as exc:
        shutil.rmtree(temp_path, ignore_errors=True)
        logger.exception(
            "Backup fehlgeschlagen: backup_id=%s data_dir=%s target_dir=%s",
            backup_id,
            data_dir,
            target_dir,
        )
        if isinstance(exc, BackupError):
            raise
        raise BackupError("Der vollständige Backup-Satz konnte nicht erstellt werden.") from exc

    if keep is not None:
        try:
            prune_backups(target_dir, keep, preserve=final_path)
        except OSError:
            # Der neue Satz ist bereits vollständig und atomar veröffentlicht.
            # Ein Rotationsproblem macht ihn nicht nachträglich ungültig.
            logger.exception("Backup-Rotation fehlgeschlagen: target_dir=%s", target_dir)
    logger.info(
        "Backup veröffentlicht: backup_id=%s stores=%d path=%s",
        backup_id,
        len(STORE_SPECS),
        final_path,
    )
    return BackupResult(
        backup_id=backup_id,
        path=final_path,
        created_at=created_at,
        stores=tuple(spec.name for spec in STORE_SPECS),
    )


def _read_manifest(path: Path, *, require_directory_name: bool = True) -> dict[str, Any]:
    manifest_path = path / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise IncompleteBackup("manifest.json fehlt oder ist kein reguläres Dokument.")
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupIntegrityError("manifest.json ist nicht lesbares JSON.") from exc
    if not isinstance(document, dict):
        raise BackupIntegrityError("manifest.json muss ein JSON-Objekt sein.")
    expected_keys = {
        "format_version", "backup_id", "created_at", "icarus_version", "stores"
    }
    if set(document) != expected_keys:
        raise BackupIntegrityError("manifest.json besitzt unbekannte oder fehlende Felder.")
    format_version = document.get("format_version")
    if not isinstance(format_version, int) or isinstance(format_version, bool):
        raise BackupIntegrityError("format_version im Manifest ist ungültig.")
    if format_version != BACKUP_FORMAT_VERSION:
        raise UnsupportedBackupFormat(
            f"Backup-Format {format_version} wird nicht unterstützt "
            f"(unterstützt: {BACKUP_FORMAT_VERSION})."
        )
    backup_id = document.get("backup_id")
    if not isinstance(backup_id, str) or not _BACKUP_NAME.fullmatch(backup_id):
        raise BackupIntegrityError("backup_id im Manifest ist ungültig.")
    if require_directory_name and path.name != backup_id:
        raise BackupIntegrityError("Backup-Verzeichnis und backup_id stimmen nicht überein.")
    _parse_utc(document.get("created_at"))
    if not isinstance(document.get("icarus_version"), str) or not document["icarus_version"]:
        raise BackupIntegrityError("icarus_version im Manifest ist ungültig.")
    return document


def _validate_backup_tree(
    path: Path,
    *,
    require_directory_name: bool = True,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise IncompleteBackup("Der Backup-Satz ist kein reguläres Verzeichnis.")
    root_names = {entry.name for entry in path.iterdir()}
    if root_names != {"manifest.json", "stores"}:
        raise IncompleteBackup("Der Backup-Satz enthält fehlende oder unerwartete Dateien.")
    stores_dir = path / "stores"
    if stores_dir.is_symlink() or not stores_dir.is_dir():
        raise IncompleteBackup("Das Store-Verzeichnis fehlt oder ist ein Symlink.")
    actual_files = {entry.name for entry in stores_dir.iterdir()}
    expected_files = {spec.filename for spec in STORE_SPECS}
    if actual_files != expected_files:
        raise IncompleteBackup(
            "Der Backup-Satz enthält nicht exakt alle erwarteten Stores "
            f"(gefunden: {sorted(actual_files)})."
        )
    for entry in stores_dir.iterdir():
        if entry.is_symlink() or not entry.is_file():
            raise IncompleteBackup(f"Store ist keine reguläre Datei: {entry.name}")

    document = _read_manifest(path, require_directory_name=require_directory_name)
    entries = document.get("stores")
    if not isinstance(entries, list) or len(entries) != len(STORE_SPECS):
        raise IncompleteBackup("Das Manifest enthält nicht exakt alle erwarteten Stores.")
    by_name: dict[str, dict[str, Any]] = {}
    expected_entry_keys = {
        "name", "file", "schema_version", "sha256", "bytes", "integrity_check"
    }
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != expected_entry_keys:
            raise BackupIntegrityError("Ein Store-Eintrag im Manifest ist ungültig.")
        name = entry.get("name")
        if not isinstance(name, str) or name in by_name:
            raise BackupIntegrityError("Store-Namen im Manifest sind ungültig oder doppelt.")
        by_name[name] = entry

    if set(by_name) != {spec.name for spec in STORE_SPECS}:
        raise IncompleteBackup("Das Manifest enthält eine falsche Store-Zuordnung.")

    for spec in STORE_SPECS:
        entry = by_name[spec.name]
        if entry.get("file") != spec.manifest_path:
            raise IncompleteBackup(f"Ungültige Store-Zuordnung für {spec.name}.")
        schema_version = entry.get("schema_version")
        byte_count = entry.get("bytes")
        sha256 = entry.get("sha256")
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or byte_count < 0
            or not isinstance(sha256, str)
            or not _SHA256.fullmatch(sha256)
            or entry.get("integrity_check") != "ok"
        ):
            raise BackupIntegrityError(f"Ungültige Metadaten für Store {spec.name}.")
        store_path = stores_dir / spec.filename
        if store_path.stat().st_size != byte_count:
            raise BackupIntegrityError(f"Dateigröße von Store {spec.name} stimmt nicht.")
        if not hmac.compare_digest(_sha256(store_path), sha256):
            raise BackupIntegrityError(f"SHA-256 von Store {spec.name} stimmt nicht.")
        integrity, actual_version = _integrity_and_version(store_path)
        if integrity != "ok":
            raise BackupIntegrityError(
                f"Integritätsprüfung für Store {spec.name} fehlgeschlagen: {integrity}"
            )
        if actual_version != schema_version:
            raise BackupIntegrityError(
                f"Schema-Version von Store {spec.name} stimmt nicht mit dem Manifest überein."
            )
        if actual_version < 0:
            raise RestoreCompatibilityError(
                f"Store {spec.name} besitzt eine ungültige negative Schema-Version."
            )
        if actual_version > spec.current_version:
            raise RestoreCompatibilityError(
                f"Store {spec.name} stammt aus einer neueren ICARUS-Version "
                f"({actual_version} > {spec.current_version})."
            )
    return document, by_name


def _prepare_compatible_copy(
    backup_path: Path,
    data_dir: Path,
) -> tuple[Path, dict[str, tuple[int, int]]]:
    document, entries = _validate_backup_tree(backup_path)
    del document
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=".tmp-icarus-restore-", dir=str(data_dir.parent))
    )
    stores_dir = staging / "stores"
    stores_dir.mkdir()
    migrations: dict[str, tuple[int, int]] = {}
    try:
        for spec in STORE_SPECS:
            source = backup_path / entries[spec.name]["file"]
            target = stores_dir / spec.filename
            before = int(entries[spec.name]["schema_version"])
            _copy_verified_store(
                source,
                target,
                expected_sha256=entries[spec.name]["sha256"],
                expected_bytes=entries[spec.name]["bytes"],
            )
            try:
                _open_and_close(spec, target)
            except MigrationError as exc:
                raise RestoreCompatibilityError(
                    f"Store {spec.name} kann nicht sicher geöffnet oder migriert werden."
                ) from exc
            integrity, after = _integrity_and_version(target)
            if integrity != "ok" or after != spec.current_version:
                raise RestoreCompatibilityError(
                    f"Store {spec.name} erfüllt nach der Vorbereitung nicht den aktuellen Vertrag."
                )
            _fsync_file(target)
            migrations[spec.name] = (before, after)
        _fsync_directory(stores_dir)
        _fsync_directory(staging)
        return staging, migrations
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def inspect_backup(path: Path) -> BackupInspection:
    """Prüft Format, Hashes, SQLite-Integrität und P2a-Kompatibilität read-only."""

    path = Path(path)
    document, _ = _validate_backup_tree(path)
    scratch_parent = Path(tempfile.mkdtemp(prefix="icarus-inspect-"))
    try:
        prepared, _ = _prepare_compatible_copy(path, scratch_parent / "data")
        shutil.rmtree(prepared, ignore_errors=True)
    finally:
        shutil.rmtree(scratch_parent, ignore_errors=True)
    return BackupInspection(
        backup_id=document["backup_id"],
        path=path,
        created_at=_parse_utc(document["created_at"]),
        stores=tuple(spec.name for spec in STORE_SPECS),
        icarus_version=document["icarus_version"],
    )


def _replace_path(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _remove_known_database_files(data_dir: Path, filename: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        target = data_dir / f"{filename}{suffix}"
        if target.exists() or target.is_symlink():
            target.unlink()


def _active_state_complete(data_dir: Path) -> bool:
    return all((data_dir / spec.filename).is_file() for spec in STORE_SPECS)


def _restore_rollback_files(
    rollback_dir: Path,
    data_dir: Path,
    previous_files: set[str],
) -> None:
    for spec in STORE_SPECS:
        for suffix in ("", "-wal", "-shm"):
            name = f"{spec.filename}{suffix}"
            source = rollback_dir / name
            active = data_dir / name
            if source.is_file():
                if active.exists() or active.is_symlink():
                    active.unlink()
                _replace_path(source, active)
            elif name not in previous_files and (active.exists() or active.is_symlink()):
                # Vorher nicht vorhanden: Ein bereits aktiviertes neues
                # Fragment muss verschwinden. Eine noch unberührte alte Datei
                # bleibt dagegen exakt dort, wo sie war.
                active.unlink()


def restore_backup(
    backup_path: Path,
    data_dir: Path,
    *,
    before_activate: Callable[[], None] | None = None,
    final_verify: Callable[[], None] | None = None,
    after_rollback: Callable[[], None] | None = None,
) -> RestoreResult:
    """Stellt einen vollständigen Satz mit Preflight und Rollback wieder her."""

    backup_path = Path(backup_path)
    data_dir = Path(data_dir)
    document, _ = _validate_backup_tree(backup_path)
    prepared, migrations = _prepare_compatible_copy(backup_path, data_dir)
    rollback_dir = Path(
        tempfile.mkdtemp(prefix=".tmp-icarus-rollback-", dir=str(data_dir.parent))
    )
    recovery_path: Path | None = None
    quiesced = False
    activated = False
    activation_started = False
    rollback_failed = False
    previous_files: set[str] = set()
    try:
        if before_activate is not None:
            before_activate()
        quiesced = True

        for spec in STORE_SPECS:
            active = data_dir / spec.filename
            if active.is_symlink():
                raise RestoreError(
                    f"Aktiver Store darf kein Symlink sein: {spec.filename}"
                )

        if _active_state_complete(data_dir):
            recovery = create_backup(
                data_dir,
                data_dir / "sicherungen",
                keep=None,
            )
            recovery_path = recovery.path

        data_dir.mkdir(parents=True, exist_ok=True)
        previous_files = {
            f"{spec.filename}{suffix}"
            for spec in STORE_SPECS
            for suffix in ("", "-wal", "-shm")
            if (data_dir / f"{spec.filename}{suffix}").is_file()
        }
        activation_started = True
        for spec in STORE_SPECS:
            # Unter der Quiescence-Barriere die Originaldateien physisch zur
            # Seite legen. So ist ein Rollback nicht nur fachlich, sondern
            # bytegenau und bewahrt auch einen eventuell vorhandenen WAL.
            for suffix in ("", "-wal", "-shm"):
                active = data_dir / f"{spec.filename}{suffix}"
                if active.is_file():
                    _replace_path(active, rollback_dir / active.name)
            _replace_path(prepared / "stores" / spec.filename, data_dir / spec.filename)
        activated = True

        for spec in STORE_SPECS:
            _open_and_close(spec, data_dir / spec.filename)
            integrity, version = _integrity_and_version(data_dir / spec.filename)
            if integrity != "ok" or version != spec.current_version:
                raise RestoreError(f"Finale Prüfung von Store {spec.name} ist fehlgeschlagen.")
        if final_verify is not None:
            final_verify()
        _fsync_directory(data_dir)
    except Exception as exc:
        if quiesced:
            try:
                if activated and before_activate is not None:
                    before_activate()
                if activation_started:
                    _restore_rollback_files(rollback_dir, data_dir, previous_files)
                if after_rollback is not None:
                    after_rollback()
                _fsync_directory(data_dir)
            except Exception as rollback_exc:
                rollback_failed = True
                logger.exception(
                    "Restore-Rollback fehlgeschlagen: backup_id=%s data_dir=%s",
                    document["backup_id"],
                    data_dir,
                )
                raise RestoreRollbackError(
                    "Restore und anschließender Rollback sind fehlgeschlagen; "
                    f"Recovery-Satz: {recovery_path or rollback_dir}."
                ) from rollback_exc
        if isinstance(exc, BackupError):
            raise
        raise RestoreError("Restore wurde abgebrochen; der vorherige Zustand blieb erhalten.") from exc
    finally:
        shutil.rmtree(prepared, ignore_errors=True)
        if not rollback_failed:
            shutil.rmtree(rollback_dir, ignore_errors=True)

    logger.info(
        "Restore abgeschlossen: backup_id=%s stores=%d recovery=%s",
        document["backup_id"],
        len(STORE_SPECS),
        recovery_path,
    )
    return RestoreResult(
        backup_id=document["backup_id"],
        stores=tuple(spec.name for spec in STORE_SPECS),
        migrations=migrations,
        recovery_path=recovery_path,
    )


def list_backups(target_dir: Path) -> list[dict[str, Any]]:
    """Listet veröffentlichte Sätze; temporäre und Legacy-Dateien bleiben außen."""

    target_dir = Path(target_dir)
    if not target_dir.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(target_dir.iterdir(), key=lambda item: item.name, reverse=True):
        if path.is_symlink() or not path.is_dir() or not _BACKUP_NAME.fullmatch(path.name):
            continue
        try:
            manifest = _read_manifest(path)
            stores_dir = path / "stores"
            byte_count = sum(
                item.stat().st_size
                for item in stores_dir.iterdir()
                if item.is_file() and not item.is_symlink()
            )
            results.append(
                {
                    "name": path.name,
                    "backup_id": manifest["backup_id"],
                    "path": str(path),
                    "bytes": byte_count,
                    "created": manifest["created_at"],
                    "stores": len(manifest.get("stores", [])),
                    "format_version": manifest["format_version"],
                    "status": "complete",
                }
            )
        except (BackupError, OSError):
            results.append({"name": path.name, "path": str(path), "status": "invalid"})
    return results


def prune_backups(
    target_dir: Path,
    keep: int,
    *,
    preserve: Path | None = None,
) -> list[Path]:
    """Behält die neuesten vollständigen Sätze; unbekannte Dateien bleiben unberührt."""

    if keep < 0:
        raise ValueError("keep darf nicht negativ sein")
    published: list[tuple[datetime, int, str, Path]] = []
    for path in Path(target_dir).iterdir():
        if not path.is_dir() or path.is_symlink() or not _BACKUP_NAME.fullmatch(path.name):
            continue
        try:
            manifest, _ = _validate_backup_tree(path)
        except BackupError:
            # Ein beschädigter, nur passend benannter Satz darf keinen gültigen
            # Satz aus der Rotation verdrängen.
            continue
        published.append(
            (_parse_utc(manifest["created_at"]), path.stat().st_mtime_ns, path.name, path)
        )
    published.sort(key=lambda item: item[:3], reverse=True)
    ordered = [item[3] for item in published]
    if preserve is not None:
        preserve = Path(preserve)
        ordered = [preserve] + [path for path in ordered if path != preserve]
    removed: list[Path] = []
    for old in ordered[keep:]:
        shutil.rmtree(old)
        removed.append(old)
    return removed


# -- Offener Export -------------------------------------------------------


def export_model(model_dict: dict[str, Any], passphrase: str | None = None) -> str:
    """Schreibt das Selbstmodell als JSON, optional verschlüsselt."""

    if not passphrase:
        return json.dumps(model_dict, ensure_ascii=False, indent=2)
    return seal_json(model_dict, passphrase, EXPORT_MAGIC)


def import_model(payload: str, passphrase: str | None = None) -> dict[str, Any]:
    """Liest einen Export, entschlüsselt bei Bedarf und prüft die Unversehrtheit."""

    document = json.loads(payload)
    if document.get("format") != EXPORT_MAGIC:
        return document
    if not passphrase:
        raise BackupError("Dieser Export ist verschlüsselt. Passphrase erforderlich.")
    try:
        return unseal_json(payload, passphrase)
    except DecryptionError as exc:
        raise BackupError(str(exc)) from exc


__all__ = [
    "BACKUP_FORMAT_VERSION",
    "STORE_SPECS",
    "BackupError",
    "BackupIntegrityError",
    "BackupInspection",
    "BackupResult",
    "IncompleteBackup",
    "RestoreCompatibilityError",
    "RestoreError",
    "RestoreRollbackError",
    "RestoreResult",
    "StoreSpec",
    "UnsupportedBackupFormat",
    "create_backup",
    "export_model",
    "import_model",
    "inspect_backup",
    "list_backups",
    "prune_backups",
    "restore_backup",
]
