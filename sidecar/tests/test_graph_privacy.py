from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from icarus_memory.graph_privacy import annotate_graph
from icarus_memory.knowledge_graph import (
    EntityInput,
    EntityType,
    KnowledgeGraph,
    ProjectionRecord,
    RelationInput,
    SourceRef,
)
from icarus_memory.knowledge_graph_api import graph_router
from icarus_memory.runtime import create_app


HEADERS = {"x-icarus-token": "privacy-test"}


def test_runtime_hides_sensitive_only_assertions_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ICARUS_SIDECAR_TOKEN", "privacy-test")
    monkeypatch.setenv("ICARUS_KEYCHAIN_BACKEND", "none")
    monkeypatch.delenv("ICARUS_BROWSER_WORKER", raising=False)

    with TestClient(create_app()) as client:
        for statement, sensitivity in (
            ("Öffentliches Ziel", "normal"),
            ("Nur für mich sichtbares Ziel", "secret"),
        ):
            response = client.post(
                "/assertions",
                headers=HEADERS,
                json={
                    "statement": statement,
                    "kind": "goal",
                    "sensitivity": sensitivity,
                    "provenance": {"source_type": "user_stated"},
                },
            )
            assert response.status_code == 200, response.text

        client.post("/graph/rebuild", headers=HEADERS).raise_for_status()

        visible = client.get("/graph/entities", headers=HEADERS).json()
        assert {item["canonical_name"] for item in visible} == {"Öffentliches Ziel"}

        all_entities = client.get(
            "/graph/entities?include_sensitive=true", headers=HEADERS
        ).json()
        names = {item["canonical_name"] for item in all_entities}
        assert names == {"Öffentliches Ziel", "Nur für mich sichtbares Ziel"}

        secret = next(
            item for item in all_entities
            if item["canonical_name"] == "Nur für mich sichtbares Ziel"
        )
        assert client.get(
            f"/graph/entities/{secret['id']}", headers=HEADERS
        ).status_code == 404
        assert client.get(
            f"/graph/entities/{secret['id']}?include_sensitive=true",
            headers=HEADERS,
        ).status_code == 200


def test_timeline_does_not_reveal_sensitive_edge_for_visible_entity(tmp_path):
    path = tmp_path / "graph.sqlite3"
    graph = KnowledgeGraph(path)
    visible = EntityInput(EntityType.PERSON, "Sichtbare Person")
    secret = EntityInput(EntityType.GOAL, "Geheimes Ziel")
    normal_source = SourceRef("assertion", "normal", sensitivity="normal")
    secret_source = SourceRef("assertion", "secret", sensitivity="secret")
    records = [
        ProjectionRecord(normal_source, (visible,)),
        ProjectionRecord(
            secret_source,
            relations=(
                RelationInput(
                    source=visible,
                    predicate="pursues",
                    target=secret,
                    provenance=secret_source,
                    sensitivity="secret",
                ),
            ),
        ),
    ]
    graph.rebuild(records)
    annotate_graph(path, records)
    visible_id = graph.resolve("Sichtbare Person", EntityType.PERSON)
    secret_id = graph.resolve("Geheimes Ziel", EntityType.GOAL)
    graph.close()

    app = FastAPI()
    template = KnowledgeGraph(path)
    app.include_router(graph_router(template))
    template.close()

    with TestClient(app) as client:
        assert client.get(f"/graph/entities/{visible_id}").status_code == 200
        assert client.get(f"/graph/entities/{secret_id}").status_code == 404
        assert client.get(
            f"/graph/entities/{visible_id}/timeline"
        ).json() == []

        private_timeline = client.get(
            f"/graph/entities/{visible_id}/timeline?include_sensitive=true"
        ).json()
        assert len(private_timeline) == 1
        assert private_timeline[0]["target_name"] == "Geheimes Ziel"
        assert private_timeline[0]["sources"][0]["sensitivity"] == "secret"
