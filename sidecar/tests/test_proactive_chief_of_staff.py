from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from icarus_memory.connectors.calendar import Event
from icarus_memory.runtime import create_app


HEADERS = {"x-icarus-token": "attention-test"}


class FakeCalendar:
    def __init__(self, events):
        self._events = list(events)

    def events(self, days=7, at=None):
        return list(self._events)


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ICARUS_SIDECAR_TOKEN", "attention-test")
    monkeypatch.setenv("ICARUS_KEYCHAIN_BACKEND", "none")
    monkeypatch.delenv("ICARUS_BROWSER_WORKER", raising=False)
    return create_app()


def test_attention_budget_is_explained_and_snoozable(monkeypatch, tmp_path):
    with TestClient(_app(monkeypatch, tmp_path)) as client:
        overdue = client.post(
            "/tasks",
            headers=HEADERS,
            json={
                "title": "Überfällige Zusage beantworten",
                "due": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
            },
        )
        overdue.raise_for_status()
        for index in range(8):
            response = client.post(
                "/tasks",
                headers=HEADERS,
                json={
                    "title": f"Bald fällige Aufgabe {index}",
                    "due": (
                        datetime.now(timezone.utc) + timedelta(days=index % 6 + 1)
                    ).isoformat(),
                },
            )
            response.raise_for_status()

        attention = client.get(
            "/chief-of-staff/attention?limit=5", headers=HEADERS
        ).json()
        assert len(attention) == 5
        assert attention[0]["title"] == "Überfällige Zusage beantworten"
        assert "überfällig" in attention[0]["reason"].lower()
        assert attention[0]["next_action"]
        assert attention[0]["consequence"]
        assert all(
            attention[index]["score"] >= attention[index + 1]["score"]
            for index in range(len(attention) - 1)
        )

        signal = attention[0]
        snooze = client.post(
            f"/chief-of-staff/attention/{signal['id']}/snooze",
            headers=HEADERS,
            json={"fingerprint": signal["fingerprint"], "hours": 24},
        )
        snooze.raise_for_status()

        after = client.get(
            "/chief-of-staff/attention?limit=10", headers=HEADERS
        ).json()
        assert signal["id"] not in {item["id"] for item in after}


def test_changed_fingerprint_reappears_after_dismissal(monkeypatch, tmp_path):
    with TestClient(_app(monkeypatch, tmp_path)) as client:
        task = client.post(
            "/tasks",
            headers=HEADERS,
            json={
                "title": "Frist mit neuem Sachstand",
                "due": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            },
        ).json()
        first = next(
            item
            for item in client.get(
                "/chief-of-staff/attention?limit=10", headers=HEADERS
            ).json()
            if item["source_id"] == task["id"]
        )
        client.post(
            f"/chief-of-staff/attention/{first['id']}/dismiss",
            headers=HEADERS,
            json={"fingerprint": first["fingerprint"]},
        ).raise_for_status()
        assert first["id"] not in {
            item["id"]
            for item in client.get(
                "/chief-of-staff/attention?limit=10", headers=HEADERS
            ).json()
        }

        # Eine Wiedereröffnung verändert den verbindlichen Aufgabenbestand und
        # damit den Fingerabdruck. Derselbe Sachverhalt darf erneut erscheinen.
        client.post(f"/tasks/{task['id']}/done", headers=HEADERS).raise_for_status()
        client.post(f"/tasks/{task['id']}/reopen", headers=HEADERS).raise_for_status()
        changed = client.get(
            "/chief-of-staff/attention?limit=10", headers=HEADERS
        ).json()
        assert first["id"] in {item["id"] for item in changed}


def test_meeting_prep_connects_calendar_project_tasks_decisions_and_episodes(
    monkeypatch, tmp_path
):
    with TestClient(_app(monkeypatch, tmp_path)) as client:
        event = Event(
            uid="icarus-review",
            summary="Icarus Private Beta Review",
            start=datetime.now(timezone.utc) + timedelta(hours=4),
            end=datetime.now(timezone.utc) + timedelta(hours=5),
            location="Online",
            attendees=["soeren@example.com", "ada@example.com"],
        )
        client.app.state.calendar = FakeCalendar([event])

        project = client.post(
            "/projects",
            headers=HEADERS,
            json={
                "name": "Icarus Private Beta",
                "description": "Review und Releasevorbereitung",
            },
        ).json()
        client.post(
            "/tasks",
            headers=HEADERS,
            json={
                "title": "Freigabekriterien festlegen",
                "project_id": project["id"],
            },
        ).raise_for_status()
        client.post(
            "/notes",
            headers=HEADERS,
            json={
                "title": "Local-first bleibt verbindlich",
                "body": "Keine zweite Wahrheit in der Cloud.",
                "kind": "decision",
                "project_id": project["id"],
            },
        ).raise_for_status()
        client.post(
            "/episodes",
            headers=HEADERS,
            json={
                "title": "Icarus Review mit Ada",
                "body": "Ada prüft die Private Beta.",
                "kind": "event",
                "participants": ["Ada"],
                "project_id": project["id"],
            },
        ).raise_for_status()

        meetings = client.get(
            "/chief-of-staff/meetings?days=2", headers=HEADERS
        ).json()
        assert meetings["error"] is None
        assert meetings["items"][0]["uid"] == "icarus-review"

        prep = client.get(
            "/chief-of-staff/meetings/icarus-review/prep", headers=HEADERS
        ).json()
        assert prep["event"]["summary"] == "Icarus Private Beta Review"
        assert prep["related_projects"][0]["project"]["id"] == project["id"]
        assert prep["related_projects"][0]["open_tasks"][0]["title"] == "Freigabekriterien festlegen"
        assert prep["related_projects"][0]["decisions"][0]["title"] == "Local-first bleibt verbindlich"
        assert prep["related_episodes"][0]["episode"]["title"] == "Icarus Review mit Ada"
        assert "Freigabekriterien festlegen" in prep["suggested_outcome"]
        assert prep["provenance"]["generated_without_model"] is True
        assert len(prep["questions"]) >= 3


def test_proactive_routes_require_sidecar_token(monkeypatch, tmp_path):
    with TestClient(_app(monkeypatch, tmp_path)) as client:
        assert client.get("/chief-of-staff/attention").status_code == 401
        assert client.get("/chief-of-staff/meetings").status_code == 401
