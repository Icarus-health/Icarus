"""Bekannte Mailanbieter, damit niemand einen Hostnamen kennen muss.

Ein Feld „IMAP-Host“ setzt Wissen voraus, das außerhalb der IT niemand hat, und
es ist obendrein ratlos machend: Wer es nicht weiß, weiß auch nicht, wonach er
suchen soll. Dabei ist die Antwort für die allermeisten Menschen eine von einem
Dutzend Zeilen.

Also: Anbieter auswählen, Adresse eintragen, fertig. Der Rest steht hier.

## Warum das keine Abhängigkeit von den Anbietern schafft

Die Tabelle ist eine **Bequemlichkeit, kein Zwang**. „Anderer Anbieter“ bleibt
immer wählbar, und dann stehen die Felder wieder offen da. Das Protokoll bleibt
IMAP und SMTP; wir raten nur die Adresse.

Ändert ein Anbieter seinen Hostnamen, ist eine Zeile hier falsch und der Nutzer
kann sie überschreiben. Das ist ein anderer Schadensfall als eine
Anbieter-API, die abgekündigt wird und den ganzen Weg mitnimmt.

## Was die App dem Nutzer sagen muss

Bei Gmail, Outlook, Yahoo und iCloud reicht das normale Kennwort **nicht** —
diese Anbieter verlangen ein eigens erzeugtes App-Passwort. Wer das nicht weiß,
tippt dreimal sein richtiges Kennwort ein, bekommt dreimal „Anmeldung
fehlgeschlagen“ und hält das Programm für kaputt. Deshalb steht der Hinweis samt
Link an jedem betroffenen Anbieter und wird in der Oberfläche angezeigt, bevor
jemand das erste Mal auf „Prüfen“ drückt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MailProvider:
    id: str
    label: str
    imap_host: str
    smtp_host: str
    imap_port: int = 993
    smtp_port: int = 587

    app_password: bool = False
    """Braucht ein eigens erzeugtes Passwort statt des Kontokennworts."""

    hint: str = ""
    help_url: str = ""
    domains: tuple[str, ...] = ()
    """Adressendungen, an denen sich der Anbieter erkennen lässt."""

    caldav_url: str = ""
    """CalDAV-Adresse, wo es eine gibt, die mit App-Passwort funktioniert."""

    caldav_note: str = ""
    """Warum es *keine* gibt.

    Google und Microsoft haben die einfache Anmeldung an CalDAV abgeschaltet.
    Das zu verschweigen hieße, den Nutzer eine Viertelstunde suchen zu lassen,
    bevor er aufgibt und annimmt, das Programm könne es nicht. Es kann es — der
    Anbieter lässt es nicht zu, und genau das gehört dort zu stehen.
    """

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "caldav_url": self.caldav_url,
            "caldav_note": self.caldav_note,
            "label": self.label,
            "imap_host": self.imap_host,
            "smtp_host": self.smtp_host,
            "imap_port": self.imap_port,
            "smtp_port": self.smtp_port,
            "app_password": self.app_password,
            "hint": self.hint,
            "help_url": self.help_url,
        }


#: Reihenfolge ist die Anzeigereihenfolge. Häufigstes zuerst — die Liste soll
#: nicht alphabetisch vollständig sein, sondern schnell zum Ziel führen.
PROVIDERS: tuple[MailProvider, ...] = (
    MailProvider(
        id="gmail", label="Gmail / Google Workspace",
        imap_host="imap.gmail.com", smtp_host="smtp.gmail.com",
        app_password=True,
        hint="Google verlangt ein App-Passwort. Das normale Kennwort wird "
             "abgelehnt — auch wenn es richtig ist.",
        help_url="https://myaccount.google.com/apppasswords",
        domains=("gmail.com", "googlemail.com"),
        caldav_note="Google lässt CalDAV nicht mehr mit App-Passwort zu. Der Kalender bleibt hier außen vor — die Mail funktioniert.",
    ),
    MailProvider(
        id="icloud", label="iCloud",
        imap_host="imap.mail.me.com", smtp_host="smtp.mail.me.com",
        app_password=True,
        hint="Apple verlangt ein app-spezifisches Passwort.",
        help_url="https://support.apple.com/de-de/102654",
        domains=("icloud.com", "me.com", "mac.com"),
        caldav_url="https://caldav.icloud.com/",
    ),
    MailProvider(
        id="outlook", label="Outlook / Microsoft 365",
        imap_host="outlook.office365.com", smtp_host="smtp.office365.com",
        app_password=True,
        hint="Microsoft verlangt ein App-Passwort, und im Geschäftskonto muss "
             "die Administration IMAP freigeschaltet haben.",
        help_url="https://account.microsoft.com/security",
        domains=("outlook.com", "hotmail.com", "live.com", "msn.com"),
        caldav_note="Microsoft bietet keinen CalDAV-Zugang mehr. Der Kalender bleibt hier außen vor — die Mail funktioniert.",
    ),
    MailProvider(
        id="gmx", label="GMX",
        imap_host="imap.gmx.net", smtp_host="mail.gmx.net",
        hint="In den GMX-Einstellungen muss „IMAP und POP3“ eingeschaltet sein.",
        help_url="https://hilfe.gmx.net/pop-imap/imap.html",
        domains=("gmx.de", "gmx.net", "gmx.at", "gmx.ch"),
        caldav_url="https://caldav.gmx.net/begenda/dav/",
    ),
    MailProvider(
        id="webde", label="WEB.DE",
        imap_host="imap.web.de", smtp_host="smtp.web.de",
        hint="In den WEB.DE-Einstellungen muss „IMAP“ eingeschaltet sein.",
        help_url="https://hilfe.web.de/pop-imap/imap.html",
        domains=("web.de",),
        caldav_url="https://caldav.web.de/begenda/dav/",
    ),
    MailProvider(
        id="mailbox", label="mailbox.org",
        imap_host="imap.mailbox.org", smtp_host="smtp.mailbox.org",
        domains=("mailbox.org",),
        caldav_url="https://dav.mailbox.org/caldav/",
    ),
    MailProvider(
        id="posteo", label="Posteo",
        imap_host="posteo.de", smtp_host="posteo.de",
        domains=("posteo.de", "posteo.net"),
        caldav_url="https://posteo.de:8443/",
    ),
    MailProvider(
        id="fastmail", label="Fastmail",
        imap_host="imap.fastmail.com", smtp_host="smtp.fastmail.com",
        app_password=True,
        hint="Fastmail verlangt ein App-Passwort.",
        help_url="https://app.fastmail.com/settings/security/apppw",
        domains=("fastmail.com", "fastmail.fm"),
        caldav_url="https://caldav.fastmail.com/dav/calendars/",
    ),
    MailProvider(
        id="yahoo", label="Yahoo",
        imap_host="imap.mail.yahoo.com", smtp_host="smtp.mail.yahoo.com",
        app_password=True,
        hint="Yahoo verlangt ein App-Passwort.",
        help_url="https://login.yahoo.com/account/security",
        domains=("yahoo.com", "yahoo.de", "ymail.com"),
    ),
    MailProvider(
        id="zoho", label="Zoho Mail",
        imap_host="imap.zoho.eu", smtp_host="smtp.zoho.eu",
        domains=("zoho.com", "zohomail.eu"),
    ),
)

BY_ID = {p.id: p for p in PROVIDERS}


def guess(address: str) -> MailProvider | None:
    """Rät den Anbieter aus der Adresse.

    Damit ist der Regelfall ein einziges Feld: Adresse eintippen, und die Hosts
    stehen schon da. Wer eine eigene Domain hat, bekommt `None` und trägt die
    Felder von Hand ein — das ist genau die Gruppe, die das auch kann.
    """
    if "@" not in address:
        return None
    domain = address.rsplit("@", 1)[1].strip().lower()
    for provider in PROVIDERS:
        if domain in provider.domains:
            return provider
    return None


def catalogue() -> list[dict[str, Any]]:
    return [p.to_dict() for p in PROVIDERS]


__all__ = ["BY_ID", "PROVIDERS", "MailProvider", "catalogue", "guess"]
