"""Aktualität: wie alt darf ein Fakt sein, bevor er nicht mehr als Gegenwart gilt?

Das Selbstmodell weiß, *wann* es etwas erfahren hat — `recorded_at`,
`valid_from`, `provenance.captured_at`, `last_confirmed_at`. Genutzt wurde davon
im Ausgabepfad nichts. Damit sah das Modell eine ein Jahr alte
Zustandsbeschreibung genauso wie eine von heute, und behauptete sie entsprechend.

Der Horizont hängt an der **Art** der Aussage, nicht an einer globalen Zahl. Ein
Wohnort altert über Jahre, ein Projektzustand über Tage. Eine globale Schwelle
wäre in beide Richtungen falsch: sie würde stabile Vorlieben grundlos verwerfen
und flüchtige Zustände zu lange für wahr halten.

`expires_at` bleibt davon unberührt. Es ist die *ausdrückliche* Frist, die der
Aufrufer gesetzt hat, und wird von `SelfModelStore.usable()` durchgesetzt. Die
Horizonte hier sind die *abgeleitete* Alterung für alles, wo niemand eine Frist
gesetzt hat — und das ist der Normalfall.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from .model import Assertion, Kind, now

DAY = timedelta(days=1)


class Currency(str, Enum):
    """Das Urteil über einen Fakt zum Zeitpunkt der Nutzung."""

    CURRENT = "current"
    """Innerhalb seines Horizonts. Darf ohne Zusatz verwendet werden."""

    STALE = "stale"
    """Über dem Horizont. Nur mit Standangabe, nicht als Gegenwart."""

    OUTDATED = "outdated"
    """Weit über dem Horizont. Gehört in den Verlauf, nicht ins Wissen."""


@dataclass(frozen=True)
class Horizon:
    stale_after: timedelta
    outdated_after: timedelta | None
    """`None` heißt: veraltet nie.

    Das gilt für Aussagen über die Vergangenheit. Eine Episode wird nicht
    unwahr, weil Zeit vergeht — sie wird nur weniger relevant.
    """


_HORIZONS: dict[Kind, Horizon] = {
    # Was sich selten ändert.
    Kind.IDENTITY: Horizon(365 * DAY, 1825 * DAY),
    Kind.RELATIONSHIP: Horizon(365 * DAY, 1825 * DAY),
    Kind.SKILL: Horizon(365 * DAY, 1825 * DAY),
    Kind.PREFERENCE: Horizon(365 * DAY, 1095 * DAY),
    # Bindende Grenzen sollen bestätigt werden, aber nicht schnell verfallen.
    Kind.CONSTRAINT: Horizon(365 * DAY, 1095 * DAY),
    # Vorhaben verschieben sich.
    Kind.GOAL: Horizon(90 * DAY, 730 * DAY),
    # Der flüchtigste Typ. „Projekt A ist blockiert" ist nach zwei Wochen eine
    # Frage, keine Tatsache.
    Kind.STATE: Horizon(7 * DAY, 365 * DAY),
    # Vergangenes bleibt wahr.
    Kind.EPISODE: Horizon(90 * DAY, None),
    # Eine Entscheidung wurde getroffen — das bleibt wahr, egal wie alt sie
    # ist. Sie veraltet nicht mit der Zeit, sondern **wenn ihre Grundlage
    # fällt**, und das prüft `entscheidungen.py`, nicht die Uhr.
    Kind.DECISION: Horizon(365 * DAY, None),
}

_FALLBACK = Horizon(30 * DAY, 365 * DAY)
"""Eine unbekannte Art gilt als flüchtig, nicht als dauerhaft."""


def horizon_for(kind: Kind) -> Horizon:
    return _HORIZONS.get(kind, _FALLBACK)


def evidence_date(assertion: Assertion) -> datetime:
    """Der jüngste Zeitpunkt, an dem dieser Fakt gestützt war.

    Eine Bestätigung wiegt am schwersten: sie ist die ausdrückliche Aussage der
    Person, dass es weiterhin gilt. Danach kommt, ab wann es inhaltlich gilt,
    dann der Zeitpunkt des Originalereignisses, und zuletzt der Moment, in dem
    Icarus davon erfuhr.
    """
    for kandidat in (
        assertion.last_confirmed_at,
        assertion.valid_from,
        assertion.provenance.captured_at,
    ):
        if kandidat is not None:
            return kandidat
    return assertion.recorded_at


def judge(assertion: Assertion, at: datetime | None = None) -> Currency:
    at = at or now()
    horizon = horizon_for(assertion.kind)
    alter = at - evidence_date(assertion)

    if alter <= horizon.stale_after:
        return Currency.CURRENT
    if horizon.outdated_after is None or alter <= horizon.outdated_after:
        return Currency.STALE
    return Currency.OUTDATED


def source_label(assertion: Assertion) -> str:
    """Die Quellenangabe für die Ausgabe.

    Bevorzugt die konkrete Kennung. Nur wenn keine da ist, bleibt die Quellenart
    übrig — dann steht in der Ausgabe wenigstens, dass der Beleg unspezifisch
    ist, statt eine Genauigkeit vorzutäuschen.
    """
    ref = (assertion.provenance.source_ref or "").strip()
    art = assertion.provenance.source_type.value
    if not ref:
        return art
    # Eine Kennung wie „mail:88" bringt ihr Schema schon mit. Ein zweites davor
    # zu setzen ergäbe „chat:mail:88" — falsch und irreführend, weil es eine
    # andere Quelle behauptet als die, aus der der Verweis stammt.
    if ":" in ref:
        return ref
    return f"{art}:{ref}"


def describe(assertion: Assertion, at: datetime | None = None) -> str:
    """Der Klammerzusatz hinter einem Fakt im Kontextblock."""
    at = at or now()
    urteil = judge(assertion, at)
    teile = [source_label(assertion)]

    if assertion.provenance.source_type.value == "inference":
        teile.append("selbst gefolgert")

    if urteil is Currency.CURRENT:
        if assertion.last_confirmed_at is not None:
            teile.append(f"bestätigt {assertion.last_confirmed_at.date().isoformat()}")
    else:
        teile.append(f"Stand {evidence_date(assertion).date().isoformat()}")
        if urteil is Currency.STALE:
            teile.append("womöglich veraltet")

    return ", ".join(teile)
