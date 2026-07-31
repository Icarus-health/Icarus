"""Konnektoren zu aktuellen Informationen — Säule 3.

Bewusst offene Protokolle (IMAP, SMTP, CalDAV) statt Anbieter-APIs. Das
funktioniert mit iCloud, Fastmail, Nextcloud, eigenen Servern und — mit
App-Passwort — auch mit Gmail und Outlook, ohne OAuth-Tanz und ohne
Schnittstelle, die in zwei Jahren abgekündigt wird.

Jeder Konnektor ist optional. Ist er nicht eingerichtet, fehlt sein Bereich im
Dashboard mit einem Hinweis — die App funktioniert weiter.
"""

from .calendar import CalendarConfig, CalendarConnector, CalendarError, Event
from .mail import MailConfig, MailConnector, MailError, Message

__all__ = [
    "CalendarConfig",
    "CalendarConnector",
    "CalendarError",
    "Event",
    "MailConfig",
    "MailConnector",
    "MailError",
    "Message",
]
