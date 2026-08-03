"""Modell-Harness: Registry, Routing, Fallback, Budget und Evaluation.

Das Harness hält Gedächtnis und Nutzeridentität vollständig außerhalb der
Anbieterprofile. Modelle sind austauschbare Laufzeitmotoren mit expliziten
Fähigkeiten und messbaren Grenzen.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from .providers import Provider, ProviderError, Reply


class TaskProfile(str, Enum):
    CONVERSATION = "conversation"
    PLANNING = "planning"
    RESEARCH = "research"
    SUMMARISATION = "summarisation"
    CODE = "code"
    DOCUMENTS = "documents"
    CLASSIFICATION = "classification"


class PrivacyClass(str, Enum):
    LOCAL_ONLY = "local_only"
    TRUSTED_REMOTE = "trusted_remote"
    REMOTE_ALLOWED = "remote_allowed"


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: str
    model: str
    capabilities: frozenset[TaskProfile]
    context_window: int
    is_local: bool
    supports_tools: bool = False
    quality: Mapping[TaskProfile, float] = field(default_factory=dict)
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    expected_latency_ms: int = 1000
    privacy: PrivacyClass = PrivacyClass.REMOTE_ALLOWED
    enabled: bool = True

    def quality_for(self, profile: TaskProfile) -> float:
        return float(self.quality.get(profile, 0.0))


@dataclass(frozen=True)
class RouteRequest:
    profile: TaskProfile
    input_tokens: int
    expected_output_tokens: int = 512
    sensitivity: str = "normal"
    requires_tools: bool = False
    minimum_quality: float = 0.0
    maximum_latency_ms: int | None = None
    maximum_cost: float | None = None
    preferred_model_id: str | None = None


@dataclass(frozen=True)
class RouteDecision:
    model_id: str
    reason: tuple[str, ...]
    estimated_cost: float
    expected_latency_ms: int
    quality: float
    fallback_rank: int


@dataclass
class CircuitState:
    failures: int = 0
    open_until: float = 0.0

    def available(self, now: float) -> bool:
        return now >= self.open_until


@dataclass
class UsageBudget:
    maximum_cost: float | None = None
    maximum_input_tokens: int | None = None
    maximum_output_tokens: int | None = None
    spent: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    def check(self, cost: float, input_tokens: int, output_tokens: int) -> None:
        if self.maximum_cost is not None and self.spent + cost > self.maximum_cost:
            raise ProviderError("Das konfigurierte Modellkostenbudget wäre überschritten.")
        if (
            self.maximum_input_tokens is not None
            and self.input_tokens + input_tokens > self.maximum_input_tokens
        ):
            raise ProviderError("Das konfigurierte Eingabetokenbudget wäre überschritten.")
        if (
            self.maximum_output_tokens is not None
            and self.output_tokens + output_tokens > self.maximum_output_tokens
        ):
            raise ProviderError("Das konfigurierte Ausgabetokenbudget wäre überschritten.")

    def consume(self, cost: float, input_tokens: int, output_tokens: int) -> None:
        self.check(cost, input_tokens, output_tokens)
        self.spent += cost
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


class ModelRegistry:
    def __init__(self, specs: Iterable[ModelSpec] = ()) -> None:
        self._specs: dict[str, ModelSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ModelSpec) -> None:
        if not spec.id.strip():
            raise ValueError("ModelSpec.id darf nicht leer sein")
        if spec.context_window <= 0:
            raise ValueError("context_window muss positiv sein")
        self._specs[spec.id] = spec

    def get(self, model_id: str) -> ModelSpec:
        try:
            return self._specs[model_id]
        except KeyError as exc:
            raise KeyError(f"Unbekanntes Modellprofil: {model_id}") from exc

    def enabled(self) -> list[ModelSpec]:
        return sorted((spec for spec in self._specs.values() if spec.enabled), key=lambda item: item.id)

    def to_dict(self) -> list[dict[str, object]]:
        result = []
        for spec in self.enabled():
            result.append(
                {
                    **asdict(spec),
                    "capabilities": sorted(item.value for item in spec.capabilities),
                    "quality": {key.value: value for key, value in spec.quality.items()},
                    "privacy": spec.privacy.value,
                }
            )
        return result


class Router:
    """Deterministisches Routing nach Qualität, Schutz, Kosten und Latenz."""

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        local_quality_margin: float = 0.05,
        failure_threshold: int = 2,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.registry = registry
        self.local_quality_margin = local_quality_margin
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self.circuits: dict[str, CircuitState] = {}

    @staticmethod
    def estimate_cost(spec: ModelSpec, request: RouteRequest) -> float:
        return (
            request.input_tokens * spec.input_cost_per_million
            + request.expected_output_tokens * spec.output_cost_per_million
        ) / 1_000_000

    def _privacy_allowed(self, spec: ModelSpec, request: RouteRequest) -> bool:
        sensitivity = request.sensitivity.casefold()
        if sensitivity in {"secret", "highly_sensitive", "local_only"}:
            return spec.is_local
        if sensitivity in {"sensitive", "confidential"}:
            return spec.is_local or spec.privacy == PrivacyClass.TRUSTED_REMOTE
        return spec.privacy != PrivacyClass.LOCAL_ONLY or spec.is_local

    def candidates(self, request: RouteRequest) -> list[RouteDecision]:
        now = self.clock()
        viable: list[tuple[ModelSpec, float, float, list[str]]] = []
        for spec in self.registry.enabled():
            reasons: list[str] = []
            if request.profile not in spec.capabilities:
                continue
            if request.input_tokens + request.expected_output_tokens > spec.context_window:
                continue
            if request.requires_tools and not spec.supports_tools:
                continue
            if not self._privacy_allowed(spec, request):
                continue
            quality = spec.quality_for(request.profile)
            if quality < request.minimum_quality:
                continue
            if request.maximum_latency_ms is not None and spec.expected_latency_ms > request.maximum_latency_ms:
                continue
            cost = self.estimate_cost(spec, request)
            if request.maximum_cost is not None and cost > request.maximum_cost:
                continue
            circuit = self.circuits.setdefault(spec.id, CircuitState())
            if not circuit.available(now):
                continue

            reasons.append(f"Qualität {quality:.2f}")
            reasons.append("lokal" if spec.is_local else "remote")
            reasons.append(f"geschätzte Kosten {cost:.6f}")
            reasons.append(f"erwartete Latenz {spec.expected_latency_ms} ms")
            viable.append((spec, quality, cost, reasons))

        if request.preferred_model_id:
            viable.sort(key=lambda item: item[0].id != request.preferred_model_id)

        # Qualität ist die harte Leitgröße. Liegt ein lokales Modell nur knapp
        # darunter, gewinnt es aus Datenschutz- und Robustheitsgründen.
        best_quality = max((item[1] for item in viable), default=0.0)
        def score(item: tuple[ModelSpec, float, float, list[str]]) -> tuple[float, ...]:
            spec, quality, cost, _ = item
            local_bonus = 1.0 if spec.is_local and quality >= best_quality - self.local_quality_margin else 0.0
            preferred = 1.0 if request.preferred_model_id == spec.id else 0.0
            return (
                preferred,
                local_bonus,
                quality,
                -float(spec.expected_latency_ms),
                -cost,
                -float(len(spec.id)),
            )

        viable.sort(key=score, reverse=True)
        return [
            RouteDecision(
                model_id=spec.id,
                reason=tuple(reasons),
                estimated_cost=cost,
                expected_latency_ms=spec.expected_latency_ms,
                quality=quality,
                fallback_rank=index,
            )
            for index, (spec, quality, cost, reasons) in enumerate(viable)
        ]

    def select(self, request: RouteRequest) -> RouteDecision:
        candidates = self.candidates(request)
        if not candidates:
            raise ProviderError("Kein Modell erfüllt Fähigkeiten, Datenschutz und Budget der Aufgabe.")
        return candidates[0]

    def success(self, model_id: str) -> None:
        self.circuits[model_id] = CircuitState()

    def failure(self, model_id: str) -> None:
        state = self.circuits.setdefault(model_id, CircuitState())
        state.failures += 1
        if state.failures >= self.failure_threshold:
            state.open_until = self.clock() + self.cooldown_seconds


class AuditSink(Protocol):
    def __call__(self, event: Mapping[str, object]) -> None: ...


class RoutingProvider:
    """Provider-kompatibler Modellrouter mit kontrollierten Fallbacks."""

    name = "router"
    model = "automatic"

    def __init__(
        self,
        registry: ModelRegistry,
        providers: Mapping[str, Provider],
        *,
        router: Router | None = None,
        budget: UsageBudget | None = None,
        audit: AuditSink | None = None,
        default_profile: TaskProfile = TaskProfile.CONVERSATION,
    ) -> None:
        missing = {spec.id for spec in registry.enabled()} - set(providers)
        if missing:
            raise ValueError(f"Providerinstanzen fehlen: {', '.join(sorted(missing))}")
        self.registry = registry
        self.providers = dict(providers)
        self.router = router or Router(registry)
        self.budget = budget or UsageBudget()
        self.audit = audit
        self.default_profile = default_profile
        enabled = registry.enabled()
        self.is_local = bool(enabled) and all(spec.is_local for spec in enabled)
        self.last_decision: RouteDecision | None = None

    @staticmethod
    def _estimate_tokens(messages: Sequence[Mapping[str, Any]]) -> int:
        characters = sum(len(str(message.get("content") or "")) for message in messages)
        return max(1, math.ceil(characters / 4))

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Reply:
        return self.complete_for(
            self.default_profile,
            messages,
            tools,
            sensitivity="normal",
        )

    def complete_for(
        self,
        profile: TaskProfile,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        sensitivity: str = "normal",
        minimum_quality: float = 0.0,
        maximum_cost: float | None = None,
        maximum_latency_ms: int | None = None,
        expected_output_tokens: int = 512,
    ) -> Reply:
        request = RouteRequest(
            profile=profile,
            input_tokens=self._estimate_tokens(messages),
            expected_output_tokens=expected_output_tokens,
            sensitivity=sensitivity,
            requires_tools=bool(tools),
            minimum_quality=minimum_quality,
            maximum_cost=maximum_cost,
            maximum_latency_ms=maximum_latency_ms,
        )
        decisions = self.router.candidates(request)
        if not decisions:
            raise ProviderError("Kein Modell erfüllt Fähigkeiten, Datenschutz und Budget der Aufgabe.")

        errors: list[str] = []
        for decision in decisions:
            spec = self.registry.get(decision.model_id)
            self.budget.check(
                decision.estimated_cost,
                request.input_tokens,
                request.expected_output_tokens,
            )
            started = time.perf_counter()
            self._audit(
                {
                    "event": "model_route_selected",
                    "model_id": spec.id,
                    "provider": spec.provider,
                    "model": spec.model,
                    "profile": profile.value,
                    "fallback_rank": decision.fallback_rank,
                    "reason": list(decision.reason),
                    "estimated_cost": decision.estimated_cost,
                    "sensitivity": sensitivity,
                }
            )
            try:
                reply = self.providers[spec.id].complete(messages, tools)
            except Exception as exc:  # Providergrenzen sind heterogen.
                self.router.failure(spec.id)
                errors.append(f"{spec.id}: {exc}")
                self._audit(
                    {
                        "event": "model_route_failed",
                        "model_id": spec.id,
                        "error": str(exc),
                        "fallback_continues": True,
                    }
                )
                continue

            elapsed_ms = round((time.perf_counter() - started) * 1000)
            self.router.success(spec.id)
            self.budget.consume(
                decision.estimated_cost,
                request.input_tokens,
                request.expected_output_tokens,
            )
            self.last_decision = decision
            self.model = spec.model
            self._audit(
                {
                    "event": "model_route_completed",
                    "model_id": spec.id,
                    "elapsed_ms": elapsed_ms,
                    "estimated_cost": decision.estimated_cost,
                }
            )
            return reply

        raise ProviderError("Alle geeigneten Modelle sind fehlgeschlagen: " + " | ".join(errors))

    def _audit(self, event: Mapping[str, object]) -> None:
        if self.audit is not None:
            self.audit(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    **event,
                }
            )


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    profile: TaskProfile
    messages: tuple[dict[str, Any], ...]
    expected: object | None = None
    tools: tuple[dict[str, Any], ...] = ()
    sensitivity: str = "normal"


@dataclass(frozen=True)
class EvaluationResult:
    case_id: str
    model_id: str
    success: bool
    quality: float
    latency_ms: int
    estimated_cost: float
    error: str | None = None


class Evaluator:
    """Vergleicht Modellversionen auf einem versionierten Icarus-Datensatz."""

    def __init__(
        self,
        registry: ModelRegistry,
        providers: Mapping[str, Provider],
        scorer: Callable[[EvaluationCase, Reply], float],
    ) -> None:
        self.registry = registry
        self.providers = providers
        self.scorer = scorer

    def run(self, cases: Iterable[EvaluationCase]) -> list[EvaluationResult]:
        results: list[EvaluationResult] = []
        for case in cases:
            request = RouteRequest(
                case.profile,
                input_tokens=RoutingProvider._estimate_tokens(case.messages),
                expected_output_tokens=512,
                sensitivity=case.sensitivity,
                requires_tools=bool(case.tools),
            )
            for spec in self.registry.enabled():
                if case.profile not in spec.capabilities:
                    continue
                cost = Router.estimate_cost(spec, request)
                started = time.perf_counter()
                try:
                    reply = self.providers[spec.id].complete(list(case.messages), list(case.tools))
                    quality = min(1.0, max(0.0, float(self.scorer(case, reply))))
                    error = None
                    success = True
                except Exception as exc:
                    quality = 0.0
                    error = str(exc)
                    success = False
                results.append(
                    EvaluationResult(
                        case.id,
                        spec.id,
                        success,
                        quality,
                        round((time.perf_counter() - started) * 1000),
                        cost,
                        error,
                    )
                )
        return results

    @staticmethod
    def report(results: Sequence[EvaluationResult]) -> dict[str, object]:
        grouped: dict[str, list[EvaluationResult]] = {}
        for result in results:
            grouped.setdefault(result.model_id, []).append(result)
        models = {}
        for model_id, items in sorted(grouped.items()):
            models[model_id] = {
                "cases": len(items),
                "success_rate": sum(item.success for item in items) / len(items),
                "mean_quality": sum(item.quality for item in items) / len(items),
                "mean_latency_ms": sum(item.latency_ms for item in items) / len(items),
                "estimated_cost": sum(item.estimated_cost for item in items),
            }
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "models": models,
            "results": [asdict(item) for item in results],
        }

    @classmethod
    def write_report(cls, path: str | Path, results: Sequence[EvaluationResult]) -> None:
        Path(path).write_text(
            json.dumps(cls.report(results), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def specs_from_json(raw: str) -> list[ModelSpec]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("ICARUS_MODEL_ROUTES muss eine JSON-Liste sein")
    specs = []
    for item in data:
        quality = {
            TaskProfile(key): float(value)
            for key, value in (item.get("quality") or {}).items()
        }
        specs.append(
            ModelSpec(
                id=str(item["id"]),
                provider=str(item["provider"]),
                model=str(item["model"]),
                capabilities=frozenset(TaskProfile(value) for value in item.get("capabilities", ["conversation"])),
                context_window=int(item.get("context_window", 8192)),
                is_local=bool(item.get("is_local", False)),
                supports_tools=bool(item.get("supports_tools", False)),
                quality=quality,
                input_cost_per_million=float(item.get("input_cost_per_million", 0.0)),
                output_cost_per_million=float(item.get("output_cost_per_million", 0.0)),
                expected_latency_ms=int(item.get("expected_latency_ms", 1000)),
                privacy=PrivacyClass(item.get("privacy", "remote_allowed")),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return specs


__all__ = [
    "CircuitState",
    "EvaluationCase",
    "EvaluationResult",
    "Evaluator",
    "ModelRegistry",
    "ModelSpec",
    "PrivacyClass",
    "RouteDecision",
    "RouteRequest",
    "Router",
    "RoutingProvider",
    "TaskProfile",
    "UsageBudget",
    "specs_from_json",
]
