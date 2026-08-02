"""Exklusiver Wartungsmodus der ausgelieferten Sidecar-Laufzeit."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from icarus_memory import SelfModelStore, SqliteBackend
from icarus_memory.audit import AuditLog
from icarus_memory.episodes import EpisodeStore
from icarus_memory.maintenance import MaintenanceGate
from icarus_memory.proposals import ProposalStore
from icarus_memory.runtime import create_app
from icarus_memory import server
from icarus_memory.tasks import TaskStore
from icarus_memory.workspace import WorkspaceStore


def test_wartende_wartung_verhindert_neue_anfragen() -> None:
    gate = MaintenanceGate()
    assert gate.try_enter_request() is True

    entered = threading.Event()
    release = threading.Event()

    def maintain() -> None:
        with gate.exclusive("backup"):
            entered.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=maintain)
    thread.start()

    # Warten, bis die Wartung angekündigt ist. Sie darf noch nicht exklusiv
    # laufen, solange die erste Anfrage aktiv ist.
    for _ in range(100):
        if gate.state().waiting_maintenance == 1:
            break
        time.sleep(0.01)
    assert gate.state().waiting_maintenance == 1
    assert entered.is_set() is False
    assert gate.try_enter_request() is False

    gate.leave_request()
    assert entered.wait(timeout=2)
    assert gate.state().maintenance is True
    assert gate.try_enter_request() is False

    release.set()
    thread.join(timeout=2)
    assert thread.is_alive() is False
    assert gate.try_enter_request() is True
    gate.leave_request()


def _runtime_client(tmp_path: Path, monkeypatch) -> tuple[TestClient, object]:
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ICARUS_KEYCHAIN_BACKEND", "none")
    for name in (
        "ICARUS_PROVIDER",
        "ICARUS_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "LLM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    backend = SqliteBackend(tmp_path / "self-model.sqlite3")
    app = create_app(
        SelfModelStore(backend, subject_id="local"),
        audit=AuditLog(tmp_path / "audit.sqlite3"),
        tasks=TaskStore(tmp_path / "tasks.sqlite3"),
        workspace=WorkspaceStore(tmp_path / "workspace.sqlite3"),
        episodes=EpisodeStore(tmp_path / "episodes.sqlite3"),
        proposals=ProposalStore(tmp_path / "proposals.sqlite3"),
    )
    app.state.backend = backend
    return TestClient(app), app


def _assert_blocked_write(client: TestClient, operation: str) -> None:
    blocked = client.post(
        "/tasks",
        json={"title": "Darf nicht in einen Wartungslauf rutschen."},
    )
    assert blocked.status_code == 503
    assert blocked.headers["retry-after"] == "2"
    assert blocked.json()["maintenance"] is True
    assert blocked.json()["operation"] == operation

    # Das Lebenszeichen bleibt erreichbar, obwohl Nutzdaten kurz gesperrt sind.
    health = client.get("/health")
    assert health.status_code == 200
    assert health.headers["x-icarus-maintenance"] == operation


def test_backup_blockiert_parallele_schreibanfrage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, app = _runtime_client(tmp_path, monkeypatch)
    original_snapshot = server.snapshot
    snapshot_started = threading.Event()
    release_snapshot = threading.Event()

    def slow_snapshot(*args, **kwargs):
        snapshot_started.set()
        assert release_snapshot.wait(timeout=5)
        return original_snapshot(*args, **kwargs)

    monkeypatch.setattr(server, "snapshot", slow_snapshot)
    result: dict[str, object] = {}

    def run_backup() -> None:
        result["response"] = client.post("/backups")

    thread = threading.Thread(target=run_backup)
    thread.start()
    assert snapshot_started.wait(timeout=2)

    _assert_blocked_write(client, "backup")

    release_snapshot.set()
    thread.join(timeout=5)
    assert thread.is_alive() is False
    backup_response = result["response"]
    assert getattr(backup_response, "status_code") == 201

    accepted = client.post(
        "/tasks",
        json={"title": "Nach der Sicherung wieder erlaubt."},
    )
    assert accepted.status_code == 201

    # Auch der automatische Backup-Job läuft durch dieselbe Schranke.
    scheduled = app.state.scheduler._run_backup  # noqa: SLF001
    assert scheduled is not None
    assert getattr(scheduled, "__icarus_maintenance_wrapped__", False) is True
    app.state.scheduler.stop()


def test_restore_blockiert_parallele_schreibanfrage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, app = _runtime_client(tmp_path, monkeypatch)
    name = client.post("/backups").json()["name"]

    original_restore = server.restore
    restore_started = threading.Event()
    release_restore = threading.Event()

    def slow_restore(*args, **kwargs):
        restore_started.set()
        assert release_restore.wait(timeout=5)
        return original_restore(*args, **kwargs)

    monkeypatch.setattr(server, "restore", slow_restore)
    result: dict[str, object] = {}

    def run_restore() -> None:
        result["response"] = client.post(
            "/backups/restore",
            json={"name": name},
        )

    thread = threading.Thread(target=run_restore)
    thread.start()
    assert restore_started.wait(timeout=2)

    _assert_blocked_write(client, "restore")

    release_restore.set()
    thread.join(timeout=5)
    assert thread.is_alive() is False
    restore_response = result["response"]
    assert getattr(restore_response, "status_code") == 200

    accepted = client.post(
        "/tasks",
        json={"title": "Nach dem Restore wieder erlaubt."},
    )
    assert accepted.status_code == 201
    app.state.scheduler.stop()
