"""Tests der Arbeitsebene: Projekte, Notizen und ihre Verbindung zu Aufgaben."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from icarus_memory.model import Provenance, SourceType, now
from icarus_memory.tasks import TaskStore
from icarus_memory.workspace import (
    NoteKind,
    Priority,
    ProjectStatus,
    WorkspaceError,
    WorkspaceStore,
)


@pytest.fixture
def workspace(tmp_path) -> WorkspaceStore:
    return WorkspaceStore(tmp_path / "workspace.sqlite3")


@pytest.fixture
def tasks(tmp_path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.sqlite3")


def _prov(ref: str = "chat:1") -> Provenance:
    return Provenance(source_type=SourceType.CHAT, source_ref=ref, captured_at=now())


# -- Projekte ---------------------------------------------------------------


def test_projekt_traegt_seine_herkunft(workspace: WorkspaceStore) -> None:
    p = workspace.add_project("NutriFlow Pro", _prov("chat:7"), area="Icarus Health")
    assert p.provenance.source_type is SourceType.CHAT
    assert p.provenance.source_ref == "chat:7"
    assert workspace.project(p.id).area == "Icarus Health"


def test_unbekanntes_projekt_wirft(workspace: WorkspaceStore) -> None:
    with pytest.raises(WorkspaceError):
        workspace.project("p-gibtesnicht")


def test_abgeschlossen_und_aufgegeben_sind_nicht_dasselbe(
    workspace: WorkspaceStore,
) -> None:
    """Der Unterschied ist der Grund, warum es beide Zustände gibt.

    Ein System, das Jahre läuft, darf in der Rückschau nicht so aussehen, als
    wäre alles gelungen.
    """
    fertig = workspace.add_project("Buch abgeben", _prov())
    weg = workspace.add_project("Podcast starten", _prov())

    workspace.update_project(fertig.id, status=ProjectStatus.DONE)
    workspace.update_project(weg.id, status=ProjectStatus.DROPPED)

    assert workspace.project(fertig.id).status is ProjectStatus.DONE
    assert workspace.project(weg.id).status is ProjectStatus.DROPPED
    # Beide sind zu, beide tragen einen Zeitpunkt — unterscheidbar bleiben sie.
    assert workspace.project(fertig.id).closed_at is not None
    assert workspace.project(weg.id).closed_at is not None


def test_geschlossene_projekte_sind_standardmaessig_weg(
    workspace: WorkspaceStore,
) -> None:
    offen = workspace.add_project("Läuft", _prov())
    zu = workspace.add_project("Fertig", _prov())
    workspace.update_project(zu.id, status=ProjectStatus.DONE)

    assert [p.id for p in workspace.projects()] == [offen.id]
    assert {p.id for p in workspace.projects(include_closed=True)} == {offen.id, zu.id}


def test_wiedereroeffnen_loescht_den_abschlusszeitpunkt(
    workspace: WorkspaceStore,
) -> None:
    p = workspace.add_project("Doch nicht fertig", _prov())
    workspace.update_project(p.id, status=ProjectStatus.DONE)
    assert workspace.project(p.id).closed_at is not None

    workspace.update_project(p.id, status=ProjectStatus.ACTIVE)
    assert workspace.project(p.id).closed_at is None


def test_liste_sortiert_nach_frist_dann_prioritaet(workspace: WorkspaceStore) -> None:
    """Nicht nach Anlagedatum.

    Eine Liste, die das älteste Projekt oben zeigt, beantwortet keine Frage,
    die morgens gestellt wird.
    """
    bald = now() + timedelta(days=2)
    spaet = now() + timedelta(days=90)

    ohne_frist = workspace.add_project("Irgendwann", _prov(), priority=Priority.HIGH)
    spaeter = workspace.add_project("Später", _prov(), deadline=spaet)
    dringend = workspace.add_project("Dringend", _prov(), deadline=bald)

    assert [p.id for p in workspace.projects()] == [dringend.id, spaeter.id, ohne_frist.id]


def test_projekt_ueber_namensfragment_finden(workspace: WorkspaceStore) -> None:
    """Der Nutzer sagt „NutriFlow", nicht `p-3f2a…`."""
    p = workspace.add_project("NutriFlow Pro", _prov())
    assert workspace.find_project("nutriflow").id == p.id
    assert workspace.find_project(p.id).id == p.id
    assert workspace.find_project("gibtesnicht") is None
    assert workspace.find_project("") is None


def test_offenes_projekt_gewinnt_bei_gleichem_namen(workspace: WorkspaceStore) -> None:
    alt = workspace.add_project("Icarus", _prov())
    workspace.update_project(alt.id, status=ProjectStatus.DONE)
    neu = workspace.add_project("Icarus", _prov())

    assert workspace.find_project("Icarus").id == neu.id


# -- Notizen ----------------------------------------------------------------


def test_notiz_an_erfundenem_projekt_wird_abgelehnt(workspace: WorkspaceStore) -> None:
    """Fail-closed: sonst wäre die Notiz still verloren.

    Sie tauchte in keiner Projektansicht je wieder auf, und niemand merkte es.
    """
    with pytest.raises(WorkspaceError):
        workspace.add_note("Protokoll", "…", _prov(), project_id="p-gibtesnicht")


def test_notiz_ist_veraenderbar_die_herkunft_nicht(workspace: WorkspaceStore) -> None:
    """Der bewusste Unterschied zur Aussagenschicht.

    Eine Notiz ist ein Arbeitsdokument und darf überschrieben werden. Woher sie
    stammt, darf sich dabei nicht ändern — sonst ließe sich ein
    Transkriptauszug nachträglich zu einer Nutzeräußerung umwidmen.
    """
    n = workspace.add_note(
        "Kickoff", "Erste Fassung",
        Provenance(source_type=SourceType.DOCUMENT, source_ref="transkript:12"),
    )
    assert n.revision == 1

    geaendert = workspace.update_note(n.id, body="Korrigierte Fassung")

    assert geaendert.body == "Korrigierte Fassung"
    assert geaendert.revision == 2
    assert geaendert.updated_at >= n.created_at
    assert geaendert.provenance.source_type is SourceType.DOCUMENT
    assert geaendert.provenance.source_ref == "transkript:12"


def test_notiz_umhaengen_prueft_das_ziel(workspace: WorkspaceStore) -> None:
    p = workspace.add_project("Ziel", _prov())
    n = workspace.add_note("Frei", "…", _prov())

    assert workspace.update_note(n.id, project_id=p.id).project_id == p.id
    with pytest.raises(WorkspaceError):
        workspace.update_note(n.id, project_id="p-gibtesnicht")


def test_notizen_nach_projekt_und_art_filtern(workspace: WorkspaceStore) -> None:
    p = workspace.add_project("NutriFlow Pro", _prov())
    workspace.add_note("Jour fixe", "…", _prov(), project_id=p.id, kind=NoteKind.MEETING)
    workspace.add_note("Warum Supabase", "…", _prov(), project_id=p.id,
                       kind=NoteKind.DECISION)
    workspace.add_note("Loses Zeug", "…", _prov())

    assert len(workspace.notes(project_id=p.id)) == 2
    assert len(workspace.notes(project_id=p.id, kind=NoteKind.DECISION)) == 1
    assert len(workspace.notes()) == 3


def test_notizsuche_findet_titel_und_text(workspace: WorkspaceStore) -> None:
    """Bewusst ohne Modell: Notizen zu finden darf nie von einem Anbieter abhängen."""
    workspace.add_note("Jour fixe", "Wir bleiben bei Postgres.", _prov())
    workspace.add_note("Postgres-Migration", "Steht an.", _prov())
    workspace.add_note("Ganz was anderes", "Nichts davon.", _prov())

    assert len(workspace.search_notes("Postgres")) == 2
    assert workspace.search_notes("gibtesnicht") == []


def test_bereiche_werden_aufgezaehlt(workspace: WorkspaceStore) -> None:
    workspace.add_project("A", _prov(), area="Icarus Health")
    workspace.add_project("B", _prov(), area="Icarus Health")
    workspace.add_project("C", _prov(), area="MBA")
    workspace.add_project("D", _prov())

    assert workspace.areas() == ["Icarus Health", "MBA"]


# -- Aufgaben am Projekt ----------------------------------------------------


def test_aufgaben_haengen_an_einem_projekt(
    workspace: WorkspaceStore, tasks: TaskStore
) -> None:
    p = workspace.add_project("NutriFlow Pro", _prov())
    am_projekt = tasks.add("BLS-Import fertigstellen", _prov(), project_id=p.id)
    tasks.add("Zahnarzt anrufen", _prov())

    assert [t.id for t in tasks.by_project(p.id)] == [am_projekt.id]
    # Projektlose Aufgaben bleiben erlaubt — nicht alles im Leben ist ein Projekt.
    assert len(tasks.open_tasks()) == 2


def test_erledigte_aufgaben_verschwinden_aus_der_projektansicht(
    workspace: WorkspaceStore, tasks: TaskStore
) -> None:
    p = workspace.add_project("NutriFlow Pro", _prov())
    t = tasks.add("Erledigt gleich", _prov(), project_id=p.id)

    tasks.complete(t.id)

    assert tasks.by_project(p.id) == []
    assert len(tasks.by_project(p.id, include_closed=True)) == 1


def test_projektzuordnung_ueberlebt_den_neustart(
    tmp_path, workspace: WorkspaceStore
) -> None:
    """Die Spalte wird bei bestehenden Dateien nachgezogen — hier der Beleg,
    dass die Zuordnung wirklich auf der Platte landet und nicht nur im JSON."""
    p = workspace.add_project("NutriFlow Pro", _prov())

    erste = TaskStore(tmp_path / "neu.sqlite3")
    t = erste.add("Bleibt zugeordnet", _prov(), project_id=p.id)
    erste.close()

    zweite = TaskStore(tmp_path / "neu.sqlite3")
    assert [x.id for x in zweite.by_project(p.id)] == [t.id]
    zweite.close()


def test_bestehende_aufgabendatei_bekommt_die_spalte(tmp_path) -> None:
    """Eine Datei aus der Zeit vor der Projektebene darf nicht kippen."""
    import sqlite3

    path = tmp_path / "alt.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
        "status TEXT NOT NULL, due TEXT, title TEXT NOT NULL, document TEXT NOT NULL);"
    )
    conn.commit()
    conn.close()

    store = TaskStore(path)
    t = store.add("Geht trotzdem", _prov(), project_id="p-egal")
    assert store.by_project("p-egal")[0].id == t.id
    store.close()


def test_naive_fristen_bekommen_eine_zeitzone(workspace: WorkspaceStore) -> None:
    """Über HTTP kommen Fristen ohne Zone herein.

    Ohne Normalisierung an der Eingangsstelle fliegt der Vergleich später an
    beliebiger Stelle zur Laufzeit.
    """
    p = workspace.add_project("Frist ohne Zone", _prov(),
                              deadline=datetime(2026, 12, 1, 23, 59))
    assert workspace.project(p.id).deadline.tzinfo is not None
    # Und die Sortierung vergleicht ihn ohne TypeError.
    workspace.add_project("Andere", _prov(),
                          deadline=datetime.now(timezone.utc) + timedelta(days=1))
    assert len(workspace.projects()) == 2
