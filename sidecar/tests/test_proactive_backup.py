from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from icarus_memory.runtime import create_app


HEADERS = {"x-icarus-token": "attention-backup-test"}


def test_dismissed_attention_survives_full_backup_and_restore(monkeypatch, tmp_path):
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ICARUS_SIDECAR_TOKEN", "attention-backup-test")
    monkeypatch.setenv("ICARUS_KEYCHAIN_BACKEND", "none")
    monkeypatch.delenv("ICARUS_BROWSER_WORKER", raising=False)

    with TestClient(create_app()) as client:
        task = client.post(
            "/tasks",
            headers=HEADERS,
            json={
                "title": "Hinweis überlebt Restore",
                "due": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
            },
        ).json()
        signal = next(
            item
            for item in client.get(
                "/chief-of-staff/attention?limit=10", headers=HEADERS
            ).json()
            if item["source_id"] == task["id"]
        )
        client.post(
            f"/chief-of-staff/attention/{signal['id']}/dismiss",
            headers=HEADERS,
            json={"fingerprint": signal["fingerprint"]},
        ).raise_for_status()
        backup = client.post("/backups", headers=HEADERS).json()

        workspace = tmp_path / "workspace.sqlite3"
        with sqlite3.connect(workspace) as db:
            db.execute("DELETE FROM attention_controls")
        visible = client.get(
            "/chief-of-staff/attention?limit=10", headers=HEADERS
        ).json()
        assert signal["id"] in {item["id"] for item in visible}

        restore = client.post(
            "/backups/restore",
            headers=HEADERS,
            json={"name": backup["name"]},
        )
        assert restore.status_code == 200, restore.text
        after = client.get(
            "/chief-of-staff/attention?limit=10", headers=HEADERS
        ).json()
        assert signal["id"] not in {item["id"] for item in after}
