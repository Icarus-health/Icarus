"""Gemeinsame Laufzeit der Icarus-Private-Beta.

Die Fachpakete bleiben einzeln testbar. Dieses Modul verbindet sie im
Produktionsprozess, ohne neue Vertrauens- oder Datenpfade zu schaffen:

* der Wissensgraph bleibt eine jederzeit neu aufbaubare Projektion,
* Workflows laufen ausschließlich über ``Agent.invoke`` und liegen in der
  bereits gesicherten Arbeitsdatenbank,
* Modellrouting schreibt nur Metadaten in das bestehende Audit-Log,
* Browserwerkzeuge werden nur aktiviert, wenn ein realer Worker nachweisbar
  konfiguriert ist,
* alle neuen HTTP-Routen verwenden dasselbe Sidecar-Token.
"""

from __future__ import annotations

import os
import secrets
import shutil
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.routing import APIRoute
from starlette.routing import Mount

from .browser_connector import browser_connector
from .durable_workflows import StepState, WorkflowState, WorkflowStore
from .knowledge_graph import KnowledgeGraph
from .knowledge_graph_api import graph_router
from .knowledge_graph_projection import project_all
from .playwright_browser import PlaywrightBrowserSession
from .policy import PolicyError
from .security import file_roots_from_env
from .workflow_api import workflow_router
from .workflow_runtime import WorkflowRunner

TOKEN_ENV = "ICARUS_SIDECAR_TOKEN"
BROWSER_WORKER_ENV = "ICARUS_BROWSER_WORKER"
BROWSER_NODE_ENV = "ICARUS_BROWSER_NODE"

_GRAPH_MUTATIONS = (
    "/assertions",
    "/projects",
    "/tasks",
    "/notes",
    "/episodes",
    "/proposals",
    "/consolidate",
    "/summaries",
)


class ThreadSafeWorkflowStore(WorkflowStore):
    """WorkflowStore für FastAPIs Worker-Threads.

    Die Workflow-Tabellen liegen absichtlich in ``workspace.sqlite3``. Damit
    gehören sie zum bereits versionierten Komplettbackup und bilden keine
    ungesicherte siebte Wahrheit neben den bestehenden Datenbanken.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.db: sqlite3.Connection
        self.reopen()

    def reopen(self) -> None:
        old = getattr(self, "db", None)
        if old is not None:
            try:
                old.close()
            except sqlite3.Error:
                pass
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self._schema()
        self.recover_interrupted()


class LockedStoreProxy:
    """Serialisiert direkte API- und Runner-Zugriffe auf dieselbe Verbindung."""

    def __init__(self, store: ThreadSafeWorkflowStore, lock: threading.RLock) -> None:
        self.raw = store
        self.lock = lock

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self.raw, name)
        if not callable(attribute):
            return attribute

        def guarded(*args: Any, **kwargs: Any) -> Any:
            with self.lock:
                return attribute(*args, **kwargs)

        return guarded


class LockedWorkflowRunner(WorkflowRunner):
    """Hält einen kompletten Zustandsübergang atomar gegenüber API-Zugriffen."""

    def __init__(
        self,
        store: LockedStoreProxy,
        invoke: Callable[[str, dict[str, Any]], dict[str, Any]],
        lock: threading.RLock,
    ) -> None:
        self._runtime_lock = lock
        super().__init__(store, invoke)

    def tick(self, workflow_id: str) -> dict[str, Any]:
        with self._runtime_lock:
            return super().tick(workflow_id)

    def run_ready(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._runtime_lock:
            return super().run_ready(limit)

    def approval_resolved(
        self,
        workflow_id: str,
        step_id: str,
        result: dict[str, Any],
        *,
        granted: bool,
    ) -> dict[str, Any]:
        with self._runtime_lock:
            return super().approval_resolved(
                workflow_id, step_id, result, granted=granted
            )

    def reconcile(
        self,
        workflow_id: str,
        step_id: str,
        *,
        executed: bool,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._runtime_lock:
            return super().reconcile(
                workflow_id,
                step_id,
                executed=executed,
                result=result,
            )

    def cancel(self, workflow_id: str) -> dict[str, Any]:
        with self._runtime_lock:
            return super().cancel(workflow_id)


class PrivateBetaRuntime:
    def __init__(self, app: FastAPI, data_dir: Path) -> None:
        self.app = app
        self.data_dir = data_dir
        self.graph_path = data_dir / "knowledge-graph.sqlite3"
        self.graph_lock = threading.RLock()
        self.graph_dirty = True
        self.graph_last_stats: dict[str, int] = {
            "entities": 0,
            "edges": 0,
            "sources": 0,
            "identity_conflicts": 0,
        }

        self.workflow_lock = threading.RLock()
        self.raw_workflows = ThreadSafeWorkflowStore(data_dir / "workspace.sqlite3")
        self.workflow_store = LockedStoreProxy(
            self.raw_workflows, self.workflow_lock
        )
        self.workflow_runner = LockedWorkflowRunner(
            self.workflow_store,
            app.state.agent.invoke,
            self.workflow_lock,
        )

        self.browser_session: PlaywrightBrowserSession | None = None
        self.browser_connector = None
        self.browser_error: str | None = None
        self._configure_browser()
        self.refresh_agent()

    # -- Agent und Modellrouting -----------------------------------------

    def refresh_agent(self) -> None:
        """Bindet langlebige Laufzeiten an den aktuell neu gebauten Agenten."""
        self.workflow_runner.invoke = self.app.state.agent.invoke
        self._bind_model_audit()
        if self.browser_connector is not None:
            tools = getattr(self.app.state.agent, "_tools", None)
            if isinstance(tools, dict):
                tools.update(self.browser_connector.tools())

    def _bind_model_audit(self) -> None:
        provider = getattr(self.app.state.agent, "provider", None)
        if provider is None or not hasattr(provider, "audit"):
            return

        def sink(event: dict[str, object]) -> None:
            name = str(event.get("event") or "model_route")
            outcome = {
                "model_route_selected": "pending",
                "model_route_completed": "executed",
                "model_route_failed": "failed",
            }.get(name, "executed")
            arguments = {
                key: value
                for key, value in event.items()
                if key not in {"event", "timestamp"}
            }
            self.app.state.audit.record(
                tool="model_router",
                action_class="read",
                level="automatic",
                outcome=outcome,
                arguments=arguments,
                model=str(event.get("model") or event.get("model_id") or "") or None,
                detail=name,
            )

        provider.audit = sink

    # -- Freigaben und Workflows -----------------------------------------

    def resolve_approval(
        self,
        approval_id: str,
        granted: bool,
        confirmation: str | None,
    ) -> dict[str, Any]:
        """Löst den normalen Nutzerweg und den zugeordneten Workflow gemeinsam auf."""
        with self.workflow_lock:
            try:
                target = self.workflow_runner.approval_target(approval_id)
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
            self.workflow_runner.begin_approval_resolution(target)
            try:
                turn = self.app.state.agent.resolve(
                    approval_id, granted, confirmation
                )
            except PolicyError as exc:
                self.workflow_runner.restore_waiting_approval(
                    target, detail=str(exc)
                )
                raise
            except Exception as exc:
                self.workflow_runner.finish_approval_resolution(
                    target,
                    {"reply": "", "approvals": [], "notices": [], "used_tools": []},
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
            self.workflow_runner.finish_approval_resolution(
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
                "Nach der Freigabe fehlt ein eindeutiger Ausführungseintrag im Audit-Log."
            )
        return None, (
            "Nach der Freigabe entstanden mehrere passende Ausführungseinträge; "
            "das Ergebnis muss manuell geklärt werden."
        )

    # -- Browser ----------------------------------------------------------

    def _configure_browser(self) -> None:
        worker_value = os.environ.get(BROWSER_WORKER_ENV, "").strip()
        if not worker_value:
            self.browser_error = (
                "Kein mitgelieferter Browser-Worker konfiguriert. "
                f"{BROWSER_WORKER_ENV} ist leer."
            )
            return
        worker = Path(worker_value).expanduser().resolve()
        node = os.environ.get(BROWSER_NODE_ENV, "node")
        if not worker.is_file():
            self.browser_error = f"Browser-Worker fehlt: {worker}"
            return
        if shutil.which(node) is None:
            self.browser_error = f"Node-Laufzeit fehlt: {node}"
            return

        try:
            session = PlaywrightBrowserSession(worker, node=node, cwd=worker.parent)
            # Der Worker importiert Playwright vor seiner Eingabeschleife. Ein
            # früher Prozessabbruch zeigt deshalb eine unvollständige
            # Installation, nicht erst den ersten Nutzerfehler.
            time.sleep(0.1)
            process = getattr(session, "_process", None)
            if process is not None and process.poll() is not None:
                raise RuntimeError("Browserprozess wurde beim Start beendet")
            roots = file_roots_from_env(os.environ.get("ICARUS_FILE_ROOTS"))
            self.browser_session = session
            self.browser_connector = browser_connector(
                session,
                download_roots=roots,
                upload_roots=roots,
            )
            self.browser_error = None
        except Exception as exc:
            self.browser_error = str(exc)
            try:
                session.close(force=True)  # type: ignore[possibly-undefined]
            except Exception:
                pass

    # -- Wissensgraph -----------------------------------------------------

    def mark_graph_dirty(self) -> None:
        self.graph_dirty = True

    def rebuild_graph(self) -> dict[str, int]:
        with self.graph_lock:
            assertions = [
                item.to_dict() for item in self.app.state.store.export().assertions
            ]
            projects = [
                item.to_dict()
                for item in self.app.state.workspace.projects(
                    include_closed=True, limit=10000
                )
            ]
            tasks = [
                item.to_dict()
                for item in self.app.state.tasks.all_tasks(limit=10000)
            ]
            notes = [
                item.to_dict()
                for item in self.app.state.workspace.notes(limit=10000)
            ]
            episodes = [
                item.to_dict()
                for item in self.app.state.episodes.all_episodes(limit=10000)
            ]
            graph = KnowledgeGraph(self.graph_path)
            try:
                self.graph_last_stats = graph.rebuild(
                    project_all(
                        assertions=assertions,
                        projects=projects,
                        tasks=tasks,
                        notes=notes,
                        episodes=episodes,
                    )
                )
            finally:
                graph.close()
            self.graph_dirty = False
            return dict(self.graph_last_stats)

    def ensure_graph(self) -> None:
        if self.graph_dirty or not self.graph_path.is_file():
            self.rebuild_graph()

    # -- Restore und Lebenszyklus ----------------------------------------

    def before_restore(self) -> None:
        """Schließt die zusätzliche Workspace-Verbindung vor Dateiaustausch."""
        with self.workflow_lock:
            self.raw_workflows.close()

    def after_restore(self) -> None:
        with self.workflow_lock:
            self.raw_workflows.reopen()
            self.workflow_runner.invoke = self.app.state.agent.invoke
        self.mark_graph_dirty()
        self.refresh_agent()

    def status(self) -> dict[str, Any]:
        self.ensure_graph()
        workflows = self.workflow_store.list()
        workflow_states = Counter(str(item["state"]) for item in workflows)
        provider = getattr(self.app.state.agent, "provider", None)
        return {
            "stage": "private_beta",
            "graph": {
                "ready": not self.graph_dirty,
                **self.graph_last_stats,
            },
            "workflows": {
                "total": len(workflows),
                "states": dict(sorted(workflow_states.items())),
            },
            "model_harness": {
                "active": getattr(provider, "name", None) == "router",
                "provider": getattr(provider, "name", None),
                "model": getattr(provider, "model", None),
            },
            "browser": {
                "active": self.browser_connector is not None,
                "detail": self.browser_error,
            },
        }

    def close(self) -> None:
        with self.workflow_lock:
            try:
                self.raw_workflows.close()
            except sqlite3.Error:
                pass
        if self.browser_session is not None:
            self.browser_session.close(force=True)


def _auth_dependency() -> Callable[..., None]:
    expected = os.environ.get(TOKEN_ENV)

    def auth(
        x_icarus_token: Annotated[str | None, Header()] = None,
    ) -> None:
        if expected is None:
            return
        if x_icarus_token is None or not secrets.compare_digest(
            x_icarus_token, expected
        ):
            raise HTTPException(status_code=401, detail="Ungültiges Token")

    return auth


def _move_ui_mount_to_end(app: FastAPI) -> list[Mount]:
    mounts = [
        route
        for route in app.router.routes
        if isinstance(route, Mount) and getattr(route, "name", None) == "ui"
    ]
    if mounts:
        app.router.routes[:] = [
            route for route in app.router.routes if route not in mounts
        ]
    return mounts


def _bind_approval_route(app: FastAPI, runtime: PrivateBetaRuntime) -> None:
    """Ersetzt nur die Ausführung des bestehenden, authentifizierten Endpunkts."""
    for route in app.router.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path != "/approvals/{approval_id}" or "POST" not in route.methods:
            continue

        def resolve(approval_id: str, body: Any) -> dict[str, Any]:
            try:
                return runtime.resolve_approval(
                    approval_id,
                    bool(body.granted),
                    body.confirmation,
                )
            except (PolicyError, ValueError, KeyError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        # FastAPI hat die Authentifizierung und das Body-Schema bereits aus dem
        # ursprünglichen Endpunkt gebaut. Nur die Funktion dahinter wechselt;
        # dadurch entsteht weder eine zweite Route noch ein zweiter Guard.
        route.endpoint = resolve
        route.dependant.call = resolve
        return
    raise RuntimeError("Produktiver Freigabeendpunkt wurde nicht gefunden")


def install_private_beta(app: FastAPI, data_dir: Path) -> PrivateBetaRuntime:
    """Montiert die gemeinsamen Private-Beta-Fähigkeiten am echten Sidecar."""
    runtime = PrivateBetaRuntime(app, data_dir)
    app.state.private_beta = runtime
    _bind_approval_route(app, runtime)
    auth = _auth_dependency()
    guard = [Depends(auth)]

    # StaticFiles auf "/" muss stets zuletzt stehen, sonst fängt es die neuen
    # API-Routen ab. Die vorhandene Mount-Instanz wird nur umgeordnet.
    ui_mounts = _move_ui_mount_to_end(app)

    graph = KnowledgeGraph(runtime.graph_path)
    try:
        app.include_router(
            graph_router(
                graph,
                dependencies=[Depends(auth), Depends(runtime.ensure_graph)],
            )
        )
    finally:
        graph.close()

    app.include_router(
        workflow_router(runtime.workflow_runner, dependencies=guard)
    )

    router = APIRouter(dependencies=guard)

    @router.get("/private-beta/status")
    def private_beta_status() -> dict[str, Any]:
        return runtime.status()

    @router.post("/graph/rebuild")
    def rebuild_graph() -> dict[str, int]:
        return runtime.rebuild_graph()

    @router.post("/workflows/run-ready")
    def run_ready(limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 500:
            raise HTTPException(
                status_code=400, detail="limit muss zwischen 1 und 500 liegen"
            )
        return runtime.workflow_runner.run_ready(limit)

    @router.get("/connectors")
    def connectors() -> list[dict[str, Any]]:
        if runtime.browser_connector is None:
            return []
        return [runtime.browser_connector.manifest.to_dict()]

    app.include_router(router)
    app.router.routes.extend(ui_mounts)

    @app.middleware("http")
    async def private_beta_state(request: Request, call_next: Callable[..., Any]):
        restoring = (
            request.method == "POST"
            and request.url.path == "/backups/restore"
        )
        if restoring:
            runtime.before_restore()
        try:
            response = await call_next(request)
        finally:
            if restoring:
                runtime.after_restore()

        if response.status_code < 400:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and (
                restoring
                or request.url.path.startswith(_GRAPH_MUTATIONS)
            ):
                runtime.mark_graph_dirty()
            if request.method == "PUT" and request.url.path == "/setup":
                runtime.refresh_agent()
        return response

    app.add_event_handler("shutdown", runtime.close)
    return runtime


__all__ = [
    "PrivateBetaRuntime",
    "ThreadSafeWorkflowStore",
    "install_private_beta",
]
