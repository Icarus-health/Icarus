"""Die Mittelfristschicht: rohe Aufzeichnungen dessen, was vorlag.

Zwischen „eine Mail ist angekommen" und „diese Person arbeitet bei X" liegt
Arbeit, und die hatte bisher keinen Ort. Der Bestand nimmt nur Behauptungen über
die Person auf; alles andere existierte nicht. Das ist eine gute Regel für den
Bestand und eine unmögliche für den Alltag.

Eine Episode behauptet nichts. Sie hält fest, **dass etwas vorlag** — mit
Inhalt, Herkunft und Zeitpunkt. Ob daraus eine Aussage über die Person folgt,
entscheidet die Verdichtung, und die legt vor, statt zu schreiben. Siehe
docs/08-gedaechtnisschichten.md.

## Drei Eigenschaften, die die Schicht tragfähig machen

**Digest.** Jede Episode trägt einen SHA-256 ihres Inhalts. Damit schließt sich
die erste offene Lücke aus dem Gedächtnis-Kontrakt: Ohne Digest ist keine
Neuprüfung vor einer folgenreichen Aktion möglich, weil niemand feststellen
kann, ob die Quelle sich seither geändert hat. Ein Vorschlag, der auf einer
inzwischen geänderten Mail beruht, würde sonst ausgeführt, als wäre nichts
passiert.

**Entdopplung über die Quellidentität.** Derselbe Quellvorgang wird bei einem
erneuten Sync nicht erneut angelegt. Der Digest schützt seinen gespeicherten
Inhaltszustand, ist aber nicht die Identität eines realen Ereignisses: Zwei
verschiedene Mails mit „Danke.“ bleiben zwei Ereignisse.

**Zustand statt Löschen.** `new → consolidated → archived`, dazu `ignored` für
das, was bewusst nichts hergab. Eine Episode verschwindet nie; sie hört nur auf,
Arbeit zu erzeugen. Wer wissen will, warum eine Aussage im Bestand steht, findet
über `produced` den Weg zurück zum Rohtext.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
from .model import Provenance, SourceType, ensure_aware, now


class EpisodeKind(str, Enum):
    """Was für ein Vorgang festgehalten wurde.

    Bewusst grob. Eine feinere Einteilung wäre schon eine Deutung, und Deuten
    ist Sache der Verdichtung.
    """

    MESSAGE = "message"
    """Etwas, das jemand geschrieben hat — Mail, Nachricht, Gesprächsausschnitt."""

    DOCUMENT = "document"
    """Etwas Geschriebenes ohne Absender — Notiz, Datei, Seite."""

    EVENT = "event"
    """Etwas mit einem Zeitpunkt — Termin, Frist, Vorgang."""

    INTERACTION = "interaction"
    """Ein Kontakt mit jemandem oder etwas — Profil angesehen, Nachricht geschickt."""

    OBSERVATION = "observation"
    """Was das System selbst bemerkt hat. Trägt nie fremden Text."""

    SUMMARY = "summary"
    """Was aus mehreren Episoden zusammengezogen wurde.

    Die einzige Art, die Icarus selbst schreibt, und deshalb die einzige, die
    wieder verschwinden darf — die Quellen bleiben unangetastet, es geht nichts
    verloren.

    Sie ist **nie Quelle für eine Aussage**. Eine Zusammenfassung ist bereits
    eine Deutung; würde daraus abgeleitet, prüfte die Belegprüfung das Zitat
    gegen einen Text, den das Modell selbst geschrieben hat. Der Beleg zeigte
    dann auf eine Behauptung statt auf Material. Siehe docs/12-zusammenfassung.md.
    """


class EpisodeState(str, Enum):
    NEW = "new"
    CONSOLIDATED = "consolidated"
    ARCHIVED = "archived"
    IGNORED = "ignored"
    """Angesehen, gab nichts her.

    Eigener Zustand statt Löschen: Sonst sieht die Verdichtung dieselbe Episode
    beim nächsten Lauf wieder und legt denselben nutzlosen Vorschlag erneut vor.
    """


class EpisodeArtifact(str, Enum):
    """Ob eine Episode eine beobachtete Quelle oder ICARUS-Arbeit ist."""

    SOURCE = "source"
    DERIVED = "derived"


@dataclass(frozen=True)
class SourceIdentity:
    """Stabile, connector-unabhängige Identität eines Quellereignisses.

    Das Konto ist Teil der Identität: dieselbe IMAP-UID in zwei Konten darf nie
    kollabieren. Die drei Werte sind absichtlich keine Credentials.
    """

    source_type: str
    source_account: str
    native_source_id: str

    def __post_init__(self) -> None:
        for name, value in (
            ("source_type", self.source_type),
            ("source_account", self.source_account),
            ("native_source_id", self.native_source_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise EpisodeError(f"{name} einer SourceIdentity darf nicht leer sein.")
            if "\x00" in value or "\n" in value or "\r" in value:
                raise EpisodeError(f"{name} einer SourceIdentity enthält unzulässige Steuerzeichen.")
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", self.source_type):
            raise EpisodeError(
                "source_type einer SourceIdentity muss ein kontrollierter Connector-Typ sein."
            )


@dataclass(frozen=True)
class Participant:
    """Quellennahe Beteiligung ohne vorweggenommene Personenzuordnung."""

    role: str
    display_name: str | None = None
    address: str | None = None
    external_id: str | None = None

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise EpisodeError("Participant.role darf nicht leer sein.")
        if not any((self.display_name, self.address, self.external_id)):
            raise EpisodeError("Ein Participant benötigt Name, Adresse oder externe ID.")

    def to_dict(self) -> dict[str, str]:
        return {
            key: value for key, value in {
                "role": self.role,
                "display_name": self.display_name,
                "address": self.address,
                "external_id": self.external_id,
            }.items() if value
        }


@dataclass(frozen=True)
class EventIngestResult:
    """Ergebnis einer idempotenten Quellenaufnahme."""

    status: str
    event: "Episode"
    previous_revision: int | None = None


def digest_of(text: str) -> str:
    """SHA-256 des Inhalts, als `sha256:…`.

    Das Präfix steht dabei, weil das Beispielprofil die Konvention
    `"sha256:9b1c…"` bereits im Freitext benutzt — jetzt als Struktur statt als
    Absprache.
    """
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_digest(episode: "Episode") -> str:
    """Digest des kanonisch gespeicherten Inhaltszustands eines Events.

    Aufnahmezeit, Revision und interne Verarbeitungszustände gehören bewusst
    nicht dazu. Sie ändern bei einem Replay nicht den Inhalt der Quelle.
    """
    content = {
        "kind": episode.kind.value,
        "event_type": episode.event_type,
        "title": episode.title,
        "body": episode.body,
        "occurred_at": _iso_utc(episode.occurred_at),
        "participants": episode.participants,
        "participant_details": [p.to_dict() for p in episode.participant_details],
        "tags": episode.tags,
        "raw_metadata": episode.raw_metadata,
        "source_updated_at": _iso_utc(episode.source_updated_at),
    }
    text = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return digest_of(text)


_EVENT_TYPES_BY_PROVENANCE: dict[SourceType, set[str]] = {
    SourceType.EMAIL: {"email.received", "email.sent", "message.received", "message.sent"},
    SourceType.CALENDAR: {"calendar.event"},
    SourceType.DOCUMENT: {"document.imported", "file.imported"},
    SourceType.USER_STATED: {"note.created"},
    SourceType.CHAT: {"note.created", "interaction.recorded"},
}


def _validate_source_event(kind: EpisodeKind, event_type: str, provenance: Provenance) -> None:
    if not event_type.strip():
        raise EpisodeError("event_type darf nicht leer sein.")
    allowed = _EVENT_TYPES_BY_PROVENANCE.get(provenance.source_type)
    if allowed is None or event_type not in allowed:
        raise EpisodeError(
            f"event_type {event_type!r} passt nicht zu {provenance.source_type.value!r}."
        )
    expected_kind = {
        SourceType.EMAIL: EpisodeKind.MESSAGE,
        SourceType.CALENDAR: EpisodeKind.EVENT,
        SourceType.DOCUMENT: EpisodeKind.DOCUMENT,
    }.get(provenance.source_type)
    if expected_kind is not None and kind is not expected_kind:
        raise EpisodeError(
            f"EpisodeKind {kind.value!r} passt nicht zu {provenance.source_type.value!r}."
        )


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return ensure_aware(value).astimezone(timezone.utc).isoformat()  # type: ignore[union-attr]


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


@dataclass
class Episode:
    id: str
    kind: EpisodeKind
    title: str
    body: str
    provenance: Provenance
    recorded_at: datetime
    """Wann Icarus davon erfahren hat."""

    digest: str = ""
    occurred_at: datetime | None = None
    """Wann es tatsächlich geschah.

    Getrennt von `recorded_at`, weil beides auseinanderfällt: Ein Vault, der
    heute importiert wird, enthält Notizen von vor drei Jahren. Ohne die
    Trennung wäre der ganze Bestand nach einem Import gleich alt — und die
    Alterungsurteile aus `currency.py` wären wertlos.
    """

    state: EpisodeState = EpisodeState.NEW
    project_id: str | None = None
    participants: list[str] = field(default_factory=list)
    """Wer beteiligt war. Die Rohdaten für Kontakte und Verläufe."""

    produced: list[str] = field(default_factory=list)
    """Kennungen der Aussagen, die aus dieser Episode entstanden sind."""

    consolidated_at: datetime | None = None
    tags: list[str] = field(default_factory=list)

    covers: list[str] = field(default_factory=list)
    """Nur bei `summary`: die Episoden, die darin aufgegangen sind.

    Der Weg zurück ins Rohmaterial. Ohne ihn wäre eine Zusammenfassung eine
    Behauptung ohne Herkunft — also genau das, was dieses Projekt vermeidet.
    """

    period: str = ""
    """Nur bei `summary`: der Zeitraum als `JJJJ-MM`.

    Nicht Schmuck, sondern die Bedingung für Wiederholbarkeit: Ein zweiter Lauf
    erkennt daran, dass es den April schon gibt. Über den Digest ginge das nicht
    — ein Modell schreibt zweimal denselben Monat mit anderen Worten.
    """

    # Canonical-Event-Vertrag. `recorded_at` bleibt für alte APIs lesbar;
    # `captured_at` ist der explizite Aufnahmezeitpunkt des Event-Vertrags.
    source_identity: SourceIdentity | None = None
    event_type: str = ""
    captured_at: datetime | None = None
    source_updated_at: datetime | None = None
    artifact: EpisodeArtifact = EpisodeArtifact.SOURCE
    scope_id: str | None = None
    trust: str = ""
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    participant_details: list[Participant] = field(default_factory=list)
    revision: int = 1
    source_state: str = "active"

    def __post_init__(self) -> None:
        if not self.digest:
            self.digest = digest_of(self.body)
        self.captured_at = ensure_aware(self.captured_at) or self.recorded_at
        self.source_updated_at = ensure_aware(self.source_updated_at)
        if self.source_identity is None:
            self.source_identity = SourceIdentity("legacy", "legacy", self.id)
        if not self.event_type:
            self.event_type = _legacy_event_type(self.kind)
        if not self.trust:
            self.trust = "derived" if self.artifact is EpisodeArtifact.DERIVED else "direct_source"
        if self.revision < 1:
            raise EpisodeError("Eine Event-Revision muss mindestens 1 sein.")

    def reference_time(self) -> datetime:
        """Der Zeitpunkt, auf den es fachlich ankommt."""
        return self.occurred_at or self.recorded_at

    def to_dict(self) -> dict[str, Any]:
        def iso(v: datetime | None) -> str | None:
            return v.astimezone().isoformat() if v else None

        return {
            "id": self.id,
            "kind": self.kind.value,
            "title": self.title,
            "body": self.body,
            "digest": self.digest,
            "provenance": self.provenance.to_dict(),
            "recorded_at": iso(self.recorded_at),
            "occurred_at": iso(self.occurred_at),
            "state": self.state.value,
            "project_id": self.project_id,
            "participants": list(self.participants),
            "produced": list(self.produced),
            "consolidated_at": iso(self.consolidated_at),
            "tags": list(self.tags),
            "covers": list(self.covers),
            "period": self.period,
            "source_identity": {
                "source_type": self.source_identity.source_type,
                "source_account": self.source_identity.source_account,
                "native_source_id": self.source_identity.native_source_id,
            },
            "source_type": self.source_identity.source_type,
            "source_account": self.source_identity.source_account,
            "native_source_id": self.source_identity.native_source_id,
            "event_type": self.event_type,
            "captured_at": iso(self.captured_at),
            "source_updated_at": iso(self.source_updated_at),
            "artifact": self.artifact.value,
            "scope_id": self.scope_id,
            "trust": self.trust,
            "raw_metadata": dict(self.raw_metadata),
            "participant_details": [p.to_dict() for p in self.participant_details],
            "revision": self.revision,
            "source_state": self.source_state,
        }


_CREATE_EPISODES = """
CREATE TABLE IF NOT EXISTS episodes (
    id          TEXT PRIMARY KEY,
    digest      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    state       TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    occurred_at TEXT,
    project_id  TEXT,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    document    TEXT NOT NULL
)
"""

_V1_INDEXES = (
    # Die Entdopplung hängt an dieser Bedingung, nicht an einer Prüfung im Code.
    # Ein zweiter Aufnahmeweg könnte die Prüfung vergessen; den Index nicht.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_episodes_digest ON episodes(digest)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_state ON episodes(state)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_occurred ON episodes(occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_project ON episodes(project_id)",
)
_V1_INDEX_CONTRACTS = {
    "idx_episodes_digest": IndexContract("episodes", ("digest",), unique=True),
    "idx_episodes_state": IndexContract("episodes", ("state",)),
    "idx_episodes_occurred": IndexContract("episodes", ("occurred_at",)),
    "idx_episodes_project": IndexContract("episodes", ("project_id",)),
}

_V1_SCHEMA = {
    "episodes": {
        "id",
        "digest",
        "kind",
        "state",
        "recorded_at",
        "occurred_at",
        "project_id",
        "title",
        "body",
        "document",
    }
}
_PRIMARY_KEYS = {"episodes": {"id"}}

_V2_EPISODE_COLUMNS = _V1_SCHEMA["episodes"] | {
    "source_type", "source_account", "native_source_id", "event_type",
    "captured_at", "source_updated_at", "artifact", "scope_id", "trust",
    "raw_metadata", "revision", "source_state",
}
_V2_SCHEMA = {
    "episodes": _V2_EPISODE_COLUMNS,
    "episode_revisions": {
        "event_id", "revision", "digest", "captured_at", "source_updated_at", "document",
    },
}
_V2_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_episodes_source_identity "
    "ON episodes(source_type, source_account, native_source_id)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_state ON episodes(state)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_occurred ON episodes(occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_project ON episodes(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_event_type ON episodes(event_type)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_artifact ON episodes(artifact)",
    "CREATE INDEX IF NOT EXISTS idx_episode_revisions_event ON episode_revisions(event_id, revision)",
)
_V2_INDEX_CONTRACTS = {
    "idx_episodes_source_identity": IndexContract(
        "episodes", ("source_type", "source_account", "native_source_id"), unique=True
    ),
    "idx_episodes_state": IndexContract("episodes", ("state",)),
    "idx_episodes_occurred": IndexContract("episodes", ("occurred_at",)),
    "idx_episodes_project": IndexContract("episodes", ("project_id",)),
    "idx_episodes_event_type": IndexContract("episodes", ("event_type",)),
    "idx_episodes_artifact": IndexContract("episodes", ("artifact",)),
    "idx_episode_revisions_event": IndexContract("episode_revisions", ("event_id", "revision")),
}
_V2_PRIMARY_KEYS = {"episodes": {"id"}, "episode_revisions": {"event_id", "revision"}}


def _legacy_event_type(kind: EpisodeKind) -> str:
    return {
        EpisodeKind.MESSAGE: "message.received",
        EpisodeKind.DOCUMENT: "document.imported",
        EpisodeKind.EVENT: "calendar.event",
        EpisodeKind.INTERACTION: "interaction.recorded",
        EpisodeKind.OBSERVATION: "note.created",
        EpisodeKind.SUMMARY: "summary.generated",
    }[kind]


def _episode_from_document(d: dict[str, Any]) -> Episode:
    """Baut den öffentlichen Episode-Vertrag aus einem gespeicherten Dokument."""
    p = d["provenance"]
    kind = EpisodeKind(d["kind"])
    return Episode(
        id=d["id"],
        kind=kind,
        title=d["title"],
        body=d["body"],
        provenance=Provenance(
            source_type=SourceType(p["source_type"]),
            source_ref=p.get("source_ref"),
            captured_at=_parse(p.get("captured_at")),
            extracted_by=p.get("extracted_by"),
            verbatim=p.get("verbatim"),
        ),
        recorded_at=_parse(d["recorded_at"]),  # type: ignore[arg-type]
        digest=d["digest"],
        occurred_at=_parse(d.get("occurred_at")),
        state=EpisodeState(d["state"]),
        project_id=d.get("project_id"),
        participants=list(d.get("participants", [])),
        produced=list(d.get("produced", [])),
        consolidated_at=_parse(d.get("consolidated_at")),
        tags=list(d.get("tags", [])),
        covers=list(d.get("covers", [])),
        period=str(d.get("period", "")),
        source_identity=SourceIdentity(
            str(d.get("source_type", d.get("source_identity", {}).get("source_type", "legacy"))),
            str(d.get("source_account", d.get("source_identity", {}).get("source_account", "legacy"))),
            str(d.get("native_source_id", d.get("source_identity", {}).get("native_source_id", d["id"]))),
        ),
        event_type=str(d.get("event_type", _legacy_event_type(kind))),
        captured_at=_parse(d.get("captured_at")) or _parse(d.get("recorded_at")),
        source_updated_at=_parse(d.get("source_updated_at")),
        artifact=EpisodeArtifact(d.get("artifact", "derived" if kind is EpisodeKind.SUMMARY else "source")),
        scope_id=d.get("scope_id"),
        trust=str(d.get("trust", "derived" if kind is EpisodeKind.SUMMARY else "direct_source")),
        raw_metadata=dict(d.get("raw_metadata", {})),
        participant_details=[Participant(**detail) for detail in d.get("participant_details", [])],
        revision=int(d.get("revision", 1)),
        source_state=str(d.get("source_state", "active")),
    )


def _migrate_v1(connection: sqlite3.Connection) -> None:
    validate_legacy_or_empty(
        connection,
        store="episodes",
        path=connection.execute("PRAGMA database_list").fetchone()[2],
        expected_tables=_V1_SCHEMA,
        expected_indexes=_V1_INDEX_CONTRACTS,
        expected_primary_keys=_PRIMARY_KEYS,
    )
    connection.execute(_CREATE_EPISODES)
    for statement in _V1_INDEXES:
        connection.execute(statement)


def _verify_v1(connection: sqlite3.Connection) -> None:
    verify_schema(
        connection,
        expected_tables=_V1_SCHEMA,
        expected_indexes=_V1_INDEX_CONTRACTS,
        expected_primary_keys=_PRIMARY_KEYS,
    )


def _migrate_v2(connection: sqlite3.Connection) -> None:
    """Erweitert v1 additiv zum Canonical-Event-Vertrag.

    Alte Episode-IDs bleiben ihre konservative lokale Quellidentität. Es wird
    weder Konto noch Projekt oder Scope geraten; die JSON-Nutzdaten bleiben
    unverändert und werden nur um nachvollziehbare Vertragsfelder ergänzt.
    """
    connection.execute("DROP INDEX idx_episodes_digest")
    for definition in (
        "source_type TEXT NOT NULL DEFAULT 'legacy'",
        "source_account TEXT NOT NULL DEFAULT 'legacy'",
        "native_source_id TEXT NOT NULL DEFAULT ''",
        "event_type TEXT NOT NULL DEFAULT 'legacy.recorded'",
        "captured_at TEXT NOT NULL DEFAULT ''",
        "source_updated_at TEXT",
        "artifact TEXT NOT NULL DEFAULT 'source'",
        "scope_id TEXT",
        "trust TEXT NOT NULL DEFAULT 'direct_source'",
        "raw_metadata TEXT NOT NULL DEFAULT '{}'",
        "revision INTEGER NOT NULL DEFAULT 1",
        "source_state TEXT NOT NULL DEFAULT 'active'",
    ):
        connection.execute(f"ALTER TABLE episodes ADD COLUMN {definition}")
    connection.execute(
        "CREATE TABLE episode_revisions ("
        "event_id TEXT NOT NULL, revision INTEGER NOT NULL, digest TEXT NOT NULL, "
        "captured_at TEXT NOT NULL, source_updated_at TEXT, document TEXT NOT NULL, "
        "PRIMARY KEY(event_id, revision))"
    )

    rows = connection.execute("SELECT * FROM episodes ORDER BY id").fetchall()
    for row in rows:
        document = json.loads(row["document"])
        legacy_body_digest = str(document.get("digest") or row["digest"])
        kind = EpisodeKind(document["kind"])
        provenance = document.get("provenance", {})
        artifact = EpisodeArtifact.DERIVED if kind is EpisodeKind.SUMMARY else EpisodeArtifact.SOURCE
        captured_at = provenance.get("captured_at") or document["recorded_at"]
        identity = {
            "source_type": "legacy",
            "source_account": "legacy",
            "native_source_id": document["id"],
        }
        raw_metadata = dict(document.get("raw_metadata", {}))
        # Proposal-Evidenz referenziert den bisherigen v1-Body-Digest dauerhaft.
        # Der v2-Digest wird kanonisch neu berechnet; der alte Prüfwert bleibt
        # deshalb explizit und nachvollziehbar am migrierten Event erhalten.
        raw_metadata["legacy_body_digest"] = legacy_body_digest
        document.update({
            "source_identity": identity,
            **identity,
            "event_type": _legacy_event_type(kind),
            "captured_at": captured_at,
            "source_updated_at": None,
            "artifact": artifact.value,
            "scope_id": None,
            "trust": "derived" if artifact is EpisodeArtifact.DERIVED else "direct_source",
            "raw_metadata": raw_metadata,
            "participant_details": [],
            "revision": 1,
            "source_state": "active",
        })
        migrated = _episode_from_document(document)
        document["digest"] = canonical_digest(migrated)
        encoded = json.dumps(document, ensure_ascii=False, sort_keys=True)
        connection.execute(
            "UPDATE episodes SET digest=?, source_type=?, source_account=?, native_source_id=?, "
            "event_type=?, captured_at=?, artifact=?, trust=?, raw_metadata=?, revision=1, "
            "source_state='active', document=? WHERE id=?",
            (document["digest"], "legacy", "legacy", document["id"], document["event_type"],
             captured_at, artifact.value, document["trust"],
             json.dumps(raw_metadata, ensure_ascii=False, sort_keys=True), encoded, document["id"]),
        )
        connection.execute(
            "INSERT INTO episode_revisions "
            "(event_id, revision, digest, captured_at, source_updated_at, document) "
            "VALUES (?, 1, ?, ?, NULL, ?)",
            (document["id"], document["digest"], captured_at, encoded),
        )
    for statement in _V2_INDEXES:
        connection.execute(statement)


def _verify_v2(connection: sqlite3.Connection) -> None:
    verify_schema(
        connection,
        expected_tables=_V2_SCHEMA,
        expected_indexes=_V2_INDEX_CONTRACTS,
        expected_primary_keys=_V2_PRIMARY_KEYS,
    )


_MIGRATIONS = (
    Migration(1, "initial_explicit_version", _migrate_v1, _verify_v1),
    Migration(2, "canonical_event_contract", _migrate_v2, _verify_v2),
)

EPISODES_SCHEMA_VERSION = _MIGRATIONS[-1].version


class EpisodeError(Exception):
    """Eine Episode ist unbekannt oder ein Zustandswechsel ist nicht erlaubt."""


class EpisodeStore:
    """Episoden in einer lokalen Datei, neben dem Selbstmodell."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        try:
            with self._lock:
                run_migrations(
                    self._conn,
                    store="episodes",
                    path=self._path,
                    migrations=_MIGRATIONS,
                )
        except Exception:
            self._conn.close()
            raise

    # -- Aufnehmen ---------------------------------------------------------

    def record(
        self,
        kind: EpisodeKind,
        title: str,
        body: str,
        provenance: Provenance,
        *,
        occurred_at: datetime | None = None,
        project_id: str | None = None,
        participants: list[str] | None = None,
        tags: list[str] | None = None,
        at: datetime | None = None,
    ) -> tuple[Episode, bool]:
        """Kompatibler Weg für manuell erzeugte Episodes.

        Der zweite Wert ist der Grund, warum diese Methode kein schlichtes
        `add()` ist: Ein Aufnahmelauf über einen Vault mit tausend Dateien muss
        berichten können, was er tatsächlich getan hat. „847 aufgenommen, 153
        schon bekannt" ist die Zeile, die den Nutzer beruhigt; „1000 verarbeitet"
        ist die, die ihn misstrauisch macht.

        Ein manueller Eintrag besitzt keine externe Replay-Identität. Deshalb
        erhält jeder Aufruf eine lokale native ID und zwei gleiche Notizen
        bleiben zwei getrennte Ereignisse. Connectoren verwenden stattdessen
        :meth:`upsert_source_event` mit ihrer echten Quellidentität.
        """
        episode = Episode(
            id=f"e-{uuid.uuid4().hex[:12]}",
            kind=kind,
            title=title,
            body=body,
            provenance=provenance,
            recorded_at=ensure_aware(at) or now(),
            occurred_at=ensure_aware(occurred_at),
            project_id=project_id,
            participants=list(participants or []),
            tags=list(tags or []),
            source_identity=SourceIdentity(
                provenance.source_type.value, "local", f"episode:{uuid.uuid4().hex}"
            ),
            event_type=_legacy_event_type(kind),
            captured_at=ensure_aware(at) or now(),
            artifact=(EpisodeArtifact.DERIVED if kind is EpisodeKind.SUMMARY
                      else EpisodeArtifact.SOURCE),
            trust=("derived" if kind is EpisodeKind.SUMMARY else "user_input"),
        )
        episode.digest = canonical_digest(episode)
        self._insert(episode)
        return episode, True

    def upsert_source_event(
        self,
        *,
        identity: SourceIdentity,
        kind: EpisodeKind,
        event_type: str,
        title: str,
        body: str,
        provenance: Provenance,
        occurred_at: datetime | None = None,
        captured_at: datetime | None = None,
        source_updated_at: datetime | None = None,
        project_id: str | None = None,
        participants: list[str] | None = None,
        participant_details: list[Participant] | None = None,
        tags: list[str] | None = None,
        scope_id: str | None = None,
        trust: str = "direct_source",
        raw_metadata: dict[str, Any] | None = None,
    ) -> EventIngestResult:
        """Nimmt ein beobachtetes Event idempotent nach Quellidentität auf.

        Gleicher Inhalt ist ein No-op. Geänderter Inhalt aktualisiert die
        aktuelle Repräsentation kontrolliert und legt eine vollständige
        Revision an. Der Digest ist dabei ausschließlich der Prüfwert des
        kanonisch gespeicherten Inhaltszustands.
        """
        if kind is EpisodeKind.SUMMARY:
            raise EpisodeError("Eine Summary ist kein Source Event.")
        _validate_source_event(kind, event_type, provenance)
        captured = ensure_aware(captured_at) or now()
        details = list(participant_details or [])
        participant_names = list(participants or [])
        for detail in details:
            candidate = detail.display_name or detail.address or detail.external_id
            if candidate and candidate not in participant_names:
                participant_names.append(candidate)
        event = Episode(
            id=f"e-{uuid.uuid4().hex[:12]}", kind=kind, title=title, body=body,
            provenance=provenance, recorded_at=captured,
            occurred_at=ensure_aware(occurred_at), project_id=project_id,
            participants=participant_names, participant_details=details,
            tags=list(tags or []), source_identity=identity, event_type=event_type,
            captured_at=captured, source_updated_at=ensure_aware(source_updated_at),
            artifact=EpisodeArtifact.SOURCE, scope_id=scope_id, trust=trust,
            raw_metadata=dict(raw_metadata or {}),
        )
        event.digest = canonical_digest(event)
        with self._lock:
            try:
                # Der Lock gilt nur für diese Store-Instanz. BEGIN IMMEDIATE
                # serialisiert die Identity-Entscheidung auch gegenüber anderen
                # Verbindungen auf dieselbe SQLite-Datei.
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT document FROM episodes WHERE source_type=? AND source_account=? "
                    "AND native_source_id=?",
                    (identity.source_type, identity.source_account, identity.native_source_id),
                ).fetchone()
                if row is None:
                    self._write_event_and_revision_locked(event, insert=True)
                    result = EventIngestResult("created", event)
                else:
                    previous = self._from_row(row)
                    if previous.digest == event.digest:
                        result = EventIngestResult("unchanged", previous)
                    else:
                        event.id = previous.id
                        event.revision = previous.revision + 1
                        event.state = previous.state
                        event.produced = list(previous.produced)
                        event.consolidated_at = previous.consolidated_at
                        self._write_event_and_revision_locked(event, insert=False)
                        result = EventIngestResult("updated", event, previous.revision)
                self._conn.commit()
                return result
            except Exception:
                self._conn.rollback()
                raise

    # -- Zustand -----------------------------------------------------------

    def mark_consolidated(
        self, episode_id: str, produced: list[str] | None = None,
        at: datetime | None = None,
    ) -> Episode:
        """Hält fest, dass die Verdichtung diese Episode angesehen hat.

        `produced` ist der Rückweg: Wer eine Aussage im Bestand sieht, kommt
        über `derived_from` zur Episode und von dort zum Rohtext. Ohne diesen
        Weg wäre die Verdichtung eine Blackbox, die Behauptungen erzeugt.
        """
        episode = self.get(episode_id)
        episode.state = EpisodeState.CONSOLIDATED
        episode.consolidated_at = ensure_aware(at) or now()
        # Ergänzen statt ersetzen: Ein zweiter Lauf kann weitere Aussagen
        # hervorbringen, und die erste Herleitung darf dabei nicht verschwinden.
        for assertion_id in produced or []:
            if assertion_id not in episode.produced:
                episode.produced.append(assertion_id)
        self._put(episode)
        return episode

    def ignore(self, episode_id: str) -> Episode:
        episode = self.get(episode_id)
        episode.state = EpisodeState.IGNORED
        self._put(episode)
        return episode

    def archive_before(self, cutoff: datetime) -> int:
        """Archiviert Verdichtetes und Verworfenes, das älter ist als `cutoff`.

        `new` bleibt unangetastet, egal wie alt: Eine Episode, die nie jemand
        angesehen hat, verschwindet nicht in der Ablage, nur weil Zeit vergeht.
        Das wäre stilles Vergessen, und zwar genau des Materials, das noch
        Arbeit erzeugen sollte.
        """
        cutoff = ensure_aware(cutoff) or cutoff
        betroffen = [
            e for e in self.all_episodes(limit=100000)
            if e.state in (EpisodeState.CONSOLIDATED, EpisodeState.IGNORED)
            and e.reference_time() < cutoff
        ]
        for episode in betroffen:
            episode.state = EpisodeState.ARCHIVED
            self._put(episode)
        return len(betroffen)

    # -- Zusammenfassungen -------------------------------------------------
    #
    # Die einzigen Episoden, die Icarus selbst schreibt. Deshalb gelten für sie
    # zwei Regeln, die für Rohmaterial nicht gälten: Sie kommen nie in die
    # Verdichtung, und sie dürfen wieder verschwinden.

    def record_summary(
        self,
        title: str,
        body: str,
        period: str,
        covers: list[str],
        *,
        extracted_by: str = "",
        at: datetime | None = None,
    ) -> Episode:
        """Legt eine Zusammenfassung an und archiviert, was darin aufgeht.

        Sie entsteht direkt als `consolidated`, nicht als `new`. Eine
        Zusammenfassung, die auf Verdichtung wartet, wäre der Kreis, den die
        Belegprüfung nicht mehr schließen kann: Das Modell prüfte sein Zitat
        gegen einen Text, den es selbst geschrieben hat.
        """
        if not covers:
            raise EpisodeError(
                "Eine Zusammenfassung ohne Quellen ist eine Behauptung ohne Herkunft."
            )
        zeit = ensure_aware(at) or now()
        episode = Episode(
            id=f"z-{uuid.uuid4().hex[:12]}",
            kind=EpisodeKind.SUMMARY,
            title=title,
            body=body,
            provenance=Provenance(
                source_type=SourceType.INFERENCE,
                source_ref=f"episoden:{len(covers)}",
                captured_at=zeit,
                extracted_by=extracted_by or None,
            ),
            recorded_at=zeit,
            state=EpisodeState.CONSOLIDATED,
            consolidated_at=zeit,
            covers=list(covers),
            period=period,
            source_identity=SourceIdentity("icarus", "local", f"summary:{uuid.uuid4().hex}"),
            event_type="summary.generated",
            captured_at=zeit,
            artifact=EpisodeArtifact.DERIVED,
            trust="derived",
        )
        episode.digest = canonical_digest(episode)
        self._insert(episode)

        # Erst jetzt archivieren. Bricht das Anlegen ab, ist nichts weggeräumt,
        # was danach niemand mehr in der Liste findet.
        for quelle in covers:
            original = self.get(quelle)
            original.state = EpisodeState.ARCHIVED
            self._put(original)
        return episode

    def summary_for(self, period: str) -> Episode | None:
        """Gibt es den Monat schon? Die Bedingung für einen zweiten Lauf."""
        for episode in self.summaries():
            if episode.period == period:
                return episode
        return None

    def summaries(self, limit: int = 200) -> list[Episode]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM episodes WHERE kind = ? "
                "ORDER BY COALESCE(occurred_at, recorded_at) DESC LIMIT ?",
                (EpisodeKind.SUMMARY.value, limit),
            ).fetchall()
        return [self._from_row(r) for r in rows]

    def delete_summary(self, episode_id: str) -> int:
        """Nimmt eine Zusammenfassung zurück und holt die Quellen hervor.

        Der einzige Weg, auf dem eine Episode je verschwindet — und er gilt
        ausschließlich für das, was Icarus selbst geschrieben hat. Rohmaterial
        wird nie gelöscht: Es ist der Beleg, auf den sich alles andere beruft.

        Ohne diesen Weg wäre die Zusammenfassung eine Einbahnstraße. Ein Modell,
        das einen Monat falsch zusammenfasst, hätte den Monat dann faktisch
        ersetzt, und niemand käme mehr an die Übersicht darüber heran.
        """
        episode = self.get(episode_id)
        if episode.kind is not EpisodeKind.SUMMARY:
            raise EpisodeError(
                "Nur Zusammenfassungen dürfen gelöscht werden. "
                f"{episode_id} ist Rohmaterial ({episode.kind.value})."
            )

        zurueck = 0
        for quelle in episode.covers:
            try:
                original = self.get(quelle)
            except EpisodeError:
                continue
            if original.state is EpisodeState.ARCHIVED:
                original.state = EpisodeState.CONSOLIDATED
                self._put(original)
                zurueck += 1

        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM episode_revisions WHERE event_id = ?", (episode_id,))
                self._conn.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return zurueck

    def link_project(self, episode_id: str, project_id: str | None) -> Episode:
        episode = self.get(episode_id)
        episode.project_id = project_id
        self._put(episode)
        return episode

    # -- Lesen -------------------------------------------------------------

    def get(self, episode_id: str) -> Episode:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM episodes WHERE id = ?", (episode_id,)
            ).fetchone()
        if row is None:
            raise EpisodeError(f"Unbekannte Episode: {episode_id}")
        return self._from_row(row)

    def by_digest(self, digest: str) -> Episode | None:
        """Kompatibilitätsabfrage, keine Event-Identitätsauflösung."""
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM episodes WHERE digest = ?", (digest,)
            ).fetchone()
        return self._from_row(row) if row else None

    def find_by_source_identity(self, identity: SourceIdentity) -> Episode | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM episodes WHERE source_type=? AND source_account=? "
                "AND native_source_id=?",
                (identity.source_type, identity.source_account, identity.native_source_id),
            ).fetchone()
        return self._from_row(row) if row else None

    def revisions(self, episode_id: str) -> list[Episode]:
        """Gibt alle erhaltenen Inhaltsfassungen chronologisch zurück."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM episode_revisions WHERE event_id=? ORDER BY revision",
                (episode_id,),
            ).fetchall()
        if not rows:
            raise EpisodeError(f"Unbekannte Episode: {episode_id}")
        return [self._from_row(row) for row in rows]

    def raw_events(self, limit: int = 500) -> list[Episode]:
        return self.events(artifact=EpisodeArtifact.SOURCE, limit=limit)

    def derived_artifacts(self, limit: int = 500) -> list[Episode]:
        return self.events(artifact=EpisodeArtifact.DERIVED, limit=limit)

    def events(
        self,
        *,
        artifact: EpisodeArtifact | None = None,
        event_type: str | None = None,
        project_id: str | None = None,
        participant: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 500,
    ) -> list[Episode]:
        """Kleine Query-Basis für spätere Understanding-Projektionen."""
        conditions: list[str] = []
        values: list[Any] = []
        if artifact is not None:
            conditions.append("artifact = ?")
            values.append(artifact.value)
        if event_type is not None:
            conditions.append("event_type = ?")
            values.append(event_type)
        if project_id is not None:
            conditions.append("project_id = ?")
            values.append(project_id)
        if since is not None:
            conditions.append("COALESCE(occurred_at, recorded_at) >= ?")
            values.append(_iso_utc(ensure_aware(since)))
        if until is not None:
            conditions.append("COALESCE(occurred_at, recorded_at) <= ?")
            values.append(_iso_utc(ensure_aware(until)))
        if participant is not None:
            conditions.append("document LIKE ?")
            values.append(f"%{participant}%")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        values.append(limit)
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM episodes" + where +
                " ORDER BY COALESCE(occurred_at, recorded_at) DESC LIMIT ?", values,
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def pending(self, limit: int = 100) -> list[Episode]:
        """Was auf Verdichtung wartet, Ältestes zuerst.

        Die Reihenfolge ist Absicht: Verdichtung soll chronologisch arbeiten,
        sonst entstehen Aussagen aus dem Mai, bevor die aus dem März gesehen
        wurden — und die Ersetzungskette steht auf dem Kopf.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM episodes WHERE state = ? AND artifact = ? "
                "ORDER BY COALESCE(occurred_at, recorded_at) ASC LIMIT ?",
                (EpisodeState.NEW.value, EpisodeArtifact.SOURCE.value, limit),
            ).fetchall()
        return [self._from_row(r) for r in rows]

    def recent(self, days: int = 7, limit: int = 100,
               at: datetime | None = None) -> list[Episode]:
        cutoff = (at or now()) - timedelta(days=days)
        return [
            e for e in self.all_episodes(limit=limit * 4)
            if e.reference_time() >= cutoff
        ][:limit]

    def by_project(self, project_id: str, limit: int = 100) -> list[Episode]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM episodes WHERE project_id = ? "
                "ORDER BY COALESCE(occurred_at, recorded_at) DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [self._from_row(r) for r in rows]

    def search(self, query: str, limit: int = 20) -> list[Episode]:
        pattern = f"%{query}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM episodes WHERE title LIKE ? OR body LIKE ? "
                "ORDER BY COALESCE(occurred_at, recorded_at) DESC LIMIT ?",
                (pattern, pattern, limit),
            ).fetchall()
        return [self._from_row(r) for r in rows]

    def all_episodes(self, limit: int = 500) -> list[Episode]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM episodes "
                "ORDER BY COALESCE(occurred_at, recorded_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._from_row(r) for r in rows]

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT state, COUNT(*) AS n FROM episodes GROUP BY state"
            ).fetchall()
        return {r["state"]: r["n"] for r in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- Intern ------------------------------------------------------------

    def _put(self, episode: Episode) -> None:
        """Schreibt nicht-inhaltliche Zustandsänderungen ohne neue Revision."""
        with self._lock:
            self._write_current_locked(episode)
            self._conn.commit()

    def _insert(self, episode: Episode) -> None:
        with self._lock:
            self._insert_locked(episode)

    def _insert_locked(self, episode: Episode) -> None:
        """Schreibt außerhalb eines bestehenden Write-Kontexts atomar."""
        try:
            self._conn.execute("BEGIN")
            self._write_event_and_revision_locked(episode, insert=True)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _write_event_and_revision_locked(self, episode: Episode, *, insert: bool) -> None:
        """Schreibt Current Row und Revision in der bereits offenen Transaktion."""
        self._write_current_locked(episode, insert=insert)
        self._write_revision_locked(episode)

    def _write_revision_locked(self, episode: Episode) -> None:
        d = episode.to_dict()
        self._conn.execute(
            "INSERT INTO episode_revisions "
            "(event_id, revision, digest, captured_at, source_updated_at, document) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (d["id"], d["revision"], d["digest"], d["captured_at"],
             d["source_updated_at"], json.dumps(d, ensure_ascii=False, sort_keys=True)),
        )

    def _write_current_locked(self, episode: Episode, *, insert: bool = False) -> None:
        d = episode.to_dict()
        if insert:
            self._conn.execute(
                "INSERT INTO episodes (id, digest, kind, state, recorded_at, occurred_at, "
                "project_id, title, body, source_type, source_account, native_source_id, "
                "event_type, captured_at, source_updated_at, artifact, scope_id, trust, "
                "raw_metadata, revision, source_state, document) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (d["id"], d["digest"], d["kind"], d["state"], d["recorded_at"],
                 d["occurred_at"], d["project_id"], d["title"], d["body"],
                 d["source_type"], d["source_account"], d["native_source_id"],
                 d["event_type"], d["captured_at"], d["source_updated_at"],
                 d["artifact"], d["scope_id"], d["trust"],
                 json.dumps(d["raw_metadata"], ensure_ascii=False, sort_keys=True),
                 d["revision"], d["source_state"], json.dumps(d, ensure_ascii=False, sort_keys=True)),
            )
        else:
            self._conn.execute(
                "UPDATE episodes SET digest=?, kind=?, state=?, recorded_at=?, occurred_at=?, "
                "project_id=?, title=?, body=?, source_type=?, source_account=?, native_source_id=?, "
                "event_type=?, captured_at=?, source_updated_at=?, artifact=?, scope_id=?, trust=?, "
                "raw_metadata=?, revision=?, source_state=?, document=? WHERE id=?",
                (d["digest"], d["kind"], d["state"], d["recorded_at"], d["occurred_at"],
                 d["project_id"], d["title"], d["body"], d["source_type"],
                 d["source_account"], d["native_source_id"], d["event_type"],
                 d["captured_at"], d["source_updated_at"], d["artifact"], d["scope_id"],
                 d["trust"], json.dumps(d["raw_metadata"], ensure_ascii=False, sort_keys=True),
                 d["revision"], d["source_state"], json.dumps(d, ensure_ascii=False, sort_keys=True), d["id"]),
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Episode:
        return _episode_from_document(json.loads(row["document"]))


__all__ = [
    "Episode",
    "EpisodeArtifact",
    "EpisodeError",
    "EpisodeKind",
    "EpisodeState",
    "EPISODES_SCHEMA_VERSION",
    "EventIngestResult",
    "EpisodeStore",
    "Participant",
    "SourceIdentity",
    "canonical_digest",
    "digest_of",
]
