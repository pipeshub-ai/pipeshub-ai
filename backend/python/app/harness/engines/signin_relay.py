"""``SignInRelay`` on Redis pub/sub (SIGNIN): a person's browser reaches the worker holding their sign-in.

Node publishes the owner's requests on the attempt's channel and waits for the answer on a reply channel it names:
``describe`` (the link the worker shows), ``code`` (the pasted code, with whether the org admits consumer plans) and
``cancel``. Nothing is stored: pub/sub keeps no message, a request no worker answers times out at Node, and the link
and the code never reach a log, the journal or a key. The worker answers only the run's owner (Node checks too);
``harness_core.engines.signin_relay.RelayedAttempt`` decides every answer. ``revoke`` reaches every worker: the
person disconnected, an admin signed them out, the org's switch went off, erasure, or a conversation was shared.

A signed-in sandbox kept between a conversation's turns (SIGNIN-5) is named by a ``KeptSignIn`` under ``KEPT_PREFIX``
(ids and times only), and Node remembers when it last revoked an org, a person or a conversation under
``REVOKED_PREFIX`` (an epoch in ms), so a turn on a kept sandbox finds a revocation its worker did not hear.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any, Protocol, cast

from app.core.org_context import spawn_in_request
from harness_core.engines.signin import KeptSignIn
from harness_core.engines.signin_relay import (
    CLOCK_SKEW_S,
    RelayedAttempt,
    expires_at_ms,
    revocation_matches,
    scopes_of,
)
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from app.harness.interaction_wakeup import PubSub
    from harness_core.interactions.ports import RunBinding

ATTEMPT_PREFIX = "harness:signin:attempt:"
REPLY_PREFIX = "harness:signin:reply:"
REVOKE_CHANNEL = "harness:signin:revoke"
KEPT_PREFIX = "harness:signin:kept:"
REVOKED_PREFIX = "harness:signin:revoked:"
# Deletes the key only while it still holds exactly ARGV[1]: a parked turn's timer never takes a later turn's record.
_TAKE_IF = "if redis.call('GET', KEYS[1]) == ARGV[1] then redis.call('DEL', KEYS[1]); return ARGV[1] end; return false"
_POLL_S = 1.0
_MAX_MESSAGE = 8 * 1024


def channel(name: str, namespace: str = "") -> str:
    return f"{namespace}:{name}" if namespace else name


class RelayClient(Protocol):
    def pubsub(self) -> PubSub: ...

    async def publish(self, channel: str, message: str) -> object: ...

    async def set(self, name: str, value: str, *, ex: int) -> object: ...

    async def getdel(self, name: str) -> object: ...

    async def mget(self, keys: list[str]) -> list[object]: ...

    async def eval(self, script: str, numkeys: int, *args: str) -> object: ...


def _data(message: object) -> dict[str, Any] | None:
    raw = cast("dict[str, Any]", message).get("data") if isinstance(message, dict) else None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str) or len(raw) > _MAX_MESSAGE:
        return None
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        return None
    return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else None


class RedisSignInRelay:
    def __init__(self, client: RelayClient, *, key_namespace: str = "", log: HarnessLog | None = None) -> None:
        self._client = client
        self._namespace = key_namespace
        self._log = log or HarnessLog.named("app.harness.engines.signin_relay")

    async def _publish(self, target: str, body: dict[str, Any]) -> None:
        try:
            _ = await self._client.publish(target, json.dumps(body))
        except Exception as exc:
            self._log.warning("engine.signin.relay_publish_failed", errorType=type(exc).__name__)

    @contextlib.asynccontextmanager
    async def attempt(self, run: RunBinding, owner_id: str, attempt_id: str) -> AsyncIterator[RelayedAttempt]:
        opened = RelayedAttempt(run, owner_id, attempt_id, expires_at_ms(), self._publish)
        replies = channel(REPLY_PREFIX, self._namespace)

        async def hear(request: dict[str, Any]) -> None:
            reply = request.get("reply")
            # Only a reply channel Node listens on: a request cannot make the worker publish anywhere else.
            if isinstance(reply, str) and reply.startswith(replies):
                await opened.handle(request, reply)

        async with self._listening(channel(ATTEMPT_PREFIX + attempt_id, self._namespace), hear):
            yield opened

    @contextlib.asynccontextmanager
    async def revoked(self, run: RunBinding, owner_id: str) -> AsyncIterator[asyncio.Event]:
        ended = asyncio.Event()

        async def hear(message: dict[str, Any]) -> None:
            if revocation_matches(message, run, owner_id):
                if not ended.is_set():
                    self._log.info("engine.signin.revoked", runId=run.run_id, reasonCode=str(message.get("reason")))
                ended.set()

        async with self._listening(channel(REVOKE_CHANNEL, self._namespace), hear):
            yield ended

    async def keep(self, key: str, kept: KeptSignIn, ttl_s: int) -> None:
        _ = await self._client.set(channel(KEPT_PREFIX + key, self._namespace), kept.encode(), ex=max(1, ttl_s))

    async def take(self, key: str, *, only: KeptSignIn | None = None) -> KeptSignIn | None:
        name = channel(KEPT_PREFIX + key, self._namespace)
        raw = await self._client.getdel(name) if only is None else await self._client.eval(_TAKE_IF, 1, name, only.encode())
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        return KeptSignIn.decode(raw) if isinstance(raw, str) else None

    async def ended_since(self, run: RunBinding, owner_id: str, at: float) -> bool:
        names = [channel(REVOKED_PREFIX + scope, self._namespace) for scope in scopes_of(run, owner_id)]
        found = await self._client.mget(names)
        for raw in found:
            text = raw.decode() if isinstance(raw, bytes) else raw
            if isinstance(text, str) and text.isdigit() and int(text) / 1000 >= at - CLOCK_SKEW_S:
                return True
        return False

    @contextlib.asynccontextmanager
    async def _listening(
        self, name: str, handler: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> AsyncIterator[None]:
        pubsub = self._client.pubsub()
        # Subscribed before the caller shows anything, so the person's first request is never missed.
        _ = await pubsub.subscribe(name)

        async def pump() -> None:
            while True:
                try:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=_POLL_S)
                except Exception as exc:
                    self._log.warning("engine.signin.relay_unavailable", errorType=type(exc).__name__)
                    await asyncio.sleep(_POLL_S)
                    continue
                data = _data(message) if message is not None else None
                if data is not None:
                    await handler(data)

        task = spawn_in_request(pump())
        try:
            yield
        finally:
            _ = task.cancel()
            with contextlib.suppress(BaseException):
                await task
            with contextlib.suppress(Exception):
                await pubsub.aclose()


__all__ = [
    "ATTEMPT_PREFIX",
    "KEPT_PREFIX",
    "REPLY_PREFIX",
    "REVOKED_PREFIX",
    "REVOKE_CHANNEL",
    "RedisSignInRelay",
    "RelayClient",
    "channel",
]
