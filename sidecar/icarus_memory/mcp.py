"""Die MCP-Tür: Icarus für andere Assistenten.

Ein Assistent auf dem Rechner — Claude Desktop, Claude Code, was auch immer —
kann hierüber in dasselbe Gedächtnis schreiben und lesen, das die App benutzt.
Damit hört das Vergessen zwischen Sitzungen auf, ohne dass ein zweites System
entsteht.

## Warum ein Client und kein eigener Stapel

Dieser Server öffnet **keine** eigene Datenbank. Er spricht über HTTP mit dem
laufenden Sidecar. Das ist der ganze Punkt:

- **Freigaben landen dort, wo ein Mensch sitzt.** Ein fremder Assistent kann
  keine Bestätigungsphrase abtippen. Beantragt er etwas Außenwirksames,
  erscheint der Antrag in der Icarus-App und wartet dort. Bauten wir hier einen
  zweiten Stapel, gäbe es eine zweite Freigabeliste, die niemand ansieht.
- **Ein Audit-Log.** Was über diese Tür kam, steht im selben Protokoll wie
  alles andere — erkennbar, nicht versteckt.
- **Ein Bestand.** Zwei Prozesse auf derselben SQLite-Datei wären technisch
  machbar und fachlich falsch: Die Regeln des Selbstmodells leben im Store,
  nicht in der Datei.

Der Sidecar bindet nur an Loopback und verlangt ein Token. Beides findet dieser
Server in `verbindung.json` im Datenverzeichnis, das der Sidecar beim Start
schreibt.

## Protokoll

JSON-RPC 2.0 über stdin/stdout, zeilenweise — das ist MCP über stdio. Bewusst
von Hand statt über ein SDK: Es sind vier Methoden, und eine Abhängigkeit, die
sich im Halbjahrestakt ändert, ist für eine App, die zehn Jahre laufen soll, der
schlechtere Tausch. Dieselbe Überlegung wie bei IMAP und CalDAV statt
Anbieter-APIs.

## Einrichtung

In der Konfiguration des jeweiligen Assistenten:

```json
{
  "mcpServers": {
    "icarus": {
      "command": "/pfad/zu/icarus-mcp"
    }
  }
}
```
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

#: Versionen, die dieser Server versteht. Kennt der Client eine davon, wird
#: seine genommen; sonst antworten wir mit unserer neuesten und lassen ihn
#: entscheiden, ob er damit leben kann.
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

CONNECTION_FILE = "verbindung.json"

#: Werkzeuge des Sidecars bekommen dieses Präfix, damit sie in einem Client
#: neben Dutzenden anderer Werkzeuge erkennbar bleiben und nicht mit
#: gleichnamigen kollidieren.
PREFIX = "icarus_"


class SidecarUnreachable(Exception):
    """Der Sidecar läuft nicht oder das Token stimmt nicht."""


def _data_dir() -> Path:
    configured = os.environ.get("ICARUS_DATA_DIR")
    if configured:
        return Path(configured)
    return Path.home() / "Library" / "Application Support" / "Icarus"


def connection() -> tuple[str, str | None]:
    """Findet Adresse und Token des laufenden Sidecars.

    Umgebungsvariablen schlagen die Datei — damit lässt sich der Server gegen
    einen von Hand gestarteten Sidecar testen, ohne die App zu öffnen.
    """
    url = os.environ.get("ICARUS_SIDECAR_URL")
    token = os.environ.get("ICARUS_SIDECAR_TOKEN")
    if url:
        return url.rstrip("/"), token

    path = _data_dir() / CONNECTION_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SidecarUnreachable(
            f"Keine Verbindungsdaten unter {path}. Läuft die Icarus-App?"
        ) from exc
    return str(data["url"]).rstrip("/"), data.get("token")


class Bridge:
    """Spricht mit dem Sidecar."""

    def __init__(
        self,
        url: str,
        token: str | None,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        headers = {"x-icarus-token": token} if token else {}
        # `client` gibt es, damit der Test gegen den echten Sidecar-Stapel
        # laufen kann statt gegen eine nachgebaute Antwort. Eine Brücke, die
        # nur mit Attrappen geprüft ist, beweist über die Brücke nichts.
        self._client = client or httpx.Client(
            base_url=url, headers=headers, timeout=timeout
        )

    def get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params or None)

    def post(self, path: str, payload: Any = None) -> Any:
        return self._request("POST", path, json=payload)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise SidecarUnreachable(f"Sidecar nicht erreichbar: {exc}") from exc
        if response.status_code == 401:
            raise SidecarUnreachable(
                "Token abgelehnt. Die App hat vermutlich neu gestartet — "
                "dann auch diesen Server neu starten."
            )
        response.raise_for_status()
        return response.json() if response.content else None

    def close(self) -> None:
        self._client.close()


# -- Zusatzwerkzeuge --------------------------------------------------------
#
# Diese hängen nicht an der Werkzeug-Registry des Agenten, sondern an
# Endpunkten, die es nur über HTTP gibt. Sie sind alle lesend.

EXTRA_TOOLS: list[dict[str, Any]] = [
    {
        "name": f"{PREFIX}heute",
        "description": (
            "Der Tagesüberblick: offene Aufgaben, Termine, ungelesene "
            "Nachrichten, laufende Projekte. Ein Aufruf statt vier."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tage": {"type": "integer", "description": "Vorausschau, Standard 7"},
            },
        },
    },
    {
        "name": f"{PREFIX}kontext",
        "description": (
            "Was Icarus über den Nutzer weiß — wörtlich so, wie es einem Modell "
            "übermittelt wird, samt Quellen und Aktualitätsurteil. Am Anfang "
            "einer Sitzung aufrufen, statt den Nutzer erklären zu lassen."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": f"{PREFIX}freigaben",
        "description": (
            "Zeigt Anträge, die in der Icarus-App auf eine Entscheidung des "
            "Nutzers warten."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _heute(bridge: Bridge, arguments: dict[str, Any]) -> str:
    data = bridge.get("/dashboard", days=int(arguments.get("tage", 7)))
    lines: list[str] = []

    projects = data.get("projects", {})
    if projects.get("items"):
        lines.append("Projekte:")
        for p in projects["items"][:8]:
            frist = f", Frist {p['deadline'][:10]}" if p.get("deadline") else ""
            lines.append(f"- {p['name']} ({p['status']}{frist})")

    tasks = data.get("tasks", {})
    if tasks.get("items"):
        overdue = tasks.get("overdue", 0)
        head = f"\nAufgaben ({len(tasks['items'])} offen"
        head += f", {overdue} überfällig)" if overdue else ")"
        lines.append(head + ":")
        for t in tasks["items"][:10]:
            mark = "ÜBERFÄLLIG " if t.get("overdue") else ""
            when = f" (fällig {t['due'][:10]})" if t.get("due") else ""
            lines.append(f"- {mark}{t['title']}{when}")
    elif not tasks.get("error"):
        lines.append("\nAufgaben: nichts offen.")

    for key, label in (("calendar", "Termine"), ("mail", "Nachrichten")):
        block = data.get(key, {})
        if block.get("error"):
            lines.append(f"\n{label}: {block['error']}")
            continue
        items = block.get("items", [])
        if not items:
            lines.append(f"\n{label}: nichts.")
            continue
        lines.append(f"\n{label}:")
        for item in items[:8]:
            if key == "calendar":
                lines.append(f"- {item.get('start', '')[:16]} {item.get('summary', '')}")
            else:
                mark = "• " if item.get("unread") else "  "
                lines.append(f"- {mark}{item.get('from', '')}: {item.get('subject', '')}")

    memory = data.get("memory", {})
    lines.append(f"\nGedächtnis: {memory.get('count', 0)} gültige Aussagen.")

    # Unbearbeitetes Rohmaterial gehört in den Überblick, sonst wächst der
    # Berg unsichtbar.
    pending = data.get("episodes", {}).get("pending", 0)
    if pending:
        lines.append(
            f"Rohmaterial: {pending} Episoden warten auf Verdichtung."
        )
    return "\n".join(lines)


def _freigaben(bridge: Bridge, _: dict[str, Any]) -> str:
    pending = bridge.get("/approvals")
    if not pending:
        return "Keine offenen Freigaben."
    return "\n\n".join(
        f"[{a['id']}] {a['tool']} ({a['level']})\n{a['dry_run']}" for a in pending
    )


# -- JSON-RPC ---------------------------------------------------------------


class Server:
    def __init__(self, bridge: Bridge) -> None:
        self._bridge = bridge
        self._tools: list[dict[str, Any]] | None = None

    def tools(self) -> list[dict[str, Any]]:
        """Die Werkzeugliste, einmal geholt und dann gehalten.

        Sie ändert sich nur, wenn Konnektoren dazukommen — und dann startet
        der Sidecar ohnehin neu.
        """
        if self._tools is None:
            registry = self._bridge.get("/tools")
            self._tools = [
                {
                    "name": f"{PREFIX}{t['name']}",
                    "description": t["description"],
                    "inputSchema": t["parameters"],
                }
                for t in registry
            ] + EXTRA_TOOLS
        return self._tools

    def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Führt ein Werkzeug aus. Gibt Text und ein Fehlerflag zurück."""
        if name == f"{PREFIX}heute":
            return _heute(self._bridge, arguments), False
        if name == f"{PREFIX}kontext":
            return self._bridge.get("/context")["context"], False
        if name == f"{PREFIX}freigaben":
            return _freigaben(self._bridge, arguments), False

        if not name.startswith(PREFIX):
            return f"Unbekanntes Werkzeug: {name}", True

        result = self._bridge.post(f"/tools/{name[len(PREFIX):]}", arguments)
        # Ein wartender Antrag ist kein Fehler — der Aufrufer soll die
        # Erklärung sehen und den Nutzer zur App schicken, statt es erneut zu
        # versuchen. `isError` würde viele Clients zum Wiederholen bringen.
        return result["text"], False

    # -- Methoden ----------------------------------------------------------

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")

        # Benachrichtigungen haben keine id und bekommen keine Antwort.
        if request_id is None:
            return None

        try:
            result = self._dispatch(method, request.get("params") or {})
        except SidecarUnreachable as exc:
            return _error(request_id, -32001, str(exc))
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:400]
            return _error(request_id, -32002, f"Sidecar meldet {exc.response.status_code}: {detail}")
        except _MethodNotFound:
            return _error(request_id, -32601, f"Unbekannte Methode: {method}")
        except Exception as exc:  # noqa: BLE001 - der Server darf nicht sterben
            return _error(request_id, -32603, f"{type(exc).__name__}: {exc}")

        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _dispatch(self, method: str | None, params: dict[str, Any]) -> Any:
        if method == "initialize":
            wanted = params.get("protocolVersion")
            return {
                "protocolVersion": (
                    wanted if wanted in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
                ),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "icarus", "version": "0.1.0"},
                "instructions": (
                    "Icarus ist das persönliche Gedächtnis des Nutzers: Aussagen "
                    "über ihn mit Herkunft und Alter, dazu Projekte, Aufgaben und "
                    "Notizen.\n\n"
                    "Rufe `icarus_kontext` zu Beginn einer Sitzung auf, statt den "
                    "Nutzer erklären zu lassen, wer er ist und woran er arbeitet. "
                    "Nutze `icarus_merken` nur für Dauerhaftes über die Person, "
                    "`icarus_notiz_anlegen` für Inhalte zu einem Projekt.\n\n"
                    "Außenwirksames wird nicht ausgeführt, sondern dem Nutzer in "
                    "der Icarus-App zur Freigabe vorgelegt. Das ist kein Fehler; "
                    "sage es ihm und versuche es nicht erneut."
                ),
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.tools()}
        if method == "tools/call":
            text, is_error = self.call(
                params.get("name", ""), params.get("arguments") or {}
            )
            return {"content": [{"type": "text", "text": text}], "isError": is_error}
        # Clients fragen das oft ungefragt ab; eine leere Liste ist die
        # ehrliche Antwort und verhindert eine Fehlermeldung beim Verbinden.
        if method == "prompts/list":
            return {"prompts": []}
        if method == "resources/list":
            return {"resources": []}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        raise _MethodNotFound


class _MethodNotFound(Exception):
    pass


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(server: Server, stdin: Any, stdout: Any) -> None:
    """Die Schleife. Eine Zeile herein, höchstens eine Zeile hinaus."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            # Ohne id lässt sich nichts zuordnen; verwerfen ist richtiger als
            # eine Antwort zu erfinden.
            continue
        response = server.handle(request)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


def main() -> None:  # pragma: no cover
    try:
        url, token = connection()
    except SidecarUnreachable as exc:
        # stderr, nicht stdout: auf stdout gehört ausschließlich JSON-RPC.
        print(f"icarus-mcp: {exc}", file=sys.stderr)
        raise SystemExit(1)

    bridge = Bridge(url, token)
    try:
        serve(Server(bridge), sys.stdin, sys.stdout)
    finally:
        bridge.close()


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["Bridge", "Server", "SidecarUnreachable", "connection", "serve"]
