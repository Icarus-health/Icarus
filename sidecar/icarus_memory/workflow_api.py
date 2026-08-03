"""FastAPI-Router für dauerhafte Workflows.

Der Hauptserver übergibt seine bestehenden Authentifizierungs- und
Wartungsabhängigkeiten. Der Router schafft keinen zweiten, ungeschützten Weg.
"""

from __future__ import annotations

from typing import Any, Sequence

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .durable_workflows import (
    StepKind,
    WorkflowPlan,
    WorkflowState,
    WorkflowStep,
)
from .policy import ActionClass
from .workflow_runtime import WorkflowRunner


class StepIn(BaseModel):
    id: str = Field(min_length=1)
    kind: StepKind
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    action_class: ActionClass = ActionClass.READ
    run_at: str | None = None
    condition: str | None = None
    max_attempts: int = Field(default=1, ge=1, le=20)
    retry_delay_seconds: int = Field(default=30, ge=1, le=86400)


class WorkflowIn(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    steps: list[StepIn] = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class ApprovalResolutionIn(BaseModel):
    step_id: str = Field(min_length=1)
    granted: bool
    result: dict[str, Any] = Field(default_factory=dict)


class ReconcileIn(BaseModel):
    step_id: str = Field(min_length=1)
    executed: bool
    result: dict[str, Any] = Field(default_factory=dict)


def workflow_router(
    runner: WorkflowRunner,
    *,
    dependencies: Sequence[Any] = (),
) -> APIRouter:
    router = APIRouter(
        prefix="/workflows",
        tags=["workflows"],
        dependencies=list(dependencies),
    )

    @router.get("")
    def list_workflows(state: WorkflowState | None = None) -> list[dict[str, Any]]:
        return runner.store.list([state] if state else None)

    @router.post("")
    def create_workflow(body: WorkflowIn) -> dict[str, Any]:
        plan = WorkflowPlan(
            id=body.id,
            name=body.name,
            steps=tuple(
                WorkflowStep(
                    id=step.id,
                    kind=step.kind,
                    tool=step.tool,
                    arguments=step.arguments,
                    action_class=step.action_class,
                    run_at=step.run_at,
                    condition=step.condition,
                    max_attempts=step.max_attempts,
                    retry_delay_seconds=step.retry_delay_seconds,
                )
                for step in body.steps
            ),
        )
        try:
            runner.store.create(plan, body.context)
        except Exception as exc:
            # SQLite-Integritätsfehler wird absichtlich nicht als interner
            # Stacktrace nach außen gegeben.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return runner.store.workflow(plan.id)

    @router.get("/{workflow_id}")
    def get_workflow(workflow_id: str) -> dict[str, Any]:
        try:
            result = runner.store.workflow(workflow_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow nicht gefunden") from exc
        result["events"] = runner.store.events(workflow_id)
        return result

    @router.post("/{workflow_id}/tick")
    def tick(workflow_id: str) -> dict[str, Any]:
        try:
            return runner.tick(workflow_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow nicht gefunden") from exc

    @router.post("/{workflow_id}/cancel")
    def cancel(workflow_id: str) -> dict[str, Any]:
        try:
            return runner.cancel(workflow_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow nicht gefunden") from exc

    @router.post("/{workflow_id}/approval")
    def resolve_approval(
        workflow_id: str, body: ApprovalResolutionIn
    ) -> dict[str, Any]:
        try:
            return runner.approval_resolved(
                workflow_id,
                body.step_id,
                body.result,
                granted=body.granted,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow oder Schritt nicht gefunden") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/{workflow_id}/reconcile")
    def reconcile(workflow_id: str, body: ReconcileIn) -> dict[str, Any]:
        try:
            return runner.reconcile(
                workflow_id,
                body.step_id,
                executed=body.executed,
                result=body.result,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Workflow oder Schritt nicht gefunden") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router


__all__ = ["workflow_router"]
