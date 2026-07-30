"""Tests der lokalen HTTP-Schnittstelle."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from icarus_memory import MemoryBackend, SelfModelStore
from icarus_memory.server import create_app


@pytest.fixture
def client() -> TestClient:
    store = SelfModelStore(MemoryBackend(), subject_id="test")
    return TestClient(create_app(store))


def _payload(statement: str, **kw) -> dict:
    body = {
        "statement": statement,
        "kind": "state",
        "provenance": {"source_type": "chat", "source_ref": "chat:1"},
    }
    body.update(kw)
    return body


def test_health_meldet_suchzustand(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_aufnehmen_und_lesen(client: TestClient) -> None:
    r = client.post("/assertions", json=_payload("Wohnt in Hamburg."))
    assert r.status_code == 201
    created = r.json()
    assert created["provenance"]["source_type"] == "chat"

    r = client.get("/assertions")
    assert [a["statement"] for a in r.json()] == ["Wohnt in Hamburg."]


def test_ersetzung_ueber_die_api(client: TestClient) -> None:
    alt = client.post("/assertions", json=_payload("Wohnt in Hamburg.")).json()
    client.post("/assertions", json=_payload("Wohnt in Leipzig.", supersedes=[alt["id"]]))

    aktuell = [a["statement"] for a in client.get("/assertions").json()]
    assert aktuell == ["Wohnt in Leipzig."]

    kette = client.get(f"/assertions/{alt['id']}/history").json()
    assert [a["statement"] for a in kette] == ["Wohnt in Hamburg.", "Wohnt in Leipzig."]
    assert kette[0]["status"] == "superseded"


def test_unbekannte_referenz_gibt_409(client: TestClient) -> None:
    r = client.post("/assertions", json=_payload("X.", supersedes=["a-gibtsnicht"]))
    assert r.status_code == 409


def test_unbekannte_id_gibt_404(client: TestClient) -> None:
    assert client.get("/assertions/a-gibtsnicht/history").status_code == 404


def test_widerruf_kaskadiert_ueber_die_api(client: TestClient) -> None:
    quelle = client.post("/assertions", json=_payload("Quelle.")).json()
    client.post(
        "/assertions",
        json={
            "statement": "Ableitung.",
            "kind": "preference",
            "provenance": {"source_type": "inference"},
            "derived_from": [quelle["id"]],
        },
    )

    r = client.post(f"/assertions/{quelle['id']}/redact", json={"reason": "user_request"})
    assert r.status_code == 200
    assert len(r.json()) == 2
    assert all(a["status"] == "redacted" for a in r.json())
    assert client.get("/assertions").json() == []


def test_export_enthaelt_schema_version(client: TestClient) -> None:
    client.post("/assertions", json=_payload("Etwas."))
    export = client.get("/export").json()
    assert export["schema_version"] == "0.1.0"
    assert len(export["assertions"]) == 1


def test_token_wird_erzwungen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne gültiges Token darf kein lokaler Prozess das Modell lesen."""
    monkeypatch.setenv("ICARUS_SIDECAR_TOKEN", "geheim")
    store = SelfModelStore(MemoryBackend(), subject_id="test")
    c = TestClient(create_app(store))

    assert c.get("/health").status_code == 200  # health bleibt offen
    assert c.get("/assertions").status_code == 401
    assert c.get("/assertions", headers={"x-icarus-token": "falsch"}).status_code == 401
    assert c.get("/assertions", headers={"x-icarus-token": "geheim"}).status_code == 200
