"""Die Aussagenschicht ist append-only.

Bisher war nur das Audit-Log wirklich unveränderlich. `SqliteBackend.put()`
schrieb `ON CONFLICT(id) DO UPDATE SET … statement=excluded.statement`, also
konnte der Rohinhalt einer Aussage überschrieben werden. Damit war die
Ersetzungskette nur eine Konvention: wer `put()` mit geändertem Text aufrief,
schrieb Geschichte um, ohne Spur.

Die Regel: Statuswechsel bleiben erlaubt — genau davon leben Ersetzung, Ablauf
und Bestätigung. Der Inhalt ist unveränderlich. Die einzige Ausnahme ist der
Widerruf, denn Löschen auf Wunsch der Person muss möglich bleiben; er ist aber
ausdrücklich, verändert den Status auf `redacted` und hinterlässt einen
Grabstein.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from icarus_memory import Kind, MemoryBackend, Provenance, SelfModelStore, SourceType
from icarus_memory.backends import ImmutableContentError, SqliteBackend
from icarus_memory.model import RedactionReason, Status


def _prov() -> Provenance:
    return Provenance(source_type=SourceType.USER_STATED, source_ref="chat:1")


@pytest.fixture
def sqlite_backend(tmp_path: Path) -> SqliteBackend:
    return SqliteBackend(tmp_path / "record.sqlite3")


# -- Was erlaubt bleiben muss --------------------------------------------------


def test_statuswechsel_bleibt_erlaubt(sqlite_backend: SqliteBackend) -> None:
    store = SelfModelStore(sqlite_backend, subject_id="s")
    erste = store.record(
        kind=Kind.PREFERENCE, statement="Trinkt morgens Kaffee.", provenance=_prov()
    )
    zweite = store.record(
        kind=Kind.PREFERENCE,
        statement="Trinkt morgens Tee.",
        provenance=_prov(),
        supersedes=[erste.id],
    )

    nachher = sqlite_backend.get(erste.id)
    assert nachher is not None
    assert nachher.status is Status.SUPERSEDED
    assert nachher.superseded_by == zweite.id
    assert nachher.statement == "Trinkt morgens Kaffee.", "Der Inhalt bleibt lesbar"


def test_widerruf_darf_den_inhalt_ersetzen(sqlite_backend: SqliteBackend) -> None:
    store = SelfModelStore(sqlite_backend, subject_id="s")
    a = store.record(
        kind=Kind.STATE, statement="Etwas sehr Privates.", provenance=_prov()
    )

    store.redact(a.id, reason=RedactionReason.USER_REQUEST)

    nachher = sqlite_backend.get(a.id)
    assert nachher is not None
    assert nachher.status is Status.REDACTED
    assert "Etwas sehr Privates" not in nachher.statement
    assert nachher.redaction is not None


def test_bestaetigung_bleibt_erlaubt(sqlite_backend: SqliteBackend) -> None:
    store = SelfModelStore(sqlite_backend, subject_id="s")
    a = store.record(kind=Kind.IDENTITY, statement="Wohnt in Hamburg.", provenance=_prov())
    store.confirm(a.id)

    nachher = sqlite_backend.get(a.id)
    assert nachher is not None
    assert nachher.last_confirmed_at is not None


# -- Was nicht mehr gehen darf -------------------------------------------------


def test_inhalt_ueberschreiben_scheitert_in_sqlite(sqlite_backend: SqliteBackend) -> None:
    store = SelfModelStore(sqlite_backend, subject_id="s")
    a = store.record(
        kind=Kind.PREFERENCE, statement="Trinkt morgens Kaffee.", provenance=_prov()
    )

    gefaelscht = replace(a, statement="Trinkt morgens Tee.")
    with pytest.raises(ImmutableContentError):
        sqlite_backend.put(gefaelscht)

    assert sqlite_backend.get(a.id).statement == "Trinkt morgens Kaffee."


def test_inhalt_ueberschreiben_scheitert_im_speicher() -> None:
    backend = MemoryBackend()
    store = SelfModelStore(backend, subject_id="s")
    a = store.record(
        kind=Kind.PREFERENCE, statement="Trinkt morgens Kaffee.", provenance=_prov()
    )

    with pytest.raises(ImmutableContentError):
        backend.put(replace(a, statement="Trinkt morgens Tee."))

    assert backend.get(a.id).statement == "Trinkt morgens Kaffee."


def test_art_und_aufnahmezeit_sind_unveraenderlich(sqlite_backend: SqliteBackend) -> None:
    store = SelfModelStore(sqlite_backend, subject_id="s")
    a = store.record(kind=Kind.PREFERENCE, statement="Mag klare Layouts.", provenance=_prov())

    with pytest.raises(ImmutableContentError):
        sqlite_backend.put(replace(a, kind=Kind.IDENTITY))

    with pytest.raises(ImmutableContentError):
        sqlite_backend.put(replace(a, recorded_at=a.recorded_at.replace(year=2020)))


def test_herkunft_ist_unveraenderlich(sqlite_backend: SqliteBackend) -> None:
    store = SelfModelStore(sqlite_backend, subject_id="s")
    a = store.record(kind=Kind.PREFERENCE, statement="Mag klare Layouts.", provenance=_prov())

    umgeschrieben = replace(
        a,
        provenance=Provenance(source_type=SourceType.USER_STATED, source_ref="chat:999"),
    )
    with pytest.raises(ImmutableContentError):
        sqlite_backend.put(umgeschrieben)


# -- Die Sperre sitzt in der Datenbank, nicht nur im Python-Code ---------------


def test_trigger_haelt_auch_bei_direktem_sql(sqlite_backend: SqliteBackend) -> None:
    """Wer die Bibliothek umgeht, kommt trotzdem nicht durch."""
    store = SelfModelStore(sqlite_backend, subject_id="s")
    a = store.record(
        kind=Kind.PREFERENCE, statement="Trinkt morgens Kaffee.", provenance=_prov()
    )

    conn = sqlite3.connect(str(sqlite_backend.path))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE assertions SET statement = ? WHERE id = ?",
                ("Etwas ganz anderes.", a.id),
            )
            conn.commit()
    finally:
        conn.close()

    assert sqlite_backend.get(a.id).statement == "Trinkt morgens Kaffee."


def test_loeschen_ist_in_sqlite_verboten(sqlite_backend: SqliteBackend) -> None:
    store = SelfModelStore(sqlite_backend, subject_id="s")
    a = store.record(kind=Kind.EPISODE, statement="Gespräch am Montag.", provenance=_prov())

    conn = sqlite3.connect(str(sqlite_backend.path))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM assertions WHERE id = ?", (a.id,))
            conn.commit()
    finally:
        conn.close()

    assert sqlite_backend.get(a.id) is not None


def test_bestehende_datenbank_bekommt_die_trigger_nachtraeglich(tmp_path: Path) -> None:
    """Eine Datei aus der Zeit vor der Regel wird beim Öffnen nachgezogen."""
    pfad = tmp_path / "alt.sqlite3"
    conn = sqlite3.connect(str(pfad))
    conn.executescript(
        """
        CREATE TABLE assertions (
            id TEXT PRIMARY KEY, recorded_at TEXT NOT NULL, status TEXT NOT NULL,
            kind TEXT NOT NULL, statement TEXT NOT NULL, document TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    backend = SqliteBackend(pfad)
    store = SelfModelStore(backend, subject_id="s")
    a = store.record(kind=Kind.PREFERENCE, statement="Mag klare Layouts.", provenance=_prov())

    conn = sqlite3.connect(str(pfad))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE assertions SET statement = 'x' WHERE id = ?", (a.id,))
            conn.commit()
    finally:
        conn.close()


def test_ersetzungskette_ueberlebt_neu_oeffnen(tmp_path: Path) -> None:
    pfad = tmp_path / "record.sqlite3"
    backend = SqliteBackend(pfad)
    store = SelfModelStore(backend, subject_id="s")
    erste = store.record(kind=Kind.STATE, statement="Projekt A läuft.", provenance=_prov())
    store.record(
        kind=Kind.STATE,
        statement="Projekt A ist fertig.",
        provenance=_prov(),
        supersedes=[erste.id],
    )

    wieder = SelfModelStore(SqliteBackend(pfad), subject_id="s")
    verlauf = wieder.history(erste.id)
    assert len(verlauf) == 2
    assert verlauf[0].statement == "Projekt A läuft."
