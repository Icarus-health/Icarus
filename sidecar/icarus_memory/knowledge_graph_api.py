"""HTTP-Router für die ableitbare Wissensgraph-Projektion.

Der Router wird bewusst als Factory geliefert. Der Hauptserver entscheidet,
welche Authentifizierungs- und Wartungsabhängigkeiten gelten; der Graph erhält
keinen alternativen, ungeschützten Ausführungspfad.

FastAPI führt synchrone Endpunkte in Worker-Threads aus. SQLite-Verbindungen
sind absichtlich threadgebunden. Der Router teilt deshalb nicht die beim Start
erzeugte Verbindung, sondern öffnet je Anfrage eine kurze eigene Verbindung
zur gleichen Projektionsdatei.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Sequence

from fastapi import APIRouter, HTTPException

from .knowledge_graph import EntityType, KnowledgeGraph, normalize_name


@contextmanager
def _request_graph(path: str) -> Iterator[KnowledgeGraph]:
    current = KnowledgeGraph(path)
    try:
        yield current
    finally:
        current.close()


def _privacy_enabled(graph: KnowledgeGraph) -> bool:
    return (
        graph._db.execute(  # noqa: SLF001 - Projektionstabellen gehören zusammen
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entity_privacy'"
        ).fetchone()
        is not None
    )


def _entity_visible(
    graph: KnowledgeGraph,
    entity_id: str,
    *,
    include_sensitive: bool,
) -> bool:
    if graph.entity(entity_id) is None:
        return False
    if include_sensitive or not _privacy_enabled(graph):
        return True
    return (
        graph._db.execute(  # noqa: SLF001
            """
            SELECT 1 FROM entity_privacy
            WHERE entity_id = ? AND sensitivity = 'normal'
            LIMIT 1
            """,
            (entity_id,),
        ).fetchone()
        is not None
    )


def _edge_visible(
    graph: KnowledgeGraph,
    edge_id: str,
    *,
    include_sensitive: bool,
) -> bool:
    row = graph._db.execute(  # noqa: SLF001
        "SELECT sensitivity FROM edges WHERE id = ?", (edge_id,)
    ).fetchone()
    if row is None:
        return False
    return include_sensitive or row["sensitivity"] == "normal"


def _sanitize_edge(
    edge: dict[str, object],
    *,
    include_sensitive: bool,
) -> dict[str, object]:
    result = dict(edge)
    if not include_sensitive:
        result["sources"] = [
            source
            for source in result.get("sources", [])
            if source.get("sensitivity", "normal") == "normal"
        ]
    return result


def graph_router(
    graph: KnowledgeGraph,
    *,
    dependencies: Sequence[Any] = (),
) -> APIRouter:
    if graph.path == ":memory:":
        raise ValueError(
            "Die Graph-API benötigt eine dateibasierte Projektion, damit jede "
            "Anfrage eine eigene SQLite-Verbindung verwenden kann."
        )
    graph_path = graph.path
    router = APIRouter(
        prefix="/graph",
        tags=["knowledge-graph"],
        dependencies=list(dependencies),
    )

    @router.get("/stats")
    def stats() -> dict[str, int]:
        with _request_graph(graph_path) as current:
            return current.stats()

    @router.get("/entities")
    def entities(
        query: str = "",
        entity_type: str = "",
        limit: int = 50,
        include_sensitive: bool = False,
    ) -> list[dict[str, object]]:
        if limit < 1 or limit > 200:
            raise HTTPException(
                status_code=400, detail="limit muss zwischen 1 und 200 liegen"
            )
        if entity_type and entity_type not in {item.value for item in EntityType}:
            raise HTTPException(status_code=400, detail="Unbekannter Entitätstyp")

        with _request_graph(graph_path) as current:
            conditions: list[str] = []
            params: list[object] = []
            if entity_type:
                conditions.append("e.entity_type = ?")
                params.append(entity_type)
            if query.strip():
                needle = f"%{normalize_name(query)}%"
                conditions.append(
                    """
                    (e.normalized_name LIKE ? OR EXISTS (
                        SELECT 1 FROM aliases a
                        WHERE a.entity_id = e.id AND a.normalized_alias LIKE ?
                    ))
                    """
                )
                params.extend((needle, needle))
            if not include_sensitive and _privacy_enabled(current):
                conditions.append(
                    """
                    EXISTS (
                        SELECT 1 FROM entity_privacy p
                        WHERE p.entity_id = e.id AND p.sensitivity = 'normal'
                    )
                    """
                )
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            params.append(limit)
            rows = current._db.execute(  # noqa: SLF001
                f"""
                SELECT e.*,
                       (SELECT COUNT(*) FROM edges r
                        WHERE r.source_id = e.id OR r.target_id = e.id) AS relation_count,
                       (SELECT COUNT(*) FROM entity_sources s
                        WHERE s.entity_id = e.id) AS source_count
                FROM entities e
                {where}
                ORDER BY e.normalized_name, e.id
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    @router.get("/conflicts")
    def conflicts(include_sensitive: bool = False) -> list[dict[str, object]]:
        with _request_graph(graph_path) as current:
            result = []
            for conflict in current.conflicts():
                candidates = [str(item) for item in conflict["candidate_ids"]]
                if all(
                    _entity_visible(
                        current,
                        candidate,
                        include_sensitive=include_sensitive,
                    )
                    for candidate in candidates
                ):
                    result.append(conflict)
            return result

    @router.get("/entities/{entity_id}")
    def entity(
        entity_id: str,
        include_sensitive: bool = False,
    ) -> dict[str, object]:
        with _request_graph(graph_path) as current:
            if not _entity_visible(
                current, entity_id, include_sensitive=include_sensitive
            ):
                raise HTTPException(status_code=404, detail="Entität nicht gefunden")
            result = current.entity(entity_id)
        assert result is not None
        return result

    @router.get("/entities/{entity_id}/neighbors")
    def neighbors(
        entity_id: str,
        include_sensitive: bool = False,
        include_inactive: bool = False,
    ) -> list[dict[str, object]]:
        with _request_graph(graph_path) as current:
            if not _entity_visible(
                current, entity_id, include_sensitive=include_sensitive
            ):
                raise HTTPException(status_code=404, detail="Entität nicht gefunden")
            edges = current.neighbors(
                entity_id,
                include_sensitive=include_sensitive,
                include_inactive=include_inactive,
            )
            return [
                _sanitize_edge(edge, include_sensitive=include_sensitive)
                for edge in edges
            ]

    @router.get("/entities/{entity_id}/timeline")
    def timeline(
        entity_id: str,
        include_sensitive: bool = False,
    ) -> list[dict[str, object]]:
        with _request_graph(graph_path) as current:
            if not _entity_visible(
                current, entity_id, include_sensitive=include_sensitive
            ):
                raise HTTPException(status_code=404, detail="Entität nicht gefunden")
            edges = current.neighbors(
                entity_id,
                include_sensitive=include_sensitive,
                include_inactive=True,
            )
            edges.sort(
                key=lambda item: (
                    str(item.get("valid_from") or ""),
                    str(item.get("valid_until") or ""),
                    str(item.get("id") or ""),
                )
            )
            return [
                _sanitize_edge(edge, include_sensitive=include_sensitive)
                for edge in edges
            ]

    @router.get("/path")
    def path(
        source_id: str,
        target_id: str,
        max_depth: int = 6,
        include_sensitive: bool = False,
    ) -> dict[str, object]:
        if max_depth < 1 or max_depth > 12:
            raise HTTPException(
                status_code=400, detail="max_depth muss zwischen 1 und 12 liegen"
            )
        with _request_graph(graph_path) as current:
            if not _entity_visible(
                current, source_id, include_sensitive=include_sensitive
            ) or not _entity_visible(
                current, target_id, include_sensitive=include_sensitive
            ):
                raise HTTPException(status_code=404, detail="Entität nicht gefunden")
            edges = current.shortest_path(
                source_id,
                target_id,
                max_depth=max_depth,
                include_sensitive=include_sensitive,
            )
        return {
            "source_id": source_id,
            "target_id": target_id,
            "edges": [
                _sanitize_edge(edge, include_sensitive=include_sensitive)
                for edge in edges
            ],
        }

    @router.get("/edges/{edge_id}/sources")
    def sources(
        edge_id: str,
        include_sensitive: bool = False,
    ) -> list[dict[str, object]]:
        with _request_graph(graph_path) as current:
            if not _edge_visible(
                current, edge_id, include_sensitive=include_sensitive
            ):
                raise HTTPException(
                    status_code=404, detail="Kante oder Quelle nicht gefunden"
                )
            result = current.sources(edge_id)
        if not include_sensitive:
            result = [
                item
                for item in result
                if item.get("sensitivity", "normal") == "normal"
            ]
        if not result:
            raise HTTPException(
                status_code=404, detail="Kante oder Quelle nicht gefunden"
            )
        return result

    return router


def install_graph_api(
    app: Any,
    graph: KnowledgeGraph,
    *,
    dependencies: Sequence[Any] = (),
) -> None:
    """Montiert den Router am bestehenden FastAPI-Server."""
    app.include_router(graph_router(graph, dependencies=dependencies))


__all__ = ["graph_router", "install_graph_api"]
