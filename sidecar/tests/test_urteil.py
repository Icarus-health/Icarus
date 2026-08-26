"""Dringend gegen wichtig.

Was wichtig ist, mahnt niemand an. Eine überfällige Aufgabe meldet sich von
selbst — sie hat ein Datum. Ein Vorhaben, an dem seit sechs Wochen nichts
passiert, meldet sich nie.

Die wichtigsten Tests sind auch hier die negativen: dass ein Ziel, an dem
gearbeitet wird, **schweigt**; dass ein Ziel ohne Marken **kein** Urteil
bekommt statt eines falschen; und dass dieses Modul nichts schreibt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from icarus_memory import (
    Kind,
    MemoryBackend,
    Provenance,
    SelfModelStore,
    SourceType,
)
from icarus_memory import briefing, urteil
from icarus_memory.episodes import EpisodeKind, EpisodeStore
from icarus_memory.tasks import TaskStore

JETZT = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store() -> SelfModelStore:
    return SelfModelStore(MemoryBackend(), subject_id="t")


@pytest.fixture()
def episodes(tmp_path) -> EpisodeStore:
    return EpisodeStore(tmp_path / "episodes.sqlite3")


@pytest.fixture()
def tasks(tmp_path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.sqlite3")


def herkunft() -> Provenance:
    return Provenance(source_type=SourceType.USER_STATED, captured_at=JETZT)


def ziel(store, satz: str, marken: list[str], *, vor_tagen: int = 120):
    return store.record(
        satz, Kind.GOAL, herkunft(),
        tags=marken, at=JETZT - timedelta(days=vor_tagen),
    )


def notiz(episodes, marken: list[str], *, vor_tagen: int):
    return episodes.record(
        EpisodeKind.MESSAGE, "Notiz", "Inhalt",
        Provenance(source_type=SourceType.DOCUMENT),
        occurred_at=JETZT - timedelta(days=vor_tagen),
        tags=marken,
    )


def hole(store, **kw):
    return urteil.vorhaben(store=store, jetzt=JETZT, **kw)


# -- Was schweigen soll -----------------------------------------------------


def test_ohne_ziele_gibt_es_kein_urteil(store) -> None:
    store.record("Irgendetwas gilt.", Kind.STATE, herkunft())

    assert hole(store) == []


def test_ein_ziel_an_dem_gearbeitet_wird_schlaeft_nicht(store, episodes) -> None:
    ziel(store, "Die Praxisumstellung bis Jahresende abschließen.", ["umstellung"])
    notiz(episodes, ["umstellung"], vor_tagen=3)

    eins = hole(store, episodes=episodes)[0]

    assert eins.schlaeft(JETZT) is False
    assert eins.tage_still(JETZT) == 3
    assert urteil.eingeschlafen(store=store, episodes=episodes, jetzt=JETZT) == []


def test_ein_ziel_ohne_marken_bekommt_kein_urteil(store, episodes) -> None:
    """Ohne Marken gibt es keinen Weg vom Ziel zu dem, was dafür getan wurde.
    Dann ist Schweigen die ehrliche Antwort — nicht „seit immer nichts“."""
    ziel(store, "Irgendwann mal aufräumen.", [])

    eins = hole(store, episodes=episodes)[0]

    assert eins.beurteilbar is False
    assert eins.tage_still(JETZT) is None
    assert eins.schlaeft(JETZT) is False


def test_ein_widerrufenes_ziel_zaehlt_nicht_mehr(store, episodes) -> None:
    z = ziel(store, "Die Praxisumstellung abschließen.", ["umstellung"])
    store.retract(z.id, at=JETZT)

    assert hole(store, episodes=episodes) == []


# -- Was auffallen soll -----------------------------------------------------


def test_ein_stillstehendes_vorhaben_faellt_auf(store, episodes) -> None:
    ziel(store, "Die Praxisumstellung bis Jahresende abschließen.", ["umstellung"])
    notiz(episodes, ["umstellung"], vor_tagen=50)

    schlafend = urteil.eingeschlafen(store=store, episodes=episodes, jetzt=JETZT)

    assert len(schlafend) == 1
    assert schlafend[0].tage_still(JETZT) == 50
    assert schlafend[0].woran == "einer Notiz"


def test_eine_aufgabe_zaehlt_auch_als_regung(store, tasks) -> None:
    ziel(store, "Die Praxisumstellung abschließen.", ["umstellung"])
    tasks.add("Schulung planen", herkunft(), tags=["umstellung"],
              at=JETZT - timedelta(days=5))

    eins = hole(store, tasks=tasks)[0]

    assert eins.tage_still(JETZT) == 5
    assert eins.woran == "einer Aufgabe"


def test_eine_aussage_zaehlt_auch_als_regung(store) -> None:
    ziel(store, "Die Praxisumstellung abschließen.", ["umstellung"])
    store.record("Das neue System läuft im Testbetrieb.", Kind.STATE, herkunft(),
                 tags=["umstellung"], at=JETZT - timedelta(days=8))

    eins = hole(store)[0]

    assert eins.tage_still(JETZT) == 8
    assert eins.woran == "einer Aussage"


def test_die_juengste_regung_zaehlt(store, episodes, tasks) -> None:
    ziel(store, "Die Praxisumstellung abschließen.", ["umstellung"])
    notiz(episodes, ["umstellung"], vor_tagen=60)
    tasks.add("Schulung planen", herkunft(), tags=["umstellung"],
              at=JETZT - timedelta(days=4))

    eins = hole(store, episodes=episodes, tasks=tasks)[0]

    assert eins.tage_still(JETZT) == 4


def test_fremde_marken_zaehlen_nicht(store, episodes) -> None:
    ziel(store, "Die Praxisumstellung abschließen.", ["umstellung"])
    notiz(episodes, ["vertrag"], vor_tagen=1)

    eins = hole(store, episodes=episodes)[0]

    # Ohne eigene Regung zählt das Alter des Ziels selbst.
    assert eins.tage_still(JETZT) == 120
    assert eins.schlaeft(JETZT) is True


def test_das_stillste_steht_oben(store, episodes) -> None:
    ziel(store, "Laut.", ["laut"], vor_tagen=200)
    ziel(store, "Still.", ["still"], vor_tagen=200)
    notiz(episodes, ["laut"], vor_tagen=2)
    notiz(episodes, ["still"], vor_tagen=90)

    liste = hole(store, episodes=episodes)

    assert [v.ziel.statement for v in liste] == ["Still.", "Laut."]


def test_marken_werden_unabhaengig_von_schreibweise_verglichen(store, episodes) -> None:
    ziel(store, "Die Praxisumstellung abschließen.", ["Umstellung"])
    notiz(episodes, ["umstellung"], vor_tagen=2)

    assert hole(store, episodes=episodes)[0].tage_still(JETZT) == 2


# -- Es wird nichts geschrieben ---------------------------------------------


def test_das_modul_schreibt_nichts(store, episodes) -> None:
    """Ob ein ruhendes Vorhaben ein Problem ist, weiß nur der Mensch —
    vielleicht ruht es mit Absicht."""
    z = ziel(store, "Die Praxisumstellung abschließen.", ["umstellung"])
    notiz(episodes, ["umstellung"], vor_tagen=90)
    vorher = len(store.alles())

    urteil.eingeschlafen(store=store, episodes=episodes, jetzt=JETZT)

    assert len(store.alles()) == vorher
    assert {a.id: a for a in store.alles()}[z.id].status.value == "active"


def test_ein_kaputter_bereich_kippt_nichts(store) -> None:
    class Kaputt:
        def all_episodes(self, *a, **k):
            raise RuntimeError("Ablage nicht lesbar")

    ziel(store, "Die Praxisumstellung abschließen.", ["umstellung"])

    assert len(hole(store, episodes=Kaputt())) == 1


# -- Im Briefing ------------------------------------------------------------


def stand(schlafend: list[dict], aufgaben: list[dict] | None = None) -> dict:
    return {
        "tasks": {"items": aufgaben or [], "overdue": 0},
        "calendar": {"items": []},
        "mail": {"items": [], "unread": 0},
        "episodes": {"pending": 0},
        "memory": {"count": 0, "recent": []},
        "goals": {"eingeschlafen": schlafend},
    }


def ruhendes(satz: str, tage: int, woran: str | None = "einer Notiz") -> dict:
    return {
        "id": "a-1", "satz": satz, "marken": ["umstellung"],
        "beurteilbar": True, "woran": woran,
        "tage_still": tage, "schlaeft": True,
    }


def test_ein_ruhendes_vorhaben_kommt_ins_briefing() -> None:
    b = briefing.erstelle(
        stand([ruhendes("Die Praxisumstellung abschließen.", 90)]), jetzt=JETZT
    )

    satz = b.punkte[0].text
    assert "Die Praxisumstellung abschließen" in satz
    assert "Zuletzt in einer Notiz." in satz
    # In Wochen oder Monaten, nie in Tagen: bei drei Monaten interessiert kein
    # einzelner Tag, und eine genaue Zahl täuscht Genauigkeit vor.
    assert "Tagen" not in satz
    assert "drei Monaten" in satz


def test_ohne_bekannte_regung_wird_keine_erfunden() -> None:
    b = briefing.erstelle(
        stand([ruhendes("Die Praxisumstellung abschließen.", 90, woran=None)]),
        jetzt=JETZT,
    )

    assert "Zuletzt" not in b.punkte[0].text


def test_an_einem_ruhigen_tag_steht_es_da() -> None:
    b = briefing.erstelle(
        stand([ruhendes("Die Praxisumstellung abschließen.", 200)]), jetzt=JETZT
    )

    assert len(b.punkte) == 1
    assert "Praxisumstellung" in b.punkte[0].text


def test_viel_ueberfaelliges_verdraengt_es_nicht() -> None:
    """Drei überfällige Aufgaben sind **ein** Punkt, nicht drei.

    Das Briefing zählt Gleichartiges zusammen, statt dieselbe Art dreimal
    aufzuführen. Also bleibt Platz, und das leise Vorhaben steht daneben —
    was richtig ist: „du hängst bei drei Dingen hinterher“ und „daran rührt
    seit einem halben Jahr niemand“ sind zwei verschiedene Nachrichten.
    """
    brennend = [
        {"id": f"t-{i}", "title": f"Sache {i}",
         "due": (JETZT - timedelta(days=5 + i)).isoformat(), "overdue": True}
        for i in range(3)
    ]
    b = briefing.erstelle(
        stand([ruhendes("Die Praxisumstellung abschließen.", 200)], brennend),
        jetzt=JETZT,
    )

    assert [p.quelle for p in b.punkte] == ["aufgabe", "vorhaben"]


def test_drei_dringende_arten_verdraengen_das_leise_vorhaben() -> None:
    """Wer drei verschiedene brennende Dinge hat, soll nicht zusätzlich an das
    Halbjahresvorhaben erinnert werden."""
    daten = stand(
        [ruhendes("Die Praxisumstellung abschließen.", 200)],
        [{"id": "t-1", "title": "Überfällig",
          "due": (JETZT - timedelta(days=5)).isoformat(), "overdue": True},
         {"id": "t-2", "title": "Abgegeben", "due": None, "overdue": False,
          "wartet_auf": "Frau Becker",
          "wartet_seit": (JETZT - timedelta(days=40)).isoformat(),
          "wartet_tage": 40}],
    )
    daten["calendar"]["items"] = [{
        "uid": "e-1", "summary": "Jour fixe",
        "start": (JETZT + timedelta(hours=2)).isoformat(),
    }]

    b = briefing.erstelle(daten, jetzt=JETZT)

    assert len(b.punkte) == 3
    assert all("Praxisumstellung" not in p.text for p in b.punkte)
    # Es geht nicht verloren, es drängt sich nur nicht vor.
    assert b.nachsatz


# -- Über die Tür -----------------------------------------------------------


def test_ein_frisches_ziel_belegt_den_morgen_nicht(tmp_path) -> None:
    """Gerade erst gefasst heißt: es hatte noch keine Gelegenheit zu ruhen."""
    from fastapi.testclient import TestClient

    from icarus_memory.server import create_app

    tuer = TestClient(create_app(
        SelfModelStore(MemoryBackend(), subject_id="t"),
        tasks=TaskStore(tmp_path / "tasks.sqlite3"),
        episodes=EpisodeStore(tmp_path / "episodes.sqlite3"),
    ))
    tuer.post("/assertions", json={
        "statement": "Die Praxisumstellung abschließen.",
        "kind": "goal", "tags": ["umstellung"],
        "provenance": {"source_type": "user_stated"},
    })

    stand = tuer.get("/dashboard").json()

    assert stand["goals"]["eingeschlafen"] == []
    assert stand["briefing"]["punkte"] == []
    assert tuer.get("/goals").json()["eingeschlafen"] == 0
