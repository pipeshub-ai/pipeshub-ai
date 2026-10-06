"""NodePdpClient: fail-closed behaviour, token claims and the aclVersion-keyed cache."""

from __future__ import annotations

import asyncio

import pytest
from jose import jwt

from app.modules.authz.node_pdp_client import (
    ChatArtifactKind,
    ChatContentCheck,
    DenyAllPdpClient,
    NodePdpClient,
    PdpConnectError,
    PdpHttpResponse,
    get_node_pdp_client,
    set_node_pdp_client,
)

from .pdp_fakes import SCOPED_SECRET, FakeConfig, FakePdpHttp, allow, deny


def _req(**over) -> ChatContentCheck:
    base = {
        "user_id": "user-c", "org_id": "a" * 24, "resource_type": "chatAttachment",
        "record_id": "rec-1", "owner_user_id": "user-b", "conversation_id": "conv-1",
    }
    base.update(over)
    return ChatContentCheck(**base)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _client(http: FakePdpHttp, **kw) -> NodePdpClient:
    return NodePdpClient(FakeConfig(), http, **kw)


class TestFailClosed:
    @pytest.mark.parametrize(
        "outcome",
        [
            PdpHttpResponse(status=500, body={"allow": True}),
            PdpHttpResponse(status=403, body={"allow": True}),
            PdpHttpResponse(status=200, body=None),
            PdpHttpResponse(status=200, body="nope"),
            PdpHttpResponse(status=200, body={"aclVersion": 1}),
            PdpHttpResponse(status=200, body={"allow": "true"}),
            PdpHttpResponse(status=200, body=["allow"]),
            asyncio.TimeoutError(),
            PdpConnectError("refused"),
            RuntimeError("boom"),
        ],
        ids=["500", "403", "no-body", "str-body", "missing-allow", "str-allow", "list-body",
             "timeout", "connect", "other"],
    )
    async def test_every_error_class_denies(self, outcome) -> None:
        http = FakePdpHttp(outcome, outcome)
        assert await _client(http).can_read_chat_content(_req()) is False

    async def test_allow_true(self) -> None:
        assert await _client(FakePdpHttp(allow())).can_read_chat_content(_req()) is True

    async def test_deny(self) -> None:
        assert await _client(FakePdpHttp(deny())).can_read_chat_content(_req()) is False

    async def test_hung_transport_is_cut_off_and_denied(self) -> None:
        class Hang:
            async def post_json(self, *a, **k) -> None:
                await asyncio.sleep(30)

        client = NodePdpClient(FakeConfig(), Hang(), timeout_s=0.05)
        assert await client.can_read_chat_content(_req()) is False

    async def test_missing_scoped_secret_denies_without_calling_node(self) -> None:
        class NoSecret(FakeConfig):
            async def get_config(self, key, default=None, use_cache=True) -> object:
                return {} if key == "/services/secretKeys" else await super().get_config(key, default)

        http = FakePdpHttp()
        assert await NodePdpClient(NoSecret(), http).can_read_chat_content(_req()) is False
        assert http.calls == []

    async def test_connect_error_is_retried_once_then_succeeds(self) -> None:
        http = FakePdpHttp(PdpConnectError("refused"), allow())
        assert await _client(http).can_read_chat_content(_req()) is True
        assert len(http.calls) == 2

    async def test_connect_error_retried_only_once(self) -> None:
        http = FakePdpHttp(PdpConnectError("a"), PdpConnectError("b"), allow())
        assert await _client(http).can_read_chat_content(_req()) is False
        assert len(http.calls) == 2

    async def test_timeout_is_not_retried(self) -> None:
        http = FakePdpHttp(asyncio.TimeoutError(), allow())
        assert await _client(http).can_read_chat_content(_req()) is False
        assert len(http.calls) == 1


class TestRequestShape:
    async def test_token_claims_and_ttl(self) -> None:
        http = FakePdpHttp(allow())
        await _client(http).can_read_chat_content(_req())
        call = http.calls[0]
        token = call["headers"]["Authorization"].removeprefix("Bearer ")
        claims = jwt.decode(token, SCOPED_SECRET, algorithms=["HS256"])
        assert claims["scopes"] == ["authz:check"]
        assert claims["orgId"] == "a" * 24
        assert claims["userId"] == "user-c"
        assert claims["exp"] - claims["iat"] == 60

    async def test_endpoint_and_body(self) -> None:
        http = FakePdpHttp(allow())
        req = _req(
            resource_type="chatArtifact", run_id="run-1",
            kind=ChatArtifactKind(visibility="VISIBLE", is_temporary=False),
        )
        await _client(http).can_read_chat_content(req)
        call = http.calls[0]
        assert call["url"] == "http://node:3000/api/v1/authz/internal/check"
        assert call["timeout_s"] == 2.0
        assert call["json"] == {
            "userId": "user-c", "orgId": "a" * 24, "action": "read",
            "resource": {
                "type": "chatArtifact", "recordId": "rec-1", "ownerUserId": "user-b",
                "conversationId": "conv-1", "runId": "run-1",
                "kind": {"visibility": "VISIBLE", "isTemporary": False},
            },
        }

    async def test_nullable_fields_are_omitted(self) -> None:
        http = FakePdpHttp(allow())
        await _client(http).can_read_chat_content(_req(conversation_id=None))
        assert http.calls[0]["json"]["resource"] == {
            "type": "chatAttachment", "recordId": "rec-1", "ownerUserId": "user-b",
        }

    async def test_token_reused_then_reminted(self, monkeypatch) -> None:
        import app.modules.authz.node_pdp_client as mod

        minted: list[int] = []
        real = mod.mint_service_token

        def counting(*a, **k) -> object:
            minted.append(1)
            return real(*a, **k)

        monkeypatch.setattr(mod, "mint_service_token", counting)
        clock = Clock()
        client = _client(FakePdpHttp(), clock=clock)
        await client.can_read_chat_content(_req(record_id="r1"))
        await client.can_read_chat_content(_req(record_id="r2"))
        assert len(minted) == 1
        clock.now += 51
        await client.can_read_chat_content(_req(record_id="r3"))
        assert len(minted) == 2


class TestCache:
    async def test_cached_allow_then_new_acl_version_asks_node_again(self) -> None:
        """PI-13."""
        http = FakePdpHttp(allow(4), deny(5))
        client = _client(http)
        assert await client.can_read_chat_content(_req(acl_version=4)) is True
        assert await client.can_read_chat_content(_req(acl_version=4)) is True
        assert len(http.calls) == 1
        assert await client.can_read_chat_content(_req(acl_version=5)) is False
        assert len(http.calls) == 2

    async def test_no_acl_version_never_caches(self) -> None:
        """PH07-06."""
        http = FakePdpHttp(allow(), allow())
        client = _client(http)
        assert await client.can_read_chat_content(_req()) is True
        assert await client.can_read_chat_content(_req()) is True
        assert len(http.calls) == 2

    async def test_revocation_is_immediate_without_acl_version(self) -> None:
        http = FakePdpHttp(allow(), deny())
        client = _client(http)
        assert await client.can_read_chat_content(_req()) is True
        assert await client.can_read_chat_content(_req()) is False

    @pytest.mark.parametrize(
        "other",
        [{"org_id": "b" * 24}, {"user_id": "user-d"}, {"record_id": "rec-2"}, {"conversation_id": "conv-2"}],
        ids=["org", "user", "record", "conversation"],
    )
    async def test_key_includes_every_field(self, other) -> None:
        http = FakePdpHttp(allow(1), deny(1))
        client = _client(http)
        assert await client.can_read_chat_content(_req(acl_version=1)) is True
        assert await client.can_read_chat_content(_req(acl_version=1, **other)) is False
        assert len(http.calls) == 2

    async def test_grant_on_chat_b_does_not_serve_chat_a(self) -> None:
        http = FakePdpHttp(allow(1), deny(1))
        client = _client(http)
        assert await client.can_read_chat_content(_req(conversation_id="chat-b", acl_version=1)) is True
        assert await client.can_read_chat_content(_req(conversation_id="chat-a", acl_version=1)) is False

    async def test_ttl_expiry(self) -> None:
        clock = Clock()
        http = FakePdpHttp(allow(1), deny(1))
        client = _client(http, clock=clock)
        assert await client.can_read_chat_content(_req(acl_version=1)) is True
        clock.now += 29
        assert await client.can_read_chat_content(_req(acl_version=1)) is True
        clock.now += 2
        assert await client.can_read_chat_content(_req(acl_version=1)) is False
        assert len(http.calls) == 2

    async def test_denies_and_errors_are_not_cached(self) -> None:
        http = FakePdpHttp(deny(1), PdpHttpResponse(status=500), allow(1))
        client = _client(http)
        assert await client.can_read_chat_content(_req(acl_version=1)) is False
        assert await client.can_read_chat_content(_req(acl_version=1)) is False
        assert await client.can_read_chat_content(_req(acl_version=1)) is True
        assert len(http.calls) == 3

    async def test_allow_for_a_newer_version_is_not_cached_under_the_stale_key(self) -> None:
        http = FakePdpHttp(allow(7), allow(7))
        client = _client(http)
        assert await client.can_read_chat_content(_req(acl_version=4)) is True
        assert await client.can_read_chat_content(_req(acl_version=4)) is True
        assert len(http.calls) == 2

    async def test_lru_bound(self) -> None:
        http = FakePdpHttp()
        client = _client(http, cache_max_entries=3)
        for i in range(4):
            await client.can_read_chat_content(_req(record_id=f"r{i}", acl_version=1))
        assert len(client._cache) == 3
        assert len(http.calls) == 4
        # r0 was evicted, r3 still cached
        await client.can_read_chat_content(_req(record_id="r3", acl_version=1))
        assert len(http.calls) == 4
        await client.can_read_chat_content(_req(record_id="r0", acl_version=1))
        assert len(http.calls) == 5

    async def test_lru_recency_is_refreshed_on_hit(self) -> None:
        http = FakePdpHttp()
        client = _client(http, cache_max_entries=2)
        await client.can_read_chat_content(_req(record_id="r0", acl_version=1))
        await client.can_read_chat_content(_req(record_id="r1", acl_version=1))
        await client.can_read_chat_content(_req(record_id="r0", acl_version=1))
        await client.can_read_chat_content(_req(record_id="r2", acl_version=1))
        before = len(http.calls)
        await client.can_read_chat_content(_req(record_id="r0", acl_version=1))
        assert len(http.calls) == before

    async def test_default_bound_is_10k_and_ttl_30s(self) -> None:
        client = _client(FakePdpHttp())
        assert client._max_entries == 10_000
        assert client._ttl == 30.0


class TestAccessor:
    def teardown_method(self) -> None:
        set_node_pdp_client(None)

    async def test_unset_accessor_is_deny_all(self) -> None:
        set_node_pdp_client(None)
        client = get_node_pdp_client()
        assert isinstance(client, DenyAllPdpClient)
        assert await client.can_read_chat_content(_req()) is False

    async def test_set_accessor(self) -> None:
        mine = _client(FakePdpHttp())
        set_node_pdp_client(mine)
        assert get_node_pdp_client() is mine
