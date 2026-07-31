"""Aufnahme: aus fremden Ablagen werden Episoden.

Ein bestehender Obsidian-Vault und die Mails von heute Morgen sind derselbe
Fall — fremder Text mit einer Herkunft, aus dem vielleicht etwas folgt. Deshalb
gibt es **eine** Pipeline, nicht zwei:

    Quelle → Adapter → Episode (Digest, Herkunft) → Verdichtung → Vorschlag

## Adapter sind absichtlich dumm

Sie machen aus einer Datei eine Episode und raten nicht, was sie bedeutet. Kein
Adapter entscheidet, ob „Termin mit Dr. Meier" eine Beziehung, ein Vorhaben oder
Vergangenes ist. Die Deutung ist Sache der Verdichtung, und die legt vor, statt
zu schreiben.

Der Grund ist nicht Bequemlichkeit. Ein Adapter, der deutet, tut es nach Regeln,
die in seinem Code stehen und die niemand sieht — und schreibt damit ein
Lebensmodell fest, das für den nächsten Nutzer falsch ist. Was ein Adapter darf,
ist ablesen, was buchstäblich dasteht: ein Datum im Dateinamen, ein Feld im
Frontmatter, ein Ordnername.

## Warum das ein Produktmerkmal ist

Wer Icarus ausprobiert, muss seine bisherige Ablage nicht aufgeben, bevor er
sieht, ob es trägt. Ein Produkt, das als ersten Schritt den Umzug des ganzen
Lebens verlangt, wird nicht ausprobiert.

Dieselbe Pipeline trägt später den Dauerbetrieb: Ein Vault, der jede Nacht
erneut gelesen wird, erzeugt über die Digest-Entdopplung nur das, was wirklich
neu ist.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .episodes import EpisodeKind, EpisodeStore
from .model import Provenance, SourceType
from .security import resolve_readable_dir

#: Was gelesen wird. Alles andere wird übersprungen und gezählt — ein
#: Aufnahmelauf, der bei einem PDF abbricht, ist im Alltag unbrauchbar.
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".org", ".rst", ".csv"}

#: Obergrenze je Datei. Ein versehentlich mitgelesener Datenbankdump soll den
#: Bestand nicht fluten; die Grenze ist großzügig für echte Notizen.
MAX_FILE_BYTES = 512 * 1024

#: Ordner, die nie gelesen werden. `.obsidian` enthält die Konfiguration des
#: Programms, nicht die Notizen des Menschen.
SKIP_DIRS = {
    ".git", ".obsidian", ".trash", ".stfolder", "node_modules",
    "__pycache__", ".DS_Store", ".smart-env",
}

#: Notion hängt an jeden exportierten Dateinamen eine UUID ohne Bindestriche.
_NOTION_SUFFIX = re.compile(r"\s+[0-9a-f]{32}$")

#: Tagesnotizen heißen fast überall so. Das Datum daraus ist eine Ablesung,
#: keine Deutung — deshalb ist es hier erlaubt.
_DATE_IN_NAME = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")


@dataclass
class IngestReport:
    """Was ein Aufnahmelauf tatsächlich getan hat.

    Getrennt nach neu und bereits bekannt, weil das der Unterschied ist, den ein
    Nutzer sehen will: „847 aufgenommen, 153 schon bekannt" beruhigt, „1000
    verarbeitet" macht misstrauisch.
    """

    source: str = ""
    recorded: int = 0
    duplicates: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    episode_ids: list[str] = field(default_factory=list)

    @property
    def seen(self) -> int:
        return self.recorded + self.duplicates + self.skipped

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "recorded": self.recorded,
            "duplicates": self.duplicates,
            "skipped": self.skipped,
            "seen": self.seen,
            "errors": self.errors,
            "episode_ids": self.episode_ids,
        }

    def summary(self) -> str:
        parts = [f"{self.recorded} aufgenommen"]
        if self.duplicates:
            parts.append(f"{self.duplicates} schon bekannt")
        if self.skipped:
            parts.append(f"{self.skipped} übersprungen")
        if self.errors:
            parts.append(f"{len(self.errors)} Fehler")
        return f"{self.source}: " + ", ".join(parts) + "."


@dataclass
class RawDocument:
    """Was ein Adapter aus einer Quelle herausholt, bevor daraus eine Episode wird."""

    title: str
    body: str
    source_ref: str
    kind: EpisodeKind = EpisodeKind.DOCUMENT
    occurred_at: datetime | None = None
    participants: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


# -- Hilfsmittel ------------------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Liest YAML-Frontmatter, ohne YAML zu parsen.

    Bewusst nur flache `schlüssel: wert`-Zeilen und einfache Listen. Ein echter
    YAML-Parser wäre eine weitere Abhängigkeit für einen Randfall — und was ein
    Adapter aus dem Frontmatter braucht (Datum, Schlagworte), steht dort flach.
    Was nicht erkannt wird, bleibt einfach im Text stehen und geht nicht
    verloren.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text

    meta: dict[str, str] = {}
    key: str | None = None
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        if line.lstrip().startswith("- ") and key:
            meta[key] = (meta.get(key, "") + "," + line.lstrip()[2:].strip()).strip(",")
            continue
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            key = key.strip()
            meta[key] = value.strip().strip("\"'")
    return meta, text[match.end():]


def date_from(meta: dict[str, str], name: str) -> datetime | None:
    """Findet den Zeitpunkt, zu dem etwas gehört — abgelesen, nicht geraten.

    Reihenfolge: ausdrückliches Feld im Frontmatter, dann ein Datum im
    Dateinamen. Findet sich keins, bleibt es leer, und die Episode trägt nur
    ihren Aufnahmezeitpunkt. Ein erfundenes Datum wäre schlimmer als keins: Die
    Alterungsurteile in `currency.py` hängen daran.
    """
    for key in ("date", "datum", "created", "erstellt", "day"):
        value = meta.get(key)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    found = _DATE_IN_NAME.search(name)
    if found:
        try:
            return datetime(*(int(g) for g in found.groups()), tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def tags_from(meta: dict[str, str]) -> list[str]:
    raw = meta.get("tags") or meta.get("tag") or ""
    return [t.strip().lstrip("#") for t in raw.replace(";", ",").split(",") if t.strip()]


def clean_title(stem: str, occurred_at: datetime | None) -> str:
    """Entfernt ein führendes Datum aus dem Titel, wenn es schon erfasst ist.

    „2026-04-02 Jour fixe" wird zu „Jour fixe". Die Angabe geht nicht verloren —
    sie steht in `occurred_at`, und dort kann `currency.py` damit rechnen. Sie
    zusätzlich im Titel zu führen, macht jede Liste unlesbar.

    Nur wenn das Datum tatsächlich übernommen wurde: Sonst wäre es fort, ohne
    irgendwo anders aufzutauchen. Der Verweis auf die Datei bleibt in jedem Fall
    unangetastet — er ist der verlässliche Schlüssel, nicht der Titel.
    """
    if occurred_at is None:
        return stem
    stripped = _DATE_IN_NAME.sub("", stem, count=1).strip(" -–—_")
    return stripped or stem


def wikilinks(text: str) -> list[str]:
    """`[[Verweise]]` als Rohmaterial für Verknüpfungen.

    Nur eingesammelt, nicht aufgelöst. Ob „[[Dr. Meier]]" eine Person, ein
    Projekt oder eine Notiz ist, entscheidet nicht der Adapter.
    """
    seen: list[str] = []
    for name in _WIKILINK.findall(text):
        cleaned = name.strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen


def _readable_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            yield path


# -- Adapter ----------------------------------------------------------------


def read_markdown_vault(root: Path) -> Iterator[RawDocument | str]:
    """Obsidian-Vault oder jeder Ordner mit Markdown.

    Liefert `RawDocument` für Gelesenes und einen `str` als Begründung für
    Übersprungenes — der Aufrufer zählt beides, statt dass hier still
    weggelassen wird.
    """
    for path in _readable_files(root):
        if path.suffix.lower() not in TEXT_SUFFIXES:
            yield f"{path.name}: kein Textformat"
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            yield f"{path.name}: größer als {MAX_FILE_BYTES // 1024} KB"
            continue

        text = path.read_text(encoding="utf-8", errors="replace")
        meta, body = parse_frontmatter(text)
        if not body.strip():
            yield f"{path.name}: leer"
            continue

        relative = path.relative_to(root)
        occurred = date_from(meta, path.stem)
        yield RawDocument(
            title=meta.get("title") or clean_title(path.stem, occurred),
            body=body.strip(),
            source_ref=f"vault:{relative.as_posix()}",
            occurred_at=occurred,
            participants=wikilinks(body),
            # Der Ordnername ist eine Ablesung: In fast jedem Vault trägt die
            # Ordnerstruktur Bedeutung, und sie zu verwerfen wäre Verlust.
            tags=tags_from(meta) + [p for p in relative.parts[:-1]],
        )


def read_notion_export(root: Path) -> Iterator[RawDocument | str]:
    """Notion-Markdown-Export.

    Zwei Eigenheiten, die den eigenen Adapter rechtfertigen: An jeden
    Dateinamen ist eine UUID ohne Bindestriche angehängt, und Datenbanken kommen
    als CSV neben den Seiten. Beides würde der Vault-Adapter falsch behandeln —
    Titel voller Hexzeichen und eine CSV als Fließtext.
    """
    for path in _readable_files(root):
        suffix = path.suffix.lower()
        if suffix not in {".md", ".csv"}:
            yield f"{path.name}: kein Textformat"
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            yield f"{path.name}: größer als {MAX_FILE_BYTES // 1024} KB"
            continue

        relative = path.relative_to(root)
        stem = _NOTION_SUFFIX.sub("", path.stem).strip()

        if suffix == ".csv":
            yield from _notion_database(path, relative, stem)
            continue

        text = path.read_text(encoding="utf-8", errors="replace")
        # Notion schreibt den Titel als erste Überschrift und darunter die
        # Eigenschaften als `Schlüssel: Wert`.
        lines = text.splitlines()
        title = stem
        if lines and lines[0].startswith("# "):
            title = lines[0][2:].strip() or stem
            lines = lines[1:]

        meta: dict[str, str] = {}
        rest = list(lines)
        for index, line in enumerate(lines):
            if not line.strip():
                rest = lines[index + 1:]
                break
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip().casefold()] = value.strip()

        body = "\n".join(rest).strip()
        if not body:
            yield f"{path.name}: leer"
            continue

        yield RawDocument(
            title=title,
            body=body,
            source_ref=f"notion:{relative.as_posix()}",
            occurred_at=date_from(meta, stem),
            tags=tags_from(meta) + [_NOTION_SUFFIX.sub("", p).strip()
                                    for p in relative.parts[:-1]],
        )


def _notion_database(path: Path, relative: Path, stem: str) -> Iterator[RawDocument | str]:
    """Eine Notion-Datenbank: jede Zeile wird eine eigene Episode.

    Die ganze Tabelle als einen Text aufzunehmen wäre einfacher und falsch —
    dann liegen fünfzig Vorgänge in einer Episode, und die Verdichtung kann
    keinen davon einzeln behandeln oder verwerfen.
    """
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, csv.Error) as exc:
        yield f"{path.name}: {exc}"
        return

    if not rows:
        yield f"{path.name}: keine Zeilen"
        return

    first_column = next(iter(rows[0].keys()), "")
    for index, row in enumerate(rows):
        filled = {k: v for k, v in row.items() if k and v and v.strip()}
        if not filled:
            continue
        title = (row.get(first_column) or "").strip() or f"{stem} #{index + 1}"
        yield RawDocument(
            title=title,
            body="\n".join(f"{k}: {v}" for k, v in filled.items()),
            source_ref=f"notion:{relative.as_posix()}#{index + 1}",
            occurred_at=date_from({k.casefold(): v for k, v in filled.items()}, stem),
            tags=[stem],
        )


def read_text_files(root: Path) -> Iterator[RawDocument | str]:
    """Irgendein Ordner mit Textdateien. Keine Annahmen über Struktur."""
    for path in _readable_files(root):
        if path.suffix.lower() not in TEXT_SUFFIXES:
            yield f"{path.name}: kein Textformat"
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            yield f"{path.name}: größer als {MAX_FILE_BYTES // 1024} KB"
            continue
        body = path.read_text(encoding="utf-8", errors="replace").strip()
        if not body:
            yield f"{path.name}: leer"
            continue
        occurred = date_from({}, path.stem)
        yield RawDocument(
            title=clean_title(path.stem, occurred),
            body=body,
            source_ref=f"datei:{path.relative_to(root).as_posix()}",
            occurred_at=occurred,
        )


ADAPTERS = {
    "obsidian": read_markdown_vault,
    "markdown": read_markdown_vault,
    "notion": read_notion_export,
    "dateien": read_text_files,
}


# -- Der Lauf ---------------------------------------------------------------


def ingest_directory(
    store: EpisodeStore,
    path: str | Path,
    adapter: str = "markdown",
    roots: list[Path] | None = None,
    limit: int = 5000,
) -> IngestReport:
    """Liest einen Ordner ein und legt Episoden an.

    `roots` sind die freigegebenen Ordner. Die Prüfung ist dieselbe wie beim
    Werkzeug `datei_lesen` — die Aufnahme darf kein Weg sein, an der
    Pfadbeschränkung vorbei den ganzen Rechner zu lesen. Ohne freigegebene
    Ordner geht gar nichts, wie überall sonst auch.

    Der Inhalt gilt durchgehend als **fremd**: Eine importierte Notiz kann eine
    Anweisung enthalten, die an ein Modell gerichtet ist. Deshalb landet sie als
    Episode und nicht im Bestand, und die Verdichtung legt vor, statt zu
    schreiben.
    """
    if adapter not in ADAPTERS:
        raise ValueError(
            f"Unbekannter Adapter: {adapter}. Bekannt: {', '.join(sorted(ADAPTERS))}"
        )

    root = resolve_readable_dir(str(path), list(roots or []))
    report = IngestReport(source=f"{adapter}:{root.name}")

    for item in ADAPTERS[adapter](root):
        if report.recorded + report.duplicates >= limit:
            report.errors.append(
                f"Bei {limit} Einträgen abgebrochen — Grenze erreicht."
            )
            break
        if isinstance(item, str):
            report.skipped += 1
            continue

        try:
            episode, is_new = store.record(
                kind=item.kind,
                title=item.title,
                body=item.body,
                provenance=Provenance(
                    source_type=SourceType.DOCUMENT,
                    source_ref=item.source_ref,
                    extracted_by=f"icarus/ingest/{adapter}",
                    captured_at=datetime.now().astimezone(),
                ),
                occurred_at=item.occurred_at,
                participants=item.participants,
                tags=item.tags,
            )
        except Exception as exc:  # noqa: BLE001 - eine Datei darf den Lauf nicht kippen
            report.errors.append(f"{item.source_ref}: {exc}")
            continue

        if is_new:
            report.recorded += 1
            report.episode_ids.append(episode.id)
        else:
            report.duplicates += 1

    return report


__all__ = [
    "ADAPTERS",
    "IngestReport",
    "MAX_FILE_BYTES",
    "RawDocument",
    "TEXT_SUFFIXES",
    "clean_title",
    "date_from",
    "ingest_directory",
    "parse_frontmatter",
    "read_markdown_vault",
    "read_notion_export",
    "read_text_files",
    "tags_from",
    "wikilinks",
]
