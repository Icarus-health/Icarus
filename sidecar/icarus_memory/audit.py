"""Anhängendes Audit-Log.

Nicht dasselbe wie Logging. Ein System, dem man über Jahre vertrauen soll, muss
im Nachhinein erklären können, was es getan hat und warum. Ohne das ist jede
Fehlersuche Spekulation.

Einträge werden **nie** geändert und **nie** gelöscht. Die Tabelle hat bewusst
kein UPDATE und kein DELETE; ein Trigger verhindert beides auch dann, wenn
jemand später anderen Code schreibt.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .model import now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    tool       TEXT NOT NULL,
    action_class TEXT NOT NULL,
    level      TEXT NOT NULL,
    outcome    TEXT NOT NULL,
    approved_by TEXT,
    model      TEXT,
    arguments  TEXT NOT NULL,
    result     TEXT,
    detail     TEXT
);

-- Das Log ist anhängend. Änderungen und Löschungen sind auf Datenbankebene
-- unterbunden, nicht nur per Konvention im Anwendungscode.
CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit
BEGIN
    SELECT RAISE(ABORT, 'Audit-Log ist anhaengend: UPDATE nicht erlaubt');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit
BEGIN
    SELECT RAISE(ABORT, 'Audit-Log ist anhaengend: DELETE nicht erlaubt');
END;
"""


class AuditLog:
    """Führt Buch über jede Aktion, die durch die Policy-Schicht ging."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Siehe SqliteBackend: FastAPI bedient Anfragen aus einem Threadpool.
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def record(
        self,
        tool: str,
        action_class: str,
        level: str,
        outcome: str,
        arguments: dict[str, Any],
        *,
        approved_by: str | None = None,
        model: str | None = None,
        result: str | None = None,
        detail: str | None = None,
        at: datetime | None = None,
    ) -> int:
        """Schreibt einen Eintrag und gibt seine laufende Nummer zurück.

        `outcome` ist eines von: executed, denied, refused, failed, pending.
        """
        at = at or now()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO audit (at, tool, action_class, level, outcome, "
                "approved_by, model, arguments, result, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    at.isoformat(),
                    tool,
                    action_class,
                    level,
                    outcome,
                    approved_by,
                    model,
                    json.dumps(arguments, ensure_ascii=False, default=str),
                    result,
                    detail,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    def entries(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for row in rows:
            entry = dict(row)
            entry["arguments"] = json.loads(entry["arguments"])
            out.append(entry)
        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = ["AuditLog"]
