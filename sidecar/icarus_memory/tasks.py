"""Aufgaben.

Bewusst eine eigene Ablage neben dem Selbstmodell, obwohl es verlockend wäre,
sie als Aussagen vom Typ `goal` zu führen.

Der Grund: Aussagen im Selbstmodell beschreiben, **wie jemand ist**. Aufgaben
beschreiben, **was zu tun ist**. Eine erledigte Aufgabe ist nicht „ersetzt“ oder
„widerrufen“ — sie ist fertig, und das ist ein anderer Lebenszyklus. Sie in
dasselbe Modell zu pressen würde beide verwässern.

Was übernommen wird, ist das Prinzip: Auch eine Aufgabe trägt ihre Herkunft.
Wenn in drei Monaten „Rechnung an Müller schicken“ auftaucht, muss beantwortbar
sein, woher das kam — aus einer Mail, aus einem Gespräch, oder hat das System
es sich ausgedacht.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from .migrations import (
    IndexContract,
    Migration,
    run_migrations,
    table_columns,
    validate_legacy_or_empty,
    verify_schema,
)
from .model import Provenance, SourceType, ensure_aware, now


class TaskStatus(str, Enum):
    OPEN = "open"
    DONE = "done"
    DROPPED = "dropped"
    """Fallengelassen — nicht erledigt, aber auch nicht mehr offen.

    Der Unterschied zu `done` ist für ein System, das Jahre läuft, wichtig:
    Sonst sieht es später aus, als wäre alles geschafft worden.
    """


@dataclass
class Task:
    id: str
    title: str
    provenance: Provenance
    created_at: datetime
    status: TaskStatus = TaskStatus.OPEN
    due: datetime | None = None
    notes: str | None = None
    done_at: datetime | None = None
    tags: list[str] = field(default_factory=list)
    wartet_auf: str | None = None
    """Bei wem die Aufgabe gerade liegt.

    Ein Stabschef unterscheidet zwei Dinge, die eine flache Liste in einen Topf
    wirft: was **du** noch tun musst, und worauf du **wartest**. Beides ist
    offen, aber nur das erste ist Arbeit für dich. Solange hier ein Name steht,
    liegt die Aufgabe bei jemand anderem.
    """

    wartet_seit: datetime | None = None
    """Seit wann sie dort liegt — die Grundlage jedes Nachfassens."""

    project_id: str | None = None
    """Zu welchem Projekt die Aufgabe gehört.

    Optional, und das ist Absicht: Nicht alles im Leben ist ein Projekt, und
    ein Pflichtfeld hier würde dazu führen, dass ein Sammelprojekt „Sonstiges“
    entsteht — das wäre dieselbe flache Liste mit mehr Schritten.
    """

    def is_overdue(self, at: datetime | None = None) -> bool:
        """Überfällig ist nur, was bei **dir** liegt.

        Was jemand anderes schuldet, kann sein Datum reißen, ohne dass es
        deine Versäumnisliste verlängert. Sonst wächst dort ein roter Berg
        aus Dingen, an denen du nichts ändern kannst — und die Liste, die
        eigentlich handlungsfähig machen soll, wird zur Anklage.
        """
        return (
            self.status is TaskStatus.OPEN
            and self.wartet_auf is None
            and self.due is not None
            and (at or now()) > self.due
        )

    def wartet_tage(self, at: datetime | None = None) -> int | None:
        """Wie viele Tage die Aufgabe schon bei jemandem liegt."""
        if self.wartet_seit is None:
            return None
        return max(0, ((at or now()) - self.wartet_seit).days)

    def to_dict(self) -> dict[str, Any]:
        def iso(v: datetime | None) -> str | None:
            return v.astimezone().isoformat() if v else None

        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "provenance": self.provenance.to_dict(),
            "created_at": iso(self.created_at),
            "due": iso(self.due),
            "notes": self.notes,
            "done_at": iso(self.done_at),
            "tags": list(self.tags),
            "project_id": self.project_id,
            "wartet_auf": self.wartet_auf,
            "wartet_seit": iso(self.wartet_seit),
            "wartet_tage": self.wartet_tage(),
            "overdue": self.is_overdue(),
        }


_CREATE_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status     TEXT NOT NULL,
    due        TEXT,
    title      TEXT NOT NULL,
    document   TEXT NOT NULL,
    project_id TEXT
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id)",
)
_INDEX_CONTRACTS = {
    "idx_tasks_status": IndexContract("tasks", ("status",)),
    "idx_tasks_due": IndexContract("tasks", ("due",)),
    "idx_tasks_project": IndexContract("tasks", ("project_id",)),
}
_LEGACY_SCHEMA = {
    "tasks": {"id", "created_at", "status", "due", "title", "document"}
}
_CURRENT_SCHEMA = {
    "tasks": {
        "id",
        "created_at",
        "status",
        "due",
        "title",
        "document",
        "project_id",
    }
}
_PRIMARY_KEYS = {"tasks": {"id"}}


def _migrate_v1(connection: sqlite3.Connection) -> None:
    validate_legacy_or_empty(
        connection,
        store="tasks",
        path=connection.execute("PRAGMA database_list").fetchone()[2],
        expected_tables=_LEGACY_SCHEMA,
        allowed_column_sets={
            "tasks": (_LEGACY_SCHEMA["tasks"], _CURRENT_SCHEMA["tasks"])
        },
        expected_indexes=_INDEX_CONTRACTS,
        expected_primary_keys=_PRIMARY_KEYS,
    )
    connection.execute(_CREATE_TASKS)
    # Bekannter Legacy-Bestand aus der Zeit vor der Projektebene. Anders als
    # bisher wird nur die erwartete fehlende Spalte behandelt; andere
    # OperationalErrors werden nicht verschluckt.
    if "project_id" not in table_columns(connection, "tasks"):
        connection.execute("ALTER TABLE tasks ADD COLUMN project_id TEXT")
    for statement in _INDEXES:
        connection.execute(statement)


def _verify_v1(connection: sqlite3.Connection) -> None:
    verify_schema(
        connection,
        expected_tables=_CURRENT_SCHEMA,
        expected_indexes=_INDEX_CONTRACTS,
        expected_primary_keys=_PRIMARY_KEYS,
    )


_MIGRATIONS = (
    Migration(1, "initial_explicit_version", _migrate_v1, _verify_v1),
)


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class TaskStore:
    """Aufgaben in einer lokalen Datei, neben dem Selbstmodell."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Wie SqliteBackend: FastAPI bedient aus einem Threadpool.
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        try:
            with self._lock:
                run_migrations(
                    self._conn,
                    store="tasks",
                    path=self._path,
                    migrations=_MIGRATIONS,
                )
        except Exception:
            self._conn.close()
            raise

    # -- Schreiben ---------------------------------------------------------

    def add(
        self,
        title: str,
        provenance: Provenance,
        *,
        due: datetime | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
        project_id: str | None = None,
        at: datetime | None = None,
    ) -> Task:
        task = Task(
            id=f"t-{uuid.uuid4().hex[:12]}",
            title=title,
            provenance=provenance,
            created_at=ensure_aware(at) or now(),
            # Über HTTP kommen Fälligkeiten ohne Zeitzone herein.
            due=ensure_aware(due),
            notes=notes,
            tags=list(tags or []),
            project_id=project_id,
        )
        self._put(task)
        return task

    def complete(self, task_id: str, at: datetime | None = None) -> Task:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unbekannte Aufgabe: {task_id}")
        task.status = TaskStatus.DONE
        task.done_at = at or now()
        self._put(task)
        return task

    def drop(self, task_id: str, at: datetime | None = None) -> Task:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unbekannte Aufgabe: {task_id}")
        task.status = TaskStatus.DROPPED
        task.done_at = at or now()
        self._put(task)
        return task

    def warten_auf(self, task_id: str, name: str, at: datetime | None = None) -> Task:
        """Die Aufgabe liegt ab jetzt bei jemand anderem.

        Wird derselbe Name noch einmal gesetzt, bleibt die Frist stehen. Sonst
        könnte man die eigene Wartezeit dadurch zurücksetzen, dass man das
        System noch einmal daran erinnert, worauf man wartet — und genau der
        Satz „das liegt seit sechs Wochen bei ihm“ ginge verloren.
        """
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unbekannte Aufgabe: {task_id}")
        name = name.strip()
        if not name:
            raise ValueError("Ohne Namen ist nicht sagbar, bei wem es liegt.")
        if task.wartet_auf is None or name != task.wartet_auf:
            task.wartet_auf = name
            task.wartet_seit = at or now()
        self._put(task)
        return task

    def zurueckholen(self, task_id: str) -> Task:
        """Die Aufgabe liegt wieder bei dir."""
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unbekannte Aufgabe: {task_id}")
        task.wartet_auf = None
        task.wartet_seit = None
        self._put(task)
        return task

    def reopen(self, task_id: str) -> Task:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Unbekannte Aufgabe: {task_id}")
        task.status = TaskStatus.OPEN
        task.done_at = None
        self._put(task)
        return task

    # -- Lesen -------------------------------------------------------------

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._from_row(row) if row else None

    def open_tasks(self, limit: int = 200) -> list[Task]:
        """Offene Aufgaben, überfällige und bald fällige zuerst.

        Aufgaben ohne Fälligkeit landen hinten — sonst verdrängen sie das,
        was tatsächlich ansteht.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM tasks WHERE status = ? "
                "ORDER BY due IS NULL, due ASC, created_at ASC LIMIT ?",
                (TaskStatus.OPEN.value, limit),
            ).fetchall()
        return [self._from_row(r) for r in rows]

    def due_within(self, days: int = 7, at: datetime | None = None) -> list[Task]:
        at = at or now()
        limit = at + timedelta(days=days)
        return [
            t for t in self.open_tasks()
            if t.due is not None and t.due <= limit
        ]

    def wartend(self, at: datetime | None = None) -> list[Task]:
        """Alles, was gerade bei anderen liegt — am längsten Wartendes zuerst."""
        offen = [t for t in self.open_tasks() if t.wartet_auf is not None]
        return sorted(offen, key=lambda t: t.wartet_seit or t.created_at)

    def by_project(self, project_id: str, include_closed: bool = False) -> list[Task]:
        """Alle Aufgaben eines Projekts.

        Die zentrale Abfrage der Projektansicht: „Was steht bei X noch an?“
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM tasks WHERE project_id = ? "
                "ORDER BY due IS NULL, due ASC, created_at ASC",
                (project_id,),
            ).fetchall()
        items = [self._from_row(r) for r in rows]
        if not include_closed:
            items = [t for t in items if t.status is TaskStatus.OPEN]
        return items

    def all_tasks(self, limit: int = 500) -> list[Task]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._from_row(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- Intern ------------------------------------------------------------

    def _put(self, task: Task) -> None:
        d = task.to_dict()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (id, created_at, status, due, title, project_id, document) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
                "due=excluded.due, title=excluded.title, "
                "project_id=excluded.project_id, document=excluded.document",
                (d["id"], d["created_at"], d["status"], d["due"], d["title"],
                 d["project_id"], json.dumps(d, ensure_ascii=False)),
            )
            self._conn.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Task:
        d = json.loads(row["document"])
        p = d["provenance"]
        return Task(
            id=d["id"],
            title=d["title"],
            provenance=Provenance(
                source_type=SourceType(p["source_type"]),
                source_ref=p.get("source_ref"),
                captured_at=_parse(p.get("captured_at")),
                extracted_by=p.get("extracted_by"),
                verbatim=p.get("verbatim"),
            ),
            created_at=_parse(d["created_at"]),  # type: ignore[arg-type]
            status=TaskStatus(d["status"]),
            due=_parse(d.get("due")),
            notes=d.get("notes"),
            done_at=_parse(d.get("done_at")),
            tags=list(d.get("tags", [])),
            project_id=d.get("project_id"),
            wartet_auf=d.get("wartet_auf"),
            wartet_seit=_parse(d.get("wartet_seit")),
        )


__all__ = ["Task", "TaskStatus", "TaskStore"]
