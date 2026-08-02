"""Datenmodell des überprüfbaren Selbstmodells.

Spiegelt ``schema/self-model.schema.json``. Das Schema bleibt die öffentliche
Referenz; diese Klassen sind die Laufzeitform davon. Das Modell ist bewusst
unabhängig von cognee oder einem bestimmten LLM.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

SCHEMA_VERSION = "0.2.0"
LEGACY_SCHEMA_VERSION = "0.1.0"


def now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime | None) -> datetime | None:
    """Normalisiert eingehende Zeitpunkte auf eine zeitzonenbehaftete Form."""
    if value is None or value.tzinfo is not None:
        return value
    return value.astimezone()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Kind(str, Enum):
    """Zeitliche Natur einer Aussage."""

    IDENTITY = "identity"
    PREFERENCE = "preference"
    STATE = "state"
    EPISODE = "episode"
    GOAL = "goal"
    RELATIONSHIP = "relationship"
    SKILL = "skill"
    CONSTRAINT = "constraint"


class Status(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    RETRACTED = "retracted"
    REDACTED = "redacted"
    DISPUTED = "disputed"
    """Die Aussage steht in einem ungeklärten Widerspruch.

    Sie bleibt nachvollziehbar und in ihrer Ersetzungs- beziehungsweise
    Ableitungskette erhalten, ist aber nicht ``usable()``. Aufgelöst wird der
    Konflikt über die bestehenden Wege: Ersetzung oder Rücknahme.
    """


class SourceType(str, Enum):
    USER_STATED = "user_stated"
    CHAT = "chat"
    EMAIL = "email"
    CALENDAR = "calendar"
    DOCUMENT = "document"
    WEB = "web"
    TOOL_OUTPUT = "tool_output"
    INFERENCE = "inference"
    MANUAL_CORRECTION = "manual_correction"


class Sensitivity(str, Enum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    SPECIAL_CATEGORY = "special_category"


class RedactionReason(str, Enum):
    USER_REQUEST = "user_request"
    RETENTION_POLICY = "retention_policy"
    CORRECTION = "correction"
    LEGAL = "legal"


@dataclass
class Provenance:
    """Herkunft einer Aussage. Ohne Herkunft darf nichts in den Bestand."""

    source_type: SourceType
    source_ref: str | None = None
    captured_at: datetime | None = None
    extracted_by: str | None = None
    verbatim: str | None = None

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {"source_type": self.source_type.value}
        if self.source_ref is not None:
            document["source_ref"] = self.source_ref
        if self.captured_at is not None:
            document["captured_at"] = _iso(self.captured_at)
        if self.extracted_by is not None:
            document["extracted_by"] = self.extracted_by
        if self.verbatim is not None:
            document["verbatim"] = self.verbatim
        return document


@dataclass
class Redaction:
    """Dokumentierter Widerruf; ein Grabstein bleibt erhalten."""

    redacted_at: datetime
    reason: RedactionReason
    cascade: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "redacted_at": _iso(self.redacted_at),
            "reason": self.reason.value,
        }
        if self.cascade:
            document["cascade"] = list(self.cascade)
        return document


@dataclass
class Assertion:
    """Eine einzelne Aussage über die Person."""

    id: str
    kind: Kind
    statement: str
    provenance: Provenance
    recorded_at: datetime
    status: Status = Status.ACTIVE

    structured: dict[str, Any] | None = None
    confidence: float | None = None
    valid_from: datetime | None = None
    expires_at: datetime | None = None
    last_confirmed_at: datetime | None = None
    supersedes: list[str] = field(default_factory=list)
    superseded_by: str | None = None
    derived_from: list[str] = field(default_factory=list)
    sensitivity: Sensitivity = Sensitivity.NORMAL
    tags: list[str] = field(default_factory=list)
    redaction: Redaction | None = None
    disputed_with: list[str] = field(default_factory=list)
    """IDs der Aussagen, zu denen ein ungeklärter Widerspruch besteht."""

    def is_usable(self, at: datetime | None = None) -> bool:
        """Nur aktive und zeitlich gültige Aussagen sind ungeprüft verwendbar."""
        at = at or now()
        if self.status is not Status.ACTIVE:
            return False
        if self.expires_at is not None and at >= self.expires_at:
            return False
        if self.valid_from is not None and at < self.valid_from:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value,
            "statement": self.statement,
            "provenance": self.provenance.to_dict(),
            "recorded_at": _iso(self.recorded_at),
            "status": self.status.value,
        }
        if self.structured is not None:
            document["structured"] = self.structured
        if self.confidence is not None:
            document["confidence"] = self.confidence
        for key in ("valid_from", "expires_at", "last_confirmed_at"):
            value = getattr(self, key)
            if value is not None:
                document[key] = _iso(value)
        if self.supersedes:
            document["supersedes"] = list(self.supersedes)
        if self.superseded_by is not None:
            document["superseded_by"] = self.superseded_by
        if self.derived_from:
            document["derived_from"] = list(self.derived_from)
        if self.sensitivity is not Sensitivity.NORMAL:
            document["sensitivity"] = self.sensitivity.value
        if self.tags:
            document["tags"] = list(self.tags)
        if self.disputed_with:
            document["disputed_with"] = list(self.disputed_with)
        if self.redaction is not None:
            document["redaction"] = self.redaction.to_dict()
        return document


@dataclass
class SelfModel:
    """Das vollständige, exportierbare Modell einer Person.

    Ein Dokument, das ausschließlich den bisherigen 0.1-Vertrag nutzt, bleibt
    als 0.1 exportierbar. Sobald eine Aussage den neuen Konfliktvertrag nutzt,
    kennzeichnet der Export sich als 0.2. So bleiben alte Verbraucher
    kompatibel, ohne neue Felder unter einer alten Versionsnummer zu verstecken.
    """

    subject_id: str
    created_at: datetime
    assertions: list[Assertion] = field(default_factory=list)
    schema_version: str = LEGACY_SCHEMA_VERSION

    def effective_schema_version(self) -> str:
        if any(
            assertion.status is Status.DISPUTED or assertion.disputed_with
            for assertion in self.assertions
        ):
            return SCHEMA_VERSION
        return self.schema_version

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.effective_schema_version(),
            "subject_id": self.subject_id,
            "created_at": _iso(self.created_at),
            "exported_at": _iso(now()),
            "assertions": [assertion.to_dict() for assertion in self.assertions],
        }


__all__ = [
    "SCHEMA_VERSION",
    "LEGACY_SCHEMA_VERSION",
    "Assertion",
    "ensure_aware",
    "Kind",
    "Provenance",
    "Redaction",
    "RedactionReason",
    "SelfModel",
    "Sensitivity",
    "SourceType",
    "Status",
    "now",
    "asdict",
]
