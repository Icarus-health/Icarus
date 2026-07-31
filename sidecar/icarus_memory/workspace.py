"""Projekte und Notizen — die Arbeitsebene.

Icarus kannte bisher zwei Dinge: **Aussagen** (wie jemand ist) und **Aufgaben**
(was zu tun ist). Für einen Assistenten, der ein Arbeitsleben trägt, fehlt
dazwischen die Ebene, an der beides hängt: das Projekt.

Fast alles, was ein Mensch tut, gehört zu etwas. Eine Aufgabe ohne Projekt ist
ein Zettel; dieselbe Aufgabe an einem Projekt ist ein Schritt. Ein System, das
nur flache Listen führt, kann deshalb nie die Frage beantworten, die im Alltag
wirklich gestellt wird — „wie steht es um X?" — und muss stattdessen bei jeder
Sitzung neu erklärt bekommen, was X überhaupt ist.

Projekte und Notizen liegen in **einer** Datei, weil sie eine Ebene sind: eine
Notiz ohne Projekt ist im Regelfall verwaist, und beide teilen denselben
Lebenszyklus (entsteht, wird bearbeitet, wird archiviert).

## Warum Notizen anders sind als Aussagen

Die Aussagenschicht ist append-only, per SQLite-Trigger erzwungen. Notizen sind
es bewusst **nicht**. Der Unterschied ist kein Nachlassen, sondern eine andere
Natur der Sache:

- Eine Aussage ist eine **Behauptung über die Person**. Wird sie überschreibbar,
  verschwindet der Widerspruch zwischen alt und neu, und niemand kann mehr
  prüfen, was das System zu wissen glaubte. Deshalb: unveränderlich.
- Eine Notiz ist ein **Arbeitsdokument**. Ein Besprechungsprotokoll, an dem man
  nichts ändern darf, zwingt zu einer zweiten Notiz mit dem Zusatz „Korrektur zu
  oben" — und macht die Ablage schlechter, nicht ehrlicher.

Was auch bei Notizen unveränderlich bleibt, ist die **Herkunft**: aus welchem
Transkript, welcher Mail, welchem Gespräch sie stammt. Dazu `revision` und
`updated_at`, damit sichtbar ist, dass überhaupt bearbeitet wurde. Wer eine
Notiz in eine belastbare Aussage überführen will, nutzt `store.record()` — dann
gelten wieder die strengen Regeln.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .model import Provenance, SourceType, ensure_aware, now


class ProjectStatus(str, Enum):
    IDEA = "idea"
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"
    DROPPED = "dropped"
    """Aufgegeben — nicht abgeschlossen.

    Dieselbe Unterscheidung wie bei Aufgaben, und aus demselben Grund: Ein
    System, das Jahre läuft, darf nicht so aussehen, als wäre alles gelungen.
    Aufgegebene Projekte sind die ehrlichste Zeile in jeder Rückschau.
    """


class Priority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class NoteKind(str, Enum):
    MEETING = "meeting"
    RESEARCH = "research"
    IDEA = "idea"
    DECISION = "decision"
    """Warum etwas so entschieden wurde.

    Eigener Typ, weil das die Art Notiz ist, die man Monate später sucht und
    fast nie findet — dieselbe Überlegung, aus der die ADRs im Repo entstanden.
    """

    REFERENCE = "reference"


@dataclass
class Project:
    id: str
    name: str
    provenance: Provenance
    created_at: datetime
    area: str | None = None
    """Lebensbereich — Firma, Studium, Buch, privat.

    Bewusst ein freier Text und keine Aufzählung: Bereiche entstehen und
    vergehen im Lauf von Jahren, und ein Schema, das sie festschreibt, müsste
    für jeden neuen Lebensabschnitt migriert werden.
    """

    status: ProjectStatus = ProjectStatus.ACTIVE
    priority: Priority = Priority.MEDIUM
    description: str | None = None
    deadline: datetime | None = None
    closed_at: datetime | None = None
    tags: list[str] = field(default_factory=list)

    def is_open(self) -> bool:
        return self.status in (ProjectStatus.IDEA, ProjectStatus.ACTIVE, ProjectStatus.PAUSED)

    def to_dict(self) -> dict[str, Any]:
        def iso(v: datetime | None) -> str | None:
            return v.astimezone().isoformat() if v else None

        return {
            "id": self.id,
            "name": self.name,
            "area": self.area,
            "status": self.status.value,
            "priority": self.priority.value,
            "description": self.description,
            "provenance": self.provenance.to_dict(),
            "created_at": iso(self.created_at),
            "deadline": iso(self.deadline),
            "closed_at": iso(self.closed_at),
            "tags": list(self.tags),
            "open": self.is_open(),
        }


@dataclass
class Note:
    id: str
    title: str
    body: str
    provenance: Provenance
    created_at: datetime
    updated_at: datetime
    kind: NoteKind = NoteKind.REFERENCE
    project_id: str | None = None
    revision: int = 1
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def iso(v: datetime | None) -> str | None:
            return v.astimezone().isoformat() if v else None

        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "kind": self.kind.value,
            "project_id": self.project_id,
            "provenance": self.provenance.to_dict(),
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
            "revision": self.revision,
            "tags": list(self.tags),
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status     TEXT NOT NULL,
    area       TEXT,
    deadline   TEXT,
    name       TEXT NOT NULL,
    document   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_area   ON projects(area);

CREATE TABLE IF NOT EXISTS notes (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    kind       TEXT NOT NULL,
    project_id TEXT,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    document   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_project ON notes(project_id);
CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at);
"""


class WorkspaceError(Exception):
    """Ein Projekt oder eine Notiz ist unbekannt, oder eine Verknüpfung zeigt ins Leere."""


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _provenance_from(d: dict[str, Any]) -> Provenance:
    return Provenance(
        source_type=SourceType(d["source_type"]),
        source_ref=d.get("source_ref"),
        captured_at=_parse(d.get("captured_at")),
        extracted_by=d.get("extracted_by"),
        verbatim=d.get("verbatim"),
    )


class WorkspaceStore:
    """Projekte und Notizen in einer lokalen Datei, neben dem Selbstmodell."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Wie SqliteBackend und TaskStore: FastAPI bedient aus einem Threadpool.
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- Projekte ----------------------------------------------------------

    def add_project(
        self,
        name: str,
        provenance: Provenance,
        *,
        area: str | None = None,
        status: ProjectStatus = ProjectStatus.ACTIVE,
        priority: Priority = Priority.MEDIUM,
        description: str | None = None,
        deadline: datetime | None = None,
        tags: list[str] | None = None,
        at: datetime | None = None,
    ) -> Project:
        project = Project(
            id=f"p-{uuid.uuid4().hex[:12]}",
            name=name,
            provenance=provenance,
            created_at=ensure_aware(at) or now(),
            area=area,
            status=status,
            priority=priority,
            description=description,
            deadline=ensure_aware(deadline),
            tags=list(tags or []),
        )
        self._put_project(project)
        return project

    def update_project(
        self,
        project_id: str,
        *,
        status: ProjectStatus | None = None,
        priority: Priority | None = None,
        description: str | None = None,
        deadline: datetime | None = None,
        area: str | None = None,
        at: datetime | None = None,
    ) -> Project:
        project = self.project(project_id)
        if status is not None:
            project.status = status
            # Abgeschlossen und aufgegeben sind beide ein Ende; nur das eine
            # ist ein Erfolg. Der Zeitpunkt gehört zu beiden.
            project.closed_at = (
                (at or now()) if not project.is_open() else None
            )
        if priority is not None:
            project.priority = priority
        if description is not None:
            project.description = description
        if deadline is not None:
            project.deadline = ensure_aware(deadline)
        if area is not None:
            project.area = area
        self._put_project(project)
        return project

    def project(self, project_id: str) -> Project:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise WorkspaceError(f"Unbekanntes Projekt: {project_id}")
        return self._project_from_row(row)

    def find_project(self, needle: str) -> Project | None:
        """Sucht ein Projekt über Kennung oder Namensfragment.

        Der Assistent bekommt vom Nutzer „NutriFlow", nicht `p-3f2a…`. Ohne
        diese Auflösung müsste bei jeder Aufgabe erst die Liste geholt werden,
        und das Modell würde raten.
        """
        needle = needle.strip()
        if not needle:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM projects WHERE id = ?", (needle,)
            ).fetchone()
        if row is not None:
            return self._project_from_row(row)

        folded = needle.casefold()
        candidates = [p for p in self.projects(include_closed=True)
                      if folded in p.name.casefold()]
        if not candidates:
            return None
        # Offene Projekte zuerst: Wer „Icarus" sagt, meint fast nie das vor
        # zwei Jahren abgeschlossene gleichnamige.
        candidates.sort(key=lambda p: (not p.is_open(), len(p.name)))
        return candidates[0]

    def projects(self, include_closed: bool = False, limit: int = 500) -> list[Project]:
        """Projekte, dringendste zuerst.

        Sortiert nach Frist und dann nach Priorität — nicht nach Anlagedatum.
        Eine Liste, die das älteste Projekt oben zeigt, beantwortet keine Frage,
        die morgens gestellt wird.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM projects ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        items = [self._project_from_row(r) for r in rows]
        if not include_closed:
            items = [p for p in items if p.is_open()]
        rank = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}
        items.sort(key=lambda p: (
            p.deadline is None,
            p.deadline or now(),
            rank[p.priority],
        ))
        return items

    def areas(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT area FROM projects WHERE area IS NOT NULL ORDER BY area"
            ).fetchall()
        return [r["area"] for r in rows]

    # -- Notizen -----------------------------------------------------------

    def add_note(
        self,
        title: str,
        body: str,
        provenance: Provenance,
        *,
        kind: NoteKind = NoteKind.REFERENCE,
        project_id: str | None = None,
        tags: list[str] | None = None,
        at: datetime | None = None,
    ) -> Note:
        if project_id is not None:
            # Fail-closed: eine Notiz an einem erfundenen Projekt wäre still
            # verloren — sie taucht in keiner Projektansicht je wieder auf.
            self.project(project_id)
        moment = ensure_aware(at) or now()
        note = Note(
            id=f"n-{uuid.uuid4().hex[:12]}",
            title=title,
            body=body,
            provenance=provenance,
            created_at=moment,
            updated_at=moment,
            kind=kind,
            project_id=project_id,
            tags=list(tags or []),
        )
        self._put_note(note)
        return note

    def update_note(
        self,
        note_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        project_id: str | None = None,
        at: datetime | None = None,
    ) -> Note:
        """Bearbeitet eine Notiz. Die Herkunft bleibt unangetastet.

        Anders als eine Aussage darf eine Notiz überschrieben werden — sie ist
        ein Arbeitsdokument. Was nicht überschrieben wird, ist die Angabe,
        woher sie ursprünglich kam; sonst ließe sich ein Transkriptauszug
        nachträglich zu einer Nutzeräußerung umwidmen.
        """
        note = self.note(note_id)
        if project_id is not None:
            self.project(project_id)
            note.project_id = project_id
        if title is not None:
            note.title = title
        if body is not None:
            note.body = body
        note.revision += 1
        note.updated_at = ensure_aware(at) or now()
        self._put_note(note)
        return note

    def note(self, note_id: str) -> Note:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM notes WHERE id = ?", (note_id,)
            ).fetchone()
        if row is None:
            raise WorkspaceError(f"Unbekannte Notiz: {note_id}")
        return self._note_from_row(row)

    def notes(
        self,
        project_id: str | None = None,
        kind: NoteKind | None = None,
        limit: int = 100,
    ) -> list[Note]:
        sql = "SELECT document FROM notes"
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._note_from_row(r) for r in rows]

    def search_notes(self, query: str, limit: int = 20) -> list[Note]:
        """Substringsuche über Titel und Text.

        Bewusst ohne Modell: Notizen zu finden darf nie davon abhängen, dass
        ein Anbieter erreichbar ist. Die semantische Suche liegt eine Ebene
        höher (cognee) und ergänzt das, statt es zu ersetzen.
        """
        pattern = f"%{query}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM notes WHERE title LIKE ? OR body LIKE ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (pattern, pattern, limit),
            ).fetchall()
        return [self._note_from_row(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- Intern ------------------------------------------------------------

    def _put_project(self, project: Project) -> None:
        d = project.to_dict()
        with self._lock:
            self._conn.execute(
                "INSERT INTO projects (id, created_at, status, area, deadline, name, document) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, area=excluded.area, "
                "deadline=excluded.deadline, name=excluded.name, document=excluded.document",
                (d["id"], d["created_at"], d["status"], d["area"], d["deadline"],
                 d["name"], json.dumps(d, ensure_ascii=False)),
            )
            self._conn.commit()

    def _put_note(self, note: Note) -> None:
        d = note.to_dict()
        with self._lock:
            self._conn.execute(
                "INSERT INTO notes (id, created_at, updated_at, kind, project_id, "
                "title, body, document) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, "
                "kind=excluded.kind, project_id=excluded.project_id, "
                "title=excluded.title, body=excluded.body, document=excluded.document",
                (d["id"], d["created_at"], d["updated_at"], d["kind"], d["project_id"],
                 d["title"], d["body"], json.dumps(d, ensure_ascii=False)),
            )
            self._conn.commit()

    @staticmethod
    def _project_from_row(row: sqlite3.Row) -> Project:
        d = json.loads(row["document"])
        return Project(
            id=d["id"],
            name=d["name"],
            provenance=_provenance_from(d["provenance"]),
            created_at=_parse(d["created_at"]),  # type: ignore[arg-type]
            area=d.get("area"),
            status=ProjectStatus(d["status"]),
            priority=Priority(d.get("priority", Priority.MEDIUM.value)),
            description=d.get("description"),
            deadline=_parse(d.get("deadline")),
            closed_at=_parse(d.get("closed_at")),
            tags=list(d.get("tags", [])),
        )

    @staticmethod
    def _note_from_row(row: sqlite3.Row) -> Note:
        d = json.loads(row["document"])
        return Note(
            id=d["id"],
            title=d["title"],
            body=d["body"],
            provenance=_provenance_from(d["provenance"]),
            created_at=_parse(d["created_at"]),  # type: ignore[arg-type]
            updated_at=_parse(d["updated_at"]),  # type: ignore[arg-type]
            kind=NoteKind(d.get("kind", NoteKind.REFERENCE.value)),
            project_id=d.get("project_id"),
            revision=int(d.get("revision", 1)),
            tags=list(d.get("tags", [])),
        )


__all__ = [
    "Note",
    "NoteKind",
    "Priority",
    "Project",
    "ProjectStatus",
    "WorkspaceError",
    "WorkspaceStore",
]
