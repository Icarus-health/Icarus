"""Tests des `disputed`-Status.

Der dritte offene Punkt aus dem Gedächtnis-Kontrakt. Das Ziel ist ausdrücklich
nicht, Widersprüche aufzulösen, sondern sie sichtbar zu markieren, statt das
Modell zwischen zwei gleichrangigen Behauptungen raten zu lassen.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from icarus_memory import MemoryBackend, SelfModelStore
from icarus_memory.agent import Agent
from icarus_memory.audit import AuditLog
from icarus_memory.model import Kind, Provenance, Sensitivity, SourceType, Status
from icarus_memory.policy import Policy
from icarus_memory.store import ConflictError


@pytest.fixture
def store() -> SelfModelStore:
    return SelfModelStore(MemoryBackend(), subject_id="test")


def _record(store: SelfModelStore, statement: str, kind: Kind = Kind.STATE, **kw):
    return store.record(
        statement=statement, kind=kind,
        provenance=Provenance(source_type=SourceType.CHAT, source_ref="chat:1"),
        **kw,
    )


def test_streit_wird_gegenseitig_vermerkt(store: SelfModelStore) -> None:
    """Ein einseitiger Verweis hieße, dass eine Seite unbemerkt als Gegenwart
    auftritt — genau der Schaden, den der Status verhindern soll."""
    a = _record(store, "Wohnt in Hamburg.")
    b = _record(store, "Wohnt in Berlin.")

    store.dispute(a.id, b.id)

    assert store._require(a.id).disputed_with == [b.id]
    assert store._require(b.id).disputed_with == [a.id]
    assert store._require(a.id).status is Status.DISPUTED
    assert store._require(b.id).status is Status.DISPUTED


def test_streit_merkt_sich_seinen_zeitpunkt(store: SelfModelStore) -> None:
    zeitpunkt = datetime(2026, 8, 1, 9, 30, tzinfo=timezone.utc)
    a = _record(store, "Wohnt in Hamburg.")
    b = _record(store, "Wohnt in Berlin.")

    store.dispute(a.id, b.id, at=zeitpunkt)

    assert store._require(a.id).status_changed_at == zeitpunkt
    assert store._require(b.id).status_changed_at == zeitpunkt


def test_strittiges_geht_nicht_als_gewusst_durch(store: SelfModelStore) -> None:
    """Der Kern: Bis hierher gingen beide als `active` in den Prompt."""
    a = _record(store, "Wohnt in Hamburg.")
    b = _record(store, "Wohnt in Berlin.")
    unstrittig = _record(store, "Mag Kaffee.", kind=Kind.PREFERENCE)

    store.dispute(a.id, b.id)

    assert [x.id for x in store.usable()] == [unstrittig.id]
    assert {x.id for x in store.disputed()} == {a.id, b.id}


def test_ein_streit_braucht_zwei_seiten(store: SelfModelStore) -> None:
    a = _record(store, "Allein.")
    with pytest.raises(ConflictError, match="mindestens zwei"):
        store.dispute(a.id)


def test_widerrufenes_streitet_nicht_mehr(store: SelfModelStore) -> None:
    a = _record(store, "Wohnt in Hamburg.")
    b = _record(store, "Wohnt in Berlin.")
    store.redact(a.id)

    with pytest.raises(ConflictError, match="redacted"):
        store.dispute(a.id, b.id)


def test_streit_wird_durch_ersetzung_aufgeloest(store: SelfModelStore) -> None:
    """Kein eigener Beilegungspfad — aufgelöst wird über bestehende,
    protokollierte Wege."""
    a = _record(store, "Wohnt in Hamburg.")
    b = _record(store, "Wohnt in Berlin.")
    store.dispute(a.id, b.id)

    klarheit = store.record(
        statement="Wohnt seit Mai in Berlin.", kind=Kind.STATE,
        provenance=Provenance(source_type=SourceType.USER_STATED),
        supersedes=[a.id, b.id],
    )

    assert store._require(a.id).status is Status.SUPERSEDED
    assert store._require(b.id).status is Status.SUPERSEDED
    assert [x.id for x in store.usable()] == [klarheit.id]


def test_streit_wird_durch_ruecknahme_aufgeloest(store: SelfModelStore) -> None:
    a = _record(store, "Wohnt in Hamburg.")
    b = _record(store, "Wohnt in Berlin.")
    store.dispute(a.id, b.id)

    store.retract(a.id)

    assert store._require(a.id).status is Status.RETRACTED
    # b bleibt strittig markiert, bis jemand es bestätigt — das System löst
    # nichts von selbst auf.
    assert store._require(b.id).status is Status.DISPUTED


def test_strittiges_ueberlebt_das_speichern(tmp_path) -> None:
    from icarus_memory.backends import SqliteBackend

    pfad = tmp_path / "m.sqlite3"
    erste = SelfModelStore(SqliteBackend(pfad), subject_id="t")
    a = _record(erste, "Wohnt in Hamburg.")
    b = _record(erste, "Wohnt in Berlin.")
    erste.dispute(a.id, b.id)

    zweite = SelfModelStore(SqliteBackend(pfad), subject_id="t")
    wieder = zweite._require(a.id)
    assert wieder.status is Status.DISPUTED
    assert wieder.disputed_with == [b.id]


# -- Was das Modell zu sehen bekommt ---------------------------------------


def _agent(store: SelfModelStore, tmp_path) -> Agent:
    return Agent(store=store, policy=Policy(),
                 audit=AuditLog(tmp_path / "audit.sqlite3"), tools={})


def test_kontext_zeigt_strittiges_getrennt(store: SelfModelStore, tmp_path) -> None:
    """Weglassen wäre bequem und falsch — der Nutzer hat es gesagt. Unter „was
    du weißt" führen wäre schlimmer — dann wählt das Modell eine Seite."""
    a = _record(store, "Wohnt in Hamburg.")
    b = _record(store, "Wohnt in Berlin.")
    _record(store, "Mag Kaffee.", kind=Kind.PREFERENCE)
    store.dispute(a.id, b.id)

    text = _agent(store, tmp_path).context()

    assert "Was du über den Nutzer weißt:" in text
    assert "Mag Kaffee." in text
    assert "Ungeklärt" in text

    # Beide Seiten stehen da, aber unter der Streit-Überschrift.
    wissen, _, streit = text.partition("Ungeklärt")
    assert "Hamburg" not in wissen and "Berlin" not in wissen
    assert "Hamburg" in streit and "Berlin" in streit


def test_ohne_streit_keine_ueberschrift(store: SelfModelStore, tmp_path) -> None:
    _record(store, "Mag Kaffee.", kind=Kind.PREFERENCE)
    assert "Ungeklärt" not in _agent(store, tmp_path).context()


def test_besonders_geschuetztes_streitet_still(store: SelfModelStore, tmp_path) -> None:
    """Der Schutzbedarf schlägt auch hier: Was nicht hinaus darf, darf auch
    nicht als Widerspruch hinaus."""
    a = _record(store, "Sehr private Angabe A.", sensitivity=Sensitivity.SPECIAL_CATEGORY)
    b = _record(store, "Sehr private Angabe B.", sensitivity=Sensitivity.SPECIAL_CATEGORY)
    store.dispute(a.id, b.id)

    text = _agent(store, tmp_path).context()

    assert "Sehr private Angabe" not in text
    assert "Ungeklärt" not in text
