"""Deterministische Adapter vom verbindlichen Bestand in den Wissensgraphen.

Die Adapter arbeiten nur mit explizit vorhandenen Feldern. Freitext wird nicht
mittels Heuristik als Person, Organisation oder Beziehung interpretiert; solche
Schlüsse gehören in einen separaten Vorschlagsprozess mit Quellenbeleg.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .knowledge_graph import (
    EntityInput,
    EntityType,
    ProjectionRecord,
    RelationInput,
    RelationStatus,
    SourceRef,
)


def _source(kind: str, item: Mapping[str, Any]) -> SourceRef:
    provenance = item.get("provenance") or {}
    return SourceRef(
        source_type=kind,
        source_id=str(item.get("id") or item.get("uid") or item.get("title") or item.get("name")),
        source_version=str(item.get("version") or item.get("updated_at") or ""),
        captured_at=str(provenance.get("captured_at") or item.get("updated_at") or "") or None,
        sensitivity=str(item.get("sensitivity") or "normal"),
    )


def _project_entity(project: Mapping[str, Any]) -> EntityInput:
    return EntityInput(
        EntityType.PROJECT,
        str(project.get("name") or project.get("title") or "Unbenanntes Projekt"),
        external_id=str(project.get("id")) if project.get("id") else None,
    )


def project_workspace(
    projects: Iterable[Mapping[str, Any]],
    tasks: Iterable[Mapping[str, Any]],
    notes: Iterable[Mapping[str, Any]],
) -> list[ProjectionRecord]:
    project_by_id = {str(item.get("id")): item for item in projects if item.get("id")}
    records: list[ProjectionRecord] = []

    for project in project_by_id.values():
        source = _source("project", project)
        entity = _project_entity(project)
        entities = [entity]
        relations = []
        deadline = project.get("deadline")
        if deadline:
            event = EntityInput(
                EntityType.EVENT,
                f"Frist: {entity.name}",
                external_id=f"project-deadline:{project.get('id')}",
            )
            entities.append(event)
            relations.append(
                RelationInput(
                    source=entity,
                    predicate="has_deadline",
                    target=event,
                    provenance=source,
                    valid_from=str(deadline),
                    status=RelationStatus.ACTIVE,
                    attributes={"deadline": str(deadline)},
                )
            )
        records.append(
            ProjectionRecord(source, tuple(entities), tuple(relations))
        )

    for task in tasks:
        project = project_by_id.get(str(task.get("project_id")))
        if project is None:
            continue
        source = _source("task", task)
        project_entity = _project_entity(project)
        goal = EntityInput(
            EntityType.GOAL,
            str(task.get("title") or "Unbenannte Aufgabe"),
            external_id=f"task:{task.get('id')}" if task.get("id") else None,
        )
        status = RelationStatus.SUPERSEDED if task.get("done") else RelationStatus.ACTIVE
        records.append(
            ProjectionRecord(
                source,
                (project_entity, goal),
                (
                    RelationInput(
                        source=project_entity,
                        predicate="has_next_step",
                        target=goal,
                        provenance=source,
                        valid_until=str(task.get("completed_at") or "") or None,
                        status=status,
                        attributes={
                            "due": task.get("due"),
                            "priority": task.get("priority"),
                        },
                    ),
                ),
            )
        )

    for note in notes:
        if note.get("kind") != "decision":
            continue
        project = project_by_id.get(str(note.get("project_id")))
        if project is None:
            continue
        source = _source("note", note)
        project_entity = _project_entity(project)
        decision = EntityInput(
            EntityType.DECISION,
            str(note.get("title") or "Unbenannte Entscheidung"),
            external_id=f"note:{note.get('id')}" if note.get("id") else None,
        )
        records.append(
            ProjectionRecord(
                source,
                (project_entity, decision),
                (
                    RelationInput(
                        source=project_entity,
                        predicate="has_decision",
                        target=decision,
                        provenance=source,
                        valid_from=str(note.get("created_at") or note.get("updated_at") or "") or None,
                        attributes={"body": note.get("body") or ""},
                    ),
                ),
            )
        )

    return records


def project_episodes(episodes: Iterable[Mapping[str, Any]]) -> list[ProjectionRecord]:
    records: list[ProjectionRecord] = []
    for episode in episodes:
        source = _source("episode", episode)
        event = EntityInput(
            EntityType.EVENT,
            str(episode.get("title") or "Unbenanntes Ereignis"),
            external_id=f"episode:{episode.get('id')}" if episode.get("id") else None,
        )
        entities = [event]
        relations = []
        for participant_name in sorted(set(episode.get("participants") or [])):
            person = EntityInput(EntityType.PERSON, str(participant_name))
            entities.append(person)
            relations.append(
                RelationInput(
                    source=person,
                    predicate="participated_in",
                    target=event,
                    provenance=source,
                    valid_from=str(episode.get("occurred_at") or "") or None,
                )
            )
        records.append(ProjectionRecord(source, tuple(entities), tuple(relations)))
    return records


def project_assertions(assertions: Iterable[Mapping[str, Any]]) -> list[ProjectionRecord]:
    """Projiziert nur Ziele und explizit getaggte Entitäten aus Aussagen."""
    records: list[ProjectionRecord] = []
    for assertion in assertions:
        source = _source("assertion", assertion)
        entities: list[EntityInput] = []
        if assertion.get("kind") == "goal":
            entities.append(
                EntityInput(
                    EntityType.GOAL,
                    str(assertion.get("statement") or "Unbenanntes Ziel"),
                    external_id=f"assertion:{assertion.get('id')}" if assertion.get("id") else None,
                )
            )
        for tag in assertion.get("tags") or []:
            if ":" not in str(tag):
                continue
            prefix, value = str(tag).split(":", 1)
            mapping = {
                "person": EntityType.PERSON,
                "organisation": EntityType.ORGANISATION,
                "role": EntityType.ROLE,
                "place": EntityType.PLACE,
            }
            entity_type = mapping.get(prefix.strip().lower())
            if entity_type and value.strip():
                entities.append(EntityInput(entity_type, value.strip()))
        if entities:
            records.append(ProjectionRecord(source, tuple(entities)))
    return records


def project_all(
    *,
    assertions: Iterable[Mapping[str, Any]] = (),
    projects: Iterable[Mapping[str, Any]] = (),
    tasks: Iterable[Mapping[str, Any]] = (),
    notes: Iterable[Mapping[str, Any]] = (),
    episodes: Iterable[Mapping[str, Any]] = (),
) -> list[ProjectionRecord]:
    return [
        *project_assertions(assertions),
        *project_workspace(projects, tasks, notes),
        *project_episodes(episodes),
    ]


__all__ = [
    "project_all",
    "project_assertions",
    "project_episodes",
    "project_workspace",
]
