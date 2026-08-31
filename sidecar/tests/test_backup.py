"""Tests für Sicherung, Wiederherstellung und Schlüsselbund."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from icarus_memory import Kind, Provenance, SelfModelStore, SourceType, SqliteBackend
from icarus_memory.backup import (
    BackupError,
    export_model,
    import_model,
)
from icarus_memory.secrets import KNOWN, Keychain, load_into_env, migrate_env_file


@pytest.fixture
def befuellte_db(tmp_path: Path) -> Path:
    path = tmp_path / "self-model.sqlite3"
    backend = SqliteBackend(path)
    store = SelfModelStore(backend, subject_id="test")
    alt = store.record("Wohnt in Hamburg.", Kind.STATE,
                       Provenance(source_type=SourceType.CHAT, source_ref="chat:1"))
    store.record("Wohnt in Leipzig.", Kind.STATE,
                 Provenance(source_type=SourceType.EMAIL), supersedes=[alt.id])
    backend.close()
    return path


# -- Export ----------------------------------------------------------------


def test_export_ohne_passphrase_ist_lesbar(befuellte_db: Path) -> None:
    store = SelfModelStore(SqliteBackend(befuellte_db), subject_id="test")
    payload = export_model(store.export().to_dict())

    wieder = json.loads(payload)
    assert wieder["schema_version"] == "0.1.0"
    assert len(wieder["assertions"]) == 2


def test_export_mit_passphrase_und_rueckweg(befuellte_db: Path) -> None:
    store = SelfModelStore(SqliteBackend(befuellte_db), subject_id="test")
    original = store.export().to_dict()

    payload = export_model(original, passphrase="ein gutes langes Passwort")
    # Der Klartext darf nirgends durchscheinen.
    assert "Leipzig" not in payload
    assert "Hamburg" not in payload

    zurueck = import_model(payload, passphrase="ein gutes langes Passwort")
    assert zurueck == original


def test_falsche_passphrase_wird_erkannt(befuellte_db: Path) -> None:
    store = SelfModelStore(SqliteBackend(befuellte_db), subject_id="test")
    payload = export_model(store.export().to_dict(), passphrase="richtig")
    with pytest.raises(BackupError, match="Prüfsumme"):
        import_model(payload, passphrase="falsch")


def test_veraenderter_export_wird_erkannt(befuellte_db: Path) -> None:
    """Ohne Authentifizierung liesse sich der Inhalt unbemerkt verändern."""
    store = SelfModelStore(SqliteBackend(befuellte_db), subject_id="test")
    payload = export_model(store.export().to_dict(), passphrase="geheim")

    document = json.loads(payload)
    data = bytearray(__import__("base64").b64decode(document["data"]))
    data[0] ^= 0xFF
    document["data"] = __import__("base64").b64encode(bytes(data)).decode()

    with pytest.raises(BackupError, match="Prüfsumme"):
        import_model(json.dumps(document), passphrase="geheim")


def test_verschluesselter_export_ohne_passphrase(befuellte_db: Path) -> None:
    store = SelfModelStore(SqliteBackend(befuellte_db), subject_id="test")
    payload = export_model(store.export().to_dict(), passphrase="geheim")
    with pytest.raises(BackupError, match="Passphrase erforderlich"):
        import_model(payload)


def test_export_bleibt_schemakonform(befuellte_db: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    store = SelfModelStore(SqliteBackend(befuellte_db), subject_id="test")
    wieder = import_model(export_model(store.export().to_dict(), passphrase="p"), passphrase="p")

    schema_path = Path(__file__).resolve().parents[2] / "schema" / "self-model.schema.json"
    jsonschema.Draft202012Validator(
        json.loads(schema_path.read_text(encoding="utf-8"))
    ).validate(wieder)


# -- Schlüsselbund ---------------------------------------------------------


def test_ohne_speicher_kein_absturz(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auf einem System ohne Schlüsselspeicher muss alles weiterlaufen."""
    monkeypatch.setattr(Keychain, "_detect", staticmethod(lambda: "none"))
    kc = Keychain()
    assert not kc.available
    assert kc.get("OPENAI_API_KEY") is None
    assert load_into_env(kc) == []


def test_umgebung_gewinnt_gegen_schluesselbund(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sonst liesse sich ein hinterlegter Schlüssel nicht übersteuern."""
    class Fake(Keychain):
        def __init__(self) -> None:
            self._service = "test"
            self._backend = "macos"

        def get(self, name: str) -> str | None:
            return "aus-dem-schluesselbund"

    monkeypatch.setenv("OPENAI_API_KEY", "aus-der-umgebung")
    try:
        load_into_env(Fake())
        assert os.environ["OPENAI_API_KEY"] == "aus-der-umgebung"
    finally:
        # `load_into_env` füllt **alle** bekannten Namen, nicht nur den
        # geprüften. Ohne dieses Aufräumen bleiben die übrigen für den Rest des
        # Laufs gesetzt, und eine spätere Testdatei sieht einen eingerichteten
        # Anbieter, den sie nie gesetzt hat — und scheitert an einer Stelle, die
        # nichts damit zu tun hat.
        #
        # Nicht über `monkeypatch.delenv`: Das merkt sich nichts, wenn der Name
        # vorher gar nicht gesetzt war, und stellt danach folglich auch nichts
        # her. Genau der Fall, der hier vorliegt.
        for name in KNOWN:
            if name != "OPENAI_API_KEY":
                os.environ.pop(name, None)


def test_migration_uebernimmt_nur_bekannte_schluessel(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# Kommentar\n"
        'OPENAI_API_KEY="sk-test"\n'
        "IRGENDWAS_ANDERES=egal\n"
        "ANTHROPIC_API_KEY=\n",
        encoding="utf-8",
    )
    gespeichert: dict[str, str] = {}

    class Fake(Keychain):
        def __init__(self) -> None:
            self._service = "test"
            self._backend = "macos"

        def set(self, name: str, value: str) -> None:
            gespeichert[name] = value

    assert migrate_env_file(env, Fake()) == ["OPENAI_API_KEY"]
    assert gespeichert == {"OPENAI_API_KEY": "sk-test"}
    # Die Datei bleibt liegen — ungefragt Dateien des Nutzers zu verändern
    # wäre schlimmer als ein Schlüssel, der einen Tag zu lang dort steht.
    assert env.is_file()
