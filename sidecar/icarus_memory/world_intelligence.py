"""Belegte Außenwelt-Intelligenz für Icarus.

Externe Quellen sind Rohmaterial, nie Autorität. Ein Abruf kann neue Episoden
anlegen und Relevanz zu vorhandenen Projekten sichtbar machen, aber niemals
eine Aussage in das Selbstmodell schreiben.

Die erste produktive Quelle sind RSS- und Atom-Feeds. Sie sind offen,
providerunabhängig und liefern Veröffentlichungszeit sowie Original-URL. Die
Netzwerkgrenze prüft Ausgangs- und Weiterleitungsziele gegen die bestehende
SSRF-Sperre. XML mit DTD oder Entitäten wird abgewiesen und die Antwortgröße ist
begrenzt.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import secrets
import sqlite3
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated, Any, Callable, Iterable, Protocol
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from starlette.routing import Mount

from .episodes import EpisodeKind
from .model import Provenance, SourceType
from .proactive import AttentionSignal
from .security import MAX_FETCH_BYTES, check_url, wrap_untrusted

TOKEN_ENV = "ICARUS_SIDECAR_TOKEN"
_WORDS = re.compile(r"[\wÄÖÜäöüß-]+", re.UNICODE)
_STOPWORDS = {
    "und", "oder", "der", "die", "das", "ein", "eine", "mit", "für",
    "von", "zur", "zum", "im", "in", "am", "an", "auf", "the", "and",
    "with", "from", "this", "that", "news", "update",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _tokens(*values: Any) -> set[str]:
    result: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            result.update(_tokens(*value))
            continue
        for token in _WORDS.findall(str(value).casefold()):
            if len(token) >= 3 and token not in _STOPWORDS:
                result.add(token)
    return result


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.parts.append(text)


def _plain_text(value: str | None, *, limit: int = 8000) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html.unescape(value))
        text = " ".join(parser.parts)
    except Exception:
        text = " ".join(value.split())
    return text[:limit]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _first_text(parent: ET.Element, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for child in list(parent):
        if _local_name(child.tag) in wanted:
            return "".join(child.itertext()).strip()
    return ""


def _atom_link(entry: ET.Element) -> str:
    fallback = ""
    for child in list(entry):
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href", "").strip()
        if not href:
            continue
        relation = child.attrib.get("rel", "alternate").casefold()
        if relation == "alternate":
            return href
        fallback = fallback or href
    return fallback


@dataclass(frozen=True)
class FeedItem:
    key: str
    title: str
    url: str
    summary: str
    published_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        document["published_at"] = _iso(self.published_at)
        return document


def parse_feed(payload: bytes, source_url: str) -> list[FeedItem]:
    if len(payload) > MAX_FETCH_BYTES:
        raise ValueError("Feed ist größer als die erlaubte Antwortgrenze")
    upper = payload[:100_000].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("Feeds mit DTD oder XML-Entitäten sind nicht erlaubt")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError(f"Ungültiger RSS-/Atom-Feed: {exc}") from exc

    root_name = _local_name(root.tag)
    entries: list[FeedItem] = []
    if root_name in {"rss", "rdf"}:
        candidates = [node for node in root.iter() if _local_name(node.tag) == "item"]
        for item in candidates:
            title = _plain_text(_first_text(item, "title"), limit=500) or "Ohne Titel"
            link = _first_text(item, "link")
            link = urljoin(source_url, link) if link else source_url
            guid = _first_text(item, "guid", "id") or link or title
            summary = _plain_text(
                _first_text(item, "description", "encoded", "content", "summary")
            )
            published = _parse_date(
                _first_text(item, "pubdate", "date", "published", "updated")
            )
            entries.append(FeedItem(guid, title, link, summary, published))
    elif root_name == "feed":
        candidates = [node for node in list(root) if _local_name(node.tag) == "entry"]
        for item in candidates:
            title = _plain_text(_first_text(item, "title"), limit=500) or "Ohne Titel"
            link = _atom_link(item)
            link = urljoin(source_url, link) if link else source_url
            key = _first_text(item, "id") or link or title
            summary = _plain_text(_first_text(item, "summary", "content"))
            published = _parse_date(_first_text(item, "published", "updated"))
            entries.append(FeedItem(key, title, link, summary, published))
    else:
        raise ValueError("Quelle ist weder ein RSS- noch ein Atom-Feed")

    deduplicated: dict[str, FeedItem] = {}
    for item in entries:
        identity = item.key or item.url or f"{item.title}:{_iso(item.published_at)}"
        deduplicated[identity] = item
    return list(deduplicated.values())


@dataclass(frozen=True)
class FetchResult:
    status: int
    url: str
    body: bytes
    etag: str | None = None
    last_modified: str | None = None


class FeedClient(Protocol):
    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult: ...


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked = check_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, checked)


class SafeFeedClient:
    def __init__(self, *, timeout: float = 15.0) -> None:
        self.timeout = timeout
        self.opener = build_opener(_SafeRedirect())

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        checked = check_url(url)
        headers = {
            "User-Agent": "Icarus/0.1 (+local personal assistant)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9",
        }
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        request = Request(checked, headers=headers)
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except HTTPError as exc:
            if exc.code == 304:
                return FetchResult(304, checked, b"", etag, last_modified)
            raise
        with response:
            final_url = check_url(response.geturl())
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_FETCH_BYTES:
                raise ValueError("Feed ist größer als die erlaubte Antwortgrenze")
            body = response.read(MAX_FETCH_BYTES + 1)
            if len(body) > MAX_FETCH_BYTES:
                raise ValueError("Feed ist größer als die erlaubte Antwortgrenze")
            return FetchResult(
                int(getattr(response, "status", 200)),
                final_url,
                body,
                response.headers.get("ETag"),
                response.headers.get("Last-Modified"),
            )


class WorldStore:
    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS world_sources (
        id               TEXT PRIMARY KEY,
        name             TEXT NOT NULL,
        url              TEXT NOT NULL UNIQUE,
        project_id       TEXT,
        enabled          INTEGER NOT NULL,
        created_at       TEXT NOT NULL,
        updated_at       TEXT NOT NULL,
        last_checked_at  TEXT,
        etag             TEXT,
        last_modified    TEXT,
        last_error       TEXT
    );
    CREATE TABLE IF NOT EXISTS world_items (
        id                 TEXT PRIMARY KEY,
        source_id          TEXT NOT NULL,
        item_key           TEXT NOT NULL,
        title              TEXT NOT NULL,
        url                TEXT NOT NULL,
        summary            TEXT NOT NULL,
        published_at       TEXT,
        episode_id         TEXT,
        project_id         TEXT,
        relevance_score    INTEGER NOT NULL,
        matched_terms_json TEXT NOT NULL,
        first_seen_at      TEXT NOT NULL,
        last_seen_at       TEXT NOT NULL,
        is_new             INTEGER NOT NULL,
        UNIQUE(source_id, item_key)
    );
    CREATE INDEX IF NOT EXISTS world_items_new
        ON world_items(is_new, relevance_score, published_at);
    CREATE INDEX IF NOT EXISTS world_items_source
        ON world_items(source_id, published_at);
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self._connect() as db:
            db.executescript(self._SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 10000")
        return db

    def add_source(self, name: str, url: str, project_id: str | None = None) -> dict[str, Any]:
        source_id = f"world-{uuid.uuid4().hex[:12]}"
        timestamp = _now().isoformat()
        with self._connect() as db:
            try:
                db.execute(
                    """
                    INSERT INTO world_sources(
                        id, name, url, project_id, enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (source_id, name.strip(), url, project_id, timestamp, timestamp),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Diese Quelle ist bereits eingetragen") from exc
        return self.source(source_id)

    def source(self, source_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM world_sources WHERE id = ?", (source_id,)
            ).fetchone()
        if row is None:
            raise KeyError(source_id)
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        return result

    def sources(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM world_sources ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item["enabled"])
            result.append(item)
        return result

    def delete_source(self, source_id: str) -> None:
        with self._connect() as db:
            exists = db.execute(
                "SELECT 1 FROM world_sources WHERE id = ?", (source_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(source_id)
            db.execute("DELETE FROM world_items WHERE source_id = ?", (source_id,))
            db.execute("DELETE FROM world_sources WHERE id = ?", (source_id,))

    def update_check(
        self,
        source_id: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                UPDATE world_sources
                SET last_checked_at = ?, etag = COALESCE(?, etag),
                    last_modified = COALESCE(?, last_modified),
                    last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    _now().isoformat(),
                    etag,
                    last_modified,
                    error,
                    _now().isoformat(),
                    source_id,
                ),
            )

    def upsert_item(
        self,
        source_id: str,
        item: FeedItem,
        *,
        episode_id: str | None,
        project_id: str | None,
        relevance_score: int,
        matched_terms: Iterable[str],
    ) -> tuple[dict[str, Any], bool]:
        item_id = "world-item-" + hashlib.sha256(
            f"{source_id}\0{item.key}".encode("utf-8")
        ).hexdigest()[:20]
        timestamp = _now().isoformat()
        with self._connect() as db:
            existing = db.execute(
                "SELECT id FROM world_items WHERE source_id = ? AND item_key = ?",
                (source_id, item.key),
            ).fetchone()
            is_new = existing is None
            db.execute(
                """
                INSERT INTO world_items(
                    id, source_id, item_key, title, url, summary,
                    published_at, episode_id, project_id, relevance_score,
                    matched_terms_json, first_seen_at, last_seen_at, is_new
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, item_key) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    summary = excluded.summary,
                    published_at = excluded.published_at,
                    episode_id = COALESCE(world_items.episode_id, excluded.episode_id),
                    project_id = excluded.project_id,
                    relevance_score = excluded.relevance_score,
                    matched_terms_json = excluded.matched_terms_json,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    item_id,
                    source_id,
                    item.key,
                    item.title,
                    item.url,
                    item.summary,
                    _iso(item.published_at),
                    episode_id,
                    project_id,
                    int(relevance_score),
                    json.dumps(sorted(set(matched_terms)), ensure_ascii=False),
                    timestamp,
                    timestamp,
                    1 if is_new else 0,
                ),
            )
        return self.item(item_id), is_new

    def item(self, item_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT i.*, s.name AS source_name
                FROM world_items i JOIN world_sources s ON s.id = i.source_id
                WHERE i.id = ?
                """,
                (item_id,),
            ).fetchone()
        if row is None:
            raise KeyError(item_id)
        return self._row_item(row)

    @staticmethod
    def _row_item(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["is_new"] = bool(result["is_new"])
        result["matched_terms"] = json.loads(result.pop("matched_terms_json"))
        return result

    def items(
        self,
        *,
        limit: int = 50,
        new_only: bool = False,
        relevant_only: bool = False,
    ) -> list[dict[str, Any]]:
        conditions = []
        if new_only:
            conditions.append("i.is_new = 1")
        if relevant_only:
            conditions.append("i.relevance_score > 0")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT i.*, s.name AS source_name
                FROM world_items i JOIN world_sources s ON s.id = i.source_id
                {where}
                ORDER BY i.is_new DESC, i.relevance_score DESC,
                         COALESCE(i.published_at, i.first_seen_at) DESC, i.id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_item(row) for row in rows]

    def mark_seen(self, item_id: str) -> dict[str, Any]:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE world_items SET is_new = 0 WHERE id = ?", (item_id,)
            )
            if cursor.rowcount != 1:
                raise KeyError(item_id)
        return self.item(item_id)


class WorldEngine:
    def __init__(
        self,
        app: FastAPI,
        store: WorldStore,
        client: FeedClient | None = None,
    ) -> None:
        self.app = app
        self.store = store
        self.client = client or SafeFeedClient()

    def _relevance(self, source: dict[str, Any], item: FeedItem) -> tuple[str | None, int, list[str]]:
        explicit = source.get("project_id")
        item_tokens = _tokens(item.title, item.summary)
        if explicit:
            return str(explicit), 100, sorted(item_tokens)[:12]

        best_project = None
        best_score = 0
        best_terms: set[str] = set()
        for project in self.app.state.workspace.projects(include_closed=False, limit=10000):
            overlap = item_tokens & _tokens(
                project.name,
                project.area,
                project.description,
                project.tags,
            )
            score = len(overlap) * 12
            if project.name.casefold() in item.title.casefold():
                score += 45
            if score > best_score:
                best_project = project.id
                best_score = score
                best_terms = overlap

        goal_terms: set[str] = set()
        for assertion in self.app.state.store.export().assertions:
            if assertion.kind.value != "goal" or not assertion.is_usable():
                continue
            goal_terms.update(item_tokens & _tokens(assertion.statement, assertion.tags))
        best_score += len(goal_terms) * 4
        return best_project, best_score, sorted(best_terms | goal_terms)

    def refresh(self, source_id: str) -> dict[str, Any]:
        try:
            source = self.store.source(source_id)
        except KeyError as exc:
            raise LookupError("Quelle nicht gefunden") from exc
        try:
            result = self.client.fetch(
                source["url"],
                etag=source.get("etag"),
                last_modified=source.get("last_modified"),
            )
            if result.status == 304:
                self.store.update_check(source_id, error=None)
                return {"source_id": source_id, "unchanged": True, "fetched": 0, "new": 0, "relevant": 0}
            items = parse_feed(result.body, result.url)
            created = 0
            relevant = 0
            duplicates = 0
            for item in items:
                project_id, score, terms = self._relevance(source, item)
                if score > 0:
                    relevant += 1
                body = wrap_untrusted(
                    "\n\n".join(
                        part
                        for part in (
                            item.title,
                            item.summary,
                            f"Originalquelle: {item.url}",
                        )
                        if part
                    ),
                    item.url or result.url,
                )
                episode, episode_new = self.app.state.episodes.record(
                    EpisodeKind.DOCUMENT,
                    item.title,
                    body,
                    Provenance(
                        SourceType.WEB,
                        source_ref=item.url or result.url,
                        captured_at=_now(),
                        verbatim=item.summary[:1000] or None,
                    ),
                    occurred_at=item.published_at,
                    project_id=project_id,
                    tags=["world", f"world-source:{source_id}"],
                )
                _, item_new = self.store.upsert_item(
                    source_id,
                    item,
                    episode_id=episode.id,
                    project_id=project_id,
                    relevance_score=score,
                    matched_terms=terms,
                )
                if item_new:
                    created += 1
                else:
                    duplicates += 1
                # Episode-Dedup und Feed-Dedup sind zwei unterschiedliche
                # Sicherungen. Ein bekannter Text aus einer neuen Feedkennung
                # bleibt im Weltindex nachvollziehbar, erzeugt aber keine zweite Episode.
                _ = episode_new
            self.store.update_check(
                source_id,
                etag=result.etag,
                last_modified=result.last_modified,
                error=None,
            )
            return {
                "source_id": source_id,
                "unchanged": False,
                "fetched": len(items),
                "new": created,
                "duplicates": duplicates,
                "relevant": relevant,
            }
        except Exception as exc:
            self.store.update_check(source_id, error=f"{type(exc).__name__}: {exc}")
            raise

    def refresh_all(self) -> list[dict[str, Any]]:
        reports = []
        for source in self.store.sources():
            if not source["enabled"]:
                continue
            try:
                reports.append({"ok": True, **self.refresh(source["id"])})
            except Exception as exc:
                reports.append(
                    {
                        "ok": False,
                        "source_id": source["id"],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        return reports


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=8, max_length=2000)
    project_id: str | None = None


def _auth_dependency() -> Callable[..., None]:
    expected = os.environ.get(TOKEN_ENV)

    def auth(x_icarus_token: Annotated[str | None, Header()] = None) -> None:
        if expected is None:
            return
        if x_icarus_token is None or not secrets.compare_digest(x_icarus_token, expected):
            raise HTTPException(status_code=401, detail="Ungültiges Token")

    return auth


def _move_ui_mount(app: FastAPI) -> list[Mount]:
    mounts = [
        route
        for route in app.router.routes
        if isinstance(route, Mount) and getattr(route, "name", None) == "ui"
    ]
    if mounts:
        app.router.routes[:] = [route for route in app.router.routes if route not in mounts]
    return mounts


def _install_attention(app: FastAPI, engine: WorldEngine) -> None:
    attention = getattr(app.state, "attention", None)
    if attention is None or getattr(attention, "__world_wrapped__", False):
        return
    original = attention.signals

    def with_world(*, limit: int = 5, at: datetime | None = None):
        moment = at or _now()
        base = list(original(limit=max(10, limit), at=moment))
        items = engine.store.items(limit=25, new_only=True, relevant_only=True)
        if items:
            signal = AttentionSignal(
                id="world:relevant",
                fingerprint=hashlib.sha256(
                    json.dumps([item["id"] for item in items], sort_keys=True).encode()
                ).hexdigest()[:20],
                score=79 + min(len(items), 12),
                kind="world",
                title=f"{len(items)} relevante Außenwelt-Änderung{'en' if len(items) != 1 else ''}",
                reason="Neue belegte Quellen passen zu aktiven Projekten oder Zielen",
                next_action="Außenwelt-Quellen prüfen und Relevantes als Rohmaterial einordnen.",
                target_view="system",
                consequence="Externe Inhalte bleiben fremde Daten und werden nicht automatisch zu Fakten.",
            )
            if attention.preferences.visible(signal, moment):
                base.append(signal)
        return sorted(
            base,
            key=lambda signal: (-signal.score, signal.due_at or "", signal.title.casefold()),
        )[:limit]

    attention.signals = with_world
    attention.__world_wrapped__ = True


def install_world_intelligence(app: FastAPI, data_dir: Path) -> WorldEngine:
    store = WorldStore(data_dir / "workspace.sqlite3")
    engine = WorldEngine(app, store)
    app.state.world = engine
    _install_attention(app, engine)
    auth = _auth_dependency()
    guard = [Depends(auth)]
    ui_mounts = _move_ui_mount(app)
    router = APIRouter(prefix="/world", dependencies=guard)

    @router.get("/sources")
    def sources() -> list[dict[str, Any]]:
        return store.sources()

    @router.post("/sources", status_code=201)
    def add_source(body: SourceIn) -> dict[str, Any]:
        try:
            url = check_url(body.url)
            if body.project_id and app.state.workspace.get_project(body.project_id) is None:
                raise HTTPException(status_code=400, detail="Projekt nicht gefunden")
            return store.add_source(body.name, url, body.project_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.delete("/sources/{source_id}", status_code=204)
    def delete_source(source_id: str) -> None:
        try:
            store.delete_source(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Quelle nicht gefunden") from exc

    @router.post("/sources/{source_id}/refresh")
    def refresh(source_id: str) -> dict[str, Any]:
        try:
            return engine.refresh(source_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Quelle konnte nicht gelesen werden: {exc}") from exc

    @router.post("/refresh")
    def refresh_all() -> list[dict[str, Any]]:
        return engine.refresh_all()

    @router.get("/items")
    def items(
        limit: int = 50,
        new_only: bool = False,
        relevant_only: bool = False,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise HTTPException(status_code=400, detail="limit muss zwischen 1 und 200 liegen")
        return store.items(limit=limit, new_only=new_only, relevant_only=relevant_only)

    @router.post("/items/{item_id}/seen")
    def mark_seen(item_id: str) -> dict[str, Any]:
        try:
            return store.mark_seen(item_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Eintrag nicht gefunden") from exc

    app.include_router(router)
    app.router.routes.extend(ui_mounts)
    return engine


__all__ = [
    "FeedItem",
    "FetchResult",
    "SafeFeedClient",
    "WorldEngine",
    "WorldStore",
    "install_world_intelligence",
    "parse_feed",
]
