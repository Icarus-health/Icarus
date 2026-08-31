"""HTTP-Integration der vollständigen Wiederherstellung.

Die Zusicherungen hier:

1. Nach der Wiederherstellung liest die App den **wiederhergestellten** Stand,
   nicht den ersetzten.
2. Der vorherige vollständige Stand bleibt als Recovery-Satz erhalten.
3. Der Name ist ein Name, kein Pfad. Sonst wäre dies ein Weg, jede beliebige
   Datei des Rechners zur Datenbank zu erklären.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from icarus_memory import SelfModelStore, SqliteBackend
from icarus_memory.audit import AuditLog
from icarus_memory.backup import RestoreRollbackError
from icarus_memory.episodes import EpisodeStore
import icarus_memory.server as server_module
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

    antwort = client.post("/backups/restore", json={"name": name}).json()

    recovery = Path(antwort["recovery_path"])
    assert recovery.is_dir()
    # Und dort steht wirklich der ersetzte Stand, nicht eine leere Hülle.
    alt = SelfModelStore(
        SqliteBackend(recovery / "stores" / "self-model.sqlite3"),
        subject_id="local",
    )
    assert "Zweiter Stand." in {a.statement for a in alt.export().assertions}


def test_die_zahl_der_aussagen_wird_gemeldet(client: TestClient) -> None:
    """Ein Knopf, der drückt und nichts sagt, ist schlimmer als keiner —
    besonders dieser."""
    merken(client, "Eins.")
    merken(client, "Zwei.")
    name = client.post("/backups").json()["name"]

    antwort = client.post("/backups/restore", json={"name": name}).json()

    assert antwort["assertions"] == 2
    assert "Recovery-Satz" in antwort["detail"]


def test_restore_oeffnet_alle_sieben_live_stores_neu(client: TestClient) -> None:
    """Kein Store darf nach dem Dateitausch am alten Inode weiterarbeiten."""

    state = client.app.state
    names = ("backend", "episodes", "tasks", "workspace", "proposals", "audit", "regeln")
    before = {name: id(getattr(state, name)) for name in names}
    backup_name = client.post("/backups").json()["name"]

    response = client.post("/backups/restore", json={"name": backup_name})

    assert response.status_code == 200
    assert {name: id(getattr(state, name)) for name in names} != before
    assert all(id(getattr(state, name)) != before[name] for name in names)
    # Reads und Writes auf allen neu gebundenen Verbindungen.
    assert state.backend.all() == []
    assert state.episodes.counts() == {}
    assert state.tasks.all_tasks() == []
    assert state.workspace.projects() == []
    assert state.proposals.counts() == {}
    assert state.audit.entries() == []
    assert state.regeln.alle() == []
    state.audit.record("restore-test", "read", "auto", "executed", {})
    state.regeln.anlegen("Restore-Test", "notify", "auto")
    assert len(state.audit.entries()) == 1
    assert len(state.regeln.alle()) == 1


def test_laufender_scheduler_laeuft_nach_restore_wieder(
    client: TestClient,
) -> None:
    state = client.app.state
    assert client.put("/schedule", json={"enabled": True}).status_code == 200
    scheduler = state.scheduler
    before_thread = scheduler._thread  # noqa: SLF001 - Lifecycle-Vertrag
    backup_name = client.post("/backups").json()["name"]

    response = client.post("/backups/restore", json={"name": backup_name})

    assert response.status_code == 200
    assert scheduler.state()["running"] is True
    assert scheduler._thread is not before_thread  # noqa: SLF001
    assert before_thread is not None and not before_thread.is_alive()


def test_laufender_scheduler_laeuft_nach_restore_rollback_wieder(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = client.app.state
    client.put("/schedule", json={"enabled": True})
    scheduler = state.scheduler
    backup_name = client.post("/backups").json()["name"]
    original = server_module._open_operational_state
    reopened_threads: list[threading.Thread | None] = []

    def fail_after_first_reopen(*args, **kwargs) -> None:
        original(*args, **kwargs)
        reopened_threads.append(scheduler._thread)  # noqa: SLF001
        if len(reopened_threads) == 1:
            raise RuntimeError("absichtlicher Fehler nach erstem Reopen")

    monkeypatch.setattr(
        server_module,
        "_open_operational_state",
        fail_after_first_reopen,
    )

    response = client.post("/backups/restore", json={"name": backup_name})

    assert response.status_code == 409
    assert scheduler.state()["running"] is True
    assert len(reopened_threads) == 2
    assert reopened_threads[0] is not None and not reopened_threads[0].is_alive()
    assert reopened_threads[1] is scheduler._thread  # noqa: SLF001
    assert reopened_threads[0] is not reopened_threads[1]


def test_bewusst_gestoppter_scheduler_bleibt_nach_restore_aus(
    client: TestClient,
) -> None:
    scheduler = client.app.state.scheduler
    client.put("/schedule", json={"enabled": True})
    assert scheduler.stop() is True
    assert scheduler.enabled is True
    assert scheduler.state()["running"] is False
    backup_name = client.post("/backups").json()["name"]

    response = client.post("/backups/restore", json={"name": backup_name})

    assert response.status_code == 200
    assert scheduler.enabled is True
    assert scheduler.state()["running"] is False
    assert scheduler._thread is None  # noqa: SLF001


def test_restore_startet_keinen_doppelten_scheduler_thread(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = client.app.state
    client.put("/schedule", json={"enabled": True})
    scheduler = state.scheduler
    backup_name = client.post("/backups").json()["name"]
    original_start = scheduler.start
    starts = 0

    def counted_start() -> None:
        nonlocal starts
        starts += 1
        original_start()

    monkeypatch.setattr(scheduler, "start", counted_start)

    response = client.post("/backups/restore", json={"name": backup_name})

    assert response.status_code == 200
    assert starts == 1
    assert scheduler._thread is not None and scheduler._thread.is_alive()  # noqa: SLF001


def test_restore_beendet_mcp_und_nutzt_den_normalen_rebuild_pfad(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = client.app.state
    backup_name = client.post("/backups").json()["name"]
    instances = []

    class FakeMCP:
        def __init__(self, _spec) -> None:
            self.started = False
            self.stopped = False
            instances.append(self)

        def start(self) -> None:
            self.started = True

        def werkzeuge(self) -> list:
            return []

        def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr(server_module.mcp_client, "MCPVerbindung", FakeMCP)
    state.settings.mcp_server = [
        {"name": "test", "befehl": ["fake"], "aktiv": True}
    ]
    server_module._docke_mcp_an(client.app)
    assert len(instances) == 1 and instances[0].started

    response = client.post("/backups/restore", json={"name": backup_name})

    assert response.status_code == 200
    assert len(instances) == 2
    assert instances[0].stopped is True
    assert instances[1].started is True
    assert instances[1].stopped is False
    assert state.mcp["test"][0] is instances[1]


def test_scheduler_timeout_bricht_vor_store_close_ab(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = client.app.state
    names = ("backend", "episodes", "tasks", "workspace", "proposals", "audit", "regeln")
    before = {name: id(getattr(state, name)) for name in names}
    backup_name = client.post("/backups").json()["name"]
    monkeypatch.setattr(state.scheduler, "stop", lambda *_args, **_kwargs: False)

    response = client.post("/backups/restore", json={"name": backup_name})

    assert response.status_code == 409
    assert {name: id(getattr(state, name)) for name in names} == before


def test_rollback_fehler_laesst_server_fail_closed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_name = client.post("/backups").json()["name"]

    def fail_restore(*_args, **_kwargs):
        raise RestoreRollbackError("absichtlicher Rollback-Fehler")

    monkeypatch.setattr(server_module, "restore_backup_set", fail_restore)

    response = client.post("/backups/restore", json={"name": backup_name})

    assert response.status_code == 503
    assert client.get("/health").status_code == 503


def test_unautorisierter_restore_erhaelt_keine_exklusive_sperre(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ICARUS_SIDECAR_TOKEN", "richtig")
    app = create_app(
        SelfModelStore(
            SqliteBackend(tmp_path / "self-model.sqlite3"),
            subject_id="local",
        ),
        audit=AuditLog(tmp_path / "audit.sqlite3"),
        tasks=TaskStore(tmp_path / "tasks.sqlite3"),
        workspace=WorkspaceStore(tmp_path / "workspace.sqlite3"),
        episodes=EpisodeStore(tmp_path / "episodes.sqlite3"),
    )
    blocked = threading.Event()
    release = threading.Event()

    def slow_run():
        blocked.set()
        release.wait(timeout=2)
        return type("Report", (), {"to_dict": lambda self: {}, "summary": lambda self: "ok"})()

    monkeypatch.setattr(app.state.scheduler, "run_once", slow_run)
    protected = TestClient(app)
    normal = threading.Thread(
        target=lambda: protected.post(
            "/schedule/run",
            headers={"x-icarus-token": "richtig"},
        )
    )
    result: dict[str, int] = {}
    rejected = threading.Event()

    def unauthorized_restore() -> None:
        response = protected.post(
            "/backups/restore",
            json={"name": "nicht-vorhanden"},
        )
        result["status"] = response.status_code
        rejected.set()

    normal.start()
    assert blocked.wait(timeout=1)
    unauthorized = threading.Thread(target=unauthorized_restore)
    unauthorized.start()
    try:
        assert rejected.wait(timeout=0.5)
        assert result["status"] == 401
    finally:
        release.set()
        normal.join(timeout=1)
        unauthorized.join(timeout=1)
        app.state.scheduler.stop()


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
    assert len(list((tmp_path / "sicherungen").iterdir())) == 1


def test_unbekannte_sicherung_gibt_404(client: TestClient) -> None:
    antwort = client.post("/backups/restore", json={"name": "gibtesnicht.sqlite3"})
    assert antwort.status_code == 404
    assert "gibtesnicht" in antwort.json()["detail"]


def test_beschaedigte_sicherung_wird_abgewiesen(client: TestClient, tmp_path) -> None:
    """Und zwar **bevor** der bestehende Stand angefasst wird."""
    merken(client, "Der Bestand, der bleiben muss.")
    name = client.post("/backups").json()["name"]
    kaputt = tmp_path / "sicherungen" / name / "stores" / "episodes.sqlite3"
    kaputt.write_bytes(b"das ist keine datenbank")

    antwort = client.post("/backups/restore", json={"name": name})

    assert antwort.status_code == 409
    assert saetze(client) == {"Der Bestand, der bleiben muss."}
    assert len(list((tmp_path / "sicherungen").iterdir())) == 1
