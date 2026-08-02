"""Tests der Wiederherstellung.

Sichern konnte Icarus schon: Der Zeitplan legt bei jedem Lauf einen Snapshot an,
und `backup.py` hat ein sorgfältiges `restore()`. Nur war es von **nirgends**
erreichbar — kein Endpunkt, nichts in der Oberfläche. Ein Sicherungsnetz ohne
Griff ist eine Beruhigung ohne Deckung: Es sichert, und am Tag, an dem man es
braucht, kommt man nicht heran.

Die Zusicherungen hier:

1. Nach der Wiederherstellung liest die App den **wiederhergestellten** Stand,
   nicht den ersetzten.
2. Der vorherige Stand ist **nicht weg**, sondern liegt daneben.
3. Der Name ist ein Name, kein Pfad. Sonst wäre dies ein Weg, jede beliebige
   Datei des Rechners zur Datenbank zu erklären.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from icarus_memory import SelfModelStore, SqliteBackend
from icarus_memory.audit import AuditLog
from icarus_memory.episodes import EpisodeStore
from icarus_memory.server import create_app
from icarus_memory.tasks import TaskStore
from icarus_memory.workspace import WorkspaceStore


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    for name in ("ICARUS_PROVIDER", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                 "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    # Echtes SQLite auf der Platte: Eine Wiederherstellung tauscht eine Datei
    # aus. Mit `MemoryBackend` prüfte dieser Test gar nichts.
    backend = SqliteBackend(tmp_path / "self-model.sqlite3")
    app = create_app(
        SelfModelStore(backend, subject_id="local"),
        audit=AuditLog(tmp_path / "audit.sqlite3"),
        tasks=TaskStore(tmp_path / "tasks.sqlite3"),
        workspace=WorkspaceStore(tmp_path / "workspace.sqlite3"),
        episodes=EpisodeStore(tmp_path / "episodes.sqlite3"),
    )
    app.state.backend = backend
    yield TestClient(app)
    app.state.scheduler.stop()


def merken(client: TestClient, satz: str) -> None:
    client.post("/assertions", json={
        "statement": satz, "kind": "identity",
        "provenance": {"source_type": "user_stated"},
    })


def saetze(client: TestClient) -> set[str]:
    return {a["statement"] for a in client.get("/assertions").json()}


# -- Der Weg, der bisher fehlte ---------------------------------------------


def test_wiederherstellen_bringt_den_alten_stand_zurueck(client: TestClient) -> None:
    """Die Zusicherung, ohne die das Sichern nichts wert ist."""
    merken(client, "Vor der Sicherung.")
    name = client.post("/backups").json()["name"]

    merken(client, "Nach der Sicherung — soll verschwinden.")
    assert "Nach der Sicherung — soll verschwinden." in saetze(client)

    antwort = client.post("/backups/restore", json={"name": name})

    assert antwort.status_code == 200
    assert antwort.json()["restored"] == name
    # Und die App liest wirklich den neuen Stand — nicht weiter die alte
    # Verbindung auf die ersetzte Datei.
    jetzt = saetze(client)
    assert "Vor der Sicherung." in jetzt
    assert "Nach der Sicherung — soll verschwinden." not in jetzt


def test_der_ersetzte_stand_bleibt_liegen(client: TestClient, tmp_path) -> None:
    """Eine Wiederherstellung, die den aktuellen Stand vernichtet, wäre ein
    zweiter Weg, alles zu verlieren."""
    merken(client, "Erster Stand.")
    name = client.post("/backups").json()["name"]
    merken(client, "Zweiter Stand.")

    client.post("/backups/restore", json={"name": name})

    beiseite = list(tmp_path.glob("*vor-wiederherstellung*"))
    assert len(beiseite) == 1

    # Und dort steht wirklich der ersetzte Stand, nicht eine leere Hülle.
    alt = SelfModelStore(SqliteBackend(beiseite[0]), subject_id="local")
    assert "Zweiter Stand." in {a.statement for a in alt.export().assertions}


def test_die_zahl_der_aussagen_wird_gemeldet(client: TestClient) -> None:
    """Ein Knopf, der drückt und nichts sagt, ist schlimmer als keiner —
    besonders dieser."""
    merken(client, "Eins.")
    merken(client, "Zwei.")
    name = client.post("/backups").json()["name"]

    antwort = client.post("/backups/restore", json={"name": name}).json()

    assert antwort["assertions"] == 2
    assert "vor-wiederherstellung" in antwort["detail"]


# -- Was nicht gehen darf ---------------------------------------------------


def test_ein_pfad_ist_kein_name(client: TestClient, tmp_path) -> None:
    """Sonst wäre dieser Endpunkt ein Weg, jede beliebige Datei des Rechners
    zur Datenbank zu erklären."""
    # Der Sicherungsordner muss existieren, sonst scheitert der Ausbruch schon
    # am fehlenden Verzeichnis und der Test prüft nichts.
    merken(client, "Bestand.")
    client.post("/backups")
    assert (tmp_path / "sicherungen").is_dir()

    # Und die fremde Datei genau dorthin, wo „../" aus dem Sicherungsordner
    # landet. Eine Ebene höher wäre der Test wertlos.
    fremde = tmp_path / "woanders.sqlite3"
    SqliteBackend(fremde)  # existiert und ist eine gültige Datenbank
    assert fremde.is_file()
    assert (tmp_path / "sicherungen" / ".." / "woanders.sqlite3").is_file()

    antwort = client.post("/backups/restore", json={"name": "../woanders.sqlite3"})

    assert antwort.status_code == 404
    # Und der Bestand ist unangetastet geblieben.
    assert not list(tmp_path.glob("*vor-wiederherstellung*"))


def test_unbekannte_sicherung_gibt_404(client: TestClient) -> None:
    antwort = client.post("/backups/restore", json={"name": "gibtesnicht.sqlite3"})
    assert antwort.status_code == 404
    assert "gibtesnicht" in antwort.json()["detail"]


def test_beschaedigte_sicherung_wird_abgewiesen(client: TestClient, tmp_path) -> None:
    """Und zwar **bevor** der bestehende Stand angefasst wird."""
    merken(client, "Der Bestand, der bleiben muss.")
    kaputt = tmp_path / "sicherungen" / "kaputt.sqlite3"
    kaputt.parent.mkdir(parents=True, exist_ok=True)
    kaputt.write_bytes(b"das ist keine datenbank")

    antwort = client.post("/backups/restore", json={"name": "kaputt.sqlite3"})

    assert antwort.status_code == 409
    assert saetze(client) == {"Der Bestand, der bleiben muss."}
    assert not list(tmp_path.glob("*vor-wiederherstellung*"))
