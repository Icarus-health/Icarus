"""Proaktiver Chief of Staff mit begrenztem Aufmerksamkeitsbudget.

Der Motor ist bewusst deterministisch. Er beobachtet nur den verbindlichen
Bestand und vorhandene Connectoren, begründet jeden Hinweis und darf nichts
selbst ausführen. Ein Hinweis ist eine priorisierte Sicht, keine neue Wahrheit.

Persönliche Bedienentscheidungen wie „später“ oder „nicht mehr zeigen“ liegen in
``workspace.sqlite3``. Sie gehören damit zum vollständigen Backup. Jede
Entscheidung bindet sich zusätzlich an einen Fingerabdruck des Hinweises: Ändert
sich der Sachverhalt, darf Icarus erneut aufmerksam machen.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Callable, Iterable

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from starlette.routing import Mount

TOKEN_ENV = "ICARUS_SIDECAR_TOKEN"
_WORDS = re.compile(r"[\wÄÖÜäöüß@.+-]+", re.UNICODE)
_STOPWORDS = {
    "und", "oder", "der", "die", "das", "ein", "eine", "mit", "für",
    "von", "zur", "zum", "im", "in", "am", "an", "auf", "meeting",
    "termin", "call", "besprechung", "the", "and", "with",
}


@dataclass(frozen=True)
class AttentionSignal:
    id: str
    fingerprint: str
    score: int
    kind: str
    title: str
    reason: str
    next_action: str
    target_view: str
    source_id: str | None = None
    due_at: str | None = None
    consequence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _fingerprint(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def _tokens(*values: Any) -> set[str]:
    result: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            result.update(_tokens(*value))
            continue
        for token in _WORDS.findall(str(value).casefold()):
            clean = token.strip(".@+-_")
            if len(clean) >= 3 and clean not in _STOPWORDS:
                result.add(clean)
            if "@" in token:
                local = token.split("@", 1)[0].replace(".", " ")
                result.update(_tokens(local))
    return result


class AttentionPreferences:
    """Kurzlebige SQLite-Verbindungen vermeiden offene Handles beim Restore."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS attention_controls (
        signal_id   TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        action      TEXT NOT NULL,
        until_at    TEXT,
        created_at  TEXT NOT NULL,
        PRIMARY KEY(signal_id, fingerprint)
    );
    CREATE INDEX IF NOT EXISTS attention_controls_until
        ON attention_controls(until_at);
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._prepare()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 10000")
        return db

    def _prepare(self) -> None:
        with self._connect() as db:
            db.executescript(self._SCHEMA)

    def set(
        self,
        signal_id: str,
        fingerprint: str,
        action: str,
        *,
        until_at: datetime | None = None,
    ) -> None:
        if action not in {"dismiss", "snooze"}:
            raise ValueError("Unbekannte Aufmerksamkeitsaktion")
        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO attention_controls(
                    signal_id, fingerprint, action, until_at, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    fingerprint,
                    action,
                    until_at.astimezone(timezone.utc).isoformat() if until_at else None,
                    _now().isoformat(),
                ),
            )

    def visible(self, signal: AttentionSignal, at: datetime) -> bool:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT action, until_at FROM attention_controls
                WHERE signal_id = ? AND fingerprint = ?
                """,
                (signal.id, signal.fingerprint),
            ).fetchone()
            if row is None:
                return True
            if row["action"] == "dismiss":
                return False
            until = _parse(row["until_at"])
            if until and until > at:
                return False
            db.execute(
                "DELETE FROM attention_controls WHERE signal_id = ? AND fingerprint = ?",
                (signal.id, signal.fingerprint),
            )
            return True


class AttentionEngine:
    def __init__(self, app: FastAPI, preferences: AttentionPreferences) -> None:
        self.app = app
        self.preferences = preferences

    def _task_signals(self, at: datetime) -> list[AttentionSignal]:
        result = []
        for task in self.app.state.tasks.open_tasks(limit=10000):
            due = task.due
            if due is None:
                continue
            seconds = (due - at).total_seconds()
            days = int(abs(seconds) // 86400) + (1 if abs(seconds) % 86400 else 0)
            if seconds < 0:
                score = 118 + min(days, 20)
                reason = f"Seit {days} Tag{'en' if days != 1 else ''} überfällig"
                consequence = "Ein unerledigter Termin kann Verpflichtungen oder Abhängigkeiten blockieren."
            elif seconds <= 86400:
                score = 108
                reason = "Innerhalb der nächsten 24 Stunden fällig"
                consequence = "Heute entscheiden: erledigen, delegieren oder neu terminieren."
            elif seconds <= 7 * 86400:
                score = 82 - int(seconds // 86400)
                reason = f"In {max(1, int(seconds // 86400))} Tagen fällig"
                consequence = "Frühzeitig handeln, bevor die Aufgabe dringend wird."
            else:
                continue
            payload = task.to_dict()
            result.append(
                AttentionSignal(
                    id=f"task:{task.id}",
                    fingerprint=_fingerprint(payload),
                    score=score,
                    kind="task",
                    title=task.title,
                    reason=reason,
                    next_action="Aufgabe öffnen und den nächsten konkreten Schritt festlegen.",
                    target_view="dashboard",
                    source_id=task.id,
                    due_at=due.isoformat(),
                    consequence=consequence,
                )
            )
        return result

    def _project_signals(self, at: datetime) -> list[AttentionSignal]:
        result = []
        open_tasks = self.app.state.tasks.open_tasks(limit=10000)
        by_project: dict[str, list[Any]] = {}
        for task in open_tasks:
            if task.project_id:
                by_project.setdefault(task.project_id, []).append(task)
        waiting_pattern = re.compile(
            r"wartet|rückmeldung|antwort ausstehend|abhängig|blockiert|fehlt noch",
            re.IGNORECASE,
        )
        for project in self.app.state.workspace.projects(include_closed=False, limit=10000):
            tasks = by_project.get(project.id, [])
            description = project.description or ""
            deadline = project.deadline
            score = 0
            reasons: list[str] = []
            if deadline:
                remaining = (deadline - at).total_seconds()
                if remaining < 0:
                    score = max(score, 112)
                    reasons.append("Projektfrist ist überschritten")
                elif remaining <= 7 * 86400 and tasks:
                    score = max(score, 94)
                    reasons.append(
                        f"Frist in {max(1, int(remaining // 86400))} Tagen bei {len(tasks)} offenen Aufgaben"
                    )
                elif remaining <= 14 * 86400 and tasks:
                    score = max(score, 76)
                    reasons.append("Frist nähert sich")
            if waiting_pattern.search(description):
                score = max(score, 88 if not tasks else 80)
                reasons.append("Eine Rückmeldung oder Blockade ist im Projekt genannt")
            if not score:
                continue
            payload = {
                "project": project.to_dict(),
                "tasks": [task.to_dict() for task in tasks],
            }
            result.append(
                AttentionSignal(
                    id=f"project:{project.id}",
                    fingerprint=_fingerprint(payload),
                    score=score,
                    kind="project",
                    title=project.name,
                    reason=" · ".join(reasons),
                    next_action=(
                        tasks[0].title
                        if tasks
                        else "Blockade oder wartende Rückmeldung konkretisieren."
                    ),
                    target_view="projects",
                    source_id=project.id,
                    due_at=deadline.isoformat() if deadline else None,
                    consequence="Projektfortschritt, Frist oder externe Abhängigkeit ist gefährdet.",
                )
            )
        return result

    def _workflow_signals(self) -> list[AttentionSignal]:
        runtime = getattr(self.app.state, "private_beta", None)
        if runtime is None:
            return []
        result = []
        priorities = {
            "needs_reconciliation": (130, "Eine möglicherweise wirksame Aktion muss geklärt werden"),
            "waiting_approval": (124, "Eine Aktion wartet auf deine Freigabe"),
            "failed": (116, "Eine Automation ist fehlgeschlagen"),
        }
        for workflow in runtime.workflow_store.list():
            state = str(workflow.get("state") or "")
            if state not in priorities:
                continue
            score, reason = priorities[state]
            result.append(
                AttentionSignal(
                    id=f"workflow:{workflow['id']}",
                    fingerprint=_fingerprint(workflow),
                    score=score,
                    kind="workflow",
                    title=str(workflow.get("name") or workflow["id"]),
                    reason=reason,
                    next_action=(
                        "Prüfe, ob die Aktion bereits ausgeführt wurde."
                        if state == "needs_reconciliation"
                        else "Öffne die Automation und entscheide bewusst."
                    ),
                    target_view="system",
                    source_id=str(workflow["id"]),
                    due_at=workflow.get("next_run_at"),
                    consequence="Icarus setzt den Ablauf nicht still oder doppelt fort.",
                )
            )
        return result

    def _inbox_signals(self) -> list[AttentionSignal]:
        result = []
        try:
            pending = int(self.app.state.proposals.counts().get("pending", 0))
            if pending:
                result.append(
                    AttentionSignal(
                        id="proposals:pending",
                        fingerprint=_fingerprint({"pending": pending}),
                        score=92,
                        kind="decision",
                        title=f"{pending} Gedächtnisvorschlag{' wartet' if pending == 1 else 'e warten'}",
                        reason="Icarus übernimmt keine langfristige Aussage ohne Zustimmung",
                        next_action="Vorschläge prüfen und bestätigen oder ablehnen.",
                        target_view="proposals",
                        consequence="Ungeprüfte Vorschläge bleiben ausdrücklich unverbindlich.",
                    )
                )
        except Exception:
            pass
        try:
            pending = int(self.app.state.episodes.counts().get("new", 0))
            if pending:
                result.append(
                    AttentionSignal(
                        id="episodes:new",
                        fingerprint=_fingerprint({"pending": pending}),
                        score=66 + min(pending, 12),
                        kind="material",
                        title=f"{pending} neue Quelle{'n' if pending != 1 else ''}",
                        reason="Noch nicht verdichtetes Rohmaterial kann offene Informationen enthalten",
                        next_action="Quellen sichten oder kontrolliert verdichten.",
                        target_view="ingest",
                        consequence="Rohmaterial gilt weiterhin nicht als bestätigtes Wissen.",
                    )
                )
        except Exception:
            pass
        return result

    def _calendar_signals(self, at: datetime) -> list[AttentionSignal]:
        calendar = getattr(self.app.state, "calendar", None)
        if calendar is None:
            return []
        try:
            events = calendar.events(days=3, at=at)
        except Exception:
            return []
        result = []
        for event in events:
            if event.start is None:
                continue
            hours = (event.start - at).total_seconds() / 3600
            if hours < -1 or hours > 48:
                continue
            score = 110 if hours <= 3 else 90 if hours <= 12 else 74
            reason = (
                "Beginnt in weniger als drei Stunden"
                if hours <= 3
                else "Findet heute statt"
                if hours <= 12
                else "Findet innerhalb der nächsten zwei Tage statt"
            )
            payload = event.to_dict()
            result.append(
                AttentionSignal(
                    id=f"event:{event.uid}",
                    fingerprint=_fingerprint(payload),
                    score=score,
                    kind="meeting",
                    title=event.summary or "Termin",
                    reason=reason,
                    next_action="Terminbriefing öffnen und gewünschtes Ergebnis festlegen.",
                    target_view="dashboard",
                    source_id=event.uid,
                    due_at=event.start.isoformat(),
                    consequence="Vorbereitung reduziert Sucharbeit unmittelbar vor dem Termin.",
                )
            )
        return result

    def _mail_signal(self) -> list[AttentionSignal]:
        mail = getattr(self.app.state, "mail", None)
        if mail is None:
            return []
        try:
            messages = mail.inbox(limit=20)
        except Exception:
            return []
        unread = [message for message in messages if message.unread]
        if not unread:
            return []
        return [
            AttentionSignal(
                id="mail:unread",
                fingerprint=_fingerprint(
                    [(message.uid, message.subject, message.date) for message in unread]
                ),
                score=62 + min(len(unread), 12),
                kind="mail",
                title=f"{len(unread)} ungelesene Nachricht{'en' if len(unread) != 1 else ''}",
                reason="Darin können neue Verpflichtungen oder Rückfragen liegen",
                next_action="Posteingang sichten und konkrete offene Schleifen erfassen.",
                target_view="chat",
                consequence="Nicht jede ungelesene Nachricht ist dringend; sie bleibt deshalb unter Fristen und Freigaben.",
            )
        ]

    def signals(self, *, limit: int = 5, at: datetime | None = None) -> list[AttentionSignal]:
        at = at or _now()
        candidates = [
            *self._workflow_signals(),
            *self._task_signals(at),
            *self._calendar_signals(at),
            *self._project_signals(at),
            *self._inbox_signals(),
            *self._mail_signal(),
        ]
        visible = [signal for signal in candidates if self.preferences.visible(signal, at)]
        return sorted(
            visible,
            key=lambda signal: (-signal.score, signal.due_at or "", signal.title.casefold()),
        )[:limit]

    def event(self, uid: str, *, days: int = 30) -> Any:
        calendar = getattr(self.app.state, "calendar", None)
        if calendar is None:
            raise LookupError("Kein Kalender verbunden")
        for event in calendar.events(days=days):
            if event.uid == uid:
                return event
        raise LookupError("Termin nicht gefunden")

    def meeting_prep(self, uid: str) -> dict[str, Any]:
        event = self.event(uid)
        event_tokens = _tokens(
            event.summary,
            event.location,
            event.attendees,
        )
        project_matches = []
        for project in self.app.state.workspace.projects(include_closed=False, limit=10000):
            project_tokens = _tokens(
                project.name,
                project.area,
                project.description,
                project.tags,
            )
            overlap = event_tokens & project_tokens
            score = len(overlap) * 10
            if project.name.casefold() in (event.summary or "").casefold():
                score += 40
            if not score:
                continue
            tasks = [task.to_dict() for task in self.app.state.tasks.by_project(project.id)]
            decisions = [
                note.to_dict()
                for note in self.app.state.workspace.notes(project_id=project.id)
                if note.kind.value == "decision"
            ]
            project_matches.append(
                {
                    "score": score,
                    "project": project.to_dict(),
                    "open_tasks": tasks[:10],
                    "decisions": decisions[:10],
                    "matched_terms": sorted(overlap),
                }
            )
        project_matches.sort(
            key=lambda item: (-item["score"], item["project"]["name"].casefold())
        )
        project_matches = project_matches[:5]

        related_episodes = []
        for episode in self.app.state.episodes.all_episodes(limit=500):
            candidate_tokens = _tokens(
                episode.title,
                episode.body,
                episode.participants,
            )
            overlap = event_tokens & candidate_tokens
            if overlap:
                related_episodes.append(
                    {
                        "score": len(overlap),
                        "episode": episode.to_dict(),
                        "matched_terms": sorted(overlap),
                    }
                )
        related_episodes.sort(
            key=lambda item: (
                -item["score"],
                str(item["episode"].get("occurred_at") or ""),
            )
        )

        memory = []
        try:
            memory = [
                assertion.to_dict()
                for assertion in self.app.state.store.recall(event.summary, limit=10)
            ]
        except Exception:
            pass

        first_task = next(
            (
                task["title"]
                for match in project_matches
                for task in match["open_tasks"]
                if task.get("status") == "open"
            ),
            None,
        )
        suggested_outcome = (
            f"Kläre den nächsten Schritt zu: {first_task}"
            if first_task
            else "Lege das gewünschte Ergebnis und den nächsten verantwortlichen Schritt fest."
        )
        questions = [
            "Welches konkrete Ergebnis soll am Ende des Termins feststehen?",
            "Welche Entscheidung oder Freigabe wird benötigt?",
            "Wer übernimmt den nächsten Schritt und bis wann?",
        ]
        if event.attendees:
            questions.append("Welche Erwartung haben die beteiligten Personen vermutlich?")

        return {
            "event": event.to_dict(),
            "suggested_outcome": suggested_outcome,
            "questions": questions,
            "related_projects": project_matches,
            "related_episodes": related_episodes[:10],
            "memory": memory,
            "provenance": {
                "calendar_uid": event.uid,
                "project_count": len(project_matches),
                "episode_count": min(len(related_episodes), 10),
                "memory_count": len(memory),
                "generated_without_model": True,
            },
        }


class SnoozeIn(BaseModel):
    fingerprint: str = Field(min_length=1)
    until_at: datetime | None = None
    hours: int | None = Field(default=None, ge=1, le=24 * 30)


class DismissIn(BaseModel):
    fingerprint: str = Field(min_length=1)


def _auth_dependency() -> Callable[..., None]:
    expected = os.environ.get(TOKEN_ENV)

    def auth(x_icarus_token: Annotated[str | None, Header()] = None) -> None:
        if expected is None:
            return
        if x_icarus_token is None or not secrets.compare_digest(x_icarus_token, expected):
            raise HTTPException(status_code=401, detail="Ungültiges Token")

    return auth


def _move_ui_mount(app: FastAPI) -> list[Mount]:
    mounts = [
        route
        for route in app.router.routes
        if isinstance(route, Mount) and getattr(route, "name", None) == "ui"
    ]
    if mounts:
        app.router.routes[:] = [route for route in app.router.routes if route not in mounts]
    return mounts


def install_proactive(app: FastAPI, data_dir: Path) -> AttentionEngine:
    preferences = AttentionPreferences(data_dir / "workspace.sqlite3")
    engine = AttentionEngine(app, preferences)
    app.state.attention = engine
    auth = _auth_dependency()
    guard = [Depends(auth)]
    ui_mounts = _move_ui_mount(app)
    router = APIRouter(prefix="/chief-of-staff", dependencies=guard)

    @router.get("/attention")
    def attention(limit: int = 5) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10:
            raise HTTPException(status_code=400, detail="limit muss zwischen 1 und 10 liegen")
        return [signal.to_dict() for signal in engine.signals(limit=limit)]

    @router.post("/attention/{signal_id:path}/snooze")
    def snooze(signal_id: str, body: SnoozeIn) -> dict[str, Any]:
        until = body.until_at
        if until is None:
            until = _now() + timedelta(hours=body.hours or 24)
        if until.tzinfo is None:
            until = until.astimezone()
        if until <= _now():
            raise HTTPException(status_code=400, detail="Der Zeitpunkt muss in der Zukunft liegen")
        preferences.set(signal_id, body.fingerprint, "snooze", until_at=until)
        return {"id": signal_id, "snoozed_until": until.astimezone(timezone.utc).isoformat()}

    @router.post("/attention/{signal_id:path}/dismiss")
    def dismiss(signal_id: str, body: DismissIn) -> dict[str, Any]:
        preferences.set(signal_id, body.fingerprint, "dismiss")
        return {"id": signal_id, "dismissed": True, "fingerprint": body.fingerprint}

    @router.get("/meetings")
    def meetings(days: int = 3) -> dict[str, Any]:
        if days < 1 or days > 30:
            raise HTTPException(status_code=400, detail="days muss zwischen 1 und 30 liegen")
        calendar = getattr(app.state, "calendar", None)
        if calendar is None:
            return {"items": [], "error": "Kein Kalender verbunden."}
        try:
            items = [event.to_dict() for event in calendar.events(days=days)]
            return {"items": items, "error": None}
        except Exception as exc:
            return {"items": [], "error": str(exc)}

    @router.get("/meetings/{uid}/prep")
    def meeting_prep(uid: str) -> dict[str, Any]:
        try:
            return engine.meeting_prep(uid)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    app.include_router(router)
    app.router.routes.extend(ui_mounts)
    return engine


__all__ = [
    "AttentionEngine",
    "AttentionPreferences",
    "AttentionSignal",
    "install_proactive",
]
