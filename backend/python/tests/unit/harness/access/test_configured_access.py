"""E3 PA-1..PA-3 and the resolver contract on ``ConfiguredModelAccess`` (``/services/aiModels``)."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, override

import pytest

from app.config.constants.service import config_node_constants
from app.harness.access.configured import (
    ACCOUNT_SECRET_KINDS,
    ConfiguredModelAccess,
    build_configured_transport,
    without_secrets,
)
from app.harness.credentials.service import pipeshub_credential_service
from harness_core.access.errors import (
    AccountNotPermitted,
    ProviderUnsupported,
    RouteNotConfigured,
)
from harness_core.access.types import AccountKind, PinnedRoute
from harness_core.credentials import CredentialPurpose
from harness_core.edition import NO_HARNESS_EDITION, HarnessEdition
from harness_core.models.placement import ModelRegions, NoModelRegions
from harness_core.models.provider_state import ANTHROPIC_MESSAGES
from harness_core.models.transports.anthropic import AnthropicTransport
from harness_core.models.transports.azure_openai import AzureOpenAITransport
from harness_core.models.transports.openai import OpenAITransport
from harness_core.security.token_patterns import SECRET_MASK
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.model_access import ModelAccessResolverContract
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import RecordedTransport

if TYPE_CHECKING:
    from collections.abc import Mapping


def _entry(provider: str, key: str, models: str, *, default: bool = False, **configuration: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "modelKey": key,
        "isDefault": default,
        "configuration": {"model": models, "apiKey": "test-key-not-real", **configuration},
    }


AI_MODELS: dict[str, Any] = {
    "llm": [
        _entry("openAI", "key-openai", "gpt-4o-mini, gpt-4o"),
        _entry(
            "azureOpenAI",
            "key-azure",
            "gpt-5.4-mini",
            default=True,
            endpoint="https://example.openai.azure.com",
            deploymentName="dep-1",
        ),
        _entry("anthropic", "key-anthropic", "claude-haiku-4-5"),
        _entry("bedrock", "key-bedrock", "claude-x"),
        _entry("cohere", "key-cohere", "command-r"),
    ]
}


@dataclass
class FakeConfig:
    document: dict[str, Any]
    fresh: dict[str, Any] | None = None
    reads: list[bool] = field(default_factory=list[bool])

    async def get_config(self, key: str, default: object = None, use_cache: bool = False, **_: object) -> object:
        assert key == config_node_constants.AI_MODELS.value
        self.reads.append(use_cache)
        if not use_cache and self.fresh is not None:
            return self.fresh
        return self.document


@dataclass
class Builds:
    calls: list[tuple[str, str, str]] = field(default_factory=list[tuple[str, str, str]])

    async def __call__(self, provider: str, entry: dict[str, object], model: str, key: str) -> RecordedTransport:
        self.calls.append((provider, model, key))
        return RecordedTransport(family={"azureOpenAI": "azure_direct"}.get(provider, provider.lower()))


@dataclass
class _Harness:
    resolver: ConfiguredModelAccess
    tenant: str = "org-1"
    configured: PinnedRoute = PinnedRoute("openai", "gpt-4o", config_key="key-openai", config_model="gpt-4o")
    unsupported: PinnedRoute = PinnedRoute("cohere", "command-r", config_key="key-cohere", config_model="command-r")
    not_permitted: PinnedRoute = PinnedRoute("bedrock", "claude-x", config_key="key-bedrock", config_model="claude-x")


class TestConfiguredModelAccessContract(ModelAccessResolverContract):
    @pytest.fixture
    def resolver_harness(self) -> _Harness:
        return _Harness(ConfiguredModelAccess(FakeConfig(copy.deepcopy(AI_MODELS)), transports=Builds()))  # type: ignore[arg-type]


async def test_pa1_by_key_and_name() -> None:
    builds = Builds()
    access = ConfiguredModelAccess(FakeConfig(copy.deepcopy(AI_MODELS)), transports=builds)  # type: ignore[arg-type]
    resolved = await access.resolve("org-1", PinnedRoute("openai", "gpt-4o-mini", config_key="key-openai", config_model="gpt-4o-mini"))
    assert (resolved.access.model, resolved.pricing_model) == ("gpt-4o-mini", "gpt-4o-mini")
    assert resolved.access.account is not None and resolved.access.account.account_id == "key-openai"
    assert builds.calls == [("openAI", "gpt-4o-mini", "key-openai")]


async def test_pa1_the_org_default_is_the_entry_marked_default() -> None:
    builds = Builds()
    access = ConfiguredModelAccess(FakeConfig(copy.deepcopy(AI_MODELS)), transports=builds)  # type: ignore[arg-type]
    resolved = await access.resolve("org-1", PinnedRoute("pipeshub", "org-default"))
    assert resolved.access.provider == "azureopenai" and resolved.access.model == "gpt-5.4-mini"
    assert builds.calls == [("azureOpenAI", "gpt-5.4-mini", "key-azure")]


async def test_mg15_a_resolved_route_carries_the_region_the_editions_model_regions_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = copy.deepcopy(AI_MODELS)
    document["llm"].append(
        _entry("azureOpenAI", "key-azure-eu", "gpt-4o", endpoint="https://swedencentral.api.cognitive.microsoft.com")
    )
    monkeypatch.setattr("app.edition_services.harness_edition", lambda: NO_HARNESS_EDITION)
    access = ConfiguredModelAccess(FakeConfig(document), transports=Builds())  # type: ignore[arg-type]
    route = PinnedRoute("azureopenai", "gpt-4o", config_key="key-azure-eu", config_model="gpt-4o")
    assert (await access.resolve("org-1", route)).access.region is None, "no region is read by default"

    asked: list[tuple[str, str]] = []

    class _Named(NoModelRegions):
        @override
        def region_of(
            self, provider: str, configuration: Mapping[str, object] | None, model: str, *, deployment: str
        ) -> str | None:
            asked.append((provider, model))
            return "swedencentral"

    monkeypatch.setattr(
        "app.edition_services.harness_edition", lambda: HarnessEdition(ports={ModelRegions: _Named()})
    )
    access = ConfiguredModelAccess(FakeConfig(document), transports=Builds())  # type: ignore[arg-type]
    assert (await access.resolve("org-1", route)).access.region == "swedencentral"
    assert asked == [("azureOpenAI", "gpt-4o")]


@pytest.mark.parametrize(
    "route",
    [
        PinnedRoute("openai", "gpt-4o", config_key="key-gone", config_model="gpt-4o"),
        PinnedRoute("openai", "gpt-9", config_key="key-openai", config_model="gpt-9"),
        PinnedRoute("anthropic", "gpt-4o", config_key="key-openai", config_model="gpt-4o"),
        PinnedRoute("openai", "gpt-4o"),
    ],
    ids=["key-gone", "name-gone", "provider-changed", "no-key"],
)
async def test_pa1_never_another_model(route: PinnedRoute) -> None:
    builds = Builds()
    config = FakeConfig(copy.deepcopy(AI_MODELS))
    with pytest.raises(RouteNotConfigured):
        await ConfiguredModelAccess(config, transports=builds).resolve("org-1", route)  # type: ignore[arg-type]
    assert builds.calls == []


async def test_pa1_a_key_missing_from_the_cache_is_read_fresh_once() -> None:
    fresh = copy.deepcopy(AI_MODELS)
    fresh["llm"].append(_entry("openAI", "key-new", "gpt-4.1"))
    config = FakeConfig(copy.deepcopy(AI_MODELS), fresh=fresh)
    resolved = await ConfiguredModelAccess(config, transports=Builds()).resolve(  # type: ignore[arg-type]
        "org-1", PinnedRoute("openai", "gpt-4.1", config_key="key-new", config_model="gpt-4.1")
    )
    assert resolved.access.model == "gpt-4.1"
    # The entry, then its key at issue and at resolve: each read cached first, then fresh once.
    assert config.reads == [True, False] * 3


async def test_pa1_no_default_is_refused() -> None:
    document = copy.deepcopy(AI_MODELS)
    for entry in document["llm"]:
        entry["isDefault"] = False
    with pytest.raises(RouteNotConfigured):
        await ConfiguredModelAccess(FakeConfig(document), transports=Builds()).resolve(  # type: ignore[arg-type]
            "org-1", PinnedRoute("pipeshub", "org-default")
        )


@pytest.mark.parametrize(
    ("provider", "outcome"),
    [
        ("openAI", AccountKind.ORG_API_KEY),
        ("azureOpenAI", AccountKind.ORG_API_KEY),
        ("anthropic", AccountKind.ORG_API_KEY),
        ("gemini", AccountKind.ORG_API_KEY),
        ("bedrock", AccountNotPermitted),
        ("vertexAI", AccountNotPermitted),
        ("ollama", ProviderUnsupported),
        ("cohere", ProviderUnsupported),
        ("openAICompatible", AccountKind.ORG_API_KEY),
    ],
)
async def test_pa2_provider_classification(provider: str, outcome: object) -> None:
    document = {"llm": [_entry(provider, "k", "m")]}
    access = ConfiguredModelAccess(FakeConfig(document), transports=Builds())  # type: ignore[arg-type]
    route = PinnedRoute(provider.lower(), "m", config_key="k", config_model="m")
    if isinstance(outcome, AccountKind):
        resolved = await access.resolve("org-1", route)
        assert resolved.access.account is not None and resolved.access.account.kind is outcome
    else:
        with pytest.raises(outcome):  # type: ignore[call-overload]
            await access.resolve("org-1", route)


async def test_pa2_no_direct_transport_is_refused() -> None:
    async def none(*_: object) -> None:
        return None

    access = ConfiguredModelAccess(FakeConfig(copy.deepcopy(AI_MODELS)), transports=none)  # type: ignore[arg-type]
    with pytest.raises(ProviderUnsupported):
        await access.resolve("org-1", PinnedRoute("openai", "gpt-4o", config_key="key-openai", config_model="gpt-4o"))


async def test_pa3_transports_are_cached_per_entry_and_rebuilt_on_change() -> None:
    builds = Builds()
    config = FakeConfig(copy.deepcopy(AI_MODELS))
    access = ConfiguredModelAccess(config, transports=builds)  # type: ignore[arg-type]
    route = PinnedRoute("openai", "gpt-4o", config_key="key-openai", config_model="gpt-4o")
    first = await access.resolve("org-1", route)
    again = await access.resolve("org-1", route)
    assert first.transport is again.transport and len(builds.calls) == 1
    config.document["llm"][0]["configuration"]["apiKey"] = "rotated-key-not-real"
    rotated = await access.resolve("org-1", route)
    assert rotated.transport is not first.transport and len(builds.calls) == 2


@pytest.mark.parametrize(
    ("entry", "kind"),
    [
        (_entry("openAI", "k", "gpt-4o-mini"), OpenAITransport),
        (
            _entry("azureOpenAI", "k", "gpt-4o-mini", endpoint="https://example.openai.azure.com", deploymentName="dep"),
            AzureOpenAITransport,
        ),
        (_entry("anthropic", "k", "claude-haiku-4-5"), AnthropicTransport),
    ],
)
async def test_pa3_the_real_builder_reaches_the_direct_transport(entry: dict[str, Any], kind: type) -> None:
    models = entry["configuration"]["model"]
    transport = await build_configured_transport(entry["provider"], entry, models, entry["modelKey"])
    assert isinstance(transport, kind)
    # WP62-MGW-9: OpenAI and Azure carry an engine's cache key (the cache policy's hosts) and its reasoning items.
    # WP56-WIRE-6: Anthropic carries its signed thinking back for Claude Code's next turn; it has no cache key or
    # parallel-calls parameter to honour.
    if isinstance(transport, OpenAITransport):
        assert "prompt_cache_key" in transport.honoured_call_options
        assert transport._keep_state is True  # pyright: ignore[reportPrivateUsage]
    else:
        assert transport.honoured_call_options == frozenset()
        assert transport.provider_state_wire == ANTHROPIC_MESSAGES


def _wired(document: dict[str, Any]) -> tuple[ConfiguredModelAccess, Builds, FakeConfig, list[CredentialPurpose]]:
    config = FakeConfig(document)
    service = pipeshub_credential_service(config, clock=ManualClock(), ids=SeededIds())
    issued: list[CredentialPurpose] = []
    real = service.issue

    async def issue(purpose: CredentialPurpose, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        issued.append(purpose)
        return await real(purpose, *args, **kwargs)

    service.issue = issue  # type: ignore[method-assign]
    builds = Builds()
    access = ConfiguredModelAccess(config, transports=builds, credentials=service, clock=ManualClock())  # type: ignore[arg-type]
    return access, builds, config, issued


class _KeyedBuilds(Builds):
    entries: list[dict[str, object]]

    async def __call__(self, provider: str, entry: dict[str, object], model: str, key: str) -> RecordedTransport:
        self.entries.append(entry)
        return await super().__call__(provider, entry, model, key)


async def test_ma1_the_key_comes_by_handle_for_the_run() -> None:
    access, _, config, issued = _wired(copy.deepcopy(AI_MODELS))
    builds = _KeyedBuilds()
    builds.entries = []
    access._transports = builds  # type: ignore[assignment]
    route = PinnedRoute("openai", "gpt-4o", config_key="key-openai", config_model="gpt-4o")
    _ = await access.resolve("org-1", route, run_id="run-7")
    assert issued == [CredentialPurpose("org-1", "model:key-openai", "run-7")]
    assert builds.entries[0]["configuration"]["apiKey"] == "test-key-not-real"  # type: ignore[index]
    assert "test-key-not-real" not in json.dumps(without_secrets(config.document["llm"][0]))


async def test_ma2_a_changed_key_rebuilds_the_transport() -> None:
    access, builds, config, _ = _wired(copy.deepcopy(AI_MODELS))
    route = PinnedRoute("openai", "gpt-4o", config_key="key-openai", config_model="gpt-4o")
    first = await access.resolve("org-1", route, run_id="run-1")
    assert await access.resolve("org-1", route, run_id="run-1") is first
    config.document["llm"][0]["configuration"]["apiKey"] = "test-key-rotated"
    second = await access.resolve("org-1", route, run_id="run-1")
    assert second is not first and len(builds.calls) == 2


async def test_ma3_an_entry_without_a_key_is_not_configured() -> None:
    document = copy.deepcopy(AI_MODELS)
    del document["llm"][0]["configuration"]["apiKey"]
    access, builds, _, _ = _wired(document)
    with pytest.raises(RouteNotConfigured):
        _ = await access.resolve("org-1", PinnedRoute("openai", "gpt-4o", config_key="key-openai", config_model="gpt-4o"))
    assert builds.calls == []


def test_ma5_ar6_each_account_kind_has_its_credential_kind() -> None:
    assert dict(ACCOUNT_SECRET_KINDS) == {
        AccountKind.ORG_API_KEY: "model",
        AccountKind.USER_API_KEY: "vault",
        AccountKind.ORG_CLOUD: "vault",
    }


async def test_sec4_9_the_resolved_route_masks_the_key_it_holds() -> None:
    access, _, _, _ = _wired(copy.deepcopy(AI_MODELS))
    route = PinnedRoute("openai", "gpt-4o", config_key="key-openai", config_model="gpt-4o")
    resolved = await access.resolve("org-1", route, run_id="run-1")
    assert resolved.secrets.mask("401: test-key-not-real refused") == f"401: {SECRET_MASK} refused"
    assert "test-key-not-real" not in repr(resolved)
