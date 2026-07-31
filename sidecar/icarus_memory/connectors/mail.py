"""Mail über IMAP und SMTP.

Bewusst IMAP/SMTP statt Anbieter-APIs: Das funktioniert mit iCloud, Fastmail,
Mailbox.org, jedem eigenen Server und — mit App-Passwort — auch mit Gmail und
Outlook. Kein OAuth-Tanz, kein Anbieter, der die Schnittstelle abkündigt. Für
ein System, das Jahre laufen soll, ist das offene Protokoll die sicherere Wette.

**Sicherheitshinweis, der hier zentral ist:** Eine E-Mail ist der gefährlichste
Injection-Weg, den es gibt — jeder kann dir eine schreiben. Der Inhalt wird
deshalb ausnahmslos als fremd markiert und kontaminiert die Runde. Ein
Assistent, der Mails liest und danach ungefragt handelt, führt aus, was
Fremde ihm schreiben.
"""

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from typing import Any

DEFAULT_IMAP_PORT = 993
DEFAULT_SMTP_PORT = 587


class MailError(Exception):
    pass


@dataclass
class MailConfig:
    imap_host: str
    username: str
    password: str
    smtp_host: str = ""
    imap_port: int = DEFAULT_IMAP_PORT
    smtp_port: int = DEFAULT_SMTP_PORT
    from_address: str = ""

    @property
    def sender(self) -> str:
        return self.from_address or self.username

    @classmethod
    def from_env(cls, env: dict[str, str]) -> MailConfig | None:
        host = env.get("ICARUS_IMAP_HOST")
        user = env.get("ICARUS_MAIL_USER")
        password = env.get("ICARUS_MAIL_PASSWORD")
        if not (host and user and password):
            return None
        return cls(
            imap_host=host,
            username=user,
            password=password,
            smtp_host=env.get("ICARUS_SMTP_HOST", ""),
            imap_port=int(env.get("ICARUS_IMAP_PORT", DEFAULT_IMAP_PORT)),
            smtp_port=int(env.get("ICARUS_SMTP_PORT", DEFAULT_SMTP_PORT)),
            from_address=env.get("ICARUS_MAIL_FROM", ""),
        )


@dataclass
class Message:
    uid: str
    subject: str
    sender: str
    date: datetime | None
    preview: str
    unread: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "subject": self.subject,
            "from": self.sender,
            "date": self.date.astimezone().isoformat() if self.date else None,
            "preview": self.preview,
            "unread": self.unread,
        }


def _decode(raw: str | None) -> str:
    """Entschlüsselt MIME-kodierte Kopfzeilen (=?utf-8?B?...?=)."""
    if not raw:
        return ""
    parts = []
    for text, charset in email.header.decode_header(raw):
        if isinstance(text, bytes):
            parts.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts).strip()


def _body(message: email.message.Message, limit: int = 500) -> str:
    """Zieht den Textkörper heraus, bevorzugt text/plain."""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")[:limit]
        return ""
    payload = message.get_payload(decode=True) or b""
    charset = message.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")[:limit]


class MailConnector:
    """Liest Nachrichten und versendet sie — Versand nur über die Freigabe."""

    def __init__(self, config: MailConfig) -> None:
        self._config = config

    # -- Lesen -------------------------------------------------------------

    def inbox(self, limit: int = 10, unread_only: bool = False) -> list[Message]:
        criteria = "UNSEEN" if unread_only else "ALL"
        try:
            with imaplib.IMAP4_SSL(
                self._config.imap_host, self._config.imap_port,
                ssl_context=ssl.create_default_context(),
            ) as imap:
                imap.login(self._config.username, self._config.password)
                imap.select("INBOX", readonly=True)  # readonly: nichts als gelesen markieren
                status, data = imap.search(None, criteria)
                if status != "OK":
                    raise MailError(f"Suche fehlgeschlagen: {status}")

                uids = (data[0].split() if data and data[0] else [])[-limit:]
                messages = []
                for uid in reversed(uids):
                    status, fetched = imap.fetch(uid, "(FLAGS RFC822)")
                    if status != "OK" or not fetched:
                        continue
                    raw = next(
                        (part[1] for part in fetched
                         if isinstance(part, tuple) and isinstance(part[1], bytes)),
                        None,
                    )
                    if raw is None:
                        continue
                    parsed = email.message_from_bytes(raw)
                    flags = str(fetched[0][0]) if isinstance(fetched[0], tuple) else ""
                    messages.append(Message(
                        uid=uid.decode(),
                        subject=_decode(parsed.get("Subject")),
                        sender=_decode(parsed.get("From")),
                        date=self._parse_date(parsed.get("Date")),
                        preview=" ".join(_body(parsed).split())[:300],
                        unread="\\Seen" not in flags,
                    ))
                return messages
        except (imaplib.IMAP4.error, OSError, ssl.SSLError) as exc:
            raise MailError(f"IMAP-Zugriff fehlgeschlagen: {exc}") from exc

    @staticmethod
    def _parse_date(raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            return email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None

    # -- Senden ------------------------------------------------------------

    def send(self, to: str, subject: str, body: str) -> str:
        """Versendet eine Mail. Wird ausschließlich nach erteilter Freigabe gerufen."""
        if not self._config.smtp_host:
            raise MailError("Kein SMTP-Server konfiguriert (ICARUS_SMTP_HOST).")

        message = EmailMessage()
        message["From"] = self._config.sender
        message["To"] = to
        message["Subject"] = subject
        message["Date"] = email.utils.formatdate(localtime=True)
        message["Message-ID"] = email.utils.make_msgid()
        message.set_content(body)

        try:
            with smtplib.SMTP(self._config.smtp_host, self._config.smtp_port, timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(self._config.username, self._config.password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            raise MailError(f"Versand fehlgeschlagen: {exc}") from exc

        return f"Gesendet an {to}."


__all__ = ["MailConfig", "MailConnector", "MailError", "Message"]
