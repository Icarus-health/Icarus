"""Vorschläge: was die Verdichtung dem Menschen vorlegt.

Die eine Regel, an der dieses ganze Modul hängt, steht in
docs/08-gedaechtnisschichten.md:

    Verdichtung schlägt vor. Sie schreibt nicht.

Ein System, das aus Mails und Notizen stillschweigend Fakten über eine Person
ableitet und in den Bestand schreibt, hat wieder ein Gedächtnis, dem niemand
zusehen kann — genau das Versagen, gegen das dieses Projekt gebaut ist. Die
Roadmap hat den Punkt von Anfang an als kritisch markiert, und daran ändert
sich nichts, wenn die Bequemlichkeit lockt.

Deshalb gibt es diese Schicht. Ein Vorschlag ist eine **Behauptung auf Probe**:
formuliert, belegt, begründet — und ohne Wirkung, bis ein Mensch zustimmt.

## Warum drei Arten

Nicht jeder Vorschlag braucht ein Modell, und das ist wichtig. Ein
Gedächtniskern, dessen Pflege einen Anbieter voraussetzt, ist keiner.

* `assertion` — „daraus folgt eine Aussage über dich". Braucht ein Modell.
* `confirmation` — „das hier ist alt, gilt es noch?". Reine Regel, kein Modell.
* `conflict` — „diese beiden widersprechen sich womöglich". Reine Regel.

Die letzten beiden tragen den Alltag: Sie halten den Bestand ehrlich, auch wenn
nie ein Schlüssel eingetragen wird.

## Belege sind Pflicht

Jeder Vorschlag trägt seine `evidence` — welche Episode, welche Stelle im
Wortlaut. Ohne das wäre die Verdichtung eine Blackbox, die Behauptungen
ausspuckt, und der Nutzer müsste raten, worauf sie beruhen. Ein Vorschlag ohne
Beleg wird abgewiesen, nicht gespeichert.
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

from .migrations import (
    IndexContract,
    Migration,
    run_migrations,
    validate_legacy_or_empty,
    verify_schema,
)
from .model import Kind, Sensitivity, ensure_aware, now


class ProposalKind(str, Enum):
    ASSERTION = "assertion"
    """Aus Episoden abgeleitete Aussage über die Person. Braucht ein Modell."""

    CONFIRMATION = "confirmation"
    """Eine bestehende Aussage ist über ihren Horizont. Gilt sie noch?

    Kein Modell nötig — `currency.py` weiß, was alt ist. Das ist der
    Mechanismus, über den ein Bestand aktuell bleibt, ohne dass jemand rät.
    """

    CONFLICT = "conflict"
    """Zwei Aussagen widersprechen sich womöglich.

    Ausdrücklich ein **Kandidat**, kein Urteil. Die Regel findet Ähnlichkeit,
    nicht Widerspruch — entscheiden muss der Mensch, und erst seine Zustimmung
    setzt `disputed`.
    """


class ProposalState(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    """Ein neuerer Vorschlag hat denselben Punkt abgedeckt.

    Nötig, weil ein zweiter Verdichtungslauf denselben Sachverhalt erneut
    vorlegen kann. Ohne diesen Zustand wüchse die Schlange, bis niemand mehr
    hineinsieht — und eine Schlange, in die niemand sieht, ist dasselbe wie
    keine Kontrolle.
    """


@dataclass
class Evidence:
    """Worauf ein Vorschlag beruht.

    `quote` ist der wörtliche Ausschnitt, nicht eine Zusammenfassung. Wer prüft,
    ob ein Vorschlag stimmt, will die Stelle sehen und nicht eine zweite
    Interpretation davon.
    """

    episode_id: str
    quote: str = ""
    digest: str = ""
    """Digest der Episode zum Zeitpunkt des Vorschlags.

    Damit ist feststellbar, ob sich die Quelle seit dem Vorschlag geändert hat —
    die Voraussetzung für die Neuprüfung, die der Gedächtnis-Kontrakt als
    offenen Punkt führt.
    """

    def to_dict(self) -> dict[str, Any]:
        return {"episode_id": self.episode_id, "quote": self.quote, "digest": self.digest}


@dataclass
class Proposal:
    id: str
    kind: ProposalKind
    statement: str
    rationale: str
    created_at: datetime

    assertion_kind: Kind | None = None
    """Welche Art Aussage daraus würde. Nur bei `assertion` gesetzt."""

    sensitivity: Sensitivity = Sensitivity.NORMAL
    confidence: float | None = None
    evidence: list[Evidence] = field(default_factory=list)
    about: list[str] = field(default_factory=list)
    """Bestehende Aussagen, um die es geht — bei `confirmation` und `conflict`."""

    supersedes: list[str] = field(default_factory=list)
    """Aussagen, die diese hier ablösen würde, wenn sie angenommen wird."""

    state: ProposalState = ProposalState.PENDING
    decided_at: datetime | None = None
    produced: str | None = None
    """Die Kennung der Aussage, die aus der Annahme entstand."""

    proposed_by: str = ""
    """Regel oder Modell, das den Vorschlag gemacht hat."""

    def to_dict(self) -> dict[str, Any]:
        def iso(v: datetime | None) -> str | None:
            return v.astimezone().isoformat() if v else None

        return {
            "id": self.id,
            "kind": self.kind.value,
            "statement": self.statement,
            "rationale": self.rationale,
            "assertion_kind": self.assertion_kind.value if self.assertion_kind else None,
            "sensitivity": self.sensitivity.value,
            "confidence": self.confidence,
            "evidence": [e.to_dict() for e in self.evidence],
            "about": list(self.about),
            "supersedes": list(self.supersedes),
            "state": self.state.value,
            "created_at": iso(self.created_at),
            "decided_at": iso(self.decided_at),
            "produced": self.produced,
            "proposed_by": self.proposed_by,
        }


_CREATE_PROPOSALS = """
CREATE TABLE IF NOT EXISTS proposals (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    state      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    document   TEXT NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_proposals_state ON proposals(state)",
    "CREATE INDEX IF NOT EXISTS idx_proposals_kind ON proposals(kind)",
    # Kein UNIQUE-Index: Ein abgelehnter Vorschlag darf mit neuer Evidence
    # später erneut auftauchen.
    "CREATE INDEX IF NOT EXISTS idx_proposals_finger ON proposals(fingerprint)",
)
_INDEX_CONTRACTS = {
    "idx_proposals_state": IndexContract("proposals", ("state",)),
    "idx_proposals_kind": IndexContract("proposals", ("kind",)),
    "idx_proposals_finger": IndexContract("proposals", ("fingerprint",)),
}
_SCHEMA_CONTRACT = {
    "proposals": {"id", "kind", "state", "created_at", "fingerprint", "document"}
}
_PRIMARY_KEYS = {"proposals": {"id"}}


def _migrate_v1(connection: sqlite3.Connection) -> None:
    validate_legacy_or_empty(
        connection,
        store="proposals",
        path=connection.execute("PRAGMA database_list").fetchone()[2],
        expected_tables=_SCHEMA_CONTRACT,
        expected_indexes=_INDEX_CONTRACTS,
        expected_primary_keys=_PRIMARY_KEYS,
    )
    connection.execute(_CREATE_PROPOSALS)
    for statement in _INDEXES:
        connection.execute(statement)


def _verify_v1(connection: sqlite3.Connection) -> None:
    verify_schema(
        connection,
        expected_tables=_SCHEMA_CONTRACT,
        expected_indexes=_INDEX_CONTRACTS,
        expected_primary_keys=_PRIMARY_KEYS,
    )


_MIGRATIONS = (
    Migration(1, "initial_explicit_version", _migrate_v1, _verify_v1),
)


class ProposalError(Exception):
    """Ein Vorschlag ist unbekannt, unbelegt oder bereits entschieden."""


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def fingerprint(kind: ProposalKind, statement: str, about: list[str]) -> str:
    """Kennzeichnet den *Sachverhalt*, nicht den einzelnen Vorschlag.

    Absichtlich grob: Kleinschreibung, normalisierte Leerzeichen, sortierte
    Bezüge. Ein Verdichtungslauf, der dieselbe Sache leicht anders formuliert,
    soll trotzdem als Wiederholung erkannt werden — sonst wächst die Schlange
    mit jeder Runde, und eine Schlange, in die niemand mehr sieht, ist dasselbe
    wie keine Kontrolle.
    """
    normal = " ".join(statement.casefold().split())
    return f"{kind.value}|{normal}|{','.join(sorted(about))}"


class ProposalStore:
    """Die Vorschlagsschlange in einer lokalen Datei."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        try:
            with self._lock:
                run_migrations(
                    self._conn,
                    store="proposals",
                    path=self._path,
                    migrations=_MIGRATIONS,
                )
        except Exception:
            self._conn.close()
            raise

    # -- Anlegen -----------------------------------------------------------

    def propose(
        self,
        kind: ProposalKind,
        statement: str,
        rationale: str,
        *,
        assertion_kind: Kind | None = None,
        sensitivity: Sensitivity = Sensitivity.NORMAL,
        confidence: float | None = None,
        evidence: list[Evidence] | None = None,
        about: list[str] | None = None,
        supersedes: list[str] | None = None,
        proposed_by: str = "",
        at: datetime | None = None,
    ) -> tuple[Proposal, bool]:
        """Legt einen Vorschlag an. Gibt ihn zurück und ob er **neu** war.

        Wird derselbe Sachverhalt erneut vorgelegt, während ein Vorschlag dazu
        noch offen ist, bleibt der bestehende stehen. Ein zweiter Eintrag wäre
        für den Nutzer dieselbe Frage zum zweiten Mal.

        Ein Vorschlag **ohne Beleg** wird abgewiesen. `confirmation` und
        `conflict` beziehen sich auf bestehende Aussagen (`about`), `assertion`
        auf Episoden (`evidence`) — eines von beidem muss da sein, sonst wäre
        die Verdichtung eine Blackbox.
        """
        evidence = list(evidence or [])
        about = list(about or [])
        if not evidence and not about:
            raise ProposalError(
                "Vorschlag ohne Beleg. Es braucht eine Episode oder eine "
                "bestehende Aussage, auf die er sich bezieht."
            )
        if kind is ProposalKind.ASSERTION and assertion_kind is None:
            raise ProposalError("Eine vorgeschlagene Aussage braucht eine Art.")

        finger = fingerprint(kind, statement, about)
        offen = self._by_fingerprint(finger, ProposalState.PENDING)
        if offen is not None:
            return offen, False

        proposal = Proposal(
            id=f"v-{uuid.uuid4().hex[:12]}",
            kind=kind,
            statement=statement,
            rationale=rationale,
            created_at=ensure_aware(at) or now(),
            assertion_kind=assertion_kind,
            sensitivity=sensitivity,
            confidence=confidence,
            evidence=evidence,
            about=about,
            supersedes=list(supersedes or []),
            proposed_by=proposed_by,
        )
        self._put(proposal, finger)
        return proposal, True

    # -- Entscheiden -------------------------------------------------------

    def accept(
        self, proposal_id: str, produced: str | None = None, at: datetime | None = None
    ) -> Proposal:
        proposal = self._require_pending(proposal_id)
        proposal.state = ProposalState.ACCEPTED
        proposal.produced = produced
        proposal.decided_at = ensure_aware(at) or now()
        self._put(proposal)
        return proposal

    def reject(self, proposal_id: str, at: datetime | None = None) -> Proposal:
        """Abgelehnt — und das bleibt sichtbar.

        Ein gelöschter Vorschlag wäre ein Vorgang ohne Spur. Wer später fragt,
        warum etwas *nicht* im Bestand steht, findet hier die Antwort.
        """
        proposal = self._require_pending(proposal_id)
        proposal.state = ProposalState.REJECTED
        proposal.decided_at = ensure_aware(at) or now()
        self._put(proposal)
        return proposal

    def supersede(self, proposal_id: str, at: datetime | None = None) -> Proposal:
        proposal = self._require_pending(proposal_id)
        proposal.state = ProposalState.SUPERSEDED
        proposal.decided_at = ensure_aware(at) or now()
        self._put(proposal)
        return proposal

    # -- Lesen -------------------------------------------------------------

    def get(self, proposal_id: str) -> Proposal:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise ProposalError(f"Unbekannter Vorschlag: {proposal_id}")
        return self._from_row(row)

    def pending(self, kind: ProposalKind | None = None, limit: int = 100) -> list[Proposal]:
        sql = "SELECT document FROM proposals WHERE state = ?"
        params: list[Any] = [ProposalState.PENDING.value]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind.value)
        sql += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._from_row(r) for r in rows]

    def all_proposals(self, limit: int = 200) -> list[Proposal]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM proposals ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._from_row(r) for r in rows]

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT state, COUNT(*) AS n FROM proposals GROUP BY state"
            ).fetchall()
        return {r["state"]: r["n"] for r in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- Intern ------------------------------------------------------------

    def _require_pending(self, proposal_id: str) -> Proposal:
        proposal = self.get(proposal_id)
        if proposal.state is not ProposalState.PENDING:
            raise ProposalError(
                f"Vorschlag {proposal_id} ist bereits {proposal.state.value}."
            )
        return proposal

    def _by_fingerprint(self, finger: str, state: ProposalState) -> Proposal | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM proposals WHERE fingerprint = ? AND state = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (finger, state.value),
            ).fetchone()
        return self._from_row(row) if row else None

    def _put(self, proposal: Proposal, finger: str | None = None) -> None:
        d = proposal.to_dict()
        finger = finger or fingerprint(proposal.kind, proposal.statement, proposal.about)
        with self._lock:
            self._conn.execute(
                "INSERT INTO proposals (id, kind, state, created_at, fingerprint, document) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET state=excluded.state, "
                "document=excluded.document",
                (d["id"], d["kind"], d["state"], d["created_at"], finger,
                 json.dumps(d, ensure_ascii=False)),
            )
            self._conn.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Proposal:
        d = json.loads(row["document"])
        return Proposal(
            id=d["id"],
            kind=ProposalKind(d["kind"]),
            statement=d["statement"],
            rationale=d.get("rationale", ""),
            created_at=_parse(d["created_at"]),  # type: ignore[arg-type]
            assertion_kind=Kind(d["assertion_kind"]) if d.get("assertion_kind") else None,
            sensitivity=Sensitivity(d.get("sensitivity", "normal")),
            confidence=d.get("confidence"),
            evidence=[
                Evidence(
                    episode_id=e["episode_id"],
                    quote=e.get("quote", ""),
                    digest=e.get("digest", ""),
                )
                for e in d.get("evidence", [])
            ],
            about=list(d.get("about", [])),
            supersedes=list(d.get("supersedes", [])),
            state=ProposalState(d["state"]),
            decided_at=_parse(d.get("decided_at")),
            produced=d.get("produced"),
            proposed_by=d.get("proposed_by", ""),
        )


__all__ = [
    "Evidence",
    "Proposal",
    "ProposalError",
    "ProposalKind",
    "ProposalState",
    "ProposalStore",
    "fingerprint",
]
