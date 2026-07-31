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


# -- Episoden und Aufnahme --------------------------------------------------


@pytest.fixture
def voller_client(tmp_path, monkeypatch) -> TestClient:
    """Ein Sidecar mit eigenen Ablagen und einem freigegebenen Ordner."""
    from icarus_memory.audit import AuditLog
    from icarus_memory.episodes import EpisodeStore
    from icarus_memory.tasks import TaskStore
    from icarus_memory.workspace import WorkspaceStore

    vault = tmp_path / "Vault"
    vault.mkdir()
    (vault / "Notiz.md").write_text("---\ndate: 2026-03-14\n---\nInhalt hier.\n",
                                    encoding="utf-8")
    monkeypatch.setenv("ICARUS_FILE_ROOTS", str(tmp_path))

    store = SelfModelStore(MemoryBackend(), subject_id="test")
    app = create_app(
        store,
        audit=AuditLog(tmp_path / "audit.sqlite3"),
        tasks=TaskStore(tmp_path / "tasks.sqlite3"),
        workspace=WorkspaceStore(tmp_path / "workspace.sqlite3"),
        episodes=EpisodeStore(tmp_path / "episodes.sqlite3"),
    )
    client = TestClient(app)
    client.vault = vault  # type: ignore[attr-defined]
    return client


def test_episode_anlegen_und_lesen(voller_client: TestClient) -> None:
    r = voller_client.post("/episodes", json={
        "title": "Anruf Meier", "body": "Er will bis Freitag Bescheid.",
        "kind": "message", "participants": ["Meier"],
    })
    assert r.status_code == 201
    created = r.json()
    assert created["created"] is True
    assert created["digest"].startswith("sha256:")

    r = voller_client.get(f"/episodes/{created['id']}")
    assert r.json()["participants"] == ["Meier"]


def test_gleicher_inhalt_meldet_sich_als_bekannt(voller_client: TestClient) -> None:
    body = {"title": "Anruf", "body": "Derselbe Wortlaut."}
    erste = voller_client.post("/episodes", json=body).json()
    zweite = voller_client.post("/episodes", json=body).json()

    assert erste["created"] is True
    assert zweite["created"] is False
    assert zweite["id"] == erste["id"]


def test_offene_episoden_werden_gefiltert(voller_client: TestClient) -> None:
    a = voller_client.post("/episodes", json={"title": "A", "body": "eins"}).json()
    voller_client.post("/episodes", json={"title": "B", "body": "zwei"})
    voller_client.post(f"/episodes/{a['id']}/ignore")

    offen = voller_client.get("/episodes?state=new").json()
    assert [e["title"] for e in offen] == ["B"]
    assert voller_client.get("/episodes/counts").json() == {"new": 1, "ignored": 1}


def test_aufnahme_legt_episoden_an(voller_client: TestClient) -> None:
    r = voller_client.post("/ingest", json={
        "path": str(voller_client.vault), "adapter": "obsidian",
    })
    assert r.status_code == 200
    assert r.json()["recorded"] == 1

    episoden = voller_client.get("/episodes").json()
    assert episoden[0]["title"] == "Notiz"
    assert episoden[0]["state"] == "new"


def test_aufnahme_ausserhalb_der_freigabe_ist_403(voller_client: TestClient) -> None:
    r = voller_client.post("/ingest", json={"path": "/etc", "adapter": "markdown"})
    assert r.status_code == 403


def test_unbekannter_adapter_ist_400(voller_client: TestClient) -> None:
    r = voller_client.post("/ingest", json={
        "path": str(voller_client.vault), "adapter": "evernote",
    })
    assert r.status_code == 400


def test_dashboard_zeigt_wartendes_rohmaterial(voller_client: TestClient) -> None:
    """Ein Chief of Staff, der einen Berg unbearbeiteten Materials verschweigt,
    ist keiner."""
    voller_client.post("/ingest", json={
        "path": str(voller_client.vault), "adapter": "obsidian",
    })
    assert voller_client.get("/dashboard").json()["episodes"]["pending"] == 1


def test_aufnahme_schreibt_nichts_in_den_bestand(voller_client: TestClient) -> None:
    """Die Kernzusicherung der Schichtung."""
    voller_client.post("/ingest", json={
        "path": str(voller_client.vault), "adapter": "obsidian",
    })
    assert voller_client.get("/assertions").json() == []
