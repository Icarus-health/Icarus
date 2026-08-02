"""Vertrag des eingefrorenen Sidecar-Einstiegs."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from icarus_memory.runtime import main


def test_packaging_entrypoint_importiert_produktionsstart() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "packaging"
        / "icarus_sidecar_entry.py"
    )
    spec = importlib.util.spec_from_file_location("icarus_sidecar_entry_test", path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main is main
