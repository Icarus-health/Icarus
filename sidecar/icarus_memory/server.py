"""Lokale HTTP-Schnittstelle für die Desktop-App.

Bindet ausschließlich an 127.0.0.1. Der Sidecar ist ein Implementierungsdetail
der App, kein Netzwerkdienst — es gibt bewusst keine Option, ihn zu öffnen.

Zusätzlich verlangt jede Anfrage ein Token, das die App beim Start erzeugt und
per Umgebungsvariable übergibt. Ohne das könnte jeder lokale Prozess das
Selbstmodell auslesen; auf einem Einzelplatzrechner ist das der relevante
Angriffsweg.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .agent import Agent
from .audit import AuditLog
from .backends import CogneeBackend
from .backup import BackupError, export_model, import_model, list_snapshots, snapshot
from .connectors import CalendarConfig, CalendarConnector, MailConfig, MailConnector
from .model import Kind, Provenance, RedactionReason, Sensitivity, SourceType
from .policy import Policy, PolicyError
from .providers import from_env as provider_from_env
from .secrets import Keychain, load_into_env
from .security import file_roots_from_env
from .store import ConflictError, SelfModelStore
from .tasks import TaskStore
from .tools import build_registry

TOKEN_ENV = "ICARUS_SIDECAR_TOKEN"
DATA_ENV = "ICARUS_DATA_DIR"
ROOTS_ENV = "ICARUS_FILE_ROOTS"


def _data_dir() -> Path:
    configured = os.environ.get(DATA_ENV)
    if configured:
        return Path(configured)
    # macOS-Konvention; die App überschreibt das ohnehin per Umgebungsvariable.
    return Path.home() / "Library" / "Application Support" / "Icarus"


# -- Anfragemodelle --------------------------------------------------------


class ProvenanceIn(BaseModel):
    source_type: SourceType
    source_ref: str | None = None
    captured_at: datetime | None = None
    extracted_by: str | None = None
    verbatim: str | None = None


class RecordIn(BaseModel):
    statement: str = Field(min_length=1)
    kind: Kind
    provenance: ProvenanceIn
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    valid_from: datetime | None = None
    expires_at: datetime | None = None
    supersedes: list[str] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)
    sensitivity: Sensitivity = Sensitivity.NORMAL
    tags: list[str] = Field(default_factory=list)


class RedactIn(BaseModel):
    reason: RedactionReason = RedactionReason.USER_REQUEST


class ChatIn(BaseModel):
    message: str = Field(min_length=1)


class ResolveIn(BaseModel):
    granted: bool
    confirmation: str | None = None


class ExportIn(BaseModel):
    passphrase: str | None = None


class VerifyIn(BaseModel):
    path: str
    passphrase: str | None = None


def _mail_sink(app: FastAPI):
    """Verbindet das Werkzeug mail_senden mit dem echten Versand.

    Wird erst nach erteilter Freigabe gerufen. Ohne eingerichteten Mailzugang
    schlägt es hörbar fehl, statt Erfolg vorzutäuschen.
    """

    def send(payload: dict) -> str:
        mail = getattr(app.state, "mail", None)
        if mail is None:
            raise RuntimeError(
                "Kein Mailzugang eingerichtet. Die Freigabe war erteilt, "
                "aber es gibt keinen Kanal (ICARUS_SMTP_HOST fehlt)."
            )
        return mail.send(payload["to"], payload["subject"], payload["body"])

    return send


class TaskIn(BaseModel):
    title: str = Field(min_length=1)
    due: datetime | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)


def create_app(
    store: SelfModelStore | None = None,
    agent: Agent | None = None,
    audit: AuditLog | None = None,
    tasks: TaskStore | None = None,
) -> FastAPI:
    app = FastAPI(title="Icarus", version="0.1.0")

    if store is None:
        backend = CogneeBackend(_data_dir() / "self-model.sqlite3")
        store = SelfModelStore(backend, subject_id="local")
        app.state.backend = backend
    app.state.store = store

    if audit is None:
        audit = AuditLog(_data_dir() / "audit.sqlite3")
    app.state.audit = audit

    if tasks is None:
        tasks = TaskStore(_data_dir() / "tasks.sqlite3")
    app.state.tasks = tasks

    if agent is None:
        # Schlüssel aus dem Schlüsselbund holen, bevor Anbieter und Konnektoren
        # gebaut werden — Zugangsdaten sollen nicht aus .env kommen müssen.
        app.state.keychain = Keychain()
        app.state.loaded_secrets = load_into_env(app.state.keychain)

        mail_config = MailConfig.from_env(dict(os.environ))
        cal_config = CalendarConfig.from_env(dict(os.environ))
        app.state.mail = MailConnector(mail_config) if mail_config else None
        app.state.calendar = CalendarConnector(cal_config) if cal_config else None

        agent = Agent(
            store=store,
            policy=Policy(),
            audit=audit,
            tools=build_registry(
                store,
                outward_sink=_mail_sink(app),
                file_roots=file_roots_from_env(os.environ.get(ROOTS_ENV)),
                mail=app.state.mail,
                calendar=app.state.calendar,
                task_store=tasks,
            ),
            provider=provider_from_env(),
        )
    app.state.agent = agent

    expected = os.environ.get(TOKEN_ENV)

    def auth(x_icarus_token: Annotated[str | None, Header()] = None) -> None:
        # Ohne gesetztes Token läuft der Sidecar offen — nur für Tests und
        # lokale Entwicklung. Die App setzt es immer.
        if expected is None:
            return
        if x_icarus_token is None or not secrets.compare_digest(x_icarus_token, expected):
            raise HTTPException(status_code=401, detail="Ungültiges Token")

    guard = [Depends(auth)]

    @app.get("/health")
    def health() -> dict[str, Any]:
        backend = getattr(app.state, "backend", None)
        provider = app.state.agent.provider
        return {
            "status": "ok",
            "semantic_search": not getattr(backend, "degraded", False),
            "detail": getattr(backend, "degraded_reason", None),
            # Ohne Modell bleibt der Gedächtniskern voll nutzbar — die
            # Oberfläche sagt das, statt einen kaputten Chat anzubieten.
            "chat": provider is not None,
            "provider": getattr(provider, "name", None),
            "model": getattr(provider, "model", None),
            # Sicherheitsrelevanter Zustand, damit die Oberfläche ihn zeigen
            # kann, statt dass der Nutzer ihn erraten muss.
            "keychain": getattr(getattr(app.state, "keychain", None), "backend", "none"),
            "file_roots": [str(p) for p in file_roots_from_env(os.environ.get(ROOTS_ENV))],
            "mail": getattr(app.state, "mail", None) is not None,
            "calendar": getattr(app.state, "calendar", None) is not None,
        }

    # -- Assistent ---------------------------------------------------------

    @app.post("/chat", dependencies=guard)
    def chat(body: ChatIn) -> dict[str, Any]:
        return app.state.agent.send(body.message).to_dict()

    @app.get("/context", dependencies=guard)
    def context() -> dict[str, str]:
        """Was das Modell über den Nutzer zu sehen bekommt — wörtlich.

        Der Nutzer soll nachlesen können, was übermittelt wird, statt es
        glauben zu müssen.
        """
        return {"context": app.state.agent.context()}

    @app.post("/chat/reset", dependencies=guard, status_code=204)
    def reset() -> None:
        app.state.agent.reset()

    # -- Freigaben ---------------------------------------------------------

    @app.get("/approvals", dependencies=guard)
    def approvals() -> list[dict[str, Any]]:
        return [a.to_dict() for a in app.state.agent.policy.pending()]

    @app.post("/approvals/{approval_id}", dependencies=guard)
    def resolve(approval_id: str, body: ResolveIn) -> dict[str, Any]:
        try:
            return app.state.agent.resolve(
                approval_id, body.granted, body.confirmation
            ).to_dict()
        except PolicyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # -- Audit -------------------------------------------------------------

    @app.get("/audit", dependencies=guard)
    def audit_entries(limit: int = 50) -> list[dict[str, Any]]:
        return app.state.audit.entries(limit)

    @app.post("/assertions", dependencies=guard, status_code=201)
    def record(body: RecordIn) -> dict[str, Any]:
        try:
            assertion = app.state.store.record(
                statement=body.statement,
                kind=body.kind,
                provenance=Provenance(
                    source_type=body.provenance.source_type,
                    source_ref=body.provenance.source_ref,
                    captured_at=body.provenance.captured_at,
                    extracted_by=body.provenance.extracted_by,
                    verbatim=body.provenance.verbatim,
                ),
                confidence=body.confidence,
                valid_from=body.valid_from,
                expires_at=body.expires_at,
                supersedes=body.supersedes,
                derived_from=body.derived_from,
                sensitivity=body.sensitivity,
                tags=body.tags,
            )
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return assertion.to_dict()

    @app.get("/assertions", dependencies=guard)
    def usable() -> list[dict[str, Any]]:
        return [a.to_dict() for a in app.state.store.usable()]

    @app.get("/recall", dependencies=guard)
    def recall(q: str, limit: int = 10) -> list[dict[str, Any]]:
        return [a.to_dict() for a in app.state.store.recall(q, limit)]

    @app.get("/assertions/{assertion_id}/history", dependencies=guard)
    def history(assertion_id: str) -> list[dict[str, Any]]:
        try:
            return [a.to_dict() for a in app.state.store.history(assertion_id)]
        except ConflictError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/assertions/{assertion_id}/confirm", dependencies=guard)
    def confirm(assertion_id: str) -> dict[str, Any]:
        try:
            return app.state.store.confirm(assertion_id).to_dict()
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/assertions/{assertion_id}/redact", dependencies=guard)
    def redact(assertion_id: str, body: RedactIn) -> list[dict[str, Any]]:
        try:
            affected = app.state.store.redact(assertion_id, reason=body.reason)
        except ConflictError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [a.to_dict() for a in affected]

    @app.get("/export", dependencies=guard)
    def export() -> dict[str, Any]:
        return app.state.store.export().to_dict()

    # -- Dashboard ---------------------------------------------------------

    @app.get("/dashboard", dependencies=guard)
    def dashboard(days: int = 7) -> dict[str, Any]:
        """Alles für die Startseite in einem Aufruf.

        Jeder Bereich ist einzeln fehlertolerant: Ist ein Konnektor nicht
        eingerichtet oder gerade nicht erreichbar, fehlt genau dieser Block mit
        einer Begründung — der Rest der Seite steht trotzdem. Ein Dashboard,
        das komplett ausfällt, weil ein Mailserver hakt, ist nutzlos.
        """
        result: dict[str, Any] = {
            "now": datetime.now().astimezone().isoformat(),
            "tasks": {"items": [], "error": None},
            "calendar": {"items": [], "error": None},
            "mail": {"items": [], "error": None},
            "memory": {"count": 0, "recent": []},
        }

        try:
            offen = app.state.tasks.open_tasks(limit=50)
            result["tasks"]["items"] = [t.to_dict() for t in offen]
            result["tasks"]["overdue"] = sum(1 for t in offen if t.is_overdue())
        except Exception as exc:  # noqa: BLE001 - ein Bereich darf die Seite nicht kippen
            result["tasks"]["error"] = str(exc)

        calendar = getattr(app.state, "calendar", None)
        if calendar is None:
            result["calendar"]["error"] = "Kein Kalender eingerichtet (ICARUS_CALDAV_URL)."
        else:
            try:
                result["calendar"]["items"] = [e.to_dict() for e in calendar.events(days=days)]
            except Exception as exc:  # noqa: BLE001
                result["calendar"]["error"] = str(exc)

        mail = getattr(app.state, "mail", None)
        if mail is None:
            result["mail"]["error"] = "Kein Mailzugang eingerichtet (ICARUS_IMAP_HOST)."
        else:
            try:
                messages = mail.inbox(limit=8)
                result["mail"]["items"] = [m.to_dict() for m in messages]
                result["mail"]["unread"] = sum(1 for m in messages if m.unread)
            except Exception as exc:  # noqa: BLE001
                result["mail"]["error"] = str(exc)

        usable = app.state.store.usable()
        result["memory"]["count"] = len(usable)
        result["memory"]["recent"] = [
            a.to_dict() for a in sorted(usable, key=lambda x: x.recorded_at, reverse=True)[:5]
        ]
        return result

    # -- Aufgaben ----------------------------------------------------------

    @app.get("/tasks", dependencies=guard)
    def list_tasks(all: bool = False) -> list[dict[str, Any]]:
        items = app.state.tasks.all_tasks() if all else app.state.tasks.open_tasks()
        return [t.to_dict() for t in items]

    @app.post("/tasks", dependencies=guard, status_code=201)
    def add_task(body: TaskIn) -> dict[str, Any]:
        task = app.state.tasks.add(
            body.title,
            Provenance(source_type=SourceType.USER_STATED,
                       captured_at=datetime.now().astimezone()),
            due=body.due, notes=body.notes, tags=body.tags,
        )
        return task.to_dict()

    @app.post("/tasks/{task_id}/done", dependencies=guard)
    def complete_task(task_id: str) -> dict[str, Any]:
        try:
            return app.state.tasks.complete(task_id).to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/tasks/{task_id}/drop", dependencies=guard)
    def drop_task(task_id: str) -> dict[str, Any]:
        """Fallenlassen, nicht erledigen.

        Der Unterschied ist für ein System, das Jahre läuft, wichtig: Sonst
        sieht es später aus, als wäre alles geschafft worden.
        """
        try:
            return app.state.tasks.drop(task_id).to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # -- Sicherung ---------------------------------------------------------

    @app.get("/backups", dependencies=guard)
    def backups() -> list[dict[str, Any]]:
        return list_snapshots(_data_dir() / "sicherungen")

    @app.post("/backups", dependencies=guard, status_code=201)
    def create_backup() -> dict[str, Any]:
        try:
            path = snapshot(_data_dir() / "self-model.sqlite3", _data_dir() / "sicherungen")
        except BackupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"path": str(path), "name": path.name}

    @app.post("/export/file", dependencies=guard)
    def export_file(body: ExportIn) -> dict[str, Any]:
        """Schreibt einen Export, optional verschlüsselt.

        Ohne Passphrase entsteht lesbares JSON — das ist gewollt, weil ein
        Format, das nur dieses Programm lesen kann, in zehn Jahren wertlos ist.
        """
        payload = export_model(app.state.store.export().to_dict(), body.passphrase)
        target = _data_dir() / "exporte"
        target.mkdir(parents=True, exist_ok=True)
        suffix = "icarus" if body.passphrase else "json"
        path = target / f"selbstmodell-{datetime.now():%Y%m%dT%H%M%S}.{suffix}"
        path.write_text(payload, encoding="utf-8")
        return {"path": str(path), "encrypted": bool(body.passphrase)}

    @app.post("/export/verify", dependencies=guard)
    def verify_export(body: VerifyIn) -> dict[str, Any]:
        """Prüft, ob ein Export lesbar ist — bevor man sich darauf verlässt.

        Eine Sicherung, die niemand je zurückgelesen hat, ist keine Sicherung.
        """
        try:
            document = import_model(Path(body.path).read_text(encoding="utf-8"), body.passphrase)
        except (BackupError, OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "ok": True,
            "schema_version": document.get("schema_version"),
            "assertions": len(document.get("assertions", [])),
        }

    return app


def main() -> None:  # pragma: no cover
    import uvicorn

    port = int(os.environ.get("ICARUS_SIDECAR_PORT", "8765"))
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    main()
