"""Plattformunabhängige Isolation des Schlüsselspeichers in Builds und Tests."""

from __future__ import annotations

from pathlib import Path

from icarus_memory.secrets import BACKEND_ENV, PASSPHRASE_ENV, Keychain


def test_none_schaltet_systemschluesselbund_aus(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(BACKEND_ENV, "none")
    monkeypatch.setenv(PASSPHRASE_ENV, "darf-trotzdem-nicht-greifen")

    keychain = Keychain(data_dir=tmp_path)

    assert keychain.backend == "none"
    assert keychain.available is False
    assert keychain.get("OPENAI_API_KEY") is None


def test_file_braucht_passphrase(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(BACKEND_ENV, "file")
    monkeypatch.delenv(PASSPHRASE_ENV, raising=False)
    assert Keychain(data_dir=tmp_path).backend == "none"

    monkeypatch.setenv(PASSPHRASE_ENV, "isolierte-passphrase")
    keychain = Keychain(data_dir=tmp_path)
    assert keychain.backend == "file"

    keychain.set("OPENAI_API_KEY", "testwert")
    assert keychain.get("OPENAI_API_KEY") == "testwert"
