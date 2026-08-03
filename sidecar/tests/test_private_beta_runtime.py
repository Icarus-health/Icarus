from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from icarus_memory.runtime import create_app


HEADERS = {"x-icarus-token": "private-beta-test"}


def _project_payload(name: str = "Private Beta") -> dict[str, object]:
    return {
        "name": name,
        "description": "Wartet auf eine externe Rückmeldung.",
        "deadline": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
    }


def _workflow_payload(workflow_id: str) -> dict[str, object]:
    return {
        "id": workflow_id,
        "name": "Auf später warten",
        "steps": [
            {
                "id": "wait",
                "kind": "wait_until",
                "run_at": (
                    datetime.now(timezone.utc) + timedelta(days=1)
                ).isoformat(),
                "action_class": "read",
            }
        ],
    }


def test_private_beta_mounts_graph_workflows_and_ui(monkeypatch, tmp_path):
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ICARUS_SIDECAR_TOKEN", "private-beta-test")
    monkeypatch.setenv("ICARUS_KEYCHAIN_BACKEND", "none")
    monkeypatch.delenv("ICARUS_BROWSER_WORKER", raising=False)

    with TestClient(create_app()) as client:
        assert client.get("/graph/stats").status_code == 401
        assert client.get("/workflows").status_code == 401
        assert client.get("/private-beta/status").status_code == 401

        page = client.get("/")
        assert page.status_code == 200
        assert "Icarus" in page.text

        project = client.post(
            "/projects", headers=HEADERS, json=_project_payload()
        ).json()
        client.post(
            "/tasks",
            headers=HEADERS,
            json={
                "title": "Private Beta veröffentlichen",
                "project_id": project["id"],
            },
        ).raise_for_status()
        client.post(
            "/notes",
            headers=HEADERS,
            json={
                "title": "Lokale Daten zuerst",
                "body": "Die Private Beta bleibt local-first.",
                "kind": "decision",
                "project_id": project["id"],
            },
        ).raise_for_status()
        client.post(
            "/episodes",
            headers=HEADERS,
            json={
                "title": "Private-Beta-Review",
                "body": "Sören und Ada prüfen den Stand.",
                "kind": "event",
                "project_id": project["id"],
                "participants": ["Sören", "Ada"],
            },
        ).raise_for_status()
        client.post(
            "/assertions",
            headers=HEADERS,
            json={
                "statement": "Icarus als persönliche Steuerungsebene etablieren",
                "kind": "goal",
                "provenance": {"source_type": "user_stated"},
                "tags": ["person:Sören"],
            },
        ).raise_for_status()

        rebuilt = client.post("/graph/rebuild", headers=HEADERS).json()
        assert rebuilt["entities"] >= 8
        assert rebuilt["edges"] >= 3
        assert client.get("/graph/conflicts", headers=HEADERS).status_code == 200

        workflow = client.post(
            "/workflows",
            headers=HEADERS,
            json=_workflow_payload("wf-persistent"),
        )
        assert workflow.status_code == 200
        ticked = client.post(
            "/workflows/wf-persistent/tick", headers=HEADERS
        ).json()
        assert ticked["state"] == "waiting_time"

        status = client.get("/private-beta/status", headers=HEADERS).json()
        assert status["stage"] == "private_beta"
        assert status["graph"]["ready"] is True
        assert status["workflows"]["total"] == 1
        assert status["browser"]["active"] is False
        assert client.get("/connectors", headers=HEADERS).json() == []


def test_workflows_are_part_of_full_backup_and_restore(monkeypatch, tmp_path):
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ICARUS_SIDECAR_TOKEN", "private-beta-test")
    monkeypatch.setenv("ICARUS_KEYCHAIN_BACKEND", "none")
    monkeypatch.delenv("ICARUS_BROWSER_WORKER", raising=False)

    with TestClient(create_app()) as client:
        client.post(
            "/workflows",
            headers=HEADERS,
            json=_workflow_payload("wf-before-backup"),
        ).raise_for_status()
        backup = client.post("/backups", headers=HEADERS).json()

        client.post(
            "/workflows",
            headers=HEADERS,
            json=_workflow_payload("wf-after-backup"),
        ).raise_for_status()
        assert len(client.get("/workflows", headers=HEADERS).json()) == 2

        restored = client.post(
            "/backups/restore",
            headers=HEADERS,
            json={"name": backup["name"]},
        )
        assert restored.status_code == 200, restored.text

        workflows = client.get("/workflows", headers=HEADERS).json()
        assert [item["id"] for item in workflows] == ["wf-before-backup"]

        status = client.get("/private-beta/status", headers=HEADERS).json()
        assert status["workflows"]["total"] == 1
        assert status["graph"]["ready"] is True


def test_model_routing_metadata_uses_existing_audit_log(monkeypatch, tmp_path):
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ICARUS_SIDECAR_TOKEN", "private-beta-test")
    monkeypatch.setenv("ICARUS_KEYCHAIN_BACKEND", "none")
    monkeypatch.delenv("ICARUS_BROWSER_WORKER", raising=False)

    class RoutedProvider:
        name = "router"
        model = "automatic"
        is_local = True
        audit = None

    with TestClient(create_app()) as client:
        provider = RoutedProvider()
        app = client.app
        app.state.agent._provider = provider  # noqa: SLF001 - Integrationsvertrag
        app.state.private_beta.refresh_agent()
        assert provider.audit is not None

        provider.audit(
            {
                "event": "model_route_completed",
                "model_id": "local-private",
                "model": "test-model",
                "estimated_cost": 0.0,
                "elapsed_ms": 12,
            }
        )
        entry = app.state.audit.entries(1)[0]
        assert entry["tool"] == "model_router"
        assert entry["outcome"] == "executed"
        assert entry["arguments"]["model_id"] == "local-private"
        assert "messages" not in entry["arguments"]
        assert "content" not in entry["arguments"]
