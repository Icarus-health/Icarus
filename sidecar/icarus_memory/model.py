"""Datenmodell des Selbstmodells.

Spiegelt schema/self-model.schema.json. Das Schema bleibt die Referenz; diese
Klassen sind die Laufzeitform davon. Bewusst ohne Abhängigkeit zu cognee: das
Modell muss die Ablage überleben können.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any

SCHEMA_VERSION = "0.1.0"


def now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime | None) -> datetime | None:
    """Hängt einem zeitzonenlosen Zeitpunkt die lokale Zone an.

    Über HTTP kommen Zeitangaben oft ohne Zone herein („2026-08-05T23:59:00"),
    intern wird aber durchgehend mit zeitzonenbehafteten Werten gerechnet. Ein
    Vergleich der beiden wirft `TypeError: can't compare offset-naive and
    offset-aware datetimes` — und zwar erst zur Laufzeit, an einer beliebigen
    späteren Stelle.

    Deshalb wird an jeder Eingangsstelle normalisiert, statt an jeder
    Vergleichsstelle zu prüfen. Die lokale Zone ist die richtige Annahme: Wer
    „fällig am 5.8. um 23:59" tippt, meint seine eigene Uhr.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.astimezone()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Kind(str, Enum):
    """Zeitliche Natur einer Aussage.

    Die Unterscheidung ist der Kern der Überprüfbarkeit: ein System, das
    "wohnt in Berlin" und "ist heute erkältet" gleich behandelt, erzeugt
    Widersprüche statt Wissen.
    """

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
    """Zwei Aussagen widersprechen sich, und keine ist entschieden.

    Das Ziel ist ausdrücklich **nicht**, den Widerspruch aufzulösen. Es ist,
    ihn sichtbar zu machen, statt das Modell zwischen zwei gleichrangigen
    Behauptungen raten zu lassen — genau der Fall, in dem ein Gedächtnis
    selbstbewusst falsch wird.

    Eine strittige Aussage bleibt lesbar und bleibt in der Ersetzungskette. Sie
    ist nicht `usable()`: Sie darf im Kontext auftauchen, aber getrennt und mit
    ihrem Gegenstück, damit erkennbar ist, dass hier etwas offen ist. Wer den
    Widerspruch auflöst, ersetzt (`supersedes`) oder zieht zurück
    (`retract`) — beides sind bestehende, protokollierte Wege.
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


#: Quellenarten, deren Text jemand anderes geschrieben hat.
#:
#: Steht hier und nicht in einer der Ansichten, weil es eine Eigenschaft der
#: Quellenart ist und keine Frage der Darstellung. Suche und Personenansicht
#: müssen dieselbe Antwort geben — zwei Listen driften auseinander, und dann
#: ist derselbe Text an einer Stelle gerahmt und an der anderen nicht.
#:
#: `inference` fehlt bewusst: Was Icarus selbst gefolgert hat, ist nicht fremd,
#: sondern eigene Arbeit — und wird über die Herleitung geprüft, nicht über
#: einen Rahmen.
FREMDE_HERKUNFT = {
    SourceType.EMAIL.value,
    SourceType.CALENDAR.value,
    SourceType.DOCUMENT.value,
    SourceType.WEB.value,
    SourceType.TOOL_OUTPUT.value,
}


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
    """Woher die Aussage stammt.

    Ohne dieses Objekt darf nichts ins Modell — eine Aussage ohne Herkunft ist
    nicht überprüfbar.
    """

    source_type: SourceType
    source_ref: str | None = None
    captured_at: datetime | None = None
    extracted_by: str | None = None
    verbatim: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"source_type": self.source_type.value}
        if self.source_ref is not None:
            d["source_ref"] = self.source_ref
        if self.captured_at is not None:
            d["captured_at"] = _iso(self.captured_at)
        if self.extracted_by is not None:
            d["extracted_by"] = self.extracted_by
        if self.verbatim is not None:
            d["verbatim"] = self.verbatim
        return d


@dataclass
class Redaction:
    """Dokumentierter Widerruf. Der Eintrag bleibt als Grabstein bestehen."""

    redacted_at: datetime
    reason: RedactionReason
    cascade: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "redacted_at": _iso(self.redacted_at),
            "reason": self.reason.value,
        }
        if self.cascade:
            d["cascade"] = list(self.cascade)
        return d


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
    """Aussagen, zu denen diese im Widerspruch steht.

    Gegenseitig gepflegt: Steht A im Streit mit B, trägt auch B die A. Ein
    einseitiger Verweis würde bedeuten, dass eine Seite des Widerspruchs
    unbemerkt als Gegenwart auftreten kann — und das ist genau der Schaden,
    den der Status verhindern soll.
    """

    def is_usable(self, at: datetime | None = None) -> bool:
        """Darf die Aussage ungeprüft verwendet werden?

        Nur aktive Aussagen, deren Ablaufdatum noch nicht erreicht ist und
        deren Gültigkeit bereits begonnen hat.
        """
        at = at or now()
        if self.status is not Status.ACTIVE:
            return False
        if self.expires_at is not None and at >= self.expires_at:
            return False
        if self.valid_from is not None and at < self.valid_from:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value,
            "statement": self.statement,
            "provenance": self.provenance.to_dict(),
            "recorded_at": _iso(self.recorded_at),
            "status": self.status.value,
        }
        if self.structured is not None:
            d["structured"] = self.structured
        if self.confidence is not None:
            d["confidence"] = self.confidence
        for key in ("valid_from", "expires_at", "last_confirmed_at"):
            value = getattr(self, key)
            if value is not None:
                d[key] = _iso(value)
        if self.supersedes:
            d["supersedes"] = list(self.supersedes)
        if self.superseded_by is not None:
            d["superseded_by"] = self.superseded_by
        if self.derived_from:
            d["derived_from"] = list(self.derived_from)
        if self.sensitivity is not Sensitivity.NORMAL:
            d["sensitivity"] = self.sensitivity.value
        if self.tags:
            d["tags"] = list(self.tags)
        if self.disputed_with:
            d["disputed_with"] = list(self.disputed_with)
        if self.redaction is not None:
            d["redaction"] = self.redaction.to_dict()
        return d


@dataclass
class SelfModel:
    """Das vollständige Modell einer Person."""

    subject_id: str
    created_at: datetime
    assertions: list[Assertion] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "subject_id": self.subject_id,
            "created_at": _iso(self.created_at),
            "exported_at": _iso(now()),
            "assertions": [a.to_dict() for a in self.assertions],
        }


__all__ = [
    "FREMDE_HERKUNFT",
    "SCHEMA_VERSION",
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
