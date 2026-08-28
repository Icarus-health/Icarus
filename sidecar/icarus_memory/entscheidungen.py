"""Entscheidungen und ihre Annahmen.

Ein Archiv kann sagen, was war. Ein Ratgeber sagt:

> Du hast dich damals für X entschieden, weil Y galt. Y gilt seit gestern
> nicht mehr.

Das ist der Unterschied, und er kostet erstaunlich wenig: Eine Entscheidung
ist eine Aussage der Art `DECISION`, und über das bereits vorhandene
`derived_from` zeigt sie auf die Aussagen, auf denen sie stand. Fällt eine
davon — ersetzt, widerrufen, abgelaufen —, ist die Entscheidung
**erschüttert**.

## Warum „erschüttert“ und nicht „ungültig“

Eine gefallene Annahme macht eine Entscheidung nicht falsch. Sie macht sie
*prüfenswert*. Der Vertrag, den man wegen einer Frist unterschrieben hat, ist
nach dem Wegfall der Frist nicht automatisch ein Fehler — vielleicht war er
ohnehin richtig.

Deshalb schreibt dieses Modul **nichts**. Es ändert keinen Status, es legt
nichts an, es widerruft nichts. Es liest den Bestand und stellt eine Frage.
Wer die Entscheidung revidieren will, tut das selbst — und dann steht die
Revision als eigene Entscheidung daneben, mit ihrer eigenen Grundlage.

## Warum nicht die Uhr

Eine Entscheidung veraltet nicht dadurch, dass sie alt ist. Ein Beschluss von
2019, dessen Grundlage steht, ist so gültig wie einer von gestern. Deshalb
zählt hier allein, ob die Grundlage steht — `currency.py` gibt der Art
`DECISION` bewusst kein Verfallsdatum.

## Warum ein Zeitfenster für das Briefing

Eine erschütterte Entscheidung, die seit einem halben Jahr erschüttert ist,
hat ihre Nachricht ausgerichtet. Sie jeden Morgen zu wiederholen, wäre kein
Rat, sondern ein Vorwurf. Fürs Briefing zählt deshalb nur, was **kürzlich**
gefallen ist; in der Ansicht steht weiterhin alles.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .model import Assertion, Kind, Status, now

#: Wie lange eine gefallene Annahme den Morgen belegen darf.
FRISCH_TAGE = 30

#: Diese Zustände heißen: die Annahme trägt nicht mehr.
GEFALLEN = {
    Status.SUPERSEDED,
    Status.RETRACTED,
    Status.EXPIRED,
    Status.REDACTED,
}

#: Und dieser heißt: sie ist strittig. Auch das ist kein Fundament, aber es
#: ist ein anderes Wort wert — strittig ist nicht widerlegt.
STRITTIG = Status.DISPUTED


@dataclass
class Wackler:
    """Eine Annahme, die unter einer Entscheidung weggerutscht ist."""

    annahme: Assertion
    """Die Aussage, auf der die Entscheidung stand."""

    ersetzt_durch: Assertion | None = None
    """Was an ihre Stelle getreten ist, falls etwas."""

    @property
    def strittig(self) -> bool:
        return self.annahme.status is STRITTIG

    def to_dict(self) -> dict[str, Any]:
        return {
            "annahme": self.annahme.statement,
            "annahme_id": self.annahme.id,
            "status": self.annahme.status.value,
            "strittig": self.strittig,
            "ersetzt_durch": (
                self.ersetzt_durch.statement if self.ersetzt_durch else None
            ),
            "ersetzt_durch_id": (
                self.ersetzt_durch.id if self.ersetzt_durch else None
            ),
        }


@dataclass
class Entscheidung:
    """Eine Entscheidung mit dem Stand ihrer Grundlage."""

    aussage: Assertion
    grundlage: list[Assertion]
    wackler: list[Wackler]

    @property
    def id(self) -> str:
        return self.aussage.id

    @property
    def satz(self) -> str:
        return self.aussage.statement

    @property
    def erschuettert(self) -> bool:
        return bool(self.wackler)

    @property
    def ohne_grundlage(self) -> bool:
        """Festgehalten, ohne zu sagen, worauf sie stand.

        Kein Fehler — nicht jede Entscheidung hat eine benennbare Annahme.
        Aber dann kann Icarus auch nicht merken, wenn sie wegfällt, und das
        soll die Ansicht sagen dürfen statt so zu tun, als wäre alles geprüft.
        """
        return not self.grundlage

    def gefallen_am(self) -> datetime | None:
        """Wann die jüngste Annahme gefallen ist."""
        zeiten = [_gefallen_am(w) for w in self.wackler]
        return max(zeiten) if zeiten else None

    def frisch_erschuettert(self, jetzt: datetime) -> bool:
        """Ist die Erschütterung neu genug, um den Morgen zu belegen?

        Alte Datensätze ohne eigenen Statuswechselzeitpunkt erhalten in
        `_gefallen_am()` einen endlichen, konservativen Fallback. Eine
        Entscheidung darf nie unbegrenzt jeden Morgen neu erscheinen.
        """
        if not self.erschuettert:
            return False
        wann = self.gefallen_am()
        if wann is None:
            return False
        return (jetzt - wann) <= timedelta(days=FRISCH_TAGE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "satz": self.satz,
            "getroffen_am": self.aussage.recorded_at.astimezone().isoformat(),
            "status": self.aussage.status.value,
            "erschuettert": self.erschuettert,
            "ohne_grundlage": self.ohne_grundlage,
            "grundlage": [
                {"id": a.id, "satz": a.statement, "status": a.status.value}
                for a in self.grundlage
            ],
            "wackler": [w.to_dict() for w in self.wackler],
        }


def _ersatz(annahme: Assertion, nach_id: dict[str, Assertion]) -> Assertion | None:
    if annahme.superseded_by:
        return nach_id.get(annahme.superseded_by)
    return None


def _gefallen_am(wackler: Wackler) -> datetime:
    """Der fachliche Zeitpunkt, an dem eine Annahme nicht mehr trug.

    Neue Datensätze tragen `status_changed_at`. Die folgenden Fallbacks halten
    alte SQLite-Dateien und Exporte lesbar, ohne einen unbekannten Zeitpunkt
    für immer als frisch zu behandeln. Der Replacement-Zeitpunkt ist bei
    älteren Supersessions exakt, ebenso Redaction und Expiry. Für alte Retracts
    und Disputes bleibt nur die Aufnahmezeit der Annahme als sichere Untergrenze.
    """
    annahme = wackler.annahme
    if annahme.status_changed_at is not None:
        return annahme.status_changed_at
    if wackler.ersetzt_durch is not None:
        return wackler.ersetzt_durch.recorded_at
    if annahme.redaction is not None:
        return annahme.redaction.redacted_at
    if annahme.status is Status.EXPIRED and annahme.expires_at is not None:
        return annahme.expires_at
    return annahme.recorded_at


def alle(store: Any, *, jetzt: datetime | None = None) -> list[Entscheidung]:
    """Alle Entscheidungen, erschütterte zuerst, danach die jüngsten.

    Liest über `store.alles()` und nicht über `recall()`: Eine widerrufene
    Entscheidung soll in der Ansicht stehen bleiben. Sie gilt nicht mehr, aber
    dass sie einmal getroffen wurde, bleibt wahr — und genau das ist der Wert
    eines Gedächtnisses, das nichts löscht.
    """
    jetzt = jetzt or now()
    bestand = list(store.alles())
    nach_id = {a.id: a for a in bestand}

    ergebnis: list[Entscheidung] = []
    for aussage in bestand:
        if aussage.kind is not Kind.DECISION:
            continue

        grundlage = [nach_id[ref] for ref in aussage.derived_from if ref in nach_id]
        wackler = [
            Wackler(annahme=a, ersetzt_durch=_ersatz(a, nach_id))
            for a in grundlage
            if a.status in GEFALLEN or a.status is STRITTIG
        ]
        ergebnis.append(
            Entscheidung(aussage=aussage, grundlage=grundlage, wackler=wackler)
        )

    ergebnis.sort(
        key=lambda e: (not e.erschuettert, -e.aussage.recorded_at.timestamp())
    )
    return ergebnis


def erschuettert(store: Any, *, jetzt: datetime | None = None) -> list[Entscheidung]:
    """Nur die, unter denen etwas weggerutscht ist — und zwar kürzlich.

    Das ist die Liste, die das Briefing liest. Alles andere steht in der
    Ansicht.
    """
    jetzt = jetzt or now()
    treffer = [
        e
        for e in alle(store, jetzt=jetzt)
        if e.frisch_erschuettert(jetzt) and e.aussage.status is Status.ACTIVE
    ]
    # Die jüngste Erschütterung zuerst — sie ist die, von der man noch nichts
    # weiß.
    treffer.sort(key=lambda e: e.gefallen_am() or e.aussage.recorded_at, reverse=True)
    return treffer


__all__ = ["Entscheidung", "Wackler", "FRISCH_TAGE", "alle", "erschuettert"]
