"""Aktualität: ein Fakt aus dem März wird nicht als Gegenwart behauptet.

Das war die auffälligste Lücke. `Agent.context()` schrieb bloß
`- [kind] statement` — kein Alter, keine Quelle. `last_confirmed_at` wurde von
`confirm()` gesetzt und auf keinem Ausgabepfad gelesen. Damit sah das Modell
eine ein Jahr alte Zustandsbeschreibung genauso wie eine von heute.

Die Regel: jeder Fakt trägt eine Quelle und ein Aktualitätsurteil. Der Horizont
hängt an der Art der Aussage, nicht an einer globalen Zahl — ein Wohnort altert
über Jahre, ein Projektzustand über Tage.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from icarus_memory import (
    Kind,
    MemoryBackend,
    Provenance,
    SelfModelStore,
    Sensitivity,
    SourceType,
)
from icarus_memory.agent import Agent
from icarus_memory.audit import AuditLog
from icarus_memory.currency import Currency, evidence_date, horizon_for, judge
from icarus_memory.model import now
from icarus_memory.policy import Policy
from icarus_memory.providers import Reply

TAG = timedelta(days=1)


class LokalerStub:
    name = "stub"
    model = "stub-1"
    is_local = True

    def __init__(self) -> None:
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools):
        self.seen.append(messages)
        return Reply(text="ok", tool_calls=[])


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.sqlite3")


def _prov(captured=None) -> Provenance:
    return Provenance(
        source_type=SourceType.CHAT, source_ref="chat:42", captured_at=captured
    )


# -- Horizonte hängen an der Art ----------------------------------------------


def test_zustand_altert_schneller_als_identitaet() -> None:
    assert horizon_for(Kind.STATE).stale_after < horizon_for(Kind.IDENTITY).stale_after
    assert horizon_for(Kind.STATE).stale_after < horizon_for(Kind.PREFERENCE).stale_after


def test_jede_art_hat_einen_horizont() -> None:
    for kind in Kind:
        h = horizon_for(kind)
        assert h.stale_after > timedelta(0)


def test_vergangenes_verfaellt_nicht() -> None:
    """Eine Episode ist eine Aussage über die Vergangenheit; sie bleibt wahr."""
    assert horizon_for(Kind.EPISODE).outdated_after is None


# -- Urteil --------------------------------------------------------------------


def test_frischer_zustand_ist_aktuell() -> None:
    store = SelfModelStore(MemoryBackend(), subject_id="s")
    a = store.record(
        kind=Kind.STATE, statement="Projekt A ist blockiert.", provenance=_prov()
    )
    assert judge(a) is Currency.CURRENT


def test_alter_zustand_ist_womoeglich_veraltet() -> None:
    store = SelfModelStore(MemoryBackend(), subject_id="s")
    vor_zwei_wochen = now() - 14 * TAG
    a = store.record(
        kind=Kind.STATE,
        statement="Projekt A ist blockiert.",
        provenance=_prov(captured=vor_zwei_wochen),
        at=vor_zwei_wochen,
    )
    assert judge(a) is Currency.STALE


def test_sehr_alter_zustand_ist_veraltet() -> None:
    store = SelfModelStore(MemoryBackend(), subject_id="s")
    vor_langem = now() - 400 * TAG
    a = store.record(
        kind=Kind.STATE,
        statement="Projekt A ist blockiert.",
        provenance=_prov(captured=vor_langem),
        at=vor_langem,
    )
    assert judge(a) is Currency.OUTDATED


def test_alte_praeferenz_bleibt_aktuell() -> None:
    store = SelfModelStore(MemoryBackend(), subject_id="s")
    vor_zwei_wochen = now() - 14 * TAG
    a = store.record(
        kind=Kind.PREFERENCE,
        statement="Mag ruhige Oberflächen.",
        provenance=_prov(captured=vor_zwei_wochen),
        at=vor_zwei_wochen,
    )
    assert judge(a) is Currency.CURRENT


def test_bestaetigung_macht_einen_fakt_wieder_aktuell() -> None:
    store = SelfModelStore(MemoryBackend(), subject_id="s")
    vor_langem = now() - 60 * TAG
    a = store.record(
        kind=Kind.STATE,
        statement="Projekt A ist blockiert.",
        provenance=_prov(captured=vor_langem),
        at=vor_langem,
    )
    assert judge(a) is Currency.STALE

    bestaetigt = store.confirm(a.id)
    assert judge(bestaetigt) is Currency.CURRENT
    assert evidence_date(bestaetigt) == bestaetigt.last_confirmed_at


# -- Ausgabe -------------------------------------------------------------------


def _agent_mit(store: SelfModelStore, audit: AuditLog) -> Agent:
    return Agent(
        store=store,
        policy=Policy(),
        audit=audit,
        tools={},
        provider=LokalerStub(),
    )


def test_kontext_nennt_die_quelle(audit: AuditLog) -> None:
    store = SelfModelStore(MemoryBackend(), subject_id="s")
    store.record(
        kind=Kind.PREFERENCE, statement="Mag ruhige Oberflächen.", provenance=_prov()
    )
    kontext = _agent_mit(store, audit).context()

    assert "Mag ruhige Oberflächen" in kontext
    assert "chat:42" in kontext, "Die Quellenkennung muss in der Ausgabe stehen"


def test_kontext_markiert_veraltetes_getrennt(audit: AuditLog) -> None:
    store = SelfModelStore(MemoryBackend(), subject_id="s")
    vor_langem = now() - 400 * TAG
    store.record(
        kind=Kind.STATE,
        statement="Wohnt gerade in einer Ferienwohnung.",
        provenance=_prov(captured=vor_langem),
        at=vor_langem,
    )
    store.record(
        kind=Kind.PREFERENCE, statement="Mag ruhige Oberflächen.", provenance=_prov()
    )
    kontext = _agent_mit(store, audit).context()

    aktuell, _, alt = kontext.partition("Alte Angaben")
    assert "Mag ruhige Oberflächen" in aktuell
    assert "Ferienwohnung" not in aktuell, (
        "Veraltetes darf nicht im Block der aktuellen Fakten stehen"
    )
    assert "Ferienwohnung" in alt


def test_kontext_zeigt_stand_bei_angealtertem(audit: AuditLog) -> None:
    store = SelfModelStore(MemoryBackend(), subject_id="s")
    vor_zwei_wochen = now() - 14 * TAG
    store.record(
        kind=Kind.STATE,
        statement="Projekt A ist blockiert.",
        provenance=_prov(captured=vor_zwei_wochen),
        at=vor_zwei_wochen,
    )
    kontext = _agent_mit(store, audit).context()

    assert "Projekt A ist blockiert" in kontext
    assert "Stand" in kontext
    assert vor_zwei_wochen.date().isoformat() in kontext


def test_aktuelles_braucht_keinen_warnhinweis(audit: AuditLog) -> None:
    store = SelfModelStore(MemoryBackend(), subject_id="s")
    store.record(
        kind=Kind.STATE, statement="Projekt A ist blockiert.", provenance=_prov()
    )
    kontext = _agent_mit(store, audit).context()

    assert "womöglich veraltet" not in kontext
    assert "Alte Angaben" not in kontext


def test_systemanweisung_verbietet_behaupten_von_altem(audit: AuditLog) -> None:
    """Die Kennzeichnung nützt nichts, wenn dem Modell die Regel fehlt."""
    from icarus_memory.agent import SYSTEM_PROMPT

    prompt = SYSTEM_PROMPT.lower()
    assert "stand" in prompt, "Das Modell muss die Standangabe kennen"
    assert "frage nach" in prompt or "nachfrag" in prompt
    assert "alte angaben" in prompt, "Der getrennte Block muss erklärt sein"
    assert "weiß ich nicht" in prompt, "Nichtwissen muss erlaubt sein"


def test_geschuetztes_bleibt_trotz_neuer_ausgabe_zurueck(audit: AuditLog) -> None:
    """Die Egress-Sperre darf durch das neue Format nicht umgangen werden."""
    store = SelfModelStore(MemoryBackend(), subject_id="s")
    store.record(
        kind=Kind.IDENTITY,
        statement="Nimmt ein verschreibungspflichtiges Medikament.",
        provenance=_prov(),
        sensitivity=Sensitivity.SPECIAL_CATEGORY,
    )
    kontext = _agent_mit(store, audit).context()

    assert "Medikament" not in kontext
    assert "besonders geschützt" in kontext


# -- Kleinigkeiten, die in der echten Ausgabe auffielen -----------------------


def test_quellenkennung_wird_nicht_doppelt_praefixt() -> None:
    from icarus_memory.currency import source_label

    store = SelfModelStore(MemoryBackend(), subject_id="s")
    aus_mail = store.record(
        kind=Kind.IDENTITY,
        statement="Macht einen MBA.",
        provenance=Provenance(source_type=SourceType.CHAT, source_ref="mail:88"),
    )
    assert source_label(aus_mail) == "mail:88"

    ohne_schema = store.record(
        kind=Kind.IDENTITY,
        statement="Wohnt in Hamburg.",
        provenance=Provenance(source_type=SourceType.CHAT, source_ref="88"),
    )
    assert source_label(ohne_schema) == "chat:88"

    ohne_ref = store.record(
        kind=Kind.IDENTITY,
        statement="Spricht Deutsch.",
        provenance=Provenance(source_type=SourceType.USER_STATED),
    )
    assert source_label(ohne_ref) == "user_stated"


def test_hinweis_auf_geschuetztes_ist_grammatisch_richtig(audit: AuditLog) -> None:
    store = SelfModelStore(MemoryBackend(), subject_id="s")
    store.record(
        kind=Kind.IDENTITY,
        statement="Etwas besonders Geschütztes.",
        provenance=_prov(),
        sensitivity=Sensitivity.SPECIAL_CATEGORY,
    )
    einer = _agent_mit(store, audit).context()
    assert "Eine weitere Aussage ist" in einer
    assert "1 weitere Aussagen" not in einer

    store.record(
        kind=Kind.STATE,
        statement="Noch etwas besonders Geschütztes.",
        provenance=_prov(),
        sensitivity=Sensitivity.SPECIAL_CATEGORY,
    )
    zwei = _agent_mit(store, audit).context()
    assert "2 weitere Aussagen sind" in zwei
