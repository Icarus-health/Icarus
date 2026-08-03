from __future__ import annotations

from pathlib import Path

import pytest

from icarus_memory.browser_connector import browser_connector
from icarus_memory.knowledge_graph_projection import project_assertions
from icarus_memory.security import SecurityError


class DownloadBrowser:
    def __init__(self) -> None:
        self.downloads: list[tuple[str, Path]] = []

    def navigate(self, url: str) -> str:
        return url

    def read(self, selector: str = "body", max_chars: int = 8000) -> str:
        return ""

    def submit(self, selector: str, fields: dict[str, str]) -> str:
        return ""

    def download(self, selector: str, target: Path) -> str:
        self.downloads.append((selector, target))
        target.write_text("fremder Inhalt", encoding="utf-8")
        return str(target)

    def upload(self, selector: str, source: Path) -> str:
        return str(source)


def test_browser_download_accepts_new_file_inside_allowed_directory(tmp_path):
    browser = DownloadBrowser()
    tool = browser_connector(
        browser,
        download_roots=[tmp_path],
        upload_roots=[tmp_path],
        url_guard=lambda value: value,
    ).tools()["browser_herunterladen"]

    target = tmp_path / "neu.txt"
    result = tool.run(selector="#download", target=str(target))

    assert target.read_text(encoding="utf-8") == "fremder Inhalt"
    assert browser.downloads == [("#download", target)]
    assert "ANFANG FREMDER INHALT" in result


def test_browser_download_rejects_directory_outside_allowed_roots(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    browser = DownloadBrowser()
    tool = browser_connector(
        browser,
        download_roots=[allowed],
        upload_roots=[allowed],
        url_guard=lambda value: value,
    ).tools()["browser_herunterladen"]

    with pytest.raises(SecurityError):
        tool.run(selector="#download", target=str(outside / "blocked.txt"))
    assert browser.downloads == []


def test_graph_projects_only_active_and_disputed_assertions():
    assertions = [
        {
            "id": "active",
            "statement": "Aktives Ziel",
            "kind": "goal",
            "status": "active",
        },
        {
            "id": "disputed",
            "statement": "Strittiges Ziel",
            "kind": "goal",
            "status": "disputed",
        },
        {
            "id": "retracted",
            "statement": "Widerrufenes Ziel",
            "kind": "goal",
            "status": "retracted",
        },
        {
            "id": "redacted",
            "statement": "Redigiertes Ziel",
            "kind": "goal",
            "status": "redacted",
        },
        {
            "id": "superseded",
            "statement": "Ersetztes Ziel",
            "kind": "goal",
            "status": "superseded",
        },
    ]

    records = project_assertions(assertions)
    statements = {
        entity.name
        for record in records
        for entity in record.entities
    }

    assert statements == {"Aktives Ziel", "Strittiges Ziel"}
