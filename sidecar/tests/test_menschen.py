"""Menschen — eine Ableitung, und die Zusagen, die dabei halten müssen.

Vier davon sind es wert, einzeln geprüft zu werden:

1. **Es wird nicht geraten.** Gleicher Name nach `strip()`, ohne Rücksicht auf
   Groß- und Kleinschreibung — sonst nichts. Zwei Menschen in einem Eintrag
   sind ein Schaden, den niemand bemerkt.
2. **Nichts Überholtes.** Die Aussagen zu einer Person kommen über `recall()`.
   Was ersetzt oder widerrufen ist, darf auf einer Personenseite nicht als
   geltende Wahrheit stehen.
3. **Keine nackte Zahl.** Wann zuletzt Kontakt war, steht in Worten da.
4. **Nichts wird gespeichert.** Es gibt keine Personentabelle und keinen Weg,
   hier etwas anzulegen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from icarus_memory import personen
from icarus_memory.agent import Agent
from icarus_memory.audit import AuditLog
from icarus_memory.backends import MemoryBackend
from icarus_memory.episodes import EpisodeKind, EpisodeStore
from icarus_memory.model import Kind, Provenance, SourceType
from icarus_memory.policy import Policy
from icarus_memory.server import create_app
from icarus_memory.store import SelfModelStore
from icarus_memory.tasks import TaskStore
from icarus_memory.tools import build_registry
from icarus_memory.workspace import WorkspaceStore

JETZT = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def episodes(tmp_path: Path) -> EpisodeStore:
    return EpisodeStore(tmp_path / "episodes.sqlite3")


@pytest.fixture
def tasks(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.sqlite3")


@pytest.fixture
def store() -> SelfModelStore:
    return SelfModelStore(MemoryBackend(), subject_id="t")


def _prov(art: SourceType = SourceType.DOCUMENT) -> Provenance:
    return Provenance(source_type=art, source_ref="notiz.md", captured_at=JETZT)


def _episode(
    episodes: EpisodeStore,
    body: str,
    beteiligte: list[str],
    *,
    vor_tagen: int = 0,
    tags: list[str] | None = None,
    herkunft: SourceType = SourceType.DOCUMENT,
    project_id: str | None = None,
):
    return episodes.record(
        EpisodeKind.MESSAGE,
        f"Notiz {body[:12]}",
        body,
        _prov(herkunft),
        occurred_at=JETZT - timedelta(days=vor_tagen),
        participants=beteiligte,
        tags=tags,
        project_id=project_id,
    )


def _alle(episodes, **kw) -> list[personen.Person]:
    return personen.alle(episodes=episodes, jetzt=JETZT, **kw)


# -- Ableiten statt anlegen --------------------------------------------------


def test_wer_in_einer_episode_vorkommt_ist_da(episodes: EpisodeStore) -> None:
    """Kein Anlegen-Knopf, kein Formular. Das Rohmaterial genügt."""
    _episode(episodes, "Besprechung zum Vertrag.", ["Dr. Meier"])

    menschen = _alle(episodes)

    assert [p.name for p in menschen] == ["Dr. Meier"]
    assert menschen[0].episoden_anzahl == 1


def test_ohne_rohmaterial_gibt_es_niemanden(episodes: EpisodeStore) -> None:
    """Eine erfundene Person wäre schlimmer als eine leere Liste."""
    assert _alle(episodes) == []


def test_leere_namen_werden_nicht_zu_menschen(episodes: EpisodeStore) -> None:
    """Ein Leerzeichen in der Beteiligtenliste ist kein Mensch."""
    _episode(episodes, "Etwas ohne Namen.", ["", "   "])

    assert _alle(episodes) == []


# -- Zusammenführen, ohne zu raten -------------------------------------------


def test_gleicher_name_in_anderer_schreibweise_ist_dieselbe_person(
    episodes: EpisodeStore,
) -> None:
    """`strip()` und Groß-/Kleinschreibung — genau diese beiden."""
    _episode(episodes, "Erstens.", ["Dr. Meier"])
    _episode(episodes, "Zweitens.", ["  dr. meier  "])
    _episode(episodes, "Drittens.", ["DR. MEIER"])

    menschen = _alle(episodes)

    assert len(menschen) == 1
    assert menschen[0].episoden_anzahl == 3


def test_die_haeufigste_schreibweise_gewinnt(episodes: EpisodeStore) -> None:
    """Sonst hinge der angezeigte Name daran, welche Datei zuerst gelesen wurde."""
    _episode(episodes, "Erstens.", ["Dr. Meier"])
    _episode(episodes, "Zweitens.", ["Dr. Meier"])
    _episode(episodes, "Drittens.", ["dr. meier"])

    assert _alle(episodes)[0].name == "Dr. Meier"


def test_zwei_aehnliche_namen_bleiben_zwei_menschen(episodes: EpisodeStore) -> None:
    """Die wichtigste Zusage dieses Moduls.

    „Meier“ und „Dr. Meier“ sind vermutlich derselbe Mensch. Vermutlich reicht
    nicht: Wer hier zusammenlegt, schreibt irgendwann die Zusage des einen dem
    anderen zu, und niemand merkt es.
    """
    _episode(episodes, "Erstens.", ["Meier"])
    _episode(episodes, "Zweitens.", ["Dr. Meier"])
    _episode(episodes, "Drittens.", ["Thomas Meier"])

    assert len(_alle(episodes)) == 3


# -- Reihenfolge -------------------------------------------------------------


def test_juengster_kontakt_zuerst(episodes: EpisodeStore) -> None:
    """Wen ich gestern gesprochen habe, ist wahrscheinlicher gemeint."""
    _episode(episodes, "Alt.", ["Frau Alt"], vor_tagen=300)
    _episode(episodes, "Neu.", ["Frau Neu"], vor_tagen=1)
    _episode(episodes, "Mittel.", ["Frau Mittel"], vor_tagen=40)

    assert [p.name for p in _alle(episodes)] == ["Frau Neu", "Frau Mittel", "Frau Alt"]


# -- Keine nackte Zahl -------------------------------------------------------


@pytest.mark.parametrize(
    ("vor_tagen", "erwartet"),
    [
        (0, "heute"),
        (1, "gestern"),
        (2, "vorgestern"),
        (3, "vor drei Tagen"),
        (6, "vor sechs Tagen"),
        (17, "am 7. August"),
    ],
)
def test_letzter_kontakt_steht_in_worten(
    episodes: EpisodeStore, vor_tagen: int, erwartet: str
) -> None:
    """„3“ zwingt den Leser zu rechnen. Gerechnet wird hier nicht."""
    _episode(episodes, "Kontakt.", ["Frau Schulz"], vor_tagen=vor_tagen)

    assert _alle(episodes)[0].kontakt_text == erwartet


def test_ein_alter_kontakt_bekommt_sein_jahr(episodes: EpisodeStore) -> None:
    """Ohne Jahr sähe der 7. August 2024 aus wie der von diesem Jahr."""
    _episode(episodes, "Lang her.", ["Frau Schulz"], vor_tagen=400)

    assert _alle(episodes)[0].kontakt_text == "am 20. Juli 2025"


def test_kontakt_text_ist_nie_eine_nackte_zahl(episodes: EpisodeStore) -> None:
    """Die Zusage als Ganzes, über alle Abstände hinweg.

    Geprüft wird auf gezählte Zeiteinheiten — „vor 29 Tagen“, „vor 3 Wochen“.
    Ein Datum darf eine Ziffer tragen („am 7. August“), eine Spanne nicht: Wer
    „vor 29 Tagen“ liest, muss selbst nachrechnen, wann das war.
    """
    gezaehlt = re.compile(
        r"\d+\s*(Tag|Tage|Tagen|Woche|Wochen|Monat|Monate|Monaten|Jahr|Jahre|Jahren)\b"
    )

    for tage in range(0, 400, 7):
        _episode(episodes, f"Kontakt {tage}.", [f"Person {tage}"], vor_tagen=tage)

    for mensch in _alle(episodes):
        assert mensch.kontakt_text, "ohne Text wäre die Zeile leer"
        assert not gezaehlt.search(mensch.kontakt_text), mensch.kontakt_text


# -- Themen ------------------------------------------------------------------


def test_hoechstens_drei_themen(episodes: EpisodeStore) -> None:
    """Mehr ist keine Auskunft mehr, sondern eine Wolke."""
    _episode(episodes, "Viel.", ["Frau Schulz"],
             tags=["Vertrag", "Praxis", "Umzug", "Abrechnung", "Technik"])

    assert len(_alle(episodes)[0].themen) == personen.MAX_THEMEN


def test_das_haeufigste_thema_steht_vorn(episodes: EpisodeStore) -> None:
    _episode(episodes, "Erstens.", ["Frau Schulz"], tags=["Vertrag", "Praxis"])
    _episode(episodes, "Zweitens.", ["Frau Schulz"], tags=["Vertrag"])
    _episode(episodes, "Drittens.", ["Frau Schulz"], tags=["Vertrag"])

    assert _alle(episodes)[0].themen[0] == "Vertrag"


def test_ein_projekt_erscheint_mit_namen_nicht_mit_kennung(
    episodes: EpisodeStore, tmp_path: Path
) -> None:
    """„p-3f9a1c“ ist kein Thema, das ein Mensch wiedererkennt."""
    workspace = WorkspaceStore(tmp_path / "workspace.sqlite3")
    projekt = workspace.add_project("Praxisumzug", _prov())
    _episode(episodes, "Dazu.", ["Frau Schulz"], project_id=projekt.id)

    themen = _alle(episodes, workspace=workspace)[0].themen

    assert themen == ["Praxisumzug"]
    assert projekt.id not in themen


# -- Offene Aufgaben ---------------------------------------------------------


def test_eine_aufgabe_mit_dem_namen_im_titel_wartet_auf_die_person(
    episodes: EpisodeStore, tasks: TaskStore
) -> None:
    """Solange keine Zuordnung im Datenmodell steht, ist der Titel alles."""
    _episode(episodes, "Gespräch.", ["Dr. Brandt"])
    tasks.add("Rückfrage an Dr. Brandt", _prov())
    tasks.add("Steuererklärung", _prov())

    mensch = _alle(episodes, tasks=tasks)[0]

    assert [a["title"] for a in mensch.offene_aufgaben] == ["Rückfrage an Dr. Brandt"]


def test_erledigtes_wartet_nicht_mehr(episodes: EpisodeStore, tasks: TaskStore) -> None:
    _episode(episodes, "Gespräch.", ["Dr. Brandt"])
    aufgabe = tasks.add("Rückfrage an Dr. Brandt", _prov())
    tasks.complete(aufgabe.id)

    assert _alle(episodes, tasks=tasks)[0].offene_aufgaben == []


def test_ein_spaeteres_feld_schlaegt_den_titel() -> None:
    """Die Vorbereitung, die `tasks.py` unangetastet lässt.

    Sobald eine Aufgabe weiß, auf wen sie wartet, ist das eine **Angabe**. Der
    Titel bleibt eine Vermutung, und eine Vermutung darf eine Angabe nicht
    überstimmen — auch nicht, wenn zufällig ein Name darin vorkommt.
    """

    @dataclass
    class KuenftigeAufgabe:
        title: str
        wartet_auf: str | None = None

    zugeordnet = KuenftigeAufgabe("Termin mit Frau Schulz", wartet_auf="Dr. Brandt")

    assert personen.wartet_auf(zugeordnet, "Dr. Brandt")
    assert not personen.wartet_auf(zugeordnet, "Frau Schulz")

    # Ohne gesetztes Feld bleibt es beim Titel.
    offen = KuenftigeAufgabe("Termin mit Frau Schulz")
    assert personen.wartet_auf(offen, "Frau Schulz")


# -- Aussagen: nichts Überholtes ---------------------------------------------


def test_aussagen_zur_person_kommen_aus_dem_bestand(
    episodes: EpisodeStore, store: SelfModelStore
) -> None:
    _episode(episodes, "Gespräch.", ["Dr. Brandt"])
    store.record("Dr. Brandt entscheidet über die Ablage.", Kind.STATE, _prov())

    mensch = _alle(episodes, store=store)[0]

    assert [a["statement"] for a in mensch.aussagen] == [
        "Dr. Brandt entscheidet über die Ablage."
    ]


def test_ersetztes_steht_nicht_auf_der_personenseite(
    episodes: EpisodeStore, store: SelfModelStore
) -> None:
    """Der Fall, der `recall()` von `search()` unterscheidet.

    Beim Widerruf verschwindet der Wortlaut ohnehin. Beim **Ersetzen** bleibt
    er für die Geschichte erhalten — und genau hier entscheidet sich, ob eine
    überholte Behauptung über einen Menschen als Gegenwart auftritt.
    """
    _episode(episodes, "Gespräch.", ["Dr. Brandt"])
    alt = store.record("Dr. Brandt arbeitet in Karlsruhe.", Kind.STATE, _prov())
    store.record(
        "Dr. Brandt arbeitet in Freiburg.", Kind.STATE, _prov(), supersedes=[alt.id]
    )

    saetze = [a["statement"] for a in _alle(episodes, store=store)[0].aussagen]

    assert saetze == ["Dr. Brandt arbeitet in Freiburg."]
    assert not any("Karlsruhe" in s for s in saetze)


def test_widerrufenes_steht_nicht_auf_der_personenseite(
    episodes: EpisodeStore, store: SelfModelStore
) -> None:
    _episode(episodes, "Gespräch.", ["Dr. Brandt"])
    aussage = store.record("Dr. Brandt ist krank.", Kind.STATE, _prov())
    store.redact(aussage.id)

    assert _alle(episodes, store=store)[0].aussagen == []


def test_eine_aussage_aus_einer_mail_bleibt_als_fremd_erkennbar(
    episodes: EpisodeStore, store: SelfModelStore
) -> None:
    """Herkunft ist nicht Vertrauenswürdigkeit — auch auf einer Personenseite."""
    _episode(episodes, "Gespräch.", ["Dr. Brandt"])
    store.record(
        "Dr. Brandt will fünf Jahre Laufzeit.", Kind.STATE,
        Provenance(source_type=SourceType.EMAIL, source_ref="uid-4711"),
    )

    assert _alle(episodes, store=store)[0].aussagen[0]["fremd"]


# -- Fremde Herkunft ---------------------------------------------------------


def test_wer_nur_aus_fremdem_material_bekannt_ist_wird_gekennzeichnet(
    episodes: EpisodeStore,
) -> None:
    """Der Name selbst ist dann eine fremde Behauptung: Eine Mail hat ihn genannt."""
    _episode(episodes, "Von außen.", ["Dr. Brandt"], herkunft=SourceType.EMAIL)

    assert _alle(episodes)[0].nur_von_aussen()


def test_wovon_der_nutzer_selbst_erzaehlt_hat_ist_nicht_fremd(
    episodes: EpisodeStore,
) -> None:
    """Was alles markiert, markiert nichts."""
    _episode(episodes, "Von außen.", ["Dr. Brandt"], herkunft=SourceType.EMAIL)
    _episode(episodes, "Selbst gesagt.", ["Dr. Brandt"], herkunft=SourceType.USER_STATED)

    assert not _alle(episodes)[0].nur_von_aussen()


# -- Eine einzelne Person ----------------------------------------------------


def test_eine_findet_unabhaengig_von_der_schreibweise(episodes: EpisodeStore) -> None:
    _episode(episodes, "Gespräch.", ["Dr. Meier"])

    assert personen.eine("  dr. MEIER ", episodes=episodes, jetzt=JETZT).name == "Dr. Meier"


def test_eine_unbekannte_person_gibt_nichts(episodes: EpisodeStore) -> None:
    """Eine leere Person sähe aus wie eine Auskunft und wäre keine."""
    _episode(episodes, "Gespräch.", ["Dr. Meier"])

    assert personen.eine("Frau Schulz", episodes=episodes, jetzt=JETZT) is None
    assert personen.eine("   ", episodes=episodes, jetzt=JETZT) is None


# -- Über HTTP ---------------------------------------------------------------


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


def test_people_liefert_die_liste(client: TestClient) -> None:
    client.post("/episodes", json={
        "kind": "message", "title": "Besprechung",
        "body": "Wir haben über den Vertrag gesprochen.",
        "participants": ["Dr. Meier"], "tags": ["Vertrag"],
    })

    menschen = client.get("/people").json()

    assert [m["name"] for m in menschen] == ["Dr. Meier"]
    assert menschen[0]["themen"] == ["Vertrag"]
    assert menschen[0]["kontakt_text"] == "heute"


def test_ein_name_mit_leerzeichen_und_umlaut_ist_erreichbar(client: TestClient) -> None:
    """Der Pfad ist kodiert; die Anwendung sieht wieder den Klarnamen."""
    client.post("/episodes", json={
        "kind": "message", "title": "Besprechung", "body": "Kurz gesprochen.",
        "participants": ["Jörg Müller-Groß"],
    })

    antwort = client.get("/people/J%C3%B6rg%20M%C3%BCller-Gro%C3%9F")

    assert antwort.status_code == 200
    assert antwort.json()["name"] == "Jörg Müller-Groß"


def test_ein_unbekannter_name_antwortet_verstaendlich(client: TestClient) -> None:
    """Mit Grund, nicht mit einem Fehlercode allein."""
    antwort = client.get("/people/Niemand")

    assert antwort.status_code == 404
    assert "Niemand" in antwort.json()["detail"]


def test_die_personenansicht_verlangt_das_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wer die Menschen um jemanden herum kennt, weiß viel über ihn."""
    monkeypatch.setenv("ICARUS_SIDECAR_TOKEN", "geheim")
    c = TestClient(create_app(SelfModelStore(MemoryBackend(), subject_id="t")))

    assert c.get("/people").status_code == 401
    assert c.get("/people/Meier").status_code == 401
    assert c.get("/people", headers={"x-icarus-token": "geheim"}).status_code == 200


def test_es_gibt_keinen_weg_eine_person_anzulegen(client: TestClient) -> None:
    """Menschen werden abgeleitet. Eine zweite Ablage neben dem Bestand
    müsste gepflegt werden — genau die Arbeit, die entfallen soll."""
    assert client.post("/people", json={"name": "Erfunden"}).status_code == 405


# -- In der Suche ------------------------------------------------------------


def test_die_suche_kennt_menschen(client: TestClient) -> None:
    client.post("/episodes", json={
        "kind": "message", "title": "Besprechung", "body": "Vertrag besprochen.",
        "participants": ["Dr. Brandt"], "tags": ["Vertrag"],
    })

    ergebnis = client.get("/suche", params={"q": "brandt"}).json()
    gruppe = [g for g in ergebnis["gruppen"] if g["art"] == "person"]

    assert gruppe, "„Brandt“ muss den Menschen finden, nicht nur die Notiz"
    assert gruppe[0]["beschriftung"] == "Menschen"
    assert gruppe[0]["treffer"][0]["titel"] == "Dr. Brandt"
    assert gruppe[0]["treffer"][0]["ziel"] == "people"
    assert gruppe[0]["treffer"][0]["zeile"] == "zuletzt heute · Vertrag"
    # Der Treffer trägt den Namen als Verweis — die Oberfläche kann damit
    # direkt die Person öffnen, statt eine Liste zum Weitersuchen zu zeigen.
    assert gruppe[0]["treffer"][0]["ref"] == "Dr. Brandt"


def test_menschen_stehen_vor_dem_wissen(client: TestClient) -> None:
    """Ein Name ist meistens ein Arbeitszusammenhang, erst dann ein Fakt."""
    client.post("/episodes", json={
        "kind": "message", "title": "Besprechung", "body": "Kurz gesprochen.",
        "participants": ["Dr. Brandt"],
    })
    client.post("/assertions", json={
        "statement": "Dr. Brandt entscheidet über die Ablage.",
        "kind": "state",
        "provenance": {"source_type": "user_stated"},
    })

    arten = [g["art"] for g in client.get("/suche", params={"q": "brandt"}).json()["gruppen"]]

    assert arten.index("person") < arten.index("aussage")


def test_ein_mensch_nur_aus_fremdem_material_kommt_gekennzeichnet_an(
    client: TestClient,
) -> None:
    # Direkt in die Ablage: Was über `POST /episodes` hereinkommt, hat der
    # Nutzer selbst eingetragen und ist deshalb nie fremd. Fremdes Material
    # kommt über die Aufnahme und über den Posteingang.
    client.app.state.episodes.record(
        EpisodeKind.DOCUMENT, "Angebot", "Wir schlagen fünf Jahre vor.",
        Provenance(source_type=SourceType.DOCUMENT, source_ref="angebot.md"),
        participants=["Dr. Brandt"],
    )

    ergebnis = client.get("/suche", params={"q": "brandt"}).json()
    gruppe = [g for g in ergebnis["gruppen"] if g["art"] == "person"][0]

    assert gruppe["treffer"][0]["fremd"]


def test_eine_klemmende_personenebene_kippt_die_suche_nicht(client: TestClient) -> None:
    """Wie jede andere Schicht: Eine Suche, die ganz ausfällt, ist nutzlos."""
    client.post("/tasks", json={"title": "Vertrag prüfen"})

    class Kaputt:
        def all_episodes(self, *a, **k):
            raise RuntimeError("Datenbank weg")

        def search(self, *a, **k):
            raise RuntimeError("Datenbank weg")

    client.app.state.episodes = Kaputt()

    ergebnis = client.get("/suche", params={"q": "vertrag"}).json()

    assert ergebnis["gesamt"] >= 1
    assert any(g["art"] == "aufgabe" for g in ergebnis["gruppen"])
