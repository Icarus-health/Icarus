"""Eine Suche über alle Schichten — und zwei Zusagen, die dabei halten müssen.

Erstens: Was ersetzt, abgelaufen oder widerrufen ist, darf nicht als
gegenwärtige Wahrheit im Ergebnis stehen. Eine Suche, die Widerrufenes
zurückgibt, macht den Widerruf wertlos.

Zweitens: Rohmaterial ist fremder Text. Es darf gefunden werden, aber es muss
als fremd gekennzeichnet ankommen — sonst rahmt die Oberfläche es nicht.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from icarus_memory.agent import Agent
from icarus_memory.audit import AuditLog
from icarus_memory.backends import MemoryBackend
from icarus_memory.episodes import EpisodeStore
from icarus_memory.model import Kind, Provenance, RedactionReason, SourceType
from icarus_memory.policy import Policy
from icarus_memory.server import create_app
from icarus_memory.store import SelfModelStore
from icarus_memory.tasks import TaskStore
from icarus_memory.tools import build_registry
from icarus_memory.workspace import WorkspaceStore


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    store = SelfModelStore(MemoryBackend(), subject_id="t")
    audit = AuditLog(tmp_path / "audit.sqlite3")
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    workspace = WorkspaceStore(tmp_path / "workspace.sqlite3")
    episodes = EpisodeStore(tmp_path / "episodes.sqlite3")
    agent = Agent(store, Policy(), audit, build_registry(store, task_store=tasks))
    app = create_app(store, agent, audit, tasks)
    app.state.workspace = workspace
    app.state.episodes = episodes
    return TestClient(app)


def gesagt() -> Provenance:
    return Provenance(source_type=SourceType.USER_STATED)


# -- Ein Feld für alles ------------------------------------------------------


def test_ein_name_findet_ueber_alle_schichten(client: TestClient) -> None:
    """Der Nutzer tippt „Brandt“ — und bekommt alles, wo Brandt vorkommt."""
    client.post("/tasks", json={"title": "Rückfrage an Dr. Brandt"})
    client.post("/projects", json={"name": "Vertrag mit Brandt"})
    client.post("/notes", json={"title": "Besprechung", "body": "Brandt hält die Ablage parallel."})
    client.post("/assertions", json={
        "statement": "Dr. Brandt entscheidet über die Ablage.",
        "kind": "state",
        "provenance": {"source_type": "user_stated"},
    })

    ergebnis = client.get("/suche", params={"q": "brandt"}).json()
    arten = {g["art"] for g in ergebnis["gruppen"]}

    assert {"aufgabe", "projekt", "notiz", "aussage"} <= arten
    assert ergebnis["gesamt"] >= 4


def test_gruppen_stehen_in_der_reihenfolge_des_nutzens(client: TestClient) -> None:
    """Was zu tun ist, steht vor dem, was man weiß."""
    client.post("/tasks", json={"title": "Vertrag prüfen"})
    client.post("/assertions", json={
        "statement": "Der Vertrag läuft drei Jahre.",
        "kind": "state",
        "provenance": {"source_type": "user_stated"},
    })

    gruppen = [g["art"] for g in client.get("/suche", params={"q": "vertrag"}).json()["gruppen"]]

    assert gruppen.index("aufgabe") < gruppen.index("aussage")


def test_ein_einzelner_buchstabe_liefert_nichts(client: TestClient) -> None:
    """Er träfe alles und hülfe niemandem."""
    client.post("/tasks", json={"title": "Alles Mögliche"})

    ergebnis = client.get("/suche", params={"q": "a"}).json()

    assert ergebnis["gesamt"] == 0
    assert ergebnis["gruppen"] == []


# -- Die Zusagen -------------------------------------------------------------


def test_ersetztes_taucht_in_der_suche_nicht_mehr_auf(client: TestClient) -> None:
    """Der alte Satz überlebt im Bestand — er darf nur nicht mehr gelten.

    Das ist der Fall, der die Zusage wirklich prüft. Beim Widerruf wird der
    Text selbst durch einen Grabstein ersetzt; da findet ihn ohnehin keine
    Suche mehr, gefiltert oder nicht. Beim **Ersetzen** bleibt der Wortlaut
    für die Geschichte erhalten — und genau hier entscheidet sich, ob die
    Suche `recall()` benutzt oder am Filter vorbei in den Speicher greift.
    """
    alt = client.post("/assertions", json={
        "statement": "Die Praxis zieht nach Karlsruhe um.",
        "kind": "state",
        "provenance": {"source_type": "user_stated"},
    }).json()

    assert client.get("/suche", params={"q": "Karlsruhe"}).json()["gesamt"] == 1

    client.post("/assertions", json={
        "statement": "Die Praxis bleibt, wo sie ist.",
        "kind": "state",
        "provenance": {"source_type": "user_stated"},
        "supersedes": [alt["id"]],
    })

    # Der alte Wortlaut steht noch im Bestand …
    verlauf = client.get(f"/assertions/{alt['id']}/history").json()
    assert any("Karlsruhe" in eintrag["statement"] for eintrag in verlauf)

    # … darf aber nicht mehr als Gegenwart zurückkommen.
    nachher = client.get("/suche", params={"q": "Karlsruhe"}).json()
    assert nachher["gesamt"] == 0, "Ersetztes darf nicht als Gegenwart zurückkommen"


def test_widerruf_entfernt_den_wortlaut(client: TestClient) -> None:
    """Widerruf löscht den Inhalt, ein Grabstein bleibt."""
    angelegt = client.post("/assertions", json={
        "statement": "Die Praxis zieht nach Freiburg um.",
        "kind": "state",
        "provenance": {"source_type": "user_stated"},
    }).json()

    client.post(f"/assertions/{angelegt['id']}/redact", json={"reason": "user_request"})

    assert client.get("/suche", params={"q": "Freiburg"}).json()["gesamt"] == 0


def test_rohmaterial_kommt_als_fremd_gekennzeichnet_an(client: TestClient) -> None:
    """Aufgenommener Text hat jemand anderes geschrieben — auch die eigene Datei.

    Ohne diese Kennzeichnung rahmt die Oberfläche den Text nicht, und dann
    steht fremder Inhalt ungerahmt neben eigenem Wissen.
    """
    client.post("/episodes", json={
        "kind": "document",
        "title": "Angebot der Gegenseite",
        "body": "Wir schlagen eine Laufzeit von fünf Jahren vor.",
        "provenance": {"source_type": "document", "source_ref": "angebot.md"},
    })

    ergebnis = client.get("/suche", params={"q": "Laufzeit"}).json()
    roh = [g for g in ergebnis["gruppen"] if g["art"] == "episode"]

    assert roh, "die Episode muss gefunden werden"
    assert all(t["fremd"] for t in roh[0]["treffer"])


def test_eigenes_wissen_ist_nicht_fremd(client: TestClient) -> None:
    """Sonst wäre die Kennzeichnung wertlos: was alles markiert, markiert nichts."""
    client.post("/assertions", json={
        "statement": "Ich arbeite lieber vormittags.",
        "kind": "preference",
        "provenance": {"source_type": "user_stated"},
    })

    ergebnis = client.get("/suche", params={"q": "vormittags"}).json()
    aussagen = [g for g in ergebnis["gruppen"] if g["art"] == "aussage"][0]

    assert not aussagen["treffer"][0]["fremd"]


def test_aus_einer_mail_gemerktes_bleibt_fremd(client: TestClient) -> None:
    """Herkunft ist nicht Vertrauenswürdigkeit — auch nach dem Merken nicht."""
    client.post("/assertions", json={
        "statement": "Die Gegenseite will fünf Jahre Laufzeit.",
        "kind": "state",
        "provenance": {"source_type": "email", "source_ref": "uid-4711"},
    })

    ergebnis = client.get("/suche", params={"q": "Gegenseite"}).json()
    aussagen = [g for g in ergebnis["gruppen"] if g["art"] == "aussage"][0]

    assert aussagen["treffer"][0]["fremd"]


# -- Robustheit --------------------------------------------------------------


def test_eine_klemmende_schicht_kippt_die_suche_nicht(client: TestClient) -> None:
    """Eine Suche, die ganz ausfällt, weil eine Tabelle klemmt, ist nutzlos."""
    client.post("/tasks", json={"title": "Vertrag prüfen"})

    class Kaputt:
        def search(self, *a, **k):
            raise RuntimeError("Datenbank weg")

    client.app.state.episodes = Kaputt()

    ergebnis = client.get("/suche", params={"q": "vertrag"}).json()

    assert ergebnis["gesamt"] >= 1
    assert any(g["art"] == "aufgabe" for g in ergebnis["gruppen"])
