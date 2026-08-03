"""Regressionsschutz für die sichere Standard-Testumgebung."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from icarus_memory import providers
from icarus_memory.secrets import BACKEND_ENV, Keychain

from .conftest import ISOLATED_ENVIRONMENT


def test_standardtest_verwendet_keinen_betriebssystem_schluesselbund(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Der Test bleibt auch dann aussagekräftig, wenn er nicht auf macOS läuft."""
    calls: list[list[str]] = []

    monkeypatch.setattr("icarus_memory.secrets.platform.system", lambda: "Darwin")
    monkeypatch.setattr(
        "icarus_memory.secrets.shutil.which",
        lambda command: "/usr/bin/security" if command == "security" else None,
    )
    monkeypatch.setattr(
        "icarus_memory.secrets._run",
        lambda command, stdin=None: calls.append(command),
    )

    keychain = Keychain(data_dir=tmp_path)

    assert keychain.backend == "none"
    assert keychain.get("OPENAI_API_KEY") is None
    assert calls == []


def test_standardtest_entfernt_geerbte_verbindungsdaten() -> None:
    expected = set(ISOLATED_ENVIRONMENT) - {BACKEND_ENV, "ICARUS_DATA_DIR"}
    assert all(name not in os.environ for name in expected)
    assert os.environ[BACKEND_ENV] == "none"
    assert Path(os.environ["ICARUS_DATA_DIR"]).name == "icarus-test-data"


def test_provider_transport_scheitert_vor_einer_verbindung() -> None:
    provider = providers.OpenAICompatible(
        "synthetisches-modell",
        api_key="synthetischer-schluessel",
        base_url="http://127.0.0.1:9/v1",
    )

    with pytest.raises(AssertionError, match="Provider-Netzwerk ist in Standardtests gesperrt"):
        provider.complete([{"role": "user", "content": "test"}], [])
