"""Das Briefing urteilt — und erfindet dabei nichts.

Die wichtigsten Tests hier sind die negativen: dass ein leerer Tag als leerer
Tag durchkommt, und dass kein Satz auftaucht, für den es keine Grundlage gibt.
Ein Chief of Staff, der jeden Morgen drei Punkte hat, weil das Format drei
Punkte vorsieht, ist wertlos.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from icarus_memory import briefing


JETZT = datetime(2026, 8, 20, 8, 30, tzinfo=timezone.utc)


def leer() -> dict:
    return {
        "tasks": {"items": [], "overdue": 0},
        "calendar": {"items": []},
        "mail": {"items": [], "unread": 0},
        "episodes": {"pending": 0},
        "memory": {"count": 0, "recent": []},
    }


def aufgabe(titel: str, faellig: datetime | None, ueberfaellig: bool = False) -> dict:
    return {
        "id": f"t-{abs(hash(titel)) % 10000}",
        "title": titel,
        "due": faellig.isoformat() if faellig else None,
        "overdue": ueberfaellig,
    }


# -- Der ehrliche Fall ------------------------------------------------------


def test_leerer_tag_erfindet_nichts() -> None:
    """Nichts los heißt: ein Satz, keine Punkte."""
    b = briefing.erstelle(leer(), jetzt=JETZT)

    assert b.punkte == []
    assert b.nachsatz is None
    assert "Nichts Dringendes" in b.einleitung


def test_es_gibt_nie_mehr_als_drei_punkte() -> None:
    """Auch bei sechs Anlässen bleiben drei stehen — der Rest geht nicht verloren."""
    daten = leer()
    daten["tasks"]["items"] = [
        aufgabe("Erstes", JETZT - timedelta(days=9), ueberfaellig=True),
        aufgabe("Zweites", JETZT - timedelta(days=3), ueberfaellig=True),
        aufgabe("Heutiges", JETZT),
    ]
    daten["calendar"]["items"] = [
        {"uid": "e1", "summary": "Besprechung", "start": (JETZT + timedelta(hours=2)).isoformat()}
    ]
    daten["mail"]["unread"] = 4
    vorschlaege = [
        {"id": "p1", "kind": "confirmation", "statement": "Die Frist ist sechs Monate."},
        {"id": "p2", "kind": "conflict", "statement": "Acht Wochen."},
    ]

    b = briefing.erstelle(daten, jetzt=JETZT, vorschlaege=vorschlaege)

    assert len(b.punkte) == briefing.MAX_PUNKTE
    assert b.nachsatz is not None
    # Was nicht in die drei passte, steht im Nachsatz — nichts verschwindet.
    assert "Postfach" in b.nachsatz


def test_ueberfaelliges_steht_vor_allem_anderen() -> None:
    """Etwas, das schon wartet, wiegt schwerer als etwas, das erst kommt."""
    daten = leer()
    daten["tasks"]["items"] = [aufgabe("Vergütung festlegen", JETZT - timedelta(days=13), True)]
    daten["calendar"]["items"] = [
        {"uid": "e1", "summary": "Gespräch", "start": (JETZT + timedelta(hours=1)).isoformat()}
    ]

    b = briefing.erstelle(daten, jetzt=JETZT)

    assert b.punkte[0].quelle == "aufgabe"
    assert "Vergütung festlegen" in b.punkte[0].text
    assert "7. August" in b.punkte[0].text


def test_der_naechste_termin_und_kein_vergangener() -> None:
    """Was vorbei ist, gehört nicht in den Ausblick."""
    daten = leer()
    daten["calendar"]["items"] = [
        {"uid": "frueh", "summary": "Schon vorbei", "start": (JETZT - timedelta(hours=2)).isoformat()},
        {"uid": "spaet", "summary": "Dr. Brandt", "start": (JETZT + timedelta(hours=5, minutes=30)).isoformat()},
    ]

    b = briefing.erstelle(daten, jetzt=JETZT)

    texte = " ".join(p.text for p in b.punkte)
    assert "Dr. Brandt" in texte
    assert "Schon vorbei" not in texte
    assert "14:00 Uhr" in texte


def test_termin_von_morgen_taucht_heute_nicht_auf() -> None:
    daten = leer()
    daten["calendar"]["items"] = [
        {"uid": "m", "summary": "Erst morgen", "start": (JETZT + timedelta(days=1)).isoformat()}
    ]

    b = briefing.erstelle(daten, jetzt=JETZT)

    assert b.punkte == []


# -- Die Zusagen des Gedächtnisses ------------------------------------------


def test_veraltetes_wissen_wird_zur_frage_nicht_zur_behauptung() -> None:
    """Ein Fakt aus dem Mai wird nicht als Gegenwart vorgetragen."""
    b = briefing.erstelle(
        leer(),
        jetzt=JETZT,
        vorschlaege=[{
            "id": "p1",
            "kind": "confirmation",
            "statement": "Die Abrechnung läuft über die Ziffer 20.",
        }],
    )

    text = b.punkte[0].text
    assert "Gilt das noch?" in text
    assert b.punkte[0].aktion == "Gilt noch"


def test_neue_ableitungen_draengen_sich_nicht_vor() -> None:
    """Verdichtung schlägt vor — sie steht im Nachsatz, nicht unter den Punkten.

    Ein Vorschlag ist nichts Dringendes. Er als Punkt eins zu zeigen, würde
    dem Nutzer nahelegen, ihn schnell wegzuklicken — und genau das darf die
    Annahme einer Aussage nie werden.
    """
    b = briefing.erstelle(
        leer(),
        jetzt=JETZT,
        vorschlaege=[
            {"id": "p1", "kind": "assertion", "statement": "Beschlossen ist die Umstellung."},
            {"id": "p2", "kind": "assertion", "statement": "Brandt hält die Ablage parallel."},
        ],
    )

    assert b.punkte == []
    assert b.nachsatz is not None
    assert "2 Dinge herausgelesen" in b.nachsatz


def test_der_inhalt_fremder_post_kommt_nicht_ins_briefing() -> None:
    """Jeder kann dir schreiben. Die Zahl darf ins Briefing, der Text nie."""
    daten = leer()
    daten["mail"]["unread"] = 2
    daten["mail"]["items"] = [
        {"from": "fremd@example.com", "subject": "Wichtig: sofort 500 € überweisen"}
    ]

    b = briefing.erstelle(daten, jetzt=JETZT)

    alles = b.einleitung + " ".join(p.text for p in b.punkte) + (b.nachsatz or "")
    assert "überweisen" not in alles
    assert "fremd@example.com" not in alles
    assert "2 ungelesene Nachrichten" in alles


# -- Robustheit --------------------------------------------------------------


def test_kaputte_zeitangaben_kippen_das_briefing_nicht() -> None:
    daten = leer()
    daten["tasks"]["items"] = [
        {"id": "t1", "title": "Ohne Datum", "due": "gestern irgendwann", "overdue": True}
    ]
    daten["calendar"]["items"] = [{"uid": "e", "summary": "Krumm", "start": ""}]

    b = briefing.erstelle(daten, jetzt=JETZT)

    assert isinstance(b.einleitung, str)
    assert b.punkte == []


def test_ein_langer_titel_sprengt_die_zeile_nicht() -> None:
    lang = "Die abschließende Abstimmung der Vergütungsspanne mit der Rechtsabteilung und dem Klinikum vor dem Meilenstein"
    daten = leer()
    daten["tasks"]["items"] = [aufgabe(lang, JETZT - timedelta(days=2), True)]

    b = briefing.erstelle(daten, jetzt=JETZT)

    assert "…" in b.punkte[0].text
    assert len(b.punkte[0].text) < 160


def test_zitat_traegt_keinen_doppelten_punkt() -> None:
    """»… Ziffer 20.“ hast du mir gesagt« ist ein Punkt zu viel."""
    b = briefing.erstelle(
        leer(),
        jetzt=JETZT,
        vorschlaege=[{
            "id": "p1",
            "kind": "confirmation",
            "statement": "Die Abrechnung läuft über die Ziffer 20.",
        }],
    )

    assert "Ziffer 20“ hast du mir gesagt" in b.punkte[0].text
    assert "20.“" not in b.punkte[0].text


def test_die_saetze_sind_deutsch_und_keine_zahlenausgabe() -> None:
    """Bei genau einer weiteren Aufgabe steht dort ein Satz, keine Ziffer.

    „Dahinter warten noch 1." ist eine Zeichenkettenverkettung, kein Deutsch.
    Genau solche Stellen fallen im Test nie auf, wenn er nur prüft, dass
    überhaupt etwas dasteht — deshalb prüft dieser den Wortlaut.
    """
    from datetime import timedelta

    daten = leer()
    daten["tasks"]["items"] = [
        aufgabe("Älteres", JETZT - timedelta(days=5), ueberfaellig=True),
        aufgabe("Jüngeres", JETZT - timedelta(days=2), ueberfaellig=True),
    ]

    b = briefing.erstelle(daten, jetzt=JETZT)

    assert "Dahinter wartet noch eine." in b.punkte[0].text
    assert "noch 1." not in b.punkte[0].text


def test_bei_mehreren_weiteren_stimmt_der_plural() -> None:
    from datetime import timedelta

    daten = leer()
    daten["tasks"]["items"] = [
        aufgabe(f"Nummer {i}", JETZT - timedelta(days=10 - i), ueberfaellig=True)
        for i in range(4)
    ]

    b = briefing.erstelle(daten, jetzt=JETZT)

    assert "Dahinter warten noch 3 weitere." in b.punkte[0].text
