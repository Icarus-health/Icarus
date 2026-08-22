"""Tests für Policy, Audit und den Agent-Loop.

Laufen ohne Netz und ohne Modell: Der Provider wird durch ein Skript ersetzt,
das vorgegebene Antworten liefert. Damit ist der Kontrollfluss prüfbar —
gerade der Teil, bei dem etwas ausgeführt wird.
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
from icarus_memory.model import now
from icarus_memory.policy import (
    ActionClass,
    ApprovalLevel,
    Policy,
    PolicyError,
)
from icarus_memory.providers import Reply, ToolCall
from icarus_memory.tools import Tool, build_registry


class ScriptedProvider:
    """Liefert vorbereitete Antworten, statt ein Modell zu fragen."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, *replies: Reply) -> None:
        self._replies = list(replies)
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools):
        self.seen.append(messages)
        return self._replies.pop(0) if self._replies else Reply(text="fertig")


@pytest.fixture
def store() -> SelfModelStore:
    return SelfModelStore(MemoryBackend(), subject_id="test")


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.sqlite3")


def chat_prov() -> Provenance:
    return Provenance(source_type=SourceType.CHAT)


def make_agent(store, audit, *replies, policy=None, sink=None, max_sensitivity=Sensitivity.SENSITIVE):
    return Agent(
        store=store,
        policy=policy or Policy(),
        audit=audit,
        tools=build_registry(store, outward_sink=sink),
        provider=ScriptedProvider(*replies),
        max_sensitivity=max_sensitivity,
    )


# -- Policy ----------------------------------------------------------------


def test_klassen_bestimmen_die_stufe() -> None:
    p = Policy()
    assert p.decide("x", ActionClass.READ, {}).level is ApprovalLevel.AUTO
    assert p.decide("x", ActionClass.WRITE_LOCAL, {}).level is ApprovalLevel.NOTIFY
    # Außenwirksames verlangt immer die strenge Bestätigung.
    d = p.decide("x", ActionClass.OUTWARD, {})
    assert d.level is ApprovalLevel.CONFIRM_STRICT
    assert d.needs_approval


def test_constraint_verbietet_aktion() -> None:
    p = Policy()
    d = p.decide(
        "mail_senden",
        ActionClass.OUTWARD,
        {"to": "chef@example.com"},
        constraints=["Niemals Mails an meinen Chef senden."],
    )
    assert d.denied
    assert "Grenze" in d.reasons[0]


def test_constraint_greift_nicht_bei_anderem() -> None:
    p = Policy()
    d = p.decide(
        "web_abruf",
        ActionClass.READ,
        {"url": "https://example.com"},
        constraints=["Niemals Bestellungen auslösen."],
    )
    assert not d.denied


def test_strenge_freigabe_verlangt_wiederholung() -> None:
    p = Policy()
    d = p.decide("mail_senden", ActionClass.OUTWARD, {"to": "a@b.de"})
    ap = p.request("mail_senden", {"to": "a@b.de"}, d, "Trockenlauf")
    assert ap.confirmation_phrase == "a@b.de"

    with pytest.raises(PolicyError, match="stimmt nicht überein"):
        p.grant(ap.id, confirmation="falsch")
    # Der Antrag überlebt den Vertipper — sonst müsste der Nutzer den ganzen
    # Vorgang wiederholen und gewöhnt sich an, Bestätigungen wegzuklicken.
    assert len(p.pending()) == 1
    assert p.grant(ap.id, confirmation="a@b.de").tool == "mail_senden"


def test_freigabe_gilt_nur_einmal() -> None:
    p = Policy()
    d = p.decide("merken", ActionClass.WRITE_LOCAL, {})
    ap = p.request("merken", {}, d, "Trockenlauf")
    p.grant(ap.id)
    with pytest.raises(PolicyError, match="Unbekannte oder abgelaufene"):
        p.grant(ap.id)


def test_freigabe_laeuft_ab() -> None:
    p = Policy(ttl=timedelta(minutes=1))
    d = p.decide("merken", ActionClass.WRITE_LOCAL, {})
    ap = p.request("merken", {}, d, "Trockenlauf", at=now())
    assert p.pending(at=now() + timedelta(seconds=30))
    assert p.pending(at=now() + timedelta(minutes=2)) == []


def test_verweigerung_verbraucht_den_antrag() -> None:
    p = Policy()
    d = p.decide("mail_senden", ActionClass.OUTWARD, {"to": "a@b.de"})
    ap = p.request("mail_senden", {"to": "a@b.de"}, d, "T")
    assert p.reject(ap.id).tool == "mail_senden"
    assert p.pending() == []


# -- Audit -----------------------------------------------------------------


def test_audit_ist_anhaengend(audit: AuditLog) -> None:
    """Kein UPDATE, kein DELETE — auf Datenbankebene, nicht per Konvention."""
    import sqlite3

    audit.record("t", "read", "auto", "executed", {"a": 1})
    conn = audit._conn
    with pytest.raises(sqlite3.IntegrityError, match="anhaengend"):
        conn.execute("UPDATE audit SET outcome='geaendert'")
    with pytest.raises(sqlite3.IntegrityError, match="anhaengend"):
        conn.execute("DELETE FROM audit")


def test_audit_haelt_argumente_fest(audit: AuditLog) -> None:
    audit.record("mail_senden", "outward", "confirm_strict", "executed",
                 {"to": "a@b.de"}, approved_by="user", model="m1")
    entry = audit.entries()[0]
    assert entry["arguments"]["to"] == "a@b.de"
    assert entry["approved_by"] == "user"
    assert entry["model"] == "m1"


# -- Kontext aus dem Selbstmodell -----------------------------------------


def test_kontext_enthaelt_nur_gueltiges(store, audit) -> None:
    alt = store.record("Wohnt in Hamburg.", Kind.STATE, chat_prov())
    store.record("Wohnt in Leipzig.", Kind.STATE, chat_prov(), supersedes=[alt.id])
    agent = make_agent(store, audit)

    ctx = agent.context()
    assert "Leipzig" in ctx
    # Der entscheidende Punkt: Veraltetes darf nicht ins Modell.
    assert "Hamburg" not in ctx


def test_kontext_haelt_besonders_geschuetztes_zurueck(store, audit) -> None:
    store.record("Mag knappe Antworten.", Kind.PREFERENCE, chat_prov())
    store.record(
        "Hat eine chronische Erkrankung.", Kind.STATE, chat_prov(),
        sensitivity=Sensitivity.SPECIAL_CATEGORY,
    )
    agent = make_agent(store, audit)

    ctx = agent.context()
    assert "knappe Antworten" in ctx
    assert "Erkrankung" not in ctx
    # Die Lücke wird benannt, statt sie zu verschweigen.
    assert "besonders geschützt" in ctx


def test_kontext_markiert_ableitungen(store, audit) -> None:
    q = store.record("Arbeitet im Gesundheitswesen.", Kind.IDENTITY, chat_prov())
    store.record(
        "Interessiert sich vermutlich für Medizinrecht.", Kind.PREFERENCE,
        Provenance(source_type=SourceType.INFERENCE), derived_from=[q.id],
    )
    assert "selbst gefolgert" in make_agent(store, audit).context()


def test_kontext_listet_grenzen(store, audit) -> None:
    store.record("Niemals Mails ohne Rückfrage senden.", Kind.CONSTRAINT, chat_prov())
    assert "Bindende Grenzen" in make_agent(store, audit).context()


# -- Agent-Loop ------------------------------------------------------------


def test_lesendes_werkzeug_laeuft_ohne_rueckfrage(store, audit) -> None:
    agent = make_agent(
        store, audit,
        Reply(tool_calls=[ToolCall("1", "aktuelle_zeit", {})]),
        Reply(text="Es ist soweit."),
    )
    turn = agent.send("Wie spät ist es?")
    assert turn.reply == "Es ist soweit."
    assert turn.approvals == []
    assert "aktuelle_zeit" in turn.used_tools
    assert audit.entries()[0]["outcome"] == "executed"


def test_schreiben_laeuft_meldet_aber(store, audit) -> None:
    agent = make_agent(
        store, audit,
        Reply(tool_calls=[ToolCall("1", "merken", {"statement": "Wohnt in Leipzig.", "kind": "state"})]),
        Reply(text="Gemerkt."),
    )
    turn = agent.send("Ich wohne in Leipzig.")
    assert turn.approvals == []
    assert any("Wohnt in Leipzig" in n for n in turn.notices)
    assert [a.statement for a in store.usable()] == ["Wohnt in Leipzig."]


def test_aussenwirksames_wird_angehalten(store, audit) -> None:
    """Der Kern von Säule 4: nichts verlässt das Haus ohne Freigabe."""
    sent: list[dict] = []
    agent = make_agent(
        store, audit,
        Reply(tool_calls=[ToolCall("1", "mail_senden", {
            "to": "team@example.com", "subject": "Status", "body": "Alles gut."})]),
        sink=lambda p: sent.append(p) or "gesendet",
    )
    turn = agent.send("Schick dem Team eine Statusmail.")

    assert len(turn.approvals) == 1
    assert sent == []  # nichts ist rausgegangen
    ap = turn.approvals[0]
    assert ap.decision.level is ApprovalLevel.CONFIRM_STRICT
    # Der Trockenlauf zeigt den vollständigen Inhalt, nicht eine Zusammenfassung.
    assert "team@example.com" in ap.dry_run
    assert "Alles gut." in ap.dry_run
    assert audit.entries()[0]["outcome"] == "pending"


def test_freigabe_fuehrt_aus(store, audit) -> None:
    sent: list[dict] = []
    agent = make_agent(
        store, audit,
        Reply(tool_calls=[ToolCall("1", "mail_senden", {
            "to": "team@example.com", "subject": "S", "body": "B"})]),
        Reply(text="Ist raus."),
        sink=lambda p: (sent.append(p), "gesendet")[1],
    )
    ap = agent.send("Mail an das Team.").approvals[0]

    turn = agent.resolve(ap.id, granted=True, confirmation="team@example.com")
    assert len(sent) == 1
    assert sent[0]["to"] == "team@example.com"
    outcomes = [e["outcome"] for e in audit.entries()]
    assert "executed" in outcomes
    assert turn.reply == "Ist raus."


def test_verweigerung_fuehrt_nicht_aus(store, audit) -> None:
    sent: list[dict] = []
    agent = make_agent(
        store, audit,
        Reply(tool_calls=[ToolCall("1", "mail_senden", {
            "to": "a@b.de", "subject": "S", "body": "B"})]),
        sink=lambda p: sent.append(p) or "gesendet",
    )
    ap = agent.send("Mail.").approvals[0]

    turn = agent.resolve(ap.id, granted=False)
    assert sent == []
    assert "abgelehnt" in turn.reply.casefold()
    assert audit.entries()[0]["outcome"] == "refused"


def test_falsche_bestaetigung_haelt_den_antrag_offen(store, audit) -> None:
    """Regression: ein Vertipper darf nicht wie eine erledigte Freigabe aussehen.

    Vorher lieferte der Endpunkt HTTP 200, worauf die Oberfläche die
    Freigabekarte entfernt hätte — obwohl nichts ausgeführt wurde.
    """
    sent: list[dict] = []
    agent = make_agent(
        store, audit,
        Reply(tool_calls=[ToolCall("1", "mail_senden", {
            "to": "team@example.com", "subject": "S", "body": "B"})]),
        Reply(text="Ist raus."),
        sink=lambda p: (sent.append(p), "gesendet")[1],
    )
    ap = agent.send("Mail.").approvals[0]

    with pytest.raises(PolicyError, match="stimmt nicht überein"):
        agent.resolve(ap.id, granted=True, confirmation="tippfehler@example.com")
    assert sent == []
    assert len(agent.policy.pending()) == 1

    agent.resolve(ap.id, granted=True, confirmation="team@example.com")
    assert len(sent) == 1


def test_grenze_verhindert_ausfuehrung(store, audit) -> None:
    """Eine Grenze aus dem Selbstmodell schlägt die Anweisung des Modells."""
    sent: list[dict] = []
    store.record("Niemals Mails senden.", Kind.CONSTRAINT, chat_prov())
    agent = make_agent(
        store, audit,
        Reply(tool_calls=[ToolCall("1", "mail_senden", {
            "to": "a@b.de", "subject": "S", "body": "B"})]),
        Reply(text="Das geht nicht."),
        sink=lambda p: sent.append(p) or "gesendet",
    )
    turn = agent.send("Schick eine Mail.")

    assert sent == []
    assert turn.approvals == []  # gar nicht erst zur Freigabe vorgelegt
    assert audit.entries()[0]["outcome"] == "denied"


def test_ohne_modell_bleibt_das_gedaechtnis_nutzbar(store, audit) -> None:
    agent = Agent(store, Policy(), audit, build_registry(store), provider=None)
    turn = agent.send("Hallo?")
    assert "kein Modell" in turn.reply
    # Speichern, lesen und widerrufen hängen nicht am Modell.
    a = store.record("Etwas.", Kind.STATE, chat_prov())
    assert len(store.usable()) == 1
    store.redact(a.id)
    assert store.usable() == []


def test_unbekanntes_werkzeug_bricht_nicht_ab(store, audit) -> None:
    agent = make_agent(
        store, audit,
        Reply(tool_calls=[ToolCall("1", "gibtsnicht", {})]),
        Reply(text="Ging nicht."),
    )
    assert agent.send("Tu was.").reply == "Ging nicht."


def test_ein_falsch_benannter_parameter_wird_erklaert() -> None:
    """Der Nutzer sah bisher `build_registry.<locals>.remember() missing 1
    required positional argument: 'statement'` im Gespräch.

    Ein interner Funktionsname für ihn, und für das Modell kein Signal, womit
    es den Aufruf reparieren könnte. Kleine Modelle benennen Parameter
    regelmäßig falsch — sie sollen erfahren, wie die Felder heißen.
    """
    from icarus_memory.agent import _fehlende_pflichtfelder
    from icarus_memory.policy import ActionClass
    from icarus_memory.tools import Tool

    werkzeug = Tool(
        name="merken",
        description="x",
        parameters={
            "type": "object",
            "properties": {"statement": {"type": "string"}, "kind": {"type": "string"}},
            "required": ["statement"],
        },
        action_class=ActionClass.WRITE_LOCAL,
        run=lambda **_: "ok",
        dry_run=lambda _: "x",
    )

    # Deutsch benannt statt englisch — der häufige Fall.
    assert _fehlende_pflichtfelder(werkzeug, {"aussage": "x"}) == ["statement"]
    # Richtig benannt: nichts fehlt.
    assert _fehlende_pflichtfelder(werkzeug, {"statement": "x"}) == []
    # Ein Werkzeug ohne Pflichtfelder verlangt nichts.
    werkzeug.parameters = {"type": "object", "properties": {}}
    assert _fehlende_pflichtfelder(werkzeug, {}) == []


# -- Kein Antrag für einen Aufruf, der gar nicht laufen kann -----------------


def test_unvollstaendiger_aufruf_wird_nie_zur_freigabe(store, audit) -> None:
    """Ein Trockenlauf mit „An: None“ darf niemandem vorgelegt werden.

    Kleine Modelle benennen Parameter regelmäßig falsch — `empfaenger` statt
    `to`. Vorher entstand daraus ein vollwertiger Freigabeantrag: strenge
    Bestätigung, Bestätigungsphrase aus dem falsch benannten Feld, und ein
    Trockenlauf, in dem Empfänger, Betreff und Text alle `None` waren. Wer die
    Phrase tippt, gibt damit etwas frei, das er nie gesehen hat — genau die
    Zusage, die der Trockenlauf tragen soll.
    """
    gesendet: list[tuple] = []
    agent = make_agent(
        store,
        audit,
        Reply(tool_calls=[ToolCall(
            id="r1",
            name="mail_senden",
            # Falsch benannt: das Werkzeug erwartet to/subject/body.
            arguments={"empfaenger": "becker@example.com", "betreff": "Hallo", "text": "…"},
        )]),
        sink=lambda *a: gesendet.append(a),
    )

    turn = agent.send("Schick eine Mail")

    assert turn.approvals == [], "Kein Antrag für einen unvollständigen Aufruf"
    assert gesendet == [], "Und erst recht nichts gesendet"


def test_das_modell_erfaehrt_wie_die_felder_heissen(store, audit) -> None:
    """Damit es den Aufruf reparieren kann, statt blind zu wiederholen."""
    agent = make_agent(
        store,
        audit,
        Reply(tool_calls=[ToolCall(id="r1", name="mail_senden", arguments={"empfaenger": "x@y.z"})]),
        Reply(text="Verstanden."),
        sink=lambda *a: None,
    )

    agent.send("Schick eine Mail")

    # Die zweite Runde muss die Rückmeldung mit den echten Feldnamen enthalten.
    letzte = agent._provider.seen[-1]
    rueckmeldung = " ".join(str(n.get("content") or "") for n in letzte)
    assert "to" in rueckmeldung and "subject" in rueckmeldung and "body" in rueckmeldung


def test_ein_vollstaendiger_aufruf_kommt_weiterhin_als_antrag(store, audit) -> None:
    """Die Gegenprobe: richtig benannt, und die Freigabe steht wie immer."""
    agent = make_agent(
        store,
        audit,
        Reply(tool_calls=[ToolCall(id="r1", name="mail_senden", arguments={
            "to": "becker@example.com", "subject": "Hallo", "body": "Text",
        })]),
        sink=lambda *a: None,
    )

    turn = agent.send("Schick eine Mail")

    assert len(turn.approvals) == 1
    antrag = turn.approvals[0]
    assert antrag.decision.level is ApprovalLevel.CONFIRM_STRICT
    # Und der Trockenlauf trägt den echten Inhalt, nicht None.
    assert "becker@example.com" in antrag.dry_run
    assert "None" not in antrag.dry_run
