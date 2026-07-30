"""Icarus Selbstmodell — die überprüfbare Gedächtnisschicht.

Die Bibliothek trennt drei Dinge bewusst:

* `model`     — das Datenmodell, gespiegelt aus schema/self-model.schema.json
* `store`     — die Logik: Ersetzung, Ablauf, kaskadierender Widerruf
* `backends`  — die Ablage: SQLite als Bestand, cognee als semantischer Index

`store` kennt weder cognee noch SQLite. Dadurch ist die Logik ohne Netz, ohne
Modell und ohne Datenbank testbar, und die Ablage bleibt austauschbar.
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

__version__ = "0.1.0"

__all__ = [
    "SCHEMA_VERSION",
    "Assertion",
    "CogneeBackend",
    "ConflictError",
    "Kind",
    "MemoryBackend",
    "Provenance",
    "Redaction",
    "RedactionReason",
    "SelfModel",
    "SelfModelStore",
    "Sensitivity",
    "SourceType",
    "SqliteBackend",
    "Status",
    "__version__",
]
