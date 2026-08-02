"""API-Schlüssel im Schlüsselbund des Betriebssystems.

Schlüssel in einer `.env` sind Klartext auf der Platte, landen in Backups, in
Editor-Verläufen und irgendwann versehentlich in einem Repository. Auf einem
Rechner, der Jahre laufen soll, ist das der Ort, an dem am ehesten etwas
ausläuft.

Ohne Zusatzabhängigkeit: macOS über `security`, Windows über PowerShell mit
DPAPI, Linux über `secret-tool`, falls vorhanden.

## Die verschlüsselte Datei

In einem Container gibt es keinen dieser Speicher. Auch auf Linux ohne
`secret-tool` gibt es keinen. Bisher hieß das: Schlüssel liegen in
Umgebungsvariablen, also in einer `compose.yaml` oder `.env` — genau der
Klartext, gegen den dieses Modul gebaut ist.

Deshalb ein vierter Speicher: `schluessel.icarus` im Datenverzeichnis,
verschlüsselt mit einer Passphrase aus `ICARUS_SECRETS_PASSPHRASE` (Verfahren in
`crypto.py`).

**Was das bringt und was nicht**, ehrlich: Das Datenverzeichnis ist das, was
gesichert, kopiert und in Snapshots gezogen wird. Nach dieser Änderung enthält
keine dieser Kopien lesbare Schlüssel. Die Passphrase lebt in der
Orchestrierungsschicht — ein anderes Artefakt mit einem anderen Lebenszyklus.
Wer allerdings **beides** hat, Passphrase und Datenverzeichnis, hat die
Schlüssel. Das ist eine echte Verbesserung gegen ausgelaufene Sicherungen, kein
Schutz gegen jemanden, der ohnehin auf dem Rechner sitzt.

Ohne jeden Speicher fällt weiterhin alles auf Umgebungsvariablen zurück — dann
funktioniert das System, nur eben ohne diesen Schutz, und sagt das auch.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

from .crypto import DecryptionError, seal_json, unseal_json

SERVICE = "health.icarus.desktop"

#: Name der verschlüsselten Schlüsseldatei im Datenverzeichnis.
SECRETS_FILE = "schluessel.icarus"
SECRETS_MAGIC = "icarus-secrets-v1"
PASSPHRASE_ENV = "ICARUS_SECRETS_PASSPHRASE"
BACKEND_ENV = "ICARUS_KEYCHAIN_BACKEND"
"""Explizite Auswahl für isolierte Builds und Container.

Erlaubt sind nur ``none`` und ``file``. Betriebssystem-Backends lassen sich
nicht erzwingen: Ein Build darf nicht behaupten, ein Schlüsselbund sei da, wenn
das zugehörige Systemwerkzeug fehlt. Ohne diese Variable bleibt die automatische
Auswahl unverändert.
"""

#: Schlüssel, die aus dem Schlüsselbund kommen dürfen.
KNOWN = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "LLM_API_KEY",
    "ICARUS_BACKUP_PASSPHRASE",
    # Konnektor-Zugangsdaten gehören genauso wenig in eine Klartextdatei.
    "ICARUS_MAIL_PASSWORD",
    "ICARUS_CALDAV_PASSWORD",
)


class KeychainError(Exception):
    pass


def _run(command: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def _data_dir() -> Path:
    configured = os.environ.get("ICARUS_DATA_DIR")
    if configured:
        return Path(configured)
    return Path.home() / "Library" / "Application Support" / "Icarus"


class Keychain:
    """Zugriff auf den Schlüsselspeicher der Plattform."""

    def __init__(self, service: str = SERVICE, data_dir: Path | None = None) -> None:
        self._service = service
        self._data_dir = Path(data_dir) if data_dir else _data_dir()
        self._backend = self._detect()

    def _detect(self) -> str:
        """Sucht den besten verfügbaren Speicher.

        Der Schlüsselbund des Betriebssystems geht vor: Er ist an das
        Benutzerkonto gebunden und braucht keine Passphrase, die irgendwo
        stehen müsste. Die verschlüsselte Datei kommt erst danach — sie ist die
        Antwort auf „es gibt keinen", nicht die bessere Lösung.

        CI und reproduzierbare Builds dürfen den System-Schlüsselbund explizit
        ausschalten. Sonst teilen Tests auf macOS unbeabsichtigt den echten
        Schlüsselbund des Runner-Kontos und beeinflussen einander.
        """
        forced = os.environ.get(BACKEND_ENV, "").strip().casefold()
        if forced == "none":
            return "none"
        if forced == "file":
            return "file" if os.environ.get(PASSPHRASE_ENV) else "none"

        system = platform.system()
        if system == "Darwin" and shutil.which("security"):
            return "macos"
        if system == "Windows" and shutil.which("powershell"):
            return "windows"
        if system == "Linux" and shutil.which("secret-tool"):
            return "secret-tool"
        if os.environ.get(PASSPHRASE_ENV):
            return "file"
        return "none"

    # -- Verschlüsselte Datei ----------------------------------------------

    @property
    def secrets_path(self) -> Path:
        return self._data_dir / SECRETS_FILE

    def _read_file(self) -> dict[str, str]:
        """Liest die Schlüsseldatei. Eine kaputte Datei blockiert nichts.

        Ein leerer Speicher ist reparierbar — der Nutzer trägt die Schlüssel
        erneut ein. Eine Ausnahme beim Start hieße, dass er nicht mehr an sein
        Gedächtnis kommt, und das ist der schlimmere Ausgang.
        """
        passphrase = os.environ.get(PASSPHRASE_ENV)
        if not passphrase or not self.secrets_path.is_file():
            return {}
        try:
            document = json.loads(self.secrets_path.read_text(encoding="utf-8"))
            if document.get("format") != SECRETS_MAGIC:
                return {}
            werte = unseal_json(self.secrets_path.read_text(encoding="utf-8"), passphrase)
        except (OSError, ValueError, DecryptionError):
            return {}
        return {str(k): str(v) for k, v in werte.items()} if isinstance(werte, dict) else {}

    def _write_file(self, werte: dict[str, str]) -> None:
        passphrase = os.environ.get(PASSPHRASE_ENV)
        if not passphrase:
            raise KeychainError(
                f"Ohne {PASSPHRASE_ENV} lässt sich die Schlüsseldatei nicht schreiben."
            )
        self._data_dir.mkdir(parents=True, exist_ok=True)
        target = self.secrets_path
        # Erst die Rechte, dann der Inhalt — sonst liegt das Chiffrat kurz
        # unter den Standardrechten. Es ist verschlüsselt, aber ein
        # Angreifer, der es kopiert, kann die Passphrase später raten.
        target.touch(mode=0o600, exist_ok=True)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        target.write_text(seal_json(werte, passphrase, SECRETS_MAGIC), encoding="utf-8")

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def available(self) -> bool:
        return self._backend != "none"

    # -- Lesen und Schreiben ----------------------------------------------

    def get(self, name: str) -> str | None:
        if self._backend == "file":
            return self._read_file().get(name)

        if self._backend == "macos":
            result = _run([
                "security", "find-generic-password",
                "-s", self._service, "-a", name, "-w",
            ])
            return result.stdout.strip() or None if result.returncode == 0 else None

        if self._backend == "secret-tool":
            result = _run(["secret-tool", "lookup", "service", self._service, "account", name])
            return result.stdout.strip() or None if result.returncode == 0 else None

        if self._backend == "windows":
            result = _run(["powershell", "-NoProfile", "-Command", _WIN_GET.format(
                path=self._windows_path(name))])
            return result.stdout.strip() or None if result.returncode == 0 else None

        return None

    def set(self, name: str, value: str) -> None:
        if self._backend == "none":
            raise KeychainError("Kein Schlüsselspeicher verfügbar.")

        if self._backend == "file":
            werte = self._read_file()
            werte[name] = value
            self._write_file(werte)
            return

        if self._backend == "macos":
            # -U aktualisiert einen vorhandenen Eintrag, statt zu scheitern.
            result = _run([
                "security", "add-generic-password",
                "-s", self._service, "-a", name, "-w", value, "-U",
            ])
        elif self._backend == "secret-tool":
            result = _run(
                ["secret-tool", "store", "--label", f"{self._service}: {name}",
                 "service", self._service, "account", name],
                stdin=value,
            )
        else:
            path = self._windows_path(name)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            result = _run(["powershell", "-NoProfile", "-Command",
                           _WIN_SET.format(path=path, value=value)])

        if result.returncode != 0:
            raise KeychainError(result.stderr.strip() or "Schreiben fehlgeschlagen.")

    def delete(self, name: str) -> None:
        if self._backend == "file":
            werte = self._read_file()
            if werte.pop(name, None) is not None:
                self._write_file(werte)
            return

        if self._backend == "macos":
            _run(["security", "delete-generic-password", "-s", self._service, "-a", name])
        elif self._backend == "secret-tool":
            _run(["secret-tool", "clear", "service", self._service, "account", name])
        elif self._backend == "windows":
            Path(self._windows_path(name)).unlink(missing_ok=True)

    def _windows_path(self, name: str) -> str:
        base = os.environ.get("APPDATA", str(Path.home()))
        return str(Path(base) / "Icarus" / "secrets" / f"{name}.dpapi")


# DPAPI bindet die Verschlüsselung an das Windows-Benutzerkonto.
_WIN_SET = (
    "Add-Type -AssemblyName System.Security; "
    "$b=[Text.Encoding]::UTF8.GetBytes('{value}'); "
    "$e=[Security.Cryptography.ProtectedData]::Protect($b,$null,'CurrentUser'); "
    "[IO.File]::WriteAllBytes('{path}',$e)"
)
_WIN_GET = (
    "Add-Type -AssemblyName System.Security; "
    "$e=[IO.File]::ReadAllBytes('{path}'); "
    "$b=[Security.Cryptography.ProtectedData]::Unprotect($e,$null,'CurrentUser'); "
    "[Text.Encoding]::UTF8.GetString($b)"
)


def load_into_env(keychain: Keychain | None = None) -> list[str]:
    """Holt bekannte Schlüssel in die Umgebung und meldet, welche kamen.

    Bereits gesetzte Umgebungsvariablen gewinnen — sonst ließe sich ein
    hinterlegter Schlüssel für einen Testlauf nicht übersteuern.
    """
    keychain = keychain or Keychain()
    if not keychain.available:
        return []

    loaded = []
    for name in KNOWN:
        if os.environ.get(name):
            continue
        value = keychain.get(name)
        if value:
            os.environ[name] = value
            loaded.append(name)
    return loaded


def migrate_env_file(path: Path, keychain: Keychain | None = None) -> list[str]:
    """Überträgt Schlüssel aus einer `.env` in den Schlüsselbund.

    Die Datei wird **nicht** automatisch gelöscht. Das ist Absicht: Ein
    Werkzeug, das ungefragt Dateien des Nutzers verändert, ist schlimmer als
    ein Schlüssel, der einen Tag zu lang liegen bleibt. Die Aufforderung zum
    Aufräumen kommt als Rückgabewert.
    """
    keychain = keychain or Keychain()
    if not keychain.available or not path.is_file():
        return []

    migrated = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip("\"'")
        if name in KNOWN and value:
            keychain.set(name, value)
            migrated.append(name)
    return migrated


__all__ = [
    "BACKEND_ENV",
    "KNOWN",
    "PASSPHRASE_ENV",
    "SECRETS_FILE",
    "SECRETS_MAGIC",
    "SERVICE",
    "Keychain",
    "KeychainError",
    "load_into_env",
    "migrate_env_file",
]
