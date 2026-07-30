"""API-Schlüssel im Schlüsselbund des Betriebssystems.

Schlüssel in einer `.env` sind Klartext auf der Platte, landen in Backups, in
Editor-Verläufen und irgendwann versehentlich in einem Repository. Auf einem
Rechner, der Jahre laufen soll, ist das der Ort, an dem am ehesten etwas
ausläuft.

Ohne Zusatzabhängigkeit: macOS über `security`, Windows über PowerShell mit
DPAPI, Linux über `secret-tool`, falls vorhanden. Gibt es keinen Speicher,
fällt alles auf Umgebungsvariablen zurück — dann funktioniert das System
weiterhin, nur eben ohne diesen Schutz, und sagt das auch.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

SERVICE = "health.icarus.desktop"

#: Schlüssel, die aus dem Schlüsselbund kommen dürfen.
KNOWN = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "LLM_API_KEY",
    "ICARUS_BACKUP_PASSPHRASE",
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


class Keychain:
    """Zugriff auf den Schlüsselspeicher der Plattform."""

    def __init__(self, service: str = SERVICE) -> None:
        self._service = service
        self._backend = self._detect()

    @staticmethod
    def _detect() -> str:
        system = platform.system()
        if system == "Darwin" and shutil.which("security"):
            return "macos"
        if system == "Windows" and shutil.which("powershell"):
            return "windows"
        if system == "Linux" and shutil.which("secret-tool"):
            return "secret-tool"
        return "none"

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def available(self) -> bool:
        return self._backend != "none"

    # -- Lesen und Schreiben ----------------------------------------------

    def get(self, name: str) -> str | None:
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


__all__ = ["KNOWN", "SERVICE", "Keychain", "KeychainError", "load_into_env", "migrate_env_file"]
