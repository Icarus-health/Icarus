"""Dauerregeln — was Icarus künftig ohne Rückfrage tun darf.

Eine Freigabe gilt einmal. Das ist richtig, kostet aber jedes Mal eine
Entscheidung, und bei der zwanzigsten gleichartigen Rückfrage klickt jeder nur
noch weg — dann ist die Rückfrage keine Kontrolle mehr, sondern ein Ritual.

Eine Dauerregel verlegt die Entscheidung nach vorn: einmal, ausdrücklich,
benannt, widerrufbar. `docs/03-delegation.md` fordert genau das
(„Dauerfreigaben nur explizit, benannt, widerrufbar") und führt es zugleich
als fehlend.

Drei Eigenschaften tragen die Sicherheit:

**Eine Regel gilt nie in einer kontaminierten Runde.** Wenn eine gelesene Mail
im Kontext steht, ist nicht mehr feststellbar, ob die Absicht vom Nutzer kommt
oder aus dem gelesenen Text. Genau dann darf keine Regel greifen — sonst wäre
sie der bequemste Weg, die Eskalation auszuhebeln.

**Eine Regel schlägt keine Grenze.** Ein `constraint` aus dem Selbstmodell
verbietet weiterhin, was er verbietet.

**Eine Regel ist eng.** Sie nennt ein Werkzeug und darf zusätzlich verlangen,
dass bestimmte Argumente genau übereinstimmen. „Mails an Frau Becker ohne
Rückfrage" ist eine Regel; „Mails ohne Rückfrage" wäre eine Blankovollmacht
und lässt sich zwar anlegen, wird aber als solche benannt.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .migrations import (
    Migration,
    run_migrations,
    validate_legacy_or_empty,
    verify_schema,
)
from .model import now

#: Worauf eine Regel eine Stufe senken darf. `deny` steht nicht darin: etwas
#: dauerhaft zu verbieten ist eine Grenze, keine Freigabe, und gehört ins
#: Selbstmodell.
ERLAUBTE_STUFEN = ("auto", "notify", "confirm")


@dataclass
class Regel:
    """Eine benannte Dauerfreigabe."""

    id: str
    name: str
    """Der Satz, den der Nutzer darüber sagen würde. Steht später im Protokoll."""

    tool: str
    stufe: str
    passt_auf: dict[str, str] = field(default_factory=dict)
    """Argumente, die genau übereinstimmen müssen. Leer heißt: jeder Aufruf."""

    angelegt_am: datetime = field(default_factory=now)
    widerrufen_am: datetime | None = None

    @property
    def aktiv(self) -> bool:
        return self.widerrufen_am is None

    @property
    def blanko(self) -> bool:
        """Ohne Einschränkung auf Argumente — gilt für jeden Aufruf des Werkzeugs."""
        return not self.passt_auf

    def trifft(self, tool: str, arguments: dict[str, Any]) -> bool:
        if not self.aktiv or tool != self.tool:
            return False
        for schluessel, wert in self.passt_auf.items():
            # Genauer Vergleich, keine Auslegung. Eine Regel, die „ungefähr"
            # trifft, ist keine Grenze mehr, sondern ein Gefühl.
            if str(arguments.get(schluessel, "")).strip().lower() != wert.strip().lower():
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "tool": self.tool,
            "stufe": self.stufe,
            "passt_auf": self.passt_auf,
            "blanko": self.blanko,
            "angelegt_am": self.angelegt_am.isoformat(),
            "widerrufen_am": self.widerrufen_am.isoformat() if self.widerrufen_am else None,
            "aktiv": self.aktiv,
        }


class RegelFehler(Exception):
    pass


_CREATE_REGELN = """
CREATE TABLE IF NOT EXISTS regeln (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    tool TEXT NOT NULL,
    stufe TEXT NOT NULL,
    passt_auf TEXT NOT NULL,
    angelegt_am TEXT NOT NULL,
    widerrufen_am TEXT
)
"""
_SCHEMA_CONTRACT = {
    "regeln": {
        "id",
        "name",
        "tool",
        "stufe",
        "passt_auf",
        "angelegt_am",
        "widerrufen_am",
    }
}
_PRIMARY_KEYS = {"regeln": {"id"}}


def _migrate_v1(connection: sqlite3.Connection) -> None:
    validate_legacy_or_empty(
        connection,
        store="rules",
        path=connection.execute("PRAGMA database_list").fetchone()[2],
        expected_tables=_SCHEMA_CONTRACT,
        expected_primary_keys=_PRIMARY_KEYS,
    )
    connection.execute(_CREATE_REGELN)


def _verify_v1(connection: sqlite3.Connection) -> None:
    verify_schema(
        connection,
        expected_tables=_SCHEMA_CONTRACT,
        expected_primary_keys=_PRIMARY_KEYS,
    )


_MIGRATIONS = (
    Migration(1, "initial_explicit_version", _migrate_v1, _verify_v1),
)


class RegelStore:
    """Dauerregeln, dauerhaft.

    Widerrufen löscht nicht, sondern setzt einen Zeitpunkt. Wer später im
    Protokoll liest, dass eine Aktion „nach Regel X" lief, muss Regel X noch
    nachschlagen können — auch wenn sie längst zurückgenommen wurde.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        try:
            # Future-Versionen werden abgewiesen, bevor journal_mode die Datei
            # verändern kann.
            run_migrations(
                self._conn,
                store="rules",
                path=self._path,
                migrations=_MIGRATIONS,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()
        except Exception:
            self._conn.close()
            raise

    def anlegen(
        self,
        name: str,
        tool: str,
        stufe: str,
        passt_auf: dict[str, str] | None = None,
    ) -> Regel:
        if stufe not in ERLAUBTE_STUFEN:
            raise RegelFehler(
                f"Stufe {stufe!r} nicht erlaubt. Möglich: {', '.join(ERLAUBTE_STUFEN)}."
            )
        if not name.strip():
            raise RegelFehler("Eine Regel ohne Namen kann später niemand beurteilen.")

        regel = Regel(
            id=f"r-{uuid.uuid4().hex[:12]}",
            name=name.strip(),
            tool=tool,
            stufe=stufe,
            passt_auf={k: str(v) for k, v in (passt_auf or {}).items() if str(v).strip()},
        )
        self._conn.execute(
            "INSERT INTO regeln (id, name, tool, stufe, passt_auf, angelegt_am, widerrufen_am)"
            " VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (regel.id, regel.name, regel.tool, regel.stufe,
             json.dumps(regel.passt_auf), regel.angelegt_am.isoformat()),
        )
        self._conn.commit()
        return regel

    def widerrufen(self, regel_id: str) -> Regel:
        regel = self.holen(regel_id)
        if regel is None:
            raise RegelFehler(f"Keine Regel mit der Kennung {regel_id!r}.")
        if not regel.aktiv:
            return regel
        zeitpunkt = now()
        self._conn.execute(
            "UPDATE regeln SET widerrufen_am = ? WHERE id = ?",
            (zeitpunkt.isoformat(), regel_id),
        )
        self._conn.commit()
        regel.widerrufen_am = zeitpunkt
        return regel

    def holen(self, regel_id: str) -> Regel | None:
        zeile = self._conn.execute(
            "SELECT * FROM regeln WHERE id = ?", (regel_id,)
        ).fetchone()
        return self._aus_zeile(zeile) if zeile else None

    def alle(self, nur_aktive: bool = False) -> list[Regel]:
        sql = "SELECT * FROM regeln"
        if nur_aktive:
            sql += " WHERE widerrufen_am IS NULL"
        sql += " ORDER BY angelegt_am DESC"
        return [self._aus_zeile(z) for z in self._conn.execute(sql).fetchall()]

    def passende(self, tool: str, arguments: dict[str, Any]) -> Regel | None:
        """Die engste aktive Regel, die auf diesen Aufruf passt.

        Engste zuerst: Wer eine Regel für „Mails an Becker" **und** eine für
        „Mails" hat, meint bei einer Mail an Becker die erste. Die weitere wäre
        sonst die stillschweigend wirksame, und das ist die falsche Richtung.
        """
        treffer = [r for r in self.alle(nur_aktive=True) if r.trifft(tool, arguments)]
        if not treffer:
            return None
        return sorted(treffer, key=lambda r: (-len(r.passt_auf), r.angelegt_am))[0]

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _aus_zeile(zeile: tuple) -> Regel:
        return Regel(
            id=zeile[0],
            name=zeile[1],
            tool=zeile[2],
            stufe=zeile[3],
            passt_auf=json.loads(zeile[4]),
            angelegt_am=datetime.fromisoformat(zeile[5]),
            widerrufen_am=datetime.fromisoformat(zeile[6]) if zeile[6] else None,
        )


__all__ = ["ERLAUBTE_STUFEN", "Regel", "RegelFehler", "RegelStore"]
