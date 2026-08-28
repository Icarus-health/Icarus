"""Dringend gegen wichtig.

Bis hierher ordnet das Briefing nach Fristen, Alter und Anzahl. Das reicht
für **dringend**. Es reicht nicht für **wichtig**, und der Unterschied ist
der Grund, warum es Stabschefs gibt.

Denn was wichtig ist, mahnt niemand an. Eine überfällige Aufgabe meldet sich
von selbst — sie hat ein Datum. Ein Vorhaben, an dem seit sechs Wochen nichts
passiert ist, meldet sich nie. Es fehlt nicht, es steht still, und Stille
erzeugt keinen Eintrag.

Genau das ist die Lücke: **Ziele erzeugten bisher keinen einzigen
Briefingpunkt.** Wer jeden Morgen abarbeitet, was ruft, kommt nie zu dem, was
schweigt.

## Warum ohne Modell

Der ursprüngliche Plan sah für diese Etappe ein Modell vor: es sollte
gewichten, was auf welches Ziel einzahlt. Beim Bauen zeigte sich, dass die
Frage viel einfacher ist und deshalb kein Modell braucht:

> Wann ist zuletzt etwas passiert, das dieses Ziel betraf?

Das ist ablesbar — aus Episoden, Aufgaben und Aussagen, die dieselben Marken
tragen wie das Ziel. Ein Modell würde hier raten, wo gezählt werden kann, und
wäre dabei weder schneller noch nachprüfbarer.

## Warum leises Gewicht

Ein eingeschlafenes Vorhaben ist wichtig, aber nicht dringend, und das muss
sich in der Rangfolge zeigen. Es steht deshalb unter allem mit Datum. An einem
vollen Tag kommt es nicht vor — wer drei brennende Dinge hat, soll nicht
zusätzlich an das Halbjahresvorhaben erinnert werden. An einem ruhigen Tag ist
es das Wertvollste, was dasteht.

## Was hier nicht passiert

Kein Ziel wird als gescheitert markiert, keins abgeschlossen, keins angelegt.
Dieses Modul liest und zählt. Ob ein stillstehendes Vorhaben ein Problem ist,
weiß nur der Mensch — vielleicht ruht es mit Absicht.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .model import Assertion, Kind, now

#: Ab wann ein Vorhaben als eingeschlafen gilt. Sechs Wochen sind lang genug,
#: dass eine Pause keine mehr ist, und kurz genug, dass kein Quartal vergeht.
SCHLAEFT_AB_TAGEN = 42

#: Ohne Marken ist kein Bezug herstellbar. Dann schweigt dieses Modul, statt
#: aus dem Fehlen von Belegen einen Stillstand zu machen.
MIN_MARKEN = 1


@dataclass
class Vorhaben:
    """Ein Ziel und wann zuletzt etwas dafür geschah."""

    ziel: Assertion
    letzte_regung: datetime | None
    woran: str | None
    """Woran die letzte Regung erkannt wurde — für die Begründung."""

    @property
    def marken(self) -> list[str]:
        return list(self.ziel.tags)

    @property
    def beurteilbar(self) -> bool:
        """Lässt sich zu diesem Ziel überhaupt etwas sagen?

        Ohne Marken gibt es keinen Weg von einem Ziel zu dem, was dafür getan
        wurde. Dann ist Schweigen die ehrliche Antwort — nicht „seit immer
        nichts passiert“.
        """
        return len(self.marken) >= MIN_MARKEN

    def tage_still(self, jetzt: datetime) -> int | None:
        """Wie lange schon nichts mehr geschah.

        Gezählt wird ab dem **späteren** von beidem: der letzten Regung und
        dem Zeitpunkt, an dem das Ziel gefasst wurde. Nichts kann still
        gestanden haben, bevor es das Vorhaben gab — sonst hieße ein heute
        gefasstes Ziel „seit drei Monaten passiert nichts“, nur weil eine alte
        eingelesene Notiz zufällig dieselbe Marke trägt.
        """
        if not self.beurteilbar:
            return None
        seit = self.ziel.recorded_at
        if self.letzte_regung is not None and self.letzte_regung > seit:
            seit = self.letzte_regung
        return max(0, (jetzt - seit).days)

    def schlaeft(self, jetzt: datetime) -> bool:
        tage = self.tage_still(jetzt)
        return tage is not None and tage >= SCHLAEFT_AB_TAGEN

    def to_dict(self, jetzt: datetime | None = None) -> dict[str, Any]:
        jetzt = jetzt or now()
        return {
            "id": self.ziel.id,
            "satz": self.ziel.statement,
            "marken": self.marken,
            "beurteilbar": self.beurteilbar,
            "letzte_regung": (
                self.letzte_regung.astimezone().isoformat()
                if self.letzte_regung else None
            ),
            "woran": self.woran,
            "tage_still": self.tage_still(jetzt),
            "schlaeft": self.schlaeft(jetzt),
        }


def _marken(werte: Any) -> set[str]:
    return {str(t).strip().casefold() for t in (werte or []) if str(t).strip()}


def _sicher(hole) -> list[Any]:
    """Ein fehlender oder kaputter Bereich darf das Urteil nicht kippen."""
    try:
        return list(hole() or [])
    except Exception:  # noqa: BLE001
        return []


def vorhaben(
    *,
    store: Any,
    episodes: Any = None,
    tasks: Any = None,
    workspace: Any = None,
    jetzt: datetime | None = None,
) -> list[Vorhaben]:
    """Alle geltenden Ziele mit ihrer letzten Regung, das stillste zuerst."""
    jetzt = jetzt or now()

    ziele = [a for a in store.usable(jetzt) if a.kind is Kind.GOAL]
    if not ziele:
        return []

    # Einmal einsammeln, was überhaupt eine Regung sein kann — statt für jedes
    # Ziel erneut über alles zu laufen.
    regungen: list[tuple[set[str], datetime, str]] = []

    # `all_episodes`, nicht `recent`: Gesucht ist die **letzte** Regung, und
    # die kann Monate zurückliegen. Ein Fenster von sieben Tagen ließe jedes
    # ruhende Vorhaben gleich alt aussehen.
    for episode in _sicher(lambda: episodes.all_episodes() if episodes else []):
        zeit = getattr(episode, "occurred_at", None) or getattr(episode, "recorded_at", None)
        if zeit is not None:
            regungen.append((_marken(getattr(episode, "tags", [])), zeit, "einer Notiz"))

    for aufgabe in _sicher(lambda: tasks.all_tasks() if tasks else []):
        zeit = getattr(aufgabe, "done_at", None) or getattr(aufgabe, "created_at", None)
        if zeit is not None:
            regungen.append((_marken(getattr(aufgabe, "tags", [])), zeit, "einer Aufgabe"))

    for aussage in _sicher(lambda: store.alles()):
        if aussage.kind is Kind.GOAL:
            continue
        regungen.append((_marken(aussage.tags), aussage.recorded_at, "einer Aussage"))

    ergebnis: list[Vorhaben] = []
    for ziel in ziele:
        eigene = _marken(ziel.tags)
        treffer = [(zeit, woran) for marken, zeit, woran in regungen if eigene & marken]
        if treffer:
            zeit, woran = max(treffer, key=lambda paar: paar[0])
        else:
            zeit, woran = None, None
        ergebnis.append(Vorhaben(ziel=ziel, letzte_regung=zeit, woran=woran))

    # Das stillste zuerst; was sich nicht beurteilen lässt, ganz nach hinten.
    ergebnis.sort(key=lambda v: (v.tage_still(jetzt) is None, -(v.tage_still(jetzt) or 0)))
    return ergebnis


def eingeschlafen(
    *,
    store: Any,
    episodes: Any = None,
    tasks: Any = None,
    workspace: Any = None,
    jetzt: datetime | None = None,
) -> list[Vorhaben]:
    """Nur die Vorhaben, an denen zu lange nichts geschah."""
    jetzt = jetzt or now()
    return [
        v
        for v in vorhaben(
            store=store, episodes=episodes, tasks=tasks,
            workspace=workspace, jetzt=jetzt,
        )
        if v.schlaeft(jetzt)
    ]


__all__ = ["Vorhaben", "SCHLAEFT_AB_TAGEN", "vorhaben", "eingeschlafen"]
