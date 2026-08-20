"""Eine Suche über alles.

Der Nutzer weiß nicht, ob „Brandt“ eine Aussage, eine Notiz, ein Projekt oder
eine Mail ist. Er weiß nur den Namen. Ihn zu fragen, in welcher Schicht er
suchen möchte, hieße, ihm die Architektur zuzumuten — genau das, was der
oberste Grundsatz verbietet.

Also: ein Feld, keine Filter, keine Syntax. Was zusammengehört, wird beim
Anzeigen gruppiert, nicht beim Suchen getrennt.

Bewusst **ohne Modell.** Etwas wiederzufinden darf nie davon abhängen, dass
ein Anbieter erreichbar ist. Die semantische Suche liegt eine Ebene höher und
ergänzt das; sie ersetzt es nicht.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Was ein fremder Text ist, entscheidet die Herkunft — nicht der Ort, an dem
# er liegt. Eine Datei vom eigenen Rechner ist genauso fremd wie eine Mail:
# jemand anderes hat sie geschrieben.
FREMDE_HERKUNFT = {"email", "calendar", "document", "web", "tool_output"}

# Reihenfolge der Gruppen in der Anzeige. Was zu tun ist, steht vor dem, was
# man weiß; Rohmaterial steht zuletzt, weil es noch kein Wissen ist.
GRUPPEN = ("aufgabe", "projekt", "aussage", "notiz", "episode")

BESCHRIFTUNG = {
    "aufgabe": "Aufgaben",
    "projekt": "Projekte",
    "aussage": "Was ich weiß",
    "notiz": "Notizen",
    "episode": "Rohmaterial",
}

# Wohin der Treffer führt. `null` heißt: dafür gibt es noch keine Ansicht —
# dann zeigt die Oberfläche keinen toten Weg an.
ZIEL = {
    "aufgabe": "dashboard",
    "projekt": "projects",
    "aussage": "memory",
    "notiz": "projects",
    "episode": "ingest",
}


@dataclass
class Treffer:
    art: str
    titel: str
    zeile: str | None
    ref: str
    fremd: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "art": self.art,
            "titel": self.titel,
            "zeile": self.zeile,
            "ref": self.ref,
            "fremd": self.fremd,
            "ziel": ZIEL.get(self.art),
        }


def _passt(text: str | None, frage: str) -> bool:
    return bool(text) and frage in text.lower()


def _kurz(text: str | None, laenge: int = 90) -> str | None:
    if not text:
        return None
    text = " ".join(text.split())
    if len(text) <= laenge:
        return text
    return text[:laenge].rsplit(" ", 1)[0] + " …"


def _ist_fremd(herkunft: dict[str, Any] | None) -> bool:
    if not herkunft:
        return False
    return herkunft.get("source_type") in FREMDE_HERKUNFT


def suche(
    frage: str,
    *,
    store: Any,
    tasks: Any,
    workspace: Any,
    episodes: Any,
    limit: int = 6,
) -> dict[str, Any]:
    """Sucht in allen Schichten und gibt gruppierte Treffer zurück.

    Jede Schicht ist einzeln fehlertolerant: hakt eine, fehlen ihre Treffer —
    die Suche liefert trotzdem, was die anderen gefunden haben. Eine Suche,
    die ganz ausfällt, weil eine Tabelle klemmt, ist nutzlos.
    """
    frage = frage.strip()
    if len(frage) < 2:
        # Ein einzelner Buchstabe trifft alles und hilft niemandem.
        return {"frage": frage, "gruppen": [], "gesamt": 0}

    klein = frage.lower()
    treffer: dict[str, list[Treffer]] = {art: [] for art in GRUPPEN}

    try:
        for aufgabe in tasks.open_tasks(limit=300):
            if _passt(aufgabe.title, klein) or _passt(aufgabe.notes, klein):
                treffer["aufgabe"].append(Treffer(
                    art="aufgabe",
                    titel=aufgabe.title,
                    zeile="überfällig" if aufgabe.is_overdue() else None,
                    ref=aufgabe.id,
                ))
    except Exception:  # noqa: BLE001 - eine Schicht darf die Suche nicht kippen
        pass

    try:
        for projekt in workspace.projects(include_closed=True):
            if _passt(projekt.name, klein) or _passt(projekt.description, klein):
                treffer["projekt"].append(Treffer(
                    art="projekt",
                    titel=projekt.name,
                    zeile=_kurz(projekt.description) or projekt.status.value,
                    ref=projekt.id,
                ))
    except Exception:  # noqa: BLE001
        pass

    try:
        # `recall` und nicht `search`: was ersetzt, abgelaufen oder widerrufen
        # ist, darf nicht als gegenwärtige Wahrheit im Ergebnis stehen.
        for aussage in store.recall(frage, limit=limit):
            daten = aussage.to_dict()
            treffer["aussage"].append(Treffer(
                art="aussage",
                titel=aussage.statement,
                zeile=None,
                ref=aussage.id,
                fremd=_ist_fremd(daten.get("provenance")),
            ))
    except Exception:  # noqa: BLE001
        pass

    try:
        for notiz in workspace.search_notes(frage, limit=limit):
            treffer["notiz"].append(Treffer(
                art="notiz",
                titel=notiz.title,
                zeile=_kurz(notiz.body),
                ref=notiz.id,
            ))
    except Exception:  # noqa: BLE001
        pass

    try:
        for episode in episodes.search(frage, limit=limit):
            treffer["episode"].append(Treffer(
                art="episode",
                titel=episode.title or "Ohne Titel",
                zeile=_kurz(episode.body),
                ref=episode.id,
                # Immer fremd, ohne Prüfung der Herkunft. Rohmaterial ist
                # aufgenommener Text, den jemand anderes geschrieben hat —
                # auch die eigene Notizdatei. Wer hier nach der Quelle
                # unterscheidet, rahmt irgendwann einen Text nicht, der es
                # gebraucht hätte.
                fremd=True,
            ))
    except Exception:  # noqa: BLE001
        pass

    gruppen = []
    gesamt = 0
    for art in GRUPPEN:
        posten = treffer[art][:limit]
        if not posten:
            continue
        gesamt += len(posten)
        gruppen.append({
            "art": art,
            "beschriftung": BESCHRIFTUNG[art],
            "treffer": [t.to_dict() for t in posten],
        })

    return {"frage": frage, "gruppen": gruppen, "gesamt": gesamt}
