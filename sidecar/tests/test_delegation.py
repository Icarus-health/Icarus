"""Was bei jemand anderem liegt, ist keine Schuld — es ist eine Wartezeit.

Eine flache Aufgabenliste wirft zwei Dinge in einen Topf: was du noch tun
musst, und worauf du wartest. Beides ist offen, aber nur das erste ist Arbeit
für dich. Diese Tests halten die Trennung fest — vor allem die negative Seite:
dass Abgegebenes nicht mehr gegen dich zählt, und dass frisch Abgegebenes den
Morgen nicht belegt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from icarus_memory import briefing
from icarus_memory.model import Provenance, SourceType
from icarus_memory.tasks import TaskStore

JETZT = datetime(2026, 8, 20, 8, 30, tzinfo=timezone.utc)


@pytest.fixture()
def aufgaben(tmp_path) -> TaskStore:
    speicher = TaskStore(tmp_path / "tasks.sqlite3")
    yield speicher
    speicher.close()


def herkunft() -> Provenance:
    return Provenance(source_type=SourceType.USER_STATED)


def lege_an(speicher: TaskStore, titel: str, faellig: datetime | None = None):
    return speicher.add(titel, herkunft(), due=faellig)


# -- Die Trennung -----------------------------------------------------------


def test_abgegebenes_traegt_den_namen(aufgaben: TaskStore) -> None:
    a = lege_an(aufgaben, "Vergütungsspanne festlegen")
    a = aufgaben.warten_auf(a.id, "Frau Becker", at=JETZT)

    assert a.wartet_auf == "Frau Becker"
    assert a.wartet_seit == JETZT
    assert aufgaben.get(a.id).wartet_auf == "Frau Becker"


def test_was_bei_jemandem_liegt_ist_nicht_ueberfaellig(aufgaben: TaskStore) -> None:
    """Der Kern. Ein gerissenes Datum bei jemand anderem ist kein Versäumnis."""
    a = lege_an(aufgaben, "Zuarbeit zum Vertrag", faellig=JETZT - timedelta(days=5))
    assert a.is_overdue(JETZT) is True

    a = aufgaben.warten_auf(a.id, "Frau Becker", at=JETZT)

    assert a.is_overdue(JETZT) is False


def test_zurueckgeholtes_zaehlt_wieder(aufgaben: TaskStore) -> None:
    a = lege_an(aufgaben, "Zuarbeit zum Vertrag", faellig=JETZT - timedelta(days=5))
    aufgaben.warten_auf(a.id, "Frau Becker", at=JETZT)

    a = aufgaben.zurueckholen(a.id)

    assert a.wartet_auf is None
    assert a.wartet_seit is None
    assert a.is_overdue(JETZT) is True


def test_derselbe_name_verlaengert_die_frist_nicht(aufgaben: TaskStore) -> None:
    """Sonst könnte man die eigene Wartezeit wegdrücken, indem man sie wiederholt."""
    a = lege_an(aufgaben, "Zuarbeit zum Vertrag")
    aufgaben.warten_auf(a.id, "Frau Becker", at=JETZT - timedelta(days=30))

    a = aufgaben.warten_auf(a.id, "Frau Becker", at=JETZT)

    assert a.wartet_seit == JETZT - timedelta(days=30)
    assert a.wartet_tage(JETZT) == 30


def test_ein_anderer_name_setzt_die_frist_neu(aufgaben: TaskStore) -> None:
    a = lege_an(aufgaben, "Zuarbeit zum Vertrag")
    aufgaben.warten_auf(a.id, "Frau Becker", at=JETZT - timedelta(days=30))

    a = aufgaben.warten_auf(a.id, "Herr Ohlsen", at=JETZT)

    assert a.wartet_seit == JETZT
    assert a.wartet_tage(JETZT) == 0


def test_ohne_namen_geht_es_nicht(aufgaben: TaskStore) -> None:
    a = lege_an(aufgaben, "Zuarbeit zum Vertrag")

    with pytest.raises(ValueError):
        aufgaben.warten_auf(a.id, "   ")


def test_wartendes_kommt_zusammen_und_das_aelteste_zuerst(aufgaben: TaskStore) -> None:
    alt = lege_an(aufgaben, "Alte Zuarbeit")
    neu = lege_an(aufgaben, "Neue Zuarbeit")
    lege_an(aufgaben, "Liegt bei mir")
    aufgaben.warten_auf(neu.id, "Herr Ohlsen", at=JETZT - timedelta(days=2))
    aufgaben.warten_auf(alt.id, "Frau Becker", at=JETZT - timedelta(days=40))

    liste = aufgaben.wartend()

    assert [t.title for t in liste] == ["Alte Zuarbeit", "Neue Zuarbeit"]


# -- Das Briefing -----------------------------------------------------------


def stand(aufgabenliste: list[dict]) -> dict:
    return {
        "tasks": {"items": aufgabenliste, "overdue": 0},
        "calendar": {"items": []},
        "mail": {"items": [], "unread": 0},
        "episodes": {"pending": 0},
        "memory": {"count": 0, "recent": []},
    }


def wartende_aufgabe(titel: str, name: str, tage: int) -> dict:
    return {
        "id": "t-1",
        "title": titel,
        "due": None,
        "overdue": False,
        "wartet_auf": name,
        "wartet_seit": (JETZT - timedelta(days=tage)).isoformat(),
        "wartet_tage": tage,
    }


def test_frisch_abgegebenes_belegt_den_morgen_nicht() -> None:
    """Wer gestern etwas weitergegeben hat, will heute nicht erinnert werden."""
    b = briefing.erstelle(
        stand([wartende_aufgabe("Vergütungsspanne festlegen", "Frau Becker", 1)]),
        jetzt=JETZT,
    )

    assert b.punkte == []


def test_lange_wartendes_kommt_in_das_briefing() -> None:
    b = briefing.erstelle(
        stand([wartende_aufgabe("Vergütungsspanne festlegen", "Frau Becker", 16)]),
        jetzt=JETZT,
    )

    satz = b.punkte[0].text
    assert "Frau Becker" in satz
    assert "Vergütungsspanne festlegen" in satz
    assert "4. August" in satz
    assert b.punkte[0].aktion == "Zurückholen"
    assert b.punkte[0].quelle == "wartet"


def test_ueberfaelliges_wiegt_schwerer_als_wartendes() -> None:
    """Was du selbst schuldest, steht über dem, was du einforderst."""
    meins = {
        "id": "t-2",
        "title": "Rückfrage an die Kasse",
        "due": (JETZT - timedelta(days=3)).isoformat(),
        "overdue": True,
    }
    b = briefing.erstelle(
        stand([wartende_aufgabe("Vergütungsspanne festlegen", "Frau Becker", 40), meins]),
        jetzt=JETZT,
    )

    assert "Rückfrage an die Kasse" in b.punkte[0].text
    assert "Frau Becker" in b.punkte[1].text


# -- Über die Tür -----------------------------------------------------------


@pytest.fixture()
def tuer(tmp_path):
    """Ein Sidecar mit eigener Aufgabenablage."""
    from fastapi.testclient import TestClient

    from icarus_memory.backends import MemoryBackend
    from icarus_memory.server import create_app
    from icarus_memory.store import SelfModelStore

    app = create_app(
        SelfModelStore(MemoryBackend(), subject_id="test"),
        tasks=TaskStore(tmp_path / "tasks.sqlite3"),
    )
    return TestClient(app)


def test_abgeben_und_zurueckholen_ueber_die_tuer(tuer) -> None:
    angelegt = tuer.post("/tasks", json={"title": "Zuarbeit zum Vertrag"}).json()

    abgegeben = tuer.post(
        f"/tasks/{angelegt['id']}/warten", json={"name": "Frau Becker"}
    )
    assert abgegeben.status_code == 200
    assert abgegeben.json()["wartet_auf"] == "Frau Becker"
    assert abgegeben.json()["overdue"] is False

    zurueck = tuer.post(f"/tasks/{angelegt['id']}/zurueckholen")
    assert zurueck.status_code == 200
    assert zurueck.json()["wartet_auf"] is None


def test_abgeben_ohne_namen_ist_ein_fehler_mit_grund(tuer) -> None:
    angelegt = tuer.post("/tasks", json={"title": "Zuarbeit zum Vertrag"}).json()

    antwort = tuer.post(f"/tasks/{angelegt['id']}/warten", json={"name": "   "})

    assert antwort.status_code == 400
    assert "bei wem" in antwort.json()["detail"]


def test_unbekannte_aufgabe_gibt_404(tuer) -> None:
    antwort = tuer.post("/tasks/t-gibtsnicht/warten", json={"name": "Frau Becker"})
    assert antwort.status_code == 404


def test_dashboard_zaehlt_abgegebenes_nicht_als_ueberfaellig(tuer) -> None:
    vorbei = (datetime.now().astimezone() - timedelta(days=5)).isoformat()
    angelegt = tuer.post(
        "/tasks", json={"title": "Zuarbeit zum Vertrag", "due": vorbei}
    ).json()
    assert tuer.get("/dashboard").json()["tasks"]["overdue"] == 1

    tuer.post(f"/tasks/{angelegt['id']}/warten", json={"name": "Frau Becker"})

    stand = tuer.get("/dashboard").json()["tasks"]
    assert stand["overdue"] == 0
    assert [t["wartet_auf"] for t in stand["wartend"]] == ["Frau Becker"]
