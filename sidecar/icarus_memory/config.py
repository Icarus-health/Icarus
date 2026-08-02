"""Einstellungen, die einen Neustart überleben.

Bis hierher kam alles aus Umgebungsvariablen: Anbieter, Schlüssel, Mailserver,
freigegebene Ordner. Für eine Entwicklungsumgebung ist das richtig. Für eine App,
die jemand herunterlädt, ist es das Ende — niemand legt eine `.env` an, bevor er
ein Programm zum ersten Mal öffnet.

Deshalb zwei Ablagen mit klarer Trennung:

* **`einstellungen.json`** im Datenverzeichnis — alles, was kein Geheimnis ist:
  welcher Anbieter, welches Modell, welcher Mailserver, welche Ordner
  freigegeben sind.
* **Der Schlüsselbund** (`secrets.py`) — alles, was eines ist: API-Schlüssel,
  Mailpasswort, CalDAV-Passwort.

Die Trennung ist keine Förmlichkeit. Die Einstellungsdatei landet in Backups, in
Sicherungen des Datenverzeichnisses, womöglich in einem Cloud-Ordner. Ein
Schlüssel darin wäre genau der Klartext auf der Platte, den `secrets.py`
vermeiden soll.

## Vorrang

Umgebungsvariablen schlagen die Datei. Wer `ICARUS_PROVIDER=ollama` vor den
Start setzt, bekommt Ollama — auch wenn in der Datei etwas anderes steht. Das
ist der Weg, einen Testlauf zu fahren, ohne die Einstellungen des Nutzers
anzufassen, und es ist dieselbe Regel wie in `secrets.load_into_env()`.

## Warum kein Konto

Es gibt kein „Anmelden". Icarus kennt keinen Server, bei dem man sich anmelden
könnte, und das ist der Punkt des ganzen Projekts: Der Bestand liegt auf dem
Rechner der Person. Was beim Einrichten passiert, ist deshalb kein Login,
sondern die Frage, welchem Anbieter das Gespräch anvertraut wird — und die
Antwort darf „keinem" sein.
"""

from __future__ import annotations

import json
import os
import weakref
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .secrets import Keychain, KeychainError

DATEINAME = "einstellungen.json"

#: Anbieter, die die Oberfläche anbieten darf. `""` heißt: kein Modell, und das
#: ist eine gültige Wahl — der Gedächtniskern läuft ohne.
PROVIDERS = ("", "openai", "anthropic", "ollama")

#: Voreingestellte Modelle je Anbieter. Nur Vorschläge; wer ein anderes will,
#: trägt es ein.
DEFAULT_MODELS = {
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-sonnet-5",
    "ollama": "llama3.1",
}

#: Welcher Schlüsselbund-Eintrag zu welchem Anbieter gehört.
PROVIDER_SECRET = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

#: Geheimnisse, die über die Einrichtung gesetzt werden können. Der Wert ist
#: der Name im Schlüsselbund; die Einstellungsdatei sieht keinen davon.
SECRET_FIELDS = {
    "api_key": None,          # hängt vom gewählten Anbieter ab
    "mail_password": "ICARUS_MAIL_PASSWORD",
    "calendar_password": "ICARUS_CALDAV_PASSWORD",
}


@dataclass
class MailSettings:
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    user: str = ""
    sender: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.imap_host and self.user)


@dataclass
class CalendarSettings:
    url: str = ""
    user: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.url)


@dataclass
class ScheduleSettings:
    """Der mitlaufende Prozess.

    Standardmäßig aus, und die Modellnutzung darin noch einmal getrennt: Ein
    Zeitplan, der ungefragt einen Anbieter ruft, gibt fremdes Geld aus.
    """

    enabled: bool = False
    interval_minutes: int = 240
    with_model: bool = False

    sources: dict[str, str] = field(default_factory=dict)
    """Ordner, die erneut eingelesen werden — Pfad auf Adapternamen.

    Dass ein zweiter Lauf nichts doppelt anlegt, steckt im Digest der
    Episodenschicht. Ohne diese Zusicherung wäre wiederholtes Einlesen keine
    Option.
    """

    backup: bool = True
    """Snapshot bei jedem Lauf. Der billigste Schritt mit dem größten Nutzen."""


@dataclass
class Settings:
    provider: str = ""
    model: str = ""
    endpoint: str = ""
    """Eigene Adresse des Anbieters. Für Ollama oder einen Proxy."""

    file_roots: list[str] = field(default_factory=list)
    """Ordner, aus denen gelesen werden darf.

    Leer heißt: gar kein Dateizugriff. Es gibt bewusst keinen Vorgabewert wie
    das Home-Verzeichnis — das wäre die Bequemlichkeit, die den Schutz aufhebt.
    Auch der Einrichtungsassistent schlägt keinen vor.
    """

    mail: MailSettings = field(default_factory=MailSettings)
    calendar: CalendarSettings = field(default_factory=CalendarSettings)
    schedule: ScheduleSettings = field(default_factory=ScheduleSettings)

    onboarded: bool = False
    """Hat jemand die Einrichtung einmal bis zum Ende durchlaufen?

    Nicht „ist alles eingerichtet": Man darf jeden Schritt überspringen. Das
    Kennzeichen sagt nur, dass die App nicht mehr beim Start in den Assistenten
    springen soll.
    """

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        """Liest Einstellungen und überliest Unbekanntes.

        Eine Datei aus einer neueren Version darf eine ältere nicht zum
        Absturz bringen — sie hat vielleicht Felder, die es hier noch nicht
        gibt. Weglassen ist richtiger als scheitern.
        """
        mail = data.get("mail") or {}
        calendar = data.get("calendar") or {}
        schedule = data.get("schedule") or {}
        return cls(
            provider=str(data.get("provider", "") or ""),
            model=str(data.get("model", "") or ""),
            endpoint=str(data.get("endpoint", "") or ""),
            file_roots=[str(p) for p in data.get("file_roots", []) if str(p).strip()],
            mail=MailSettings(
                imap_host=str(mail.get("imap_host", "") or ""),
                imap_port=int(mail.get("imap_port") or 993),
                smtp_host=str(mail.get("smtp_host", "") or ""),
                smtp_port=int(mail.get("smtp_port") or 587),
                user=str(mail.get("user", "") or ""),
                sender=str(mail.get("sender", "") or ""),
            ),
            calendar=CalendarSettings(
                url=str(calendar.get("url", "") or ""),
                user=str(calendar.get("user", "") or ""),
            ),
            schedule=ScheduleSettings(
                enabled=bool(schedule.get("enabled", False)),
                interval_minutes=int(schedule.get("interval_minutes") or 240),
                with_model=bool(schedule.get("with_model", False)),
                sources={str(k): str(v) for k, v in (schedule.get("sources") or {}).items()},
                backup=bool(schedule.get("backup", True)),
            ),
            onboarded=bool(data.get("onboarded", False)),
        )


# Geladene Settings-Objekte bleiben in der laufenden App referenziert. Ein
# Restore ersetzt deshalb nicht einfach das Objekt, sondern aktualisiert genau
# diese Instanz. Schwache Referenzen verhindern, dass Tests oder beendete Apps
# durch dieses Register am Leben gehalten werden.
_LIVE_SETTINGS: dict[Path, list[weakref.ReferenceType[Settings]]] = {}
_APPLIED_ENV: dict[int, set[str]] = {}


def path_for(data_dir: Path) -> Path:
    return Path(data_dir) / DATEINAME


def _read(data_dir: Path) -> Settings:
    target = path_for(data_dir)
    try:
        return Settings.from_dict(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return Settings()


def _register(data_dir: Path, settings: Settings) -> None:
    key = path_for(data_dir).resolve()
    alive = [reference for reference in _LIVE_SETTINGS.get(key, []) if reference()]
    alive.append(weakref.ref(settings))
    _LIVE_SETTINGS[key] = alive


def load(data_dir: Path) -> Settings:
    """Liest und registriert die Einstellungen der laufenden Installation.

    Eine kaputte Datei blockiert den Start nicht. Ein leeres Formular ist
    reparierbar, ein Programm, das nicht mehr an seinen Bestand kommt, nicht.
    """
    settings = _read(data_dir)
    _register(data_dir, settings)
    return settings


def reload_registered(data_dir: Path) -> int:
    """Aktualisiert laufende Settings-Objekte nach einer Wiederherstellung.

    Der Server hält ``app.state.settings`` über seine gesamte Lebensdauer. Würde
    nur die JSON-Datei zurückgespielt, blieben Agent, Mail und Kalender bis zum
    Neustart auf dem alten Stand. Hier wird die vorhandene Instanz in-place
    ersetzt und anschließend ihre von Icarus gesetzte Umgebung neu aufgebaut.
    """
    key = path_for(data_dir).resolve()
    restored = _read(data_dir)
    alive: list[weakref.ReferenceType[Settings]] = []

    for reference in _LIVE_SETTINGS.get(key, []):
        current = reference()
        if current is None:
            continue
        fresh = Settings.from_dict(restored.to_dict())
        current.provider = fresh.provider
        current.model = fresh.model
        current.endpoint = fresh.endpoint
        current.file_roots = fresh.file_roots
        current.mail = fresh.mail
        current.calendar = fresh.calendar
        current.schedule = fresh.schedule
        current.onboarded = fresh.onboarded
        apply_to_env(current)
        alive.append(reference)

    if alive:
        _LIVE_SETTINGS[key] = alive
    else:
        _LIVE_SETTINGS.pop(key, None)
    return len(alive)


def save(data_dir: Path, settings: Settings) -> Path:
    """Schreibt die Einstellungen mit 0600.

    Keine Geheimnisse darin, aber Mailadressen und Serveradressen sind auch
    nichts, was andere Konten auf dem Rechner lesen müssen.
    """
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = path_for(directory)
    target.touch(mode=0o600, exist_ok=True)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    target.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def apply_to_env(settings: Settings, environ: dict[str, str] | None = None) -> list[str]:
    """Überträgt die Einstellungen in die Umgebung, aus der alles andere liest.

    So bleibt der bestehende Aufbau unangetastet: `providers.from_env()`,
    `MailConfig.from_env()` und `file_roots_from_env()` wissen nichts von dieser
    Datei und müssen es auch nicht.

    Werte, die dieser Settings-Instanz bei einem früheren Aufruf entstammen,
    werden zuerst entfernt. Echte externe Umgebungsvariablen bleiben bestehen
    und schlagen weiterhin die Datei.
    """
    environ = os.environ if environ is None else environ
    manages_process_environment = environ is os.environ
    if manages_process_environment:
        for name in _APPLIED_ENV.pop(id(settings), set()):
            environ.pop(name, None)

    gesetzt: list[str] = []

    def put(name: str, value: str | None) -> None:
        if not value or environ.get(name):
            return
        environ[name] = str(value)
        gesetzt.append(name)

    put("ICARUS_PROVIDER", settings.provider)
    put("ICARUS_MODEL", settings.model)
    put("ICARUS_BASE_URL", settings.endpoint)
    put("ICARUS_FILE_ROOTS", ":".join(settings.file_roots))

    put("ICARUS_IMAP_HOST", settings.mail.imap_host)
    put("ICARUS_IMAP_PORT", str(settings.mail.imap_port) if settings.mail.imap_host else "")
    put("ICARUS_SMTP_HOST", settings.mail.smtp_host)
    put("ICARUS_SMTP_PORT", str(settings.mail.smtp_port) if settings.mail.smtp_host else "")
    put("ICARUS_MAIL_USER", settings.mail.user)
    put("ICARUS_MAIL_FROM", settings.mail.sender or settings.mail.user)

    put("ICARUS_CALDAV_URL", settings.calendar.url)
    put("ICARUS_CALDAV_USER", settings.calendar.user or settings.mail.user)

    if manages_process_environment:
        _APPLIED_ENV[id(settings)] = set(gesetzt)
    return gesetzt


def secret_name_for_provider(provider: str) -> str | None:
    """Unter welchem Namen der Schlüssel dieses Anbieters liegt.

    Ollama braucht keinen; deshalb ist `None` eine gültige Antwort und kein
    Fehler.
    """
    return PROVIDER_SECRET.get(provider)


def store_secret(keychain: Keychain, name: str, value: str) -> None:
    """Legt ein Geheimnis ab — im Schlüsselbund, sonst nur in der Umgebung.

    Ohne Schlüsselspeicher (Linux ohne `secret-tool`, manche Serverumgebungen)
    wäre die Alternative, den Schlüssel in die Einstellungsdatei zu schreiben.
    Das wird hier ausdrücklich **nicht** getan: Er gilt dann nur für die
    laufende Sitzung, und die Oberfläche sagt das. Lieber unbequem als
    Klartext auf der Platte.
    """
    os.environ[name] = value
    if keychain.available:
        keychain.set(name, value)


def clear_secret(keychain: Keychain, name: str) -> None:
    os.environ.pop(name, None)
    if keychain.available:
        try:
            keychain.delete(name)
        except KeychainError:
            pass


def secret_status(keychain: Keychain) -> dict[str, bool]:
    """Welche Geheimnisse hinterlegt sind — nur ob, nie welche.

    Die Oberfläche soll „hinterlegt" anzeigen können, ohne dass ein Schlüssel je
    wieder über die Schnittstelle zurückkommt. Ein Feld, das den Wert
    zurückliefert, wäre der bequemste Weg, ihn irgendwann zu protokollieren.
    """
    status: dict[str, bool] = {}
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                 "ICARUS_MAIL_PASSWORD", "ICARUS_CALDAV_PASSWORD"):
        status[name] = bool(
            os.environ.get(name) or (keychain.available and keychain.get(name))
        )
    return status


__all__ = [
    "DATEINAME",
    "DEFAULT_MODELS",
    "PROVIDERS",
    "PROVIDER_SECRET",
    "CalendarSettings",
    "MailSettings",
    "ScheduleSettings",
    "Settings",
    "apply_to_env",
    "clear_secret",
    "load",
    "path_for",
    "reload_registered",
    "save",
    "secret_name_for_provider",
    "secret_status",
    "store_secret",
]
