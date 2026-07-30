"""Werkzeuge — Säule 3 und der Ausführungsteil von Säule 4.

Jedes Werkzeug deklariert seine Aktionsklasse und liefert einen **Trockenlauf**:
den vollständigen Text dessen, was passieren würde. Nicht „Mail an Team
senden?", sondern der fertige Inhalt mit Empfängerliste. Der häufigste reale
Schaden ist nicht die böswillige Aktion, sondern die plausibel klingende an den
falschen Adressaten.

Werkzeuge führen nichts von sich aus aus. Sie werden von der Registry
aufgerufen, und die geht immer durch die Policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx

from .model import Kind, Provenance, SourceType
from .policy import ActionClass


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    action_class: ActionClass
    run: Callable[..., str]
    dry_run: Callable[[dict[str, Any]], str]

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


# -- Säule 3: aktuelle Informationen ---------------------------------------


def _web_fetch(url: str, max_chars: int = 4000) -> str:
    """Holt eine Seite und gibt Text zurück."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("Nur http- und https-URLs sind erlaubt.")
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        response = client.get(url, headers={"user-agent": "Icarus/0.1"})
        response.raise_for_status()
        text = response.text

    # Sehr einfache Textextraktion — genug, um Inhalte ins Gespräch zu holen,
    # ohne eine Parser-Abhängigkeit einzuführen.
    import re

    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _read_file(path: str, max_chars: int = 4000) -> str:
    target = Path(path).expanduser()
    if not target.is_file():
        raise ValueError(f"Keine Datei: {target}")
    return target.read_text(encoding="utf-8", errors="replace")[:max_chars]


def _now(_: str = "") -> str:
    """Ohne das rät ein Modell beim Datum — und temporale Fehler sind genau die,
    die ein Langzeitgedächtnis unbrauchbar machen."""
    return datetime.now().astimezone().strftime("%A, %d.%m.%Y, %H:%M %Z")


def build_registry(store: Any, outward_sink: Callable[[dict], str] | None = None) -> dict[str, Tool]:
    """Baut die Werkzeugliste.

    `outward_sink` steht für die tatsächliche Zustellung einer außenwirksamen
    Aktion. Ohne Anbindung wird nichts verschickt — das Werkzeug existiert
    trotzdem, weil daran der Freigabeweg hängt und geprüft werden kann.
    """

    def remember(statement: str, kind: str = "state", **_: Any) -> str:
        assertion = store.record(
            statement=statement,
            kind=Kind(kind),
            provenance=Provenance(
                source_type=SourceType.CHAT,
                extracted_by="icarus/agent",
                captured_at=datetime.now().astimezone(),
            ),
            confidence=0.8,
        )
        return f"Gemerkt als {assertion.id}."

    def recall(query: str, limit: int = 5, **_: Any) -> str:
        hits = store.recall(query, limit)
        if not hits:
            return "Dazu ist nichts gespeichert."
        return "\n".join(
            f"- {a.statement} (Herkunft: {a.provenance.source_type.value})" for a in hits
        )

    def send_email(to: str, subject: str, body: str, **_: Any) -> str:
        if outward_sink is None:
            raise RuntimeError(
                "Kein Mailversand angebunden. Die Freigabe war erteilt, "
                "aber es gibt keinen Kanal."
            )
        return outward_sink({"to": to, "subject": subject, "body": body})

    tools = [
        Tool(
            name="aktuelle_zeit",
            description="Gibt das aktuelle Datum und die Uhrzeit zurück.",
            parameters={"type": "object", "properties": {}},
            action_class=ActionClass.READ,
            run=lambda **_: _now(),
            dry_run=lambda _: "Datum und Uhrzeit ablesen.",
        ),
        Tool(
            name="web_abruf",
            description="Ruft eine Webseite ab und gibt ihren Text zurück.",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Vollständige URL"}},
                "required": ["url"],
            },
            action_class=ActionClass.READ,
            run=lambda url, **_: _web_fetch(url),
            dry_run=lambda a: f"Die Seite {a.get('url')} abrufen und lesen.",
        ),
        Tool(
            name="datei_lesen",
            description="Liest eine lokale Textdatei.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Pfad zur Datei"}},
                "required": ["path"],
            },
            action_class=ActionClass.READ,
            run=lambda path, **_: _read_file(path),
            dry_run=lambda a: f"Die Datei {a.get('path')} lesen.",
        ),
        Tool(
            name="gedaechtnis_suchen",
            description="Durchsucht das Selbstmodell nach gespeicherten Aussagen.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            action_class=ActionClass.READ,
            run=recall,
            dry_run=lambda a: f"Im Gedächtnis nach {a.get('query')!r} suchen.",
        ),
        Tool(
            name="merken",
            description=(
                "Speichert eine Aussage über den Nutzer dauerhaft im Selbstmodell. "
                "Nur verwenden, wenn der Nutzer etwas über sich mitteilt."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "statement": {
                        "type": "string",
                        "description": "Die Aussage, aus Sicht des Systems über den Nutzer",
                    },
                    "kind": {
                        "type": "string",
                        "enum": [k.value for k in Kind],
                        "description": "identity und constraint sind dauerhaft, state verändert sich",
                    },
                },
                "required": ["statement"],
            },
            action_class=ActionClass.WRITE_LOCAL,
            run=remember,
            dry_run=lambda a: f"Dauerhaft merken: {a.get('statement')!r} (Art: {a.get('kind', 'state')})",
        ),
        Tool(
            name="mail_senden",
            description="Sendet eine E-Mail. Außenwirksam und nicht rückholbar.",
            parameters={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
            action_class=ActionClass.OUTWARD,
            run=send_email,
            # Vollständiger Trockenlauf: Empfänger, Betreff und der ganze Text.
            dry_run=lambda a: (
                f"E-Mail senden\n"
                f"An:      {a.get('to')}\n"
                f"Betreff: {a.get('subject')}\n"
                f"---\n{a.get('body')}"
            ),
        ),
    ]
    return {t.name: t for t in tools}


__all__ = ["Tool", "build_registry"]
