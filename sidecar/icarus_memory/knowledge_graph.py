"""Ableitbarer Wissensgraph über dem verbindlichen Icarus-Bestand.

Der Graph ist eine Projektion, keine zweite Wahrheit. Jede Kante verweist auf
mindestens eine Quelle aus Selbstmodell, Projekten, Aufgaben, Notizen oder
Episoden. Die komplette Datenbank darf jederzeit gelöscht und deterministisch
neu aufgebaut werden.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Iterator, Sequence
from uuid import NAMESPACE_URL, uuid5


class EntityType(str, Enum):
    PERSON = "person"
    ORGANISATION = "organisation"
    ROLE = "role"
    PROJECT = "project"
    GOAL = "goal"
    DECISION = "decision"
    EVENT = "event"
    PLACE = "place"


class RelationStatus(str, Enum):
    ACTIVE = "active"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"


@dataclass(frozen=True)
class SourceRef:
    source_type: str
    source_id: str
    source_version: str = ""
    captured_at: str | None = None
    sensitivity: str = "normal"

    @property
    def key(self) -> str:
        return f"{self.source_type}:{self.source_id}:{self.source_version}"


@dataclass(frozen=True)
class EntityInput:
    entity_type: EntityType
    name: str
    aliases: tuple[str, ...] = ()
    external_id: str | None = None


@dataclass(frozen=True)
class RelationInput:
    source: EntityInput
    predicate: str
    target: EntityInput
    provenance: SourceRef
    valid_from: str | None = None
    valid_until: str | None = None
    status: RelationStatus = RelationStatus.ACTIVE
    sensitivity: str = "normal"
    attributes: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectionRecord:
    provenance: SourceRef
    entities: tuple[EntityInput, ...] = ()
    relations: tuple[RelationInput, ...] = ()


def normalize_name(value: str) -> str:
    """Normalisiert Namen stabil, ohne semantische Vermutungen einzubauen."""
    decomposed = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", " ", decomposed)


def stable_entity_id(entity: EntityInput) -> str:
    """Erzeugt eine stabile interne ID.

    Externe IDs gewinnen, wenn vorhanden. Andernfalls wird nur innerhalb eines
    Entitätstyps über den normalisierten Namen aufgelöst. Personen und Orte mit
    gleichem Namen können deshalb nie still zusammenfallen.
    """
    identity = entity.external_id or normalize_name(entity.name)
    return str(uuid5(NAMESPACE_URL, f"icarus:{entity.entity_type.value}:{identity}"))


def stable_edge_id(relation: RelationInput) -> str:
    payload = "|".join(
        [
            stable_entity_id(relation.source),
            normalize_name(relation.predicate),
            stable_entity_id(relation.target),
            relation.valid_from or "",
            relation.valid_until or "",
            relation.status.value,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class IdentityConflict(ValueError):
    """Eine Identität ist mehrdeutig und darf nicht still vereinigt werden."""


class KnowledgeGraph:
    """SQLite-Projektion mit nachvollziehbaren Quellen und Zeitbezug."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._schema()

    def close(self) -> None:
        self._db.close()

    def _schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                external_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS entity_external_unique
                ON entities(entity_type, external_id)
                WHERE external_id IS NOT NULL;

            CREATE TABLE IF NOT EXISTS aliases (
                entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                alias TEXT NOT NULL,
                normalized_alias TEXT NOT NULL,
                PRIMARY KEY(entity_id, normalized_alias)
            );
            CREATE INDEX IF NOT EXISTS alias_lookup
                ON aliases(normalized_alias, entity_id);

            CREATE TABLE IF NOT EXISTS edges (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                predicate TEXT NOT NULL,
                target_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                valid_from TEXT,
                valid_until TEXT,
                status TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                attributes_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS edge_source ON edges(source_id, status);
            CREATE INDEX IF NOT EXISTS edge_target ON edges(target_id, status);

            CREATE TABLE IF NOT EXISTS edge_sources (
                edge_id TEXT NOT NULL REFERENCES edges(id) ON DELETE CASCADE,
                source_key TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_version TEXT NOT NULL,
                captured_at TEXT,
                sensitivity TEXT NOT NULL,
                PRIMARY KEY(edge_id, source_key)
            );

            CREATE TABLE IF NOT EXISTS entity_sources (
                entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                source_key TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_version TEXT NOT NULL,
                PRIMARY KEY(entity_id, source_key)
            );

            CREATE TABLE IF NOT EXISTS identity_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                normalized_alias TEXT NOT NULL,
                candidate_ids_json TEXT NOT NULL,
                source_key TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS merge_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                primary_id TEXT NOT NULL,
                merged_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                reversible_snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self._db.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def clear(self) -> None:
        """Entfernt nur die Projektion. Verbindliche Quellen bleiben unberührt."""
        with self._db:
            for table in (
                "edge_sources",
                "edges",
                "entity_sources",
                "aliases",
                "identity_conflicts",
                "merge_events",
                "entities",
            ):
                self._db.execute(f"DELETE FROM {table}")

    def rebuild(self, records: Iterable[ProjectionRecord]) -> dict[str, int]:
        """Baut die Projektion in stabiler Reihenfolge vollständig neu auf."""
        materialized = sorted(records, key=lambda item: item.provenance.key)
        self.clear()
        for record in materialized:
            for entity in sorted(
                record.entities,
                key=lambda item: (item.entity_type.value, normalize_name(item.name)),
            ):
                self.upsert_entity(entity, record.provenance)
            for relation in sorted(record.relations, key=stable_edge_id):
                self.upsert_relation(relation)
        self.remove_orphans()
        return self.stats()

    def upsert_entity(self, entity: EntityInput, provenance: SourceRef) -> str:
        entity_id = stable_entity_id(entity)
        normalized = normalize_name(entity.name)
        aliases = {entity.name, *entity.aliases}
        with self._db:
            self._db.execute(
                """
                INSERT INTO entities(
                    id, entity_type, canonical_name, normalized_name,
                    external_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    canonical_name = excluded.canonical_name,
                    normalized_name = excluded.normalized_name,
                    external_id = COALESCE(excluded.external_id, entities.external_id)
                """,
                (
                    entity_id,
                    entity.entity_type.value,
                    entity.name.strip(),
                    normalized,
                    entity.external_id,
                    self._now(),
                ),
            )
            for alias in sorted(aliases, key=normalize_name):
                if not alias.strip():
                    continue
                normalized_alias = normalize_name(alias)
                candidates = self._db.execute(
                    """
                    SELECT DISTINCT e.id, e.entity_type
                    FROM aliases a JOIN entities e ON e.id = a.entity_id
                    WHERE a.normalized_alias = ? AND e.id <> ?
                    """,
                    (normalized_alias, entity_id),
                ).fetchall()
                incompatible = [row for row in candidates if row["entity_type"] != entity.entity_type.value]
                if incompatible:
                    ids = sorted([entity_id, *(row["id"] for row in incompatible)])
                    self._db.execute(
                        """
                        INSERT INTO identity_conflicts(
                            normalized_alias, candidate_ids_json, source_key,
                            reason, created_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            normalized_alias,
                            json.dumps(ids),
                            provenance.key,
                            "Gleicher Alias bei verschiedenen Entitätstypen",
                            self._now(),
                        ),
                    )
                self._db.execute(
                    """
                    INSERT OR IGNORE INTO aliases(entity_id, alias, normalized_alias)
                    VALUES (?, ?, ?)
                    """,
                    (entity_id, alias.strip(), normalized_alias),
                )
            self._db.execute(
                """
                INSERT OR IGNORE INTO entity_sources(
                    entity_id, source_key, source_type, source_id, source_version
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entity_id,
                    provenance.key,
                    provenance.source_type,
                    provenance.source_id,
                    provenance.source_version,
                ),
            )
        return entity_id

    def upsert_relation(self, relation: RelationInput) -> str:
        source_id = self.upsert_entity(relation.source, relation.provenance)
        target_id = self.upsert_entity(relation.target, relation.provenance)
        edge_id = stable_edge_id(relation)
        with self._db:
            self._db.execute(
                """
                INSERT INTO edges(
                    id, source_id, predicate, target_id, valid_from, valid_until,
                    status, sensitivity, attributes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    sensitivity = excluded.sensitivity,
                    attributes_json = excluded.attributes_json
                """,
                (
                    edge_id,
                    source_id,
                    normalize_name(relation.predicate),
                    target_id,
                    relation.valid_from,
                    relation.valid_until,
                    relation.status.value,
                    relation.sensitivity,
                    json.dumps(relation.attributes, sort_keys=True, ensure_ascii=False),
                ),
            )
            p = relation.provenance
            self._db.execute(
                """
                INSERT OR REPLACE INTO edge_sources(
                    edge_id, source_key, source_type, source_id, source_version,
                    captured_at, sensitivity
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    edge_id,
                    p.key,
                    p.source_type,
                    p.source_id,
                    p.source_version,
                    p.captured_at,
                    p.sensitivity,
                ),
            )
        return edge_id

    def remove_source(self, source: SourceRef) -> None:
        """Entfernt eine widerrufene/ersetzte Quelle samt verwaister Kanten."""
        with self._db:
            self._db.execute("DELETE FROM edge_sources WHERE source_key = ?", (source.key,))
            self._db.execute("DELETE FROM entity_sources WHERE source_key = ?", (source.key,))
        self.remove_orphans()

    def remove_orphans(self) -> None:
        with self._db:
            self._db.execute(
                "DELETE FROM edges WHERE id NOT IN (SELECT DISTINCT edge_id FROM edge_sources)"
            )
            self._db.execute(
                """
                DELETE FROM entities
                WHERE id NOT IN (SELECT DISTINCT entity_id FROM entity_sources)
                  AND id NOT IN (SELECT source_id FROM edges)
                  AND id NOT IN (SELECT target_id FROM edges)
                """
            )

    def resolve(self, value: str, entity_type: EntityType | None = None) -> str | None:
        normalized = normalize_name(value)
        params: list[str] = [normalized]
        condition = ""
        if entity_type is not None:
            condition = " AND e.entity_type = ?"
            params.append(entity_type.value)
        rows = self._db.execute(
            f"""
            SELECT DISTINCT e.id
            FROM aliases a JOIN entities e ON e.id = a.entity_id
            WHERE a.normalized_alias = ?{condition}
            ORDER BY e.id
            """,
            params,
        ).fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            raise IdentityConflict(
                f"{value!r} ist mehrdeutig: {', '.join(row['id'] for row in rows)}"
            )
        return rows[0]["id"]

    def entity(self, entity_id: str) -> dict[str, object] | None:
        row = self._db.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["aliases"] = [
            alias["alias"]
            for alias in self._db.execute(
                "SELECT alias FROM aliases WHERE entity_id = ? ORDER BY normalized_alias",
                (entity_id,),
            )
        ]
        return result

    def neighbors(
        self,
        entity_id: str,
        *,
        include_sensitive: bool = False,
        include_inactive: bool = False,
    ) -> list[dict[str, object]]:
        conditions = ["(e.source_id = ? OR e.target_id = ?)"]
        params: list[object] = [entity_id, entity_id]
        if not include_inactive:
            conditions.append("e.status IN ('active', 'disputed')")
        if not include_sensitive:
            conditions.append("e.sensitivity = 'normal'")
        rows = self._db.execute(
            f"""
            SELECT e.*, s.canonical_name AS source_name,
                   t.canonical_name AS target_name
            FROM edges e
            JOIN entities s ON s.id = e.source_id
            JOIN entities t ON t.id = e.target_id
            WHERE {' AND '.join(conditions)}
            ORDER BY COALESCE(e.valid_from, ''), e.predicate, e.id
            """,
            params,
        ).fetchall()
        return [self._edge_dict(row) for row in rows]

    def shortest_path(
        self,
        source_id: str,
        target_id: str,
        *,
        max_depth: int = 6,
        include_sensitive: bool = False,
    ) -> list[dict[str, object]]:
        if source_id == target_id:
            return []
        queue: deque[tuple[str, list[dict[str, object]]]] = deque([(source_id, [])])
        visited = {source_id}
        while queue:
            current, path = queue.popleft()
            if len(path) >= max_depth:
                continue
            for edge in self.neighbors(current, include_sensitive=include_sensitive):
                next_id = edge["target_id"] if edge["source_id"] == current else edge["source_id"]
                if next_id in visited:
                    continue
                new_path = [*path, edge]
                if next_id == target_id:
                    return new_path
                visited.add(str(next_id))
                queue.append((str(next_id), new_path))
        return []

    def timeline(self, entity_id: str) -> list[dict[str, object]]:
        rows = self._db.execute(
            """
            SELECT e.*, s.canonical_name AS source_name,
                   t.canonical_name AS target_name
            FROM edges e
            JOIN entities s ON s.id = e.source_id
            JOIN entities t ON t.id = e.target_id
            WHERE e.source_id = ? OR e.target_id = ?
            ORDER BY COALESCE(e.valid_from, ''), COALESCE(e.valid_until, ''), e.id
            """,
            (entity_id, entity_id),
        ).fetchall()
        return [self._edge_dict(row) for row in rows]

    def sources(self, edge_id: str) -> list[dict[str, object]]:
        return [
            dict(row)
            for row in self._db.execute(
                "SELECT * FROM edge_sources WHERE edge_id = ? ORDER BY source_key",
                (edge_id,),
            ).fetchall()
        ]

    def conflicts(self) -> list[dict[str, object]]:
        return [
            {
                **dict(row),
                "candidate_ids": json.loads(row["candidate_ids_json"]),
            }
            for row in self._db.execute(
                "SELECT * FROM identity_conflicts ORDER BY id"
            ).fetchall()
        ]

    def merge(self, primary_id: str, merged_id: str, reason: str) -> None:
        """Vereinigt zwei Entitäten reversibel und protokolliert den Zustand."""
        if primary_id == merged_id:
            return
        primary = self.entity(primary_id)
        merged = self.entity(merged_id)
        if primary is None or merged is None:
            raise KeyError("Unbekannte Entität")
        if primary["entity_type"] != merged["entity_type"]:
            raise IdentityConflict("Nur Entitäten desselben Typs dürfen vereinigt werden")
        snapshot = {
            "primary": primary,
            "merged": merged,
            "edges": [dict(row) for row in self._db.execute(
                "SELECT * FROM edges WHERE source_id = ? OR target_id = ?",
                (merged_id, merged_id),
            )],
            "sources": [dict(row) for row in self._db.execute(
                "SELECT * FROM entity_sources WHERE entity_id = ?", (merged_id,)
            )],
        }
        with self._db:
            for alias in merged["aliases"]:
                self._db.execute(
                    "INSERT OR IGNORE INTO aliases(entity_id, alias, normalized_alias) VALUES (?, ?, ?)",
                    (primary_id, alias, normalize_name(str(alias))),
                )
            self._db.execute("UPDATE edges SET source_id = ? WHERE source_id = ?", (primary_id, merged_id))
            self._db.execute("UPDATE edges SET target_id = ? WHERE target_id = ?", (primary_id, merged_id))
            self._db.execute(
                "INSERT OR IGNORE INTO entity_sources SELECT ?, source_key, source_type, source_id, source_version FROM entity_sources WHERE entity_id = ?",
                (primary_id, merged_id),
            )
            self._db.execute("DELETE FROM entities WHERE id = ?", (merged_id,))
            self._db.execute(
                "INSERT INTO merge_events(primary_id, merged_id, reason, reversible_snapshot, created_at) VALUES (?, ?, ?, ?, ?)",
                (primary_id, merged_id, reason, json.dumps(snapshot, sort_keys=True), self._now()),
            )

    def stats(self) -> dict[str, int]:
        return {
            "entities": self._db.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
            "edges": self._db.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
            "sources": self._db.execute("SELECT COUNT(*) FROM edge_sources").fetchone()[0],
            "identity_conflicts": self._db.execute(
                "SELECT COUNT(*) FROM identity_conflicts"
            ).fetchone()[0],
        }

    def digest(self) -> str:
        """Stabiler Fingerabdruck für deterministische Neuaufbau-Tests."""
        payload = {
            "entities": [dict(row) for row in self._db.execute(
                "SELECT id, entity_type, canonical_name, normalized_name, external_id FROM entities ORDER BY id"
            )],
            "aliases": [dict(row) for row in self._db.execute(
                "SELECT entity_id, alias, normalized_alias FROM aliases ORDER BY entity_id, normalized_alias"
            )],
            "edges": [dict(row) for row in self._db.execute(
                "SELECT * FROM edges ORDER BY id"
            )],
            "edge_sources": [dict(row) for row in self._db.execute(
                "SELECT * FROM edge_sources ORDER BY edge_id, source_key"
            )],
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _edge_dict(self, row: sqlite3.Row) -> dict[str, object]:
        result = dict(row)
        result["attributes"] = json.loads(result.pop("attributes_json"))
        result["sources"] = self.sources(str(row["id"]))
        return result


__all__ = [
    "EntityInput",
    "EntityType",
    "IdentityConflict",
    "KnowledgeGraph",
    "ProjectionRecord",
    "RelationInput",
    "RelationStatus",
    "SourceRef",
    "normalize_name",
    "stable_edge_id",
    "stable_entity_id",
]
