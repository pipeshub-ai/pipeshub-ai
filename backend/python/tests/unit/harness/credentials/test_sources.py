"""P2.4a SR: the read-only PipesHub sources over the platform's own records."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.config.constants.service import config_node_constants
from app.harness.credentials.sources import (
    MCP_KIND,
    MODEL_KIND,
    TOOLSET_KIND,
    McpSecretSource,
    ModelKeySource,
    ToolsetSecretSource,
    owner_scoped,
)
from harness_core.credentials import CredentialReadOnly, CredentialScope
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.credentials import SecretSourceContract
from tests.unit.harness.credentials.support import (
    TOOLSET_INSTANCES,
    DictConfig,
    owned_toolsets,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

    from harness_core.credentials.ports import SecretSource

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def _toolset(record: dict[str, Any] | None) -> DictConfig:
    return DictConfig(owned_toolsets({"/services/toolsets/inst-1/user-1": record} if record is not None else {}))


async def test_sr1_toolset_record_when_authenticated() -> None:
    record = {"isAuthenticated": True, "credentials": {"access_token": TOKEN}}
    config = _toolset(record)
    source = ToolsetSecretSource(config)
    scope = CredentialScope(TOOLSET_KIND, "inst-1/user-1")
    first = await source.read("org-1", scope)
    assert first is not None and first.material == record and TOKEN not in first.version
    config.values["/services/toolsets/inst-1/user-1"]["credentials"]["access_token"] = TOKEN[:-4] + "ZZZZ"
    refreshed = await source.read("org-1", scope, first.version)
    assert refreshed is not None and refreshed.version != first.version


@pytest.mark.parametrize("record", [None, {"isAuthenticated": False, "credentials": {"access_token": TOKEN}}, "x"])
async def test_sr1_missing_or_unauthenticated_toolset(record: Any) -> None:  # noqa: ANN401
    config = DictConfig(owned_toolsets({"/services/toolsets/inst-1/user-1": record} if record is not None else {}))
    assert await ToolsetSecretSource(config).read("org-1", CredentialScope(TOOLSET_KIND, "inst-1/user-1")) is None


def _instance(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"_id": "mcp-1", "orgId": "org-1", "authMode": "api_token", "createdBy": "admin-1", **extra}


async def _mcp(instance: dict[str, Any] | None, values: dict[str, Any]) -> Any:  # noqa: ANN401
    async def resolved(instance_id: str, config: object, *_: object) -> dict[str, Any] | None:
        return instance

    with patch("app.edition_config.get_mcp_instance_resolved", new=resolved):
        return await McpSecretSource(DictConfig(values)).read("org-1", CredentialScope(MCP_KIND, "mcp-1/user-1"))


async def test_sr2_mcp_effective_record() -> None:
    own = {"isAuthenticated": True, "apiToken": TOKEN}
    found = await _mcp(_instance(), {"/services/mcp/credentials/mcp-1/user-1": own})
    assert found is not None and found.material == own
    admin = {"isAuthenticated": True, "apiToken": "admin-token-1"}
    shared = await _mcp(_instance(useAdminAuth=True), {"/services/mcp/credentials/mcp-1/admin-1": admin})
    assert shared is not None and shared.material == admin
    none = await _mcp(_instance(authMode="none"), {})
    assert none is not None and none.material == {}


async def test_sr2_mcp_unauthenticated_or_gone() -> None:
    assert await _mcp(_instance(), {"/services/mcp/credentials/mcp-1/user-1": {"isAuthenticated": False}}) is None
    assert await _mcp(None, {}) is None


async def test_sr3_model_key_by_model_key() -> None:
    config = DictConfig(
        {
            config_node_constants.AI_MODELS.value: {
                "llm": [
                    {"modelKey": "key-1", "configuration": {"apiKey": "sk-proj-" + "x" * 30, "model": "m"}},
                    {"modelKey": "key-2", "configuration": {"model": "m"}},
                ]
            }
        }
    )
    source = ModelKeySource(config)
    found = await source.read("org-1", CredentialScope(MODEL_KIND, "key-1"))
    assert found is not None and found.material == "sk-proj-" + "x" * 30
    assert await source.read("org-1", CredentialScope(MODEL_KIND, "key-2")) is None
    assert await source.read("org-1", CredentialScope(MODEL_KIND, "key-gone")) is None


async def test_sr3_the_logged_version_is_not_a_plain_digest_of_the_secret() -> None:
    import hashlib

    weak = "hunter2"
    entries = [{"modelKey": "key-1", "configuration": {"apiKey": weak, "model": "m"}}]
    config = DictConfig({config_node_constants.AI_MODELS.value: {"llm": entries}})
    source = ModelKeySource(config)
    first = await source.read("org-1", CredentialScope(MODEL_KIND, "key-1"))
    again = await source.read("org-1", CredentialScope(MODEL_KIND, "key-1"))
    assert first is not None and again is not None and first.version == again.version
    # credential.issued logs the version: a dictionary of sha256 guesses must not find the password in it.
    assert first.version not in {hashlib.sha256(json.dumps(weak).encode()).hexdigest()[:16],
                                 hashlib.sha256(weak.encode()).hexdigest()[:16]}
    entries[0]["configuration"]["apiKey"] = weak + "!"
    rotated = await source.read("org-1", CredentialScope(MODEL_KIND, "key-1"))
    assert rotated is not None and rotated.version != first.version


@pytest.mark.parametrize("source", [ToolsetSecretSource, McpSecretSource, ModelKeySource])
async def test_sr4_read_only(source: type) -> None:
    with pytest.raises(CredentialReadOnly):
        _ = await source(DictConfig()).write("org-1", CredentialScope("k", "a/b"), "x")


def _text(material: JsonValue) -> str:
    return material if isinstance(material, str) else json.dumps(material)


@dataclass
class _PlatformRecords:
    """A read-only source over the records its owner keeps in configuration (QA19-9)."""

    kind: str
    owner: Callable[[DictConfig, str, CredentialScope, JsonValue], None]
    build: Callable[[DictConfig], SecretSource]
    partitions_tenants: bool
    config: DictConfig = field(default_factory=DictConfig)
    clock: ManualClock = field(default_factory=ManualClock)
    keeps_history: bool = False

    @property
    def source(self) -> SecretSource:
        return self.build(self.config)

    async def own(self, tenant: str, scope: CredentialScope, material: JsonValue) -> None:
        self.owner(self.config, tenant, scope, material)


def _toolset_owner(config: DictConfig, tenant: str, scope: CredentialScope, material: JsonValue) -> None:
    instance, owner = owner_scoped(scope.name)
    instances = [i for i in config.values.get(TOOLSET_INSTANCES, []) if i["_id"] != instance]
    config.values[TOOLSET_INSTANCES] = [*instances, {"_id": instance, "orgId": tenant, "toolsetType": "jira"}]
    config.values[f"/services/toolsets/{instance}/{owner}"] = {"isAuthenticated": True, "credentials": material}


def _mcp_owner(config: DictConfig, tenant: str, scope: CredentialScope, material: JsonValue) -> None:
    instance, owner = owner_scoped(scope.name)
    config.values[f"/services/mcp/instances/{instance}"] = _instance(_id=instance, orgId=tenant)
    config.values[f"/services/mcp/credentials/{instance}/{owner}"] = {"isAuthenticated": True, "apiToken": _text(material)}


def _model_owner(config: DictConfig, tenant: str, scope: CredentialScope, material: JsonValue) -> None:
    config.values[config_node_constants.AI_MODELS.value] = {
        "llm": [{"modelKey": scope.name, "configuration": {"apiKey": _text(material), "model": "m"}}]
    }


class TestToolsetSecretSourceContract(SecretSourceContract):
    @pytest.fixture
    def source_harness(self) -> _PlatformRecords:
        return _PlatformRecords(TOOLSET_KIND, _toolset_owner, ToolsetSecretSource, partitions_tenants=True)


class TestMcpSecretSourceContract(SecretSourceContract):
    @pytest.fixture
    def source_harness(self) -> _PlatformRecords:
        return _PlatformRecords(MCP_KIND, _mcp_owner, McpSecretSource, partitions_tenants=True)


class TestModelKeySourceContract(SecretSourceContract):
    @pytest.fixture
    def source_harness(self) -> _PlatformRecords:
        # aiModels is one deployment-wide document with no org in it (QA19-O2 open for this source).
        return _PlatformRecords(MODEL_KIND, _model_owner, ModelKeySource, partitions_tenants=False)


async def test_qa19_o2_another_orgs_instance_resolves_no_secret() -> None:
    toolsets = DictConfig(owned_toolsets({"/services/toolsets/inst-1/user-1": {"isAuthenticated": True}}, "org-2"))
    assert await ToolsetSecretSource(toolsets).read("org-1", CredentialScope(TOOLSET_KIND, "inst-1/user-1")) is None
    assert await _mcp(_instance(orgId="org-2"), {"/services/mcp/credentials/mcp-1/user-1": {"isAuthenticated": True}}) is None
