"""SEC4: ``ToolCredentials`` (an attempt's connections) and ``AgentContext`` hold what the attempt resolved."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.harness.credentials.service import pipeshub_credential_service
from app.harness.credentials.tool_credentials import (
    ToolCredentials,
    mcp_reference,
    toolset_reference,
)
from harness_core.security.token_patterns import SECRET_MASK
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.call_secrets import CallSecretsContract
from harness_core.testing.ids import SeededIds
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.harness.credentials.support import DictConfig, owned_toolsets

if TYPE_CHECKING:
    from pydantic import JsonValue


class _Connections:
    def __init__(self) -> None:
        self._records: dict[str, Any] = {}
        self._config = DictConfig()
        self.secrets = ToolCredentials(
            pipeshub_credential_service(self._config, clock=ManualClock(), ids=SeededIds()),
            tenant="org-1",
            run_id="run-1",
            principal="user-1",
        )

    async def resolve(self, material: JsonValue) -> None:
        instance = f"inst-{len(self._records) + 1}"
        self._records[f"/services/toolsets/{instance}/user-1"] = {**dict(material), "isAuthenticated": True}  # type: ignore[arg-type]
        self._config.values = owned_toolsets(self._records)
        _ = await self.secrets.toolset_config(toolset_reference(instance, "user-1"))

    async def close(self) -> None:
        _ = await self.secrets.close()


class TestToolCredentialsCallSecretsContract(CallSecretsContract):
    @pytest.fixture
    def call_secrets_harness(self) -> _Connections:
        return _Connections()


async def test_sec4_an_mcp_instance_in_headers_mode_masks_its_header_value() -> None:
    header = "org-header-value-71c2e9"
    config = DictConfig(
        {"/services/mcp/credentials/mcp-1/user-1": {"isAuthenticated": True, "credentials": {"headerName": "X-Org", "headerValue": header}}}
    )
    instance = {"_id": "mcp-1", "orgId": "org-1", "authMode": "headers", "createdBy": "user-1"}
    connections = ToolCredentials(
        pipeshub_credential_service(config, clock=ManualClock(), ids=SeededIds()), tenant="org-1", run_id="run-1", principal="user-1"
    )
    with patch("app.edition_config.get_mcp_instance_resolved", AsyncMock(return_value=instance)):
        _ = await connections.mcp_auth(mcp_reference("mcp-1", "user-1"))

    assert connections.resolved_secrets().mask(f"403 for {header}") == f"403 for {SECRET_MASK}"


async def test_sec4_a_context_hands_on_what_its_connections_resolved() -> None:
    config = DictConfig(owned_toolsets({"/services/toolsets/inst-1/user-1": {"isAuthenticated": True, "credentials": {"client_secret": "ctx-client-secret-5e"}}}))
    context = make_context(config_service=config, run_id="run-1")
    assert not context.resolved_secrets()

    _ = await context.tool_credentials().toolset_config(toolset_reference("inst-1", "user-1"))
    assert context.resolved_secrets().mask("ctx-client-secret-5e") == SECRET_MASK

    _ = await context.close_credentials()
    assert not context.resolved_secrets()
