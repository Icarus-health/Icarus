"""Ableitbare Datenschutzmetadaten für den Wissensgraphen.

Die ursprüngliche Graphprojektion schützt sensible Kanten. Für eine suchbare
Consumer-Oberfläche reicht das nicht: Auch der bloße Name einer Person, eines
Ziels oder einer Organisation kann sensibel sein. Dieses Modul erzeugt deshalb
eine zweite, ebenfalls vollständig ableitbare Projektion innerhalb derselben
Graphdatei.

Die Tabelle ist kein verbindlicher Bestand und wird bei jedem Graph-Neuaufbau
vollständig ersetzt. Sie gehört nicht in das Installationsbackup.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from types import MethodType
from typing import Any

from .knowledge_graph import ProjectionRecord, stable_entity_id
from .knowledge_graph_projection import project_all


CREATE_PRIVACY_TABLE = """
CREATE TABLE IF NOT EXISTS entity_privacy (
    entity_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    PRIMARY KEY(entity_id, source_key)
)
"""


def _records(runtime: Any) -> list[ProjectionRecord]:
    assertions = [
        item.to_dict() for item in runtime.app.state.store.export().assertions
    ]
    projects = [
        item.to_dict()
        for item in runtime.app.state.workspace.projects(
            include_closed=True, limit=10000
        )
    ]
    tasks = [
        item.to_dict() for item in runtime.app.state.tasks.all_tasks(limit=10000)
    ]
    notes = [
        item.to_dict() for item in runtime.app.state.workspace.notes(limit=10000)
    ]
    episodes = [
        item.to_dict()
        for item in runtime.app.state.episodes.all_episodes(limit=10000)
    ]
    return project_all(
        assertions=assertions,
        projects=projects,
        tasks=tasks,
        notes=notes,
        episodes=episodes,
    )


def annotate_graph(path: str | Path, records: Iterable[ProjectionRecord]) -> None:
    """Schreibt die Sensitivität jeder Entität aus ihren Quellen neu."""
    db = sqlite3.connect(str(path))
    try:
        with db:
            db.execute(CREATE_PRIVACY_TABLE)
            db.execute("DELETE FROM entity_privacy")
            for record in records:
                candidates = list(record.entities)
                for relation in record.relations:
                    candidates.extend((relation.source, relation.target))
                seen: set[str] = set()
                for entity in candidates:
                    entity_id = stable_entity_id(entity)
                    if entity_id in seen:
                        continue
                    seen.add(entity_id)
                    db.execute(
                        """
                        INSERT OR REPLACE INTO entity_privacy(
                            entity_id, source_key, sensitivity
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            entity_id,
                            record.provenance.key,
                            record.provenance.sensitivity or "normal",
                        ),
                    )
    finally:
        db.close()


def install_graph_privacy(runtime: Any) -> None:
    """Erweitert den bestehenden Runtime-Neuaufbau ohne zweiten Datenpfad."""
    original = runtime.rebuild_graph

    def rebuild_with_privacy(self: Any) -> dict[str, int]:
        try:
            stats = original()
            annotate_graph(self.graph_path, _records(self))
            return stats
        except Exception:
            self.graph_dirty = True
            raise

    runtime.rebuild_graph = MethodType(rebuild_with_privacy, runtime)
    # Eine Projektion aus einem früheren Stand besitzt die Metadaten eventuell
    # noch nicht. Der nächste Graphzugriff baut beides deterministisch neu.
    runtime.mark_graph_dirty()


__all__ = ["annotate_graph", "install_graph_privacy"]
