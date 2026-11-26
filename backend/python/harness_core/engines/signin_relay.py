"""The worker's side of ``SignInRelay``, over any request/answer transport (SIGNIN).

``RelayedAttempt`` answers the person's requests for one open sign-in: ``describe`` (the link while it waits),
``code`` (one at a time, with whether the org admits only work plans) and ``cancel``; only the run's owner in the
run's org gets an answer that says anything. The Redis adapter (``app/harness/engines/signin_relay.py``) carries it
on pub/sub; ``InMemorySignInRelay`` carries it in process, with the person's side for tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from harness_core.engines.signin import (
    SIGNIN_WAIT_S,
    CodeRequest,
    KeptSignIn,
    SignInRefused,
    SignInReply,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

    from harness_core.interactions.ports import RunBinding

@dataclass
class RelayedAttempt:
    """One open sign-in as the person's requests reach it; ``send(target, body)`` answers a request."""

    run: RunBinding
    owner_id: str
    attempt_id: str
    expires_at_ms: int
    send: Callable[[str, dict[str, Any]], Awaitable[None]]
    link: str | None = None
    state: str = "waiting"
    codes: asyncio.Queue[tuple[CodeRequest, str]] = field(default_factory=asyncio.Queue)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    pending_reply: str | None = None

    def show(self, link: str) -> None:
        self.link = link

    async def code(self, timeout_s: float) -> CodeRequest:
        getting = asyncio.ensure_future(self.codes.get())
        cancelled = asyncio.ensure_future(self.cancelled.wait())
        try:
            done, _ = await asyncio.wait({getting, cancelled}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (getting, cancelled):
                _ = task.cancel()
        if getting in done:
            request, reply = getting.result()
            self.pending_reply = reply
            return request
        if cancelled in done:
            self.state = "cancelled"
            raise SignInRefused("the person cancelled the sign-in", reason="signin_cancelled")
        self.state = "expired"
        raise SignInRefused("nobody signed in in time", reason="signin_timeout")

    async def reply(self, reply: SignInReply) -> None:
        target, self.pending_reply = self.pending_reply, None
        self.state = "signed_in" if reply.state == "signed_in" else ("waiting" if reply.retry else "failed")
        if target is None:
            return
        body: dict[str, Any] = {"ok": True, "ownerId": self.owner_id, "state": reply.state, "retry": reply.retry}
        if reply.reason_code is not None:
            body["reasonCode"] = reply.reason_code
        if reply.signed_in is not None:
            # The account's email and plan go to Node only, which shows them to the owner (never journaled).
            body["email"] = reply.signed_in.email
            body["plan"] = reply.signed_in.plan
        await self.send(target, body)

    async def handle(self, request: Mapping[str, Any], reply: str) -> None:
        """One request from the person's side; ``reply`` is where its answer goes."""
        if request.get("actorId") != self.owner_id or request.get("orgId") != self.run.tenant:
            await self.send(reply, {"ok": False, "reasonCode": "signin_not_owner"})
            return
        op = request.get("op")
        if op == "describe":
            await self.send(reply, self.described())
        elif op == "cancel":
            self.cancelled.set()
            await self.send(reply, {"ok": True, "ownerId": self.owner_id, "state": "cancelled"})
        elif op == "code":
            code, work_only = request.get("code"), request.get("workPlansOnly")
            if not isinstance(code, str) or not isinstance(work_only, bool) or self.pending_reply is not None:
                await self.send(reply, {"ok": False, "reasonCode": "signin_code_invalid"})
                return
            self.codes.put_nowait((CodeRequest(code=code, work_plans_only=work_only), reply))
        else:
            await self.send(reply, {"ok": False, "reasonCode": "signin_unknown_request"})

    def described(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "ok": True,
            "ownerId": self.owner_id,
            "orgId": self.run.tenant,
            "sessionId": self.run.scope,
            "runId": self.run.run_id,
            "state": self.state,
            "expiresAt": self.expires_at_ms,
        }
        if self.link is not None and self.state == "waiting":
            body["link"] = self.link
        return body


def expires_at_ms(now_s: float | None = None) -> int:
    return int(((time.time() if now_s is None else now_s) + SIGNIN_WAIT_S) * 1000)


# Node's clock stamps a revocation, the worker's a sign-in: one this close before a sign-in still ends it.
CLOCK_SKEW_S = 60.0


def revocation_scopes(org_id: str, user_id: str | None, session_id: str | None) -> tuple[str, ...]:
    """Where a revocation's time is remembered (Node writes them, ``revoke`` in ``redis-engine-signin-relay.ts``):
    the whole org, one person in it, or one conversation."""
    if user_id is not None:
        return (f"{org_id}:u:{user_id}",)
    if session_id is not None:
        return (f"{org_id}:s:{session_id}",)
    return (org_id,)


def scopes_of(run: RunBinding, owner_id: str) -> tuple[str, ...]:
    """Every scope a revocation of this run's sign-in may have named."""
    return (run.tenant, f"{run.tenant}:u:{owner_id}", f"{run.tenant}:s:{run.scope}")


def revocation_matches(message: Mapping[str, Any], run: RunBinding, owner_id: str) -> bool:
    """A revocation names the run's org and its owner, its session, or the whole org."""
    if message.get("orgId") != run.tenant:
        return False
    user, session = message.get("userId"), message.get("sessionId")
    return (user is None and session is None) or user == owner_id or session == run.scope


class InMemorySignInRelay:
    """``SignInRelay`` in process; ``ask`` and ``revoke`` are the person's (Node's) side, for tests."""

    def __init__(self) -> None:
        self._open: dict[str, RelayedAttempt] = {}
        self._revoked: list[tuple[RunBinding, str, asyncio.Event]] = []
        self._answers: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.kept: dict[str, KeptSignIn] = {}
        self.revoked_at: dict[str, float] = {}
        # The person's (Node's) clock for revocations; tests move it.
        self.now: Callable[[], float] = time.time

    async def _send(self, target: str, body: dict[str, Any]) -> None:
        answer = self._answers.pop(target, None)
        if answer is not None and not answer.done():
            answer.set_result(body)

    @contextlib.asynccontextmanager
    async def attempt(self, run: RunBinding, owner_id: str, attempt_id: str) -> AsyncIterator[RelayedAttempt]:
        opened = RelayedAttempt(run, owner_id, attempt_id, expires_at_ms(), self._send)
        self._open[attempt_id] = opened
        try:
            yield opened
        finally:
            _ = self._open.pop(attempt_id, None)

    @contextlib.asynccontextmanager
    async def revoked(self, run: RunBinding, owner_id: str) -> AsyncIterator[asyncio.Event]:
        entry = (run, owner_id, asyncio.Event())
        self._revoked.append(entry)
        try:
            yield entry[2]
        finally:
            self._revoked.remove(entry)

    async def keep(self, key: str, kept: KeptSignIn, ttl_s: int) -> None:
        self.kept[key] = kept

    async def take(self, key: str, *, only: KeptSignIn | None = None) -> KeptSignIn | None:
        found = self.kept.get(key)
        if found is None or (only is not None and found != only):
            return None
        return self.kept.pop(key)

    async def ended_since(self, run: RunBinding, owner_id: str, at: float) -> bool:
        times = [self.revoked_at[s] for s in scopes_of(run, owner_id) if s in self.revoked_at]
        return any(t >= at - CLOCK_SKEW_S for t in times)

    async def ask(self, attempt_id: str, request: Mapping[str, Any], timeout_s: float = 2.0) -> dict[str, Any] | None:
        opened = self._open.get(attempt_id)
        if opened is None:
            return None
        target = f"reply-{len(self._answers)}-{id(request)}"
        answer: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._answers[target] = answer
        await opened.handle(request, target)
        try:
            return await asyncio.wait_for(answer, timeout_s)
        except TimeoutError:
            _ = self._answers.pop(target, None)
            return None

    async def revoke(self, message: Mapping[str, Any]) -> None:
        user, session = message.get("userId"), message.get("sessionId")
        for scope in revocation_scopes(str(message.get("orgId")), user, session):
            self.revoked_at[scope] = self.now()
        for run, owner, ended in list(self._revoked):
            if revocation_matches(message, run, owner):
                ended.set()


__all__ = [
    "CLOCK_SKEW_S",
    "InMemorySignInRelay",
    "RelayedAttempt",
    "expires_at_ms",
    "revocation_matches",
    "revocation_scopes",
    "scopes_of",
]
