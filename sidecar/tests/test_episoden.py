"""Tests der Mittelfristschicht.

Die Schicht steht und fällt mit drei Zusicherungen: Digest, Entdopplung und
Zustand statt Löschen. Alles andere ist Ablage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from icarus_memory.episodes import (
    EpisodeError,
    EpisodeKind,
    EpisodeState,
    EpisodeStore,
    digest_of,
)
from icarus_memory.model import Provenance, SourceType, now


@pytest.fixture
def store(tmp_path) -> EpisodeStore:
    return EpisodeStore(tmp_path / "episodes.sqlite3")


def _prov(ref: str = "mail:1") -> Provenance:
    return Provenance(source_type=SourceType.EMAIL, source_ref=ref, captured_at=now())


def _record(store: EpisodeStore, title: str, body: str, **kw):
    return store.record(EpisodeKind.MESSAGE, title, body, _prov(), **kw)


# -- Digest -----------------------------------------------------------------


def test_jede_episode_traegt_einen_digest(store: EpisodeStore) -> None:
    """Ohne Digest ist keine Neuprüfung vor einer folgenreichen Aktion möglich.

    Der erste offene Punkt aus dem Gedächtnis-Kontrakt.
    """
    episode, _ = _record(store, "Angebot", "Wir liefern bis Freitag.")

    assert episode.digest.startswith("sha256:")
    assert episode.digest == digest_of("Wir liefern bis Freitag.")
    assert store.get(episode.id).digest == episode.digest


def test_geaenderte_quelle_hat_einen_anderen_digest() -> None:
    """Genau daran soll später erkennbar sein, dass sich etwas verschoben hat."""
    assert digest_of("Wir liefern bis Freitag.") != digest_of("Wir liefern bis Montag.")


# -- Entdopplung ------------------------------------------------------------


def test_derselbe_inhalt_wird_nicht_zweimal_aufgenommen(store: EpisodeStore) -> None:
    """Ohne das ist kein Prozess denkbar, der dauerhaft mitläuft."""
    erste, neu1 = _record(store, "Angebot", "Wir liefern bis Freitag.")
    zweite, neu2 = _record(store, "Angebot (Kopie)", "Wir liefern bis Freitag.")

    assert neu1 is True
    assert neu2 is False
    assert zweite.id == erste.id
    assert len(store.all_episodes()) == 1


def test_wiederholte_aufnahme_erhaelt_zustand_und_herleitung(
    store: EpisodeStore,
) -> None:
    """Der bestehende Eintrag bleibt stehen — sonst käme die Episode erneut in
    die Verdichtung, und der Nutzer bekäme Vorschläge vorgelegt, die er längst
    entschieden hat."""
    episode, _ = _record(store, "Angebot", "Wir liefern bis Freitag.")
    store.mark_consolidated(episode.id, produced=["a-1"])

    wieder, neu = _record(store, "Angebot", "Wir liefern bis Freitag.")

    assert neu is False
    assert wieder.state is EpisodeState.CONSOLIDATED
    assert wieder.produced == ["a-1"]


def test_entdopplung_haengt_am_index_nicht_am_code(store: EpisodeStore) -> None:
    """Ein zweiter Aufnahmeweg könnte die Prüfung vergessen; der Index nicht."""
    import sqlite3

    _record(store, "Angebot", "Wir liefern bis Freitag.")
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(  # noqa: SLF001 - genau die Umgehung ist der Test
            "INSERT INTO episodes (id, digest, kind, state, recorded_at, "
            "title, body, document) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("e-vorbei", digest_of("Wir liefern bis Freitag."), "message", "new",
             now().isoformat(), "Am Code vorbei", "egal", "{}"),
        )


# -- Zustand ----------------------------------------------------------------


def test_verdichten_haelt_den_rueckweg_fest(store: EpisodeStore) -> None:
    """Ohne `produced` wäre die Verdichtung eine Blackbox, die Behauptungen
    erzeugt."""
    episode, _ = _record(store, "Angebot", "Wir liefern bis Freitag.")

    store.mark_consolidated(episode.id, produced=["a-1", "a-2"])

    danach = store.get(episode.id)
    assert danach.state is EpisodeState.CONSOLIDATED
    assert danach.produced == ["a-1", "a-2"]
    assert danach.consolidated_at is not None


def test_zweiter_lauf_ergaenzt_die_herleitung(store: EpisodeStore) -> None:
    episode, _ = _record(store, "Angebot", "Wir liefern bis Freitag.")
    store.mark_consolidated(episode.id, produced=["a-1"])
    store.mark_consolidated(episode.id, produced=["a-1", "a-2"])

    assert store.get(episode.id).produced == ["a-1", "a-2"]


def test_verworfenes_kommt_nicht_wieder(store: EpisodeStore) -> None:
    """Eigener Zustand statt Löschen: Sonst legt die Verdichtung beim nächsten
    Lauf denselben nutzlosen Vorschlag erneut vor."""
    episode, _ = _record(store, "Newsletter", "Jetzt 20 % sparen!")

    store.ignore(episode.id)

    assert store.pending() == []
    assert store.get(episode.id).state is EpisodeState.IGNORED


def test_unbearbeitetes_wird_nie_archiviert(store: EpisodeStore) -> None:
    """Stilles Vergessen genau des Materials, das noch Arbeit erzeugen sollte —
    das darf nicht passieren, egal wie alt es ist."""
    alt = now() - timedelta(days=400)
    unbearbeitet, _ = _record(store, "Alt und ungesehen", "x", occurred_at=alt)
    gesehen, _ = _record(store, "Alt und gesehen", "y", occurred_at=alt)
    store.mark_consolidated(gesehen.id)

    archiviert = store.archive_before(now() - timedelta(days=90))

    assert archiviert == 1
    assert store.get(unbearbeitet.id).state is EpisodeState.NEW
    assert store.get(gesehen.id).state is EpisodeState.ARCHIVED


def test_unbekannte_episode_wirft(store: EpisodeStore) -> None:
    with pytest.raises(EpisodeError):
        store.get("e-gibtesnicht")


# -- Zeit -------------------------------------------------------------------


def test_geschehen_und_erfahren_sind_getrennt(store: EpisodeStore) -> None:
    """Ein heute importierter Vault enthält Notizen von vor drei Jahren.

    Ohne die Trennung wäre der ganze Bestand nach einem Import gleich alt, und
    die Alterungsurteile aus currency.py wären wertlos.
    """
    damals = datetime(2023, 5, 17, tzinfo=timezone.utc)
    episode, _ = _record(store, "Alte Notiz", "Von damals.", occurred_at=damals)

    assert episode.occurred_at == damals
    assert episode.recorded_at > damals
    assert episode.reference_time() == damals


def test_ohne_geschehenszeit_zaehlt_die_aufnahme(store: EpisodeStore) -> None:
    episode, _ = _record(store, "Ohne Datum", "Kein Zeitpunkt bekannt.")
    assert episode.reference_time() == episode.recorded_at


def test_offene_episoden_kommen_chronologisch(store: EpisodeStore) -> None:
    """Sonst entstehen Aussagen aus dem Mai, bevor die aus dem März gesehen
    wurden — und die Ersetzungskette steht auf dem Kopf."""
    _record(store, "Mai", "spaeter",
            occurred_at=datetime(2026, 5, 1, tzinfo=timezone.utc))
    _record(store, "Maerz", "frueher",
            occurred_at=datetime(2026, 3, 1, tzinfo=timezone.utc))

    assert [e.title for e in store.pending()] == ["Maerz", "Mai"]


def test_naive_zeitangaben_bekommen_eine_zone(store: EpisodeStore) -> None:
    episode, _ = _record(store, "Ohne Zone", "x",
                         occurred_at=datetime(2026, 3, 1, 12, 0))
    assert episode.occurred_at.tzinfo is not None
    # Und die chronologische Sortierung wirft keinen TypeError.
    _record(store, "Mit Zone", "y", occurred_at=now())
    assert len(store.pending()) == 2


# -- Lesen ------------------------------------------------------------------


def test_suche_findet_titel_und_text(store: EpisodeStore) -> None:
    _record(store, "Angebot Meier", "Wir liefern bis Freitag.")
    _record(store, "Ganz anderes", "Hier steht Meier im Text.")
    _record(store, "Drittes", "Nichts davon.")

    assert len(store.search("Meier")) == 2


def test_zaehlung_nach_zustand(store: EpisodeStore) -> None:
    a, _ = _record(store, "Eins", "a")
    _record(store, "Zwei", "b")
    store.ignore(a.id)

    assert store.counts() == {"new": 1, "ignored": 1}


def test_episoden_haengen_an_projekten(store: EpisodeStore) -> None:
    episode, _ = _record(store, "Jour fixe", "Protokoll", project_id="p-1")
    _record(store, "Privat", "Nichts damit zu tun")

    assert [e.id for e in store.by_project("p-1")] == [episode.id]

    store.link_project(episode.id, None)
    assert store.by_project("p-1") == []


def test_beteiligte_werden_festgehalten(store: EpisodeStore) -> None:
    """Die Rohdaten für Kontakte und Verläufe."""
    episode, _ = _record(store, "Mail", "Text", participants=["meier@example.com"])
    assert store.get(episode.id).participants == ["meier@example.com"]


def test_ueberleben_des_neustarts(tmp_path) -> None:
    erste = EpisodeStore(tmp_path / "e.sqlite3")
    episode, _ = erste.record(
        EpisodeKind.DOCUMENT, "Bleibt", "Inhalt", _prov("datei:x.md"),
        occurred_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        participants=["Dr. Meier"], tags=["projekt"],
    )
    erste.mark_consolidated(episode.id, produced=["a-9"])
    erste.close()

    zweite = EpisodeStore(tmp_path / "e.sqlite3")
    wieder = zweite.get(episode.id)
    assert wieder.digest == episode.digest
    assert wieder.state is EpisodeState.CONSOLIDATED
    assert wieder.produced == ["a-9"]
    assert wieder.participants == ["Dr. Meier"]
    assert wieder.occurred_at == datetime(2024, 1, 2, tzinfo=timezone.utc)
    zweite.close()
