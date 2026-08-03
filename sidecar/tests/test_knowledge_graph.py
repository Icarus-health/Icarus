from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from icarus_memory.knowledge_graph import (
    EntityInput,
    EntityType,
    IdentityConflict,
    KnowledgeGraph,
    ProjectionRecord,
    RelationInput,
    RelationStatus,
    SourceRef,
    stable_entity_id,
)
from icarus_memory.knowledge_graph_api import graph_router
from icarus_memory.knowledge_graph_projection import project_all


def relation_record(
    source_id: str = "note-1",
    *,
    sensitive: bool = False,
) -> ProjectionRecord:
    provenance = SourceRef(
        "note",
        source_id,
        "v1",
        captured_at="2026-08-03T06:00:00+00:00",
        sensitivity="sensitive" if sensitive else "normal",
    )
    person = EntityInput(EntityType.PERSON, "Ada Lovelace", aliases=("Ada",))
    organisation = EntityInput(EntityType.ORGANISATION, "Analytical Engine Society")
    return ProjectionRecord(
        provenance,
        (person, organisation),
        (
            RelationInput(
                person,
                "member_of",
                organisation,
                provenance,
                valid_from="1842-01-01T00:00:00+00:00",
                sensitivity="sensitive" if sensitive else "normal",
                attributes={"role": "author"},
            ),
        ),
    )


def test_rebuild_is_deterministic_and_every_edge_has_a_source(tmp_path):
    graph = KnowledgeGraph(tmp_path / "graph.sqlite3")
    records = [
        relation_record("note-2"),
        relation_record("note-1"),
    ]

    first = graph.rebuild(records)
    digest = graph.digest()
    second = graph.rebuild(reversed(records))

    assert first == second == {
        "entities": 2,
        "edges": 1,
        "sources": 2,
        "identity_conflicts": 0,
    }
    assert graph.digest() == digest
    ada_id = stable_entity_id(EntityInput(EntityType.PERSON, "Ada Lovelace", aliases=("Ada",)))
    edges = graph.neighbors(ada_id)
    assert len(edges) == 1
    assert {source["source_id"] for source in edges[0]["sources"]} == {"note-1", "note-2"}


def test_revoking_sources_removes_orphan_edges_and_entities():
    graph = KnowledgeGraph()
    record = relation_record()
    graph.rebuild([record])

    graph.remove_source(record.provenance)

    assert graph.stats() == {
        "entities": 0,
        "edges": 0,
        "sources": 0,
        "identity_conflicts": 0,
    }


def test_identity_conflicts_are_visible_instead_of_silent_merge():
    graph = KnowledgeGraph()
    source = SourceRef("assertion", "a-1")
    graph.rebuild(
        [
            ProjectionRecord(source, (EntityInput(EntityType.PERSON, "Paris"),)),
            ProjectionRecord(
                SourceRef("episode", "e-1"),
                (EntityInput(EntityType.PLACE, "Paris"),),
            ),
        ]
    )

    assert graph.stats()["identity_conflicts"] == 1
    assert graph.conflicts()[0]["normalized_alias"] == "paris"
    try:
        graph.resolve("Paris")
    except IdentityConflict as error:
        assert "mehrdeutig" in str(error)
    else:
        raise AssertionError("Mehrdeutige Identität wurde still vereinigt")
    assert graph.resolve("Paris", EntityType.PLACE) is not None


def test_sensitive_relationships_follow_egress_boundary():
    graph = KnowledgeGraph()
    graph.rebuild([relation_record(sensitive=True)])
    ada_id = graph.resolve("Ada", EntityType.PERSON)
    assert ada_id is not None

    assert graph.neighbors(ada_id) == []
    assert len(graph.neighbors(ada_id, include_sensitive=True)) == 1


def test_path_timeline_status_and_sources_are_queryable():
    graph = KnowledgeGraph()
    source = SourceRef("project", "p-1")
    project = EntityInput(EntityType.PROJECT, "Icarus", external_id="p-1")
    decision = EntityInput(EntityType.DECISION, "Lokale Datenhaltung", external_id="d-1")
    organisation = EntityInput(EntityType.ORGANISATION, "Icarus Health")
    graph.rebuild(
        [
            ProjectionRecord(
                source,
                (project, decision, organisation),
                (
                    RelationInput(project, "has_decision", decision, source),
                    RelationInput(
                        decision,
                        "benefits",
                        organisation,
                        SourceRef("note", "n-1"),
                        status=RelationStatus.DISPUTED,
                    ),
                ),
            )
        ]
    )

    project_id = stable_entity_id(project)
    organisation_id = stable_entity_id(organisation)
    path = graph.shortest_path(project_id, organisation_id)
    assert [edge["predicate"] for edge in path] == ["has_decision", "benefits"]
    assert len(graph.timeline(project_id)) == 1
    assert graph.sources(path[0]["id"])[0]["source_id"] == "p-1"


def test_workspace_and_episode_projection_only_uses_explicit_fields():
    records = project_all(
        assertions=[
            {
                "id": "a1",
                "kind": "goal",
                "statement": "Icarus fertigstellen",
                "tags": ["person:Sören"],
            }
        ],
        projects=[
            {
                "id": "p1",
                "name": "Icarus",
                "deadline": "2026-12-31T00:00:00+00:00",
            }
        ],
        tasks=[{"id": "t1", "title": "Release bauen", "project_id": "p1"}],
        notes=[
            {
                "id": "n1",
                "title": "Apple Silicon zuerst",
                "body": "Entscheidung",
                "kind": "decision",
                "project_id": "p1",
            }
        ],
        episodes=[
            {
                "id": "e1",
                "title": "Review",
                "participants": ["Sören", "Ada"],
            }
        ],
    )
    graph = KnowledgeGraph()
    stats = graph.rebuild(records)

    assert stats["entities"] >= 8
    assert graph.resolve("Sören", EntityType.PERSON) is not None
    project_id = graph.resolve("Icarus", EntityType.PROJECT)
    assert project_id is not None
    predicates = {edge["predicate"] for edge in graph.neighbors(project_id)}
    assert {"has_deadline", "has_next_step", "has_decision"}.issubset(predicates)


def test_api_requires_supplied_guard_and_exposes_sources(tmp_path):
    graph = KnowledgeGraph(tmp_path / "api-graph.sqlite3")
    graph.rebuild([relation_record()])

    def guard(x_test_token: str | None = Header(default=None)) -> None:
        if x_test_token != "ok":
            raise HTTPException(status_code=401)

    app = FastAPI()
    app.include_router(graph_router(graph, dependencies=[Depends(guard)]))
    client = TestClient(app)

    assert client.get("/graph/stats").status_code == 401
    stats = client.get("/graph/stats", headers={"x-test-token": "ok"})
    assert stats.status_code == 200
    assert stats.json()["edges"] == 1

    ada_id = graph.resolve("Ada", EntityType.PERSON)
    neighbors = client.get(
        f"/graph/entities/{ada_id}/neighbors",
        headers={"x-test-token": "ok"},
    )
    assert neighbors.status_code == 200
    edge_id = neighbors.json()[0]["id"]
    sources = client.get(
        f"/graph/edges/{edge_id}/sources",
        headers={"x-test-token": "ok"},
    )
    assert sources.json()[0]["source_id"] == "note-1"


def test_api_rejects_shared_in_memory_connection():
    graph = KnowledgeGraph()
    try:
        graph_router(graph)
    except ValueError as error:
        assert "dateibasierte Projektion" in str(error)
    else:
        raise AssertionError("Threadgebundene In-Memory-Verbindung wurde für die API akzeptiert")
