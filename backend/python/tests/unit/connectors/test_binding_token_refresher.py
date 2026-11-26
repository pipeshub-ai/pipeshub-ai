"""CPD-8 TR-1..TR-9 and OF-1..OF-3: one TokenRefresher over bindings, and the OAuth flows end to end.

The providers are faked at the wire: Slack's ``oauth.v2.access`` (``tests/support/slack_oauth.FakeSlackOAuth``, inside
the real ``OAuthProvider``) for toolsets, and an RFC 6749 token endpoint behind ``httpx`` for MCP (inside the real
``oauth_client``). Bindings are written with Node's binder semantics (``FakeBindingWriter``) and read through the real
vault; a run's lease goes through the real ``BindingCredentialBroker``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

import httpx
import pytest

import app.edition_config  # noqa: F401  (loaded first in every service; importing the toolset routes cold is a cycle, CPD-8 FB-3)
from app.agents.mcp import oauth_client
from app.connectors.core.base.token_service import oauth_service
from app.connectors.core.base.token_service.token_refresher import (
    McpOAuthStrategy,
    RefreshRefused,
    Renewed,
    TokenRefresher,
    ToolsetOAuthStrategy,
)
from app.control_plane.runtime.bindings import binding_version_digest
from app.control_plane.testing.contracts import pinned_binding
from app.harness.credentials.binding_writer import ImportRequest, Material
from app.harness.credentials.bindings import BindingCredentialBroker, BindingDocuments
from app.harness.credentials.service import pipeshub_credential_service
from app.harness.credentials.vault import VaultSecretStore
from harness_core.credentials import CredentialPurpose, CredentialRevoked
from harness_core.testing.clock import ManualClock
from tests.support.slack_oauth import TOKEN_URL as SLACK_TOKEN_URL
from tests.support.slack_oauth import FakeSlackOAuth
from tests.unit.harness.credentials.binding_support import FakeBindingWriter
from tests.unit.harness.credentials.support import DictConfig

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "org-1"
MCP_TOKEN_URL = "https://mcp.example.test/oauth/token"


def slack_record(refresh_token: str = "xoxe-1-old") -> dict[str, Any]:
    return {
        "isAuthenticated": True,
        "authType": "OAUTH",
        "toolsetType": "slack",
        "instanceId": "ts-1",
        "orgId": ORG,
        "oauthConfigId": "oc-1",
        "credentials": {
            "access_token": "xoxe.xoxp-1-old",
            "refresh_token": refresh_token,
            "expires_in": 43200,
            "created_at": "2026-01-01T00:00:00",
        },
    }


def mcp_record(refresh_token: str = "mcp-refresh-1") -> dict[str, Any]:
    return {
        "isAuthenticated": True,
        "authMode": "oauth",
        "oauthTokens": {
            "accessToken": "mcp-access-1",
            "tokenType": "Bearer",
            "refreshToken": refresh_token,
            "expiresIn": 3600,
            "tokenUrl": MCP_TOKEN_URL,
        },
    }


@dataclass
class FakeMcpTokenEndpoint:
    """An RFC 6749 token endpoint that rotates refresh tokens: each works once, then ``invalid_grant`` (400)."""

    live: set[str] = field(default_factory=lambda: {"mcp-refresh-1", "mcp-refresh-b"})
    client_id: str = "dcr-client-1"
    requests: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    issued: int = 0
    fail_next: int = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == MCP_TOKEN_URL
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.requests.append(form)
        if self.fail_next:
            self.fail_next -= 1
            return httpx.Response(503, text="upstream down")
        if form.get("client_id") != self.client_id:
            return httpx.Response(401, json={"error": "invalid_client"})
        if form.get("grant_type") != "refresh_token" or form.get("refresh_token") not in self.live:
            return httpx.Response(400, json={"error": "invalid_grant"})
        self.live.discard(form["refresh_token"])
        self.issued += 1
        rotated = f"mcp-refresh-{self.issued + 1}"
        self.live.add(rotated)
        return httpx.Response(
            200,
            json={"access_token": f"mcp-access-{self.issued + 1}", "refresh_token": rotated, "expires_in": 3600},
        )


@pytest.fixture
def mcp_endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMcpTokenEndpoint]:
    endpoint = FakeMcpTokenEndpoint()
    real = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:  # noqa: ANN401
        # Replaces main's GuardedTransport; the token URL's address check is test_url_guard.py's.
        kwargs["transport"] = httpx.MockTransport(endpoint.handle)
        return real(*args, **kwargs)

    monkeypatch.setattr(oauth_client.httpx, "AsyncClient", client)
    yield endpoint


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> FakeSlackOAuth:
    fake = FakeSlackOAuth("xoxe-1-old", nested_under_authed_user=True)
    monkeypatch.setattr(oauth_service, "ClientSession", fake.client_session)
    return fake


@dataclass
class World:
    clock: ManualClock = field(default_factory=ManualClock)

    def __post_init__(self) -> None:
        self.config = DictConfig(
            {
                "/services/oauths/toolsets/slack": [
                    {
                        "_id": "oc-1",
                        "orgId": ORG,
                        "config": {
                            "clientId": "client-1",
                            "clientSecret": "secret-1",
                            "authorizeUrl": "https://slack.com/oauth/v2/authorize",
                            "tokenUrl": SLACK_TOKEN_URL,
                            "redirectUri": "https://pipeshub.example/toolsets/oauth/callback/slack",
                            "scopeParameterName": "user_scope",
                            "tokenResponsePath": "authed_user",
                        },
                    }
                ],
                "/services/mcp/dcr-clients/mcp-1": {"clientId": "dcr-client-1"},
            }
        )
        self.writer = FakeBindingWriter(self.config, now_ms=self.now_ms)
        self.documents = BindingDocuments(VaultSecretStore(self.config, self.clock))
        self.refresher = TokenRefresher(
            self.writer,
            self.documents,
            [ToolsetOAuthStrategy(self.config, self.now_ms), McpOAuthStrategy(self.config, self.now_ms)],
            self.clock,
        )

    def now_ms(self) -> int:
        return int(self.clock.now().timestamp() * 1000)

    async def bind(self, kind: str, installation: str, record: dict[str, Any], *, expires_in_s: int = 300) -> str:
        imported = await self.writer.import_record(
            ORG,
            ImportRequest(installation, "user:u1", "oauth", kind, None, Material("record", record, self.now_ms() + expires_in_s * 1000), None, None),
        )
        assert imported.binding is not None
        return imported.binding.id

    async def current(self, binding: str) -> dict[str, Any]:
        document = await self.documents.document(ORG, binding)
        path = self.writer.vault(ORG).path(binding)
        from harness_core.credentials import CredentialScope

        secret = await self.documents.vault.read_at(path, ORG, CredentialScope("binding", binding))
        assert secret is not None and document
        return secret.material  # type: ignore[return-value]


async def test_tr1_a_due_toolset_binding_is_refreshed_through_its_oauth_app(slack: FakeSlackOAuth) -> None:
    w = World()
    binding = await w.bind("toolset", "ts-1", slack_record())
    cycle = await w.refresher.run_once()
    assert cycle.refreshed == 1
    assert [r["refresh_token"] for r in slack.requests] == ["xoxe-1-old"]
    assert slack.requests[0]["client_id"] == "client-1"
    new_access, new_refresh = slack.issued[0]
    record = await w.current(binding)
    assert (record["credentials"]["access_token"], record["credentials"]["refresh_token"]) == (new_access, new_refresh)
    assert record["oauthConfigId"] == "oc-1" and record["isAuthenticated"] is True
    row = w.writer.rows[binding]
    assert row["revision"] == 2 and row["expiresAt"] is not None
    assert not [k for k in w.config.writes if k.startswith("/services/toolsets/")]


async def test_tr2_a_due_mcp_binding_is_refreshed_with_its_dcr_client(mcp_endpoint: FakeMcpTokenEndpoint) -> None:
    w = World()
    binding = await w.bind("mcp", "mcp-1", mcp_record())
    assert (await w.refresher.run_once()).refreshed == 1
    record = await w.current(binding)
    assert record["oauthTokens"]["accessToken"] == "mcp-access-2"
    assert record["oauthTokens"]["refreshToken"] == "mcp-refresh-2"
    assert record["oauthTokens"]["tokenUrl"] == MCP_TOKEN_URL
    assert mcp_endpoint.requests[0]["client_id"] == "dcr-client-1"
    assert w.writer.rows[binding]["expiresAt"] is not None


async def test_tr3_a_refused_refresh_token_takes_the_binding_out_of_the_scan(
    slack: FakeSlackOAuth, mcp_endpoint: FakeMcpTokenEndpoint
) -> None:
    w = World()
    toolset = await w.bind("toolset", "ts-1", slack_record("xoxe-1-revoked-at-slack"))
    mcp = await w.bind("mcp", "mcp-1", mcp_record("mcp-refresh-stale"))
    cycle = await w.refresher.run_once()
    assert cycle.refused == 2
    for binding in (toolset, mcp):
        assert w.writer.rows[binding]["status"] == "error"
        assert w.writer.rows[binding]["lastError"] == "invalid_grant"
    assert await w.refresher.run_once() == type(cycle)()
    assert len(slack.requests) == 1 and len(mcp_endpoint.requests) == 1


async def test_tr4_a_transient_failure_is_tried_again_next_cycle(mcp_endpoint: FakeMcpTokenEndpoint) -> None:
    w = World()
    binding = await w.bind("mcp", "mcp-1", mcp_record())
    mcp_endpoint.fail_next = 1
    assert (await w.refresher.run_once()).failed == 1
    assert w.writer.rows[binding]["status"] == "active" and w.writer.rows[binding]["lastError"] == "refresh_failed"
    assert (await w.refresher.run_once()).refreshed == 1


async def test_tr5_another_replica_refreshing_first_writes_no_failure(mcp_endpoint: FakeMcpTokenEndpoint) -> None:
    w = World()
    binding = await w.bind("mcp", "mcp-1", mcp_record())
    [due] = await w.writer.due(2**53, 10, "replica-a")
    _ = await w.writer.refreshed(ORG, binding, due.revision, Material("record", mcp_record("mcp-refresh-1"), 1))
    assert await w.refresher.refresh(due) == "raced"
    assert w.writer.rows[binding]["lastError"] is None and w.writer.rows[binding]["status"] == "active"


class _Broken:
    kind = "mcp"

    async def renew(self, org_id: str, installation: str, owner: str, record: dict[str, Any]) -> Renewed:
        raise RefreshRefused("should_not_run", permanent=True)


async def test_tr6_no_strategy_for_the_kind_is_refused_and_one_kind_has_one_strategy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    w = World()
    binding = await w.bind("plugin", "pl-1", {"isAuthenticated": True})
    cycle = await w.refresher.run_once()
    assert cycle.refused == 1 and w.writer.rows[binding]["lastError"] == "refresh_unsupported"
    assert "token_refresher.refused" in caplog.text
    with pytest.raises(ValueError, match="one refresh strategy"):
        _ = TokenRefresher(w.writer, w.documents, [_Broken(), _Broken()], w.clock)


async def test_tr7_the_due_window_and_the_cycle_limit(mcp_endpoint: FakeMcpTokenEndpoint) -> None:
    w = World()
    later = await w.bind("mcp", "mcp-1", mcp_record(), expires_in_s=3600)
    soon = await w.bind("mcp", "mcp-2", mcp_record("mcp-refresh-b"), expires_in_s=60)
    w.config.values["/services/mcp/dcr-clients/mcp-2"] = {"clientId": "dcr-client-1"}
    refresher = TokenRefresher(w.writer, w.documents, [McpOAuthStrategy(w.config, w.now_ms)], w.clock, limit=1)
    assert (await refresher.run_once()).refreshed == 1
    assert w.writer.rows[soon]["revision"] == 2 and w.writer.rows[later]["revision"] == 1
    assert (await refresher.run_once()).refreshed == 0
    _ = w.clock.advance(timedelta(minutes=55).total_seconds())
    assert (await refresher.run_once()).refreshed == 1
    assert w.writer.rows[later]["revision"] == 2


async def test_tr9_on_demand_refresh_of_an_owners_binding(mcp_endpoint: FakeMcpTokenEndpoint) -> None:
    w = World()
    binding = await w.bind("mcp", "mcp-1", mcp_record(), expires_in_s=3600)
    record = await w.refresher.refresh_owner(ORG, "mcp-1", "user:u1")
    assert record is not None and record["oauthTokens"]["accessToken"] == "mcp-access-2"
    assert w.writer.rows[binding]["revision"] == 2
    assert await w.refresher.refresh_owner(ORG, "mcp-1", "user:nobody") is None
    assert not [k for k in w.config.writes if k.startswith("/services/mcp/credentials/")]


# --- OF: the OAuth flows end to end -------------------------------------------------------------


def _broker(w: World) -> BindingCredentialBroker:
    return BindingCredentialBroker(pipeshub_credential_service(w.config, clock=w.clock), w.documents)


PURPOSE = CredentialPurpose(ORG, "toolset:ts-1", "run-1", "attempt-1")


async def test_of1_toolset_bound_pinned_refreshed_leased_then_revoked(slack: FakeSlackOAuth) -> None:
    w = World()
    binding = await w.bind("toolset", "ts-1", slack_record())
    pin = pinned_binding(ref=binding, version_digest=binding_version_digest(binding, 1)).model_copy(
        update={"owner": "user:u1"}
    )
    broker = _broker(w)
    early = await broker.lease(pin, PURPOSE, 300)
    assert (await broker.resolve(early, PURPOSE)).material["credentials"]["access_token"] == "xoxe.xoxp-1-old"  # type: ignore[index]
    assert (await w.refresher.run_once()).refreshed == 1
    new_access, _ = slack.issued[0]
    lease = await broker.lease(pin, PURPOSE, 300)
    assert (await broker.resolve(lease, PURPOSE)).material["credentials"]["access_token"] == new_access  # type: ignore[index]
    assert (await slack.answer(SLACK_TOKEN_URL, {"grant_type": "refresh_token", "client_id": "c", "client_secret": "s", "refresh_token": "xoxe-1-old"}))["ok"] is False
    _ = await w.writer.revoke_owner(ORG, "ts-1", "user:u1")
    with pytest.raises(CredentialRevoked):
        _ = await broker.resolve(lease, PURPOSE)
    with pytest.raises(CredentialRevoked):
        _ = await broker.lease(pin, PURPOSE, 300)
    assert (await w.refresher.run_once()).refreshed == 0


async def test_of2_mcp_with_a_dcr_client_the_same(mcp_endpoint: FakeMcpTokenEndpoint) -> None:
    w = World()
    binding = await w.bind("mcp", "mcp-1", mcp_record())
    pin = pinned_binding(ref=binding, version_digest=binding_version_digest(binding, 1)).model_copy(update={"owner": "user:u1"})
    purpose = CredentialPurpose(ORG, "mcp:mcp-1", "run-1", "attempt-1")
    broker = _broker(w)
    assert (await w.refresher.run_once()).refreshed == 1
    lease = await broker.lease(pin, purpose, 300)
    assert (await broker.resolve(lease, purpose)).material["oauthTokens"]["accessToken"] == "mcp-access-2"  # type: ignore[index]
    _ = await w.writer.revoke_owner(ORG, "mcp-1", "user:u1")
    with pytest.raises(CredentialRevoked):
        _ = await broker.resolve(lease, purpose)


async def test_of3_a_refresh_token_revoked_at_the_provider_leaves_the_last_access_token_until_revoked(
    mcp_endpoint: FakeMcpTokenEndpoint,
) -> None:
    w = World()
    binding = await w.bind("mcp", "mcp-1", mcp_record("revoked-at-the-provider"))
    pin = pinned_binding(ref=binding, version_digest=binding_version_digest(binding, 1)).model_copy(update={"owner": "user:u1"})
    purpose = CredentialPurpose(ORG, "mcp:mcp-1", "run-1", "attempt-1")
    assert (await w.refresher.run_once()).refused == 1
    assert w.writer.rows[binding]["status"] == "error"
    broker = _broker(w)
    lease = await broker.lease(pin, purpose, 300)
    assert (await broker.resolve(lease, purpose)).material["oauthTokens"]["accessToken"] == "mcp-access-1"  # type: ignore[index]
    assert json.dumps(w.writer.audited)


class _Started:
    def __init__(self, name: str, log: list[str]) -> None:
        self.name, self.log = name, log

    async def start(self, wait_for_initial_refresh: bool = True) -> None:
        self.log.append(f"start {self.name}")

    async def stop(self) -> None:
        self.log.append(f"stop {self.name}")


@pytest.mark.parametrize("switch", ["true", ""])
async def test_tr8_the_switch_starts_one_refresher_instead_of_the_two_services(
    switch: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.connectors.core.base.token_service import startup_service as module

    log: list[str] = []
    monkeypatch.setenv("CONTROL_PLANE_CREDENTIALS", switch)
    monkeypatch.setattr(module, "TokenRefreshService", lambda *_a, **_k: _Started("connectors", log))
    monkeypatch.setattr(module, "ToolsetTokenRefreshService", lambda *_a, **_k: _Started("toolsets", log))
    monkeypatch.setattr(module, "MCPTokenRefreshService", lambda *_a, **_k: _Started("mcp", log))

    class _Refresher:
        def start(self) -> None:
            log.append("start binding refresher")

        async def stop(self) -> None:
            log.append("stop binding refresher")

    async def refresher(*_: object) -> _Refresher:
        return _Refresher()

    monkeypatch.setattr(module, "deployment_token_refresher", refresher)
    service = module.StartupService()
    await service.initialize(DictConfig(), graph_provider=None)
    await service.shutdown()
    if switch:
        assert log == ["start connectors", "start binding refresher", "stop connectors", "stop binding refresher"]
    else:
        assert log == ["start connectors", "start toolsets", "start mcp", "stop connectors", "stop toolsets", "stop mcp"]


async def test_tr9_the_mcp_on_demand_refresh_goes_through_the_binding_when_switched_on(
    mcp_endpoint: FakeMcpTokenEndpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agents.mcp import token_refresh
    from app.connectors.core.base.token_service import token_refresher as module

    w = World()
    _ = await w.bind("mcp", "mcp-1", mcp_record(), expires_in_s=3600)
    w.config.values["/services/mcp/credentials/mcp-1/u1"] = mcp_record("old-path-refresh-token")

    async def refresher(_config: object) -> TokenRefresher:
        return w.refresher

    monkeypatch.setattr(module, "binding_refresher_for", refresher)
    monkeypatch.setenv("CONTROL_PLANE_CREDENTIALS", "true")
    tokens = await token_refresh.refresh_credential_record("mcp-1", "u1", w.config, org_id=ORG)  # type: ignore[arg-type]
    assert tokens.access_token == "mcp-access-2"
    assert w.config.values["/services/mcp/credentials/mcp-1/u1"] == mcp_record("old-path-refresh-token")
    with pytest.raises(token_refresh.MCPTokenRefreshError):
        _ = await token_refresh.refresh_credential_record("mcp-1", "nobody", w.config, org_id=ORG)  # type: ignore[arg-type]
