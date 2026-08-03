"""Einheitliches Connector-SDK mit maschinenlesbaren Wirkungsmanifesten.

Manifeste beschreiben nicht nur Parameter, sondern Folgen: welche Daten gelesen
oder verändert werden, wer etwas sieht, ob Geld oder Recht betroffen sind,
welche Geheimnisse benötigt werden und ob die Aktion rückgängig ist.

Der Adapter erzeugt ausschließlich bestehende ``Tool``-Objekte. Damit bleibt
``Agent.invoke()`` der einzige Ausführungsweg und die zentrale Policy wird nicht
umgangen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from .policy import ActionClass
from .tools import Tool


class Visibility(str, Enum):
    PRIVATE = "private"
    NAMED_RECIPIENTS = "named_recipients"
    ORGANISATION = "organisation"
    PUBLIC = "public"


class Reversibility(str, Enum):
    REVERSIBLE = "reversible"
    PARTIALLY_REVERSIBLE = "partially_reversible"
    IRREVERSIBLE = "irreversible"


@dataclass(frozen=True)
class SecretRequirement:
    """Benannte Fähigkeit, niemals der Geheimniswert selbst."""

    name: str
    purpose: str
    optional: bool = False


@dataclass(frozen=True)
class EffectManifest:
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    deletes: tuple[str, ...] = ()
    recipient_fields: tuple[str, ...] = ()
    visibility: Visibility = Visibility.PRIVATE
    reversibility: Reversibility = Reversibility.REVERSIBLE
    financial: bool = False
    legal: bool = False
    publishes: bool = False
    uploads: bool = False
    downloads: bool = False
    secrets: tuple[SecretRequirement, ...] = ()
    returns_untrusted: bool = False

    def action_class(self, arguments: Mapping[str, Any]) -> ActionClass:
        recipients = self.recipients(arguments)
        outward = bool(
            recipients
            or self.visibility is not Visibility.PRIVATE
            or self.publishes
            or self.uploads
            or self.financial
            or self.legal
            or self.reversibility is Reversibility.IRREVERSIBLE
        )
        if outward:
            return ActionClass.OUTWARD
        if self.writes or self.deletes or self.downloads:
            return ActionClass.WRITE_LOCAL
        return ActionClass.READ

    def recipients(self, arguments: Mapping[str, Any]) -> tuple[str, ...]:
        found: list[str] = []
        for field_name in self.recipient_fields:
            value = arguments.get(field_name)
            if isinstance(value, str) and value.strip():
                found.append(value.strip())
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                found.extend(str(item).strip() for item in value if str(item).strip())
        return tuple(dict.fromkeys(found))

    def consequences(self, arguments: Mapping[str, Any]) -> list[str]:
        items: list[str] = []
        if self.reads:
            items.append("Liest: " + ", ".join(self.reads))
        if self.writes:
            items.append("Verändert: " + ", ".join(self.writes))
        if self.deletes:
            items.append("Löscht: " + ", ".join(self.deletes))
        recipients = self.recipients(arguments)
        if recipients:
            items.append("Empfänger: " + ", ".join(recipients))
        if self.visibility is Visibility.PUBLIC or self.publishes:
            items.append("Wird öffentlich sichtbar")
        elif self.visibility is Visibility.ORGANISATION:
            items.append("Wird innerhalb einer Organisation sichtbar")
        if self.financial:
            items.append("Hat finanzielle Wirkung")
        if self.legal:
            items.append("Kann rechtliche Wirkung haben")
        if self.reversibility is Reversibility.IRREVERSIBLE:
            items.append("Ist nicht zuverlässig rückgängig zu machen")
        elif self.reversibility is Reversibility.PARTIALLY_REVERSIBLE:
            items.append("Ist nur teilweise rückgängig zu machen")
        if self.uploads:
            items.append("Überträgt eine lokale Datei an einen externen Dienst")
        if self.downloads:
            items.append("Schreibt fremde Daten auf den lokalen Rechner")
        if self.secrets:
            items.append(
                "Benötigt Zugang: "
                + ", ".join(requirement.name for requirement in self.secrets)
            )
        return items


@dataclass(frozen=True)
class OperationManifest:
    name: str
    description: str
    parameters: dict[str, Any]
    effect: EffectManifest
    dry_run: Callable[[Mapping[str, Any]], str]

    def validate(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError(f"Ungültiger Operationsname: {self.name!r}")
        if self.parameters.get("type") != "object":
            raise ValueError(f"{self.name}: Parameterschema muss ein Objekt sein")
        properties = self.parameters.get("properties", {})
        missing = [
            field_name
            for field_name in self.effect.recipient_fields
            if field_name not in properties
        ]
        if missing:
            raise ValueError(
                f"{self.name}: Empfängerfelder fehlen im Schema: {', '.join(missing)}"
            )
        secret_names = [secret.name for secret in self.effect.secrets]
        if len(secret_names) != len(set(secret_names)):
            raise ValueError(f"{self.name}: Geheimnisanforderungen sind doppelt")

    def full_dry_run(self, arguments: Mapping[str, Any]) -> str:
        body = self.dry_run(arguments).strip()
        consequences = self.effect.consequences(arguments)
        if not consequences:
            return body
        return body + "\n\nFolgen:\n- " + "\n- ".join(consequences)


@dataclass(frozen=True)
class ConnectorManifest:
    id: str
    name: str
    version: str
    operations: tuple[OperationManifest, ...]
    homepage: str | None = None

    def validate(self) -> None:
        if not self.id or not self.version:
            raise ValueError("Connector-ID und Version sind Pflicht")
        names = [operation.name for operation in self.operations]
        if len(names) != len(set(names)):
            raise ValueError("Operationsnamen müssen innerhalb eines Connectors eindeutig sein")
        for operation in self.operations:
            operation.validate()

    def operation(self, name: str) -> OperationManifest:
        for operation in self.operations:
            if operation.name == name:
                return operation
        raise KeyError(f"Unbekannte Connector-Operation: {name}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "homepage": self.homepage,
            "operations": [
                {
                    "name": operation.name,
                    "description": operation.description,
                    "parameters": operation.parameters,
                    "effect": {
                        "reads": list(operation.effect.reads),
                        "writes": list(operation.effect.writes),
                        "deletes": list(operation.effect.deletes),
                        "recipient_fields": list(operation.effect.recipient_fields),
                        "visibility": operation.effect.visibility.value,
                        "reversibility": operation.effect.reversibility.value,
                        "financial": operation.effect.financial,
                        "legal": operation.effect.legal,
                        "publishes": operation.effect.publishes,
                        "uploads": operation.effect.uploads,
                        "downloads": operation.effect.downloads,
                        "secrets": [
                            {
                                "name": secret.name,
                                "purpose": secret.purpose,
                                "optional": secret.optional,
                            }
                            for secret in operation.effect.secrets
                        ],
                        "returns_untrusted": operation.effect.returns_untrusted,
                    },
                }
                for operation in self.operations
            ],
        }


class Connector:
    """Bindet ein Manifest an konkrete, aber weiterhin policy-gebundene Funktionen."""

    def __init__(
        self,
        manifest: ConnectorManifest,
        handlers: Mapping[str, Callable[..., str]],
    ) -> None:
        manifest.validate()
        missing = {operation.name for operation in manifest.operations} - set(handlers)
        extra = set(handlers) - {operation.name for operation in manifest.operations}
        if missing:
            raise ValueError("Handler fehlen: " + ", ".join(sorted(missing)))
        if extra:
            raise ValueError("Handler ohne Manifest: " + ", ".join(sorted(extra)))
        self.manifest = manifest
        self.handlers = dict(handlers)

    def tools(self) -> dict[str, Tool]:
        result: dict[str, Tool] = {}
        for operation in self.manifest.operations:
            handler = self.handlers[operation.name]
            result[operation.name] = Tool(
                name=operation.name,
                description=operation.description,
                parameters=operation.parameters,
                action_class=operation.effect.action_class({}),
                class_for=lambda arguments, effect=operation.effect: effect.action_class(arguments),
                run=handler,
                dry_run=lambda arguments, item=operation: item.full_dry_run(arguments),
                returns_untrusted=operation.effect.returns_untrusted,
            )
        return result


class ConnectorRegistry:
    def __init__(self, connectors: Iterable[Connector] = ()) -> None:
        self._connectors: dict[str, Connector] = {}
        for connector in connectors:
            self.register(connector)

    def register(self, connector: Connector) -> None:
        connector_id = connector.manifest.id
        if connector_id in self._connectors:
            raise ValueError(f"Connector bereits registriert: {connector_id}")
        self._connectors[connector_id] = connector

    def tools(self) -> dict[str, Tool]:
        tools: dict[str, Tool] = {}
        for connector_id in sorted(self._connectors):
            for name, tool in self._connectors[connector_id].tools().items():
                if name in tools:
                    raise ValueError(f"Werkzeugname kollidiert: {name}")
                tools[name] = tool
        return tools

    def catalogue(self) -> list[dict[str, Any]]:
        return [
            self._connectors[key].manifest.to_dict()
            for key in sorted(self._connectors)
        ]


__all__ = [
    "Connector",
    "ConnectorManifest",
    "ConnectorRegistry",
    "EffectManifest",
    "OperationManifest",
    "Reversibility",
    "SecretRequirement",
    "Visibility",
]
