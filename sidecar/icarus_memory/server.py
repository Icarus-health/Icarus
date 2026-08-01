"""Lokale HTTP-Schnittstelle für die Desktop-App.

Bindet ausschließlich an 127.0.0.1. Der Sidecar ist ein Implementierungsdetail
der App, kein Netzwerkdienst — es gibt bewusst keine Option, ihn zu öffnen.

Zusätzlich verlangt jede Anfrage ein Token, das die App beim Start erzeugt und
per Umgebungsvariable übergibt. Ohne das könnte jeder lokale Prozess das
Selbstmodell auslesen; auf einem Einzelplatzrechner ist das der relevante
Angriffsweg.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Agent
from .audit import AuditLog
from .backends import CogneeBackend
from .backup import BackupError, export_model, import_model, list_snapshots, snapshot
from . import config
from .consolidation import Consolidator
from .proposals import ProposalError, ProposalKind, ProposalStore
from .scheduler import Scheduler, backup_job, consolidation_job, ingest_job
from .connectors import CalendarConfig, CalendarConnector, MailConfig, MailConnector
from .episodes import EpisodeError, EpisodeKind, EpisodeState, EpisodeStore
from .ingest import ADAPTERS, ingest_directory
from .model import Kind, Provenance, RedactionReason, Sensitivity, SourceType
from .policy import Policy, PolicyError
from .providers import from_env as provider_from_env
from .secrets import Keychain, load_into_env
from .security import SecurityError, file_roots_from_env
from .store import ConflictError, SelfModelStore
from .tasks import TaskStore
from .tools import build_registry
from .workspace import (
    NoteKind,
    Priority,
    ProjectStatus,
    WorkspaceError,
    WorkspaceStore,
)

TOKEN_ENV = "ICARUS_SIDECAR_TOKEN"
DATA_ENV = "ICARUS_DATA_DIR"
ROOTS_ENV = "ICARUS_FILE_ROOTS"
UI_ENV = "ICARUS_UI_DIR"

#: Adresse, an die gebunden wird. Standard ist Loopback, und das bleibt so.
#:
#: Im Container muss der Dienst auf 0.0.0.0 hören, sonst greift die
#: Portfreigabe nicht — dort ist „alle Adressen" *innerhalb* des Containers,
#: und was von außen erreichbar ist, entscheidet die Freigabe in `compose.yaml`.
#: Die muss `127.0.0.1:8765:8765` lauten. Steht dort `8765:8765`, hängt das
#: gesamte persönliche Gedächtnis im lokalen Netz.
HOST_ENV = "ICARUS_SIDECAR_HOST"
DEFAULT_HOST = "127.0.0.1"


def _ui_dir() -> Path | None:
    """Wo die Oberfläche liegt, wenn der Sidecar sie selbst ausliefern soll.

    In der Tauri-App liefert die App die Dateien aus und hier ist nichts zu
    tun. Im Container gibt es keine App — dann ist der Sidecar auch der
    Webserver, und man öffnet ihn im Browser.
    """
    configured = os.environ.get(UI_ENV)
    if configured:
        target = Path(configured)
        return target if target.is_dir() else None
    # Arbeitskopie: sidecar/icarus_memory/server.py → ../../app/src
    candidate = Path(__file__).resolve().parents[2] / "app" / "src"
    return candidate if candidate.is_dir() else None


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
    project_id: str | None = None


class ProjectIn(BaseModel):
    name: str = Field(min_length=1)
    area: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    priority: Priority = Priority.MEDIUM
    description: str | None = None
    deadline: datetime | None = None
    tags: list[str] = Field(default_factory=list)


class ProjectPatch(BaseModel):
    status: ProjectStatus | None = None
    priority: Priority | None = None
    description: str | None = None
    deadline: datetime | None = None
    area: str | None = None


class NoteIn(BaseModel):
    title: str = Field(min_length=1)
    body: str = ""
    kind: NoteKind = NoteKind.REFERENCE
    project_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class NotePatch(BaseModel):
    title: str | None = None
    body: str | None = None
    project_id: str | None = None


class EpisodeIn(BaseModel):
    kind: EpisodeKind = EpisodeKind.DOCUMENT
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    occurred_at: datetime | None = None
    project_id: str | None = None
    participants: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class IngestIn(BaseModel):
    path: str = Field(min_length=1)
    adapter: str = "markdown"
    limit: int = Field(default=5000, ge=1, le=100000)


class MailIn(BaseModel):
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    user: str = ""
    sender: str = ""


class CalendarIn(BaseModel):
    url: str = ""
    user: str = ""


class SetupIn(BaseModel):
    """Einstellungen ändern.

    Jedes Feld ist optional und `None` bedeutet **nicht ändern**. Ein leerer
    String bedeutet dagegen **leeren** — sonst ließe sich ein einmal
    eingetragener Mailserver nie wieder loswerden.

    Geheimnisse gehen nur in diese Richtung. Es gibt bewusst kein Feld, das sie
    zurückgibt: Ein solches wäre der bequemste Weg, einen Schlüssel
    versehentlich zu protokollieren.
    """

    provider: str | None = None
    model: str | None = None
    endpoint: str | None = None
    file_roots: list[str] | None = None
    mail: MailIn | None = None
    calendar: CalendarIn | None = None
    onboarded: bool | None = None

    api_key: str | None = None
    mail_password: str | None = None
    calendar_password: str | None = None


class ScheduleIn(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    with_model: bool | None = None
    sources: dict[str, str] | None = None
    backup: bool | None = None


class ConsolidateIn(BaseModel):
    limit: int = Field(default=20, ge=1, le=200)
    with_model: bool = True


class ToolIn(BaseModel):
    """Argumente eines direkten Werkzeugaufrufs.

    Bewusst frei: Jedes Werkzeug bringt sein eigenes Schema mit, und die
    Prüfung gehört dorthin, nicht in eine zweite Beschreibung hier.
    """

    model_config = {"extra": "allow"}


def _build_agent(app: FastAPI) -> Agent:
    """Baut Konnektoren, Werkzeuge und Agent aus der aktuellen Umgebung.

    Als eigene Funktion, weil das **zweimal** gebraucht wird: beim Start und
    nach jeder Änderung an den Einstellungen. Ohne das müsste der Nutzer die
    App neu starten, nachdem er einen Schlüssel eingetragen hat — und genau
    diese Art Reibung ist der Grund, warum Programme nach dem ersten Versuch
    weggelegt werden.

    Der Gesprächsverlauf geht dabei absichtlich verloren. Ein Verlauf, der vor
    einem Anbieterwechsel entstanden ist, gehört einem anderen Modell; ihn
    mitzunehmen hieße, Aussagen weiterzuschleppen, die ein neuer Anbieter nie
    gesehen hat.
    """
    mail_config = MailConfig.from_env(dict(os.environ))
    cal_config = CalendarConfig.from_env(dict(os.environ))
    app.state.mail = MailConnector(mail_config) if mail_config else None
    app.state.calendar = CalendarConnector(cal_config) if cal_config else None

    agent = Agent(
        store=app.state.store,
        policy=Policy(),
        audit=app.state.audit,
        tools=build_registry(
            app.state.store,
            outward_sink=_mail_sink(app),
            file_roots=file_roots_from_env(os.environ.get(ROOTS_ENV)),
            mail=app.state.mail,
            calendar=app.state.calendar,
            task_store=app.state.tasks,
            workspace=app.state.workspace,
            episodes=app.state.episodes,
        ),
        provider=provider_from_env(),
    )
    app.state.agent = agent
    # Der Verdichter hängt am selben Anbieter wie der Agent. Ohne Neubau würde
    # er nach einem Anbieterwechsel weiter das alte Modell fragen.
    app.state.consolidator = Consolidator(
        store=app.state.store,
        episodes=app.state.episodes,
        proposals=app.state.proposals,
        provider=agent.provider,
    )
    _wire_scheduler(app)
    return agent


def _wire_scheduler(app: FastAPI) -> None:
    """Hängt den Zeitplan an die aktuellen Bausteine.

    Muss nach jedem Agentenneubau erneut laufen: Der Verdichter darin ist ein
    neues Objekt, und ein Zeitplan, der auf den alten zeigt, würde weiter das
    abgemeldete Modell fragen.
    """
    settings: config.Settings = app.state.settings
    plan = settings.schedule
    roots = file_roots_from_env(os.environ.get(ROOTS_ENV))

    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is None:
        scheduler = Scheduler()
        app.state.scheduler = scheduler

    scheduler._run_ingest = (  # noqa: SLF001 - bewusst neu verdrahtet
        ingest_job(app.state.episodes, roots, dict(plan.sources))
        if plan.sources else None
    )
    scheduler._run_consolidation = consolidation_job(app.state.consolidator)  # noqa: SLF001
    scheduler._run_backup = backup_job(_data_dir()) if plan.backup else None  # noqa: SLF001
    scheduler.configure(
        enabled=plan.enabled,
        interval_minutes=plan.interval_minutes,
        with_model=plan.with_model,
    )
    if plan.enabled:
        scheduler.start()


def create_app(
    store: SelfModelStore | None = None,
    agent: Agent | None = None,
    audit: AuditLog | None = None,
    tasks: TaskStore | None = None,
    workspace: WorkspaceStore | None = None,
    episodes: EpisodeStore | None = None,
    proposals: ProposalStore | None = None,
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

    if workspace is None:
        workspace = WorkspaceStore(_data_dir() / "workspace.sqlite3")
    app.state.workspace = workspace

    if episodes is None:
        episodes = EpisodeStore(_data_dir() / "episodes.sqlite3")
    app.state.episodes = episodes

    if proposals is None:
        proposals = ProposalStore(_data_dir() / "proposals.sqlite3")
    app.state.proposals = proposals

    if agent is None:
        # Schlüssel aus dem Schlüsselbund holen, bevor Anbieter und Konnektoren
        # gebaut werden — Zugangsdaten sollen nicht aus .env kommen müssen.
        app.state.keychain = Keychain()
        app.state.loaded_secrets = load_into_env(app.state.keychain)

        # Und dann die Einstellungen des Nutzers. Gesetzte Umgebungsvariablen
        # gewinnen, siehe config.apply_to_env.
        app.state.settings = config.load(_data_dir())
        config.apply_to_env(app.state.settings)

        _build_agent(app)
    else:
        app.state.agent = agent
        app.state.settings = getattr(app.state, "settings", config.Settings())

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

    # -- Einrichtung -------------------------------------------------------
    #
    # Damit niemand eine .env anlegen muss, bevor er das Programm zum ersten
    # Mal öffnet. Geheimnisse gehen in den Schlüsselbund und kommen über diese
    # Schnittstelle nie zurück — nur die Auskunft, ob eines hinterlegt ist.

    def _setup_state() -> dict[str, Any]:
        settings: config.Settings = app.state.settings
        keychain = getattr(app.state, "keychain", None) or Keychain()
        provider = app.state.agent.provider
        backend = getattr(app.state, "backend", None)
        return {
            "settings": settings.to_dict(),
            "secrets": config.secret_status(keychain),
            "keychain": keychain.backend,
            # Ohne Schlüsselspeicher gilt ein eingetragener Schlüssel nur für
            # diese Sitzung. Das muss die Oberfläche sagen dürfen.
            "keychain_available": keychain.available,
            "providers": [p for p in config.PROVIDERS],
            "default_models": config.DEFAULT_MODELS,
            "adapters": sorted(ADAPTERS),
            "status": {
                "chat": provider is not None,
                "provider": getattr(provider, "name", None),
                "model": getattr(provider, "model", None),
                "mail": getattr(app.state, "mail", None) is not None,
                "calendar": getattr(app.state, "calendar", None) is not None,
                "file_roots": [
                    str(p) for p in file_roots_from_env(os.environ.get(ROOTS_ENV))
                ],
                "semantic_search": not getattr(backend, "degraded", False),
            },
        }

    @app.get("/setup", dependencies=guard)
    def get_setup() -> dict[str, Any]:
        return _setup_state()

    @app.put("/setup", dependencies=guard)
    def put_setup(body: SetupIn) -> dict[str, Any]:
        """Übernimmt Einstellungen und baut den Agenten neu.

        Nur gesetzte Felder werden angefasst. Ein `null` heißt „nicht ändern",
        ein leerer String heißt „leeren" — sonst könnte man einen einmal
        eingetragenen Mailserver nie wieder loswerden.
        """
        settings: config.Settings = app.state.settings
        keychain = getattr(app.state, "keychain", None) or Keychain()

        if body.provider is not None:
            if body.provider not in config.PROVIDERS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unbekannter Anbieter: {body.provider!r}",
                )
            settings.provider = body.provider
            # Modell mitziehen, wenn der Nutzer keins nennt: Ein Anbieter ohne
            # Modell führt zu einer Fehlermeldung beim ersten Satz, und der
            # Nutzer weiß nicht, warum.
            if body.model is None and body.provider:
                settings.model = config.DEFAULT_MODELS.get(body.provider, "")
        if body.model is not None:
            settings.model = body.model
        if body.endpoint is not None:
            settings.endpoint = body.endpoint
        if body.file_roots is not None:
            settings.file_roots = [p for p in body.file_roots if p.strip()]

        if body.mail is not None:
            settings.mail = config.MailSettings(
                imap_host=body.mail.imap_host,
                imap_port=body.mail.imap_port,
                # Ein Mailkonto ohne SMTP kann lesen und nicht senden. Den
                # IMAP-Host als Vorgabe zu übernehmen wäre geraten und falsch.
                smtp_host=body.mail.smtp_host,
                smtp_port=body.mail.smtp_port,
                user=body.mail.user,
                sender=body.mail.sender,
            )
        if body.calendar is not None:
            settings.calendar = config.CalendarSettings(
                url=body.calendar.url, user=body.calendar.user
            )
        if body.onboarded is not None:
            settings.onboarded = body.onboarded

        # Geheimnisse: leerer String löscht, None lässt unangetastet.
        secret_targets = {
            "api_key": config.secret_name_for_provider(settings.provider),
            "mail_password": config.SECRET_FIELDS["mail_password"],
            "calendar_password": config.SECRET_FIELDS["calendar_password"],
        }
        for feld, name in secret_targets.items():
            value = getattr(body, feld)
            if value is None or name is None:
                continue
            if value:
                config.store_secret(keychain, name, value)
            else:
                config.clear_secret(keychain, name)

        config.save(_data_dir(), settings)

        # Die Umgebung neu setzen. Die aus der Datei stammenden Werte müssen
        # weichen, sonst gewinnt für immer die erste Einstellung.
        for name in (
            "ICARUS_PROVIDER", "ICARUS_MODEL", "ICARUS_BASE_URL", ROOTS_ENV,
            "ICARUS_IMAP_HOST", "ICARUS_IMAP_PORT", "ICARUS_SMTP_HOST",
            "ICARUS_SMTP_PORT", "ICARUS_MAIL_USER", "ICARUS_MAIL_FROM",
            "ICARUS_CALDAV_URL", "ICARUS_CALDAV_USER",
        ):
            os.environ.pop(name, None)
        config.apply_to_env(settings)
        _build_agent(app)

        return _setup_state()

    @app.post("/setup/test/{ziel}", dependencies=guard)
    def test_setup(ziel: str) -> dict[str, Any]:
        """Probiert eine Verbindung wirklich aus, statt sie zu behaupten.

        Ein Einrichtungsassistent, der „gespeichert" sagt und beim ersten
        echten Gebrauch scheitert, ist schlimmer als keiner — dann sucht der
        Nutzer den Fehler an der falschen Stelle.
        """
        try:
            if ziel == "modell":
                provider = app.state.agent.provider
                if provider is None:
                    return {"ok": False, "detail": "Kein Anbieter eingerichtet."}
                reply = provider.complete(
                    [{"role": "user", "content": "Antworte mit dem Wort: bereit"}], []
                )
                return {
                    "ok": True,
                    "detail": f"{provider.name} ({provider.model}) antwortet.",
                    "sample": reply.text[:200],
                }

            if ziel == "mail":
                mail = getattr(app.state, "mail", None)
                if mail is None:
                    return {"ok": False, "detail": "Kein Mailzugang eingerichtet."}
                messages = mail.inbox(limit=1)
                return {"ok": True, "detail": f"Posteingang erreichbar ({len(messages)} gelesen)."}

            if ziel == "kalender":
                calendar = getattr(app.state, "calendar", None)
                if calendar is None:
                    return {"ok": False, "detail": "Kein Kalender eingerichtet."}
                events = calendar.events(days=7)
                return {"ok": True, "detail": f"Kalender erreichbar ({len(events)} Termine)."}
        except Exception as exc:  # noqa: BLE001 - der echte Fehler ist die Antwort
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

        raise HTTPException(status_code=400, detail=f"Unbekanntes Ziel: {ziel}")

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
            "projects": {"items": [], "error": None},
            "tasks": {"items": [], "error": None},
            "calendar": {"items": [], "error": None},
            "mail": {"items": [], "error": None},
            "memory": {"count": 0, "recent": []},
        }

        try:
            result["projects"]["items"] = [
                p.to_dict() for p in app.state.workspace.projects()
            ]
        except Exception as exc:  # noqa: BLE001
            result["projects"]["error"] = str(exc)

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

        # Was roh vorliegt und noch niemand angesehen hat. Ein Chief of Staff,
        # der einen Berg unbearbeiteten Materials verschweigt, ist keiner.
        try:
            counts = app.state.episodes.counts()
            result["episodes"] = {
                "pending": counts.get("new", 0),
                "counts": counts,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            result["episodes"] = {"pending": 0, "counts": {}, "error": str(exc)}

        try:
            offen = app.state.proposals.counts().get("pending", 0)
            result["proposals"] = {"pending": offen, "error": None}
        except Exception as exc:  # noqa: BLE001
            result["proposals"] = {"pending": 0, "error": str(exc)}

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
            project_id=body.project_id,
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

    # -- Projekte ----------------------------------------------------------

    @app.get("/projects", dependencies=guard)
    def list_projects(all: bool = False) -> list[dict[str, Any]]:
        return [p.to_dict() for p in app.state.workspace.projects(include_closed=all)]

    @app.post("/projects", dependencies=guard, status_code=201)
    def add_project(body: ProjectIn) -> dict[str, Any]:
        project = app.state.workspace.add_project(
            body.name,
            Provenance(source_type=SourceType.USER_STATED,
                       captured_at=datetime.now().astimezone()),
            area=body.area, status=body.status, priority=body.priority,
            description=body.description, deadline=body.deadline, tags=body.tags,
        )
        return project.to_dict()

    @app.get("/projects/{project_id}", dependencies=guard)
    def project_detail(project_id: str) -> dict[str, Any]:
        """Projekt samt allem, was daran hängt.

        Ein Aufruf statt drei — die Projektansicht soll nicht aus drei
        Anfragen zusammengesetzt werden, die einzeln scheitern können.
        """
        try:
            project = app.state.workspace.project(project_id)
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            **project.to_dict(),
            "tasks": [t.to_dict() for t in app.state.tasks.by_project(project_id)],
            "notes": [
                n.to_dict() for n in app.state.workspace.notes(project_id=project_id)
            ],
        }

    @app.patch("/projects/{project_id}", dependencies=guard)
    def patch_project(project_id: str, body: ProjectPatch) -> dict[str, Any]:
        try:
            project = app.state.workspace.update_project(
                project_id,
                status=body.status, priority=body.priority,
                description=body.description, deadline=body.deadline, area=body.area,
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return project.to_dict()

    # -- Notizen -----------------------------------------------------------

    @app.get("/notes", dependencies=guard)
    def list_notes(project_id: str | None = None, q: str | None = None) -> list[dict[str, Any]]:
        if q:
            return [n.to_dict() for n in app.state.workspace.search_notes(q)]
        return [n.to_dict() for n in app.state.workspace.notes(project_id=project_id)]

    @app.post("/notes", dependencies=guard, status_code=201)
    def add_note(body: NoteIn) -> dict[str, Any]:
        try:
            note = app.state.workspace.add_note(
                body.title, body.body,
                Provenance(source_type=SourceType.USER_STATED,
                           captured_at=datetime.now().astimezone()),
                kind=body.kind, project_id=body.project_id, tags=body.tags,
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return note.to_dict()

    @app.get("/notes/{note_id}", dependencies=guard)
    def note_detail(note_id: str) -> dict[str, Any]:
        try:
            return app.state.workspace.note(note_id).to_dict()
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/notes/{note_id}", dependencies=guard)
    def patch_note(note_id: str, body: NotePatch) -> dict[str, Any]:
        try:
            note = app.state.workspace.update_note(
                note_id, title=body.title, body=body.body, project_id=body.project_id,
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return note.to_dict()

    # -- Episoden ----------------------------------------------------------
    #
    # Die Mittelfristschicht. Was hier liegt, ist Rohmaterial und behauptet
    # nichts über den Nutzer — deshalb gibt es hier keinen Weg in den Bestand.
    # Den zieht erst die Verdichtung, und sie legt vor.

    @app.get("/episodes", dependencies=guard)
    def list_episodes(
        state: EpisodeState | None = None,
        project_id: str | None = None,
        q: str | None = None,
        days: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        eps = app.state.episodes
        if q:
            items = eps.search(q, limit)
        elif project_id:
            items = eps.by_project(project_id, limit)
        elif days is not None:
            items = eps.recent(days, limit)
        elif state is EpisodeState.NEW:
            items = eps.pending(limit)
        else:
            items = eps.all_episodes(limit)
        if state is not None:
            items = [e for e in items if e.state is state]
        return [e.to_dict() for e in items]

    @app.get("/episodes/counts", dependencies=guard)
    def episode_counts() -> dict[str, int]:
        return app.state.episodes.counts()

    @app.post("/episodes", dependencies=guard, status_code=201)
    def add_episode(body: EpisodeIn) -> dict[str, Any]:
        episode, is_new = app.state.episodes.record(
            kind=body.kind, title=body.title, body=body.body,
            provenance=Provenance(source_type=SourceType.USER_STATED,
                                  captured_at=datetime.now().astimezone()),
            occurred_at=body.occurred_at, project_id=body.project_id,
            participants=body.participants, tags=body.tags,
        )
        # 200 statt 201, wenn der Digest schon bekannt war: Der Aufrufer soll
        # unterscheiden können, ob er etwas erzeugt hat oder nur wiedergefunden.
        return {**episode.to_dict(), "created": is_new}

    @app.get("/episodes/{episode_id}", dependencies=guard)
    def episode_detail(episode_id: str) -> dict[str, Any]:
        try:
            return app.state.episodes.get(episode_id).to_dict()
        except EpisodeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/episodes/{episode_id}/ignore", dependencies=guard)
    def ignore_episode(episode_id: str) -> dict[str, Any]:
        try:
            return app.state.episodes.ignore(episode_id).to_dict()
        except EpisodeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # -- Aufnahme ----------------------------------------------------------

    @app.get("/ingest/adapters", dependencies=guard)
    def adapters() -> dict[str, Any]:
        return {
            "adapters": sorted(ADAPTERS),
            # Ohne freigegebene Ordner geht nichts. Die Oberfläche soll das
            # sagen können, statt den Nutzer in einen Fehler laufen zu lassen.
            "file_roots": [str(p) for p in file_roots_from_env(os.environ.get(ROOTS_ENV))],
        }

    @app.post("/ingest", dependencies=guard)
    def ingest(body: IngestIn) -> dict[str, Any]:
        """Liest einen Ordner ein. Alles landet als Episode, nichts im Bestand."""
        try:
            report = ingest_directory(
                app.state.episodes, body.path, body.adapter,
                roots=file_roots_from_env(os.environ.get(ROOTS_ENV)),
                limit=body.limit,
            )
        except SecurityError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return report.to_dict()

    # -- Verdichtung -------------------------------------------------------
    #
    # Die Regel steht in docs/08-gedaechtnisschichten.md: Verdichtung schlägt
    # vor, sie schreibt nicht. Deshalb gibt es hier keinen Endpunkt, der eine
    # Aussage direkt aus einer Episode erzeugt — der Weg führt immer über einen
    # Vorschlag und dessen Annahme.

    @app.post("/consolidate", dependencies=guard)
    def consolidate(body: ConsolidateIn) -> dict[str, Any]:
        report = app.state.consolidator.run(
            limit=body.limit, with_model=body.with_model
        )
        return {**report.to_dict(), "summary": report.summary()}

    @app.get("/proposals", dependencies=guard)
    def list_proposals(
        kind: ProposalKind | None = None, all: bool = False, limit: int = 100
    ) -> list[dict[str, Any]]:
        items = (
            app.state.proposals.all_proposals(limit)
            if all else app.state.proposals.pending(kind, limit)
        )
        return [p.to_dict() for p in items]

    @app.get("/proposals/counts", dependencies=guard)
    def proposal_counts() -> dict[str, int]:
        return app.state.proposals.counts()

    @app.post("/proposals/{proposal_id}/accept", dependencies=guard)
    def accept_proposal(proposal_id: str) -> dict[str, Any]:
        try:
            assertion = app.state.consolidator.accept(proposal_id)
        except ProposalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "proposal": app.state.proposals.get(proposal_id).to_dict(),
            "assertion": assertion.to_dict() if assertion else None,
        }

    @app.post("/proposals/{proposal_id}/reject", dependencies=guard)
    def reject_proposal(proposal_id: str) -> dict[str, Any]:
        try:
            app.state.consolidator.reject(proposal_id)
        except ProposalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return app.state.proposals.get(proposal_id).to_dict()

    # -- Zeitplan ----------------------------------------------------------
    #
    # Er macht die Vorschlagsschlange voller, nie den Bestand. Das ist die
    # Eigenschaft, die ihn unbedenklich macht: Im schlimmsten Fall entsteht
    # Arbeit, die jemand ignoriert — nie ein falscher Fakt.

    @app.get("/schedule", dependencies=guard)
    def get_schedule() -> dict[str, Any]:
        return {
            **app.state.scheduler.state(),
            "sources": dict(app.state.settings.schedule.sources),
            "backup": app.state.settings.schedule.backup,
        }

    @app.put("/schedule", dependencies=guard)
    def put_schedule(body: ScheduleIn) -> dict[str, Any]:
        plan = app.state.settings.schedule
        if body.enabled is not None:
            plan.enabled = body.enabled
        if body.interval_minutes is not None:
            plan.interval_minutes = body.interval_minutes
        if body.with_model is not None:
            plan.with_model = body.with_model
        if body.sources is not None:
            unbekannt = sorted(set(body.sources.values()) - set(ADAPTERS))
            if unbekannt:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unbekannte Quelle: {', '.join(unbekannt)}",
                )
            plan.sources = dict(body.sources)
        if body.backup is not None:
            plan.backup = body.backup

        config.save(_data_dir(), app.state.settings)
        if not plan.enabled:
            app.state.scheduler.stop()
        _wire_scheduler(app)
        return get_schedule()

    @app.post("/schedule/run", dependencies=guard)
    def run_schedule() -> dict[str, Any]:
        """Einen Durchgang von Hand auslösen — auch wenn der Plan aus ist."""
        report = app.state.scheduler.run_once()
        return {**report.to_dict(), "summary": report.summary()}

    # -- Werkzeuge ---------------------------------------------------------
    #
    # Die Tür für andere Assistenten (siehe mcp.py). Bewusst *nicht* an der
    # Policy vorbei: `agent.invoke()` geht durch dieselbe Prüfung wie das
    # Modell im Haus, und Außenwirksames kommt als Antrag zurück, statt
    # ausgeführt zu werden.

    @app.get("/tools", dependencies=guard)
    def list_tools() -> list[dict[str, Any]]:
        return app.state.agent.tool_schemas()

    @app.post("/tools/{name}", dependencies=guard)
    def call_tool(name: str, body: ToolIn) -> dict[str, Any]:
        return app.state.agent.invoke(name, body.model_dump())

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

    # -- Oberfläche --------------------------------------------------------
    #
    # Ganz zum Schluss, und das ist keine Kosmetik: Ein Mount auf "/" fängt
    # alles ab, was vorher nicht als Route registriert wurde. Stünde er weiter
    # oben, wären alle danach angemeldeten Endpunkte unerreichbar.
    #
    # Nur im Browserbetrieb. Die Tauri-App liefert ihre Dateien selbst aus.

    ui = _ui_dir()
    if ui is not None:
        # Die Dateien selbst sind **nicht** durch das Token geschützt, und das
        # ist Absicht: Sie enthalten kein Nutzerdatum, nur HTML, CSS und
        # JavaScript. Geschützt ist alles darunter, also jeder Datenendpunkt.
        # Wäre auch die Seite geschützt, könnte der Browser sie nie laden, ohne
        # das Token schon zu kennen — und der einzige Weg, es ihm zu geben,
        # wäre, es in die Seite zu schreiben.
        app.mount("/", StaticFiles(directory=str(ui), html=True), name="ui")
        app.state.ui_dir = str(ui)

    return app



def write_connection_file(directory: Path, port: int, token: str | None) -> Path:
    """Hinterlegt Adresse und Token für die MCP-Tür.

    Die App vergibt Port und Token bei jedem Start neu. Ohne diese Datei müsste
    der Nutzer beides von Hand in die Konfiguration seines Assistenten
    eintragen — und nach jedem Neustart erneut.

    Die Datei enthält ein Token und gehört deshalb niemandem sonst: 0600, und
    das Verzeichnis 0700. Auf Windows greifen die Bits nicht; dort schützt die
    Lage im Benutzerprofil.
    """
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    path = directory / "verbindung.json"
    payload = {"url": f"http://127.0.0.1:{port}", "token": token}
    # Erst die Rechte, dann der Inhalt — sonst steht das Token kurzzeitig
    # unter den Standardrechten auf der Platte.
    path.touch(mode=0o600, exist_ok=True)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> None:  # pragma: no cover
    import uvicorn

    # Eine Binary, zwei Rollen. Im gebündelten Zustand liegt nur diese hier im
    # App-Paket; ein zweites PyInstaller-Ziel für die MCP-Tür würde den
    # Interpreter und alle Abhängigkeiten ein zweites Mal mitschleppen, für
    # zweihundert Zeilen Code. Beim Installieren über pip gibt es daneben
    # weiterhin den eigenen Befehl `icarus-mcp`.
    if "--mcp" in sys.argv[1:]:
        from .mcp import main as mcp_main

        mcp_main()
        return

    port = int(os.environ.get("ICARUS_SIDECAR_PORT", "8765"))
    host = os.environ.get(HOST_ENV, DEFAULT_HOST)
    token = os.environ.get(TOKEN_ENV)
    write_connection_file(_data_dir(), port, token)

    if _ui_dir() is not None:
        # Browserbetrieb: Der Nutzer braucht die Adresse samt Token, sonst
        # kommt er nicht hinein. Das Token in die URL zu schreiben ist derselbe
        # Weg, den Jupyter geht, und aus demselben Grund: Der Browser muss es
        # kennen, andere Prozesse auf dem Rechner sollen es nicht. Wer die
        # Zeile sieht, sitzt bereits an der Konsole des Dienstes.
        adresse = f"http://127.0.0.1:{port}/"
        if token:
            adresse += f"?token={token}"
        print(f"\n  Icarus läuft:  {adresse}\n", flush=True)
        if not token:
            print(
                "  WARNUNG: Ohne ICARUS_SIDECAR_TOKEN kann jeder Prozess auf "
                "diesem Rechner das Gedächtnis auslesen.\n",
                flush=True,
            )
    if host != DEFAULT_HOST:
        print(
            f"  Hinweis: Es wird an {host} gebunden statt an {DEFAULT_HOST}. "
            "Im Container ist das richtig — die Portfreigabe muss dann aber "
            "auf 127.0.0.1 beschränkt sein.\n",
            flush=True,
        )

    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    main()
