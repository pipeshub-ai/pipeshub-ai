"""With CONTROL_PLANE_CREDENTIALS on, a PUT that changes an MCP server's connection target must leave no sign-in
of the old target usable.

With the switch on, a credential is its owner's binding, held by Node's binder (CPD-8); the old path holds
nothing, or a migrated copy. The purge of a target change must therefore end every binding made on the
instance too, or the read-through (``read_through.BindingReadThrough.read``) keeps serving it for the new
target. A revoke that cannot be confirmed must refuse the change (503 + Retry-After), as a kept old-path
record does (API1-S4).

Driven through the real router with ``FakeBindingWriter`` (Node's binder semantics) behind the legacy seam.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.agents.mcp import lifecycle as mcp_lifecycle
from app.agents.mcp import service as mcp_service
from app.agents.mcp.discovery import build_auth_env_and_headers
from app.api.routes import mcp_servers as mcp_routes
from app.harness.credentials import legacy_records as legacy_records_module
from app.harness.credentials.binding_writer import (
    BindingConflict,
    BindingRow,
    BindingWriteFailed,
    NodeBindingWriter,
)
from app.harness.credentials.legacy_records import LegacyCredentialRecords
from app.harness.journal_node.http import NodeServiceClient
from app.utils.env_flags import CONTROL_PLANE_CREDENTIALS
from tests.unit.api.routes.test_mcp_purge_confirmed import (
    ADMIN,
    INST,
    INSTANCE_PATH,
    NEW_URL,
    OLD_TOKEN,
    OLD_URL,
    ORG,
    STATE,
    USER,
    USER_CRED,
    FlakyConfig,
    _client,
    _instance,
    _put_body,
)
from tests.unit.harness.credentials.binding_support import FakeBindingWriter

AGENT = "agent-key-7"


def _record(owner: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {
        "isAuthenticated": True, "userId": owner, "orgId": ORG,
        "oauthTokens": {"accessToken": OLD_TOKEN, "refreshToken": "old-refresh", "tokenUrl": "https://auth.old.example.com/token"},
        **extra,
    }


class _BinderDown(FakeBindingWriter):
    """Node's binder answers nothing for a revoke: unreachable, or an error status."""

    def __init__(self, config: Any, failure: Exception) -> None:  # noqa: ANN401
        super().__init__(config)
        self.failure = failure

    async def revoke_owner(self, *_: object, **__: object) -> BindingRow | None:
        raise self.failure

    async def revoke_installation(self, *_: object, **__: object) -> list[BindingRow]:
        raise self.failure


def _use_seam(monkeypatch: pytest.MonkeyPatch, seam: LegacyCredentialRecords) -> None:
    monkeypatch.setattr(legacy_records_module, "legacy_records", lambda _config: seam)
    monkeypatch.setattr(mcp_routes, "legacy_records", lambda _config: seam)


async def _bound_world(
    monkeypatch: pytest.MonkeyPatch, *, keep_old_record: bool = False, writer_failure: Exception | None = None,
) -> tuple[FlakyConfig, FakeBindingWriter, Any]:
    """An OAuth server at OLD_URL whose USER and agent AGENT signed in with the switch on (bindings).
    ``keep_old_record``: USER's old-path record is still there, as the migration left it."""
    config = FlakyConfig("healthy")
    config.values[INSTANCE_PATH] = _instance()
    client = _client(config, monkeypatch)  # clears the switch; it is set below
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    writer = FakeBindingWriter(config) if writer_failure is None else _BinderDown(config, writer_failure)  # type: ignore[arg-type]

    async def node() -> FakeBindingWriter:
        return writer

    seam = LegacyCredentialRecords(config, node)  # type: ignore[arg-type]
    _use_seam(monkeypatch, seam)
    if keep_old_record:
        config.values[USER_CRED] = _record(USER)
    assert await seam.save("mcp", ORG, INST, USER, _record(USER), user_id=USER)
    assert await seam.save("mcp", ORG, INST, AGENT, _record(AGENT, agentKey=AGENT))
    assert writer.find(ORG, INST, f"user:{USER}") is not None and writer.find(ORG, INST, f"agent:{AGENT}") is not None
    return config, writer, client


async def _sent_for(config: FlakyConfig, owner: str) -> tuple[str | None, str | None]:
    """The URL the MCP client connects to for ``owner`` and the Authorization header it sends there (the chat path)."""
    instance = await config.get_config(INSTANCE_PATH)
    auth = await mcp_service.resolve_effective_user_auth(instance, owner, config)  # type: ignore[arg-type]
    server = mcp_service.instance_config_from_dict(instance)
    credentials = mcp_service.credentials_to_discovery_dict(instance["authMode"], auth or {})
    _, headers = build_auth_env_and_headers(server, credentials)
    return server.url, headers.get("Authorization")


@pytest.mark.parametrize("keep_old_record", [False, True], ids=["bound_only", "migrated_copy_kept"])
async def test_a_url_change_with_bindings_on_leaves_no_old_sign_in(
    keep_old_record: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, writer, client = await _bound_world(monkeypatch, keep_old_record=keep_old_record)
    assert await _sent_for(config, USER) == (OLD_URL, f"Bearer {OLD_TOKEN}")  # signed in before the change

    resp = client.put(f"/api/v1/mcp-servers/instances/{INST}", json=_put_body())

    assert resp.status_code == 200, resp.text
    assert resp.json()["credentialsReset"] is True
    sent = {owner: await _sent_for(config, owner) for owner in (USER, AGENT)}
    # Never: the new URL paired with a token given for the old one.
    assert sent == {USER: (NEW_URL, None), AGENT: (NEW_URL, None)}, sent
    rows = {owner: writer.find(ORG, INST, owner) for owner in (f"user:{USER}", f"agent:{AGENT}")}
    assert {owner: (row or {}).get("status") for owner, row in rows.items()} == {
        f"user:{USER}": "revoked", f"agent:{AGENT}": "revoked",
    }


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("node unreachable"), BindingWriteFailed("POST revoke: 503", 503)],
    ids=["binder_unreachable", "binder_answers_503"],
)
async def test_a_url_change_is_refused_while_the_old_sign_ins_cannot_be_ended(
    failure: Exception, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _, client = await _bound_world(monkeypatch, writer_failure=failure)

    resp = client.put(f"/api/v1/mcp-servers/instances/{INST}", json=_put_body())

    url, authorization = await _sent_for(config, USER)
    evidence = (resp.status_code, resp.json(), url, authorization)
    assert not (url == NEW_URL and authorization == f"Bearer {OLD_TOKEN}"), evidence
    assert resp.status_code == 503, evidence
    assert resp.headers.get("Retry-After"), evidence
    assert url == OLD_URL, evidence


async def test_a_url_change_with_bindings_off_never_calls_the_binder(monkeypatch: pytest.MonkeyPatch) -> None:
    config, _, client = await _bound_world(monkeypatch, writer_failure=AssertionError("binder called"))
    monkeypatch.delenv(CONTROL_PLANE_CREDENTIALS)

    resp = client.put(f"/api/v1/mcp-servers/instances/{INST}", json=_put_body())

    assert resp.status_code == 200, resp.text
    assert (await config.get_config(INSTANCE_PATH))["url"] == NEW_URL


async def test_a_rename_with_bindings_on_keeps_every_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    config, writer, client = await _bound_world(monkeypatch)

    resp = client.put(f"/api/v1/mcp-servers/instances/{INST}", json=_put_body(url=OLD_URL, name="Search (renamed)"))

    assert resp.status_code == 200, resp.text
    assert resp.json()["credentialsReset"] is False
    assert await _sent_for(config, USER) == (OLD_URL, f"Bearer {OLD_TOKEN}")
    row = writer.find(ORG, INST, f"user:{USER}")
    assert row is not None and row["status"] == "active"


def _statuses(writer: FakeBindingWriter) -> dict[str, str | None]:
    return {owner: (writer.find(ORG, INST, owner) or {}).get("status") for owner in (f"user:{USER}", f"agent:{AGENT}")}


async def test_a_node_503_on_the_wire_refuses_the_change_and_names_the_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    config, _, client = await _bound_world(monkeypatch)
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503, json={"error": {"code": "SERVICE_UNAVAILABLE"}})

    node = NodeServiceClient(
        base_url="http://node.test", timeout_s=5,
        http_client=httpx.AsyncClient(base_url="http://node.test", transport=httpx.MockTransport(handle)),
    )

    async def token(org: str | None, user: str | None) -> str:
        return f"tok-{org}-{user}"

    async def factory() -> NodeBindingWriter:
        return NodeBindingWriter(node, token)

    _use_seam(monkeypatch, LegacyCredentialRecords(config, factory))  # type: ignore[arg-type]

    resp = client.put(f"/api/v1/mcp-servers/instances/{INST}", json=_put_body())

    assert resp.status_code == 503 and resp.headers.get("Retry-After"), resp.text
    assert [r.url.path for r in seen] == ["/api/v1/control-plane/internal/bindings/revoke-installation"]
    assert seen[0].headers["authorization"] == f"Bearer tok-{ORG}-{ADMIN}"
    assert (await config.get_config(INSTANCE_PATH))["url"] == OLD_URL


async def test_a_binding_changed_during_the_revoke_is_a_500_and_keeps_the_old_url(monkeypatch: pytest.MonkeyPatch) -> None:
    config, _, client = await _bound_world(monkeypatch, writer_failure=BindingConflict("/revoke-installation"))

    resp = client.put(f"/api/v1/mcp-servers/instances/{INST}", json=_put_body())

    assert resp.status_code == 500 and "Retry-After" not in resp.headers, resp.text
    assert (await config.get_config(INSTANCE_PATH))["url"] == OLD_URL


@pytest.mark.parametrize(
    ("revoke_failure", "listing_failure"),
    [(BindingWriteFailed("POST /revoke-installation: 400"), ConnectionError("store unreachable")),
     (httpx.ConnectError("node unreachable"), ValueError("bug"))],
    ids=["revoke_bug_store_outage", "binder_outage_store_bug"],
)
async def test_a_bug_beside_an_outage_is_a_500(
    revoke_failure: Exception, listing_failure: Exception, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _, client = await _bound_world(monkeypatch, writer_failure=revoke_failure)
    listing = config.list_keys_in_directory

    async def list_keys(directory: str) -> list[str]:
        if directory.startswith("/services/mcp/credentials/"):
            raise listing_failure
        return await listing(directory)

    monkeypatch.setattr(config, "list_keys_in_directory", list_keys)

    resp = client.put(f"/api/v1/mcp-servers/instances/{INST}", json=_put_body())

    assert resp.status_code == 500 and "Retry-After" not in resp.headers, resp.text
    assert (await config.get_config(INSTANCE_PATH))["url"] == OLD_URL


async def test_the_old_path_copy_is_removed_even_when_the_binder_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    config, _, _ = await _bound_world(monkeypatch, keep_old_record=True, writer_failure=httpx.ConnectError("down"))

    with pytest.raises(httpx.ConnectError):
        await mcp_lifecycle.purge_instance_credentials(config, _instance())  # type: ignore[arg-type]
    assert USER_CRED not in config.values


async def test_the_bindings_are_revoked_even_when_a_store_delete_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    config, writer, _ = await _bound_world(monkeypatch)
    config.mode = "delete_raises"

    with pytest.raises(ConnectionError):
        await mcp_lifecycle.purge_instance_credentials(config, _instance())  # type: ignore[arg-type]
    assert _statuses(writer) == {f"user:{USER}": "revoked", f"agent:{AGENT}": "revoked"}


async def test_deleting_a_server_revokes_its_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    config, writer, _ = await _bound_world(monkeypatch)

    _ = await mcp_lifecycle.delete_instance_data(config, _instance())  # type: ignore[arg-type]

    assert _statuses(writer) == {f"user:{USER}": "revoked", f"agent:{AGENT}": "revoked"}


async def test_deleting_a_server_with_the_binder_down_still_removes_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    config, _, _ = await _bound_world(monkeypatch, keep_old_record=True, writer_failure=httpx.ConnectError("down"))

    removed = await mcp_lifecycle.delete_instance_data(config, _instance())  # type: ignore[arg-type]

    assert USER_CRED not in config.values and INSTANCE_PATH in removed


@pytest.mark.parametrize("step", ["sign_in_states_listed", "sign_in_state_deleted"])
async def test_a_sign_in_saved_during_the_purge_is_not_used_at_the_new_url(
    step: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _, client = await _bound_world(monkeypatch)
    config.values[STATE] = {"instanceId": INST}
    seam = legacy_records_module.legacy_records(config)
    listing, deleting = config.list_keys_in_directory, config.delete_config
    landed: list[str] = []

    async def save_once(key: str) -> None:
        if not landed:
            landed.append(key)
            assert await seam.save("mcp", ORG, INST, USER, _record(USER), user_id=USER)

    async def list_keys(directory: str) -> list[str]:
        if step == "sign_in_states_listed" and directory.startswith("/services/mcp/oauth-states"):
            await save_once(directory)
        return await listing(directory)

    async def delete(key: str) -> bool:
        if step == "sign_in_state_deleted" and key == STATE:
            await save_once(key)
        return await deleting(key)

    monkeypatch.setattr(config, "list_keys_in_directory", list_keys)
    monkeypatch.setattr(config, "delete_config", delete)

    resp = client.put(f"/api/v1/mcp-servers/instances/{INST}", json=_put_body())

    assert resp.status_code == 200 and landed, resp.text
    assert await _sent_for(config, USER) == (NEW_URL, None)


async def test_signing_in_again_after_the_change_is_used_at_the_new_url(monkeypatch: pytest.MonkeyPatch) -> None:
    config, _, client = await _bound_world(monkeypatch)
    assert client.put(f"/api/v1/mcp-servers/instances/{INST}", json=_put_body()).status_code == 200
    fresh = _record(USER, oauthTokens={"accessToken": "new-target-token", "refreshToken": "r", "tokenUrl": "https://auth.new.example.com/token"})

    assert await legacy_records_module.legacy_records(config).save("mcp", ORG, INST, USER, fresh, user_id=USER)

    assert await _sent_for(config, USER) == (NEW_URL, "Bearer new-target-token")
