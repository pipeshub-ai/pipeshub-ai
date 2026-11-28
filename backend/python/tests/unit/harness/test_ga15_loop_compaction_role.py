"""GA-15 (WP-73) MROLE-7: the loop's summaries call the compaction role's model when the run's pin maps one.

The pin's ``models.roles`` is folded as Node folds it (``model-roles.json``); the mapped model is found among the
org's configured models as a fallback route is, reached and metered like the run's own, and the summary is charged
as compaction. Unmapped, unconfigured, or a run on a model account: the run's own model summarises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from app.harness.assembly import model_policy
from app.harness.assembly.context_compiler import build_compactor
from app.harness.assembly.model_policy import compaction_model, compaction_route
from harness_core.logs import HarnessLog


def _run(roles: object) -> SimpleNamespace:
    dimensions = {} if roles is None else {"models.roles": SimpleNamespace(value=roles)}
    resolved = SimpleNamespace(effective_config=SimpleNamespace(dimensions=dimensions))
    run = SimpleNamespace(resolved=resolved, model=SimpleNamespace(provider="openai", model="gpt-6-sol"))
    run.model_copy = lambda update: SimpleNamespace(**{**vars(run), **update})
    return run


ENTRIES = [
    {"provider": "openAI", "modelKey": "mk-primary", "configuration": {"model": "gpt-6-sol"}},
    {"provider": "openAI", "modelKey": "mk-nano", "configuration": {"model": "gpt-5.4-nano, gpt-5.4-mini"}},
]


@pytest.fixture(autouse=True)
def _entries(monkeypatch: pytest.MonkeyPatch) -> None:
    async def entries(_config: object) -> list[dict[str, Any]]:
        return ENTRIES

    monkeypatch.setattr("app.harness.credentials.sources.configured_llm_entries", entries)


def test_mrole_7_the_pin_maps_the_compaction_role() -> None:
    run = _run({"compaction": "openai:gpt-5.4-nano", "fast": "anthropic:claude-haiku-4-5"})

    assert compaction_route(run, primary_account=False) == ("openai", "gpt-5.4-nano")
    assert compaction_route(run, primary_account=True) is None
    assert compaction_route(_run({"fast": "openai:gpt-5.4-nano"}), primary_account=False) is None
    assert compaction_route(SimpleNamespace(resolved=None), primary_account=False) is None  # type: ignore[arg-type]


@dataclass
class _Resolver:
    seen: list[Any] = field(default_factory=list)

    async def __call__(self, routed: SimpleNamespace, _services: object) -> SimpleNamespace:
        self.seen.append(routed.model)
        return SimpleNamespace(name=f"resolved:{routed.model.model_key}", transport=None)


async def test_mrole_7_the_mapped_model_is_the_configured_one_reached_and_metered_as_the_run_is() -> None:
    resolve, metered = _Resolver(), []
    services = SimpleNamespace(config_service=lambda: None)

    def meter(model: SimpleNamespace, routed: SimpleNamespace) -> SimpleNamespace:
        metered.append((model.name, routed.model.model))
        return SimpleNamespace(name=f"metered:{model.name}")

    got = await compaction_model(
        _run({"compaction": "openai:gpt-5.4-nano"}),
        services,  # type: ignore[arg-type]
        primary_account=False,
        resolve=resolve,
        meter=meter,
        log=HarnessLog.named("test"),
    )

    assert got.name == "metered:resolved:mk-nano"  # type: ignore[union-attr]
    assert [(c.provider, c.model, c.model_key, c.model_name) for c in resolve.seen] == [
        ("openai", "gpt-5.4-nano", "mk-nano", "gpt-5.4-nano")
    ]
    assert metered == [("resolved:mk-nano", "gpt-5.4-nano")]


@pytest.mark.parametrize(
    ("roles", "account"),
    [(None, False), ({"compaction": "openai:not-configured"}, False), ({"compaction": "openai:gpt-5.4-nano"}, True)],
    ids=["unmapped", "not-configured", "model-account"],
)
async def test_mrole_7_otherwise_the_runs_own_model_summarises(roles: object, account: bool) -> None:
    resolve = _Resolver()

    got = await compaction_model(
        _run(roles),
        SimpleNamespace(config_service=lambda: None),  # type: ignore[arg-type]
        primary_account=account,
        resolve=resolve,
        meter=lambda model, _routed: model,
        log=HarnessLog.named("test"),
    )

    assert got is None and resolve.seen == []


class _Transport:
    provider = "openai"
    model_name = "m"


def test_mrole_7_the_compactor_calls_the_compaction_model_when_there_is_one() -> None:
    own, summariser = _Transport(), _Transport()
    model = SimpleNamespace(context_length=1000, transport=own, compaction=SimpleNamespace(transport=summariser))
    host = SimpleNamespace()

    compiler = build_compactor(model, "gpt-6-sol", host)  # type: ignore[arg-type]

    assert compiler.strategy._model.transport is summariser  # type: ignore[attr-defined]
    plain = build_compactor(SimpleNamespace(context_length=1000, transport=own, compaction=None), "m", host)  # type: ignore[arg-type]
    assert plain.strategy._model.transport is own  # type: ignore[attr-defined]
    assert model_policy.compaction_route is compaction_route
