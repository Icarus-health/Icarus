"""Persistenz für das Selbstmodell.

Bewusst zweigeteilt, und das ist eine Architekturentscheidung, keine Bequemlichkeit:

* **Verbindlicher Bestand** liegt in SQLite. Aussagen, Provenienz, Ersetzungs-
  und Ableitungsketten müssen exakt, per ID adressierbar und ohne Modellaufruf
  lesbar sein. Ein Knowledge Graph, der per LLM befüllt wird, ist dafür die
  falsche Grundlage — er ist verlustbehaftet und nicht deterministisch.
* **Semantisches Wiederfinden** übernimmt cognee. Dort liegt die Stärke:
  Graph-Traversierung und Ähnlichkeitssuche über die Formulierungen.

Damit überlebt das Selbstmodell einen Wechsel der Memory-Bibliothek. Fällt
cognee weg, bleibt der Bestand vollständig; nur die semantische Suche fällt auf
Substringsuche zurück. Das ist Säule 2 in praktischer Form.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .model import (
    Assertion,
    Kind,
    Provenance,
    Redaction,
    RedactionReason,
    Sensitivity,
    SourceType,
    Status,
)


# -- Serialisierung --------------------------------------------------------


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def assertion_from_dict(d: dict[str, Any]) -> Assertion:
    prov = d["provenance"]
    redaction = None
    if d.get("redaction"):
        r = d["redaction"]
        redaction = Redaction(
            redacted_at=_parse_dt(r["redacted_at"]),  # type: ignore[arg-type]
            reason=RedactionReason(r["reason"]),
            cascade=list(r.get("cascade", [])),
        )
    return Assertion(
        id=d["id"],
        kind=Kind(d["kind"]),
        statement=d["statement"],
        provenance=Provenance(
            source_type=SourceType(prov["source_type"]),
            source_ref=prov.get("source_ref"),
            captured_at=_parse_dt(prov.get("captured_at")),
            extracted_by=prov.get("extracted_by"),
            verbatim=prov.get("verbatim"),
        ),
        recorded_at=_parse_dt(d["recorded_at"]),  # type: ignore[arg-type]
        status=Status(d["status"]),
        structured=d.get("structured"),
        confidence=d.get("confidence"),
        valid_from=_parse_dt(d.get("valid_from")),
        expires_at=_parse_dt(d.get("expires_at")),
        last_confirmed_at=_parse_dt(d.get("last_confirmed_at")),
        supersedes=list(d.get("supersedes", [])),
        superseded_by=d.get("superseded_by"),
        derived_from=list(d.get("derived_from", [])),
        sensitivity=Sensitivity(d.get("sensitivity", "normal")),
        tags=list(d.get("tags", [])),
        redaction=redaction,
    )


# -- Backends --------------------------------------------------------------


class MemoryBackend:
    """Flüchtiger Speicher. Für Tests und kurzlebige Sitzungen."""

    def __init__(self) -> None:
        self._data: dict[str, Assertion] = {}

    def put(self, assertion: Assertion) -> None:
        self._data[assertion.id] = assertion

    def get(self, assertion_id: str) -> Assertion | None:
        return self._data.get(assertion_id)

    def all(self) -> list[Assertion]:
        return list(self._data.values())

    def search(self, query: str, limit: int) -> list[Assertion]:
        needle = query.casefold()
        hits = [a for a in self._data.values() if needle in a.statement.casefold()]
        return hits[:limit]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS assertions (
    id          TEXT PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    status      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    statement   TEXT NOT NULL,
    document    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assertions_status ON assertions(status);
CREATE INDEX IF NOT EXISTS idx_assertions_kind   ON assertions(kind);
"""


class SqliteBackend:
    """Verbindlicher Bestand in einer lokalen Datei.

    Die vollständige Aussage liegt als JSON in `document`; die herausgezogenen
    Spalten dienen nur dem Filtern. So bleibt das Format des Schemas führend
    und die Tabelle muss bei Schemaerweiterungen nicht wandern.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # `check_same_thread=False`, weil FastAPI synchrone Endpunkte in einem
        # Threadpool ausführt: jede Anfrage kann auf einem anderen Thread landen.
        # Die Verbindung wird deshalb selbst serialisiert — sqlite3 gibt sonst
        # "SQLite objects created in a thread can only be used in that same thread".
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def put(self, assertion: Assertion) -> None:
        d = assertion.to_dict()
        with self._lock:
            self._conn.execute(
                "INSERT INTO assertions (id, recorded_at, status, kind, statement, document) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "recorded_at=excluded.recorded_at, status=excluded.status, "
                "kind=excluded.kind, statement=excluded.statement, document=excluded.document",
                (
                    d["id"],
                    d["recorded_at"],
                    d["status"],
                    d["kind"],
                    d["statement"],
                    json.dumps(d, ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def get(self, assertion_id: str) -> Assertion | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM assertions WHERE id = ?", (assertion_id,)
            ).fetchone()
        return assertion_from_dict(json.loads(row["document"])) if row else None

    def all(self) -> list[Assertion]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM assertions ORDER BY recorded_at"
            ).fetchall()
        return [assertion_from_dict(json.loads(r["document"])) for r in rows]

    def search(self, query: str, limit: int) -> list[Assertion]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM assertions WHERE statement LIKE ? "
                "ORDER BY recorded_at DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
        return [assertion_from_dict(json.loads(r["document"])) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class CogneeBackend:
    """SQLite als Bestand, cognee als semantischer Index.

    cognee wird nur für `search` befragt; die Trefferliste wird anschließend
    gegen den verbindlichen Bestand aufgelöst. Damit kann der Graph nie eine
    Aussage erfinden, die es im Bestand nicht gibt.

    Ist cognee nicht verfügbar oder kein Modell konfiguriert, fällt die Suche
    auf die Substringsuche von SQLite zurück. Der Bestand funktioniert dann
    unverändert weiter — nur unschärfer auffindbar.
    """

    def __init__(self, path: str | Path, dataset: str = "icarus_self_model") -> None:
        self._store = SqliteBackend(path)
        self._dataset = dataset
        self._cognee = None
        self._degraded_reason: str | None = None
        try:  # pragma: no cover - hängt von der Umgebung ab
            import cognee  # noqa: PLC0415

            self._cognee = cognee
        except Exception as exc:  # pragma: no cover
            self._degraded_reason = f"cognee nicht importierbar: {exc}"

    @property
    def degraded(self) -> bool:
        """True, wenn die semantische Suche gerade nicht zur Verfügung steht."""
        return self._cognee is None

    @property
    def degraded_reason(self) -> str | None:
        return self._degraded_reason

    def put(self, assertion: Assertion) -> None:
        self._store.put(assertion)
        # Widerrufenes und Ersetztes darf nicht im semantischen Index landen —
        # sonst taucht es über Ähnlichkeit wieder auf, obwohl es nicht mehr gilt.
        if self._cognee is not None and assertion.status is Status.ACTIVE:
            self._index(assertion)

    def get(self, assertion_id: str) -> Assertion | None:
        return self._store.get(assertion_id)

    def all(self) -> list[Assertion]:
        return self._store.all()

    def search(self, query: str, limit: int) -> list[Assertion]:
        if self._cognee is None:
            return self._store.search(query, limit)
        try:  # pragma: no cover - benötigt konfiguriertes Modell
            ids = self._recall_ids(query, limit)
        except Exception as exc:  # pragma: no cover
            self._degraded_reason = f"cognee-Suche fehlgeschlagen: {exc}"
            return self._store.search(query, limit)

        resolved = [self._store.get(i) for i in ids]
        return [a for a in resolved if a is not None][:limit]

    # -- cognee-Anbindung ---------------------------------------------------

    def _index(self, assertion: Assertion) -> None:  # pragma: no cover
        """Schreibt die Aussage in cognees Graph, mit der ID als Anker."""
        import asyncio

        payload = f"[{assertion.id}] ({assertion.kind.value}) {assertion.statement}"
        try:
            asyncio.run(self._cognee.remember(payload, session_id=self._dataset))
        except Exception as exc:
            self._degraded_reason = f"cognee-Indexierung fehlgeschlagen: {exc}"

    def _recall_ids(self, query: str, limit: int) -> list[str]:  # pragma: no cover
        """Holt Treffer von cognee und zieht die eingebetteten IDs heraus."""
        import asyncio
        import re

        results = asyncio.run(self._cognee.recall(query, session_id=self._dataset))
        ids: list[str] = []
        for result in results or []:
            for match in re.findall(r"\[(a-[0-9a-f]{12})\]", str(result)):
                if match not in ids:
                    ids.append(match)
        return ids[:limit]

    def close(self) -> None:
        self._store.close()


__all__ = [
    "CogneeBackend",
    "MemoryBackend",
    "SqliteBackend",
    "assertion_from_dict",
]
