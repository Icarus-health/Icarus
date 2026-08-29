"""Die Selbstmodell-Logik.

Hier liegt das, was keine fremde Memory-Bibliothek liefert: Ersetzung statt
Überschreiben, zeitliche Gültigkeit und ein Widerruf, der abgeleitete Aussagen
mitnimmt.

Bewusst frei von Abhängigkeiten zu cognee. Die Persistenz liegt hinter dem
Protokoll `Backend` (siehe backends.py) — dadurch ist diese Logik ohne
Netzzugang, ohne LLM und ohne Datenbank testbar, und die Ablage bleibt
austauschbar. Das ist Säule 2 in praktischer Form.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Iterable, Protocol

from .model import (
    Assertion,
    ensure_aware,
    Kind,
    Provenance,
    Redaction,
    RedactionReason,
    SelfModel,
    Sensitivity,
    SourceType,
    Status,
    now,
)


class Backend(Protocol):
    """Persistenz für Aussagen. Absichtlich klein gehalten."""

    def put(self, assertion: Assertion) -> None: ...

    def get(self, assertion_id: str) -> Assertion | None: ...

    def all(self) -> list[Assertion]: ...

    def search(self, query: str, limit: int) -> list[Assertion]: ...


class ConflictError(Exception):
    """Verletzung einer Modellregel."""


def _new_id() -> str:
    return f"a-{uuid.uuid4().hex[:12]}"


class SelfModelStore:
    """Verwaltet das Selbstmodell einer Person über einem Backend."""

    def __init__(self, backend: Backend, subject_id: str) -> None:
        self._backend = backend
        self._subject_id = subject_id
        self._created_at = now()

    # -- Schreiben ---------------------------------------------------------

    def record(
        self,
        statement: str,
        kind: Kind,
        provenance: Provenance,
        *,
        confidence: float | None = None,
        valid_from: datetime | None = None,
        expires_at: datetime | None = None,
        supersedes: Iterable[str] = (),
        derived_from: Iterable[str] = (),
        sensitivity: Sensitivity = Sensitivity.NORMAL,
        tags: Iterable[str] = (),
        structured: dict | None = None,
        at: datetime | None = None,
    ) -> Assertion:
        """Nimmt eine neue Aussage auf.

        Ersetzte Aussagen werden nicht überschrieben, sondern auf
        `superseded` gesetzt und zeigen auf die neue zurück.
        """
        at = ensure_aware(at) or now()
        # Zeitangaben aus der API tragen oft keine Zone; ohne Normalisierung
        # scheitert später jeder Vergleich mit TypeError.
        valid_from = ensure_aware(valid_from)
        expires_at = ensure_aware(expires_at)
        supersedes = list(supersedes)
        derived_from = list(derived_from)

        # Ersetzte und abgeleitete Aussagen müssen existieren, sonst bricht die
        # Kette und das Modell wird unprüfbar.
        for ref in (*supersedes, *derived_from):
            if self._backend.get(ref) is None:
                raise ConflictError(f"Unbekannte Aussage referenziert: {ref}")

        assertion = Assertion(
            id=_new_id(),
            kind=kind,
            statement=statement,
            provenance=provenance,
            recorded_at=at,
            status=Status.ACTIVE,
            confidence=confidence,
            valid_from=valid_from,
            expires_at=expires_at,
            status_changed_at=at,
            supersedes=supersedes,
            derived_from=derived_from,
            sensitivity=sensitivity,
            tags=list(tags),
            structured=structured,
        )
        self._backend.put(assertion)

        for ref in supersedes:
            old = self._backend.get(ref)
            assert old is not None  # oben geprüft
            if old.status is Status.REDACTED:
                raise ConflictError(
                    f"Aussage {ref} wurde widerrufen und kann nicht ersetzt werden."
                )
            old.status = Status.SUPERSEDED
            old.status_changed_at = at
            old.superseded_by = assertion.id
            self._backend.put(old)

        return assertion

    def confirm(self, assertion_id: str, at: datetime | None = None) -> Assertion:
        """Bestätigt eine Aussage erneut.

        Eine abgelaufene Aussage wird dadurch wieder aktiv — das ist der
        Mechanismus, über den das Modell aktuell bleibt, ohne zu raten.
        """
        at = ensure_aware(at) or now()
        assertion = self._require(assertion_id)
        if assertion.status in (Status.REDACTED, Status.RETRACTED):
            raise ConflictError(
                f"Aussage {assertion_id} ist {assertion.status.value} "
                "und kann nicht bestätigt werden."
            )
        if assertion.status is Status.SUPERSEDED:
            raise ConflictError(
                f"Aussage {assertion_id} wurde durch {assertion.superseded_by} "
                "ersetzt. Stattdessen die ersetzende Aussage bestätigen."
            )
        assertion.last_confirmed_at = at
        if assertion.status is Status.EXPIRED:
            assertion.status = Status.ACTIVE
            assertion.status_changed_at = at
            # Ein bereits überschrittenes Ablaufdatum würde die Aussage sofort
            # wieder unbenutzbar machen.
            if assertion.expires_at is not None and at >= assertion.expires_at:
                assertion.expires_at = None
        self._backend.put(assertion)
        return assertion

    def retract(self, assertion_id: str, at: datetime | None = None) -> Assertion:
        """Die Aussage war inhaltlich falsch.

        Unterschied zu `redact`: hier stimmt der Inhalt nicht. Bei `redact`
        war er womöglich richtig, soll aber weg.
        """
        at = ensure_aware(at) or now()
        assertion = self._require(assertion_id)
        # Derselbe fachliche Vorgang darf nicht wie ein neuer Statuswechsel
        # aussehen. Connector-Retries und wiederholte API-Aufrufe sind normal;
        # sie dürfen das Freshness-Fenster einer Entscheidung nicht erneuern.
        if assertion.status is Status.RETRACTED:
            return assertion
        assertion.status = Status.RETRACTED
        assertion.status_changed_at = at
        assertion.last_confirmed_at = None
        self._backend.put(assertion)
        return assertion

    def redact(
        self,
        assertion_id: str,
        reason: RedactionReason = RedactionReason.USER_REQUEST,
        at: datetime | None = None,
    ) -> list[Assertion]:
        """Löscht eine Aussage samt allem, was daraus abgeleitet wurde.

        Das ist der Kern des Widerrufspfads. Wird eine Quelle gelöscht, darf
        das darauf Aufgebaute nicht stehen bleiben — sonst überlebt die
        Information ihre eigene Löschung.

        Der Inhalt wird entfernt, ein Grabstein bleibt, damit eine Lücke als
        Lücke erkennbar ist statt als Nie-dagewesen.
        """
        at = ensure_aware(at) or now()
        self._require(assertion_id)

        affected = self._descendants(assertion_id)
        cascade = sorted(affected - {assertion_id})

        for current_id in sorted(affected):
            assertion = self._require(current_id)
            # Ein Grabstein ist endgültig. Derselbe Löschvorgang kann durch
            # Retries erneut eintreffen, darf aber weder den fachlichen
            # Statuswechsel noch den ursprünglichen Redaction-Zeitpunkt
            # umdatieren. Noch nicht redigierte Nachfahren werden weiterhin
            # vom Kaskadenpfad erfasst.
            if assertion.status is Status.REDACTED:
                continue
            assertion.statement = "Entfernt auf Wunsch der Person."
            assertion.structured = None
            assertion.status = Status.REDACTED
            assertion.status_changed_at = at
            assertion.confidence = None
            assertion.tags = []
            assertion.provenance = Provenance(
                source_type=assertion.provenance.source_type
            )
            assertion.redaction = Redaction(
                redacted_at=at,
                reason=reason,
                cascade=cascade if current_id == assertion_id else [],
            )
            self._backend.put(assertion)

        return [self._require(i) for i in sorted(affected)]

    def dispute(
        self, *assertion_ids: str, at: datetime | None = None
    ) -> list[Assertion]:
        """Markiert zwei oder mehr Aussagen als einander widersprechend.

        Löst nichts auf — das ist der Punkt. Bis hierher gingen zwei
        widersprüchliche Aussagen beide als `active` in den Prompt, und das
        Modell hat sich für eine entschieden, ohne dass jemand es merkte. Ein
        Gedächtnis, das so etwas tut, ist selbstbewusst falsch, und das ist
        schlimmer als eine offene Frage.

        Der Verweis wird **gegenseitig** gesetzt. Ein einseitiger würde
        bedeuten, dass eine Seite des Widerspruchs weiterhin unbemerkt als
        Gegenwart auftritt.

        Aufgelöst wird über die bestehenden Wege: `record(supersedes=…)`, wenn
        eine Aussage die andere ablöst, oder `retract()`, wenn eine schlicht
        falsch war. Beide sind protokolliert; ein eigener „Streit beilegen"-Pfad
        wäre ein dritter Weg, Bestand zu ändern, und davon gibt es genug.
        """
        at = ensure_aware(at) or now()
        if len(assertion_ids) < 2:
            raise ConflictError("Ein Widerspruch braucht mindestens zwei Aussagen.")

        betroffen = [self._require(i) for i in assertion_ids]
        for assertion in betroffen:
            if assertion.status in (Status.REDACTED, Status.RETRACTED):
                raise ConflictError(
                    f"Aussage {assertion.id} ist {assertion.status.value} und "
                    "steht in keinem Streit mehr."
                )

        for assertion in betroffen:
            geaendert = False
            # Ein bereits offener Streit bleibt derselbe Status. Neue
            # widersprechende Evidence erweitert die Relation unten, ist aber
            # kein zweiter Statuswechsel und erneuert deshalb nicht dessen
            # Freshness-Zeitpunkt.
            if assertion.status is not Status.DISPUTED:
                assertion.status = Status.DISPUTED
                assertion.status_changed_at = at
                geaendert = True
            for other in betroffen:
                if other.id != assertion.id and other.id not in assertion.disputed_with:
                    assertion.disputed_with.append(other.id)
                    geaendert = True
            if geaendert:
                self._backend.put(assertion)
        return betroffen

    def disputed(self) -> list[Assertion]:
        return [a for a in self._backend.all() if a.status is Status.DISPUTED]

    # -- Lesen -------------------------------------------------------------

    def alles(self) -> list[Assertion]:
        """Der ganze Bestand, auch Ersetztes und Widerrufenes.

        Bewusst getrennt von `usable()` und `recall()`, und bewusst mit einem
        Namen, der nach „alles“ klingt: Wer das hier ruft, bekommt auch, was
        nicht mehr gilt, und muss selbst wissen, warum er das will.

        Der eine legitime Grund ist die Rückschau. Dass eine Entscheidung
        einmal getroffen wurde, bleibt wahr, auch wenn sie widerrufen ist —
        und ein Gedächtnis, das nichts löscht, muss das zeigen können.
        """
        return list(self._backend.all())

    def usable(self, at: datetime | None = None) -> list[Assertion]:
        """Alle Aussagen, die gerade ungeprüft verwendet werden dürfen.

        Läuft nebenbei den Ablauf nach: Aussagen, deren `expires_at`
        überschritten ist, werden auf `expired` gesetzt.
        """
        at = at or now()
        result = []
        for assertion in self._backend.all():
            if (
                assertion.status is Status.ACTIVE
                and assertion.expires_at is not None
                and at >= assertion.expires_at
            ):
                assertion.status = Status.EXPIRED
                # Der Status fällt am fachlichen Ablaufzeitpunkt, nicht erst
                # dann, wenn irgendein späterer Lesezugriff ihn materialisiert.
                assertion.status_changed_at = assertion.expires_at
                self._backend.put(assertion)
            if assertion.is_usable(at):
                result.append(assertion)
        return result

    def recall(self, query: str, limit: int = 10, at: datetime | None = None) -> list[Assertion]:
        """Sucht Aussagen, gibt aber nur verwendbare zurück.

        Der Filter ist der Punkt: ein Treffer, der ersetzt oder abgelaufen ist,
        darf nicht als gegenwärtige Wahrheit zurückkommen.
        """
        at = at or now()
        usable_ids = {a.id for a in self.usable(at)}
        return [a for a in self._backend.search(query, limit) if a.id in usable_ids]

    def history(self, assertion_id: str) -> list[Assertion]:
        """Die Ersetzungskette einer Aussage, älteste zuerst."""
        chain: list[Assertion] = []
        seen: set[str] = set()

        # Zum Anfang der Kette zurücklaufen.
        current = self._require(assertion_id)
        while current.supersedes:
            previous = self._backend.get(current.supersedes[0])
            if previous is None or previous.id in seen:
                break
            seen.add(previous.id)
            current = previous

        # Und von dort vorwärts.
        seen = {current.id}
        chain.append(current)
        while current.superseded_by:
            nxt = self._backend.get(current.superseded_by)
            if nxt is None or nxt.id in seen:
                break
            seen.add(nxt.id)
            chain.append(nxt)
            current = nxt
        return chain

    def export(self) -> SelfModel:
        """Das vollständige Modell, passend zu schema/self-model.schema.json."""
        return SelfModel(
            subject_id=self._subject_id,
            created_at=self._created_at,
            assertions=sorted(self._backend.all(), key=lambda a: a.recorded_at),
        )

    def shareable(
        self, max_sensitivity: Sensitivity = Sensitivity.NORMAL, at: datetime | None = None
    ) -> list[Assertion]:
        """Aussagen, die an ein externes Modell übergeben werden dürfen.

        Heute nur eine Filterung nach Schutzbedarf. Die eigentliche
        Durchsetzung gehört in die Policy-Schicht (docs/03-delegation.md);
        diese Methode ist ihr Ansatzpunkt, nicht ihr Ersatz.
        """
        order = {
            Sensitivity.NORMAL: 0,
            Sensitivity.SENSITIVE: 1,
            Sensitivity.SPECIAL_CATEGORY: 2,
        }
        ceiling = order[max_sensitivity]
        return [a for a in self.usable(at) if order[a.sensitivity] <= ceiling]

    # -- Intern ------------------------------------------------------------

    def _require(self, assertion_id: str) -> Assertion:
        assertion = self._backend.get(assertion_id)
        if assertion is None:
            raise ConflictError(f"Unbekannte Aussage: {assertion_id}")
        return assertion

    def _descendants(self, root_id: str) -> set[str]:
        """Die Aussage selbst plus alles, was transitiv daraus abgeleitet wurde."""
        children: dict[str, list[str]] = defaultdict(list)
        for assertion in self._backend.all():
            for parent in assertion.derived_from:
                children[parent].append(assertion.id)

        found = {root_id}
        stack = [root_id]
        while stack:
            current = stack.pop()
            for child in children.get(current, ()):
                if child not in found:
                    found.add(child)
                    stack.append(child)
        return found


__all__ = ["Backend", "ConflictError", "SelfModelStore"]
