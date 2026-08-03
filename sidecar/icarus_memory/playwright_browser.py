"""Prozessisolierter Playwright-Adapter für den Browserconnector.

Playwright läuft in einem eigenen Node-Prozess. Der Python-Sidecar tauscht nur
strukturierte JSON-Nachrichten mit ihm aus; Webseiteninhalt bleibt im
Browserprozess und wird anschließend vom Connector als fremder Inhalt gerahmt.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4


class BrowserProcessError(RuntimeError):
    pass


class PlaywrightBrowserSession:
    """Implementiert ``BrowserSession`` über einen persistenten JSONL-Prozess."""

    def __init__(
        self,
        worker: str | Path,
        *,
        node: str = "node",
        cwd: str | Path | None = None,
        startup_timeout_seconds: float = 30.0,
    ) -> None:
        self.worker = Path(worker).resolve()
        if not self.worker.is_file():
            raise FileNotFoundError(self.worker)
        self._lock = threading.Lock()
        self._closed = False
        self._process = subprocess.Popen(
            [node, str(self.worker)],
            cwd=str(Path(cwd).resolve()) if cwd else str(self.worker.parent),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if self._process.stdin is None or self._process.stdout is None:
            self.close(force=True)
            raise BrowserProcessError("Browserprozess konnte keine Pipes öffnen")
        # Der Worker startet Chromium vor der ersten Eingabe. Ein eigener
        # Ping-Befehl wäre eine zusätzliche Protokollfläche; die erste echte
        # Operation übernimmt daher den Startnachweis. Das Zeitlimit wird vom
        # aufrufenden E2E-Prozess kontrolliert.
        self.startup_timeout_seconds = startup_timeout_seconds

    def __enter__(self) -> "PlaywrightBrowserSession":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def navigate(self, url: str) -> str:
        return str(self._request("navigate", {"url": url}))

    def read(self, selector: str = "body", max_chars: int = 8000) -> str:
        return str(
            self._request(
                "read",
                {"selector": selector, "max_chars": int(max_chars)},
            )
        )

    def submit(self, selector: str, fields: dict[str, str]) -> str:
        return str(
            self._request(
                "submit",
                {"selector": selector, "fields": dict(fields)},
            )
        )

    def download(self, selector: str, target: Path) -> str:
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(
            self._request(
                "download",
                {"selector": selector, "target": str(target)},
            )
        )

    def upload(self, selector: str, source: Path) -> str:
        return str(
            self._request(
                "upload",
                {"selector": selector, "source": str(source)},
            )
        )

    def close(self, *, force: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        if not force and self._process.poll() is None:
            try:
                self._request_unlocked("close", {})
            except Exception:
                force = True
        if force and self._process.poll() is None:
            self._process.kill()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)

    def _request(self, operation: str, arguments: dict[str, Any]) -> Any:
        with self._lock:
            return self._request_unlocked(operation, arguments)

    def _request_unlocked(self, operation: str, arguments: dict[str, Any]) -> Any:
        if self._process.poll() is not None:
            raise BrowserProcessError(self._failure_detail("Browserprozess ist beendet"))
        if self._process.stdin is None or self._process.stdout is None:
            raise BrowserProcessError("Browserprozess besitzt keine offenen Pipes")
        request_id = uuid4().hex
        payload = {
            "id": request_id,
            "operation": operation,
            "arguments": arguments,
        }
        try:
            self._process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
            line = self._process.stdout.readline()
        except (BrokenPipeError, OSError) as exc:
            raise BrowserProcessError(self._failure_detail(str(exc))) from exc
        if not line:
            raise BrowserProcessError(self._failure_detail("Keine Antwort vom Browserprozess"))
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BrowserProcessError(f"Ungültige Browserantwort: {line[:500]}") from exc
        if response.get("id") != request_id:
            raise BrowserProcessError("Browserantwort gehört nicht zur Anfrage")
        if not response.get("ok"):
            raise BrowserProcessError(str(response.get("error") or "Browseroperation fehlgeschlagen"))
        return response.get("result")

    def _failure_detail(self, message: str) -> str:
        stderr = ""
        if self._process.stderr is not None and self._process.poll() is not None:
            stderr = self._process.stderr.read()[-4000:]
        return f"{message}{': ' + stderr if stderr else ''}"


__all__ = ["BrowserProcessError", "PlaywrightBrowserSession"]
