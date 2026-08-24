"""Das Briefing — was heute zählt, in Sätzen.

Der Unterschied zwischen einer Übersicht und einem Briefing ist ein Urteil.
Eine Übersicht listet auf, was es gibt, und überlässt die Gewichtung dem
Leser. Ein Briefing sagt, was zuerst dran ist, und begründet es.

Dieses Modul ist die Urteilsschicht. Es erfindet nichts: jeder Satz hat eine
Quelle im Bestand, und wo nichts Dringendes ist, sagt es das in einem Satz —
statt drei Punkte zu erfinden, damit die Seite voll aussieht.

Bewusst **ohne Modell.** Ein Briefing, das nur mit eingerichtetem Anbieter
funktioniert, wäre am ersten Morgen leer. Die Regeln hier sind schlichter als
ein Modell, dafür laufen sie immer, offline, in Millisekunden — und sie sind
nachprüfbar. Ein Modell kann später die Formulierung übernehmen; die
Rangfolge sollte es nicht.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

# Mehr trägt kein Mensch am Morgen. Alles Weitere steht im Nachsatz und in den
# Ansichten dahinter — es geht nicht verloren, es drängt sich nur nicht vor.
MAX_PUNKTE = 3

MONATE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)

ZAHLWORT = {1: "Eine Sache", 2: "Zwei Dinge", 3: "Drei Dinge"}

# Ab wann eine abgegebene Sache es wert ist, den Morgen zu belegen. Wer
# gestern etwas weitergegeben hat, will nicht heute daran erinnert werden —
# das wäre kein Stabschef, sondern ein Wecker.
WARTEFRIST_TAGE = 14


@dataclass
class Punkt:
    """Ein Satz, der es in das Briefing geschafft hat."""

    text: str
    """Ganzer deutscher Satz. Keine Stichworte, keine Feldnamen."""

    gewicht: int
    """Höher heißt weiter oben. Nur zum Sortieren, nie angezeigt."""

    quelle: str
    """`aufgabe`, `termin`, `bestaetigung`, `widerspruch`, `vorschlag`, `mail`."""

    ref: str | None = None
    """Kennung des Gegenstands, damit die Oberfläche handeln kann."""

    aktion: str | None = None
    """Beschriftung des Knopfes — die Folge, nicht „OK“."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "quelle": self.quelle,
            "ref": self.ref,
            "aktion": self.aktion,
        }


@dataclass
class Briefing:
    einleitung: str
    punkte: list[Punkt] = field(default_factory=list)
    nachsatz: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "einleitung": self.einleitung,
            "punkte": [p.to_dict() for p in self.punkte],
            "nachsatz": self.nachsatz,
        }


def _tag(wert: str | datetime | None) -> datetime | None:
    """ISO-Zeichenkette oder Zeitpunkt zu einem Zeitpunkt — oder nichts."""
    if wert is None:
        return None
    if isinstance(wert, datetime):
        return wert
    try:
        return datetime.fromisoformat(str(wert).replace("Z", "+00:00"))
    except ValueError:
        return None


def _datum(zeit: datetime) -> str:
    """»7. August« — so, wie man es sagen würde."""
    return f"{zeit.day}. {MONATE[zeit.month - 1]}"


def _uhrzeit(zeit: datetime) -> str:
    return f"{zeit.hour}:{zeit.minute:02d} Uhr"


def _gleicher_tag(a: datetime, b: datetime) -> bool:
    """Vergleicht Kalendertage, nicht Zeitpunkte — und verträgt gemischte Zonen."""
    if (a.tzinfo is None) != (b.tzinfo is None):
        a = a.replace(tzinfo=None)
        b = b.replace(tzinfo=None)
    return a.date() == b.date()


def _ueberfaellige(aufgaben: list[dict], jetzt: datetime) -> list[tuple[datetime, dict]]:
    """Überfälliges, das Älteste zuerst. Es hat am längsten gewartet."""
    treffer: list[tuple[datetime, dict]] = []
    for aufgabe in aufgaben:
        if not aufgabe.get("overdue"):
            continue
        faellig = _tag(aufgabe.get("due"))
        if faellig is not None:
            treffer.append((faellig, aufgabe))
    treffer.sort(key=lambda paar: paar[0])
    return treffer


def _bei(name: str) -> str:
    """„bei Herrn Ohlsen“, nicht „bei Herr Ohlsen“.

    Nur die Anrede wird gebeugt, nie der Name selbst — bei fremden Namen ist
    jede Regel eine Wette, und ein falsch gebeugter Name liest sich schlimmer
    als ein ungebeugter.
    """
    return f"Herrn {name[5:]}" if name.startswith("Herr ") else name


def _lange_wartend(aufgaben: list[dict], jetzt: datetime) -> list[dict]:
    """Was bei anderen liegt, und zwar lange genug. Am längsten zuerst."""
    treffer = [
        a for a in aufgaben
        if a.get("wartet_auf") and (a.get("wartet_tage") or 0) >= WARTEFRIST_TAGE
    ]
    treffer.sort(key=lambda a: a.get("wartet_tage") or 0, reverse=True)
    return treffer


def _naechster_termin(termine: list[dict], jetzt: datetime) -> tuple[datetime, dict] | None:
    """Der nächste Termin von heute, der noch bevorsteht."""
    kommend: list[tuple[datetime, dict]] = []
    for termin in termine:
        beginn = _tag(termin.get("start"))
        if beginn is None or not _gleicher_tag(beginn, jetzt):
            continue
        vergleich = beginn if beginn.tzinfo == jetzt.tzinfo else beginn.replace(tzinfo=jetzt.tzinfo)
        if vergleich < jetzt:
            continue
        kommend.append((beginn, termin))
    kommend.sort(key=lambda paar: paar[0])
    return kommend[0] if kommend else None


def _heute_faellig(aufgaben: list[dict], jetzt: datetime) -> list[dict]:
    treffer = []
    for aufgabe in aufgaben:
        if aufgabe.get("overdue"):
            continue
        faellig = _tag(aufgabe.get("due"))
        if faellig is not None and _gleicher_tag(faellig, jetzt):
            treffer.append(aufgabe)
    return treffer


def _kurz(satz: str, laenge: int = 74) -> str:
    """Kürzt auf Wortgrenze, damit ein langer Satz die Zeile nicht sprengt."""
    satz = " ".join(satz.split())
    if len(satz) <= laenge:
        return satz
    return satz[:laenge].rsplit(" ", 1)[0].rstrip(",;:.") + " …"


def _zitat(satz: str, laenge: int = 74) -> str:
    """Wie `_kurz`, aber für ein Zitat mitten im Satz.

    Der Schlusspunkt der zitierten Aussage muss weg: „… Ziffer 20.“ hast du
    mir gesagt — das ist ein Punkt zu viel und liest sich wie ein Stolpern.
    """
    return _kurz(satz, laenge).rstrip(".")


def erstelle(
    daten: dict[str, Any],
    *,
    jetzt: datetime,
    vorschlaege: list[dict[str, Any]] | None = None,
) -> Briefing:
    """Aus dem Stand der Dinge ein Briefing.

    `daten` ist die Antwort von `/dashboard`, `vorschlaege` die offenen
    Vorschläge. Beides wird nur gelesen — dieses Modul ändert nichts und legt
    nichts an.
    """
    vorschlaege = vorschlaege or []
    kandidaten: list[Punkt] = []
    uebrig: list[str] = []

    aufgaben = daten.get("tasks", {}).get("items", []) or []

    # 1. Überfälliges. Nichts wiegt schwerer als etwas, das schon wartet.
    ueberfaellig = _ueberfaellige(aufgaben, jetzt)
    if ueberfaellig:
        faellig, aufgabe = ueberfaellig[0]
        weitere = len(ueberfaellig) - 1
        anhang = " Dahinter wartet noch eine." if weitere == 1 else (
            f" Dahinter warten noch {weitere} weitere." if weitere > 1 else ""
        )
        kandidaten.append(Punkt(
            text=(
                f"Seit dem {_datum(faellig)} wartet „{_kurz(aufgabe.get('title', ''))}“ "
                f"auf dich.{anhang}"
            ),
            gewicht=100,
            quelle="aufgabe",
            ref=aufgabe.get("id"),
            aktion="Erledigt",
        ))

    # 2. Was bei anderen liegt und dort zu lange liegt. Ein Stabschef fasst
    # nach; er wartet nicht darauf, dass die andere Seite von selbst einfällt.
    wartend = _lange_wartend(aufgaben, jetzt)
    if wartend:
        aufgabe = wartend[0]
        seit = _tag(aufgabe.get("wartet_seit"))
        wann = f"Seit dem {_datum(seit)}" if seit else "Seit Längerem"
        kandidaten.append(Punkt(
            text=(
                f"{wann} liegt „{_kurz(aufgabe.get('title', ''))}“ "
                f"bei {_bei(aufgabe.get('wartet_auf') or '')}."
            ),
            gewicht=95,
            quelle="wartet",
            ref=aufgabe.get("id"),
            aktion="Zurückholen",
        ))

    # 3. Der nächste Termin. Er hat eine Uhrzeit — er wartet nicht.
    naechster = _naechster_termin(daten.get("calendar", {}).get("items", []) or [], jetzt)
    if naechster is not None:
        beginn, termin = naechster
        titel = _kurz(termin.get("summary") or "Termin")
        ort = termin.get("location")
        zusatz = f" ({_kurz(ort, 40)})" if ort else ""
        kandidaten.append(Punkt(
            text=f"Um {_uhrzeit(beginn)} ist „{titel}“{zusatz}.",
            gewicht=90,
            quelle="termin",
            ref=termin.get("uid"),
            aktion="Vorbereiten",
        ))

    # 4. Wissen, das sein Verfallsdatum überschritten hat. Ein Fakt aus dem
    #    Mai darf nicht stillschweigend als Gegenwart gelten.
    bestaetigungen = [v for v in vorschlaege if v.get("kind") == "confirmation"]
    if bestaetigungen:
        erster = bestaetigungen[0]
        weitere = len(bestaetigungen) - 1
        anhang = " Zwei weitere Angaben sind ebenso alt." if weitere == 2 else (
            " Eine weitere Angabe ist ebenso alt." if weitere == 1 else (
                f" {weitere} weitere Angaben sind ebenso alt." if weitere > 2 else ""
            )
        )
        kandidaten.append(Punkt(
            text=(
                f"„{_zitat(erster.get('statement', ''))}“ hast du mir gesagt und "
                f"seitdem nicht bestätigt. Gilt das noch?{anhang}"
            ),
            gewicht=70,
            quelle="bestaetigung",
            ref=erster.get("id"),
            aktion="Gilt noch",
        ))

    # 5. Widersprüche. Zwei Sätze, die nicht beide stimmen können.
    widersprueche = [v for v in vorschlaege if v.get("kind") == "conflict"]
    if widersprueche:
        kandidaten.append(Punkt(
            text=(
                f"„{_zitat(widersprueche[0].get('statement', ''))}“ widerspricht "
                f"etwas anderem, das ich von dir habe."
            ),
            gewicht=65,
            quelle="widerspruch",
            ref=widersprueche[0].get("id"),
            aktion="Ansehen",
        ))

    # 6. Was heute fällig ist, aber noch nicht überfällig.
    heute = _heute_faellig(aufgaben, jetzt)
    if heute:
        if len(heute) == 1:
            text = f"Heute fällig: „{_kurz(heute[0].get('title', ''))}“."
        else:
            text = f"Heute sind {len(heute)} Aufgaben fällig."
        kandidaten.append(Punkt(
            text=text,
            gewicht=60,
            quelle="aufgabe",
            ref=heute[0].get("id") if len(heute) == 1 else None,
            aktion="Ansehen",
        ))

    # 7. Ungelesene Post. Fremder Inhalt — nur die Zahl, nie der Inhalt.
    ungelesen = daten.get("mail", {}).get("unread", 0) or 0
    if ungelesen:
        kandidaten.append(Punkt(
            text=(
                "Eine ungelesene Nachricht liegt im Postfach."
                if ungelesen == 1
                else f"{ungelesen} ungelesene Nachrichten liegen im Postfach."
            ),
            gewicht=40,
            quelle="mail",
            ref=None,
            aktion="Ansehen",
        ))

    kandidaten.sort(key=lambda p: p.gewicht, reverse=True)
    punkte = kandidaten[:MAX_PUNKTE]

    for rest in kandidaten[MAX_PUNKTE:]:
        uebrig.append(rest.text)

    # Der Nachsatz: was ich gelernt habe und was noch ungelesen herumliegt.
    # Beides ist kein Grund zur Eile, aber es darf nicht verschwinden.
    neue = [v for v in vorschlaege if v.get("kind") == "assertion"]
    if neue:
        uebrig.append(
            "Aus deinen Notizen habe ich eine Sache herausgelesen, die noch "
            "nirgends steht."
            if len(neue) == 1
            else f"Aus deinen Notizen habe ich {len(neue)} Dinge herausgelesen, "
                 "die noch nirgends stehen."
        )

    roh = daten.get("episodes", {}).get("pending", 0) or 0
    if roh:
        uebrig.append(
            f"{roh} aufgenommene Notizen habe ich noch nicht angesehen."
            if roh > 1
            else "Eine aufgenommene Notiz habe ich noch nicht angesehen."
        )

    if punkte:
        wieviel = ZAHLWORT.get(len(punkte), f"{len(punkte)} Dinge")
        einleitung = f"{wieviel} {'ist' if len(punkte) == 1 else 'sind'} heute wichtig."
    elif uebrig:
        einleitung = "Nichts Dringendes heute."
    else:
        # Der ehrlichste Fall. Kein erfundener Punkt, damit die Seite voll ist.
        einleitung = "Nichts Dringendes heute. Ich melde mich, wenn sich das ändert."

    return Briefing(
        einleitung=einleitung,
        punkte=punkte,
        nachsatz=" ".join(uebrig) if uebrig else None,
    )
