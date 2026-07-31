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

**Entdopplung über den Digest.** Denselben Vault zweimal aufnehmen erzeugt keine
zweite Kopie. Ohne das ist kein Prozess denkbar, der dauerhaft mitläuft — und
genau der ist das Ziel.

**Zustand statt Löschen.** `new → consolidated → archived`, dazu `ignored` für
das, was bewusst nichts hergab. Eine Episode verschwindet nie; sie hört nur auf,
Arbeit zu erzeugen. Wer wissen will, warum eine Aussage im Bestand steht, findet
über `produced` den Weg zurück zum Rohtext.
"""

from __future__ import annotations

import hashlib
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


class EpisodeState(str, Enum):
    NEW = "new"
    CONSOLIDATED = "consolidated"
    ARCHIVED = "archived"
    IGNORED = "ignored"
    """Angesehen, gab nichts her.

    Eigener Zustand statt Löschen: Sonst sieht die Verdichtung dieselbe Episode
    beim nächsten Lauf wieder und legt denselben nutzlosen Vorschlag erneut vor.
    """


def digest_of(text: str) -> str:
    """SHA-256 des Inhalts, als `sha256:…`.

    Das Präfix steht dabei, weil das Beispielprofil die Konvention
    `"sha256:9b1c…"` bereits im Freitext benutzt — jetzt als Struktur statt als
    Absprache.
    """
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


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

    def __post_init__(self) -> None:
        if not self.digest:
            self.digest = digest_of(self.body)

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
        }


_SCHEMA = """
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
);

-- Die Entdopplung hängt an dieser Bedingung, nicht an einer Prüfung im Code.
-- Ein zweiter Aufnahmeweg könnte die Prüfung vergessen; den Index nicht.
CREATE UNIQUE INDEX IF NOT EXISTS idx_episodes_digest ON episodes(digest);

CREATE INDEX IF NOT EXISTS idx_episodes_state    ON episodes(state);
CREATE INDEX IF NOT EXISTS idx_episodes_occurred ON episodes(occurred_at);
CREATE INDEX IF NOT EXISTS idx_episodes_project  ON episodes(project_id);
"""


class EpisodeError(Exception):
    """Eine Episode ist unbekannt oder ein Zustandswechsel ist nicht erlaubt."""


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


class EpisodeStore:
    """Episoden in einer lokalen Datei, neben dem Selbstmodell."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

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
        """Nimmt eine Episode auf. Gibt sie zurück und ob sie **neu** war.

        Der zweite Wert ist der Grund, warum diese Methode kein schlichtes
        `add()` ist: Ein Aufnahmelauf über einen Vault mit tausend Dateien muss
        berichten können, was er tatsächlich getan hat. „847 aufgenommen, 153
        schon bekannt" ist die Zeile, die den Nutzer beruhigt; „1000 verarbeitet"
        ist die, die ihn misstrauisch macht.

        Bei einem bekannten Digest bleibt der **bestehende** Eintrag stehen. Ihn
        zu überschreiben hieße, Zustand und `produced` zu verlieren — die
        Episode käme erneut in die Verdichtung, und der Nutzer bekäme Vorschläge
        vorgelegt, die er längst entschieden hat.
        """
        existing = self.by_digest(digest_of(body))
        if existing is not None:
            return existing, False

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
        )
        self._put(episode)
        return episode, True

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
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM episodes WHERE digest = ?", (digest,)
            ).fetchone()
        return self._from_row(row) if row else None

    def pending(self, limit: int = 100) -> list[Episode]:
        """Was auf Verdichtung wartet, Ältestes zuerst.

        Die Reihenfolge ist Absicht: Verdichtung soll chronologisch arbeiten,
        sonst entstehen Aussagen aus dem Mai, bevor die aus dem März gesehen
        wurden — und die Ersetzungskette steht auf dem Kopf.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM episodes WHERE state = ? "
                "ORDER BY COALESCE(occurred_at, recorded_at) ASC LIMIT ?",
                (EpisodeState.NEW.value, limit),
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
        d = episode.to_dict()
        with self._lock:
            self._conn.execute(
                "INSERT INTO episodes (id, digest, kind, state, recorded_at, "
                "occurred_at, project_id, title, body, document) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET state=excluded.state, "
                "project_id=excluded.project_id, document=excluded.document",
                (d["id"], d["digest"], d["kind"], d["state"], d["recorded_at"],
                 d["occurred_at"], d["project_id"], d["title"], d["body"],
                 json.dumps(d, ensure_ascii=False)),
            )
            self._conn.commit()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Episode:
        d = json.loads(row["document"])
        p = d["provenance"]
        return Episode(
            id=d["id"],
            kind=EpisodeKind(d["kind"]),
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
        )


__all__ = [
    "Episode",
    "EpisodeError",
    "EpisodeKind",
    "EpisodeState",
    "EpisodeStore",
    "digest_of",
]
