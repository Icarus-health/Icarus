from __future__ import annotations

import json

import pytest

from icarus_memory.model_harness import (
    EvaluationCase,
    Evaluator,
    ModelRegistry,
    ModelSpec,
    PrivacyClass,
    RouteRequest,
    Router,
    RoutingProvider,
    TaskProfile,
    UsageBudget,
)
from icarus_memory.providers import ProviderError, Reply, from_env


class FakeProvider:
    def __init__(self, name: str, *, text: str = "ok", fails: int = 0, local: bool = False):
        self.name = name
        self.model = name
        self.is_local = local
        self.text = text
        self.fails = fails
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self.calls <= self.fails:
            raise ProviderError(f"{self.name} nicht erreichbar")
        return Reply(text=self.text, model=self.model)


def spec(
    model_id: str,
    *,
    quality: float,
    local: bool,
    tools: bool = True,
    privacy: PrivacyClass = PrivacyClass.REMOTE_ALLOWED,
    latency: int = 1000,
    input_cost: float = 0.0,
    output_cost: float = 0.0,
) -> ModelSpec:
    return ModelSpec(
        id=model_id,
        provider="openai_compatible",
        model=model_id,
        capabilities=frozenset({TaskProfile.CONVERSATION, TaskProfile.PLANNING}),
        context_window=32_000,
        is_local=local,
        supports_tools=tools,
        quality={
            TaskProfile.CONVERSATION: quality,
            TaskProfile.PLANNING: quality,
        },
        input_cost_per_million=input_cost,
        output_cost_per_million=output_cost,
        expected_latency_ms=latency,
        privacy=privacy,
    )


def test_local_model_wins_when_quality_is_within_margin():
    registry = ModelRegistry(
        [
            spec("remote", quality=0.92, local=False, latency=300),
            spec("local", quality=0.89, local=True, latency=900),
        ]
    )
    decision = Router(registry, local_quality_margin=0.05).select(
        RouteRequest(TaskProfile.CONVERSATION, input_tokens=200)
    )

    assert decision.model_id == "local"
    assert "lokal" in decision.reason


def test_higher_quality_remote_wins_outside_local_margin():
    registry = ModelRegistry(
        [
            spec("remote", quality=0.96, local=False),
            spec("local", quality=0.70, local=True),
        ]
    )
    decision = Router(registry).select(
        RouteRequest(TaskProfile.CONVERSATION, input_tokens=200)
    )

    assert decision.model_id == "remote"


def test_sensitive_request_never_falls_back_to_untrusted_remote():
    registry = ModelRegistry(
        [
            spec("remote", quality=0.99, local=False),
            spec("local", quality=0.75, local=True),
        ]
    )
    router = Router(registry)

    candidates = router.candidates(
        RouteRequest(
            TaskProfile.CONVERSATION,
            input_tokens=200,
            sensitivity="secret",
        )
    )

    assert [candidate.model_id for candidate in candidates] == ["local"]


def test_trusted_remote_is_allowed_for_confidential_but_not_secret():
    trusted = spec(
        "trusted",
        quality=0.9,
        local=False,
        privacy=PrivacyClass.TRUSTED_REMOTE,
    )
    registry = ModelRegistry([trusted])
    router = Router(registry)

    assert router.select(
        RouteRequest(
            TaskProfile.CONVERSATION,
            input_tokens=10,
            sensitivity="confidential",
        )
    ).model_id == "trusted"
    with pytest.raises(ProviderError):
        router.select(
            RouteRequest(
                TaskProfile.CONVERSATION,
                input_tokens=10,
                sensitivity="secret",
            )
        )


def test_tools_context_latency_quality_and_cost_are_hard_filters():
    registry = ModelRegistry(
        [
            spec("no-tools", quality=1.0, local=True, tools=False),
            spec("slow", quality=0.9, local=True, latency=9000),
            spec(
                "expensive",
                quality=0.9,
                local=True,
                input_cost=1000,
                output_cost=1000,
            ),
            spec("fit", quality=0.85, local=True, latency=200),
        ]
    )
    request = RouteRequest(
        TaskProfile.PLANNING,
        input_tokens=100,
        expected_output_tokens=100,
        requires_tools=True,
        minimum_quality=0.8,
        maximum_latency_ms=1000,
        maximum_cost=0.01,
    )

    assert Router(registry).select(request).model_id == "fit"


def test_fallback_opens_circuit_and_skips_broken_model():
    now = [100.0]
    registry = ModelRegistry(
        [
            spec("primary", quality=0.95, local=True),
            spec("fallback", quality=0.90, local=True),
        ]
    )
    primary = FakeProvider("primary", fails=10, local=True)
    fallback = FakeProvider("fallback", text="fallback-ok", local=True)
    events = []
    router = Router(
        registry,
        failure_threshold=1,
        cooldown_seconds=60,
        clock=lambda: now[0],
    )
    provider = RoutingProvider(
        registry,
        {"primary": primary, "fallback": fallback},
        router=router,
        audit=events.append,
    )

    assert provider.complete([{"role": "user", "content": "Hallo"}], []).text == "fallback-ok"
    assert primary.calls == 1
    assert fallback.calls == 1
    assert any(event["event"] == "model_route_failed" for event in events)

    assert provider.complete([{"role": "user", "content": "Noch einmal"}], []).text == "fallback-ok"
    assert primary.calls == 1, "Geöffneter Circuit muss den defekten Provider überspringen"
    assert fallback.calls == 2

    now[0] += 61
    provider.complete([{"role": "user", "content": "Nach Abkühlung"}], [])
    assert primary.calls == 2


def test_budget_is_checked_before_provider_call():
    registry = ModelRegistry(
        [spec("paid", quality=0.9, local=False, input_cost=1000, output_cost=1000)]
    )
    fake = FakeProvider("paid")
    provider = RoutingProvider(
        registry,
        {"paid": fake},
        budget=UsageBudget(maximum_cost=0.0001),
    )

    with pytest.raises(ProviderError, match="Kostenbudget"):
        provider.complete_for(
            TaskProfile.CONVERSATION,
            [{"role": "user", "content": "x" * 400}],
            [],
            expected_output_tokens=100,
        )
    assert fake.calls == 0


def test_routing_decision_is_auditable_and_usage_is_consumed():
    registry = ModelRegistry([spec("local", quality=0.9, local=True)])
    fake = FakeProvider("local", text="Antwort", local=True)
    events = []
    budget = UsageBudget(maximum_input_tokens=1000, maximum_output_tokens=1000)
    provider = RoutingProvider(
        registry,
        {"local": fake},
        audit=events.append,
        budget=budget,
    )

    reply = provider.complete_for(
        TaskProfile.PLANNING,
        [{"role": "user", "content": "Plane den Tag"}],
        [],
        expected_output_tokens=50,
    )

    assert reply.text == "Antwort"
    assert provider.last_decision is not None
    assert provider.last_decision.model_id == "local"
    assert budget.input_tokens > 0
    assert budget.output_tokens == 50
    assert [event["event"] for event in events] == [
        "model_route_selected",
        "model_route_completed",
    ]


def test_evaluator_produces_comparable_report(tmp_path):
    registry = ModelRegistry(
        [
            spec("good", quality=0.9, local=True),
            spec("bad", quality=0.7, local=True),
        ]
    )
    providers = {
        "good": FakeProvider("good", text="richtig", local=True),
        "bad": FakeProvider("bad", text="falsch", local=True),
    }
    evaluator = Evaluator(
        registry,
        providers,
        scorer=lambda case, reply: 1.0 if reply.text == case.expected else 0.0,
    )
    cases = [
        EvaluationCase(
            id="classification-1",
            profile=TaskProfile.CONVERSATION,
            messages=({"role": "user", "content": "Test"},),
            expected="richtig",
        )
    ]

    results = evaluator.run(cases)
    report = evaluator.report(results)
    assert report["models"]["good"]["mean_quality"] == 1.0
    assert report["models"]["bad"]["mean_quality"] == 0.0

    path = tmp_path / "report.json"
    evaluator.write_report(path, results)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert set(saved["models"]) == {"good", "bad"}


def test_from_env_builds_local_routing_provider_without_key(monkeypatch):
    monkeypatch.setenv(
        "ICARUS_MODEL_ROUTES",
        json.dumps(
            [
                {
                    "id": "ollama-local",
                    "provider": "openai_compatible",
                    "model": "llama3.1",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "capabilities": ["conversation"],
                    "context_window": 8192,
                    "is_local": True,
                    "supports_tools": True,
                    "quality": {"conversation": 0.8},
                }
            ]
        ),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    provider = from_env()

    assert isinstance(provider, RoutingProvider)
    assert provider.is_local is True
    assert provider.registry.get("ollama-local").model == "llama3.1"


def test_legacy_single_provider_path_remains_available(monkeypatch):
    monkeypatch.delenv("ICARUS_MODEL_ROUTES", raising=False)
    monkeypatch.setenv("ICARUS_PROVIDER", "ollama")
    monkeypatch.setenv("ICARUS_MODEL", "local-test")
    monkeypatch.setenv("ICARUS_BASE_URL", "http://localhost:11434/v1")

    provider = from_env()

    assert provider is not None
    assert provider.model == "local-test"
    assert provider.is_local is True
