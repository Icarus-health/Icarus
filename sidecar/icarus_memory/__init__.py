"""Icarus Selbstmodell — die überprüfbare Gedächtnisschicht.

Die Bibliothek trennt drei Dinge bewusst:

* `model`     — das Datenmodell, gespiegelt aus schema/self-model.schema.json
* `store`     — die Logik: Ersetzung, Ablauf, kaskadierender Widerruf
* `backends`  — die Ablage: SQLite als Bestand, cognee als semantischer Index

`store` kennt weder cognee noch SQLite. Dadurch ist die Logik ohne Netz, ohne
Modell und ohne Datenbank testbar, und die Ablage bleibt austauschbar.

Daneben stehen drei Ablagen mit eigenem Lebenszyklus, die bewusst *nicht* ins
Selbstmodell gepresst werden — eine erledigte Aufgabe ist nicht „ersetzt", ein
abgeschlossenes Projekt nicht „widerrufen":

* `tasks`     — Aufgaben, mit Herkunft und der Unterscheidung erledigt/aufgegeben
* `workspace` — Projekte und Notizen, die Ebene, an der Aufgaben und Wissen hängen
"""

from .backends import CogneeBackend, MemoryBackend, SqliteBackend
from .model import (
    SCHEMA_VERSION,
    Assertion,
    Kind,
    Provenance,
    Redaction,
    RedactionReason,
    SelfModel,
    Sensitivity,
    SourceType,
    Status,
)
from .store import ConflictError, SelfModelStore
from .tasks import Task, TaskStatus, TaskStore
from .workspace import (
    Note,
    NoteKind,
    Priority,
    Project,
    ProjectStatus,
    WorkspaceError,
    WorkspaceStore,
)

__version__ = "0.1.0"

__all__ = [
    "SCHEMA_VERSION",
    "Assertion",
    "CogneeBackend",
    "ConflictError",
    "Kind",
    "MemoryBackend",
    "Note",
    "NoteKind",
    "Priority",
    "Project",
    "ProjectStatus",
    "Provenance",
    "Redaction",
    "RedactionReason",
    "SelfModel",
    "SelfModelStore",
    "Sensitivity",
    "SourceType",
    "SqliteBackend",
    "Status",
    "Task",
    "TaskStatus",
    "TaskStore",
    "WorkspaceError",
    "WorkspaceStore",
    "__version__",
]
