"""Säule 4: kontrollierte Delegation.

Jede Aktion, die Icarus ausführen kann, läuft hier durch. Es gibt keinen Weg
an dieser Schicht vorbei — der Agent führt nichts selbst aus, er stellt Anträge.

Die Reihenfolge ist die zentrale Entscheidung des Projekts: Ausführung kommt
*nach* dem Freigabemodell. Ein Assistent, der den Rechner bedient, ohne dass
jemand zusieht, ist kein Feature, sondern ein Risiko mit Oberfläche.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from .model import Kind, Status, now


class ActionClass(str, Enum):
    """Wie weit eine Aktion reicht."""

    READ = "read"
    """Verändert nichts. Kalender lesen, Websuche, Datei öffnen."""

    WRITE_LOCAL = "write_local"
    """Verändert eigene Daten. Notiz anlegen, Selbstmodell ergänzen."""

    OUTWARD = "outward"
    """Verlässt das eigene System. Mail senden, Termin mit Gästen, Bestellung.

    Die Grenze zu WRITE_LOCAL ist die wichtigste im ganzen Modell: Sobald etwas
    bei Dritten sichtbar wird, lässt es sich nicht mehr wirklich zurücknehmen.
    """


class ApprovalLevel(str, Enum):
    AUTO = "auto"
    NOTIFY = "notify"
    CONFIRM = "confirm"
    CONFIRM_STRICT = "confirm_strict"
    DENY = "deny"


DEFAULT_LEVELS: dict[ActionClass, ApprovalLevel] = {
    ActionClass.READ: ApprovalLevel.AUTO,
    ActionClass.WRITE_LOCAL: ApprovalLevel.NOTIFY,
    ActionClass.OUTWARD: ApprovalLevel.CONFIRM_STRICT,
}


@dataclass(frozen=True)
class Decision:
    """Das Ergebnis der Prüfung."""

    level: ApprovalLevel
    action_class: ActionClass
    reasons: list[str] = field(default_factory=list)

    @property
    def needs_approval(self) -> bool:
        return self.level in (ApprovalLevel.CONFIRM, ApprovalLevel.CONFIRM_STRICT)

    @property
    def denied(self) -> bool:
        return self.level is ApprovalLevel.DENY


@dataclass
class PendingApproval:
    """Ein Antrag, der auf die Entscheidung des Nutzers wartet."""

    id: str
    tool: str
    arguments: dict[str, Any]
    decision: Decision
    dry_run: str
    """Was genau passieren würde — vollständig, nicht zusammengefasst."""

    requested_at: datetime
    expires_at: datetime
    confirmation_phrase: str | None = None
    """Bei CONFIRM_STRICT: Text, den der Nutzer bestätigen muss."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "arguments": self.arguments,
            "action_class": self.decision.action_class.value,
            "level": self.decision.level.value,
            "reasons": self.decision.reasons,
            "dry_run": self.dry_run,
            "requested_at": self.requested_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "confirmation_phrase": self.confirmation_phrase,
        }


class PolicyError(Exception):
    """Eine Aktion wurde abgelehnt oder eine Freigabe war ungültig."""


class Policy:
    """Entscheidet, ob und wie eine Aktion ausgeführt werden darf.

    Freigaben gelten **einmal**. Nicht für die Sitzung, nicht für „ähnliche
    Fälle". Wer Dauerfreigaben will, hebt die Stufe für ein Werkzeug bewusst und
    benannt an — nicht implizit durch wiederholtes Klicken.
    """

    def __init__(
        self,
        levels: dict[ActionClass, ApprovalLevel] | None = None,
        overrides: dict[str, ApprovalLevel] | None = None,
        ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        self._levels = dict(DEFAULT_LEVELS)
        if levels:
            self._levels.update(levels)
        self._overrides = dict(overrides or {})
        self._ttl = ttl
        self._pending: dict[str, PendingApproval] = {}

    # -- Entscheidung ------------------------------------------------------

    #: Anhebung, wenn fremder Inhalt im Kontext steht.
    _ESCALATION: dict[ApprovalLevel, ApprovalLevel] = {
        ApprovalLevel.AUTO: ApprovalLevel.AUTO,
        ApprovalLevel.NOTIFY: ApprovalLevel.CONFIRM,
        ApprovalLevel.CONFIRM: ApprovalLevel.CONFIRM_STRICT,
        ApprovalLevel.CONFIRM_STRICT: ApprovalLevel.CONFIRM_STRICT,
    }

    def decide(
        self,
        tool: str,
        action_class: ActionClass,
        arguments: dict[str, Any],
        constraints: list[str] | None = None,
        tainted: bool = False,
    ) -> Decision:
        reasons: list[str] = []
        level = self._overrides.get(tool, self._levels[action_class])
        if tool in self._overrides:
            reasons.append(f"Für '{tool}' ist die Stufe ausdrücklich auf {level.value} gesetzt.")

        # Constraints aus dem Selbstmodell schlagen alles andere. Sie sind die
        # Grenzen, die der Nutzer selbst gezogen hat.
        for constraint in constraints or []:
            if self._violates(constraint, tool, arguments):
                return Decision(
                    ApprovalLevel.DENY,
                    action_class,
                    [f"Verstößt gegen eine gesetzte Grenze: {constraint!r}"],
                )

        # Nach fremdem Inhalt ist nicht mehr feststellbar, ob eine Absicht vom
        # Nutzer stammt oder aus dem gelesenen Text. Reines Lesen bleibt frei —
        # alles mit Wirkung wird vorgelegt. Diese Ebene verlässt sich nicht
        # darauf, dass das Modell den Angriff erkennt.
        if tainted and action_class is not ActionClass.READ:
            escalated = self._ESCALATION[level]
            if escalated is not level:
                reasons.append(
                    "Zuvor wurden fremde Inhalte gelesen. Solange die im Kontext "
                    "stehen, wird jede wirksame Aktion vorgelegt."
                )
                level = escalated

        return Decision(level, action_class, reasons)

    @staticmethod
    def _violates(constraint: str, tool: str, arguments: dict[str, Any]) -> bool:
        """Prüft, ob eine gesetzte Grenze die Aktion verbietet.

        Bewusst wörtlich: Die Grenze nennt entweder das Werkzeug oder einen
        Begriff, der in den Argumenten vorkommt. Eine Auslegung per Modell wäre
        genau die Unschärfe, die man bei harten Grenzen nicht will — ein
        Constraint muss nachvollziehbar greifen oder gar nicht.
        """
        haystack = f"{tool} {' '.join(str(v) for v in arguments.values())}".casefold()
        needle = constraint.casefold()
        if tool.casefold() in needle:
            return True
        # Inhaltswörter der Grenze gegen die Argumente prüfen.
        words = [w.strip(".,;:!?\"'") for w in needle.split() if len(w) > 4]
        return any(word in haystack for word in words)

    # -- Freigaben ---------------------------------------------------------

    def request(
        self,
        tool: str,
        arguments: dict[str, Any],
        decision: Decision,
        dry_run: str,
        at: datetime | None = None,
    ) -> PendingApproval:
        at = at or now()
        strict = decision.level is ApprovalLevel.CONFIRM_STRICT
        approval = PendingApproval(
            id=f"ap-{uuid.uuid4().hex[:12]}",
            tool=tool,
            arguments=arguments,
            decision=decision,
            dry_run=dry_run,
            requested_at=at,
            expires_at=at + self._ttl,
            # Bei außenwirksamen Aktionen muss der Nutzer den Kern wiederholen.
            # Ein Klick auf "OK" ist kein Beleg dafür, dass jemand gelesen hat,
            # an wen die Mail geht.
            confirmation_phrase=self._phrase(tool, arguments) if strict else None,
        )
        self._pending[approval.id] = approval
        return approval

    @staticmethod
    def _phrase(tool: str, arguments: dict[str, Any]) -> str:
        """Der Kerninhalt, den der Nutzer wiederholen muss."""
        for key in ("to", "recipient", "empfaenger", "url", "path"):
            if key in arguments:
                return str(arguments[key])
        return tool

    def pending(self, at: datetime | None = None) -> list[PendingApproval]:
        at = at or now()
        self._expire(at)
        return sorted(self._pending.values(), key=lambda a: a.requested_at)

    def get(self, approval_id: str, at: datetime | None = None) -> PendingApproval:
        self._expire(at or now())
        approval = self._pending.get(approval_id)
        if approval is None:
            raise PolicyError(f"Unbekannte oder abgelaufene Freigabe: {approval_id}")
        return approval

    def grant(
        self,
        approval_id: str,
        confirmation: str | None = None,
        at: datetime | None = None,
    ) -> PendingApproval:
        """Erteilt eine Freigabe und verbraucht sie.

        Stimmt die Bestätigung nicht, bleibt der Antrag **bestehen** und es
        fliegt ein PolicyError. Ein Vertipper darf die Freigabe nicht
        vernichten — sonst müsste der Nutzer den ganzen Vorgang wiederholen und
        gewöhnt sich an, Bestätigungen wegzuklicken.
        """
        at = at or now()
        approval = self.get(approval_id, at)

        if approval.confirmation_phrase is not None:
            if (confirmation or "").strip() != approval.confirmation_phrase.strip():
                raise PolicyError(
                    "Bestätigung stimmt nicht überein. Erwartet: "
                    f"{approval.confirmation_phrase!r}"
                )

        del self._pending[approval_id]
        return approval

    def reject(self, approval_id: str, at: datetime | None = None) -> PendingApproval:
        """Verweigert eine Freigabe und verbraucht sie."""
        approval = self.get(approval_id, at or now())
        del self._pending[approval_id]
        return approval

    def _expire(self, at: datetime) -> None:
        for key in [k for k, v in self._pending.items() if at >= v.expires_at]:
            del self._pending[key]


def constraints_from_store(store: Any, at: datetime | None = None) -> list[str]:
    """Zieht die harten Grenzen aus dem Selbstmodell.

    Aussagen mit `kind: constraint` sind bindend — sie stammen vom Nutzer und
    schlagen jede Anweisung, auch eine spätere.
    """
    return [
        a.statement
        for a in store.usable(at)
        if a.kind is Kind.CONSTRAINT and a.status is Status.ACTIVE
    ]


__all__ = [
    "ActionClass",
    "ApprovalLevel",
    "DEFAULT_LEVELS",
    "Decision",
    "PendingApproval",
    "Policy",
    "PolicyError",
    "constraints_from_store",
]
