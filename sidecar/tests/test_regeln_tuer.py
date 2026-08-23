"""Die Regeln über die HTTP-Tür — und die Zusagen dahinter."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from icarus_memory import MemoryBackend, SelfModelStore
from icarus_memory.audit import AuditLog
from icarus_memory.episodes import EpisodeStore
from icarus_memory.server import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    app = create_app(
        SelfModelStore(MemoryBackend(), subject_id="t"),
        audit=AuditLog(tmp_path / "audit.sqlite3"),
        episodes=EpisodeStore(tmp_path / "episodes.sqlite3"),
    )
    with TestClient(app) as c:
        yield c
    app.state.scheduler.stop()


def test_anlegen_ansehen_widerrufen(client: TestClient) -> None:
    leer = client.get("/rules").json()
    assert leer["items"] == []

    angelegt = client.post("/rules", json={
        "name": "Aufgaben anlegen brauche ich nicht gemeldet zu bekommen",
        "tool": "aufgabe_anlegen",
        "stufe": "auto",
    })
    assert angelegt.status_code == 201
    regel = angelegt.json()
    assert regel["aktiv"] and regel["blanko"]

    assert len(client.get("/rules").json()["items"]) == 1

    zurueck = client.post(f"/rules/{regel['id']}/revoke").json()
    assert not zurueck["aktiv"]
    # Aus der Liste raus, aber nicht aus der Welt.
    assert client.get("/rules").json()["items"] == []
    assert len(client.get("/rules?alle=true").json()["items"]) == 1


def test_eine_regel_auf_ein_erfundenes_werkzeug_wird_abgelehnt(client: TestClient) -> None:
    """Sonst glaubt jemand, er habe etwas freigegeben, das still nie greift."""
    antwort = client.post("/rules", json={
        "name": "Irgendwas", "tool": "gibt_es_nicht", "stufe": "auto",
    })

    assert antwort.status_code == 400
    assert "Unbekanntes Werkzeug" in antwort.json()["detail"]


def test_verbieten_geht_nicht_ueber_eine_regel(client: TestClient) -> None:
    antwort = client.post("/rules", json={
        "name": "Nie Aufgaben", "tool": "aufgabe_anlegen", "stufe": "deny",
    })

    assert antwort.status_code == 400


def test_eine_dauerfreigabe_steht_im_protokoll(client: TestClient) -> None:
    """Sie ist selbst eine folgenreiche Entscheidung."""
    client.post("/rules", json={
        "name": "Aufgaben ohne Meldung", "tool": "aufgabe_anlegen", "stufe": "auto",
    })

    eintraege = client.get("/audit").json()
    passend = [e for e in eintraege if e["tool"] == "regel_anlegen"]
    assert passend, "Das Anlegen einer Dauerregel fehlt im Protokoll"
    assert "Aufgaben ohne Meldung" in passend[0]["detail"]
