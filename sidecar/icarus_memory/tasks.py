"""Aufgaben.

Bewusst eine eigene Ablage neben dem Selbstmodell, obwohl es verlockend wäre,
sie als Aussagen vom Typ `goal` zu führen.

Der Grund: Aussagen im Selbstmodell beschreiben, **wie jemand ist**. Aufgaben
beschreiben, **was zu tun ist**. Eine erledigte Aufgabe ist nicht „ersetzt" oder
„widerrufen" — sie ist fertig, und das ist ein anderer Lebenszyklus. Sie in
dasselbe Modell zu pressen würde beide verwässern.

Was übernommen wird, ist das Prinzip: Auch eine Aufgabe trägt ihre Herkunft.
Wenn in drei Monaten „Rechnung an Müller schicken" auftaucht, muss beantwortbar
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

    def is_overdue(self, at: datetime | None = None) -> bool:
        at = at or now()
        return self.status is TaskStatus.OPEN and self.due is not None and at > self.due

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
            "overdue": self.is_overdue(),
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status     TEXT NOT NULL,
    due        TEXT,
    title      TEXT NOT NULL,
    document   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due    ON tasks(due);
"""


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
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- Schreiben ---------------------------------------------------------

    def add(
        self,
        title: str,
        provenance: Provenance,
        *,
        due: datetime | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
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
                "INSERT INTO tasks (id, created_at, status, due, title, document) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
                "due=excluded.due, title=excluded.title, document=excluded.document",
                (d["id"], d["created_at"], d["status"], d["due"], d["title"],
                 json.dumps(d, ensure_ascii=False)),
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
        )


__all__ = ["Task", "TaskStatus", "TaskStore"]
