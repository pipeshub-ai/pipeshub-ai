"""SIGNIN-2: ``SignInRelayContract`` (RC-1..RC-10) on the in-memory relay and on Redis pub/sub (fakeredis), as
Node's ``redis-engine-signin-relay.ts`` talks to it; and what only Redis can get wrong (SR-1..SR-3).

Nothing relayed is kept: pub/sub stores no message, so after an exchange no Redis key holds the link, the code or
the email (LK-S1).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import fakeredis
import pytest

from app.harness.engines.signin_relay import (
    ATTEMPT_PREFIX,
    REPLY_PREFIX,
    REVOKE_CHANNEL,
    REVOKED_PREFIX,
    RedisSignInRelay,
    channel,
)
from harness_core.engines.signin import SignedIn, SignInReply
from harness_core.engines.signin_relay import InMemorySignInRelay, revocation_scopes
from harness_core.testing.contracts.signin_relay import (
    LINK,
    OWNER,
    RUN,
    SignInRelayContract,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

NS = "tenant-a"


@dataclass
class _Memory:
    relay: InMemorySignInRelay

    async def ask(self, attempt_id: str, request: Mapping[str, Any]) -> dict[str, Any] | None:
        return await self.relay.ask(attempt_id, request, timeout_s=0.5)

    async def revoke(self, message: Mapping[str, Any]) -> None:
        await self.relay.revoke(message)


class TestInMemorySignInRelay(SignInRelayContract):
    @pytest.fixture
    def relay_harness(self) -> _Memory:
        return _Memory(InMemorySignInRelay())


@dataclass
class _Redis:
    relay: RedisSignInRelay
    client: Any

    async def ask(self, attempt_id: str, request: Mapping[str, Any], *, timeout: float = 1.0) -> dict[str, Any] | None:
        """Node's side: publish on the attempt's channel, wait for the answer on a reply channel."""
        reply = channel(f"{REPLY_PREFIX}{uuid.uuid4()}", NS)
        pubsub = self.client.pubsub()
        await pubsub.subscribe(reply)
        try:
            _ = await self.client.publish(channel(ATTEMPT_PREFIX + attempt_id, NS), json.dumps({**request, "reply": reply}))
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
                if message is not None:
                    return json.loads(message["data"])
            return None
        finally:
            await pubsub.aclose()

    async def revoke(self, message: Mapping[str, Any]) -> None:
        """As Node's ``revoke``: remembered for a day under the scope it names, then announced."""
        for scope in revocation_scopes(message["orgId"], message.get("userId"), message.get("sessionId")):
            _ = await self.client.set(channel(REVOKED_PREFIX + scope, NS), str(int(time.time() * 1000)), ex=86_400)
        _ = await self.client.publish(channel(REVOKE_CHANNEL, NS), json.dumps(message))


@pytest.fixture
async def redis_harness() -> AsyncIterator[_Redis]:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield _Redis(RedisSignInRelay(client, key_namespace=NS), client)
    await client.aclose()


class TestRedisSignInRelay(SignInRelayContract):
    @pytest.fixture
    def relay_harness(self, redis_harness: _Redis) -> _Redis:
        return redis_harness


def _req(op: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"op": op, "actorId": OWNER, "orgId": RUN.tenant, **extra}


async def test_sr1_nothing_relayed_is_left_in_redis(redis_harness: _Redis) -> None:
    async with redis_harness.relay.attempt(RUN, OWNER, "sin_1") as attempt:
        attempt.show(LINK)
        _ = await redis_harness.ask("sin_1", _req("describe"))
        asking = asyncio.ensure_future(redis_harness.ask("sin_1", _req("code", code="SECRETCODE#s1", workPlansOnly=True)))
        _ = await attempt.code(2)
        await attempt.reply(SignInReply("signed_in", signed_in=SignedIn("ada@example.com", "team")))
        _ = await asking
    client = redis_harness.client
    values = [await client.dump(key) async for key in client.scan_iter("*")]
    assert "SECRETCODE" not in repr(values) and "ada@example.com" not in repr(values) and LINK not in repr(values)


async def test_sr2_a_request_naming_any_other_reply_channel_is_ignored(redis_harness: _Redis) -> None:
    client = redis_harness.client
    elsewhere = client.pubsub()
    await elsewhere.subscribe("victim:channel")
    async with redis_harness.relay.attempt(RUN, OWNER, "sin_1") as attempt:
        attempt.show(LINK)
        body = {**_req("describe"), "reply": "victim:channel"}
        _ = await client.publish(channel(ATTEMPT_PREFIX + "sin_1", NS), json.dumps(body))
        await asyncio.sleep(0.3)
        assert await elsewhere.get_message(ignore_subscribe_messages=True, timeout=0.3) is None
    await elsewhere.aclose()


async def test_sr3_the_channels_are_the_ones_node_uses() -> None:
    assert channel(ATTEMPT_PREFIX + "sin_1") == "harness:signin:attempt:sin_1"
    assert channel(REPLY_PREFIX, "tenant-a") == "tenant-a:harness:signin:reply:"
    assert channel(REVOKE_CHANNEL, "tenant-a") == "tenant-a:harness:signin:revoke"
    # SIGNIN-5: where Node remembers a revocation (`REVOKED_PREFIX` in redis-engine-signin-relay.ts, SR-N6).
    assert channel(REVOKED_PREFIX + "org-1:u:u1", "tenant-a") == "tenant-a:harness:signin:revoked:org-1:u:u1"
