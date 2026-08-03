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
