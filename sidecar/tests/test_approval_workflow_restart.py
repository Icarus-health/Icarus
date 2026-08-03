from __future__ import annotations

from fastapi.testclient import TestClient

from icarus_memory.policy import ActionClass
from icarus_memory.runtime import create_app
from icarus_memory.tools import Tool


HEADERS = {"x-icarus-token": "approval-restart-test"}


def _prepare_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ICARUS_SIDECAR_TOKEN", "approval-restart-test")
    monkeypatch.setenv("ICARUS_KEYCHAIN_BACKEND", "none")
    monkeypatch.delenv("ICARUS_BROWSER_WORKER", raising=False)


def _install_tool(client: TestClient, calls: list[str]) -> None:
    def run(to: str) -> str:
        calls.append(to)
        return f"Nachricht an {to} gesendet"

    client.app.state.agent._tools["restart_nachricht_senden"] = Tool(  # noqa: SLF001
        name="restart_nachricht_senden",
        description="Sendet eine ausschließlich lokale Testnachricht.",
        parameters={
            "type": "object",
            "properties": {"to": {"type": "string"}},
            "required": ["to"],
        },
        action_class=ActionClass.OUTWARD,
        run=run,
        dry_run=lambda arguments: f"Testnachricht an {arguments['to']} senden",
    )


def test_restart_restores_pending_approval_and_keeps_one_shot(
    monkeypatch, tmp_path
):
    _prepare_runtime(monkeypatch, tmp_path)
    calls: list[str] = []
    recipient = "restart@example.invalid"

    with TestClient(create_app()) as first:
        _install_tool(first, calls)
        first.post(
            "/workflows",
            headers=HEADERS,
            json={
                "id": "wf-real-restart",
                "name": "Freigabe über Neustart",
                "steps": [
                    {
                        "id": "send",
                        "kind": "invoke",
                        "tool": "restart_nachricht_senden",
                        "arguments": {"to": recipient},
                        "action_class": "outward",
                    }
                ],
            },
        ).raise_for_status()
        waiting = first.post(
            "/workflows/wf-real-restart/tick", headers=HEADERS
        ).json()
        approval_id = waiting["steps"][0]["approval_ids"][0]
        approval = next(
            item
            for item in first.get("/approvals", headers=HEADERS).json()
            if item["id"] == approval_id
        )
        assert calls == []

    with TestClient(create_app()) as second:
        _install_tool(second, calls)
        restored = second.get("/approvals", headers=HEADERS).json()
        assert [item["id"] for item in restored] == [approval_id]

        granted = second.post(
            f"/approvals/{approval_id}",
            headers=HEADERS,
            json={
                "granted": True,
                "confirmation": approval["confirmation_phrase"],
            },
        )
        assert granted.status_code == 200, granted.text
        completed = second.get(
            "/workflows/wf-real-restart", headers=HEADERS
        ).json()
        assert completed["state"] == "completed"
        assert completed["steps"][0]["state"] == "succeeded"
        assert calls == [recipient]

    with TestClient(create_app()) as third:
        _install_tool(third, calls)
        assert third.get("/approvals", headers=HEADERS).json() == []
        replay = third.post(
            f"/approvals/{approval_id}",
            headers=HEADERS,
            json={
                "granted": True,
                "confirmation": approval["confirmation_phrase"],
            },
        )
        assert replay.status_code == 409
        completed = third.get(
            "/workflows/wf-real-restart", headers=HEADERS
        ).json()
        assert completed["state"] == "completed"
        assert calls == [recipient]
