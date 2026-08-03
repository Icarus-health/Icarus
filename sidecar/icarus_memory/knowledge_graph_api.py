"""HTTP-Router für die ableitbare Wissensgraph-Projektion.

Der Router wird bewusst als Factory geliefert. Der Hauptserver entscheidet,
welche Authentifizierungs- und Wartungsabhängigkeiten gelten; der Graph erhält
keinen alternativen, ungeschützten Ausführungspfad.

FastAPI führt synchrone Endpunkte in Worker-Threads aus. SQLite-Verbindungen
sind absichtlich threadgebunden. Der Router teilt deshalb nicht die beim Start
erzeugte Verbindung, sondern öffnet je Anfrage eine kurze eigene Verbindung
zur gleichen Projektionsdatei. Das ist sicherer als ``check_same_thread=False``
auf einem global gemeinsam verwendeten Connection-Objekt.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Sequence

from fastapi import APIRouter, HTTPException

from .knowledge_graph import KnowledgeGraph


@contextmanager
def _request_graph(path: str) -> Iterator[KnowledgeGraph]:
    current = KnowledgeGraph(path)
    try:
        yield current
    finally:
        current.close()


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

    @router.get("/conflicts")
    def conflicts() -> list[dict[str, object]]:
        with _request_graph(graph_path) as current:
            return current.conflicts()

    @router.get("/entities/{entity_id}")
    def entity(entity_id: str) -> dict[str, object]:
        with _request_graph(graph_path) as current:
            result = current.entity(entity_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Entität nicht gefunden")
        return result

    @router.get("/entities/{entity_id}/neighbors")
    def neighbors(
        entity_id: str,
        include_sensitive: bool = False,
        include_inactive: bool = False,
    ) -> list[dict[str, object]]:
        with _request_graph(graph_path) as current:
            if current.entity(entity_id) is None:
                raise HTTPException(status_code=404, detail="Entität nicht gefunden")
            return current.neighbors(
                entity_id,
                include_sensitive=include_sensitive,
                include_inactive=include_inactive,
            )

    @router.get("/entities/{entity_id}/timeline")
    def timeline(entity_id: str) -> list[dict[str, object]]:
        with _request_graph(graph_path) as current:
            if current.entity(entity_id) is None:
                raise HTTPException(status_code=404, detail="Entität nicht gefunden")
            return current.timeline(entity_id)

    @router.get("/path")
    def path(
        source_id: str,
        target_id: str,
        max_depth: int = 6,
        include_sensitive: bool = False,
    ) -> dict[str, object]:
        if max_depth < 1 or max_depth > 12:
            raise HTTPException(status_code=400, detail="max_depth muss zwischen 1 und 12 liegen")
        with _request_graph(graph_path) as current:
            edges = current.shortest_path(
                source_id,
                target_id,
                max_depth=max_depth,
                include_sensitive=include_sensitive,
            )
        return {
            "source_id": source_id,
            "target_id": target_id,
            "edges": edges,
        }

    @router.get("/edges/{edge_id}/sources")
    def sources(edge_id: str) -> list[dict[str, object]]:
        with _request_graph(graph_path) as current:
            result = current.sources(edge_id)
        if not result:
            raise HTTPException(status_code=404, detail="Kante oder Quelle nicht gefunden")
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
