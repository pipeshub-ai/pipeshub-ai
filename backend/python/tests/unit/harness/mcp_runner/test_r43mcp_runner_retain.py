"""R36COVER-FU-4 and R43MCP-FU-2, the process side: which runners a pool keeps, and the pool's name across a
restart."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.harness.mcp_runner import process
from harness_core.sandbox.mcp_runner.types import RunnerKey, RunnerSecrets

KEY = RunnerKey("org-1", "srv-1", "user-1")
STDIO = {
    "_id": "srv-1", "orgId": "org-1", "name": "github", "createdBy": "admin-1", "transport": "stdio", "typeId": "github",
    "authMode": "api_token",
}
SIGNED_IN = {"isAuthenticated": True, "credentials": {"apiToken": "tok-1"}}


class _Store:
    """The config service as the sweep sees it; ``down`` fails reads as an encrypted store does: the default unless
    the caller asks it to raise."""

    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values
        self.down = False

    async def get_config(
        self, key: str, default: Any = None, use_cache: bool = False, *, raise_on_error: bool = False  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        if self.down:
            if raise_on_error:
                raise ConnectionError("store unreachable")
            return default
        return self.values.get(key, default)


async def _kept(instance: dict[str, Any] | None, *, custom_allowed: bool = True) -> bool:
    async def resolved(instance_id: str, config: object, org: str, user: str | None = None) -> dict[str, Any] | None:
        assert (instance_id, org, user) == ("srv-1", "org-1", "user-1")
        return instance

    store = _Store({"/services/mcp/credentials/srv-1/user-1": SIGNED_IN})
    with patch("app.edition_config.get_mcp_instance_resolved", resolved), \
         patch.object(process, "_config_service", lambda: store), \
         patch("app.agents.mcp.stdio_policy.custom_stdio_allowed", lambda: custom_allowed):
        return await process.server_still_runs(KEY)


@pytest.mark.asyncio
async def test_rr1_a_stdio_instance_of_the_org_keeps_its_runners() -> None:
    assert isinstance(await _kept(STDIO), RunnerSecrets)


@pytest.mark.asyncio
async def test_rr1_a_deleted_instance_a_changed_transport_or_a_disabled_custom_server_does_not() -> None:
    assert await _kept(None) is False
    assert await _kept({**STDIO, "transport": "streamable_http"}) is False
    assert await _kept({**STDIO, "typeId": None}, custom_allowed=False) is False


CUSTOM = {
    "_id": "srv-1", "orgId": "org-1", "name": "custom", "createdBy": "admin-1", "transport": "stdio",
    "authMode": "api_token",
    "command": "npx", "args": ["-y", "some-server"], "requiredEnv": ["SOME_TOKEN"],
}
INSTANCE_PATH = "/services/mcp/instances/srv-1"
OWN_PATH = "/services/mcp/credentials/srv-1/user-1"
SHARED_PATH = "/services/mcp/credentials/srv-1/_shared"


async def _standing(store: _Store) -> bool | RunnerSecrets:
    with patch.object(process, "_config_service", lambda: store), \
         patch("app.agents.mcp.stdio_policy.custom_stdio_allowed", lambda: True):
        return await process.server_still_runs(KEY)


def _secrets(token: str) -> dict[str, Any]:
    return {"isAuthenticated": True, "credentials": {"apiToken": token}}


@pytest.mark.asyncio
async def test_rr3_a_signed_in_owner_keeps_the_runner_with_the_secrets_it_would_start_with() -> None:
    standing = await _standing(_Store({INSTANCE_PATH: CUSTOM, OWN_PATH: _secrets("tok-1")}))
    assert isinstance(standing, RunnerSecrets) and standing.values() == {"SOME_TOKEN": "tok-1"}


@pytest.mark.asyncio
async def test_rr3_a_removed_or_disconnected_sign_in_does_not() -> None:
    assert await _standing(_Store({INSTANCE_PATH: CUSTOM})) is False
    assert await _standing(_Store({INSTANCE_PATH: CUSTOM, OWN_PATH: {"isAuthenticated": False}})) is False


@pytest.mark.asyncio
async def test_rr3_a_changed_secret_answers_the_new_secrets() -> None:
    store = _Store({INSTANCE_PATH: CUSTOM, OWN_PATH: _secrets("tok-1")})
    before = await _standing(store)
    store.values[OWN_PATH] = _secrets("tok-2")
    after = await _standing(store)
    assert isinstance(before, RunnerSecrets) and isinstance(after, RunnerSecrets)
    assert before.values() != after.values()


@pytest.mark.asyncio
async def test_rr3_a_shared_admin_credential_is_read_from_its_slot_and_its_removal_stops_the_runner() -> None:
    shared = {**CUSTOM, "useAdminAuth": True, "sharedCredentialSlot": True}
    standing = await _standing(_Store({INSTANCE_PATH: shared, SHARED_PATH: _secrets("admin-tok")}))
    assert isinstance(standing, RunnerSecrets) and standing.values() == {"SOME_TOKEN": "admin-tok"}
    assert await _standing(_Store({INSTANCE_PATH: shared, OWN_PATH: _secrets("tok-1")})) is False


@pytest.mark.asyncio
async def test_rr3_a_legacy_shared_credential_is_read_where_it_is_and_never_moved_by_the_sweep() -> None:
    legacy = {**CUSTOM, "useAdminAuth": True}
    store = _Store({INSTANCE_PATH: legacy, "/services/mcp/credentials/srv-1/admin-1": _secrets("old-admin")})
    with patch("app.agents.mcp.service.adopt_legacy_shared_credential") as adopt:
        standing = await _standing(store)
    adopt.assert_not_called()
    assert isinstance(standing, RunnerSecrets) and standing.values() == {"SOME_TOKEN": "old-admin"}


@pytest.mark.asyncio
async def test_rr3_a_store_that_cannot_be_read_raises_so_the_sweep_keeps_the_runner() -> None:
    store = _Store({INSTANCE_PATH: CUSTOM, OWN_PATH: _secrets("tok-1")})
    store.down = True
    with pytest.raises(ConnectionError):
        _ = await _standing(store)


def test_rr2_the_pool_name_is_the_host_and_the_service_not_the_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process.socket, "gethostname", lambda: "pipeshub-ai")
    name = process.pool_name()
    assert name.startswith("pipeshub-ai:") and str(process.os.getpid()) not in name.split(":", 1)[1]
    assert len(name) <= 63
