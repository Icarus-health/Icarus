"""Parität zwischen dem öffentlichen Selbstmodell-Schema und der Laufzeitform."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from icarus_memory.backends import MemoryBackend
from icarus_memory.model import (
    SCHEMA_VERSION,
    Kind,
    Provenance,
    Sensitivity,
    SourceType,
    Status,
)
from icarus_memory.store import SelfModelStore


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "self-model.schema.json"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_schema_version_der_laufzeit_ist_dokumentiert() -> None:
    schema = _schema()

    assert SCHEMA_VERSION == "0.2.0"
    assert schema["properties"]["schema_version"]["examples"] == [SCHEMA_VERSION]


def test_schema_enums_spiegeln_die_laufzeit() -> None:
    assertion = _schema()["$defs"]["assertion"]["properties"]
    provenance = _schema()["$defs"]["provenance"]["properties"]

    assert set(assertion["kind"]["enum"]) == {item.value for item in Kind}
    assert set(assertion["status"]["enum"]) == {item.value for item in Status}
    assert set(assertion["sensitivity"]["enum"]) == {
        item.value for item in Sensitivity
    }
    assert set(provenance["source_type"]["enum"]) == {
        item.value for item in SourceType
    }


def test_strittiger_export_ist_schemakonform() -> None:
    store = SelfModelStore(MemoryBackend(), subject_id="test")
    first = store.record(
        "Wohnt in Hamburg.",
        Kind.STATE,
        Provenance(source_type=SourceType.CHAT, source_ref="chat:1"),
    )
    second = store.record(
        "Wohnt in Berlin.",
        Kind.STATE,
        Provenance(source_type=SourceType.CHAT, source_ref="chat:2"),
    )
    store.dispute(first.id, second.id)

    document = store.export().to_dict()
    jsonschema.Draft202012Validator(_schema()).validate(document)

    exported = {item["id"]: item for item in document["assertions"]}
    assert exported[first.id]["status"] == "disputed"
    assert exported[first.id]["disputed_with"] == [second.id]
