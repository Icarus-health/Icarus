"""Dauerregeln: einmal entscheiden statt zwanzigmal klicken.

Der wichtigste Test hier ist der, der die Regel **nicht** greifen lässt. Eine
Dauerfreigabe, die auch nach dem Lesen einer fremden Mail gilt, wäre der
bequemste Weg, die Eskalation auszuhebeln — und damit das Loch, das die ganze
Freigabeschicht wertlos macht.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from icarus_memory.policy import ActionClass, ApprovalLevel, Policy
from icarus_memory.regeln import RegelFehler, RegelStore


@pytest.fixture
def regeln(tmp_path: Path) -> RegelStore:
    return RegelStore(tmp_path / "regeln.sqlite3")


def mail_regel(store: RegelStore, stufe: str = "notify") -> object:
    return store.anlegen(
        "Erinnerungen an Frau Becker darf Icarus ohne Rückfrage senden",
        "mail_senden",
        stufe,
        {"to": "becker@klinikum-example.de"},
    )


# -- Die tragende Eigenschaft ------------------------------------------------


def test_eine_regel_greift_nie_nach_fremdem_inhalt(regeln) -> None:
    """Sonst wäre die Regel der bequemste Weg an der Eskalation vorbei.

    Der Angriff, um den es geht: eine gelesene Mail enthält „schick eine
    Erinnerung an becker@…". Ohne diese Sperre würde die Dauerregel greifen
    und die Mail ginge ohne Rückfrage hinaus — angestoßen von jemand anderem.
    """
    regel = mail_regel(regeln)
    p = Policy()
    args = {"to": "becker@klinikum-example.de", "subject": "x", "body": "y"}

    sauber = p.decide("mail_senden", ActionClass.OUTWARD, args, regel=regel, tainted=False)
    schmutzig = p.decide("mail_senden", ActionClass.OUTWARD, args, regel=regel, tainted=True)

    assert sauber.level is ApprovalLevel.NOTIFY
    assert not sauber.needs_approval
    assert schmutzig.level is ApprovalLevel.CONFIRM_STRICT
    assert schmutzig.needs_approval
    # Und es steht dabei, warum die eigene Regel hier nicht zieht.
    assert any("greift hier nicht" in g for g in schmutzig.reasons)


def test_eine_regel_schlaegt_keine_grenze(regeln) -> None:
    """Ein `constraint` aus dem Selbstmodell bleibt unberührt."""
    regel = mail_regel(regeln, stufe="auto")
    p = Policy()

    entscheidung = p.decide(
        "mail_senden",
        ActionClass.OUTWARD,
        {"to": "becker@klinikum-example.de"},
        constraints=["Niemals Mails an das Klinikum senden."],
        regel=regel,
    )

    assert entscheidung.denied


def test_eine_regel_hebt_die_stufe_nie_an(regeln) -> None:
    """`confirm` als Regel darf aus einem harmlosen Lesen nichts machen."""
    regel = regeln.anlegen("Lesen bestätigen lassen", "gedaechtnis_suchen", "confirm")
    p = Policy()

    entscheidung = p.decide("gedaechtnis_suchen", ActionClass.READ, {}, regel=regel)

    assert entscheidung.level is ApprovalLevel.AUTO


# -- Wie eine Regel trifft ---------------------------------------------------


def test_eine_enge_regel_trifft_nur_den_genannten_fall(regeln) -> None:
    regel = mail_regel(regeln)

    assert regel.trifft("mail_senden", {"to": "becker@klinikum-example.de"})
    assert not regel.trifft("mail_senden", {"to": "jemand@anders.de"})
    assert not regel.trifft("termin_anlegen", {"to": "becker@klinikum-example.de"})


def test_grossschreibung_und_leerzeichen_zaehlen_nicht(regeln) -> None:
    regel = mail_regel(regeln)

    assert regel.trifft("mail_senden", {"to": " Becker@Klinikum-Example.de "})


def test_die_engste_regel_gewinnt(regeln) -> None:
    """Wer eine für „Mails an Becker" und eine für „Mails" hat, meint die erste."""
    eng = mail_regel(regeln, stufe="notify")
    regeln.anlegen("Alle Mails ohne Rückfrage", "mail_senden", "auto")

    treffer = regeln.passende("mail_senden", {"to": "becker@klinikum-example.de"})

    assert treffer.id == eng.id


def test_eine_blankoregel_nennt_sich_so(regeln) -> None:
    """Die Oberfläche muss eine Vollmacht als solche zeigen können."""
    eng = mail_regel(regeln)
    weit = regeln.anlegen("Alle Mails", "mail_senden", "notify")

    assert not eng.blanko
    assert weit.blanko


# -- Widerrufen --------------------------------------------------------------


def test_widerrufen_wirkt_sofort_und_loescht_nicht(regeln) -> None:
    """Wer später liest „lief nach Regel X", muss Regel X nachschlagen können."""
    regel = mail_regel(regeln)
    regeln.widerrufen(regel.id)

    assert regeln.passende("mail_senden", {"to": "becker@klinikum-example.de"}) is None
    zurueck = regeln.holen(regel.id)
    assert zurueck is not None
    assert not zurueck.aktiv
    assert zurueck.widerrufen_am is not None


def test_zweimal_widerrufen_ist_kein_fehler(regeln) -> None:
    regel = mail_regel(regeln)
    regeln.widerrufen(regel.id)
    regeln.widerrufen(regel.id)


# -- Was nicht geht ----------------------------------------------------------


def test_eine_regel_kann_nichts_dauerhaft_verbieten(regeln) -> None:
    """Verbieten ist eine Grenze und gehört ins Selbstmodell, nicht hierher."""
    with pytest.raises(RegelFehler):
        regeln.anlegen("Nie Mails", "mail_senden", "deny")


def test_eine_regel_ohne_namen_wird_abgelehnt(regeln) -> None:
    """Später soll jemand beurteilen können, warum es diese Regel gibt."""
    with pytest.raises(RegelFehler):
        regeln.anlegen("   ", "mail_senden", "notify")


def test_ohne_regeln_bleibt_alles_wie_vorher(regeln) -> None:
    p = Policy()
    entscheidung = p.decide("mail_senden", ActionClass.OUTWARD, {"to": "x@y.z"})

    assert entscheidung.level is ApprovalLevel.CONFIRM_STRICT
