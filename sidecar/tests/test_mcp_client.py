"""Die MCP-Tür in die andere Richtung — und was dabei nicht verhandelbar ist.

Zwei Zusagen tragen diese Etappe, und beide sind der Grund, warum das Andocken
überhaupt vertretbar ist:

1. **Jedes angedockte Werkzeug ist `returns_untrusted`.** Was ein fremder
   Server zurückgibt, ist fremder Inhalt — er verseucht den Zug, hebt die
   Freigabestufe, und keine Dauerregel senkt sie wieder.
2. **Jedes angedockte Werkzeug ist `OUTWARD`.** Ein fremdes Werkzeug sagt
   nicht, ob es liest oder handelt. Danach zu raten hieße, eine
   Sicherheitszusage an eine Zeichenkette zu hängen, die der fremde Server
   frei wählt.

Der Rest ist Protokoll und lässt sich reparieren. Diese beiden nicht.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from icarus_memory.mcp_client import (
    MAX_WERKZEUGE,
    FremdesWerkzeug,
    MCPFehler,
    MCPVerbindung,
    Serverangabe,
    nachsehen,
)
from icarus_memory.policy import ActionClass, ApprovalLevel, Policy
from icarus_memory.tools import build_registry


# -- Ein fremder Server zum Anfassen ----------------------------------------


RUMPF = '''
import json, sys
WERKZEUGE = %(werkzeuge)s
for zeile in sys.stdin:
    zeile = zeile.strip()
    if not zeile:
        continue
    n = json.loads(zeile)
    m, k, p = n.get("method"), n.get("id"), n.get("params") or {}
    if k is None:
        continue
    %(sonderfall)s
    if m == "initialize":
        a = {"jsonrpc": "2.0", "id": k, "result": {
            "protocolVersion": p.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "probe", "version": "1"}}}
    elif m == "tools/list":
        a = {"jsonrpc": "2.0", "id": k, "result": {"tools": WERKZEUGE}}
    elif m == "tools/call":
        name = p.get("name")
        if name == "kaputt":
            a = {"jsonrpc": "2.0", "id": k, "result": {
                "content": [{"type": "text", "text": "So nicht."}], "isError": True}}
        else:
            a = {"jsonrpc": "2.0", "id": k, "result": {
                "content": [{"type": "text", "text": "Ausgabe von " + str(name)}],
                "isError": False}}
    else:
        a = {"jsonrpc": "2.0", "id": k, "error": {"code": -32601, "message": "Unbekannt"}}
    sys.stdout.write(json.dumps(a, ensure_ascii=False) + chr(10))
    sys.stdout.flush()
'''


def schreibe_server(tmp_path: Path, werkzeuge: list[dict], sonderfall: str = "") -> Path:
    ziel = tmp_path / "fremd.py"
    ziel.write_text(
        RUMPF % {"werkzeuge": json.dumps(werkzeuge), "sonderfall": sonderfall},
        encoding="utf-8",
    )
    return ziel


def angabe(tmp_path: Path, werkzeuge: list[dict] | None = None,
           sonderfall: str = "", name: str = "Probe") -> Serverangabe:
    werkzeuge = werkzeuge if werkzeuge is not None else [
        {"name": "wetter", "description": "Wetter für einen Ort.",
         "inputSchema": {"type": "object", "properties": {"ort": {"type": "string"}}}},
    ]
    return Serverangabe(
        name=name,
        befehl=[sys.executable, str(schreibe_server(tmp_path, werkzeuge, sonderfall))],
    )


# -- Die Zusagen ------------------------------------------------------------


def test_jedes_angedockte_werkzeug_liefert_fremden_inhalt(tmp_path) -> None:
    """Die wichtigste Zusage der Etappe.

    Ein angedockter Dienst erweitert, was Icarus **kann** — nicht, wem es
    **glaubt**.
    """
    with MCPVerbindung(angabe(tmp_path)) as v:
        registry = build_registry(
            store=None, mcp_verbindungen={"probe": (v, v.werkzeuge())}
        )

    fremde = [t for t in registry.values() if t.name.startswith("Probe.")]
    assert fremde, "kein angedocktes Werkzeug in der Registry"
    assert all(t.returns_untrusted for t in fremde)


def test_jedes_angedockte_werkzeug_ist_aussenwirksam(tmp_path) -> None:
    """Ein fremdes Werkzeug sagt nicht, ob es liest oder handelt.

    `tools/list` kennt kein Feld dafür, und nach dem Namen zu raten hieße, eine
    Sicherheitszusage an eine Zeichenkette zu hängen, die der fremde Server
    frei wählt.
    """
    werkzeuge = [
        {"name": "nur_lesen", "description": "Liest bloß.", "inputSchema": {}},
        {"name": "loeschen", "description": "Löscht alles.", "inputSchema": {}},
    ]
    with MCPVerbindung(angabe(tmp_path, werkzeuge)) as v:
        registry = build_registry(
            store=None, mcp_verbindungen={"probe": (v, v.werkzeuge())}
        )

    fremde = [t for t in registry.values() if t.name.startswith("Probe.")]
    assert len(fremde) == 2
    assert all(t.action_class is ActionClass.OUTWARD for t in fremde)
    # Auch das harmlos klingende: die Voreinstellung ist die vorsichtige.
    assert registry["Probe.nur_lesen"].action_class is ActionClass.OUTWARD


def test_nach_fremdem_inhalt_wird_jede_wirkung_vorgelegt(tmp_path) -> None:
    """Die Zusage greift erst zusammen mit der Policy — also hier geprüft."""
    policy = Policy()

    ohne = policy.decide("Probe.wetter", ActionClass.OUTWARD, {}, tainted=False)
    mit = policy.decide("Probe.wetter", ActionClass.OUTWARD, {}, tainted=True)

    assert ohne.level is ApprovalLevel.CONFIRM_STRICT
    assert mit.level is ApprovalLevel.CONFIRM_STRICT


def test_keine_dauerregel_senkt_die_stufe_nach_fremdem_inhalt(tmp_path) -> None:
    """Sonst wäre die Regel der Weg, die Verseuchung zu umgehen."""
    from icarus_memory.regeln import Regel

    policy = Policy()
    regel = Regel(
        id="r-1", name="Wetter immer erlaubt", tool="Probe.wetter",
        stufe="auto", passt_auf={},
        angelegt_am=None, widerrufen_am=None,
    )

    entschieden = policy.decide(
        "Probe.wetter", ActionClass.OUTWARD, {}, tainted=True, regel=regel
    )

    assert entschieden.level is ApprovalLevel.CONFIRM_STRICT
    assert any("greift hier nicht" in g for g in entschieden.reasons)


# -- Das Protokoll ----------------------------------------------------------


def test_andocken_und_werkzeuge_holen(tmp_path) -> None:
    werkzeuge = nachsehen(angabe(tmp_path))

    assert [w.name for w in werkzeuge] == ["wetter"]
    assert werkzeuge[0].voller_name == "Probe.wetter"
    assert werkzeuge[0].beschreibung == "Wetter für einen Ort."


def test_ein_werkzeug_aufrufen(tmp_path) -> None:
    with MCPVerbindung(angabe(tmp_path)) as v:
        assert v.rufe("wetter", {"ort": "Staufen"}) == "Ausgabe von wetter"


def test_ein_fehler_des_servers_kommt_als_satz(tmp_path) -> None:
    """Nie ein Stapelabzug in der Oberfläche."""
    werkzeuge = [{"name": "kaputt", "description": "", "inputSchema": {}}]
    with MCPVerbindung(angabe(tmp_path, werkzeuge)) as v:
        with pytest.raises(MCPFehler) as fehler:
            v.rufe("kaputt", {})

    assert "So nicht." in str(fehler.value)


def test_ein_befehl_den_es_nicht_gibt_sagt_das(tmp_path) -> None:
    with pytest.raises(MCPFehler) as fehler:
        nachsehen(Serverangabe(name="Kaputt", befehl=["gibtsdiesenbefehlnicht"]))

    assert "gibt es auf diesem Rechner nicht" in str(fehler.value)


def test_ohne_befehl_geht_es_nicht(tmp_path) -> None:
    with pytest.raises(MCPFehler) as fehler:
        nachsehen(Serverangabe(name="Leer", befehl=[]))

    assert "kein Befehl" in str(fehler.value)


def test_ein_server_der_nicht_antwortet_haelt_icarus_nicht_an(tmp_path) -> None:
    """Ein hängender Kindprozess wäre sonst ein hängendes Programm."""
    stumm = tmp_path / "stumm.py"
    stumm.write_text("import time\ntime.sleep(300)\n", encoding="utf-8")
    still = Serverangabe(name="Stumm", befehl=[sys.executable, str(stumm)])

    verbindung = MCPVerbindung(still)
    verbindung._frist_kurz = True
    import icarus_memory.mcp_client as modul
    alt = modul.START_SEKUNDEN
    modul.START_SEKUNDEN = 1.0
    try:
        with pytest.raises(MCPFehler) as fehler:
            verbindung.start()
    finally:
        modul.START_SEKUNDEN = alt
        verbindung.stop()

    assert "nicht innerhalb" in str(fehler.value)


def test_schrott_auf_stdout_bringt_die_verbindung_nicht_um(tmp_path) -> None:
    """Manche Server schreiben Protokollzeilen nach stdout. Das ist ihr Fehler,
    aber kein Grund, hier aufzugeben."""
    laerm = 'sys.stdout.write("Server gestartet, Version 3" + chr(10)); sys.stdout.flush()'
    a = angabe(tmp_path, sonderfall=laerm)

    with MCPVerbindung(a) as v:
        assert [w.name for w in v.werkzeuge()] == ["wetter"]


def test_zu_viele_werkzeuge_werden_gedeckelt(tmp_path) -> None:
    """Ein Server, der Tausende Werkzeuge meldet, hat entweder ein Problem oder
    will eines machen."""
    viele = [{"name": f"w{i}", "description": "", "inputSchema": {}}
             for i in range(MAX_WERKZEUGE + 20)]

    assert len(nachsehen(angabe(tmp_path, viele))) == MAX_WERKZEUGE


# -- Namen ------------------------------------------------------------------


def test_der_server_steht_im_werkzeugnamen(tmp_path) -> None:
    """Wer einen Werkzeugnamen im Protokoll liest, soll sehen, von wem er kam."""
    w = FremdesWerkzeug(server="Mein Dienst", name="etwas tun",
                        beschreibung="", schema={})

    assert w.voller_name == "Mein_Dienst.etwas_tun"


# -- Geheimnisse ------------------------------------------------------------


def test_die_umgebung_geht_nur_mit_schluesselnamen_nach_aussen() -> None:
    """Dort stehen Zugangsdaten, und diese Antwort geht an die Oberfläche."""
    a = Serverangabe(name="Probe", befehl=["x"],
                     umgebung={"API_KEY": "geheim-123", "REGION": "eu"})

    d = a.to_dict()

    assert d["umgebung"] == ["API_KEY", "REGION"]
    assert "geheim-123" not in json.dumps(d)


# -- Über die Tür -----------------------------------------------------------


@pytest.fixture()
def tuer(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from icarus_memory import MemoryBackend, SelfModelStore
    from icarus_memory.server import create_app

    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    app = create_app(SelfModelStore(MemoryBackend(), subject_id="t"))
    klient = TestClient(app)
    klient.app_ref = app  # type: ignore[attr-defined]
    yield klient
    app.state.scheduler.stop()


def befehl_fuer(tmp_path: Path, werkzeuge: list[dict] | None = None) -> str:
    werkzeuge = werkzeuge if werkzeuge is not None else [
        {"name": "wetter", "description": "Wetter.", "inputSchema": {}},
    ]
    return f"{sys.executable} {schreibe_server(tmp_path, werkzeuge)}"


def test_verbinden_und_nachsehen_sagt_was_dabei_herauskam(tuer, tmp_path) -> None:
    """Jede Aktion antwortet — mit Ergebnis oder mit Grund."""
    antwort = tuer.post("/mcp/pruefen", json={
        "name": "Probe", "befehl": befehl_fuer(tmp_path),
    }).json()

    assert antwort["ok"] is True
    assert antwort["werkzeuge"] == ["Probe.wetter"]
    assert "ein Werkzeug gefunden" in antwort["detail"]


def test_nachsehen_bei_einem_dienst_der_nicht_startet(tuer) -> None:
    antwort = tuer.post("/mcp/pruefen", json={
        "name": "Kaputt", "befehl": "gibtsdiesenbefehlnicht",
    }).json()

    assert antwort["ok"] is False
    assert "gibt es auf diesem Rechner nicht" in antwort["detail"]
    # Kein Stapelabzug in der Oberfläche.
    assert "Traceback" not in antwort["detail"]


def test_andocken_abdocken_und_dazwischen_die_werkzeuge(tuer, tmp_path) -> None:
    angelegt = tuer.post("/mcp/server", json={
        "name": "Probe", "befehl": befehl_fuer(tmp_path),
    })
    assert angelegt.status_code == 201
    assert angelegt.json()["werkzeuge"] == ["Probe.wetter"]

    liste = tuer.get("/mcp/server").json()["items"]
    assert [e["name"] for e in liste] == ["Probe"]
    assert liste[0]["verbunden"] is True
    assert liste[0]["werkzeuge"] == ["Probe.wetter"]

    # Und der Agent kennt das Werkzeug wirklich.
    assert "Probe.wetter" in tuer.app_ref.state.agent.tool_names

    weg = tuer.delete("/mcp/server/Probe")
    assert weg.status_code == 200
    assert tuer.get("/mcp/server").json()["items"] == []
    assert "Probe.wetter" not in tuer.app_ref.state.agent.tool_names


def test_ein_dienst_der_nicht_startet_wird_nicht_eingetragen(tuer) -> None:
    """Sonst sammeln sich Einträge, von denen niemand weiß, ob sie je
    funktioniert haben."""
    antwort = tuer.post("/mcp/server", json={
        "name": "Kaputt", "befehl": "gibtsdiesenbefehlnicht",
    })

    assert antwort.status_code == 400
    assert tuer.get("/mcp/server").json()["items"] == []


def test_derselbe_name_zweimal_ist_ein_fehler_mit_grund(tuer, tmp_path) -> None:
    tuer.post("/mcp/server", json={"name": "Probe", "befehl": befehl_fuer(tmp_path)})

    zweitens = tuer.post("/mcp/server", json={
        "name": "Probe", "befehl": befehl_fuer(tmp_path),
    })

    assert zweitens.status_code == 409
    assert "schon eingetragen" in zweitens.json()["detail"]


def test_ein_unlesbarer_befehl_sagt_das(tuer) -> None:
    antwort = tuer.post("/mcp/server", json={"name": "Schief", "befehl": 'x "unbeendet'})

    assert antwort.status_code == 400
    assert "nicht lesbar" in antwort.json()["detail"]


def test_der_befehl_laeuft_nicht_durch_eine_shell(tuer, tmp_path) -> None:
    """Der Befehl kommt aus einer Einstellungsdatei. Eine Shell würde daraus
    eine Befehlszeile machen, die mehr kann als starten."""
    beweis = tmp_path / "beweis.txt"
    antwort = tuer.post("/mcp/pruefen", json={
        "name": "Böse", "befehl": f"echo egal; touch {beweis}",
    }).json()

    assert antwort["ok"] is False
    assert not beweis.exists()


def test_abdocken_eines_unbekannten_dienstes_gibt_404(tuer) -> None:
    assert tuer.delete("/mcp/server/GibtsNicht").status_code == 404
