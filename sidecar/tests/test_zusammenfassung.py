"""Tests der Zusammenfassungsschicht.

Die eine Frage, an der alles hängt: Kann das Verfahren etwas verlieren? Wenn
nein, ist eine schlechte Zusammenfassung ein Ärgernis. Wenn ja, ist sie ein
Datenverlust, und dann darf man es gar nicht erst laufen lassen.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from icarus_memory import SelfModelStore, SqliteBackend
from icarus_memory.audit import AuditLog
from icarus_memory.episodes import (
    EpisodeError,
    EpisodeKind,
    EpisodeState,
    EpisodeStore,
)
from icarus_memory.model import Provenance, SourceType, now
from icarus_memory.proposals import ProposalStore
from icarus_memory.server import create_app
from icarus_memory.summaries import (
    MIN_EPISODES,
    SUMMARISE_AFTER_DAYS,
    Summarizer,
    _parse_summary,
)
from icarus_memory.tasks import TaskStore
from icarus_memory.workspace import WorkspaceStore


class FakeProvider:
    """Ein Anbieter, der zurückgibt, was der Test vorgibt."""

    model = "test-modell"

    def __init__(self, antwort: str = "") -> None:
        self.antwort = antwort or (
            '{"titel": "Rückblick April", "rueckblick": '
            '"Arbeitete durchgehend an Projekt A."}'
        )
        self.gefragt: list[str] = []

    def complete(self, messages, tools):  # noqa: ANN001
        self.gefragt.append(messages[-1]["content"])

        class Antwort:
            text = self.antwort

        return Antwort()


@pytest.fixture
def episodes(tmp_path) -> EpisodeStore:
    return EpisodeStore(tmp_path / "episodes.sqlite3")


def alt(episodes: EpisodeStore, n: int, monat: int = 4, jahr: int = 2020,
        praefix: str = "Notiz") -> list:
    """Legt `n` alte, bereits angesehene Episoden in einem Monat an."""
    entstanden = []
    for i in range(n):
        episode, _ = episodes.record(
            EpisodeKind.DOCUMENT,
            f"{praefix} {i}",
            f"Inhalt {praefix} {i} über Projekt A.",
            Provenance(source_type=SourceType.DOCUMENT),
            occurred_at=now().replace(year=jahr, month=monat, day=1 + i % 27),
        )
        episodes.mark_consolidated(episode.id)
        entstanden.append(episodes.get(episode.id))
    return entstanden


# -- Was zusammengefasst werden darf ----------------------------------------


def test_frisches_material_bleibt_in_ruhe(episodes: EpisodeStore) -> None:
    """Was letzte Woche war, braucht man noch im Wortlaut."""
    for i in range(10):
        e, _ = episodes.record(
            EpisodeKind.DOCUMENT, f"N{i}", f"Inhalt {i}",
            Provenance(source_type=SourceType.DOCUMENT),
            occurred_at=now() - timedelta(days=3),
        )
        episodes.mark_consolidated(e.id)

    assert Summarizer(episodes).candidates() == []


def test_ungesehenes_wird_nie_eingeschmolzen(episodes: EpisodeStore) -> None:
    """Dieselbe Regel wie beim Archivieren.

    Stilles Vergessen träfe sonst genau das Material, das noch Arbeit erzeugen
    sollte — und niemand würde es je bemerken.
    """
    for i in range(10):
        episodes.record(
            EpisodeKind.DOCUMENT, f"N{i}", f"Inhalt {i}",
            Provenance(source_type=SourceType.DOCUMENT),
            occurred_at=now() - timedelta(days=SUMMARISE_AFTER_DAYS + 30),
        )

    assert Summarizer(episodes).candidates() == []


def test_was_der_bestand_benutzt_bleibt_ganz(episodes: EpisodeStore) -> None:
    """Der eine Punkt, an dem dieses Projekt nicht verhandelt.

    Eine Aussage zeigt über `derived_from` auf ihre Episode. Ginge die in einer
    Zusammenfassung auf, wäre der Weg vom Fakt zurück zum Rohtext gekappt.
    """
    gruppe = alt(episodes, MIN_EPISODES + 2)
    episodes.mark_consolidated(gruppe[0].id, produced=["a-1"])

    kandidaten = Summarizer(episodes).candidates()

    assert len(kandidaten) == 1
    ids = {e.id for e in kandidaten[0].episodes}
    assert gruppe[0].id not in ids
    assert len(ids) == MIN_EPISODES + 1


def test_zu_wenig_material_wird_nicht_zusammengefasst(episodes: EpisodeStore) -> None:
    """Drei Notizen sind schon die Übersicht."""
    alt(episodes, MIN_EPISODES - 1)
    provider = FakeProvider()

    report = Summarizer(episodes, provider).run()

    assert report.written == 0
    assert report.skipped == 1
    assert provider.gefragt == []


def test_monate_werden_getrennt(episodes: EpisodeStore) -> None:
    alt(episodes, MIN_EPISODES, monat=4, praefix="April")
    alt(episodes, MIN_EPISODES, monat=5, praefix="Mai")

    kandidaten = Summarizer(episodes).candidates()

    assert [k.period for k in kandidaten] == ["2020-04", "2020-05"]


# -- Ohne Modell ------------------------------------------------------------


def test_ohne_modell_wird_gefunden_aber_nichts_geschrieben(
    episodes: EpisodeStore,
) -> None:
    """Nützlich statt Notbetrieb: Die Oberfläche kann sagen, was passieren
    *würde*, bevor jemand einen Anbieter einträgt."""
    alt(episodes, MIN_EPISODES + 3)

    report = Summarizer(episodes, provider=None).run()

    assert report.candidates == 1
    assert report.written == 0
    assert report.used_model is False
    assert "Modell" in report.summary()
    assert episodes.summaries() == []


# -- Mit Modell -------------------------------------------------------------


def test_die_zusammenfassung_nennt_ihre_quellen(episodes: EpisodeStore) -> None:
    gruppe = alt(episodes, MIN_EPISODES)

    Summarizer(episodes, FakeProvider()).run()

    zusammen = episodes.summaries()
    assert len(zusammen) == 1
    assert set(zusammen[0].covers) == {e.id for e in gruppe}
    assert zusammen[0].period == "2020-04"
    assert zusammen[0].provenance.extracted_by == "modell/test-modell"


def test_die_quellen_bleiben_liegen(episodes: EpisodeStore) -> None:
    """Der Punkt, an dem sich entscheidet, ob man das laufen lassen darf."""
    gruppe = alt(episodes, MIN_EPISODES)

    Summarizer(episodes, FakeProvider()).run()

    for original in gruppe:
        wieder = episodes.get(original.id)
        assert wieder.state is EpisodeState.ARCHIVED
        assert wieder.body == original.body


def test_eine_zusammenfassung_kommt_nie_in_die_verdichtung(
    episodes: EpisodeStore,
) -> None:
    """Sonst prüfte das Modell sein Zitat gegen einen Text, den es selbst
    geschrieben hat — der Beleg zeigte auf eine Behauptung statt auf Material."""
    alt(episodes, MIN_EPISODES)
    Summarizer(episodes, FakeProvider()).run()

    assert episodes.summaries()
    assert episodes.pending() == []
    assert all(e.kind is not EpisodeKind.SUMMARY for e in episodes.pending(1000))


def test_zweiter_lauf_schreibt_den_monat_nicht_erneut(episodes: EpisodeStore) -> None:
    """Über den Digest ginge das nicht: Ein Modell schreibt denselben Monat
    zweimal mit anderen Worten."""
    alt(episodes, MIN_EPISODES)
    provider = FakeProvider()

    Summarizer(episodes, provider).run()
    zweiter = Summarizer(episodes, provider).run()

    assert zweiter.written == 0
    assert zweiter.candidates == 0
    assert len(episodes.summaries()) == 1


def test_leerer_rueckblick_archiviert_nichts(episodes: EpisodeStore) -> None:
    """Sonst verschwände ein ganzer Monat hinter einer Überschrift ohne Inhalt."""
    gruppe = alt(episodes, MIN_EPISODES)

    report = Summarizer(episodes, FakeProvider('{"titel": "x"}')).run()

    assert report.written == 0
    assert report.errors
    assert episodes.summaries() == []
    assert all(
        episodes.get(e.id).state is EpisodeState.CONSOLIDATED for e in gruppe
    )


def test_ein_ausfall_verbrennt_kein_material(episodes: EpisodeStore) -> None:
    class Kaputt:
        model = "x"

        def complete(self, messages, tools):  # noqa: ANN001
            raise RuntimeError("Anbieter weg")

    gruppe = alt(episodes, MIN_EPISODES)
    report = Summarizer(episodes, Kaputt()).run()

    assert report.written == 0
    assert report.errors
    assert all(
        episodes.get(e.id).state is EpisodeState.CONSOLIDATED for e in gruppe
    )


def test_das_material_geht_als_fremder_inhalt_hinein(episodes: EpisodeStore) -> None:
    """Eine Notiz kann eine Anweisung enthalten; hier ist sie Material."""
    alt(episodes, MIN_EPISODES)
    provider = FakeProvider()

    Summarizer(episodes, provider).run()

    assert provider.gefragt
    assert "ANFANG FREMDER INHALT" in provider.gefragt[0]
    assert "zeitraum:2020-04" in provider.gefragt[0]


# -- Zurücknehmen -----------------------------------------------------------


def test_eine_zusammenfassung_laesst_sich_zuruecknehmen(
    episodes: EpisodeStore,
) -> None:
    """Ohne diesen Weg wäre das Verfahren eine Einbahnstraße."""
    gruppe = alt(episodes, MIN_EPISODES)
    Summarizer(episodes, FakeProvider()).run()
    zusammen = episodes.summaries()[0]

    zurueck = episodes.delete_summary(zusammen.id)

    assert zurueck == MIN_EPISODES
    assert episodes.summaries() == []
    for original in gruppe:
        assert episodes.get(original.id).state is EpisodeState.CONSOLIDATED
    with pytest.raises(EpisodeError):
        episodes.get(zusammen.id)


def test_rohmaterial_laesst_sich_nicht_loeschen(episodes: EpisodeStore) -> None:
    """Die Zusammenfassung ist die einzige Episode, die Icarus selbst schreibt,
    und deshalb die einzige, die je verschwinden darf."""
    gruppe = alt(episodes, 1)

    with pytest.raises(EpisodeError, match="Rohmaterial"):
        episodes.delete_summary(gruppe[0].id)

    assert episodes.get(gruppe[0].id)


def test_eine_zusammenfassung_ohne_quellen_wird_abgewiesen(
    episodes: EpisodeStore,
) -> None:
    with pytest.raises(EpisodeError, match="Herkunft"):
        episodes.record_summary("x", "y", "2020-04", covers=[])


# -- Antwort lesen ----------------------------------------------------------


def test_json_im_codeblock_wird_gelesen() -> None:
    titel, text = _parse_summary('```json\n{"titel": "A", "rueckblick": "B"}\n```')
    assert (titel, text) == ("A", "B")


def test_unlesbares_gilt_als_leer() -> None:
    assert _parse_summary("Klar, hier ist dein Rückblick!") == ("", "")


# -- Über die Schnittstelle -------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    for name in ("ICARUS_PROVIDER", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    app = create_app(
        SelfModelStore(SqliteBackend(tmp_path / "self-model.sqlite3"), subject_id="t"),
        audit=AuditLog(tmp_path / "audit.sqlite3"),
        tasks=TaskStore(tmp_path / "tasks.sqlite3"),
        workspace=WorkspaceStore(tmp_path / "workspace.sqlite3"),
        episodes=EpisodeStore(tmp_path / "episodes.sqlite3"),
        proposals=ProposalStore(tmp_path / "proposals.sqlite3"),
    )
    yield TestClient(app)
    app.state.scheduler.stop()


def test_kandidaten_sind_ohne_modell_sichtbar(client: TestClient) -> None:
    alt(client.app.state.episodes, MIN_EPISODES + 1)

    antwort = client.get("/summaries").json()

    assert antwort["items"] == []
    assert antwort["candidates"][0]["period"] == "2020-04"
    assert antwort["candidates"][0]["count"] == MIN_EPISODES + 1


def test_lauf_ohne_modell_sagt_das_auch(client: TestClient) -> None:
    alt(client.app.state.episodes, MIN_EPISODES + 1)

    antwort = client.post("/summaries/run", json={}).json()

    assert antwort["written"] == 0
    assert antwort["used_model"] is False
    assert "Modell" in antwort["summary"]


def test_rohmaterial_ueber_die_schnittstelle_zu_loeschen_gibt_409(
    client: TestClient,
) -> None:
    gruppe = alt(client.app.state.episodes, 1)

    antwort = client.delete(f"/summaries/{gruppe[0].id}")

    assert antwort.status_code == 409
    assert "Rohmaterial" in antwort.json()["detail"]


def test_der_zeitplan_fasst_mit_zusammen(client: TestClient) -> None:
    """Der Schritt läuft nach der Verdichtung, nicht davor: Sonst verschwände
    Material aus der Verdichtung, das nie jemand angesehen hat."""
    antwort = client.post("/schedule/run").json()

    namen = [j["name"] for j in antwort["jobs"]]
    assert namen.index("verdichtung") < namen.index("zusammenfassung")
    assert namen.index("zusammenfassung") < namen.index("sicherung")
