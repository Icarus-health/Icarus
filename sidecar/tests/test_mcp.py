"""Tests der MCP-Tür.

Sie laufen gegen den **echten** Sidecar-Stapel — Server, Agent, Policy, Audit —
über einen ASGI-Transport statt über das Netz. Eine Brücke, die nur gegen
nachgebaute Antworten geprüft ist, beweist über die Brücke nichts; genau die
Frage, ob ein fremder Assistent an der Freigabe vorbeikommt, wäre dann offen.
"""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from icarus_memory import MemoryBackend, SelfModelStore
from icarus_memory.audit import AuditLog
from icarus_memory.mcp import (
    PREFIX,
    SUPPORTED_PROTOCOLS,
    Bridge,
    Server,
    SidecarUnreachable,
    connection,
    serve,
)
from icarus_memory.model import Kind, Provenance, SourceType
from icarus_memory.server import create_app, write_connection_file
from icarus_memory.tasks import TaskStore
from icarus_memory.workspace import WorkspaceStore


@pytest.fixture
def app(tmp_path):
    store = SelfModelStore(MemoryBackend(), subject_id="test")
    return create_app(
        store,
        audit=AuditLog(tmp_path / "audit.sqlite3"),
        tasks=TaskStore(tmp_path / "tasks.sqlite3"),
        workspace=WorkspaceStore(tmp_path / "workspace.sqlite3"),
    )


@pytest.fixture
def server(app) -> Server:
    # TestClient ist ein httpx.Client mit ASGI-Transport — dadurch läuft die
    # Brücke gegen den echten Stapel, ohne einen Port zu öffnen.
    return Server(Bridge("http://test", None, client=TestClient(app)))


def _call(server: Server, method: str, params: dict | None = None, id_: int = 1):
    return server.handle(
        {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    )


# -- Protokoll --------------------------------------------------------------


def test_initialize_uebernimmt_die_version_des_clients(server: Server) -> None:
    r = _call(server, "initialize", {"protocolVersion": "2024-11-05"})
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert r["result"]["serverInfo"]["name"] == "icarus"


def test_unbekannte_version_faellt_auf_die_neueste_zurueck(server: Server) -> None:
    r = _call(server, "initialize", {"protocolVersion": "1999-01-01"})
    assert r["result"]["protocolVersion"] == SUPPORTED_PROTOCOLS[0]


def test_benachrichtigungen_bekommen_keine_antwort(server: Server) -> None:
    """Ohne id ist es eine Notification. Eine Antwort darauf bricht Clients."""
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unbekannte_methode_meldet_32601(server: Server) -> None:
    r = _call(server, "gibt/es/nicht")
    assert r["error"]["code"] == -32601


def test_leere_listen_statt_fehler(server: Server) -> None:
    """Clients fragen das beim Verbinden ungefragt ab."""
    assert _call(server, "prompts/list")["result"] == {"prompts": []}
    assert _call(server, "resources/list")["result"] == {"resources": []}
    assert _call(server, "ping")["result"] == {}


def test_werkzeuge_tragen_das_praefix(server: Server) -> None:
    tools = _call(server, "tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}

    assert f"{PREFIX}merken" in names
    assert f"{PREFIX}projekt_anlegen" in names
    assert f"{PREFIX}heute" in names
    assert f"{PREFIX}kontext" in names
    assert all(n.startswith(PREFIX) for n in names)
    assert all("inputSchema" in t for t in tools)


# -- Die eigentliche Frage: kommt jemand an der Policy vorbei? --------------


def test_aussenwirksames_wird_nicht_ausgefuehrt_sondern_vorgelegt(
    server: Server, app
) -> None:
    """Der Kern der ganzen Tür.

    Ein fremder Assistent kann keine Bestätigungsphrase abtippen. Also darf er
    auch nichts auslösen, was das Haus verlässt — der Antrag landet in der App,
    wo ein Mensch sitzt.
    """
    r = _call(server, "tools/call", {
        "name": f"{PREFIX}mail_senden",
        "arguments": {"to": "fremd@example.com", "subject": "Hallo", "body": "Text"},
    })

    text = r["result"]["content"][0]["text"]
    assert "Freigabe" in text
    assert "fremd@example.com" in text  # Der Trockenlauf ist vollständig.
    # Kein isError: sonst versuchen es viele Clients sofort erneut.
    assert r["result"]["isError"] is False

    # Und der Antrag wartet tatsächlich in der App.
    pending = app.state.agent.policy.pending()
    assert len(pending) == 1
    assert pending[0].tool == "mail_senden"
    assert pending[0].confirmation_phrase == "fremd@example.com"


def test_grenzen_aus_dem_selbstmodell_gelten_auch_hier(server: Server, app) -> None:
    """Eine gesetzte Grenze schlägt auch den Weg über MCP."""
    app.state.store.record(
        statement="Niemals Mails an presse@example.com senden.",
        kind=Kind.CONSTRAINT,
        provenance=Provenance(source_type=SourceType.USER_STATED),
    )

    r = _call(server, "tools/call", {
        "name": f"{PREFIX}mail_senden",
        "arguments": {"to": "presse@example.com", "subject": "x", "body": "y"},
    })

    assert "Abgelehnt" in r["result"]["content"][0]["text"]
    assert app.state.agent.policy.pending() == []


def test_schreibender_zugriff_laeuft_und_steht_im_protokoll(
    server: Server, app
) -> None:
    r = _call(server, "tools/call", {
        "name": f"{PREFIX}projekt_anlegen",
        "arguments": {"name": "NutriFlow Pro", "bereich": "Icarus Health"},
    })

    assert "Projekt angelegt" in r["result"]["content"][0]["text"]
    assert [p.name for p in app.state.workspace.projects()] == ["NutriFlow Pro"]

    eintraege = app.state.audit.entries(10)
    assert any(
        e["tool"] == "projekt_anlegen" and e["outcome"] == "executed" for e in eintraege
    )


def test_unbekanntes_werkzeug_ist_ein_fehler(server: Server) -> None:
    r = _call(server, "tools/call", {"name": f"{PREFIX}gibtesnicht", "arguments": {}})
    assert "Unbekanntes Werkzeug" in r["result"]["content"][0]["text"]


def test_werkzeug_ohne_praefix_wird_abgewiesen(server: Server) -> None:
    r = _call(server, "tools/call", {"name": "mail_senden", "arguments": {}})
    assert r["result"]["isError"] is True


# -- Die Zusatzwerkzeuge ----------------------------------------------------


def test_heute_fasst_alles_in_einem_aufruf_zusammen(server: Server, app) -> None:
    app.state.workspace.add_project(
        "NutriFlow Pro", Provenance(source_type=SourceType.USER_STATED)
    )
    app.state.tasks.add(
        "BLS-Import", Provenance(source_type=SourceType.USER_STATED)
    )

    text = _call(server, "tools/call", {"name": f"{PREFIX}heute"})["result"]["content"][0]["text"]

    assert "NutriFlow Pro" in text
    assert "BLS-Import" in text
    # Nicht eingerichtete Konnektoren fehlen mit Begründung, statt die Seite
    # zu kippen — und die Begründung nennt keinen Variablennamen, auch nicht
    # auf diesem Weg. Was über die MCP-Tür geht, liest am Ende ein Mensch.
    assert "Nachrichten:" in text
    assert "ICARUS_" not in text
    assert "Einrichtung" in text
    assert "Gedächtnis:" in text


def test_kontext_liefert_woertlich_was_ein_modell_saehe(server: Server, app) -> None:
    app.state.store.record(
        statement="Mag ruhige, klare Oberflächen.",
        kind=Kind.PREFERENCE,
        provenance=Provenance(source_type=SourceType.CHAT, source_ref="chat:12"),
    )

    text = _call(server, "tools/call", {"name": f"{PREFIX}kontext"})["result"]["content"][0]["text"]

    assert "Mag ruhige, klare Oberflächen." in text
    assert "chat:12" in text  # Die Quelle steht dabei.


def test_freigaben_zeigen_was_in_der_app_wartet(server: Server) -> None:
    _call(server, "tools/call", {
        "name": f"{PREFIX}mail_senden",
        "arguments": {"to": "wer@example.com", "subject": "s", "body": "b"},
    })

    text = _call(server, "tools/call", {"name": f"{PREFIX}freigaben"})["result"]["content"][0]["text"]
    assert "mail_senden" in text
    assert "wer@example.com" in text


# -- Verbindung und Schleife ------------------------------------------------


def test_ohne_sidecar_kommt_eine_verstaendliche_meldung(server: Server) -> None:
    tot = Server(Bridge("http://127.0.0.1:1", None, timeout=0.05))
    r = _call(tot, "tools/list")
    assert r["error"]["code"] == -32001
    assert "nicht erreichbar" in r["error"]["message"]


def test_abgelehntes_token_sagt_was_zu_tun_ist(app) -> None:
    """Nach einem Neustart der App stimmt das Token nicht mehr.

    Die Meldung muss sagen, was zu tun ist — ein nacktes 401 schickt niemanden
    an die richtige Stelle.
    """
    import os

    from icarus_memory.server import TOKEN_ENV

    os.environ[TOKEN_ENV] = "richtig"
    try:
        geschuetzt = create_app(SelfModelStore(MemoryBackend(), subject_id="t"))
        client = TestClient(geschuetzt, headers={"x-icarus-token": "falsch"})
        with pytest.raises(SidecarUnreachable, match="neu gestartet"):
            Bridge("http://test", None, client=client).get("/tools")
    finally:
        del os.environ[TOKEN_ENV]


def test_verbindungsdatei_wird_gefunden(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ICARUS_SIDECAR_URL", raising=False)
    monkeypatch.delenv("ICARUS_SIDECAR_TOKEN", raising=False)

    write_connection_file(tmp_path, 8765, "geheim")

    assert connection() == ("http://127.0.0.1:8765", "geheim")


def test_verbindungsdatei_ist_nur_fuer_den_eigentuemer_lesbar(tmp_path) -> None:
    """Sie enthält ein Token."""
    path = write_connection_file(tmp_path, 8765, "geheim")
    assert path.stat().st_mode & 0o077 == 0


def test_fehlende_verbindungsdatei_nennt_den_pfad(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ICARUS_SIDECAR_URL", raising=False)

    with pytest.raises(SidecarUnreachable, match="Läuft die Icarus-App"):
        connection()


def test_umgebungsvariable_schlaegt_die_datei(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    write_connection_file(tmp_path, 8765, "aus-datei")
    monkeypatch.setenv("ICARUS_SIDECAR_URL", "http://127.0.0.1:9999/")
    monkeypatch.setenv("ICARUS_SIDECAR_TOKEN", "aus-env")

    assert connection() == ("http://127.0.0.1:9999", "aus-env")


def test_schleife_beantwortet_zeilenweise(server: Server) -> None:
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
        + "\n"                       # Leerzeilen überspringen
        + "{kein json}\n"            # Müll verwerfen, nicht raten
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n"
    )
    stdout = io.StringIO()

    serve(server, stdin, stdout)

    antworten = [json.loads(z) for z in stdout.getvalue().splitlines()]
    assert [a["id"] for a in antworten] == [1, 2]
