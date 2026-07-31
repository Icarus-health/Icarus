"""Kalender über CalDAV.

Wie bei Mail bewusst das offene Protokoll statt Anbieter-APIs: CalDAV
funktioniert mit iCloud, Nextcloud, Fastmail, Radicale und Google. Kein OAuth,
keine Schnittstelle, die in zwei Jahren abgekündigt wird.

Der iCalendar-Parser ist absichtlich klein gehalten und deckt das ab, was für
Anzeige und Anlegen nötig ist: Zeitpunkt, Titel, Ort, Teilnehmer. Ein
vollständiger RFC-5545-Parser samt Wiederholungsregeln ist ein eigenes Projekt;
was hier fehlt, ist unten benannt.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import httpx


class CalendarError(Exception):
    pass


@dataclass
class CalendarConfig:
    url: str
    username: str
    password: str

    @classmethod
    def from_env(cls, env: dict[str, str]) -> CalendarConfig | None:
        url = env.get("ICARUS_CALDAV_URL")
        user = env.get("ICARUS_CALDAV_USER")
        password = env.get("ICARUS_CALDAV_PASSWORD")
        if not (url and user and password):
            return None
        return cls(url=url, username=user, password=password)


@dataclass
class Event:
    uid: str
    summary: str
    start: datetime | None
    end: datetime | None
    location: str = ""
    attendees: list[str] = field(default_factory=list)
    all_day: bool = False

    def to_dict(self) -> dict[str, Any]:
        def iso(v: datetime | None) -> str | None:
            return v.astimezone().isoformat() if v else None

        return {
            "uid": self.uid,
            "summary": self.summary,
            "start": iso(self.start),
            "end": iso(self.end),
            "location": self.location,
            "attendees": list(self.attendees),
            "all_day": self.all_day,
        }


# -- iCalendar -------------------------------------------------------------


def _unfold(text: str) -> str:
    """iCalendar bricht lange Zeilen um; Fortsetzungen beginnen mit Leerzeichen."""
    return re.sub(r"\r?\n[ \t]", "", text)


def _parse_dt(value: str, params: str) -> tuple[datetime | None, bool]:
    """Liest DTSTART/DTEND. Gibt Zeitpunkt und Ganztags-Flag zurück."""
    value = value.strip()
    if "VALUE=DATE" in params.upper() and len(value) == 8:
        parsed = datetime.combine(
            date(int(value[0:4]), int(value[4:6]), int(value[6:8])), time.min
        )
        return parsed.replace(tzinfo=timezone.utc), True
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), False
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc), False
    except ValueError:
        return None, False


def parse_events(ical: str) -> list[Event]:
    """Zieht VEVENT-Blöcke aus iCalendar-Text."""
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", _unfold(ical), re.DOTALL):
        fields: dict[str, tuple[str, str]] = {}
        attendees = []
        for line in block.strip().splitlines():
            if ":" not in line:
                continue
            head, _, value = line.partition(":")
            name, _, params = head.partition(";")
            name = name.strip().upper()
            if name == "ATTENDEE":
                attendees.append(value.replace("mailto:", "").strip())
            else:
                fields[name] = (value.strip(), params)

        start, all_day = _parse_dt(*fields["DTSTART"]) if "DTSTART" in fields else (None, False)
        end, _ = _parse_dt(*fields["DTEND"]) if "DTEND" in fields else (None, False)

        events.append(Event(
            uid=fields.get("UID", (str(uuid.uuid4()), ""))[0],
            summary=fields.get("SUMMARY", ("(ohne Titel)", ""))[0],
            start=start,
            end=end,
            location=fields.get("LOCATION", ("", ""))[0],
            attendees=attendees,
            all_day=all_day,
        ))
    return sorted(events, key=lambda e: (e.start is None, e.start or datetime.max.replace(tzinfo=timezone.utc)))


def build_event(
    summary: str,
    start: datetime,
    end: datetime,
    location: str = "",
    attendees: list[str] | None = None,
    uid: str | None = None,
) -> tuple[str, str]:
    """Baut einen VEVENT. Gibt UID und iCalendar-Text zurück."""
    uid = uid or f"{uuid.uuid4()}@icarus"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Icarus//DE", "BEGIN:VEVENT",
        f"UID:{uid}", f"DTSTAMP:{stamp}",
        f"DTSTART:{start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
        f"DTEND:{end.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
        f"SUMMARY:{summary}",
    ]
    if location:
        lines.append(f"LOCATION:{location}")
    for attendee in attendees or []:
        lines.append(f"ATTENDEE;RSVP=TRUE:mailto:{attendee}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return uid, "\r\n".join(lines) + "\r\n"


# -- Zugriff ---------------------------------------------------------------

_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop><C:calendar-data/></D:prop>
  <C:filter>
    <C:comp-filter name="VCALENDAR">
      <C:comp-filter name="VEVENT">
        <C:time-range start="{start}" end="{end}"/>
      </C:comp-filter>
    </C:comp-filter>
  </C:filter>
</C:calendar-query>"""


class CalendarConnector:
    def __init__(self, config: CalendarConfig) -> None:
        self._config = config

    def _client(self) -> httpx.Client:
        return httpx.Client(
            auth=(self._config.username, self._config.password),
            timeout=30.0,
            follow_redirects=True,
        )

    def events(self, days: int = 7, at: datetime | None = None) -> list[Event]:
        """Termine im Zeitfenster ab jetzt."""
        at = at or datetime.now(timezone.utc)
        body = _REPORT.format(
            start=at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            end=(at + timedelta(days=days)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        )
        try:
            with self._client() as client:
                response = client.request(
                    "REPORT", self._config.url,
                    headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
                    content=body.encode("utf-8"),
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CalendarError(f"CalDAV-Zugriff fehlgeschlagen: {exc}") from exc

        return parse_events(response.text)

    def create(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        location: str = "",
        attendees: list[str] | None = None,
    ) -> str:
        """Legt einen Termin an. Mit Gästen ist das außenwirksam — die
        Einstufung passiert in der Werkzeugschicht, nicht hier."""
        uid, ical = build_event(summary, start, end, location, attendees)
        url = self._config.url.rstrip("/") + f"/{uid}.ics"
        try:
            with self._client() as client:
                response = client.put(
                    url,
                    headers={"Content-Type": "text/calendar; charset=utf-8",
                             "If-None-Match": "*"},
                    content=ical.encode("utf-8"),
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CalendarError(f"Termin konnte nicht angelegt werden: {exc}") from exc

        wann = start.astimezone().strftime("%d.%m.%Y um %H:%M")
        return f"Termin {summary!r} am {wann} angelegt."


__all__ = [
    "CalendarConfig",
    "CalendarConnector",
    "CalendarError",
    "Event",
    "build_event",
    "parse_events",
]
