"""Säule 2 auf der Modellseite: austauschbare Anbieter und optionales Routing.

Zwei Transportformen decken praktisch das Feld ab:

* **OpenAI-kompatibel** — OpenAI selbst, Ollama, LM Studio, vLLM, llama.cpp,
  Groq, Together und die meisten lokalen Server.
* **Anthropic** — eigenes Format für Nachrichten und Werkzeuge.

Ohne `ICARUS_MODEL_ROUTES` bleibt das bisherige Verhalten vollständig erhalten.
Mit einer JSON-Registry wird ein konservativer RoutingProvider gebaut, der
Datenschutz, Fähigkeiten, Qualität, Kosten, Latenz und Fallbacks berücksichtigt.
"""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol
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

    Nur Loopback zählt als lokal. Ein Hostname, der zufällig auf Loopback zeigt,
    wird nicht aufgelöst, weil seine Bedeutung sich später ändern könnte.
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
    """Deckt OpenAI und jeden Server mit `/chat/completions` ab."""

    name = "openai"

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        name: str = "openai",
    ) -> None:
        # Der Transport ist bei OpenAI und Ollama gleich, der sichtbare
        # Anbieter aber nicht. Die Oberfläche muss „Ollama" sagen, wenn das
        # Gespräch lokal läuft — „openai" wäre hier eine falsche Zusage.
        self.name = name
        self.model = model
        self._key = api_key or "not-needed"
        self._base = base_url.rstrip("/")
        self.is_local = is_local_endpoint(base_url)

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Reply:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = [
                {"type": "function", "function": tool} for tool in tools
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
            raise ProviderError(
                f"Anfrage an {self._base} fehlgeschlagen: {exc}"
            ) from exc

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        calls = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function", {})
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(
                ToolCall(
                    id=raw.get("id", ""),
                    name=function.get("name", ""),
                    arguments=arguments,
                )
            )

        return Reply(
            text=message.get("content") or "",
            tool_calls=calls,
            model=self.model,
        )


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
        self._max_tokens = max_tokens
        self.is_local = is_local_endpoint(base_url)

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Reply:
        system = " ".join(
            message["content"]
            for message in messages
            if message.get("role") == "system"
        )
        converted = [
            self._convert(message)
            for message in messages
            if message.get("role") != "system"
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
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", {"type": "object"}),
                }
                for tool in tools
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
            raise ProviderError(
                f"Anfrage an {self._base} fehlgeschlagen: {exc}"
            ) from exc

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
        return Reply(
            text="".join(text_parts),
            tool_calls=calls,
            model=self.model,
        )

    @staticmethod
    def _convert(message: dict[str, Any]) -> dict[str, Any]:
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
                function = raw.get("function", {})
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": raw.get("id", ""),
                        "name": function.get("name", ""),
                        "input": arguments,
                    }
                )
            return {"role": "assistant", "content": blocks}

        return {"role": role, "content": message.get("content") or ""}


def _single_provider(
    provider_name: str,
    model: str,
    *,
    environment: Mapping[str, str],
    base_url: str | None = None,
    api_key_env: str | None = None,
    max_tokens: int = 4096,
) -> Provider | None:
    choice = provider_name.strip().lower()
    if choice == "anthropic":
        key_name = api_key_env or "ANTHROPIC_API_KEY"
        key = environment.get(key_name)
        if not key:
            return None
        return Anthropic(
            model,
            key,
            base_url=base_url or "https://api.anthropic.com/v1",
            max_tokens=max_tokens,
        )

    if choice == "ollama":
        return OpenAICompatible(
            model,
            api_key="ollama",
            base_url=base_url or "http://localhost:11434/v1",
            name="ollama",
        )

    if choice in {"openai", "openai_compatible"}:
        key_name = api_key_env or "OPENAI_API_KEY"
        key = environment.get(key_name) or environment.get("LLM_API_KEY")
        if not key and not is_local_endpoint(base_url or ""):
            return None
        return OpenAICompatible(
            model,
            api_key=key or "not-needed",
            base_url=base_url or "https://api.openai.com/v1",
        )

    raise ProviderError(f"Unbekannter Modellanbieter in der Registry: {choice}")


def _routing_from_env(environment: Mapping[str, str]) -> Provider:
    from .model_harness import (
        ModelRegistry,
        RoutingProvider,
        UsageBudget,
        specs_from_json,
    )

    raw = environment["ICARUS_MODEL_ROUTES"]
    data = json.loads(raw)
    specs = specs_from_json(raw)
    registry = ModelRegistry(specs)
    providers: dict[str, Provider] = {}
    by_id = {str(item["id"]): item for item in data}

    for spec in registry.enabled():
        item = by_id[spec.id]
        provider = _single_provider(
            spec.provider,
            spec.model,
            environment=environment,
            base_url=item.get("base_url"),
            api_key_env=item.get("api_key_env"),
            max_tokens=int(item.get("max_tokens", 4096)),
        )
        if provider is None:
            raise ProviderError(
                f"Für Modellprofil {spec.id!r} fehlt der benötigte Schlüssel."
            )
        providers[spec.id] = provider

    maximum_cost = environment.get("ICARUS_MODEL_MAX_COST")
    maximum_input = environment.get("ICARUS_MODEL_MAX_INPUT_TOKENS")
    maximum_output = environment.get("ICARUS_MODEL_MAX_OUTPUT_TOKENS")
    budget = UsageBudget(
        maximum_cost=float(maximum_cost) if maximum_cost else None,
        maximum_input_tokens=int(maximum_input) if maximum_input else None,
        maximum_output_tokens=int(maximum_output) if maximum_output else None,
    )
    return RoutingProvider(registry, providers, budget=budget)


def from_env() -> Provider | None:
    """Baut einen einzelnen Anbieter oder den optionalen Modellrouter.

    `ICARUS_MODEL_ROUTES` ist opt-in. Ohne diese Variable gilt weiterhin die
    bisherige Reihenfolge: ausdrückliche Wahl, vorhandener Schlüssel, lokales
    Ollama. Der Gedächtniskern funktioniert weiterhin ohne Modell.
    """
    if os.environ.get("ICARUS_MODEL_ROUTES"):
        return _routing_from_env(dict(os.environ))

    choice = os.environ.get("ICARUS_PROVIDER", "").strip().lower()
    model = os.environ.get("ICARUS_MODEL", "").strip()

    if choice == "anthropic" or (
        not choice and os.environ.get("ANTHROPIC_API_KEY")
    ):
        key = os.environ.get("ANTHROPIC_API_KEY")
        if key:
            return Anthropic(model or "claude-sonnet-5", key)

    if choice == "ollama":
        return OpenAICompatible(
            model or "llama3.1",
            api_key="ollama",
            base_url=os.environ.get(
                "ICARUS_BASE_URL", "http://localhost:11434/v1"
            ),
            name="ollama",
        )

    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    if choice == "openai" or key:
        if key:
            return OpenAICompatible(
                model or "gpt-4.1-mini",
                api_key=key,
                base_url=os.environ.get(
                    "ICARUS_BASE_URL", "https://api.openai.com/v1"
                ),
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
    "is_local_endpoint",
]
