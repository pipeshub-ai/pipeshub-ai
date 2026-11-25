"""Model accounts behind ``ConfiguredModelAccess`` and the real ``ModelGateway`` (P2.4b AR, RV, RO, LK-4)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.harness.access.configured import ConfiguredModelAccess
from app.harness.credentials.accounts import VaultModelAccountStore
from app.harness.credentials.service import pipeshub_credential_service
from app.harness.credentials.vault import VAULT_ROOT, VaultSecretStore
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import GatewayRig, RecordedTransport
from tests.unit.harness.credentials.support import DictConfig

if TYPE_CHECKING:
    from harness_core.access.types import AccountRef, PinnedRoute

TENANT = "org-1"

# SEC-C-02 (ADR-MA-01): an account route's model must be one the org configured for that provider, so the
# accounts these tests use reach models the org lists (``/services/aiModels``).
CONFIGURED_LLMS: list[dict[str, object]] = [
    {"provider": "openAI", "modelKey": "m-openai", "configuration": {"model": "gpt-4o, gpt-x"}},
    {"provider": "anthropic", "modelKey": "m-anthropic", "configuration": {"model": "claude-x, claude-haiku-4-5"}},
    {"provider": "azureOpenAI", "modelKey": "m-azure", "configuration": {"model": "gpt-5"}},
    {
        "provider": "bedrock",
        "modelKey": "m-bedrock",
        "configuration": {"model": "us.anthropic.claude-sonnet-4-6-v1:0, meta.llama3-70b-instruct-v1:0"},
    },
    {"provider": "vertexAI", "modelKey": "m-vertex", "configuration": {"model": "claude-sonnet-4-6@20250929, gemini-2.5-pro"}},
]


@dataclass
class OutageConfig(DictConfig):
    """``DictConfig`` that reports a store outage as ``ConfigurationService`` does: ``default``
    unless the caller passes ``raise_on_error=True``; and records how the vault is read."""

    down: bool = False
    vault_reads: list[tuple[str, bool, bool]] = field(default_factory=list[tuple[str, bool, bool]])

    async def get_config(
        self, key: str, default: Any = None, use_cache: bool = False, *, raise_on_error: bool = False, **_: Any  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        if key.startswith(VAULT_ROOT + "/"):
            self.vault_reads.append((key, use_cache, raise_on_error))
        if self.down:
            if raise_on_error:
                raise ConnectionError("kv down")
            return default
        return await super().get_config(key, default, use_cache)


@dataclass
class Builds:
    """The transport factory: records what each transport was built from."""

    transport: RecordedTransport = field(default_factory=RecordedTransport)
    entries: list[tuple[str, dict[str, object], str, str]] = field(default_factory=list[tuple[str, dict[str, object], str, str]])

    async def __call__(self, provider: str, entry: dict[str, object], model: str, key: str) -> RecordedTransport:
        self.entries.append((provider, entry, model, key))
        return self.transport


@dataclass
class Accounts:
    clock: ManualClock = field(default_factory=ManualClock)
    config: OutageConfig = field(default_factory=OutageConfig)
    builds: Builds = field(default_factory=Builds)

    def __post_init__(self) -> None:
        from app.config.constants.service import config_node_constants

        self.config.values.setdefault(config_node_constants.AI_MODELS.value, {"llm": copy.deepcopy(CONFIGURED_LLMS)})
        self.vault = VaultSecretStore(self.config, self.clock)
        self.store = VaultModelAccountStore(self.vault, self.clock)
        self.credentials = pipeshub_credential_service(self.config, clock=self.clock, ids=SeededIds())
        self.access = ConfiguredModelAccess(
            self.config,  # type: ignore[arg-type]
            transports=self.builds,
            credentials=self.credentials,
            clock=self.clock,
        )

    async def put(self, ref: AccountRef, provider: str, key: str, **fields: str) -> None:
        _ = await self.store.put(
            TENANT, ref, provider, {"provider": provider, "apiKey": key, **fields}, secret_hint=key, label=None, actor="user-a"
        )

    def rig(self) -> GatewayRig:
        return GatewayRig(clock=self.clock, transport=self.builds.transport, resolver=_Resolver(self.access))  # type: ignore[arg-type]


class _Resolver:
    """``ConfiguredModelAccess`` where the rig expects its static resolver (``pin`` registers routes)."""

    def __init__(self, inner: ConfiguredModelAccess) -> None:
        self.inner = inner

    def add(self, *_: object, **__: object) -> None:
        return None

    async def resolve(self, tenant: str, route: PinnedRoute, *, run_id: str | None = None, need: object = None) -> Any:  # noqa: ANN401
        return await self.inner.resolve(tenant, route, run_id=run_id)
