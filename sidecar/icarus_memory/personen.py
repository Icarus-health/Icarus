"""Menschen — abgeleitet, nicht gespeichert.

Ein Stabschef kennt die Menschen um den Chef herum. Icarus weiß längst, mit wem
seine Episoden zu tun hatten: `Episode.participants` steht seit der
Mittelfristschicht da und wurde bisher nur mitgeschleppt. Dieses Modul macht
daraus eine Ansicht — und **nur** eine Ansicht.

Es gibt hier keine Personentabelle, keinen Anlegen-Knopf, kein Feld, das jemand
pflegen müsste. Wer in einer aufgenommenen Notiz vorkommt, ist da; wer nirgends
mehr vorkommt, ist weg. Zwei Gründe:

- Eine eigene Ablage wäre eine zweite Wahrheit neben dem Bestand
  (Architekturgrenze 10 der Produktvision) — und sie müsste gepflegt werden.
  Genau die Arbeit, die das Programm dem Nutzer abnehmen soll.
- Was hier steht, ist jederzeit aus dem Rohmaterial neu herleitbar. Eine
  falsche Person verschwindet, indem man die Episode korrigiert, nicht indem
  jemand einen Datensatz aufräumt, von dem er nichts weiß.

## Zusammenführen, ohne zu raten

Gleicher Name nach `strip()`, verglichen ohne Rücksicht auf Groß- und
Kleinschreibung — mehr nicht. Kein Fuzzy-Matching, keine Vornamen-Heuristik,
kein „Dr. Meier ist vermutlich Thomas Meier“.

Zwei Einträge für einen Menschen sind ärgerlich und in einer Sekunde als
Dopplung zu erkennen. Zwei Menschen in einem Eintrag sind ein Schaden, den
niemand bemerkt — bis eine Zusage der einen Person der anderen zugeschrieben
wird. Die Asymmetrie entscheidet die Regel.

## Warum `recall()` und nicht `search()`

Die Aussagen zu einer Person kommen über `store.recall()`. Der Unterschied ist
nicht kosmetisch: `search()` liefert auch Ersetztes, Abgelaufenes und
Widerrufenes. Auf einer Personenseite stünde das dann als geltende Wahrheit
über einen Menschen — genau der Fehler, den der Widerruf verhindern soll.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Die Monatsnamen kommen aus dem Briefing statt hier ein zweites Mal zu stehen.
# Zwei Listen driften auseinander, und dann heißt derselbe Monat an zwei Stellen
# der Oberfläche verschieden.
from .briefing import MONATE
from .model import FREMDE_HERKUNFT

#: Höchstens drei Themen. Mehr ist keine Auskunft mehr, sondern eine Wolke.
MAX_THEMEN = 3

#: Wie viele Aussagen zu einer Person höchstens gezeigt werden.
MAX_AUSSAGEN = 8

#: Wie weit ins Rohmaterial zurückgesehen wird. Hoch genug, dass ein
#: gewachsener Bestand vollständig erfasst wird, begrenzt genug, dass die
#: Ableitung nicht in eine Million Zeilen läuft.
MAX_EPISODEN = 5000

#: Zahlwörter für die Tage, die als Tage ausgesprochen werden. Darüber steht
#: das Datum — „vor neunundzwanzig Tagen“ sagt kein Mensch.
ZAHLWORT = {3: "drei", 4: "vier", 5: "fünf", 6: "sechs"}


@dataclass
class Person:
    """Ein Mensch, wie er sich aus dem Bestand ergibt.

    Nichts davon wird geschrieben. Jedes Feld ist eine Antwort auf eine Frage,
    die ein Stabschef beantworten können muss, bevor sein Chef in ein Gespräch
    geht: Wer ist das, wann hatten wir zuletzt miteinander zu tun, worum ging
    es, was wissen wir, und was liegt bei ihm.
    """

    name: str
    """Die Schreibweise, die am häufigsten vorkommt."""

    episoden_anzahl: int
    letzter_kontakt: datetime | None
    tage_her: int | None

    kontakt_text: str = ""
    """Wann zuletzt Kontakt war, in Worten: „gestern“, „vor drei Tagen“,
    „am 7. August“.

    Hier und nicht in der Oberfläche, weil die Formulierung eine Entscheidung
    ist und keine Darstellung: Eine nackte Zahl („3“, „29 Tage“) zwingt den
    Leser zu rechnen, und gerechnet wird hier nicht.
    """

    themen: list[str] = field(default_factory=list)
    offene_aufgaben: list[dict[str, Any]] = field(default_factory=list)
    aussagen: list[dict[str, Any]] = field(default_factory=list)

    herkuenfte: list[str] = field(default_factory=list)
    """Aus welchen Quellenarten die gemeinsamen Episoden stammen.

    Damit kann die aufrufende Schicht entscheiden, ob dieser Mensch nur aus
    fremdem Material bekannt ist — siehe `nur_von_aussen`.
    """

    def nur_von_aussen(self) -> bool:
        """Kennt Icarus diesen Menschen ausschließlich aus fremdem Material?

        Dann ist schon der Name eine fremde Behauptung: Niemand hat bestätigt,
        dass es diese Person gibt, eine Mail hat es behauptet. Das gehört
        gekennzeichnet.

        Bewusst „alle“ und nicht „irgendeine“: Was alles markiert, markiert
        nichts. Sobald der Nutzer selbst einmal von diesem Menschen erzählt
        hat, ist er kein Fremdbefund mehr.
        """
        return bool(self.herkuenfte) and all(
            h in FREMDE_HERKUNFT for h in self.herkuenfte
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "episoden_anzahl": self.episoden_anzahl,
            "letzter_kontakt": (
                self.letzter_kontakt.astimezone().isoformat()
                if self.letzter_kontakt else None
            ),
            "tage_her": self.tage_her,
            "kontakt_text": self.kontakt_text,
            "themen": list(self.themen),
            "offene_aufgaben": list(self.offene_aufgaben),
            "aussagen": list(self.aussagen),
            "herkuenfte": list(self.herkuenfte),
            "nur_von_aussen": self.nur_von_aussen(),
        }


def schluessel(name: str) -> str:
    """Woran zwei Nennungen als derselbe Mensch erkannt werden.

    `strip()` und Groß-/Kleinschreibung, sonst nichts. Die eine Stelle, an der
    diese Regel steht — wer sie ändern will, ändert sie hier und sieht dabei,
    was oben im Modulkopf dazu steht.
    """
    return name.strip().casefold()


def wartet_auf(aufgabe: Any, name: str) -> bool:
    """Wartet diese Aufgabe auf diesen Menschen?

    Die Zuordnung steckt absichtlich in **einer** Funktion. Heute gibt es kein
    Feld dafür: Eine Aufgabe weiß nicht, auf wen sie wartet, also bleibt nur
    der Titel — „Rückfrage an Dr. Brandt“. Das ist grob, aber es ist ehrlich
    grob, und es findet genau die Aufgaben, die ein Mensch selbst so
    formuliert hat.

    Sobald `Task` ein Feld `wartet_auf` trägt, entscheidet dieses Feld, und
    zwar allein: Ein gesetztes Feld ist eine Angabe, der Titel ist eine
    Vermutung. Die Vermutung darf die Angabe nicht überstimmen. Bis dahin
    genügt es, diese Funktion zu erweitern — sonst nichts.
    """
    zugesagt = getattr(aufgabe, "wartet_auf", None)
    if zugesagt:
        return schluessel(str(zugesagt)) == schluessel(name)

    titel = getattr(aufgabe, "title", "") or ""
    return schluessel(name) in titel.casefold()


def _datum(zeit: datetime, jetzt: datetime) -> str:
    """»am 7. August« — und mit Jahr, wenn es ein anderes ist."""
    jahr = "" if zeit.year == jetzt.year else f" {zeit.year}"
    return f"am {zeit.day}. {MONATE[zeit.month - 1]}{jahr}"


def _kontakt_text(tage: int | None, zeitpunkt: datetime | None,
                  jetzt: datetime) -> str:
    """Wann zuletzt — als Satzteil, nie als Zahl."""
    if tage is None or zeitpunkt is None:
        return ""
    if tage <= 0:
        return "heute"
    if tage == 1:
        return "gestern"
    if tage == 2:
        return "vorgestern"
    if tage in ZAHLWORT:
        return f"vor {ZAHLWORT[tage]} Tagen"
    return _datum(zeitpunkt, jetzt)


def _tage_her(zeitpunkt: datetime, jetzt: datetime) -> int:
    """Ganze Kalendertage dazwischen.

    Nach Kalendertagen und nicht nach 24-Stunden-Schritten: Wer gestern Abend
    geschrieben hat, hat gestern geschrieben, auch wenn erst zwanzig Stunden
    vergangen sind.
    """
    a, b = zeitpunkt, jetzt
    if (a.tzinfo is None) != (b.tzinfo is None):
        a = a.replace(tzinfo=None)
        b = b.replace(tzinfo=None)
    return max(0, (b.date() - a.date()).days)


def _ist_fremd(herkunft: dict[str, Any] | None) -> bool:
    if not herkunft:
        return False
    return herkunft.get("source_type") in FREMDE_HERKUNFT


@dataclass
class _Rohbau:
    """Was sich beim Durchgang durch die Episoden ansammelt."""

    schreibweisen: Counter = field(default_factory=Counter)
    themen: Counter = field(default_factory=Counter)
    herkuenfte: set[str] = field(default_factory=set)
    anzahl: int = 0
    letzter: datetime | None = None
    jueng_schreibweise: str = ""


def _sammeln(episodes: Any, *, workspace: Any = None) -> dict[str, _Rohbau]:
    """Geht das Rohmaterial einmal durch und legt die Menschen zusammen."""
    projektnamen: dict[str, str] = {}
    if workspace is not None:
        # Nur den Namen, nie die Kennung: „p-3f9a1c“ ist kein Thema, das ein
        # Mensch wiedererkennt.
        for projekt in workspace.projects(include_closed=True):
            projektnamen[projekt.id] = projekt.name

    gefunden: dict[str, _Rohbau] = {}
    for episode in episodes.all_episodes(limit=MAX_EPISODEN):
        wann = episode.reference_time()
        for beteiligt in episode.participants:
            name = beteiligt.strip()
            if not name:
                continue
            eintrag = gefunden.setdefault(schluessel(name), _Rohbau())
            eintrag.schreibweisen[name] += 1
            eintrag.anzahl += 1
            eintrag.herkuenfte.add(episode.provenance.source_type.value)
            for marke in episode.tags:
                eintrag.themen[marke] += 1
            if episode.project_id and episode.project_id in projektnamen:
                eintrag.themen[projektnamen[episode.project_id]] += 1
            if eintrag.letzter is None or wann > eintrag.letzter:
                eintrag.letzter = wann
                eintrag.jueng_schreibweise = name
    return gefunden


def _haeufigste(zaehler: Counter, wieviele: int) -> list[str]:
    """Die häufigsten Einträge, bei Gleichstand alphabetisch.

    Die zweite Bedingung ist kein Schönheitsfehler: Ohne sie hängt die
    Reihenfolge an der Einlesereihenfolge, und dieselbe Person sieht bei jedem
    Aufruf anders aus.
    """
    posten = sorted(zaehler.items(), key=lambda p: (-p[1], p[0].casefold()))
    return [name for name, _ in posten[:wieviele]]


def _bauen(rohbau: _Rohbau, *, tasks: Any, store: Any, jetzt: datetime) -> Person:
    name = _haeufigste(rohbau.schreibweisen, 1)[0] if rohbau.schreibweisen else ""

    tage = _tage_her(rohbau.letzter, jetzt) if rohbau.letzter else None

    aufgaben: list[dict[str, Any]] = []
    if tasks is not None:
        aufgaben = [
            a.to_dict() for a in tasks.open_tasks(limit=300)
            if wartet_auf(a, name)
        ]

    aussagen: list[dict[str, Any]] = []
    if store is not None:
        # `recall`, nicht `search`: siehe Modulkopf.
        for aussage in store.recall(name, limit=MAX_AUSSAGEN):
            daten = aussage.to_dict()
            daten["fremd"] = _ist_fremd(daten.get("provenance"))
            aussagen.append(daten)

    return Person(
        name=name,
        episoden_anzahl=rohbau.anzahl,
        letzter_kontakt=rohbau.letzter,
        tage_her=tage,
        kontakt_text=_kontakt_text(tage, rohbau.letzter, jetzt),
        themen=_haeufigste(rohbau.themen, MAX_THEMEN),
        offene_aufgaben=aufgaben,
        aussagen=aussagen,
        herkuenfte=sorted(rohbau.herkuenfte),
    )


def alle(
    *,
    episodes: Any,
    tasks: Any = None,
    store: Any = None,
    jetzt: datetime,
    workspace: Any = None,
) -> list[Person]:
    """Alle Menschen aus dem Rohmaterial, jüngster Kontakt zuerst.

    Die Sortierung ist die Aussage der Liste: Wen ich gestern gesprochen habe,
    ist wahrscheinlicher gemeint als wer vor zwei Jahren einmal vorkam. Eine
    alphabetische Liste wäre ein Telefonbuch, und ein Telefonbuch hilft nur
    dem, der den Namen schon kennt.
    """
    gefunden = _sammeln(episodes, workspace=workspace)
    menschen = [
        _bauen(rohbau, tasks=tasks, store=store, jetzt=jetzt)
        for rohbau in gefunden.values()
    ]
    # Ohne Zeitangabe ganz nach hinten, sonst stünde ein Mensch ohne
    # Kontaktdatum vor dem, mit dem gerade gesprochen wurde.
    menschen.sort(
        key=lambda p: (p.letzter_kontakt is None, -(p.letzter_kontakt.timestamp()
                                                    if p.letzter_kontakt else 0),
                       p.name.casefold())
    )
    return menschen


def eine(
    name: str,
    *,
    episodes: Any,
    tasks: Any = None,
    store: Any = None,
    jetzt: datetime,
    workspace: Any = None,
) -> Person | None:
    """Ein Mensch, oder nichts.

    Nichts heißt: Der Name kommt in keiner Episode vor. Eine leere Person
    zurückzugeben wäre schlimmer — dann sähe die Oberfläche eine Seite über
    jemanden, von dem Icarus nichts weiß, und der Nutzer hielte die Leere für
    eine Auskunft.
    """
    gesucht = schluessel(name)
    if not gesucht:
        return None
    rohbau = _sammeln(episodes, workspace=workspace).get(gesucht)
    if rohbau is None:
        return None
    return _bauen(rohbau, tasks=tasks, store=store, jetzt=jetzt)


__all__ = ["MAX_THEMEN", "Person", "alle", "eine", "schluessel", "wartet_auf"]
