"""`InProcessRunCancellationRegistry` (`app/agents/agent_loop/cancellation/
in_process.py`) — the single-worker `RunCancellationRegistry` implementation,
and the same local-hit path `KVBackedRunCancellationRegistry` composes."""

from __future__ import annotations

from app.agent_loop_lib.core.context import CancellationToken
from app.agents.agent_loop.cancellation.in_process import (
    InProcessRunCancellationRegistry,
)
from app.agents.agent_loop.cancellation.registry import RunOwner


def _owner(user_id: str = "user-1", org_id: str = "org-1") -> RunOwner:
    return RunOwner(user_id=user_id, org_id=org_id, conversation_id="conv-1")


class TestIsActive:
    async def test_false_before_register(self) -> None:
        registry = InProcessRunCancellationRegistry()
        assert await registry.is_active("run-1") is False

    async def test_true_after_register(self) -> None:
        registry = InProcessRunCancellationRegistry()
        await registry.register("run-1", CancellationToken(), _owner())
        assert await registry.is_active("run-1") is True

    async def test_false_after_unregister(self) -> None:
        registry = InProcessRunCancellationRegistry()
        await registry.register("run-1", CancellationToken(), _owner())
        await registry.unregister("run-1")
        assert await registry.is_active("run-1") is False


class TestCancel:
    async def test_matching_owner_cancels_the_token(self) -> None:
        registry = InProcessRunCancellationRegistry()
        token = CancellationToken()
        await registry.register("run-1", token, _owner())

        outcome = await registry.cancel("run-1", _owner())

        assert outcome == "cancelled"
        assert token.is_cancelled is True

    async def test_mismatched_user_id_is_forbidden_and_does_not_touch_the_token(self) -> None:
        registry = InProcessRunCancellationRegistry()
        token = CancellationToken()
        await registry.register("run-1", token, _owner(user_id="alice"))

        outcome = await registry.cancel("run-1", _owner(user_id="mallory"))

        assert outcome == "forbidden"
        assert token.is_cancelled is False

    async def test_mismatched_org_id_is_forbidden_even_with_the_same_user_id(self) -> None:
        registry = InProcessRunCancellationRegistry()
        token = CancellationToken()
        await registry.register("run-1", token, _owner(org_id="org-a"))

        outcome = await registry.cancel("run-1", _owner(org_id="org-b"))

        assert outcome == "forbidden"
        assert token.is_cancelled is False

    async def test_unknown_run_id_is_not_found(self) -> None:
        registry = InProcessRunCancellationRegistry()
        outcome = await registry.cancel("never-registered", _owner())
        assert outcome == "not_found"

    async def test_a_run_id_reused_after_unregister_is_not_found(self) -> None:
        registry = InProcessRunCancellationRegistry()
        await registry.register("run-1", CancellationToken(), _owner())
        await registry.unregister("run-1")

        outcome = await registry.cancel("run-1", _owner())

        assert outcome == "not_found"

    async def test_duplicate_register_for_the_same_run_id_overwrites_the_prior_entry(self) -> None:
        """Registering the same `run_id` twice (a client resending the same
        client-generated UUID) replaces the token — the route layer's
        `is_active()` 409 check is what's actually meant to prevent this in
        practice; this just documents last-write-wins at the registry
        level rather than raising."""
        registry = InProcessRunCancellationRegistry()
        first_token = CancellationToken()
        second_token = CancellationToken()
        await registry.register("run-1", first_token, _owner())
        await registry.register("run-1", second_token, _owner())

        outcome = await registry.cancel("run-1", _owner())

        assert outcome == "cancelled"
        assert first_token.is_cancelled is False
        assert second_token.is_cancelled is True
