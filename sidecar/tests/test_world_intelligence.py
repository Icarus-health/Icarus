from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import icarus_memory.world_intelligence as world_module
from icarus_memory.runtime import create_app
from icarus_memory.world_intelligence import (
    FetchResult,
    parse_feed,
)


HEADERS = {"x-icarus-token": "world-test"}
RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Icarus News</title>
  <item>
    <guid>news-1</guid>
    <title>Icarus Private Beta erreicht neuen Meilenstein</title>
    <link>https://example.com/icarus-beta</link>
    <pubDate>Mon, 03 Aug 2026 06:00:00 GMT</pubDate>
    <description><![CDATA[<p>Neue Funktionen fuer das Icarus Projekt.</p>]]></description>
  </item>
</channel></rss>"""
ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Updates</title>
  <entry>
    <id>atom-1</id>
    <title>Gesundheit und Nachhaltigkeit</title>
    <link rel="alternate" href="https://example.com/update" />
    <updated>2026-08-03T07:00:00Z</updated>
    <summary>Ein belegtes Update.</summary>
  </entry>
</feed>"""


class FakeClient:
    def __init__(self, payload=RSS):
        self.payload = payload
        self.calls = []

    def fetch(self, url, *, etag=None, last_modified=None):
        self.calls.append((url, etag, last_modified))
        return FetchResult(
            200,
            url,
            self.payload,
            etag='"feed-v1"',
            last_modified="Mon, 03 Aug 2026 06:00:00 GMT",
        )


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ICARUS_SIDECAR_TOKEN", "world-test")
    monkeypatch.setenv("ICARUS_KEYCHAIN_BACKEND", "none")
    monkeypatch.delenv("ICARUS_BROWSER_WORKER", raising=False)
    monkeypatch.setattr(world_module, "check_url", lambda value: value)
    return create_app()


def test_parse_rss_and_atom_with_dates_and_plain_text():
    rss = parse_feed(RSS, "https://example.com/feed.xml")
    assert len(rss) == 1
    assert rss[0].key == "news-1"
    assert rss[0].summary == "Neue Funktionen fuer das Icarus Projekt."
    assert rss[0].published_at == datetime(2026, 8, 3, 6, tzinfo=timezone.utc)

    atom = parse_feed(ATOM, "https://example.com/atom.xml")
    assert len(atom) == 1
    assert atom[0].key == "atom-1"
    assert atom[0].url == "https://example.com/update"
    assert atom[0].published_at == datetime(2026, 8, 3, 7, tzinfo=timezone.utc)


def test_parser_rejects_dtd_and_oversized_or_invalid_xml():
    with pytest.raises(ValueError, match="DTD"):
        parse_feed(
            b'<!DOCTYPE rss [<!ENTITY x "boom">]><rss><channel/></rss>',
            "https://example.com/feed",
        )
    with pytest.raises(ValueError, match="Ungültiger"):
        parse_feed(b"<rss><item>", "https://example.com/feed")


def test_refresh_keeps_external_content_untrusted_and_links_to_project(
    monkeypatch, tmp_path
):
    with TestClient(_app(monkeypatch, tmp_path)) as client:
        project = client.post(
            "/projects",
            headers=HEADERS,
            json={"name": "Icarus Private Beta"},
        ).json()
        client.app.state.world.client = FakeClient()

        source = client.post(
            "/world/sources",
            headers=HEADERS,
            json={
                "name": "Icarus News",
                "url": "https://example.com/feed.xml",
                "project_id": project["id"],
            },
        )
        assert source.status_code == 201, source.text
        source = source.json()

        report = client.post(
            f"/world/sources/{source['id']}/refresh", headers=HEADERS
        ).json()
        assert report["fetched"] == 1
        assert report["new"] == 1
        assert report["relevant"] == 1

        items = client.get(
            "/world/items?new_only=true&relevant_only=true", headers=HEADERS
        ).json()
        assert len(items) == 1
        item = items[0]
        assert item["project_id"] == project["id"]
        assert item["relevance_score"] == 100
        assert item["source_name"] == "Icarus News"

        episodes = client.app.state.episodes.all_episodes(limit=20)
        episode = next(entry for entry in episodes if entry.id == item["episode_id"])
        assert "ANFANG FREMDER INHALT" in episode.body
        assert "Originalquelle: https://example.com/icarus-beta" in episode.body
        assert episode.project_id == project["id"]
        assert episode.provenance.source_type.value == "web"

        attention = client.get(
            "/chief-of-staff/attention?limit=10", headers=HEADERS
        ).json()
        world = next(signal for signal in attention if signal["id"] == "world:relevant")
        assert "relevante Außenwelt" in world["title"]
        assert world["target_view"] == "system"

        client.post(f"/world/items/{item['id']}/seen", headers=HEADERS).raise_for_status()
        after = client.get(
            "/chief-of-staff/attention?limit=10", headers=HEADERS
        ).json()
        assert "world:relevant" not in {signal["id"] for signal in after}

        second = client.post(
            f"/world/sources/{source['id']}/refresh", headers=HEADERS
        ).json()
        assert second["new"] == 0
        assert second["duplicates"] == 1
        assert len(client.app.state.episodes.all_episodes(limit=20)) == len(episodes)


def test_sources_and_seen_state_survive_full_backup(monkeypatch, tmp_path):
    with TestClient(_app(monkeypatch, tmp_path)) as client:
        client.app.state.world.client = FakeClient(ATOM)
        source = client.post(
            "/world/sources",
            headers=HEADERS,
            json={"name": "Updates", "url": "https://example.com/atom.xml"},
        ).json()
        client.post(
            f"/world/sources/{source['id']}/refresh", headers=HEADERS
        ).raise_for_status()
        item = client.get("/world/items", headers=HEADERS).json()[0]
        client.post(f"/world/items/{item['id']}/seen", headers=HEADERS).raise_for_status()
        backup = client.post("/backups", headers=HEADERS).json()

        client.delete(f"/world/sources/{source['id']}", headers=HEADERS).raise_for_status()
        assert client.get("/world/sources", headers=HEADERS).json() == []

        client.post(
            "/backups/restore",
            headers=HEADERS,
            json={"name": backup["name"]},
        ).raise_for_status()
        restored_sources = client.get("/world/sources", headers=HEADERS).json()
        assert restored_sources[0]["id"] == source["id"]
        restored_items = client.get("/world/items", headers=HEADERS).json()
        assert restored_items[0]["id"] == item["id"]
        assert restored_items[0]["is_new"] is False


def test_world_routes_require_authentication(monkeypatch, tmp_path):
    with TestClient(_app(monkeypatch, tmp_path)) as client:
        assert client.get("/world/sources").status_code == 401
        assert client.get("/world/items").status_code == 401
        assert client.post("/world/refresh").status_code == 401
