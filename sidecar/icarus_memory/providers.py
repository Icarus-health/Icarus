"""Säule 2 auf der Modellseite: austauschbare Anbieter.

Zwei Formen decken praktisch das Feld ab:

* **OpenAI-kompatibel** — OpenAI selbst, Ollama, LM Studio, vLLM, llama.cpp,
  Groq, Together und die meisten lokalen Server. Ein lokales Modell ist damit
  eine Frage der Basis-URL, keine Sonderbehandlung.
* **Anthropic** — eigenes Format für Nachrichten und Werkzeuge.

Nach außen sehen beide gleich aus. Der Rest des Systems kennt nur `Provider`,
`Reply` und `ToolCall` und weiß nicht, wer antwortet — das ist der Punkt.
"""

from __future__ import annotations

import json
import ipaddress
import os
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""


class ProviderError(Exception):
    pass


class Provider(Protocol):
    name: str
    model: str
    is_local: bool
    """Läuft der Anbieter auf diesem Rechner? Steuert die Egress-Grenze."""

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Reply: ...


def _http(timeout: float = 120.0) -> httpx.Client:
    return httpx.Client(timeout=timeout)


def is_local_endpoint(base_url: str) -> bool:
    """Läuft dieser Anbieter auf diesem Rechner?

    Entscheidet über den Schutzbedarf, der ihm zugemutet werden darf. Nur
    Loopback zählt als lokal: ein Ollama im Heimnetz ist bereits ein Netzwerk,
    und ein Hostname, der heute auf 127.0.0.1 zeigt, kann morgen umziehen.
    Deshalb wird die Adresse literal geprüft und nicht aufgelöst.
    """
    try:
        host = urlparse(base_url).hostname
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class OpenAICompatible:
    """Deckt OpenAI und jeden Server mit /chat/completions ab.

    Für Ollama genügt base_url=http://localhost:11434/v1 und ein beliebiger
    Schlüssel — damit läuft Icarus vollständig lokal.
    """

    name = "openai"

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.model = model
        self._key = api_key or "not-needed"
        self._base = base_url.rstrip("/")
        self.is_local = is_local_endpoint(base_url)

    @property
    def base_url(self) -> str:
        """Wohin dieser Anbieter zeigt.

        Öffentlich, weil die Einrichtung es anzeigen können muss — wer drei
        Dienste ausprobiert hat, will sehen, welcher gerade eingestellt ist.
        Der Schlüssel bleibt privat; er hat in keiner Anzeige etwas verloren.
        """
        return self._base

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Reply:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = [
                {"type": "function", "function": t} for t in tools
            ]
        try:
            with _http() as client:
                response = client.post(
                    f"{self._base}/chat/completions",
                    headers={"Authorization": f"Bearer {self._key}"},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Anfrage an {self._base} fehlgeschlagen: {exc}") from exc

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        calls = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=raw.get("id", ""), name=fn.get("name", ""), arguments=args))

        return Reply(text=message.get("content") or "", tool_calls=calls, model=self.model)


class Anthropic:
    """Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://api.anthropic.com/v1",
        max_tokens: int = 4096,
    ) -> None:
        self.model = model
        self._key = api_key
        self._base = base_url.rstrip("/")

    @property
    def base_url(self) -> str:
        return self._base

        self._max_tokens = max_tokens
        self.is_local = is_local_endpoint(base_url)

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Reply:
        system = " ".join(m["content"] for m in messages if m.get("role") == "system")
        converted = [
            self._convert(m) for m in messages if m.get("role") != "system"
        ]

        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "messages": converted,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t.get("parameters", {"type": "object"}),
                }
                for t in tools
            ]

        try:
            with _http() as client:
                response = client.post(
                    f"{self._base}/messages",
                    headers={
                        "x-api-key": self._key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Anfrage an {self._base} fehlgeschlagen: {exc}") from exc

        text_parts, calls = [], []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=block.get("input") or {},
                    )
                )
        return Reply(text="".join(text_parts), tool_calls=calls, model=self.model)

    @staticmethod
    def _convert(message: dict[str, Any]) -> dict[str, Any]:
        """Übersetzt die neutrale Form in Anthropics Blockformat."""
        role = message.get("role")

        if role == "tool":
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": message.get("tool_call_id", ""),
                        "content": message.get("content", ""),
                    }
                ],
            }

        if role == "assistant" and message.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            for raw in message["tool_calls"]:
                fn = raw.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": raw.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": args,
                    }
                )
            return {"role": "assistant", "content": blocks}

        return {"role": role, "content": message.get("content") or ""}


def verfuegbare_modelle(base_url: str, api_key: str | None = None) -> list[str]:
    """Fragt einen OpenAI-kompatiblen Endpunkt, welche Modelle er kennt.

    Damit wird aus einem Tippfeld eine Liste — der Nutzer muss den genauen
    Namen eines Modells nicht auswendig können, und ein Tippfehler kann nicht
    mehr in einen Fehler beim ersten Gespräch münden.

    Wirft nichts: kann der Endpunkt nicht antworten oder kennt er den Weg
    nicht, kommt eine leere Liste zurück und das Feld bleibt ein Tippfeld.
    """
    ziel = base_url.rstrip("/") + "/models"
    kopf = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with _http(timeout=8.0) as client:
            antwort = client.get(ziel, headers=kopf)
            antwort.raise_for_status()
            daten = antwort.json()
    except Exception:  # noqa: BLE001 - eine fehlende Liste ist kein Fehler
        return []

    posten = daten.get("data") if isinstance(daten, dict) else None
    if not isinstance(posten, list):
        return []

    namen = []
    for eintrag in posten:
        if isinstance(eintrag, dict):
            name = eintrag.get("id") or eintrag.get("name")
            if isinstance(name, str) and name:
                namen.append(name)
    return sorted(set(namen))


def from_env() -> Provider | None:
    """Baut den Anbieter aus der Umgebung.

    Reihenfolge: ausdrückliche Wahl über ICARUS_PROVIDER, sonst der erste
    Anbieter, für den ein Schlüssel vorliegt, sonst Ollama, falls es lokal
    erreichbar ist. Gibt None zurück, wenn nichts konfiguriert ist — der
    Gedächtniskern funktioniert auch ohne Modell.
    """
    choice = os.environ.get("ICARUS_PROVIDER", "").strip().lower()
    model = os.environ.get("ICARUS_MODEL", "").strip()

    if choice == "anthropic" or (not choice and os.environ.get("ANTHROPIC_API_KEY")):
        key = os.environ.get("ANTHROPIC_API_KEY")
        if key:
            return Anthropic(model or "claude-sonnet-5", key)

    # Ein Anbieter, der die OpenAI-Schnittstelle spricht, aber woanders steht.
    # Das ist der Schlüssel zu OpenRouter, Groq, Together, DeepSeek, Mistral,
    # LM Studio, llama.cpp und vLLM — eine Zeile statt eines Anbieters je Dienst.
    if choice == "kompatibel":
        adresse = os.environ.get("ICARUS_BASE_URL", "").strip()
        if not adresse:
            return None
        return OpenAICompatible(
            model or "",
            # Manche lokalen Server verlangen gar keinen Schlüssel und stören
            # sich auch nicht an einem beliebigen. Leer ginge oft schief.
            api_key=os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY") or "kein-schluessel",
            base_url=adresse,
        )

    if choice == "ollama":
        return OpenAICompatible(
            model or "llama3.1",
            api_key="ollama",
            base_url=os.environ.get("ICARUS_BASE_URL", "http://localhost:11434/v1"),
        )

    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    if choice == "openai" or key:
        if key:
            return OpenAICompatible(
                model or "gpt-4.1-mini",
                api_key=key,
                base_url=os.environ.get("ICARUS_BASE_URL", "https://api.openai.com/v1"),
            )

    return None


__all__ = [
    "Anthropic",
    "OpenAICompatible",
    "Provider",
    "ProviderError",
    "Reply",
    "ToolCall",
    "from_env",
    "verfuegbare_modelle",
]
