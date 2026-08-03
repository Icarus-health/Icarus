from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from icarus_memory.policy import (
    ActionClass,
    ApprovalLevel,
    Decision,
    PendingApproval,
)
from icarus_memory.runtime import create_app
from icarus_memory.tools import Tool


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


def _approval_workflow_payload(
    workflow_id: str,
    *,
    tool: str = "workflow_nachricht_senden",
    recipient: str = "workflow@example.invalid",
) -> dict[str, object]:
    return {
        "id": workflow_id,
        "name": "Freigabegebundene Nachricht",
        "steps": [
            {
                "id": "send",
                "kind": "invoke",
                "tool": tool,
                "arguments": {"to": recipient},
                "action_class": "outward",
            }
        ],
    }


def _prepare_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ICARUS_SIDECAR_TOKEN", "private-beta-test")
    monkeypatch.setenv("ICARUS_KEYCHAIN_BACKEND", "none")
    monkeypatch.delenv("ICARUS_BROWSER_WORKER", raising=False)


def _install_outward_tool(
    client: TestClient,
    calls: list[str],
    *,
    name: str = "workflow_nachricht_senden",
    fail: bool = False,
) -> None:
    def run(to: str) -> str:
        calls.append(to)
        if fail:
            raise RuntimeError("Testausführung fehlgeschlagen")
        return f"Nachricht an {to} gesendet"

    client.app.state.agent._tools[name] = Tool(  # noqa: SLF001 - Integrationsvertrag
        name=name,
        description="Sendet eine lokale Testnachricht.",
        parameters={
            "type": "object",
            "properties": {"to": {"type": "string"}},
            "required": ["to"],
        },
        action_class=ActionClass.OUTWARD,
        run=run,
        dry_run=lambda arguments: f"Testnachricht an {arguments['to']} senden",
    )


def _pending_from_dict(raw: dict[str, object]) -> PendingApproval:
    return PendingApproval(
        id=str(raw["id"]),
        tool=str(raw["tool"]),
        arguments=dict(raw["arguments"]),
        decision=Decision(
            ApprovalLevel(str(raw["level"])),
            ActionClass(str(raw["action_class"])),
            list(raw["reasons"]),
        ),
        dry_run=str(raw["dry_run"]),
        requested_at=datetime.fromisoformat(str(raw["requested_at"])),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        confirmation_phrase=(
            str(raw["confirmation_phrase"])
            if raw["confirmation_phrase"] is not None
            else None
        ),
    )


def _restore_pending(client: TestClient, raw: dict[str, object]) -> None:
    pending = _pending_from_dict(raw)
    client.app.state.agent.policy._pending[pending.id] = pending  # noqa: SLF001


def test_private_beta_mounts_graph_workflows_and_ui(monkeypatch, tmp_path):
    _prepare_runtime(monkeypatch, tmp_path)

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
    _prepare_runtime(monkeypatch, tmp_path)

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
    _prepare_runtime(monkeypatch, tmp_path)

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


def test_normal_approval_route_grants_workflow_once(monkeypatch, tmp_path):
    _prepare_runtime(monkeypatch, tmp_path)
    calls: list[str] = []

    with TestClient(create_app()) as client:
        _install_outward_tool(client, calls)
        client.post(
            "/workflows",
            headers=HEADERS,
            json=_approval_workflow_payload("wf-grant"),
        ).raise_for_status()
        waiting = client.post(
            "/workflows/wf-grant/tick", headers=HEADERS
        ).json()
        approval_id = waiting["steps"][0]["approval_ids"][0]
        approval = next(
            item
            for item in client.get("/approvals", headers=HEADERS).json()
            if item["id"] == approval_id
        )

        wrong = client.post(
            f"/approvals/{approval_id}",
            headers=HEADERS,
            json={"granted": True, "confirmation": "falsch"},
        )
        assert wrong.status_code == 409
        unchanged = client.get("/workflows/wf-grant", headers=HEADERS).json()
        assert unchanged["state"] == "waiting_approval"
        assert unchanged["steps"][0]["state"] == "waiting"
        assert calls == []

        granted = client.post(
            f"/approvals/{approval_id}",
            headers=HEADERS,
            json={
                "granted": True,
                "confirmation": approval["confirmation_phrase"],
            },
        )
        assert granted.status_code == 200, granted.text
        completed = client.get("/workflows/wf-grant", headers=HEADERS).json()
        assert completed["state"] == "completed"
        assert completed["steps"][0]["state"] == "succeeded"
        assert calls == ["workflow@example.invalid"]

        duplicate = client.post(
            f"/approvals/{approval_id}",
            headers=HEADERS,
            json={
                "granted": True,
                "confirmation": approval["confirmation_phrase"],
            },
        )
        assert duplicate.status_code == 409
        assert calls == ["workflow@example.invalid"]


def test_expired_approval_does_not_change_workflow(monkeypatch, tmp_path):
    _prepare_runtime(monkeypatch, tmp_path)
    calls: list[str] = []

    with TestClient(create_app()) as client:
        _install_outward_tool(client, calls)
        client.post(
            "/workflows",
            headers=HEADERS,
            json=_approval_workflow_payload("wf-expired"),
        ).raise_for_status()
        waiting = client.post(
            "/workflows/wf-expired/tick", headers=HEADERS
        ).json()
        approval_id = waiting["steps"][0]["approval_ids"][0]
        pending = client.app.state.agent.policy._pending[approval_id]  # noqa: SLF001
        pending.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        expired = client.post(
            f"/approvals/{approval_id}",
            headers=HEADERS,
            json={"granted": True, "confirmation": "workflow@example.invalid"},
        )
        assert expired.status_code == 409
        workflow = client.get("/workflows/wf-expired", headers=HEADERS).json()
        assert workflow["state"] == "waiting_approval"
        assert workflow["steps"][0]["state"] == "waiting"
        assert calls == []


def test_normal_approval_route_rejects_workflow_without_tool(monkeypatch, tmp_path):
    _prepare_runtime(monkeypatch, tmp_path)
    calls: list[str] = []

    with TestClient(create_app()) as client:
        _install_outward_tool(client, calls)
        client.post(
            "/workflows",
            headers=HEADERS,
            json=_approval_workflow_payload("wf-reject"),
        ).raise_for_status()
        waiting = client.post(
            "/workflows/wf-reject/tick", headers=HEADERS
        ).json()
        approval_id = waiting["steps"][0]["approval_ids"][0]

        rejected = client.post(
            f"/approvals/{approval_id}",
            headers=HEADERS,
            json={"granted": False},
        )
        assert rejected.status_code == 200, rejected.text
        failed = client.get("/workflows/wf-reject", headers=HEADERS).json()
        assert failed["state"] == "failed"
        assert failed["steps"][0]["state"] == "failed"
        assert "abgelehnt" in failed["error"]
        assert calls == []


def test_failed_approval_execution_never_completes_workflow(monkeypatch, tmp_path):
    _prepare_runtime(monkeypatch, tmp_path)
    calls: list[str] = []

    with TestClient(create_app()) as client:
        _install_outward_tool(client, calls, fail=True)
        client.post(
            "/workflows",
            headers=HEADERS,
            json=_approval_workflow_payload("wf-failure"),
        ).raise_for_status()
        waiting = client.post(
            "/workflows/wf-failure/tick", headers=HEADERS
        ).json()
        approval_id = waiting["steps"][0]["approval_ids"][0]
        approval = next(
            item
            for item in client.get("/approvals", headers=HEADERS).json()
            if item["id"] == approval_id
        )

        resolved = client.post(
            f"/approvals/{approval_id}",
            headers=HEADERS,
            json={
                "granted": True,
                "confirmation": approval["confirmation_phrase"],
            },
        )
        assert resolved.status_code == 200, resolved.text
        workflow = client.get("/workflows/wf-failure", headers=HEADERS).json()
        assert workflow["state"] == "needs_reconciliation"
        assert workflow["steps"][0]["state"] == "needs_reconciliation"
        assert calls == ["workflow@example.invalid"]


def test_non_workflow_approval_keeps_normal_behaviour(monkeypatch, tmp_path):
    _prepare_runtime(monkeypatch, tmp_path)
    calls: list[str] = []

    with TestClient(create_app()) as client:
        _install_outward_tool(client, calls)
        requested = client.app.state.agent.invoke(
            "workflow_nachricht_senden", {"to": "direct@example.invalid"}
        )
        approval = requested["approvals"][0]

        resolved = client.post(
            f"/approvals/{approval['id']}",
            headers=HEADERS,
            json={
                "granted": True,
                "confirmation": approval["confirmation_phrase"],
            },
        )
        assert resolved.status_code == 200, resolved.text
        assert calls == ["direct@example.invalid"]
        assert client.get("/workflows", headers=HEADERS).json() == []


def test_restart_keeps_approval_mapping_and_blocks_replay(monkeypatch, tmp_path):
    _prepare_runtime(monkeypatch, tmp_path)
    calls: list[str] = []

    with TestClient(create_app()) as first:
        _install_outward_tool(first, calls)
        first.post(
            "/workflows",
            headers=HEADERS,
            json=_approval_workflow_payload("wf-restart"),
        ).raise_for_status()
        waiting = first.post(
            "/workflows/wf-restart/tick", headers=HEADERS
        ).json()
        approval_id = waiting["steps"][0]["approval_ids"][0]
        approval = next(
            item
            for item in first.get("/approvals", headers=HEADERS).json()
            if item["id"] == approval_id
        )

    with TestClient(create_app()) as second:
        _install_outward_tool(second, calls)
        _restore_pending(second, approval)
        resolved = second.post(
            f"/approvals/{approval_id}",
            headers=HEADERS,
            json={
                "granted": True,
                "confirmation": approval["confirmation_phrase"],
            },
        )
        assert resolved.status_code == 200, resolved.text
        workflow = second.get("/workflows/wf-restart", headers=HEADERS).json()
        assert workflow["state"] == "completed"
        assert calls == ["workflow@example.invalid"]

    with TestClient(create_app()) as third:
        _install_outward_tool(third, calls)
        _restore_pending(third, approval)
        replay = third.post(
            f"/approvals/{approval_id}",
            headers=HEADERS,
            json={
                "granted": True,
                "confirmation": approval["confirmation_phrase"],
            },
        )
        assert replay.status_code == 409
        workflow = third.get("/workflows/wf-restart", headers=HEADERS).json()
        assert workflow["state"] == "completed"
        assert calls == ["workflow@example.invalid"]


def test_ambiguous_approval_mapping_fails_closed(tmp_path):
    # Die Datenbank erzwingt keine globale Eindeutigkeit der Approval-ID. Die
    # Laufzeit muss deshalb auch bei absichtlich beschädigten Daten fail-closed
    # bleiben und darf keinen der beiden Schritte auflösen.
    from icarus_memory.durable_workflows import (
        StepKind,
        StepState,
        WorkflowPlan,
        WorkflowState,
        WorkflowStep,
        WorkflowStore,
    )
    from icarus_memory.workflow_runtime import WorkflowRunner

    store = WorkflowStore(tmp_path / "ambiguous.sqlite3")
    for workflow_id in ("wf-one", "wf-two"):
        store.create(
            WorkflowPlan(
                id=workflow_id,
                name=workflow_id,
                steps=(
                    WorkflowStep(
                        id="send",
                        kind=StepKind.INVOKE,
                        tool="send",
                        action_class=ActionClass.OUTWARD,
                    ),
                ),
            )
        )
        store.update_step(
            workflow_id,
            0,
            StepState.WAITING,
            approval_ids=["ap-duplicate"],
        )
        store.update_workflow(
            workflow_id,
            WorkflowState.WAITING_APPROVAL,
        )

    runner = WorkflowRunner(store, lambda _name, _arguments: {"ok": True})
    with pytest.raises(ValueError, match="mehreren Workflows"):
        runner.approval_target("ap-duplicate")
