"""Vollständige Sicherung der lokalen Icarus-Installation."""

from __future__ import annotations

import json
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest

from icarus_memory import backup as backup_module
from icarus_memory import config
from icarus_memory.backup import BackupError, list_snapshots, restore, snapshot


def _database(path: Path, table: str, value: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"CREATE TABLE {table} (value TEXT)")
        connection.execute(f"INSERT INTO {table} VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _value(path: Path, table: str) -> str:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(f"SELECT value FROM {table}").fetchone()[0]
    finally:
        connection.close()


@pytest.fixture
def installation(tmp_path: Path) -> Path:
    _database(tmp_path / "self-model.sqlite3", "assertions", "gedaechtnis")
    _database(tmp_path / "audit.sqlite3", "audit", "protokoll")
    _database(tmp_path / "tasks.sqlite3", "tasks", "aufgabe")
    _database(tmp_path / "workspace.sqlite3", "projects", "projekt")
    _database(tmp_path / "episodes.sqlite3", "episodes", "episode")
    _database(tmp_path / "proposals.sqlite3", "proposals", "vorschlag")
    (tmp_path / "einstellungen.json").write_text(
        json.dumps({"provider": "ollama"}), encoding="utf-8"
    )
    (tmp_path / "schluessel.icarus").write_text(
        "verschluesselter-inhalt", encoding="utf-8"
    )
    return tmp_path


def test_mehrere_datenbanken_erzeugen_installationsbackup(
    installation: Path,
) -> None:
    target = snapshot(
        installation / "self-model.sqlite3",
        installation / "sicherungen",
    )

    assert target.suffix == ".zip"
    entries = list_snapshots(installation / "sicherungen")
    assert entries[0]["kind"] == "installation"
    assert set(entries[0]["members"]) == {
        "self-model.sqlite3",
        "audit.sqlite3",
        "tasks.sqlite3",
        "workspace.sqlite3",
        "episodes.sqlite3",
        "proposals.sqlite3",
        "einstellungen.json",
        "schluessel.icarus",
    }

    with zipfile.ZipFile(target) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["format"] == "icarus-installation-backup-v1"
    assert "nicht enthalten" in manifest["notes"]["os_keychain"]


def test_installationsbackup_stellt_arbeitskontext_wieder_her(
    installation: Path,
) -> None:
    target = snapshot(
        installation / "self-model.sqlite3",
        installation / "sicherungen",
    )

    connection = sqlite3.connect(installation / "tasks.sqlite3")
    connection.execute("UPDATE tasks SET value = 'veraendert'")
    connection.commit()
    connection.close()
    (installation / "einstellungen.json").write_text(
        json.dumps({"provider": "anthropic"}), encoding="utf-8"
    )

    restore(target, installation / "self-model.sqlite3")

    assert _value(installation / "tasks.sqlite3", "tasks") == "aufgabe"
    settings = json.loads(
        (installation / "einstellungen.json").read_text(encoding="utf-8")
    )
    assert settings["provider"] == "ollama"

    assert list(installation.glob("self-model.vor-wiederherstellung-*.sqlite3"))
    recovery = list(installation.glob(".icarus-recovery-*"))
    assert len(recovery) == 1
    assert _value(recovery[0] / "tasks.sqlite3", "tasks") == "veraendert"
    assert (recovery[0] / "einstellungen.json").is_file()


def test_restore_staging_liegt_im_beschreibbaren_datenverzeichnis(
    installation: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Im Container ist das Elternverzeichnis von `/daten` nicht beschreibbar.

    Das Restore-Staging muss deshalb innerhalb des Icarus-Datenverzeichnisses
    entstehen. Sonst funktioniert die Sicherung, aber ausgerechnet die
    Wiederherstellung scheitert erst im realen Containerbetrieb.
    """
    target = snapshot(
        installation / "self-model.sqlite3",
        installation / "sicherungen",
    )
    real_temporary_directory = backup_module.tempfile.TemporaryDirectory
    restore_directories: list[Path] = []

    def temporary_directory(*args, **kwargs):
        if str(kwargs.get("prefix", "")).startswith(".icarus-restore-"):
            restore_directories.append(Path(kwargs["dir"]))
        return real_temporary_directory(*args, **kwargs)

    monkeypatch.setattr(
        backup_module.tempfile,
        "TemporaryDirectory",
        temporary_directory,
    )

    restore(target, installation / "self-model.sqlite3")

    assert restore_directories == [installation]


def test_restore_aktualisiert_laufende_einstellungen(
    installation: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Datei, Settings-Objekt und Provider-Umgebung müssen denselben Stand haben."""
    monkeypatch.delenv("ICARUS_PROVIDER", raising=False)
    monkeypatch.delenv("ICARUS_MODEL", raising=False)

    running = config.load(installation)
    config.apply_to_env(running)
    assert running.provider == "ollama"
    assert os.environ["ICARUS_PROVIDER"] == "ollama"

    target = snapshot(
        installation / "self-model.sqlite3",
        installation / "sicherungen",
    )

    running.provider = "anthropic"
    running.model = "claude-test"
    config.save(installation, running)
    config.apply_to_env(running)
    assert os.environ["ICARUS_PROVIDER"] == "anthropic"
    assert os.environ["ICARUS_MODEL"] == "claude-test"

    restore(target, installation / "self-model.sqlite3")

    assert running.provider == "ollama"
    assert running.model == ""
    assert os.environ["ICARUS_PROVIDER"] == "ollama"
    assert "ICARUS_MODEL" not in os.environ


def test_pruefsummenfehler_wird_vor_der_wiederherstellung_erkannt(
    installation: Path,
    tmp_path: Path,
) -> None:
    target = snapshot(
        installation / "self-model.sqlite3",
        installation / "sicherungen",
    )
    broken = tmp_path / "kaputt.zip"

    with zipfile.ZipFile(target) as source, zipfile.ZipFile(
        broken, "w", compression=zipfile.ZIP_DEFLATED
    ) as destination:
        for name in source.namelist():
            content = source.read(name)
            if name == "tasks.sqlite3":
                content += b"manipuliert"
            destination.writestr(name, content)

    before = _value(installation / "tasks.sqlite3", "tasks")
    with pytest.raises(BackupError, match="Größe|Prüfsumme"):
        restore(broken, installation / "self-model.sqlite3")
    assert _value(installation / "tasks.sqlite3", "tasks") == before
