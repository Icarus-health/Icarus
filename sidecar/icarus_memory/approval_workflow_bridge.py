"""Verbindet den normalen Freigabeweg mit dauerhaften Workflows.

Die gespeicherte Approval-ID ist die einzige Zuordnung. Das Werkzeug wird
weiterhin ausschließlich von ``Agent.resolve`` ausgeführt; diese Brücke
übernimmt danach nur das bereits auditierte Ergebnis in den Workflow.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute

from .durable_workflows import StepState
from .policy import PolicyError


class ApprovalWorkflowBridge:
    """Löst Freigabe und zugeordneten Workflow unter derselben Sperre auf."""

    def __init__(self, app: FastAPI, runtime: Any) -> None:
        self.app = app
        self.runtime = runtime

    def resolve(
        self,
        approval_id: str,
        granted: bool,
        confirmation: str | None,
    ) -> dict[str, Any]:
        with self.runtime.workflow_lock:
            try:
                target = self.runtime.workflow_runner.approval_target(approval_id)
            except ValueError as exc:
                raise PolicyError(str(exc)) from exc

            if target is None:
                return self.app.state.agent.resolve(
                    approval_id, granted, confirmation
                ).to_dict()

            if StepState(target["step_state"]) is not StepState.WAITING:
                raise PolicyError(
                    "Diese workflowgebundene Freigabe wurde bereits aufgelöst "
                    "oder muss manuell geklärt werden."
                )

            # Ablauf und Bestätigungsphrase werden geprüft, bevor der Workflow
            # mutiert. Ein Vertipper oder eine abgelaufene Freigabe lässt den
            # sichtbaren Wartezustand deshalb unverändert.
            approval = self.app.state.agent.policy.get(approval_id)
            if approval.confirmation_phrase is not None:
                expected = approval.confirmation_phrase.strip()
                if (confirmation or "").strip() != expected:
                    raise PolicyError(
                        "Bestätigung stimmt nicht überein. Erwartet: "
                        f"{approval.confirmation_phrase!r}"
                    )

            before_seq = self._latest_audit_seq()
            self.runtime.workflow_runner.begin_approval_resolution(target)
            try:
                turn = self.app.state.agent.resolve(
                    approval_id, granted, confirmation
                )
            except PolicyError as exc:
                self.runtime.workflow_runner.restore_waiting_approval(
                    target, detail=str(exc)
                )
                raise
            except Exception as exc:
                self.runtime.workflow_runner.finish_approval_resolution(
                    target,
                    {
                        "reply": "",
                        "approvals": [],
                        "notices": [],
                        "used_tools": [],
                    },
                    granted=granted,
                    outcome=None,
                    detail=f"Fehler während der Freigabeauflösung: {exc}",
                )
                raise

            result = turn.to_dict()
            audit_entry, audit_detail = self._approval_audit_entry(
                before_seq,
                approval.tool,
                approval.arguments,
            )
            outcome = audit_entry["outcome"] if audit_entry is not None else None
            self.runtime.workflow_runner.finish_approval_resolution(
                target,
                result,
                granted=granted,
                outcome=outcome,
                detail=audit_detail,
            )
            return result

    def _latest_audit_seq(self) -> int:
        entries = self.app.state.audit.entries(1)
        return int(entries[0]["seq"]) if entries else 0

    def _approval_audit_entry(
        self,
        after_seq: int,
        tool: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        matches = [
            entry
            for entry in self.app.state.audit.entries(1000)
            if int(entry["seq"]) > after_seq
            and entry["approved_by"] == "user"
            and entry["tool"] == tool
            and entry["arguments"] == arguments
        ]
        if len(matches) == 1:
            entry = matches[0]
            return entry, str(
                entry.get("detail")
                or entry.get("result")
                or f"Audit-Ergebnis: {entry['outcome']}"
            )
        if not matches:
            return None, (
                "Nach der Freigabe fehlt ein eindeutiger Ausführungseintrag "
                "im Audit-Log."
            )
        return None, (
            "Nach der Freigabe entstanden mehrere passende "
            "Ausführungseinträge; das Ergebnis muss manuell geklärt werden."
        )


def install_approval_workflow_bridge(app: FastAPI, runtime: Any) -> None:
    """Ersetzt nur die Funktion hinter dem bestehenden Freigabeendpunkt."""
    bridge = ApprovalWorkflowBridge(app, runtime)
    app.state.approval_workflow_bridge = bridge

    for route in app.router.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path != "/approvals/{approval_id}" or "POST" not in route.methods:
            continue

        def resolve(approval_id: str, body: Any) -> dict[str, Any]:
            try:
                return bridge.resolve(
                    approval_id,
                    bool(body.granted),
                    body.confirmation,
                )
            except (PolicyError, ValueError, KeyError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        # Authentifizierung und Body-Schema sind bereits am ursprünglichen
        # Endpunkt gebaut. Nur die Funktion dahinter wechselt; es entsteht
        # weder eine zweite Route noch ein zweiter Freigabeweg.
        route.endpoint = resolve
        route.dependant.call = resolve
        return

    raise RuntimeError("Produktiver Freigabeendpunkt wurde nicht gefunden")


__all__ = ["ApprovalWorkflowBridge", "install_approval_workflow_bridge"]
