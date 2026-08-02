"""Sicherung und Wiederherstellung von Icarus.

Kleine Einzeltests und alte Installationen können weiterhin nur das Selbstmodell
als SQLite-Snapshot sichern. Sobald im Datenverzeichnis weitere Icarus-Bausteine
liegen, entsteht dagegen ein vollständiges, versioniertes Installations-Backup.

Das Archiv enthält ausschließlich die verbindlichen lokalen Daten:

* Selbstmodell und Arbeitsdatenbanken,
* Audit-Log, Aufgaben, Projekte, Episoden und Vorschläge,
* Einstellungen,
* die verschlüsselte Schlüsseldatei, falls dieser Fallback verwendet wird.

Der Betriebssystem-Schlüsselbund wird bewusst nicht exportiert. Auf einem neuen
Gerät müssen dessen Geheimnisse erneut eingetragen werden.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .crypto import DecryptionError, seal_json, unseal_json
from .model import now

SNAPSHOT_PREFIX = "self-model-"
BUNDLE_PREFIX = "icarus-"
INSTALLATION_FORMAT = "icarus-installation-backup-v1"
MANIFEST_NAME = "manifest.json"
EXPORT_MAGIC = "icarus-export-v1"

DATABASE_FILES = (
    "self-model.sqlite3",
    "audit.sqlite3",
    "tasks.sqlite3",
    "workspace.sqlite3",
    "episodes.sqlite3",
    "proposals.sqlite3",
)
CONFIG_FILES = (
    "einstellungen.json",
    "schluessel.icarus",
)
KNOWN_FILES = frozenset((*DATABASE_FILES, *CONFIG_FILES))


class BackupError(Exception):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_sqlite(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(source_path))
    try:
        destination = sqlite3.connect(str(target_path))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def _check_sqlite(path: Path, expected_table: str | None = None) -> None:
    connection = sqlite3.connect(str(path))
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupError(
                f"Datenbank {path.name} ist beschädigt: "
                f"{result[0] if result else 'unlesbar'}"
            )
        if expected_table:
            connection.execute(f"SELECT COUNT(*) FROM {expected_table}")
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"Datenbank {path.name} ist nicht lesbar: {exc}") from exc
    finally:
        connection.close()


def _installation_members(data_dir: Path) -> list[Path]:
    return [
        data_dir / name
        for name in (*DATABASE_FILES, *CONFIG_FILES)
        if (data_dir / name).is_file()
    ]


def _snapshot_paths(target_dir: Path) -> list[Path]:
    if not target_dir.is_dir():
        return []
    return [
        *target_dir.glob(f"{BUNDLE_PREFIX}*.zip"),
        *target_dir.glob(f"{SNAPSHOT_PREFIX}*.sqlite3"),
    ]


# -- Snapshots -------------------------------------------------------------


def snapshot(
    db_path: Path,
    target_dir: Path,
    keep: int = 14,
    at: datetime | None = None,
) -> Path:
    """Sichert Icarus konsistent.

    Gibt es neben dem Selbstmodell weitere Icarus-Daten, wird die vollständige
    Installation als ZIP mit Manifest gesichert. Ein isoliertes Selbstmodell
    bleibt aus Rückwärtskompatibilität ein direkt lesbarer SQLite-Snapshot.
    """
    at = at or now()
    if not db_path.is_file():
        raise BackupError(f"Keine Datenbank unter {db_path}")

    data_dir = db_path.parent
    members = _installation_members(data_dir)
    if db_path.name == "self-model.sqlite3" and len(members) > 1:
        return snapshot_installation(data_dir, target_dir, keep=keep, at=at)

    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = at.strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{SNAPSHOT_PREFIX}{stamp}.sqlite3"
    _copy_sqlite(db_path, target)
    prune(target_dir, keep)
    return target


def snapshot_installation(
    data_dir: Path,
    target_dir: Path,
    keep: int = 14,
    at: datetime | None = None,
) -> Path:
    """Legt ein vollständiges, versioniertes Icarus-Backup an."""
    at = at or now()
    members = _installation_members(data_dir)
    if not members:
        raise BackupError(f"Keine Icarus-Daten unter {data_dir}")

    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = at.strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{BUNDLE_PREFIX}{stamp}.zip"
    temporary_archive = target_dir / f".{target.name}.tmp"

    with tempfile.TemporaryDirectory(
        prefix=".icarus-snapshot-", dir=target_dir
    ) as temporary:
        staging = Path(temporary)
        entries: list[dict[str, Any]] = []

        for source in members:
            staged = staging / source.name
            if source.name in DATABASE_FILES:
                _copy_sqlite(source, staged)
                _check_sqlite(
                    staged,
                    expected_table=(
                        "assertions" if source.name == "self-model.sqlite3" else None
                    ),
                )
                kind = "sqlite"
            else:
                shutil.copy2(source, staged)
                kind = "file"

            entries.append(
                {
                    "name": source.name,
                    "kind": kind,
                    "bytes": staged.stat().st_size,
                    "sha256": _sha256(staged),
                }
            )

        manifest = {
            "format": INSTALLATION_FORMAT,
            "version": 1,
            "created_at": at.astimezone().isoformat(),
            "files": entries,
            "notes": {
                "os_keychain": (
                    "Zugangsdaten aus dem Betriebssystem-Schlüsselbund sind "
                    "nicht enthalten und müssen auf einem neuen Gerät erneut "
                    "eingetragen werden."
                )
            },
        }
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        try:
            with zipfile.ZipFile(
                temporary_archive, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.write(staging / MANIFEST_NAME, MANIFEST_NAME)
                for entry in entries:
                    archive.write(staging / entry["name"], entry["name"])
            os.replace(temporary_archive, target)
        finally:
            temporary_archive.unlink(missing_ok=True)

    prune(target_dir, keep)
    return target


def prune(target_dir: Path, keep: int) -> list[Path]:
    """Behält die neuesten `keep` Sicherungen unabhängig vom alten Format."""
    snapshots = sorted(
        _snapshot_paths(target_dir),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    removed = []
    for old in snapshots[max(0, keep):]:
        old.unlink()
        removed.append(old)
    return removed


def list_snapshots(target_dir: Path) -> list[dict[str, Any]]:
    entries = []
    for path in sorted(
        _snapshot_paths(target_dir),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    ):
        stat = path.stat()
        item: dict[str, Any] = {
            "name": path.name,
            "path": str(path),
            "bytes": stat.st_size,
            "created": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
            "kind": "installation" if path.suffix == ".zip" else "self-model",
        }
        if path.suffix == ".zip":
            try:
                with zipfile.ZipFile(path) as archive:
                    manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
                item["created"] = manifest.get("created_at", item["created"])
                item["members"] = [
                    entry.get("name") for entry in manifest.get("files", [])
                ]
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                item["invalid"] = True
        entries.append(item)
    return entries


def restore(snapshot_path: Path, db_path: Path) -> None:
    """Spielt eine alte Einzelsicherung oder ein vollständiges Backup zurück."""
    if not snapshot_path.is_file():
        raise BackupError(f"Kein Snapshot unter {snapshot_path}")

    if snapshot_path.suffix == ".zip":
        _restore_installation(snapshot_path, db_path.parent)
        return

    _check_sqlite(snapshot_path, expected_table="assertions")

    if db_path.is_file():
        aside = db_path.with_suffix(
            f".vor-wiederherstellung-{now():%Y%m%dT%H%M%SZ}.sqlite3"
        )
        _copy_sqlite(db_path, aside)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    _copy_sqlite(snapshot_path, db_path)


def _restore_installation(snapshot_path: Path, data_dir: Path) -> None:
    """Validiert alles, bevor die erste Nutzdatei verändert wird.

    Das Staging liegt im Icarus-Datenverzeichnis. Dieser Ort gehört im Desktop-
    wie im Containerbetrieb dem Icarus-Prozess. Das Elternverzeichnis kann
    absichtlich nicht beschreibbar sein, etwa `/` bei `/daten` im Container.
    """
    try:
        archive = zipfile.ZipFile(snapshot_path)
    except zipfile.BadZipFile as exc:
        raise BackupError(f"Sicherung ist kein lesbares ZIP-Archiv: {exc}") from exc

    data_dir.mkdir(parents=True, exist_ok=True)
    with archive, tempfile.TemporaryDirectory(
        prefix=".icarus-restore-", dir=data_dir
    ) as temporary:
        staging = Path(temporary)
        try:
            manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, ValueError) as exc:
            raise BackupError(f"Manifest der Sicherung ist nicht lesbar: {exc}") from exc

        if manifest.get("format") != INSTALLATION_FORMAT or manifest.get("version") != 1:
            raise BackupError("Unbekanntes oder nicht unterstütztes Sicherungsformat.")

        raw_entries = manifest.get("files")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise BackupError("Die Sicherung enthält keine Nutzdateien.")

        names = archive.namelist()
        if len(names) != len(set(names)):
            raise BackupError("Die Sicherung enthält doppelte Dateinamen.")

        declared: set[str] = set()
        for raw in raw_entries:
            if not isinstance(raw, dict):
                raise BackupError("Ungültiger Eintrag im Sicherungsmanifest.")
            name = str(raw.get("name", ""))
            if name not in KNOWN_FILES or Path(name).name != name:
                raise BackupError(f"Unzulässige Datei in der Sicherung: {name!r}")
            if name in declared:
                raise BackupError(f"Datei doppelt im Manifest: {name}")
            if name not in names:
                raise BackupError(f"Datei fehlt im Archiv: {name}")
            declared.add(name)

            target = staging / name
            with archive.open(name) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)

            if target.stat().st_size != int(raw.get("bytes", -1)):
                raise BackupError(f"Größe stimmt nicht für {name}.")
            if _sha256(target) != raw.get("sha256"):
                raise BackupError(f"Prüfsumme stimmt nicht für {name}.")
            if name in DATABASE_FILES:
                _check_sqlite(
                    target,
                    expected_table=(
                        "assertions" if name == "self-model.sqlite3" else None
                    ),
                )

        unexpected = set(names) - {MANIFEST_NAME} - declared
        if unexpected:
            raise BackupError(
                "Die Sicherung enthält nicht deklarierte Dateien: "
                + ", ".join(sorted(unexpected))
            )

        stamp = now().strftime("%Y%m%dT%H%M%SZ")
        recovery_dir = data_dir / f".icarus-recovery-{stamp}"

        # Das Selbstmodell behält aus Rückwärtskompatibilität seinen bisherigen,
        # sichtbaren Namen. Die übrigen Dateien landen gesammelt in einem
        # Recovery-Ordner statt die Datenablage mit sechs ähnlich benannten
        # Dateien zu füllen. Beides bleibt manuell lesbar.
        for current in _installation_members(data_dir):
            if current.name == "self-model.sqlite3":
                aside = current.with_name(
                    f"self-model.vor-wiederherstellung-{stamp}.sqlite3"
                )
                _copy_sqlite(current, aside)
                continue

            recovery_dir.mkdir(parents=True, exist_ok=True)
            aside = recovery_dir / current.name
            if current.name in DATABASE_FILES:
                _copy_sqlite(current, aside)
            else:
                shutil.copy2(current, aside)

        # SQLite wird in die bestehenden Dateien zurückgespielt. Dadurch sehen
        # auch bereits geöffnete Store-Verbindungen den neuen Stand, statt auf
        # einem ersetzten Inode weiterzuarbeiten.
        for name in DATABASE_FILES:
            source = staging / name
            if source.is_file():
                _copy_sqlite(source, data_dir / name)

        for name in CONFIG_FILES:
            source = staging / name
            target = data_dir / name
            if source.is_file():
                temporary_target = target.with_name(f".{target.name}.restore")
                shutil.copy2(source, temporary_target)
                if name == "schluessel.icarus":
                    try:
                        temporary_target.chmod(0o600)
                    except OSError:
                        pass
                os.replace(temporary_target, target)
            elif target.is_file():
                target.unlink()

        # Die Datei allein reicht in einem laufenden Prozess nicht. Der Server
        # hält dieselbe Settings-Instanz und baut direkt nach `restore()` Agent,
        # Mail und Kalender neu. Deshalb muss diese Instanz vorher auf den
        # wiederhergestellten Stand gebracht werden.
        from . import config

        config.reload_registered(data_dir)


# -- Export ----------------------------------------------------------------


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
    "BUNDLE_PREFIX",
    "BackupError",
    "DATABASE_FILES",
    "INSTALLATION_FORMAT",
    "export_model",
    "import_model",
    "list_snapshots",
    "prune",
    "restore",
    "snapshot",
    "snapshot_installation",
]
