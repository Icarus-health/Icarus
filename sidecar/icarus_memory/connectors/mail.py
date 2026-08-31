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
import hashlib
import imaplib
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from typing import Any

DEFAULT_IMAP_PORT = 993
DEFAULT_SMTP_PORT = 587

#: Wie viel vom Text eine einzeln geöffnete Nachricht mitbringt. Großzügig,
#: aber begrenzt — eine Mail mit einem eingebetteten Bild als Base64 hat
#: Megabyte, und die will niemand im Browser stehen haben.
FULL_BODY_LIMIT = 20_000


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

    body: str = ""
    """Der volle Text — nur beim Einzelabruf gefüllt.

    Die Liste trägt ihn nicht: Zwanzig ganze Mails sind ein Vielfaches der
    Datenmenge, und sie stehen ohnehin zusammengefaltet da.
    """

    message_id: str = ""
    """Für `In-Reply-To`. Ohne den Kopf hängt eine Antwort nicht am Verlauf,
    sondern erscheint beim Empfänger als neue Nachricht."""

    reply_to: str = ""
    """`Reply-To`, wo gesetzt. Sonst ist der Absender gemeint."""

    recipients: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)

    def answer_address(self) -> str:
        """An wen eine Antwort geht. `Reply-To` gewinnt — dafür steht er da."""
        return self.reply_to or self.sender

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "subject": self.subject,
            "from": self.sender,
            "date": self.date.astimezone().isoformat() if self.date else None,
            "preview": self.preview,
            "unread": self.unread,
            "body": self.body,
            "message_id": self.message_id,
            "answer_to": self.answer_address(),
            "to": list(self.recipients),
            "cc": list(self.cc),
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

    @property
    def source_account_id(self) -> str:
        """Stabile, nicht geheime Kennung eines IMAP-Kontos."""
        raw = f"{self._config.imap_host}\0{self._config.username}".encode("utf-8")
        return "imap:" + hashlib.sha256(raw).hexdigest()[:16]

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

    def message(self, uid: str) -> Message:
        """Eine einzelne Nachricht mit vollem Text.

        `readonly=True` wie beim Posteingang: Etwas anzusehen darf es nicht als
        gelesen markieren. Wer seine Mail woanders bearbeitet, soll dort
        denselben Zustand vorfinden — Icarus schaut zu, es räumt nicht auf.
        """
        try:
            with imaplib.IMAP4_SSL(
                self._config.imap_host, self._config.imap_port,
                ssl_context=ssl.create_default_context(),
            ) as imap:
                imap.login(self._config.username, self._config.password)
                imap.select("INBOX", readonly=True)
                status, fetched = imap.fetch(str(uid).encode(), "(FLAGS RFC822)")
                if status != "OK" or not fetched or fetched[0] is None:
                    raise MailError(f"Nachricht {uid} nicht gefunden.")
                raw = next(
                    (part[1] for part in fetched
                     if isinstance(part, tuple) and isinstance(part[1], bytes)),
                    None,
                )
                if raw is None:
                    raise MailError(f"Nachricht {uid} nicht lesbar.")
                parsed = email.message_from_bytes(raw)
                flags = str(fetched[0][0]) if isinstance(fetched[0], tuple) else ""
                text = _body(parsed, limit=FULL_BODY_LIMIT)
                return Message(
                    uid=str(uid),
                    subject=_decode(parsed.get("Subject")),
                    sender=_decode(parsed.get("From")),
                    date=self._parse_date(parsed.get("Date")),
                    preview=" ".join(text.split())[:300],
                    unread="\\Seen" not in flags,
                    body=text,
                    message_id=(parsed.get("Message-ID") or "").strip(),
                    reply_to=_decode(parsed.get("Reply-To")),
                    recipients=[addr for _, addr in email.utils.getaddresses(parsed.get_all("To", [])) if addr],
                    cc=[addr for _, addr in email.utils.getaddresses(parsed.get_all("Cc", [])) if addr],
                )
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

    def send(
        self, to: str, subject: str, body: str, in_reply_to: str = ""
    ) -> str:
        """Versendet eine Mail. Wird ausschließlich nach erteilter Freigabe gerufen."""
        if not self._config.smtp_host:
            raise MailError("Kein SMTP-Server konfiguriert (ICARUS_SMTP_HOST).")

        message = EmailMessage()
        message["From"] = self._config.sender
        message["To"] = to
        message["Subject"] = subject
        message["Date"] = email.utils.formatdate(localtime=True)
        message["Message-ID"] = email.utils.make_msgid()
        if in_reply_to:
            # Ohne diese beiden Köpfe erscheint eine Antwort beim Empfänger als
            # neue Nachricht statt im Verlauf. Das ist kein Schönheitsfehler:
            # Wer zwanzig Mails am Tag bekommt, findet sie dann nicht wieder.
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
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
