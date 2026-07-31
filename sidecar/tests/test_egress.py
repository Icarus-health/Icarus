"""Egress-Sperre: sensible Fakten dürfen lokal genutzt werden, aber nicht
automatisch an einen externen Anbieter gehen.

Der Schutzbedarf war bisher nur eine Markierung. `Agent` hob die Obergrenze auf
`sensitive`, und in `providers.py` gab es keine Prüfung — als `sensitive`
markierte Aussagen gingen damit an OpenAI oder Anthropic.

Diese Tests halten die Regel fest: die Obergrenze folgt dem Anbieter, nicht der
Bequemlichkeit, und die Nutzlast wird unmittelbar vor dem Versand ein zweites
Mal unabhängig geprüft.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from icarus_memory import MemoryBackend
from icarus_memory.agent import Agent, EgressBlocked
from icarus_memory.audit import AuditLog
from icarus_memory.model import Kind, Provenance, Sensitivity, SourceType
from icarus_memory.policy import Policy
from icarus_memory.providers import Reply, is_local_endpoint
from icarus_memory.store import SelfModelStore


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.sqlite3")


class StubProvider:
    """Ein Anbieter, der die gesehene Nutzlast festhält, statt zu senden."""

    name = "stub"
    model = "stub-1"

    def __init__(self, *, is_local: bool) -> None:
        self.is_local = is_local
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools):
        self.seen.append(messages)
        return Reply(text="verstanden", tool_calls=[])


def _store() -> SelfModelStore:
    store = SelfModelStore(MemoryBackend(), subject_id="soeren")
    store.record(
        kind=Kind.PREFERENCE,
        statement="Mag ruhige, klare Oberflächen.",
        provenance=Provenance(source_type=SourceType.USER_STATED),
        sensitivity=Sensitivity.NORMAL,
    )
    store.record(
        kind=Kind.STATE,
        statement="Schläft seit Wochen schlecht.",
        provenance=Provenance(source_type=SourceType.USER_STATED),
        sensitivity=Sensitivity.SENSITIVE,
    )
    store.record(
        kind=Kind.IDENTITY,
        statement="Nimmt ein verschreibungspflichtiges Medikament.",
        provenance=Provenance(source_type=SourceType.USER_STATED),
        sensitivity=Sensitivity.SPECIAL_CATEGORY,
    )
    return store


def _agent(provider: StubProvider, audit: AuditLog) -> Agent:
    return Agent(
        store=_store(),
        policy=Policy(),
        audit=audit,
        tools={},
        provider=provider,
    )


# -- Erkennung des Anbieterorts ------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434/v1",
        "http://[::1]:11434/v1",
        "https://127.0.0.1/v1",
    ],
)
def test_loopback_gilt_als_lokal(url: str) -> None:
    assert is_local_endpoint(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://api.anthropic.com/v1",
        "http://192.168.1.10:11434/v1",
        "http://ollama.example.com/v1",
    ],
)
def test_alles_andere_gilt_als_extern(url: str) -> None:
    assert is_local_endpoint(url) is False


# -- Obergrenze folgt dem Anbieter --------------------------------------------


def test_externer_anbieter_bekommt_nichts_sensibles(audit: AuditLog) -> None:
    provider = StubProvider(is_local=False)
    agent = _agent(provider, audit)
    kontext = agent.context()

    assert "ruhige, klare Oberflächen" in kontext
    assert "Schläft seit Wochen schlecht" not in kontext
    assert "verschreibungspflichtiges Medikament" not in kontext
    assert "2 weitere Aussagen" in kontext


def test_lokaler_anbieter_darf_sensibles_nutzen(audit: AuditLog) -> None:
    provider = StubProvider(is_local=True)
    agent = _agent(provider, audit)
    kontext = agent.context()

    assert "Schläft seit Wochen schlecht" in kontext
    # Besonders geschützt bleibt auch lokal zurück, bis es ausdrücklich
    # freigegeben wird.
    assert "verschreibungspflichtiges Medikament" not in kontext


def test_ohne_anbieter_gilt_die_strengste_grenze(audit: AuditLog) -> None:
    agent = Agent(store=_store(), policy=Policy(), audit=audit, tools={}, provider=None)
    kontext = agent.context()

    assert "Schläft seit Wochen schlecht" not in kontext


def test_nutzer_darf_die_grenze_senken_aber_nicht_heben(audit: AuditLog) -> None:
    provider = StubProvider(is_local=False)
    store = _store()
    # Der Aufrufer verlangt ausdrücklich `special_category`. Der externe
    # Anbieter deckelt das trotzdem auf `normal`.
    agent = Agent(
        store=store,
        policy=Policy(),
        audit=audit,
        tools={},
        provider=provider,
        max_sensitivity=Sensitivity.SPECIAL_CATEGORY,
    )
    assert agent.effective_sensitivity_ceiling() == Sensitivity.NORMAL

    lokal = Agent(
        store=_store(),
        policy=Policy(),
        audit=audit,
        tools={},
        provider=StubProvider(is_local=True),
        max_sensitivity=Sensitivity.NORMAL,
    )
    assert lokal.effective_sensitivity_ceiling() == Sensitivity.NORMAL


# -- Zweite, unabhängige Prüfung der Nutzlast ---------------------------------


def test_nutzlast_wird_vor_dem_versand_geprueft(audit: AuditLog) -> None:
    """Auch wenn der Kontextaufbau umgangen wird, darf nichts hinausgehen."""
    provider = StubProvider(is_local=False)
    agent = _agent(provider, audit)

    geschmuggelt = [
        {"role": "system", "content": "Du bist Icarus."},
        {"role": "user", "content": "Schläft seit Wochen schlecht."},
    ]
    with pytest.raises(EgressBlocked):
        agent.assert_egress_allowed(geschmuggelt)

    assert provider.seen == []


def test_lokale_nutzlast_darf_sensibles_enthalten(audit: AuditLog) -> None:
    provider = StubProvider(is_local=True)
    agent = _agent(provider, audit)

    agent.assert_egress_allowed(
        [{"role": "system", "content": "Schläft seit Wochen schlecht."}]
    )


def test_besonders_geschuetztes_geht_auch_lokal_nicht_ungefragt_raus(audit: AuditLog) -> None:
    provider = StubProvider(is_local=True)
    agent = _agent(provider, audit)

    with pytest.raises(EgressBlocked):
        agent.assert_egress_allowed(
            [{"role": "user", "content": "Nimmt ein verschreibungspflichtiges Medikament."}]
        )


def test_regulaerer_versand_an_externen_anbieter_bleibt_moeglich(audit: AuditLog) -> None:
    provider = StubProvider(is_local=False)
    agent = _agent(provider, audit)
    turn = agent.send("Wie soll ich das Dashboard bauen?")

    assert turn.reply == "verstanden"
    assert provider.seen, "Der Anbieter muss regulär aufgerufen worden sein"
    versandt = "\n".join(
        m["content"] for m in provider.seen[0] if isinstance(m.get("content"), str)
    )
    assert "ruhige, klare Oberflächen" in versandt
    assert "Schläft seit Wochen schlecht" not in versandt


def test_blockierter_egress_wird_protokolliert(audit: AuditLog) -> None:
    provider = StubProvider(is_local=False)
    agent = Agent(
        store=_store(), policy=Policy(), audit=audit, tools={}, provider=provider
    )

    with pytest.raises(EgressBlocked):
        agent.assert_egress_allowed(
            [{"role": "user", "content": "Schläft seit Wochen schlecht."}]
        )

    eintraege = audit.entries()
    assert any("egress" in str(e).lower() for e in eintraege), (
        "Eine verweigerte Ausleitung muss im Audit-Log stehen"
    )
