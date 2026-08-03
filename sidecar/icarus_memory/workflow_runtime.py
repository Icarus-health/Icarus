"""Gehärteter Produktionsrunner für dauerhafte Workflows.

``durable_workflows`` enthält das persistente Datenmodell und die grundlegende
Zustandsmaschine. Dieser Runner ist der einzige empfohlene Ausführungseinstieg:

* endgültig fehlgeschlagene Schritte sind terminal,
* nur reine Lesezugriffe dürfen automatisch wiederholt werden,
* bei einem unklaren Fehler nach Start einer lokalen oder externen Wirkung wird
  niemals geraten, sondern ``needs_reconciliation`` gesetzt,
* interne Idempotenzschlüssel werden nicht als unerwartete Werkzeugparameter
  an Connectoren weitergereicht; sie bleiben im dauerhaften Auditkontext.
"""

from __future__ import annotations

from typing import Any, Mapping

from .durable_workflows import (
    StepState,
    WorkflowRunner as BaseWorkflowRunner,
    WorkflowState,
)
from .policy import ActionClass


class WorkflowRunner(BaseWorkflowRunner):
    """Sicherer Produktionsrunner mit konservativer Fehlerbehandlung."""

    def tick(self, workflow_id: str) -> dict[str, Any]:
        workflow = self.store.workflow(workflow_id)
        step = self.store.current_step(workflow_id)
        if step is not None and StepState(step["state"]) is StepState.FAILED:
            if int(step["attempts"]) >= int(step["max_attempts"]):
                # Endgültig fehlgeschlagen: Der Zustand ist sichtbar und darf
                # nur durch eine explizite Bedienaktion verändert werden.
                return workflow
        return super().tick(workflow_id)

    def approval_target(self, approval_id: str) -> dict[str, Any] | None:
        """Findet den genau zugeordneten Schritt ausschließlich per Approval-ID.

        Auch bereits terminale Schritte werden durchsucht. So kann eine alte,
        nach einem Neustart erneut auftauchende Freigabe nicht als ungebundener
        Antrag durchrutschen und das Werkzeug ein zweites Mal ausführen.
        """
        matches: list[dict[str, Any]] = []
        for workflow in self.store.list():
            for step in workflow["steps"]:
                if approval_id in step["approval_ids"]:
                    matches.append(
                        {
                            "approval_id": approval_id,
                            "workflow_id": workflow["id"],
                            "workflow_state": workflow["state"],
                            "step_id": step["step_id"],
                            "step_state": step["state"],
                        }
                    )
        if len(matches) > 1:
            raise ValueError(
                "Die Freigabe ist mehreren Workflows zugeordnet; nichts wurde ausgeführt."
            )
        return matches[0] if matches else None

    def begin_approval_resolution(self, target: Mapping[str, Any]) -> dict[str, Any]:
        """Schreibt vor der Nutzerentscheidung eine dauerhafte Ausführungsmarke."""
        workflow, step = self._approval_step(target)
        if WorkflowState(workflow["state"]) is not WorkflowState.WAITING_APPROVAL:
            raise ValueError("Der Workflow wartet nicht mehr auf diese Freigabe")
        if StepState(step["state"]) is not StepState.WAITING:
            raise ValueError("Der Schritt wartet nicht mehr auf diese Freigabe")

        self.store.update_step(
            str(workflow["id"]),
            int(step["position"]),
            StepState.STARTED,
            started_at=self.store.now(),
            error=None,
        )
        self.store.update_workflow(
            str(workflow["id"]), WorkflowState.RUNNING, error=None
        )
        self.store.event(
            str(workflow["id"]),
            str(step["step_id"]),
            "approval_resolution_started",
            {"approval_id": target["approval_id"]},
        )
        return self.store.workflow(str(workflow["id"]))

    def restore_waiting_approval(
        self,
        target: Mapping[str, Any],
        *,
        detail: str,
    ) -> dict[str, Any]:
        """Stellt bei einer nicht eingelösten Freigabe den Wartezustand wieder her."""
        workflow, step = self._approval_step(target)
        if StepState(step["state"]) is not StepState.STARTED:
            raise ValueError("Die Freigabeauflösung wurde nicht begonnen")
        self.store.update_step(
            str(workflow["id"]),
            int(step["position"]),
            StepState.WAITING,
            started_at=None,
            error=None,
        )
        self.store.update_workflow(
            str(workflow["id"]), WorkflowState.WAITING_APPROVAL, error=None
        )
        self.store.event(
            str(workflow["id"]),
            str(step["step_id"]),
            "approval_resolution_not_consumed",
            {"approval_id": target["approval_id"], "detail": detail},
        )
        return self.store.workflow(str(workflow["id"]))

    def finish_approval_resolution(
        self,
        target: Mapping[str, Any],
        result: Mapping[str, Any],
        *,
        granted: bool,
        outcome: str | None,
        detail: str,
    ) -> dict[str, Any]:
        """Übernimmt das Agent-Ergebnis, ohne das Werkzeug erneut aufzurufen."""
        workflow, step = self._approval_step(target)
        workflow_id = str(workflow["id"])
        step_id = str(step["step_id"])
        if StepState(step["state"]) is not StepState.STARTED:
            raise ValueError("Die Freigabeauflösung wurde nicht begonnen")

        if not granted and outcome == "refused":
            self.store.update_step(
                workflow_id,
                int(step["position"]),
                StepState.FAILED,
                result=dict(result),
                error="Freigabe abgelehnt",
                completed_at=self.store.now(),
            )
            self.store.update_workflow(
                workflow_id,
                WorkflowState.FAILED,
                error=f"Freigabe für Schritt {step_id} abgelehnt",
            )
            self.store.event(
                workflow_id,
                step_id,
                "approval_rejected",
                {"approval_id": target["approval_id"]},
            )
            return self.store.workflow(workflow_id)

        if granted and outcome == "executed":
            self._succeed(workflow_id, step, dict(result))
            self.store.event(
                workflow_id,
                step_id,
                "approval_granted",
                {"approval_id": target["approval_id"]},
            )
            return self.tick(workflow_id)

        reason = detail or "Das Ausführungsergebnis der Freigabe ist unklar."
        resolved = self._needs_reconciliation(
            workflow_id,
            step,
            int(step["attempts"]),
            reason,
        )
        self.store.event(
            workflow_id,
            step_id,
            "approval_resolution_unclear",
            {
                "approval_id": target["approval_id"],
                "granted": granted,
                "outcome": outcome,
                "detail": reason,
            },
        )
        return resolved

    def _approval_step(
        self, target: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        workflow = self.store.workflow(str(target["workflow_id"]))
        step = next(
            (
                item
                for item in workflow["steps"]
                if item["step_id"] == target["step_id"]
            ),
            None,
        )
        if step is None or target["approval_id"] not in step["approval_ids"]:
            raise KeyError(str(target["approval_id"]))
        return workflow, step

    def _invoke(
        self,
        workflow: Mapping[str, Any],
        step: Mapping[str, Any],
    ) -> dict[str, Any]:
        workflow_id = str(workflow["id"])
        position = int(step["position"])
        attempts = int(step["attempts"]) + 1
        now = self.clock()
        next_attempt_at = step.get("next_attempt_at")
        if next_attempt_at and now < self._parse_time(next_attempt_at):
            self.store.update_workflow(workflow_id, WorkflowState.FAILED)
            return self.store.workflow(workflow_id)

        self.store.update_step(
            workflow_id,
            position,
            StepState.STARTED,
            attempts=attempts,
            started_at=now.isoformat(),
            error=None,
        )
        self.store.update_workflow(workflow_id, WorkflowState.RUNNING)
        self.store.event(
            workflow_id,
            str(step["step_id"]),
            "step_invocation_started",
            {
                "tool": step["tool"],
                "invocation_key": step["invocation_key"],
                "attempt": attempts,
            },
        )

        # Der Invocation-Key ist Teil des dauerhaften Protokolls. Connectoren,
        # die providerseitige Idempotenz unterstützen, können ihn über einen
        # expliziten Adapter erhalten; er wird nicht heimlich in ihr Schema
        # geschoben.
        arguments = dict(step["arguments"])
        try:
            result = self.invoke(str(step["tool"]), arguments)
        except Exception as exc:
            if ActionClass(step["action_class"]) is ActionClass.READ:
                return self._failed_invocation(
                    workflow_id, step, attempts, str(exc)
                )
            return self._needs_reconciliation(
                workflow_id,
                step,
                attempts,
                f"Unklarer Fehler nach Start: {exc}",
            )

        approvals = result.get("approvals") or []
        if approvals:
            approval_ids = [str(item["id"]) for item in approvals]
            self.store.update_step(
                workflow_id,
                position,
                StepState.WAITING,
                approval_ids=approval_ids,
                result=result,
            )
            self.store.update_workflow(
                workflow_id, WorkflowState.WAITING_APPROVAL
            )
            self.store.event(
                workflow_id,
                str(step["step_id"]),
                "approval_requested",
                {"approval_ids": approval_ids},
            )
            return self.store.workflow(workflow_id)

        if result.get("ok", False):
            self._succeed(workflow_id, step, result)
            return self.tick(workflow_id)

        error = str(result.get("text") or "Werkzeugaufruf fehlgeschlagen")
        if ActionClass(step["action_class"]) is ActionClass.READ:
            return self._failed_invocation(workflow_id, step, attempts, error)
        return self._needs_reconciliation(
            workflow_id,
            step,
            attempts,
            "Wirksamer Aufruf lieferte kein eindeutiges Ergebnis: " + error,
        )

    def _needs_reconciliation(
        self,
        workflow_id: str,
        step: Mapping[str, Any],
        attempts: int,
        error: str,
    ) -> dict[str, Any]:
        self.store.update_step(
            workflow_id,
            int(step["position"]),
            StepState.NEEDS_RECONCILIATION,
            attempts=attempts,
            error=error,
        )
        self.store.update_workflow(
            workflow_id,
            WorkflowState.NEEDS_RECONCILIATION,
            error=f"Schritt {step['step_id']} muss manuell geklärt werden.",
        )
        self.store.event(
            workflow_id,
            str(step["step_id"]),
            "step_needs_reconciliation",
            {
                "attempts": attempts,
                "error": error,
                "invocation_key": step["invocation_key"],
            },
        )
        return self.store.workflow(workflow_id)

    @staticmethod
    def _parse_time(value: str):
        from datetime import datetime

        return datetime.fromisoformat(value)


__all__ = ["WorkflowRunner"]
