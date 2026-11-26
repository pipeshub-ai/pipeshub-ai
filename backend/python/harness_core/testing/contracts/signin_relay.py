"""Contract suite for ``SignInRelay`` adapters (SIGNIN, SIGNIN-5): RC-1..RC-10.

Subclass ``SignInRelayContract`` and provide a ``relay_harness`` fixture: the relay, and the person's side as Node
plays it (``ask`` an attempt and get its answer, ``revoke`` sign-ins).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any, Protocol

import pytest

from harness_core.engines.signin import KeptSignIn, SignedIn, SignInRefused, SignInReply
from harness_core.interactions.ports import RunBinding

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.signin import SignInRelay

RUN = RunBinding("org-1", "ses-1", "run-1")
OWNER = "user-1"
LINK = "https://claude.com/cai/oauth/authorize?state=s1"


class RelayHarness(Protocol):
    @property
    def relay(self) -> SignInRelay: ...

    async def ask(self, attempt_id: str, request: Mapping[str, Any]) -> dict[str, Any] | None:
        """The person's request and the worker's answer; ``None`` when no worker answers."""
        ...

    async def revoke(self, message: Mapping[str, Any]) -> None: ...


def _req(op: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"op": op, "actorId": OWNER, "orgId": RUN.tenant, **extra}


class SignInRelayContract:
    @pytest.fixture
    def relay_harness(self) -> RelayHarness:
        raise NotImplementedError("provide a relay_harness fixture")

    async def test_rc1_the_owner_gets_the_link_only_while_the_attempt_waits(self, relay_harness: RelayHarness) -> None:
        async with relay_harness.relay.attempt(RUN, OWNER, "sin_1") as attempt:
            assert "link" not in (await relay_harness.ask("sin_1", _req("describe")) or {})
            attempt.show(LINK)
            described = await relay_harness.ask("sin_1", _req("describe"))
            assert described is not None
            assert {k: described[k] for k in ("ok", "ownerId", "orgId", "sessionId", "runId", "state", "link")} == {
                "ok": True, "ownerId": OWNER, "orgId": "org-1", "sessionId": "ses-1", "runId": "run-1",
                "state": "waiting", "link": LINK,
            }
            assert described["expiresAt"] > 0
        assert await relay_harness.ask("sin_1", _req("describe")) is None

    @pytest.mark.parametrize("who", [{"actorId": "user-2"}, {"orgId": "org-2"}, {"actorId": None}])
    async def test_rc2_anyone_but_the_owner_in_the_runs_org_is_refused(
        self, relay_harness: RelayHarness, who: dict[str, Any]
    ) -> None:
        async with relay_harness.relay.attempt(RUN, OWNER, "sin_1") as attempt:
            attempt.show(LINK)
            for op in ("describe", "code", "cancel"):
                answer = await relay_harness.ask("sin_1", {**_req(op, code="c#s1", workPlansOnly=True), **who})
                assert answer == {"ok": False, "reasonCode": "signin_not_owner"}
            with pytest.raises(SignInRefused) as nothing:
                _ = await attempt.code(0.05)
            assert nothing.value.reason_code == "signin_timeout"

    async def test_rc3_a_code_reaches_the_attempt_and_its_reply_reaches_the_person(
        self, relay_harness: RelayHarness
    ) -> None:
        async with relay_harness.relay.attempt(RUN, OWNER, "sin_1") as attempt:
            attempt.show(LINK)
            asking = asyncio.ensure_future(relay_harness.ask("sin_1", _req("code", code="c#s1", workPlansOnly=False)))
            request = await attempt.code(2)
            assert (request.code, request.work_plans_only) == ("c#s1", False)
            await attempt.reply(SignInReply("signed_in", signed_in=SignedIn("ada@example.com", "team")))
            assert await asking == {"ok": True, "ownerId": OWNER, "state": "signed_in", "retry": False, "email": "ada@example.com",
                                    "plan": "team"}
            described = await relay_harness.ask("sin_1", _req("describe"))
            assert described is not None and described["state"] == "signed_in" and "link" not in described

    async def test_rc4_a_malformed_code_request_never_reaches_the_login(self, relay_harness: RelayHarness) -> None:
        async with relay_harness.relay.attempt(RUN, OWNER, "sin_1") as attempt:
            for body in (_req("code"), _req("code", code=5, workPlansOnly=True), _req("code", code="c#s1")):
                assert await relay_harness.ask("sin_1", body) == {"ok": False, "reasonCode": "signin_code_invalid"}
            with pytest.raises(SignInRefused):
                _ = await attempt.code(0.05)

    async def test_rc5_cancel_and_timeout_end_the_wait(self, relay_harness: RelayHarness) -> None:
        async with relay_harness.relay.attempt(RUN, OWNER, "sin_1") as attempt:
            assert await relay_harness.ask("sin_1", _req("cancel")) == {"ok": True, "ownerId": OWNER, "state": "cancelled"}
            with pytest.raises(SignInRefused) as cancelled:
                _ = await attempt.code(2)
            assert cancelled.value.reason_code == "signin_cancelled"
        async with relay_harness.relay.attempt(RUN, OWNER, "sin_2") as attempt:
            with pytest.raises(SignInRefused) as expired:
                _ = await attempt.code(0.05)
            assert expired.value.reason_code == "signin_timeout"

    @pytest.mark.parametrize(
        ("message", "ends"),
        [
            ({"orgId": "org-1", "userId": OWNER, "reason": "user"}, True),
            ({"orgId": "org-1", "sessionId": "ses-1", "reason": "shared"}, True),
            ({"orgId": "org-1", "reason": "policy"}, True),
            ({"orgId": "org-1", "userId": "user-2", "reason": "user"}, False),
            ({"orgId": "org-1", "sessionId": "ses-2", "reason": "shared"}, False),
            ({"orgId": "org-2", "userId": OWNER, "reason": "user"}, False),
        ],
    )
    async def test_rc6_revocation_reaches_exactly_the_runs_it_names(
        self, relay_harness: RelayHarness, *, message: dict[str, Any], ends: bool
    ) -> None:
        async with relay_harness.relay.revoked(RUN, OWNER) as ended:
            await relay_harness.revoke(message)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(ended.wait(), 1.5)
            assert ended.is_set() is ends

    async def test_rc8_a_kept_sign_in_is_taken_once(self, relay_harness: RelayHarness) -> None:
        """SIGNIN-5: what a turn parks, the next turn takes, and nobody takes it twice."""
        kept = KeptSignIn("sbx-1", OWNER, 1_000.0, 7)
        assert await relay_harness.relay.take("k1") is None
        await relay_harness.relay.keep("k1", kept, 60)
        assert await relay_harness.relay.take("k2") is None
        assert await relay_harness.relay.take("k1") == kept
        assert await relay_harness.relay.take("k1") is None

    async def test_rc9_a_watch_takes_only_its_own_record(self, relay_harness: RelayHarness) -> None:
        """A parked turn's idle watch never takes the record a later turn parked under the same key."""
        mine, later = KeptSignIn("sbx-1", OWNER, 1_000.0, 7), KeptSignIn("sbx-1", OWNER, 1_000.0, 9)
        await relay_harness.relay.keep("k1", later, 60)
        assert await relay_harness.relay.take("k1", only=mine) is None
        assert await relay_harness.relay.take("k1", only=later) == later
        assert await relay_harness.relay.take("k1") is None

    @pytest.mark.parametrize(
        ("message", "ends"),
        [
            ({"orgId": "org-1", "userId": OWNER, "reason": "admin"}, True),
            ({"orgId": "org-1", "sessionId": "ses-1", "reason": "shared"}, True),
            ({"orgId": "org-1", "reason": "policy"}, True),
            ({"orgId": "org-1", "userId": "user-2", "reason": "admin"}, False),
            ({"orgId": "org-1", "sessionId": "ses-2", "reason": "shared"}, False),
            ({"orgId": "org-2", "reason": "policy"}, False),
        ],
    )
    async def test_rc10_a_revocation_is_remembered_for_a_kept_sign_in(
        self, relay_harness: RelayHarness, *, message: dict[str, Any], ends: bool
    ) -> None:
        """A revocation nobody heard (the parking worker was down) still ends a kept sign-in made before it."""
        assert await relay_harness.relay.ended_since(RUN, OWNER, time.time() - 10) is False
        await relay_harness.revoke(message)
        assert await relay_harness.relay.ended_since(RUN, OWNER, time.time() - 10) is ends
        # A sign-in made well after the revocation is not ended by it.
        assert await relay_harness.relay.ended_since(RUN, OWNER, time.time() + 3_600) is False

    async def test_rc7_one_code_at_a_time(self, relay_harness: RelayHarness) -> None:
        async with relay_harness.relay.attempt(RUN, OWNER, "sin_1") as attempt:
            first = asyncio.ensure_future(relay_harness.ask("sin_1", _req("code", code="c#s1", workPlansOnly=True)))
            _ = await attempt.code(2)
            assert await relay_harness.ask("sin_1", _req("code", code="d#s1", workPlansOnly=True)) == {
                "ok": False, "reasonCode": "signin_code_invalid"}
            await attempt.reply(SignInReply("failed", "signin_failed"))
            assert (await first or {}).get("reasonCode") == "signin_failed"


__all__ = ["LINK", "OWNER", "RUN", "RelayHarness", "SignInRelayContract"]
