"""Tests der Selbstmodell-Logik.

Laufen ohne Netz, ohne Modell und ohne cognee — das ist Absicht: die Regeln,
die das Modell überprüfbar machen, dürfen nicht von einer Fremdbibliothek
abhängen.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from icarus_memory import (
    ConflictError,
    Kind,
    MemoryBackend,
    Provenance,
    RedactionReason,
    SelfModelStore,
    Sensitivity,
    SourceType,
    SqliteBackend,
    Status,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def chat(ref: str = "chat:1") -> Provenance:
    return Provenance(source_type=SourceType.CHAT, source_ref=ref, captured_at=T0)


@pytest.fixture
def store() -> SelfModelStore:
    return SelfModelStore(MemoryBackend(), subject_id="test")


# -- Provenienz ------------------------------------------------------------


def test_aussage_traegt_herkunft(store: SelfModelStore) -> None:
    a = store.record("Wohnt in Hamburg.", Kind.STATE, chat(), at=T0)
    assert a.provenance.source_type is SourceType.CHAT
    assert a.provenance.source_ref == "chat:1"
    assert a.status is Status.ACTIVE


def test_unbekannte_referenz_wird_abgelehnt(store: SelfModelStore) -> None:
    """Eine gebrochene Kette macht das Modell unprüfbar."""
    with pytest.raises(ConflictError, match="Unbekannte Aussage"):
        store.record("Abgeleitet.", Kind.PREFERENCE, chat(), derived_from=["a-gibtsnicht"])


# -- Ersetzung statt Überschreiben ----------------------------------------


def test_ersetzung_haelt_die_kette(store: SelfModelStore) -> None:
    alt = store.record("Wohnt in Hamburg.", Kind.STATE, chat(), at=T0)
    neu = store.record(
        "Wohnt in Leipzig.", Kind.STATE, chat("mail:2"), supersedes=[alt.id], at=T0
    )

    alt_neu_gelesen = store.history(neu.id)[0]
    assert alt_neu_gelesen.status is Status.SUPERSEDED
    assert alt_neu_gelesen.superseded_by == neu.id
    # Der alte Wert ist nicht verschwunden, nur nicht mehr gültig.
    assert alt_neu_gelesen.statement == "Wohnt in Hamburg."


def test_ersetzte_aussage_ist_nicht_mehr_verwendbar(store: SelfModelStore) -> None:
    alt = store.record("Wohnt in Hamburg.", Kind.STATE, chat(), at=T0)
    store.record("Wohnt in Leipzig.", Kind.STATE, chat(), supersedes=[alt.id], at=T0)

    usable = {a.statement for a in store.usable(at=T0)}
    assert usable == {"Wohnt in Leipzig."}


def test_history_liefert_kette_in_reihenfolge(store: SelfModelStore) -> None:
    a1 = store.record("Berlin.", Kind.STATE, chat(), at=T0)
    a2 = store.record("Hamburg.", Kind.STATE, chat(), supersedes=[a1.id], at=T0)
    a3 = store.record("Leipzig.", Kind.STATE, chat(), supersedes=[a2.id], at=T0)

    for start in (a1.id, a2.id, a3.id):
        assert [a.statement for a in store.history(start)] == [
            "Berlin.",
            "Hamburg.",
            "Leipzig.",
        ]


# -- Zeitliche Gültigkeit --------------------------------------------------


def test_abgelaufene_aussage_faellt_raus(store: SelfModelStore) -> None:
    store.record(
        "Ist diese Woche krankgeschrieben.",
        Kind.STATE,
        chat(),
        expires_at=T0 + timedelta(days=7),
        at=T0,
    )
    assert len(store.usable(at=T0 + timedelta(days=1))) == 1
    assert store.usable(at=T0 + timedelta(days=8)) == []


def test_ablauf_setzt_status(store: SelfModelStore) -> None:
    a = store.record("Temporär.", Kind.STATE, chat(), expires_at=T0 + timedelta(days=1), at=T0)
    store.usable(at=T0 + timedelta(days=2))
    assert store.history(a.id)[0].status is Status.EXPIRED


def test_zukuenftige_gueltigkeit_zaehlt_noch_nicht(store: SelfModelStore) -> None:
    store.record(
        "Wohnt ab Juni in Leipzig.",
        Kind.STATE,
        chat(),
        valid_from=T0 + timedelta(days=30),
        at=T0,
    )
    assert store.usable(at=T0) == []
    assert len(store.usable(at=T0 + timedelta(days=31))) == 1


def test_bestaetigung_belebt_abgelaufene_aussage(store: SelfModelStore) -> None:
    """Der Mechanismus, über den das Modell aktuell bleibt, ohne zu raten."""
    a = store.record("Arbeitet bei X.", Kind.STATE, chat(), expires_at=T0 + timedelta(days=1), at=T0)
    later = T0 + timedelta(days=5)
    store.usable(at=later)

    store.confirm(a.id, at=later)
    assert [x.statement for x in store.usable(at=later)] == ["Arbeitet bei X."]
    assert store.history(a.id)[0].last_confirmed_at == later


def test_ersetzte_aussage_kann_nicht_bestaetigt_werden(store: SelfModelStore) -> None:
    alt = store.record("Alt.", Kind.STATE, chat(), at=T0)
    store.record("Neu.", Kind.STATE, chat(), supersedes=[alt.id], at=T0)
    with pytest.raises(ConflictError, match="ersetzt"):
        store.confirm(alt.id)


# -- Widerruf und Kaskade --------------------------------------------------


def test_widerruf_nimmt_abgeleitete_mit(store: SelfModelStore) -> None:
    """Der Kern des Widerrufspfads: Information darf ihre Löschung nicht überleben."""
    quelle = store.record("Arbeitet im Gesundheitswesen.", Kind.IDENTITY, chat(), at=T0)
    ableitung = store.record(
        "Interessiert sich vermutlich für Medizinrecht.",
        Kind.PREFERENCE,
        Provenance(source_type=SourceType.INFERENCE),
        derived_from=[quelle.id],
        at=T0,
    )
    enkel = store.record(
        "Könnte Fachliteratur zu Medizinrecht schätzen.",
        Kind.PREFERENCE,
        Provenance(source_type=SourceType.INFERENCE),
        derived_from=[ableitung.id],
        at=T0,
    )

    affected = store.redact(quelle.id, reason=RedactionReason.USER_REQUEST, at=T0)

    assert {a.id for a in affected} == {quelle.id, ableitung.id, enkel.id}
    for a in affected:
        assert a.status is Status.REDACTED
        assert "Medizin" not in a.statement
        assert "Gesundheitswesen" not in a.statement
    assert store.usable(at=T0) == []


def test_widerruf_hinterlaesst_grabstein(store: SelfModelStore) -> None:
    a = store.record("Etwas Privates.", Kind.RELATIONSHIP, chat(), at=T0)
    store.redact(a.id, at=T0)

    stein = store.history(a.id)[0]
    assert stein.status is Status.REDACTED
    assert stein.redaction is not None
    assert stein.redaction.reason is RedactionReason.USER_REQUEST
    # Die Lücke bleibt als Lücke erkennbar.
    assert stein.id == a.id


def test_kaskade_wird_beim_ursprung_vermerkt(store: SelfModelStore) -> None:
    quelle = store.record("Quelle.", Kind.IDENTITY, chat(), at=T0)
    kind = store.record(
        "Ableitung.",
        Kind.PREFERENCE,
        Provenance(source_type=SourceType.INFERENCE),
        derived_from=[quelle.id],
        at=T0,
    )
    store.redact(quelle.id, at=T0)

    stein = store.history(quelle.id)[0]
    assert stein.redaction is not None
    assert stein.redaction.cascade == [kind.id]


def test_widerrufenes_kann_nicht_ersetzt_werden(store: SelfModelStore) -> None:
    a = store.record("Etwas.", Kind.STATE, chat(), at=T0)
    store.redact(a.id, at=T0)
    with pytest.raises(ConflictError, match="widerrufen"):
        store.record("Neu.", Kind.STATE, chat(), supersedes=[a.id], at=T0)


def test_zyklus_in_ableitungen_terminiert(store: SelfModelStore) -> None:
    """Defensiv: eine Schleife darf den Widerruf nicht hängen lassen."""
    a = store.record("A.", Kind.IDENTITY, chat(), at=T0)
    b = store.record(
        "B.", Kind.PREFERENCE, Provenance(source_type=SourceType.INFERENCE),
        derived_from=[a.id], at=T0,
    )
    # Künstlich einen Zyklus erzeugen.
    raw_a = store.history(a.id)[0]
    raw_a.derived_from = [b.id]

    affected = store.redact(a.id, at=T0)
    assert {x.id for x in affected} == {a.id, b.id}


# -- Suche und Schutzbedarf ------------------------------------------------


def test_recall_gibt_nichts_ungueltiges_zurueck(store: SelfModelStore) -> None:
    alt = store.record("Wohnt in Hamburg.", Kind.STATE, chat(), at=T0)
    store.record("Wohnt in Leipzig.", Kind.STATE, chat(), supersedes=[alt.id], at=T0)

    treffer = [a.statement for a in store.recall("Wohnt", at=T0)]
    assert treffer == ["Wohnt in Leipzig."]


def test_shareable_filtert_besonders_geschuetztes(store: SelfModelStore) -> None:
    store.record("Mag knappe Antworten.", Kind.PREFERENCE, chat(), at=T0)
    store.record(
        "Hat eine chronische Erkrankung.",
        Kind.STATE,
        chat(),
        sensitivity=Sensitivity.SPECIAL_CATEGORY,
        at=T0,
    )

    normal = [a.statement for a in store.shareable(at=T0)]
    assert normal == ["Mag knappe Antworten."]

    alles = store.shareable(max_sensitivity=Sensitivity.SPECIAL_CATEGORY, at=T0)
    assert len(alles) == 2


# -- Export gegen das Schema ----------------------------------------------


def test_export_ist_schemakonform(store: SelfModelStore) -> None:
    """Der Export muss gegen dieselbe Datei validieren, die die Doku beschreibt."""
    jsonschema = pytest.importorskip("jsonschema")

    alt = store.record("Wohnt in Hamburg.", Kind.STATE, chat(), at=T0)
    store.record("Wohnt in Leipzig.", Kind.STATE, chat(), supersedes=[alt.id], at=T0)
    store.record("Läuft ab.", Kind.STATE, chat(), expires_at=T0 + timedelta(days=1), at=T0)
    store.usable(at=T0 + timedelta(days=2))
    geheim = store.record("Privat.", Kind.RELATIONSHIP, chat(), at=T0)
    store.redact(geheim.id, at=T0)

    # Der Laufzeitvertrag darf dem veröffentlichten Export-Schema nicht
    # vorauslaufen. Beide Werte existieren seit der Entscheidungs- und
    # Dispute-Etappe und fehlten bisher unbemerkt im Schema, weil das statische
    # Beispielprofil sie nicht enthält.
    grundlage = store.record("Das Budget ist freigegeben.", Kind.STATE, chat(), at=T0)
    store.record(
        "Wir starten im Januar.",
        Kind.DECISION,
        chat(),
        derived_from=[grundlage.id],
        at=T0,
    )
    gegenstimme = store.record(
        "Das Budget ist noch nicht freigegeben.", Kind.STATE, chat(), at=T0
    )
    store.dispute(grundlage.id, gegenstimme.id)

    schema_path = Path(__file__).resolve().parents[2] / "schema" / "self-model.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    validator = jsonschema.Draft202012Validator(schema)
    validator.check_schema(schema)
    validator.validate(store.export().to_dict())


# -- SQLite-Bestand --------------------------------------------------------


def test_sqlite_haelt_den_bestand(tmp_path: Path) -> None:
    path = tmp_path / "self-model.sqlite3"

    backend = SqliteBackend(path)
    s1 = SelfModelStore(backend, subject_id="test")
    alt = s1.record("Wohnt in Hamburg.", Kind.STATE, chat(), at=T0)
    neu = s1.record("Wohnt in Leipzig.", Kind.STATE, chat(), supersedes=[alt.id], at=T0)
    backend.close()

    # Neu öffnen — der Bestand muss vollständig und exakt zurückkommen.
    wieder = SelfModelStore(SqliteBackend(path), subject_id="test")
    assert [a.statement for a in wieder.usable(at=T0)] == ["Wohnt in Leipzig."]
    kette = wieder.history(neu.id)
    assert [a.statement for a in kette] == ["Wohnt in Hamburg.", "Wohnt in Leipzig."]
    assert kette[0].status is Status.SUPERSEDED
    assert kette[0].provenance.source_ref == "chat:1"


def test_sqlite_ueber_threads_nutzbar(tmp_path: Path) -> None:
    """Regression: FastAPI führt synchrone Endpunkte in einem Threadpool aus.

    Eine thread-gebundene sqlite3-Verbindung scheitert dort mit
    "SQLite objects created in a thread can only be used in that same thread".
    Der Fehler trat erst im echten Serverbetrieb auf, nicht in den Unit-Tests.
    """
    import concurrent.futures as cf

    backend = SqliteBackend(tmp_path / "threads.sqlite3")
    store = SelfModelStore(backend, subject_id="test")

    def schreiben(i: int) -> str:
        return store.record(f"Aussage {i}.", Kind.EPISODE, chat(), at=T0).id

    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(schreiben, range(24)))

    assert len(set(ids)) == 24
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        gelesen = list(pool.map(lambda i: backend.get(i), ids))
    assert all(a is not None for a in gelesen)
    assert len(store.usable(at=T0)) == 24
