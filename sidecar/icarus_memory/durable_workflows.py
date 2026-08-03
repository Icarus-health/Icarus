"""Dauerhafte, policy-gebundene Workflows.

Die Laufzeit führt Werkzeuge ausschließlich über einen übergebenen
``Agent.invoke``-Gateway aus. Sie besitzt keinen Zugriff auf ``Tool.run``.
Damit gelten dieselben Grenzen, Freigaben und Auditereignisse wie im Gespräch.

Vor jedem wirksamen Aufruf wird eine Ausführungsmarke dauerhaft geschrieben.
Stürzt der Prozess in der kritischen Lücke ab, wird die Aktion nach einem
Neustart **nicht** automatisch wiederholt, sondern auf manuelle Klärung gesetzt.
Das ist absichtlich vorsichtiger als ein mögliches doppeltes Senden oder Zahlen.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from uuid import uuid4

from .policy import ActionClass


class WorkflowState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_TIME = "waiting_time"
    WAITING_CONDITION = "waiting_condition"
    WAITING_APPROVAL = "waiting_approval"
    NEEDS_RECONCILIATION = "needs_reconciliation"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class StepKind(str, Enum):
    INVOKE = "invoke"
    WAIT_UNTIL = "wait_until"
    WAIT_CONDITION = "wait_condition"


class StepState(str, Enum):
    PENDING = "pending"
    STARTED = "started"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_RECONCILIATION = "needs_reconciliation"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class WorkflowStep:
    id: str
    kind: StepKind
    tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    action_class: ActionClass = ActionClass.READ
    run_at: str | None = None
    condition: str | None = None
    max_attempts: int = 1
    retry_delay_seconds: int = 30

    def validate(self) -> None:
        if not self.id:
            raise ValueError("Schritt-ID darf nicht leer sein")
        if self.kind is StepKind.INVOKE and not self.tool:
            raise ValueError(f"{self.id}: INVOKE benötigt ein Werkzeug")
        if self.kind is StepKind.WAIT_UNTIL and not self.run_at:
            raise ValueError(f"{self.id}: WAIT_UNTIL benötigt run_at")
        if self.kind is StepKind.WAIT_CONDITION and not self.condition:
            raise ValueError(f"{self.id}: WAIT_CONDITION benötigt condition")
        if self.max_attempts < 1:
            raise ValueError(f"{self.id}: max_attempts muss mindestens 1 sein")


@dataclass(frozen=True)
class WorkflowPlan:
    id: str
    name: str
    steps: tuple[WorkflowStep, ...]
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def validate(self) -> None:
        if not self.id or not self.name:
            raise ValueError("Workflow-ID und Name sind Pflicht")
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Schritt-IDs müssen eindeutig sein")
        for step in self.steps:
            step.validate()


class InvokeGateway(Protocol):
    def __call__(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class ConditionResolver(Protocol):
    def __call__(self, condition: str, context: Mapping[str, Any]) -> bool: ...


class WorkflowStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self._schema()
        self.recover_interrupted()

    def close(self) -> None:
        self.db.close()

    def _schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                state TEXT NOT NULL,
                current_step INTEGER NOT NULL DEFAULT 0,
                context_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS workflow_steps (
                workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                step_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                tool TEXT,
                arguments_json TEXT NOT NULL,
                action_class TEXT NOT NULL,
                run_at TEXT,
                condition_name TEXT,
                max_attempts INTEGER NOT NULL,
                retry_delay_seconds INTEGER NOT NULL,
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                approval_ids_json TEXT NOT NULL DEFAULT '[]',
                result_json TEXT,
                error TEXT,
                started_at TEXT,
                completed_at TEXT,
                invocation_key TEXT NOT NULL,
                PRIMARY KEY(workflow_id, position),
                UNIQUE(workflow_id, step_id),
                UNIQUE(invocation_key)
            );

            CREATE TABLE IF NOT EXISTS workflow_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                step_id TEXT,
                event TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.db.commit()

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, plan: WorkflowPlan, context: Mapping[str, Any] | None = None) -> None:
        plan.validate()
        now = self.now()
        with self.db:
            self.db.execute(
                """
                INSERT INTO workflows(
                    id, name, state, current_step, context_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    plan.id,
                    plan.name,
                    WorkflowState.PENDING.value,
                    json.dumps(dict(context or {}), sort_keys=True),
                    plan.created_at,
                    now,
                ),
            )
            for position, step in enumerate(plan.steps):
                self.db.execute(
                    """
                    INSERT INTO workflow_steps(
                        workflow_id, position, step_id, kind, tool,
                        arguments_json, action_class, run_at, condition_name,
                        max_attempts, retry_delay_seconds, state, invocation_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan.id,
                        position,
                        step.id,
                        step.kind.value,
                        step.tool,
                        json.dumps(step.arguments, sort_keys=True),
                        step.action_class.value,
                        step.run_at,
                        step.condition,
                        step.max_attempts,
                        step.retry_delay_seconds,
                        StepState.PENDING.value,
                        f"wf:{plan.id}:step:{step.id}",
                    ),
                )
        self.event(plan.id, None, "workflow_created", {"steps": len(plan.steps)})

    def event(
        self,
        workflow_id: str,
        step_id: str | None,
        event: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        with self.db:
            self.db.execute(
                """
                INSERT INTO workflow_events(
                    workflow_id, step_id, event, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    step_id,
                    event,
                    json.dumps(dict(detail or {}), sort_keys=True),
                    self.now(),
                ),
            )

    def recover_interrupted(self) -> None:
        """Markiert unklare wirksame Aufrufe, statt sie doppelt zu starten."""
        rows = self.db.execute(
            """
            SELECT workflow_id, position, step_id, action_class
            FROM workflow_steps WHERE state = ?
            """,
            (StepState.STARTED.value,),
        ).fetchall()
        with self.db:
            for row in rows:
                if row["action_class"] == ActionClass.READ.value:
                    self.db.execute(
                        """
                        UPDATE workflow_steps
                        SET state = ?, error = ?
                        WHERE workflow_id = ? AND position = ?
                        """,
                        (
                            StepState.PENDING.value,
                            "Unterbrochener Lesezugriff wird wiederholt.",
                            row["workflow_id"],
                            row["position"],
                        ),
                    )
                else:
                    self.db.execute(
                        """
                        UPDATE workflow_steps
                        SET state = ?, error = ?
                        WHERE workflow_id = ? AND position = ?
                        """,
                        (
                            StepState.NEEDS_RECONCILIATION.value,
                            "Ausführung begann vor dem Neustart; keine automatische Wiederholung.",
                            row["workflow_id"],
                            row["position"],
                        ),
                    )
                    self.db.execute(
                        """
                        UPDATE workflows SET state = ?, error = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            WorkflowState.NEEDS_RECONCILIATION.value,
                            f"Schritt {row['step_id']} muss manuell geklärt werden.",
                            self.now(),
                            row["workflow_id"],
                        ),
                    )
        self.db.commit()

    def workflow(self, workflow_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
        if row is None:
            raise KeyError(workflow_id)
        result = dict(row)
        result["context"] = json.loads(result.pop("context_json"))
        result["steps"] = [self._step_dict(item) for item in self.db.execute(
            "SELECT * FROM workflow_steps WHERE workflow_id = ? ORDER BY position",
            (workflow_id,),
        )]
        return result

    def list(self, states: Sequence[WorkflowState] | None = None) -> list[dict[str, Any]]:
        if states:
            placeholders = ",".join("?" for _ in states)
            rows = self.db.execute(
                f"SELECT id FROM workflows WHERE state IN ({placeholders}) ORDER BY created_at",
                [state.value for state in states],
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT id FROM workflows ORDER BY created_at"
            ).fetchall()
        return [self.workflow(row["id"]) for row in rows]

    def current_step(self, workflow_id: str) -> dict[str, Any] | None:
        workflow = self.db.execute(
            "SELECT current_step FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
        if workflow is None:
            raise KeyError(workflow_id)
        row = self.db.execute(
            """
            SELECT * FROM workflow_steps
            WHERE workflow_id = ? AND position = ?
            """,
            (workflow_id, workflow["current_step"]),
        ).fetchone()
        return self._step_dict(row) if row else None

    def update_workflow(
        self,
        workflow_id: str,
        state: WorkflowState,
        *,
        current_step: int | None = None,
        error: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        fields = ["state = ?", "updated_at = ?", "error = ?"]
        values: list[Any] = [state.value, self.now(), error]
        if current_step is not None:
            fields.append("current_step = ?")
            values.append(current_step)
        if context is not None:
            fields.append("context_json = ?")
            values.append(json.dumps(dict(context), sort_keys=True))
        values.append(workflow_id)
        with self.db:
            self.db.execute(
                f"UPDATE workflows SET {', '.join(fields)} WHERE id = ?", values
            )

    def update_step(
        self,
        workflow_id: str,
        position: int,
        state: StepState,
        **fields: Any,
    ) -> None:
        assignments = ["state = ?"]
        values: list[Any] = [state.value]
        mapping = {
            "attempts": "attempts",
            "next_attempt_at": "next_attempt_at",
            "approval_ids": "approval_ids_json",
            "result": "result_json",
            "error": "error",
            "started_at": "started_at",
            "completed_at": "completed_at",
        }
        for key, column in mapping.items():
            if key not in fields:
                continue
            value = fields[key]
            if key in {"approval_ids", "result"} and value is not None:
                value = json.dumps(value, sort_keys=True)
            assignments.append(f"{column} = ?")
            values.append(value)
        values.extend([workflow_id, position])
        with self.db:
            self.db.execute(
                f"""
                UPDATE workflow_steps SET {', '.join(assignments)}
                WHERE workflow_id = ? AND position = ?
                """,
                values,
            )

    def events(self, workflow_id: str) -> list[dict[str, Any]]:
        return [
            {
                **dict(row),
                "detail": json.loads(row["detail_json"]),
            }
            for row in self.db.execute(
                "SELECT * FROM workflow_events WHERE workflow_id = ? ORDER BY id",
                (workflow_id,),
            )
        ]

    @staticmethod
    def _step_dict(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise ValueError("Schritt fehlt")
        result = dict(row)
        result["arguments"] = json.loads(result.pop("arguments_json"))
        result["approval_ids"] = json.loads(result.pop("approval_ids_json"))
        result["result"] = (
            json.loads(result["result_json"])
            if result.get("result_json")
            else None
        )
        result.pop("result_json", None)
        return result


class WorkflowRunner:
    def __init__(
        self,
        store: WorkflowStore,
        invoke: InvokeGateway,
        *,
        conditions: ConditionResolver | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.invoke = invoke
        self.conditions = conditions or (lambda _name, _context: False)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_agent(
        cls,
        store: WorkflowStore,
        agent: Any,
        **kwargs: Any,
    ) -> "WorkflowRunner":
        """Der explizite Produktionsadapter: ausschließlich ``Agent.invoke``."""
        return cls(store, agent.invoke, **kwargs)

    def tick(self, workflow_id: str) -> dict[str, Any]:
        workflow = self.store.workflow(workflow_id)
        state = WorkflowState(workflow["state"])
        if state in {
            WorkflowState.COMPLETED,
            WorkflowState.CANCELLED,
            WorkflowState.NEEDS_RECONCILIATION,
        }:
            return workflow

        step = self.store.current_step(workflow_id)
        if step is None:
            self.store.update_workflow(workflow_id, WorkflowState.COMPLETED)
            self.store.event(workflow_id, None, "workflow_completed")
            return self.store.workflow(workflow_id)

        position = int(step["position"])
        step_id = str(step["step_id"])
        step_state = StepState(step["state"])
        if step_state in {
            StepState.SUCCEEDED,
            StepState.SKIPPED,
        }:
            self._advance(workflow_id, position)
            return self.tick(workflow_id)
        if step_state is StepState.NEEDS_RECONCILIATION:
            self.store.update_workflow(
                workflow_id,
                WorkflowState.NEEDS_RECONCILIATION,
                error=step.get("error"),
            )
            return self.store.workflow(workflow_id)
        if step_state is StepState.WAITING and step["approval_ids"]:
            self.store.update_workflow(workflow_id, WorkflowState.WAITING_APPROVAL)
            return self.store.workflow(workflow_id)

        kind = StepKind(step["kind"])
        if kind is StepKind.WAIT_UNTIL:
            target = datetime.fromisoformat(step["run_at"])
            if self.clock() < target:
                self.store.update_step(workflow_id, position, StepState.WAITING)
                self.store.update_workflow(workflow_id, WorkflowState.WAITING_TIME)
                return self.store.workflow(workflow_id)
            self._succeed(workflow_id, step, {"reached": step["run_at"]})
            return self.tick(workflow_id)

        if kind is StepKind.WAIT_CONDITION:
            if not self.conditions(str(step["condition_name"]), workflow["context"]):
                self.store.update_step(workflow_id, position, StepState.WAITING)
                self.store.update_workflow(
                    workflow_id, WorkflowState.WAITING_CONDITION
                )
                return self.store.workflow(workflow_id)
            self._succeed(
                workflow_id,
                step,
                {"condition": step["condition_name"], "met": True},
            )
            return self.tick(workflow_id)

        return self._invoke(workflow, step)

    def run_ready(self, limit: int = 100) -> list[dict[str, Any]]:
        states = [
            WorkflowState.PENDING,
            WorkflowState.RUNNING,
            WorkflowState.WAITING_TIME,
            WorkflowState.WAITING_CONDITION,
            WorkflowState.FAILED,
        ]
        results = []
        for workflow in self.store.list(states)[:limit]:
            results.append(self.tick(workflow["id"]))
        return results

    def approval_resolved(
        self,
        workflow_id: str,
        step_id: str,
        result: Mapping[str, Any],
        *,
        granted: bool,
    ) -> dict[str, Any]:
        workflow = self.store.workflow(workflow_id)
        step = next((item for item in workflow["steps"] if item["step_id"] == step_id), None)
        if step is None:
            raise KeyError(step_id)
        if StepState(step["state"]) is not StepState.WAITING:
            raise ValueError("Dieser Schritt wartet nicht auf eine Freigabe")
        if granted:
            self._succeed(workflow_id, step, dict(result))
            self.store.event(
                workflow_id,
                step_id,
                "approval_granted",
                {"approval_ids": step["approval_ids"]},
            )
            return self.tick(workflow_id)
        self.store.update_step(
            workflow_id,
            int(step["position"]),
            StepState.FAILED,
            error="Freigabe abgelehnt",
            completed_at=self.store.now(),
        )
        self.store.update_workflow(
            workflow_id,
            WorkflowState.FAILED,
            error=f"Freigabe für Schritt {step_id} abgelehnt",
        )
        self.store.event(workflow_id, step_id, "approval_rejected")
        return self.store.workflow(workflow_id)

    def reconcile(
        self,
        workflow_id: str,
        step_id: str,
        *,
        executed: bool,
        result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        workflow = self.store.workflow(workflow_id)
        step = next((item for item in workflow["steps"] if item["step_id"] == step_id), None)
        if step is None:
            raise KeyError(step_id)
        if StepState(step["state"]) is not StepState.NEEDS_RECONCILIATION:
            raise ValueError("Schritt benötigt keine Klärung")
        if executed:
            self._succeed(workflow_id, step, dict(result or {"reconciled": True}))
            self.store.event(workflow_id, step_id, "reconciled_as_executed")
            return self.tick(workflow_id)
        self.store.update_step(
            workflow_id,
            int(step["position"]),
            StepState.PENDING,
            error=None,
            started_at=None,
        )
        self.store.update_workflow(workflow_id, WorkflowState.RUNNING, error=None)
        self.store.event(workflow_id, step_id, "reconciled_as_not_executed")
        return self.tick(workflow_id)

    def cancel(self, workflow_id: str) -> dict[str, Any]:
        self.store.update_workflow(workflow_id, WorkflowState.CANCELLED)
        self.store.event(workflow_id, None, "workflow_cancelled")
        return self.store.workflow(workflow_id)

    def _invoke(self, workflow: Mapping[str, Any], step: Mapping[str, Any]) -> dict[str, Any]:
        workflow_id = str(workflow["id"])
        position = int(step["position"])
        attempts = int(step["attempts"]) + 1
        now = self.clock()
        next_attempt_at = step.get("next_attempt_at")
        if next_attempt_at and now < datetime.fromisoformat(next_attempt_at):
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

        arguments = dict(step["arguments"])
        # Werkzeuge dürfen den Schlüssel ignorieren. Er erscheint im Audit und
        # ermöglicht idempotente Connectoren, ohne ihr Schema zu brechen.
        arguments.setdefault("_icarus_idempotency_key", step["invocation_key"])
        try:
            result = self.invoke(str(step["tool"]), arguments)
        except Exception as exc:
            return self._failed_invocation(workflow_id, step, attempts, str(exc))

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
        return self._failed_invocation(
            workflow_id,
            step,
            attempts,
            str(result.get("text") or "Werkzeugaufruf fehlgeschlagen"),
        )

    def _failed_invocation(
        self,
        workflow_id: str,
        step: Mapping[str, Any],
        attempts: int,
        error: str,
    ) -> dict[str, Any]:
        position = int(step["position"])
        step_id = str(step["step_id"])
        if attempts < int(step["max_attempts"]):
            retry_at = self.clock() + timedelta(
                seconds=int(step["retry_delay_seconds"])
            )
            self.store.update_step(
                workflow_id,
                position,
                StepState.PENDING,
                attempts=attempts,
                next_attempt_at=retry_at.isoformat(),
                error=error,
            )
            self.store.update_workflow(
                workflow_id, WorkflowState.FAILED, error=error
            )
            self.store.event(
                workflow_id,
                step_id,
                "step_retry_scheduled",
                {"attempt": attempts, "retry_at": retry_at.isoformat(), "error": error},
            )
        else:
            self.store.update_step(
                workflow_id,
                position,
                StepState.FAILED,
                attempts=attempts,
                error=error,
                completed_at=self.store.now(),
            )
            self.store.update_workflow(
                workflow_id, WorkflowState.FAILED, error=error
            )
            self.store.event(
                workflow_id,
                step_id,
                "step_failed",
                {"attempts": attempts, "error": error},
            )
        return self.store.workflow(workflow_id)

    def _succeed(
        self,
        workflow_id: str,
        step: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        self.store.update_step(
            workflow_id,
            int(step["position"]),
            StepState.SUCCEEDED,
            result=dict(result),
            completed_at=self.store.now(),
            error=None,
        )
        self.store.event(
            workflow_id,
            str(step["step_id"]),
            "step_succeeded",
            {"result": dict(result)},
        )
        self._advance(workflow_id, int(step["position"]))

    def _advance(self, workflow_id: str, position: int) -> None:
        next_position = position + 1
        exists = self.store.db.execute(
            """
            SELECT 1 FROM workflow_steps
            WHERE workflow_id = ? AND position = ?
            """,
            (workflow_id, next_position),
        ).fetchone()
        if exists:
            self.store.update_workflow(
                workflow_id,
                WorkflowState.RUNNING,
                current_step=next_position,
                error=None,
            )
        else:
            self.store.update_workflow(
                workflow_id,
                WorkflowState.COMPLETED,
                current_step=next_position,
                error=None,
            )
            self.store.event(workflow_id, None, "workflow_completed")


def new_plan(name: str, steps: Iterable[WorkflowStep]) -> WorkflowPlan:
    return WorkflowPlan(id=f"wf-{uuid4().hex[:12]}", name=name, steps=tuple(steps))


__all__ = [
    "StepKind",
    "StepState",
    "WorkflowPlan",
    "WorkflowRunner",
    "WorkflowState",
    "WorkflowStep",
    "WorkflowStore",
    "new_plan",
]
