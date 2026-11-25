"""SYNC3: main's shared admin MCP credential slot (`_shared`, #4003) under CPD-8 bindings.

A shared credential migrated before main's slot is bound under its creator. With CONTROL_PLANE_CREDENTIALS
on, the move to `_shared` carries the binding's current material (not the frozen old record), legacy and
durable readers both resolve it, and an admin's disconnect leaves nothing for either to find.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.agents.mcp import service as mcp_service
from app.harness.credentials import legacy_records as legacy_records_module
from app.harness.credentials.bindings import BindingDocuments
from app.harness.credentials.legacy_records import LegacyCredentialRecords
from app.harness.credentials.sources import MCP_KIND, McpSecretSource
from app.harness.credentials.vault import VaultSecretStore
from app.utils.env_flags import CONTROL_PLANE_CREDENTIALS
from harness_core.credentials import CredentialRevoked, CredentialScope
from harness_core.testing.clock import ManualClock
from tests.unit.harness.credentials.binding_support import FakeBindingWriter
from tests.unit.harness.credentials.support import DictConfig

ORG, CREATOR, CALLER = "org-1", "admin-1", "user-9"
CREATOR_PATH = "/services/mcp/credentials/mcp-1/admin-1"
SHARED_PATH = "/services/mcp/credentials/mcp-1/_shared"
FROZEN = {"isAuthenticated": True, "apiToken": "frozen-old-record-token-0123456789"}
CURRENT = {"isAuthenticated": True, "apiToken": "rotated-binding-token-0123456789ab"}
INSTANCE = {"_id": "mcp-1", "orgId": ORG, "authMode": "api_token", "useAdminAuth": True, "createdBy": CREATOR}


def world(monkeypatch: pytest.MonkeyPatch) -> tuple[DictConfig, FakeBindingWriter, LegacyCredentialRecords]:
    config = DictConfig({CREATOR_PATH: dict(FROZEN)})
    writer = FakeBindingWriter(config)

    async def node() -> FakeBindingWriter:
        return writer

    seam = LegacyCredentialRecords(config, node)  # type: ignore[arg-type]
    monkeypatch.setattr(legacy_records_module, "legacy_records", lambda _config: seam)
    return config, writer, seam


async def durable(config: DictConfig) -> Any:  # noqa: ANN401
    async def resolved(*_: object) -> dict[str, Any]:
        return dict(INSTANCE)

    source = McpSecretSource(config, BindingDocuments(VaultSecretStore(config, ManualClock())))
    with patch("app.edition_config.get_mcp_instance_resolved", new=resolved):
        try:
            found = await source.read(ORG, CredentialScope(MCP_KIND, f"mcp-1/{CALLER}"))
        except CredentialRevoked:
            return None
    return found.material if found is not None else None


async def migrated_creator_binding(monkeypatch: pytest.MonkeyPatch) -> tuple[DictConfig, FakeBindingWriter, LegacyCredentialRecords]:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    config, writer, seam = world(monkeypatch)
    # CPD-8's migration bound the creator's record; a later rotation went to the binding only.
    assert await seam.save("mcp", ORG, "mcp-1", CREATOR, dict(CURRENT), user_id=CREATOR)
    assert config.values[CREATOR_PATH] == FROZEN
    return config, writer, seam


async def test_sync3_s1_on_legacy_and_durable_resolve_the_bindings_current_material(monkeypatch: pytest.MonkeyPatch) -> None:
    config, writer, _ = await migrated_creator_binding(monkeypatch)

    assert await mcp_service.resolve_effective_user_auth(dict(INSTANCE), CALLER, config) == CURRENT
    shared, creator = writer.find(ORG, "mcp-1", "user:_shared"), writer.find(ORG, "mcp-1", f"user:{CREATOR}")
    assert shared is not None and shared["status"] == "active"
    assert creator is not None and creator["status"] == "revoked"
    assert CREATOR_PATH not in config.values
    assert await durable(config) == CURRENT
    # Moved once: a second read finds the slot and adopts nothing.
    assert await mcp_service.adopt_legacy_shared_credential(dict(INSTANCE), config) is None
    assert await mcp_service.resolve_effective_user_auth(dict(INSTANCE), CALLER, config) == CURRENT


async def test_sync3_s1_on_the_durable_read_moves_it_first_too(monkeypatch: pytest.MonkeyPatch) -> None:
    config, writer, _ = await migrated_creator_binding(monkeypatch)
    assert await durable(config) == CURRENT
    assert writer.find(ORG, "mcp-1", "user:_shared") is not None
    assert await mcp_service.resolve_effective_user_auth(dict(INSTANCE), CALLER, config) == CURRENT


@pytest.mark.parametrize("read_first", [True, False])
async def test_sync3_s2_on_an_admin_disconnect_leaves_nothing_to_resolve(
    monkeypatch: pytest.MonkeyPatch, read_first: bool,
) -> None:
    config, _, seam = await migrated_creator_binding(monkeypatch)
    if read_first:
        _ = await mcp_service.resolve_effective_user_auth(dict(INSTANCE), CALLER, config)
    # The disconnect route: adopt into the slot (`_shared_aware_credential_path`), then clear the slot.
    _ = await mcp_service.adopt_legacy_shared_credential(dict(INSTANCE), config)
    await seam.remove("mcp", ORG, "mcp-1", mcp_service.SHARED_CREDENTIAL_OWNER, user_id=CREATOR)

    assert await mcp_service.resolve_effective_user_auth(dict(INSTANCE), CALLER, config) is None
    assert await durable(config) is None
    assert CREATOR_PATH not in config.values and SHARED_PATH not in config.values


async def test_sync3_s3_off_is_main_the_old_record_moves_to_the_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONTROL_PLANE_CREDENTIALS, raising=False)
    config, writer, _ = world(monkeypatch)
    assert await mcp_service.resolve_effective_user_auth(dict(INSTANCE), CALLER, config) == FROZEN
    assert config.values[SHARED_PATH] == FROZEN and CREATOR_PATH not in config.values
    assert writer.calls == []
    assert await durable(config) == FROZEN
