"""CPD-8: the binding secret source and the broker (CBR-1..6, BSS-1..5), and the old paths read through bindings (RT-1..7).

Documents are in the format Node's binder writes (``binding_support.NodeFormatBindings``); the real Node
code writing them is ``test_binding_interop.py``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from app.control_plane.runtime.bindings import BINDING_KIND, binding_version_digest
from app.control_plane.testing.contracts import (
    BINDING,
    MATERIAL,
    ORG,
    PURPOSE,
    CredentialBrokerContract,
    pinned_binding,
)
from app.harness.credentials.bindings import (
    BindingCredentialBroker,
    BindingDocuments,
    BindingSecretSource,
)
from app.harness.credentials.service import pipeshub_credential_service
from app.harness.credentials.sources import (
    MCP_KIND,
    TOOLSET_KIND,
    McpSecretSource,
    ToolsetSecretSource,
)
from app.harness.credentials.vault import VaultSecretStore
from app.utils.env_flags import CONTROL_PLANE_CREDENTIALS
from harness_core.contracts.canonical_json import canonical_digest
from harness_core.credentials import (
    CredentialRevoked,
    CredentialScope,
    CredentialUnavailable,
)
from harness_core.credentials.errors import CredentialStoreUnavailable
from harness_core.security.secrets import masking
from harness_core.security.token_patterns import redact_tokens
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.call_secrets import CallSecretsContract
from harness_core.testing.contracts.credentials import SecretSourceContract
from tests.unit.harness.credentials.binding_support import NodeFormatBindings
from tests.unit.harness.credentials.support import DictConfig, owned_toolsets

if TYPE_CHECKING:
    from pydantic import JsonValue

VECTOR = Path(__file__).resolve().parents[6] / "contracts" / "control-plane" / "vectors" / "binding-version-digests.json"
TOKEN = "xoxe.xoxp-1-" + "BindingSourceAccess0123456789abcd"
OLD_TOKEN = "xoxe.xoxp-1-" + "OldPathAccessToken0123456789abcde"


@dataclass
class _Harness:
    clock: ManualClock = field(default_factory=ManualClock)
    config: DictConfig = field(default_factory=DictConfig)

    def __post_init__(self) -> None:
        self.bindings = NodeFormatBindings(self.config, ORG)
        service = pipeshub_credential_service(self.config, clock=self.clock)
        self.broker = BindingCredentialBroker(service, BindingDocuments(VaultSecretStore(self.config, self.clock)))

    def store(self, binding: str, material: JsonValue) -> str:
        return self.bindings.store(binding, material, installation=f"inst-{binding[-1]}")

    def tombstone(self, binding: str) -> None:
        self.bindings.tombstone(binding)


class TestBindingCredentialBrokerContract(CredentialBrokerContract):
    @pytest.fixture
    def broker_harness(self) -> _Harness:
        return _Harness()


@dataclass
class _Bound:
    """``BindingSecretSource`` over documents Node's binder writes: ``own`` is the binder's next version."""

    kind: str = BINDING_KIND
    clock: ManualClock = field(default_factory=ManualClock)
    config: DictConfig = field(default_factory=DictConfig)
    keeps_history: bool = True
    partitions_tenants: bool = True

    @property
    def source(self) -> BindingSecretSource:
        return BindingSecretSource(BindingDocuments(VaultSecretStore(self.config, self.clock)))

    async def own(self, tenant: str, scope: CredentialScope, material: JsonValue) -> None:
        _ = NodeFormatBindings(self.config, tenant, self.clock).store(scope.name, material, installation="inst-1")


class TestBindingSecretSourceContract(SecretSourceContract):
    @pytest.fixture
    def source_harness(self) -> _Bound:
        return _Bound()


class _BrokerSecrets:
    def __init__(self) -> None:
        self.h = _Harness()
        self.secrets = self.h.broker
        self._n = 0

    async def resolve(self, material: JsonValue) -> None:
        self._n += 1
        binding = f"bnd_01J9ZQ5Y8K3M4N5P6Q7R8S{self._n:04d}"
        pinned = self.h.store(binding, material)
        lease = await self.h.broker.lease(pinned_binding(ref=binding, version_digest=pinned), PURPOSE, 300)
        _ = await self.h.broker.resolve(lease, PURPOSE)

    async def close(self) -> None:
        _ = await self.h.broker.close()


class TestBindingCredentialBrokerCallSecretsContract(CallSecretsContract):
    @pytest.fixture
    def call_secrets_harness(self) -> _BrokerSecrets:
        return _BrokerSecrets()


def test_vb3_the_version_digest_is_the_shared_vector() -> None:
    cases = json.loads(VECTOR.read_text(encoding="utf-8"))["cases"]
    assert len(cases) > 1
    for case in cases:
        assert binding_version_digest(case["binding"], case["version"]) == case["digest"]


async def test_bss1_reads_the_current_or_the_asked_version_in_the_tenant_only() -> None:
    h = _Harness()
    _ = h.store(BINDING, "first-secret-value-0001")
    _ = h.store(BINDING, "second-secret-value-0002")
    source = BindingSecretSource(BindingDocuments(VaultSecretStore(h.config, h.clock)))
    scope = CredentialScope(BINDING_KIND, BINDING)
    current = await source.read(ORG, scope)
    assert current is not None and (current.version, current.material) == ("2", "second-secret-value-0002")
    asked = await source.read(ORG, scope, "1")
    assert asked is not None and asked.material == "first-secret-value-0001"
    assert await source.read("org-b", scope) is None
    assert await source.read(ORG, CredentialScope(BINDING_KIND, "bnd_01J9ZQ5Y8K3M4N5P6Q7R8S9999")) is None


async def test_bss4_what_the_broker_resolves_is_masked_exactly_inside_the_call() -> None:
    h = _Harness()
    unshaped = "plain-oauth-client-secret-without-a-shape"
    lease = await h.broker.lease(pinned_binding(version_digest=h.store(BINDING, {"clientSecret": unshaped})), PURPOSE, 300)
    _ = await h.broker.resolve(lease, PURPOSE)
    echoed = f"upstream said: invalid client {unshaped}"
    assert unshaped in redact_tokens(echoed)
    with masking(h.broker):
        assert unshaped not in redact_tokens(echoed)
    assert await h.broker.close() == 1
    with masking(h.broker):
        assert unshaped in redact_tokens(echoed)


class _DownConfig(DictConfig):
    async def get_config(self, key: str, default: Any = None, use_cache: bool = False, **kw: Any) -> Any:  # noqa: ANN401
        if kw.get("raise_on_error"):
            raise ConnectionError("kv down")
        return default


async def test_bss5_a_store_outage_is_unavailable_never_no_secret(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    clock = ManualClock()
    config = _DownConfig()
    broker = BindingCredentialBroker(
        pipeshub_credential_service(config, clock=clock), BindingDocuments(VaultSecretStore(config, clock))
    )
    with pytest.raises(CredentialStoreUnavailable):
        _ = await broker.lease(pinned_binding(version_digest=binding_version_digest(BINDING, 1)), PURPOSE, 300)
    assert "reason=store_unavailable" in caplog.text or "store_unavailable" in caplog.text


# --- RT: the old paths read through bindings ---------------------------------------------------------

TENANT = "org-1"
OLD = "/services/toolsets/inst-1/user-1"


def _old_record(token: str = OLD_TOKEN) -> dict[str, Any]:
    return {"isAuthenticated": True, "credentials": {"access_token": token}}


def _world(old: dict[str, Any] | None) -> tuple[DictConfig, NodeFormatBindings, ToolsetSecretSource]:
    config = DictConfig(owned_toolsets({OLD: old} if old is not None else {}, TENANT))
    if old is None:
        config.values["/services/toolset-instances"] = [{"_id": "inst-1", "orgId": TENANT, "toolsetType": "slack"}]
    bindings = NodeFormatBindings(config, TENANT)
    source = ToolsetSecretSource(config, BindingDocuments(VaultSecretStore(config, ManualClock())))
    return config, bindings, source


def _import(bindings: NodeFormatBindings, record: dict[str, Any], digest_of: object) -> None:
    _ = bindings.store(
        BINDING,
        record,  # type: ignore[arg-type]
        installation="inst-1",
        owner="user:user-1",
        source={"path": OLD, "digest": canonical_digest(digest_of)},
    )


SCOPE = CredentialScope(TOOLSET_KIND, "inst-1/user-1")


async def test_rt1_switch_off_reads_only_the_old_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONTROL_PLANE_CREDENTIALS, raising=False)
    config, bindings, source = _world(_old_record())
    _import(bindings, _old_record(TOKEN), _old_record())
    reads: list[str] = []
    original = config.get_config

    async def watched(key: str, *args: Any, **kw: Any) -> Any:  # noqa: ANN401
        reads.append(key)
        return await original(key, *args, **kw)

    config.get_config = watched  # type: ignore[method-assign]
    found = await source.read(TENANT, SCOPE)
    assert found is not None and found.material == _old_record()
    assert not [key for key in reads if key.startswith("/services/controlPlane/")]


async def test_rt2_on_serves_the_binding_refreshed_since_the_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    _, bindings, source = _world(_old_record())
    _import(bindings, _old_record(), _old_record())
    _ = bindings.store(BINDING, _old_record(TOKEN))  # type: ignore[arg-type]
    found = await source.read(TENANT, SCOPE)
    assert found is not None and found.material == _old_record(TOKEN)


async def test_rt3_on_an_old_record_changed_after_the_copy_wins(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    caplog.set_level(logging.WARNING)
    _, bindings, source = _world(_old_record("changed-after-the-copy-0123456789"))
    _import(bindings, _old_record(), _old_record())
    found = await source.read(TENANT, SCOPE)
    assert found is not None and found.material == _old_record("changed-after-the-copy-0123456789")
    assert "credential.binding_stale" in caplog.text


async def test_rt4_on_a_tombstone_is_final(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    _, bindings, source = _world(_old_record())
    _import(bindings, _old_record(), _old_record())
    bindings.tombstone(BINDING)
    with pytest.raises(CredentialRevoked):
        _ = await source.read(TENANT, SCOPE)


async def test_rt5_on_without_a_binding_reads_the_old_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    _, _, source = _world(_old_record())
    found = await source.read(TENANT, SCOPE)
    assert found is not None and found.material == _old_record()
    _, bindings, gone = _world(None)
    _import(bindings, _old_record(TOKEN), _old_record())
    kept = await gone.read(TENANT, SCOPE)
    assert kept is not None and kept.material == _old_record(TOKEN)


def _mcp_world(instance: dict[str, Any]) -> tuple[DictConfig, NodeFormatBindings, McpSecretSource]:
    config = DictConfig({f"/services/mcp/instances/{instance['_id']}": instance})
    bindings = NodeFormatBindings(config, TENANT)
    return config, bindings, McpSecretSource(config, BindingDocuments(VaultSecretStore(config, ManualClock())))


async def test_rt6_mcp_admin_auth_reads_the_creators_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    instance = {"_id": "mcp-1", "orgId": TENANT, "authMode": "api_token", "createdBy": "admin-1", "useAdminAuth": True}
    config, bindings, source = _mcp_world(instance)
    old = {"isAuthenticated": True, "apiToken": OLD_TOKEN}
    config.values["/services/mcp/credentials/mcp-1/admin-1"] = old
    _ = bindings.store(
        BINDING,
        {"isAuthenticated": True, "apiToken": TOKEN},
        installation="mcp-1",
        owner="user:admin-1",
        source={"path": "/services/mcp/credentials/mcp-1/admin-1", "digest": canonical_digest(old)},
    )
    found = await source.read(TENANT, CredentialScope(MCP_KIND, "mcp-1/user-1"))
    assert found is not None and found.material == {"isAuthenticated": True, "apiToken": TOKEN}


async def test_rt7_another_orgs_instance_resolves_nothing_on_either_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    instance = {"_id": "mcp-1", "orgId": "org-2", "authMode": "api_token", "createdBy": "admin-1"}
    config, bindings, source = _mcp_world(instance)
    config.values["/services/mcp/credentials/mcp-1/user-1"] = {"isAuthenticated": True, "apiToken": OLD_TOKEN}
    _ = bindings.store(BINDING, {"isAuthenticated": True, "apiToken": TOKEN}, installation="mcp-1", owner="user:user-1")
    assert await source.read(TENANT, CredentialScope(MCP_KIND, "mcp-1/user-1")) is None
    with pytest.raises(CredentialUnavailable):
        _ = await _Harness().broker.lease(pinned_binding(version_digest=binding_version_digest(BINDING, 1)), PURPOSE, 300)
    assert MATERIAL
