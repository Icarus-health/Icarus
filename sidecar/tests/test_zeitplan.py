"""Tests des mitlaufenden Prozesses.

Die entscheidende Eigenschaft: Er macht die Vorschlagsschlange voller, nie den
Bestand. Im schlimmsten Fall entsteht Arbeit, die jemand ignoriert — nie ein
falscher Fakt.
"""

from __future__ import annotations

import time
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from icarus_memory import SelfModelStore, SqliteBackend
from icarus_memory import config
from icarus_memory.audit import AuditLog
from icarus_memory.episodes import EpisodeStore
from icarus_memory.model import Kind, Provenance, SourceType, now
from icarus_memory.proposals import ProposalStore
from icarus_memory.scheduler import (
    DEFAULT_INTERVAL_MINUTES,
    MIN_INTERVAL_MINUTES,
    JobResult,
    Scheduler,
)
import icarus_memory.scheduler as scheduler_module
from icarus_memory.server import create_app
from icarus_memory.tasks import TaskStore
from icarus_memory.workspace import WorkspaceStore


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    for name in ("ICARUS_FILE_ROOTS", "ICARUS_PROVIDER",
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    # Echtes SQLite statt Speicherbetrieb: Der Sicherungsschritt greift auf
    # die Datei zu, und ein Test, der sie nie anlegt, prüft ihn nicht.
    app = create_app(
        SelfModelStore(SqliteBackend(tmp_path / "self-model.sqlite3"), subject_id="test"),
        audit=AuditLog(tmp_path / "audit.sqlite3"),
        tasks=TaskStore(tmp_path / "tasks.sqlite3"),
        workspace=WorkspaceStore(tmp_path / "workspace.sqlite3"),
        episodes=EpisodeStore(tmp_path / "episodes.sqlite3"),
        proposals=ProposalStore(tmp_path / "proposals.sqlite3"),
    )
    yield TestClient(app)
    app.state.scheduler.stop()


# -- Der Takt ---------------------------------------------------------------


def test_standardmaessig_aus() -> None:
    """Zwei Gründe: Kosten und Lärm. Beide ernst."""
    plan = Scheduler()
    assert plan.enabled is False
    assert plan.next_run() is None
    assert plan.state()["with_model"] is False


def test_zu_kurzer_takt_wird_angehoben() -> None:
    """Alles darunter erzeugt Lärm, bevor jemand die erste Runde geprüft hat."""
    plan = Scheduler()
    plan.configure(interval_minutes=1)
    assert plan.state()["interval_minutes"] == MIN_INTERVAL_MINUTES


def test_erster_lauf_ist_sofort_faellig() -> None:
    plan = Scheduler()
    plan.configure(enabled=True)
    assert plan.next_run() is not None
    assert plan.next_run() <= now()


def test_nach_einem_lauf_zaehlt_der_takt() -> None:
    plan = Scheduler(run_backup=lambda: JobResult("sicherung", True, "x"))
    plan.configure(enabled=True, interval_minutes=60)
    plan.run_once()

    naechster = plan.next_run()
    assert naechster is not None
    assert naechster > now()
    assert naechster <= now() + timedelta(minutes=60)


# -- Fehlertoleranz ---------------------------------------------------------


def test_ein_kaputter_schritt_stoppt_die_anderen_nicht() -> None:
    """Ein Mailserver, der hakt, darf nicht verhindern, dass die Sicherung
    läuft — genau diese Kopplung macht Hintergrundprozesse unbrauchbar."""
    def kaputt() -> list[JobResult]:
        raise RuntimeError("Netzwerk weg")

    plan = Scheduler(
        run_ingest=kaputt,
        run_consolidation=lambda m: JobResult("verdichtung", True, "2 Vorschläge"),
        run_backup=lambda: JobResult("sicherung", True, "snapshot-1"),
    )

    report = plan.run_once()

    assert report.ok is False
    namen = [j.name for j in report.jobs]
    assert namen == ["aufnahme", "verdichtung", "sicherung"]
    assert report.jobs[1].ok and report.jobs[2].ok
    assert "Netzwerk weg" in report.jobs[0].detail


def test_der_bericht_bleibt_abrufbar() -> None:
    plan = Scheduler(run_backup=lambda: JobResult("sicherung", True, "snapshot-1"))
    plan.run_once()

    zustand = plan.state()
    assert zustand["last_run"]["ok"] is True
    assert zustand["last_run"]["jobs"][0]["detail"] == "snapshot-1"


def test_thread_startet_und_endet_sauber() -> None:
    laeufe: list[int] = []
    plan = Scheduler(run_backup=lambda: (laeufe.append(1), JobResult("s", True))[1])
    plan.configure(enabled=True, interval_minutes=MIN_INTERVAL_MINUTES)

    plan.start()
    assert plan.state()["running"] is True
    plan.stop()
    assert plan.state()["running"] is False


def test_zweimal_starten_erzeugt_keinen_zweiten_thread() -> None:
    plan = Scheduler()
    plan.configure(enabled=True)
    plan.start()
    erster = plan._thread  # noqa: SLF001
    plan.start()
    try:
        assert plan._thread is erster  # noqa: SLF001
    finally:
        plan.stop()


def test_stop_timeout_beendet_den_zeitplan_nicht_still(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def slow_backup() -> JobResult:
        started.set()
        release.wait(timeout=2)
        completed.set()
        return JobResult("sicherung", True, "fertig")

    monkeypatch.setattr(scheduler_module, "TICK_SECONDS", 0.001)
    plan = Scheduler(run_backup=slow_backup)
    plan.configure(enabled=True)
    plan.start()
    assert started.wait(timeout=1)

    assert plan.stop(timeout=0.01) is False
    assert not plan._stop.is_set()  # noqa: SLF001 - Sicherheitsvertrag des Timeouts

    release.set()
    assert completed.wait(timeout=1)
    time.sleep(0.02)
    try:
        assert plan.state()["running"] is True
    finally:
        plan.stop()


# -- Über die Schnittstelle -------------------------------------------------


def test_zeitplan_ist_beim_ersten_start_aus(client: TestClient) -> None:
    zustand = client.get("/schedule").json()
    assert zustand["enabled"] is False
    assert zustand["running"] is False
    assert zustand["with_model"] is False
    assert zustand["interval_minutes"] == DEFAULT_INTERVAL_MINUTES


def test_einschalten_startet_den_thread(client: TestClient) -> None:
    zustand = client.put("/schedule", json={"enabled": True}).json()
    assert zustand["enabled"] is True
    assert zustand["running"] is True

    aus = client.put("/schedule", json={"enabled": False}).json()
    assert aus["running"] is False


def test_zeitplan_ueberlebt_den_neustart(client: TestClient, tmp_path) -> None:
    client.put("/schedule", json={
        "enabled": True, "interval_minutes": 60, "with_model": True,
    })

    wieder = config.load(tmp_path).schedule
    assert wieder.enabled is True
    assert wieder.interval_minutes == 60
    assert wieder.with_model is True


def test_unbekannte_quelle_wird_abgewiesen(client: TestClient) -> None:
    antwort = client.put("/schedule", json={"sources": {"/tmp/x": "evernote"}})
    assert antwort.status_code == 400
    assert "evernote" in antwort.json()["detail"]


def test_lauf_von_hand_geht_auch_bei_ausgeschaltetem_plan(client: TestClient) -> None:
    """Wer den Zeitplan nicht will, soll trotzdem einmal drücken können."""
    assert client.get("/schedule").json()["enabled"] is False

    antwort = client.post("/schedule/run").json()

    assert antwort["ok"] is True
    assert any(j["name"] == "verdichtung" for j in antwort["jobs"])


def test_ein_lauf_fuellt_die_schlange_nicht_den_bestand(client: TestClient) -> None:
    """Die Eigenschaft, die den Prozess unbedenklich macht."""
    alt = (now() - timedelta(days=400)).isoformat()
    client.post("/assertions", json={
        "statement": "Projekt A ist blockiert.", "kind": "state",
        "provenance": {"source_type": "chat", "captured_at": alt},
    })
    vorher = client.get("/assertions").json()

    client.post("/schedule/run")

    assert client.get("/proposals").json()  # es wurde etwas vorgelegt
    # Und der Bestand ist unverändert: gleiche Anzahl, nichts bestätigt.
    nachher = client.get("/assertions").json()
    assert len(nachher) == len(vorher)
    # `to_dict()` lässt leere Felder weg, deshalb beidseitig mit `.get()`.
    assert nachher[0].get("last_confirmed_at") == vorher[0].get("last_confirmed_at")
    assert nachher[0]["status"] == vorher[0]["status"]


def test_ohne_datenbank_ist_die_sicherung_kein_fehler(tmp_path) -> None:
    """Beim ersten Start gibt es noch nichts zu sichern.

    Das als Fehler zu melden hieße, dass ein neuer Nutzer bei jedem Lauf einen
    roten Schritt sieht — und wer sich an rote Meldungen gewöhnt, übersieht die
    eine, die zählt.
    """
    from icarus_memory.scheduler import backup_job

    ergebnis = backup_job(tmp_path)()
    assert ergebnis.ok is True
    assert "nichts zu sichern" in ergebnis.detail


def test_der_lauf_sichert_das_selbstmodell(client: TestClient) -> None:
    """Eine Sicherung, die nur läuft, wenn jemand daran denkt, verhindert den
    einen katastrophalen Fehlerfall nicht."""
    client.post("/assertions", json={
        "statement": "Etwas Merkenswertes.", "kind": "identity",
        "provenance": {"source_type": "user_stated"},
    })

    antwort = client.post("/schedule/run").json()

    sicherung = [j for j in antwort["jobs"] if j["name"] == "sicherung"]
    assert sicherung and sicherung[0]["ok"] is True
    assert client.get("/backups").json()


def test_aufnahme_laeuft_nur_fuer_eingestellte_ordner(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    vault = tmp_path / "Vault"
    vault.mkdir()
    (vault / "a.md").write_text("Ein Satz.", encoding="utf-8")

    # Ohne Quellen passiert bei der Aufnahme nichts.
    assert not any(
        j["name"].startswith("aufnahme")
        for j in client.post("/schedule/run").json()["jobs"]
    )

    client.put("/setup", json={"file_roots": [str(tmp_path)]})
    client.put("/schedule", json={"sources": {str(vault): "markdown"}})

    jobs = client.post("/schedule/run").json()["jobs"]
    aufnahme = [j for j in jobs if j["name"].startswith("aufnahme")]
    assert aufnahme and "1 neu" in aufnahme[0]["detail"]

    # Zweiter Lauf: nichts doppelt. Das ist die Zusicherung, ohne die ein
    # wiederholter Lauf keine Option wäre.
    jobs = client.post("/schedule/run").json()["jobs"]
    aufnahme = [j for j in jobs if j["name"].startswith("aufnahme")]
    assert "0 neu, 1 bekannt" in aufnahme[0]["detail"]


def test_unerreichbarer_ordner_kippt_den_lauf_nicht(
    client: TestClient, tmp_path
) -> None:
    client.put("/setup", json={"file_roots": [str(tmp_path)]})
    client.put("/schedule", json={"sources": {str(tmp_path / "weg"): "markdown"}})

    antwort = client.post("/schedule/run").json()

    assert antwort["ok"] is False
    # Verdichtung und Sicherung liefen trotzdem.
    assert any(j["name"] == "verdichtung" and j["ok"] for j in antwort["jobs"])
    assert any(j["name"] == "sicherung" and j["ok"] for j in antwort["jobs"])
