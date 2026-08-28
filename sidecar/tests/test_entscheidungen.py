"""Entscheidungen und ihre Annahmen.

Der Wert dieser Etappe steckt in einem einzigen Satz, den Icarus vorher nicht
sagen konnte: „Du hast dich damals für X entschieden, weil Y galt. Y gilt
nicht mehr.“

Die wichtigsten Tests sind auch hier die negativen: dass eine Entscheidung mit
stehender Grundlage **nicht** gemeldet wird, dass eine alte Erschütterung den
Morgen **nicht** mehr belegt, und dass dieses Modul **nichts** schreibt.
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
    Status,
)
from icarus_memory import entscheidungen

JETZT = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store() -> SelfModelStore:
    return SelfModelStore(MemoryBackend(), subject_id="t")


def herkunft() -> Provenance:
    return Provenance(source_type=SourceType.USER_STATED, captured_at=JETZT)


def aussage(store, satz: str, *, art: Kind = Kind.STATE, at=None):
    return store.record(satz, art, herkunft(), at=at or JETZT - timedelta(days=60))


def entscheidung(store, satz: str, grundlage=(), *, at=None):
    return store.record(
        satz,
        Kind.DECISION,
        herkunft(),
        derived_from=[a.id for a in grundlage],
        at=at or JETZT - timedelta(days=50),
    )


# -- Der ehrliche Fall ------------------------------------------------------


def test_stehende_grundlage_meldet_nichts(store) -> None:
    grund = aussage(store, "Die Kündigungsfrist beträgt sechs Monate.")
    entscheidung(store, "Wir unterschreiben den Kooperationsvertrag.", [grund])

    assert entscheidungen.erschuettert(store, jetzt=JETZT) == []
    alle = entscheidungen.alle(store, jetzt=JETZT)
    assert len(alle) == 1
    assert alle[0].erschuettert is False


def test_ohne_entscheidungen_ist_die_liste_leer(store) -> None:
    aussage(store, "Irgendetwas gilt.")

    assert entscheidungen.alle(store, jetzt=JETZT) == []


# -- Die Annahme fällt ------------------------------------------------------


def test_ersetzte_annahme_erschuettert_die_entscheidung(store) -> None:
    grund = aussage(store, "Die Kündigungsfrist beträgt sechs Monate.")
    beschluss = entscheidung(store, "Wir unterschreiben den Vertrag.", [grund])

    store.record(
        "Die Kündigungsfrist beträgt drei Monate.",
        Kind.STATE,
        herkunft(),
        supersedes=[grund.id],
        at=JETZT - timedelta(days=2),
    )

    treffer = entscheidungen.erschuettert(store, jetzt=JETZT)

    assert len(treffer) == 1
    assert treffer[0].id == beschluss.id
    wackler = treffer[0].wackler[0]
    assert wackler.annahme.statement == "Die Kündigungsfrist beträgt sechs Monate."
    assert wackler.ersetzt_durch.statement == "Die Kündigungsfrist beträgt drei Monate."


def test_widerrufene_annahme_erschuettert_auch(store) -> None:
    grund = aussage(store, "Das Klinikum trägt die Hälfte der Kosten.")
    entscheidung(store, "Wir kalkulieren mit halben Kosten.", [grund])

    store.retract(grund.id, at=JETZT - timedelta(days=1))

    treffer = entscheidungen.erschuettert(store, jetzt=JETZT)

    assert len(treffer) == 1
    # Widerrufen heißt: es stand nichts an ihrer Stelle.
    assert treffer[0].wackler[0].ersetzt_durch is None


def test_eine_stehende_annahme_rettet_die_entscheidung_nicht(store) -> None:
    """Es genügt, dass **eine** Säule wegfällt."""
    steht = aussage(store, "Die Praxis bleibt am Standort.")
    faellt = aussage(store, "Die Förderung läuft bis Jahresende.")
    entscheidung(store, "Wir stellen jemanden ein.", [steht, faellt])

    store.retract(faellt.id, at=JETZT - timedelta(days=3))

    treffer = entscheidungen.erschuettert(store, jetzt=JETZT)

    assert len(treffer) == 1
    assert [w.annahme.statement for w in treffer[0].wackler] == [
        "Die Förderung läuft bis Jahresende."
    ]
    # Die stehende Säule bleibt sichtbar — sonst sieht es aus, als hätte die
    # Entscheidung nur auf dem gestanden, was weggefallen ist.
    assert len(treffer[0].grundlage) == 2


# -- Was den Morgen nicht belegen darf --------------------------------------


def test_eine_alte_erschuetterung_belegt_den_morgen_nicht(store) -> None:
    """Jeden Morgen zu wiederholen, was vor einem halben Jahr fiel, ist kein
    Rat, sondern ein Vorwurf."""
    grund = aussage(store, "Die Frist beträgt sechs Monate.")
    entscheidung(store, "Wir unterschreiben.", [grund])
    store.record(
        "Die Frist beträgt drei Monate.",
        Kind.STATE,
        herkunft(),
        supersedes=[grund.id],
        at=JETZT - timedelta(days=200),
    )

    assert entscheidungen.erschuettert(store, jetzt=JETZT) == []
    # In der Ansicht steht sie weiterhin.
    assert entscheidungen.alle(store, jetzt=JETZT)[0].erschuettert is True


def test_ein_alter_widerruf_belegt_den_morgen_nicht(store) -> None:
    """Der Fehlerfall ohne Replacement: `at` wurde bisher verworfen und die
    Entscheidung deshalb für immer als frisch behandelt."""
    grund = aussage(store, "Das Klinikum trägt die Hälfte der Kosten.")
    entscheidung(store, "Wir kalkulieren mit halben Kosten.", [grund])
    store.retract(grund.id, at=JETZT - timedelta(days=31))

    assert entscheidungen.erschuettert(store, jetzt=JETZT) == []
    eine = entscheidungen.alle(store, jetzt=JETZT)[0]
    assert eine.erschuettert is True
    assert eine.gefallen_am() == JETZT - timedelta(days=31)


def test_wiederholter_widerruf_erneuert_freshness_nicht(store) -> None:
    grund = aussage(store, "Das Klinikum trägt die Hälfte der Kosten.")
    entscheidung(store, "Wir kalkulieren mit halben Kosten.", [grund])
    erster_widerruf = JETZT - timedelta(days=31)
    store.retract(grund.id, at=erster_widerruf)

    # Derselbe Vorgang kommt etwa durch einen Connector-Retry erneut herein.
    store.retract(grund.id, at=JETZT)

    assert store._require(grund.id).status_changed_at == erster_widerruf
    assert entscheidungen.erschuettert(store, jetzt=JETZT) == []


def test_echter_neuer_statuswechsel_setzt_einen_neuen_zeitpunkt(store) -> None:
    grund = aussage(store, "Das Budget ist freigegeben.")
    gegenstimme = aussage(store, "Das Budget ist noch nicht freigegeben.")
    entscheidung(store, "Wir starten im Januar.", [grund])
    alter_streit = JETZT - timedelta(days=31)
    store.dispute(grund.id, gegenstimme.id, at=alter_streit)
    assert entscheidungen.erschuettert(store, jetzt=JETZT) == []

    # disputed → retracted ist ein anderer fachlicher Status und darf deshalb
    # bewusst ein neues Überprüfungsfenster öffnen.
    store.retract(grund.id, at=JETZT)

    assert store._require(grund.id).status_changed_at == JETZT
    assert len(entscheidungen.erschuettert(store, jetzt=JETZT)) == 1


def test_neue_streitevidence_erneuert_decision_freshness_nicht(store) -> None:
    grund = aussage(store, "Der Standort bleibt Hamburg.")
    erste_evidence = aussage(store, "Der Mietvertrag endet.")
    neue_evidence = aussage(store, "Die Geschäftsführung plant Leipzig.")
    entscheidung(store, "Wir investieren in Hamburg.", [grund])
    erster_streit = JETZT - timedelta(days=31)
    store.dispute(grund.id, erste_evidence.id, at=erster_streit)

    store.dispute(grund.id, neue_evidence.id, at=JETZT)

    # Die zusätzliche Evidence ist verknüpft, aber ihre bewusste spätere
    # Attention gehört in die Evidence-/Attention-Domain und nicht in einen
    # erneuerten Statuszeitpunkt der alten Annahme.
    gespeichert = store._require(grund.id)
    assert gespeichert.status_changed_at == erster_streit
    assert set(gespeichert.disputed_with) == {
        erste_evidence.id,
        neue_evidence.id,
    }
    assert entscheidungen.erschuettert(store, jetzt=JETZT) == []


def test_ein_alter_ablauf_belegt_den_morgen_nicht(store) -> None:
    ablauf = JETZT - timedelta(days=31)
    grund = store.record(
        "Die Förderung läuft bis Juni.",
        Kind.STATE,
        herkunft(),
        expires_at=ablauf,
        at=JETZT - timedelta(days=100),
    )
    entscheidung(store, "Wir stellen jemanden ein.", [grund])
    # Der Ablauf wird beim Lesen materialisiert, fachlich gilt aber sein
    # Ablaufdatum und nicht dieser spätere Lesezeitpunkt.
    store.usable(at=JETZT)

    assert entscheidungen.erschuettert(store, jetzt=JETZT) == []
    assert entscheidungen.alle(store, jetzt=JETZT)[0].gefallen_am() == ablauf


def test_ein_alter_streit_belegt_den_morgen_nicht(store) -> None:
    grund = aussage(store, "Das Budget ist freigegeben.")
    gegenstimme = aussage(store, "Das Budget ist noch nicht freigegeben.")
    entscheidung(store, "Wir starten im Januar.", [grund])
    store.dispute(
        grund.id,
        gegenstimme.id,
        at=JETZT - timedelta(days=31),
    )

    assert entscheidungen.erschuettert(store, jetzt=JETZT) == []
    assert entscheidungen.alle(store, jetzt=JETZT)[0].gefallen_am() == (
        JETZT - timedelta(days=31)
    )


def test_redaction_hat_einen_endlichen_erschuetterungszeitpunkt(store) -> None:
    """Redaction zieht die abhängige Entscheidung aus Datenschutzgründen mit.

    Die Rückschau muss den Zeitpunkt trotzdem korrekt kennen; auch ein alter
    oder unvollständig migrierter Bestand darf daraus kein ewiges „frisch“
    ableiten.
    """
    grund = aussage(store, "Enthält eine zu löschende Annahme.")
    entscheidung(store, "Darauf beruhte eine Entscheidung.", [grund])
    wechsel = JETZT - timedelta(days=31)
    store.redact(grund.id, at=wechsel)

    assert entscheidungen.erschuettert(store, jetzt=JETZT) == []
    eine = entscheidungen.alle(store, jetzt=JETZT)[0]
    assert eine.aussage.status is Status.REDACTED
    assert eine.gefallen_am() == wechsel


def test_dreissig_tage_sind_eingeschlossen_danach_ist_schluss(store) -> None:
    grund = aussage(store, "Die Frist steht.")
    entscheidung(store, "Wir unterschreiben.", [grund])
    store.retract(grund.id, at=JETZT - timedelta(days=30))

    assert len(entscheidungen.erschuettert(store, jetzt=JETZT)) == 1
    assert entscheidungen.erschuettert(
        store, jetzt=JETZT + timedelta(microseconds=1)
    ) == []


def test_legacy_widerruf_ohne_zeitpunkt_ist_nicht_unbegrenzt_frisch(store) -> None:
    """Bestehende JSON-Dokumente besitzen `status_changed_at` nicht.

    Ihr ehrlicher Fallback ist endlich: die Aufnahmezeit der Annahme. So geht
    kein alter Bestand kaputt und dieselbe Warnung erscheint nicht für immer.
    """
    grund = aussage(
        store,
        "Die Lieferung kommt rechtzeitig.",
        at=JETZT - timedelta(days=100),
    )
    entscheidung(store, "Wir planen den Start.", [grund])
    store.retract(grund.id, at=JETZT - timedelta(days=1))
    legacy = store._backend.get(grund.id)
    assert legacy is not None
    legacy.status_changed_at = None
    store._backend.put(legacy)

    assert entscheidungen.erschuettert(store, jetzt=JETZT) == []
    eine = entscheidungen.alle(store, jetzt=JETZT)[0]
    assert eine.gefallen_am() == JETZT - timedelta(days=100)
    assert eine.wackler[0].annahme.status is Status.RETRACTED


def test_eine_widerrufene_entscheidung_belegt_den_morgen_nicht(store) -> None:
    grund = aussage(store, "Die Frist beträgt sechs Monate.")
    beschluss = entscheidung(store, "Wir unterschreiben.", [grund])
    store.record(
        "Die Frist beträgt drei Monate.",
        Kind.STATE,
        herkunft(),
        supersedes=[grund.id],
        at=JETZT - timedelta(days=2),
    )
    store.retract(beschluss.id, at=JETZT)

    assert entscheidungen.erschuettert(store, jetzt=JETZT) == []


def test_ohne_benannte_grundlage_sagt_die_ansicht_das(store) -> None:
    """Nicht jede Entscheidung hat eine benennbare Annahme — aber dann kann
    Icarus auch nicht merken, wenn sie wegfällt."""
    entscheidung(store, "Wir machen es so.")

    eine = entscheidungen.alle(store, jetzt=JETZT)[0]

    assert eine.ohne_grundlage is True
    assert eine.erschuettert is False


# -- Es wird nichts geschrieben ---------------------------------------------


def test_das_modul_schreibt_nichts(store) -> None:
    """Eine gefallene Annahme macht eine Entscheidung nicht falsch, nur
    prüfenswert. Wer sie revidieren will, tut das selbst."""
    grund = aussage(store, "Die Frist beträgt sechs Monate.")
    beschluss = entscheidung(store, "Wir unterschreiben.", [grund])
    store.record(
        "Die Frist beträgt drei Monate.",
        Kind.STATE,
        herkunft(),
        supersedes=[grund.id],
        at=JETZT - timedelta(days=2),
    )
    vorher = len(store.alles())

    entscheidungen.erschuettert(store, jetzt=JETZT)
    entscheidungen.alle(store, jetzt=JETZT)

    assert len(store.alles()) == vorher
    geblieben = {a.id: a for a in store.alles()}
    assert geblieben[beschluss.id].status.value == "active"


# -- Reihenfolge ------------------------------------------------------------


def test_erschuettertes_steht_oben(store) -> None:
    ruhig = aussage(store, "Alles beim Alten.")
    entscheidung(store, "Alte Entscheidung, Grundlage steht.", [ruhig],
                 at=JETZT - timedelta(days=100))
    faellt = aussage(store, "Die Förderung läuft.")
    entscheidung(store, "Neue Entscheidung, Grundlage fällt.", [faellt],
                 at=JETZT - timedelta(days=90))
    store.retract(faellt.id, at=JETZT - timedelta(days=1))

    alle = entscheidungen.alle(store, jetzt=JETZT)

    assert alle[0].satz == "Neue Entscheidung, Grundlage fällt."


# -- Über die Tür -----------------------------------------------------------


@pytest.fixture()
def tuer(tmp_path):
    from fastapi.testclient import TestClient

    from icarus_memory.server import create_app

    app = create_app(SelfModelStore(MemoryBackend(), subject_id="t"))
    return TestClient(app)


def test_entscheidung_festhalten_und_wiederfinden(tuer) -> None:
    grund = tuer.post("/assertions", json={
        "statement": "Die Kündigungsfrist beträgt sechs Monate.",
        "kind": "state",
        "provenance": {"source_type": "user_stated"},
    }).json()

    angelegt = tuer.post("/decisions", json={
        "statement": "Wir unterschreiben den Kooperationsvertrag.",
        "derived_from": [grund["id"]],
    })

    assert angelegt.status_code == 201
    assert angelegt.json()["erschuettert"] is False
    assert [g["satz"] for g in angelegt.json()["grundlage"]] == [
        "Die Kündigungsfrist beträgt sechs Monate."
    ]

    liste = tuer.get("/decisions").json()
    assert liste["erschuettert"] == 0
    assert len(liste["items"]) == 1


def test_eine_unbekannte_grundlage_ist_ein_fehler_mit_grund(tuer) -> None:
    """Sonst stünde eine Entscheidung auf einem Verweis ins Leere, und
    niemand könnte je merken, dass die Kette gebrochen ist."""
    antwort = tuer.post("/decisions", json={
        "statement": "Wir unterschreiben.",
        "derived_from": ["a-gibtsnicht"],
    })

    assert antwort.status_code == 409
    assert "a-gibtsnicht" in antwort.json()["detail"]


def test_das_dashboard_traegt_die_erschuetterung(tuer) -> None:
    grund = tuer.post("/assertions", json={
        "statement": "Die Förderung läuft bis Jahresende.",
        "kind": "state",
        "provenance": {"source_type": "user_stated"},
    }).json()
    tuer.post("/decisions", json={
        "statement": "Wir stellen jemanden ein.",
        "derived_from": [grund["id"]],
    })
    assert tuer.get("/dashboard").json()["decisions"]["erschuettert"] == []

    tuer.post(f"/assertions/{grund['id']}/retract")

    stand = tuer.get("/dashboard").json()
    wanken = stand["decisions"]["erschuettert"]
    assert [e["satz"] for e in wanken] == ["Wir stellen jemanden ein."]

    satz = stand["briefing"]["punkte"][0]["text"]
    # Ohne Schlusspunkt: das Zitat steht mitten im Satz.
    assert "„Wir stellen jemanden ein“" in satz
    assert "Die Förderung läuft bis Jahresende." in satz
    assert "Das gilt nicht mehr." in satz


def test_das_briefing_nennt_was_an_die_stelle_getreten_ist(tuer) -> None:
    grund = tuer.post("/assertions", json={
        "statement": "Die Frist beträgt sechs Monate.",
        "kind": "state",
        "provenance": {"source_type": "user_stated"},
    }).json()
    tuer.post("/decisions", json={
        "statement": "Wir unterschreiben.",
        "derived_from": [grund["id"]],
    })
    tuer.post("/assertions", json={
        "statement": "Die Frist beträgt drei Monate.",
        "kind": "state",
        "provenance": {"source_type": "user_stated"},
        "supersedes": [grund["id"]],
    })

    satz = tuer.get("/dashboard").json()["briefing"]["punkte"][0]["text"]

    assert "Jetzt gilt: „Die Frist beträgt drei Monate.“" in satz
    # Kein doppeltes Satzzeichen — „…“. liest sich wie ein Stolpern.
    assert "“." not in satz


# -- Widerruf gegen Löschung ------------------------------------------------
#
# Zwei Wege, eine Annahme fallen zu lassen, und sie tun bewusst
# Verschiedenes. Diese beiden Tests halten den Unterschied fest — er ist
# leicht zu verwischen und teuer, wenn er verwischt.


def test_widerruf_laesst_die_entscheidung_stehen(tuer) -> None:
    """„Das stimmte nie“ erschüttert die Entscheidung. Es löscht sie nicht."""
    grund = tuer.post("/assertions", json={
        "statement": "Die Förderung läuft bis Jahresende.",
        "kind": "state",
        "provenance": {"source_type": "user_stated"},
    }).json()
    tuer.post("/decisions", json={
        "statement": "Wir stellen jemanden ein.",
        "derived_from": [grund["id"]],
    })

    tuer.post(f"/assertions/{grund['id']}/retract")

    items = tuer.get("/decisions").json()["items"]
    assert [e["satz"] for e in items] == ["Wir stellen jemanden ein."]
    assert items[0]["erschuettert"] is True
    assert items[0]["status"] == "active"


def test_loeschung_reisst_die_entscheidung_mit(tuer) -> None:
    """Und das ist Absicht.

    Wird eine Quelle auf Wunsch gelöscht, darf nichts stehen bleiben, was
    daraus abgeleitet wurde — sonst überlebt die Information ihre eigene
    Löschung. Eine Entscheidung ist davon nicht ausgenommen, auch wenn ihr
    Satz die Annahme nicht wörtlich enthält. Im Zweifel zu viel löschen ist
    die einzig vertretbare Richtung.
    """
    grund = tuer.post("/assertions", json={
        "statement": "Die Förderung läuft bis Jahresende.",
        "kind": "state",
        "provenance": {"source_type": "user_stated"},
    }).json()
    tuer.post("/decisions", json={
        "statement": "Wir stellen jemanden ein.",
        "derived_from": [grund["id"]],
    })

    tuer.post(f"/assertions/{grund['id']}/redact", json={"reason": "user_request"})

    items = tuer.get("/decisions").json()["items"]
    assert items[0]["status"] == "redacted"
    assert items[0]["satz"] == "Entfernt auf Wunsch der Person."
