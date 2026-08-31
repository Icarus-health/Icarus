"""Tests der Mittelfristschicht.

Die Schicht steht und fällt mit drei Zusicherungen: Digest, Entdopplung und
Zustand statt Löschen. Alles andere ist Ablage.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from icarus_memory.episodes import (
    EpisodeArtifact,
    EpisodeError,
    EpisodeKind,
    EpisodeState,
    EpisodeStore,
    Participant,
    SourceIdentity,
    canonical_digest,
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
    assert episode.digest != digest_of("Wir liefern bis Freitag.")
    assert store.get(episode.id).digest == episode.digest


def test_manuelle_episode_hat_kanonischen_inhaltsdigest(store: EpisodeStore) -> None:
    episode, _ = _record(store, "Angebot", "Wir liefern bis Freitag.")

    assert episode.digest == canonical_digest(episode)


def test_geaenderte_quelle_hat_einen_anderen_digest() -> None:
    """Genau daran soll später erkennbar sein, dass sich etwas verschoben hat."""
    assert digest_of("Wir liefern bis Freitag.") != digest_of("Wir liefern bis Montag.")


# -- Entdopplung ------------------------------------------------------------


def _source_event(store: EpisodeStore, identity: SourceIdentity, body: str, **overrides):
    values = {
        "identity": identity, "kind": EpisodeKind.MESSAGE,
        "event_type": "email.received", "title": "Angebot", "body": body,
        "provenance": _prov(identity.native_source_id),
    }
    values.update(overrides)
    return store.upsert_source_event(**values)


def test_quellidentitaet_ist_idempotent_und_erhaelt_zustand(store: EpisodeStore) -> None:
    identity = SourceIdentity("imap", "account-a", "message-1")
    erste = _source_event(store, identity, "Wir liefern bis Freitag.")
    store.mark_consolidated(erste.event.id, produced=["a-1"])
    zweite = _source_event(store, identity, "Wir liefern bis Freitag.")

    assert erste.status == "created"
    assert zweite.status == "unchanged"
    assert zweite.event.id == erste.event.id
    assert zweite.event.state is EpisodeState.CONSOLIDATED
    assert zweite.event.produced == ["a-1"]
    assert len(store.revisions(erste.event.id)) == 1


def test_gleicher_text_aber_andere_source_identity_bleibt_getrennt(store: EpisodeStore) -> None:
    erste = _source_event(store, SourceIdentity("imap", "account-a", "one"), "Danke.")
    zweite = _source_event(store, SourceIdentity("imap", "account-a", "two"), "Danke.")

    assert erste.event.digest == zweite.event.digest
    assert erste.event.id != zweite.event.id
    assert len(store.raw_events()) == 2


def test_gleiches_native_id_in_zwei_konten_bleibt_getrennt(store: EpisodeStore) -> None:
    erste = _source_event(store, SourceIdentity("imap", "account-a", "123"), "Danke.")
    zweite = _source_event(store, SourceIdentity("imap", "account-b", "123"), "Danke.")

    assert erste.event.id != zweite.event.id
    assert len(store.raw_events()) == 2


def test_source_identity_wird_auch_auf_datenbankebene_erzwungen(store: EpisodeStore) -> None:
    import sqlite3

    result = _source_event(store, SourceIdentity("imap", "account-a", "message-1"), "Text")
    episode = result.event
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(  # noqa: SLF001 - Umgehung prueft den Indexvertrag
            "INSERT INTO episodes (id, digest, kind, state, recorded_at, occurred_at, project_id, title, body, "
            "source_type, source_account, native_source_id, event_type, captured_at, source_updated_at, artifact, "
            "scope_id, trust, raw_metadata, revision, source_state, document) "
            "VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, '{}', 1, 'active', '{}')",
            ("e-vorbei", episode.digest, "message", "new", now().isoformat(), "Kopie", "Text",
             "imap", "account-a", "message-1", "email.received", now().isoformat(), "source", "direct_source"),
        )


def test_geaenderter_quellvorgang_erzeugt_revision_keinen_neuen_event(store: EpisodeStore) -> None:
    identity = SourceIdentity("imap", "account-a", "message-1")
    erste = _source_event(store, identity, "Bis Freitag.")
    zweite = _source_event(store, identity, "Bis Montag.")

    assert zweite.status == "updated"
    assert zweite.event.id == erste.event.id
    assert zweite.event.revision == 2
    assert zweite.previous_revision == 1
    history = store.revisions(erste.event.id)
    assert [item.body for item in history] == ["Bis Freitag.", "Bis Montag."]
    assert history[0].digest != history[1].digest


def test_revision_write_rollt_bei_fehler_komplett_zurueck(
    store: EpisodeStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = SourceIdentity("imap", "account-a", "message-atomic")
    first = _source_event(store, identity, "Vorher")
    original = store._write_revision_locked  # noqa: SLF001 - gezielte Failure Injection

    def fail(_: object) -> None:
        raise RuntimeError("Revision fehlgeschlagen")

    monkeypatch.setattr(store, "_write_revision_locked", fail)
    with pytest.raises(RuntimeError, match="Revision"):
        _source_event(store, identity, "Nachher")

    assert store.get(first.event.id).body == "Vorher"
    assert [item.revision for item in store.revisions(first.event.id)] == [1]
    monkeypatch.setattr(store, "_write_revision_locked", original)
    retry = _source_event(store, identity, "Nachher")
    assert retry.status == "updated" and retry.event.revision == 2


def _parallel_source_events(
    stores: list[EpisodeStore],
    identities_and_bodies: list[tuple[SourceIdentity, str]],
):
    barrier = Barrier(len(stores))

    def run(
        store: EpisodeStore, identity_and_body: tuple[SourceIdentity, str],
    ):
        barrier.wait()
        identity, body = identity_and_body
        return _source_event(store, identity, body)

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        futures = [
            executor.submit(run, store, identity_and_body)
            for store, identity_and_body in zip(stores, identities_and_bodies, strict=True)
        ]
        return [future.result(timeout=10) for future in futures]


def test_parallel_ingest_gleicher_identity_und_inhalt_ist_idempotent(tmp_path) -> None:
    path = tmp_path / "parallel-same.sqlite3"
    stores = [EpisodeStore(path), EpisodeStore(path)]
    identity = SourceIdentity("imap", "account-a", "parallel-same")
    try:
        results = _parallel_source_events(
            stores, [(identity, "Gleicher Inhalt"), (identity, "Gleicher Inhalt")],
        )

        assert sorted(result.status for result in results) == ["created", "unchanged"]
        assert len({result.event.id for result in results}) == 1
        assert len(stores[0].raw_events()) == 1
        assert len(stores[0].revisions(results[0].event.id)) == 1
    finally:
        for store in stores:
            store.close()


def test_parallel_ingest_gleicher_identity_und_anderer_inhalt_bleibt_konsistent(
    tmp_path,
) -> None:
    path = tmp_path / "parallel-update.sqlite3"
    stores = [EpisodeStore(path), EpisodeStore(path)]
    identity = SourceIdentity("imap", "account-a", "parallel-update")
    try:
        results = _parallel_source_events(
            stores, [(identity, "Inhalt A"), (identity, "Inhalt B")],
        )

        assert sorted(result.status for result in results) == ["created", "updated"]
        assert len({result.event.id for result in results}) == 1
        event_id = results[0].event.id
        history = stores[0].revisions(event_id)
        current = stores[0].get(event_id)
        assert [item.revision for item in history] == [1, 2]
        assert {item.body for item in history} == {"Inhalt A", "Inhalt B"}
        assert current.to_dict() == history[-1].to_dict()
        assert all(item.digest == canonical_digest(item) for item in history)
    finally:
        for store in stores:
            store.close()


def test_parallel_ingest_unterschiedlicher_identity_bleibt_getrennt(tmp_path) -> None:
    path = tmp_path / "parallel-distinct.sqlite3"
    stores = [EpisodeStore(path), EpisodeStore(path)]
    identities = [
        SourceIdentity("imap", "account-a", "parallel-one"),
        SourceIdentity("imap", "account-a", "parallel-two"),
    ]
    try:
        results = _parallel_source_events(
            stores, [(identities[0], "Danke."), (identities[1], "Danke.")],
        )

        assert [result.status for result in results] == ["created", "created"]
        assert len({result.event.id for result in results}) == 2
        assert len(stores[0].raw_events()) == 2
        assert all(len(stores[0].revisions(result.event.id)) == 1 for result in results)
    finally:
        for store in stores:
            store.close()


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


def test_operativer_project_link_veraendert_nicht_den_source_digest(store: EpisodeStore) -> None:
    event = _source_event(store, SourceIdentity("imap", "account-a", "project-link"), "Text").event
    digest = event.digest
    store.link_project(event.id, "p-1")

    assert store.get(event.id).digest == digest
    assert len(store.revisions(event.id)) == 1


def test_beteiligte_werden_festgehalten(store: EpisodeStore) -> None:
    """Die Rohdaten für Kontakte und Verläufe."""
    episode, _ = _record(store, "Mail", "Text", participants=["meier@example.com"])
    assert store.get(episode.id).participants == ["meier@example.com"]


def test_structured_participants_bleiben_rohdaten_ohne_person_merge(store: EpisodeStore) -> None:
    result = _source_event(
        store, SourceIdentity("imap", "account-a", "message-1"), "Text",
        participant_details=[Participant(role="sender", display_name="Claudia", address="claudia@example.com")],
    )

    event = store.get(result.event.id)
    assert event.participants == ["Claudia"]
    assert event.participant_details[0].to_dict() == {
        "role": "sender", "display_name": "Claudia", "address": "claudia@example.com",
    }


def test_summary_ist_derived_und_raw_query_sieht_sie_nicht(store: EpisodeStore) -> None:
    source, _ = _record(store, "Quelle", "Rohtext")
    summary = store.record_summary("Rückblick", "Zusammenfassung", "2026-08", [source.id])

    assert source.artifact is EpisodeArtifact.SOURCE
    assert summary.artifact is EpisodeArtifact.DERIVED
    assert [event.id for event in store.raw_events()] == [source.id]
    assert [event.id for event in store.derived_artifacts()] == [summary.id]
    assert summary not in store.pending()


def test_geloeschte_summary_entfernt_auch_ihre_revisionen(store: EpisodeStore) -> None:
    source, _ = _record(store, "Quelle", "Rohtext")
    summary = store.record_summary("Rückblick", "Zusammenfassung", "2026-08", [source.id])

    assert store.delete_summary(summary.id) == 1
    with pytest.raises(EpisodeError, match="Unbekannte"):
        store.revisions(summary.id)


def test_inconsistent_source_category_is_rejected(store: EpisodeStore) -> None:
    with pytest.raises(EpisodeError, match="passt nicht"):
        store.upsert_source_event(
            identity=SourceIdentity("imap", "account-a", "bad-category"),
            kind=EpisodeKind.MESSAGE,
            event_type="calendar.event",
            title="Falsch", body="Text", provenance=_prov("bad-category"),
        )


def test_invalid_source_identity_fails_closed(store: EpisodeStore) -> None:
    with pytest.raises(EpisodeError, match="source_account"):
        _source_event(store, SourceIdentity("imap", " ", "message-1"), "Text")
    with pytest.raises(EpisodeError, match="kontrollierter Connector"):
        SourceIdentity("unbekannter typ", "account-a", "message-1")
    with pytest.raises(EpisodeError, match="Steuerzeichen"):
        SourceIdentity("imap", "account-a", "message\n1")


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
