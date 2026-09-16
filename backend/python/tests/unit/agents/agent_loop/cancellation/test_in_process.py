"""`InProcessRunCancellationRegistry` (`app/agents/agent_loop/cancellation/
in_process.py`) — the single-worker `RunCancellationRegistry` implementation,
and the same local-hit path `KVBackedRunCancellationRegistry` composes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.context import CancellationToken
from app.agents.agent_loop.cancellation.in_process import (
    InProcessRunCancellationRegistry,
)
from app.agents.agent_loop.cancellation.registry import RunOwner

if TYPE_CHECKING:
    import pytest


def _owner(
    user_id: str = "user-1", org_id: str = "org-1", conversation_id: str | None = "conv-1"
) -> RunOwner:
    return RunOwner(user_id=user_id, org_id=org_id, conversation_id=conversation_id)


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

    async def test_mismatched_conversation_id_is_forbidden_even_for_the_same_user_and_org(
        self,
    ) -> None:
        """Guards against a same-user, cross-conversation cancel: a user who
        owns both conversations A and B must not be able to cancel B's run
        through a cancel request Node scoped to conversation A just by
        supplying B's runId (see RunOwner's docstring)."""
        registry = InProcessRunCancellationRegistry()
        token = CancellationToken()
        await registry.register("run-1", token, _owner(conversation_id="conv-B"))

        outcome = await registry.cancel("run-1", _owner(conversation_id="conv-A"))

        assert outcome == "forbidden"
        assert token.is_cancelled is False

    async def test_matching_conversation_id_cancels_the_token(self) -> None:
        registry = InProcessRunCancellationRegistry()
        token = CancellationToken()
        await registry.register("run-1", token, _owner(conversation_id="conv-1"))

        outcome = await registry.cancel("run-1", _owner(conversation_id="conv-1"))

        assert outcome == "cancelled"
        assert token.is_cancelled is True

    async def test_missing_requester_conversation_id_falls_back_to_user_org_check(
        self,
    ) -> None:
        """An older/rolling-deploy Node build that doesn't yet send
        conversationId must not regress to a hard 403 — only enforced when
        BOTH sides carry a conversation_id."""
        registry = InProcessRunCancellationRegistry()
        token = CancellationToken()
        await registry.register("run-1", token, _owner(conversation_id="conv-1"))

        outcome = await registry.cancel("run-1", _owner(conversation_id=None))

        assert outcome == "cancelled"
        assert token.is_cancelled is True

    async def test_missing_owner_conversation_id_falls_back_to_user_org_check(self) -> None:
        """A run registered before a conversationId existed (a brand-new
        conversation's very first turn) must still be cancellable."""
        registry = InProcessRunCancellationRegistry()
        token = CancellationToken()
        await registry.register("run-1", token, _owner(conversation_id=None))

        outcome = await registry.cancel("run-1", _owner(conversation_id="conv-1"))

        assert outcome == "cancelled"
        assert token.is_cancelled is True

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

    async def test_duplicate_register_for_the_same_run_id_overwrites_the_prior_entry(
        self, caplog: "pytest.LogCaptureFixture",
    ) -> None:
        """Registering the same `run_id` twice (a client resending the same
        client-generated UUID) replaces the token — the route layer's
        `is_active()` 409 check is what's actually meant to prevent this in
        practice; this just documents last-write-wins at the registry
        level rather than raising. It must still be loud about it: silently
        overwriting a live entry means the FIRST run becomes uncancellable
        without any signal that this happened."""
        registry = InProcessRunCancellationRegistry()
        first_token = CancellationToken()
        second_token = CancellationToken()
        await registry.register("run-1", first_token, _owner())
        with caplog.at_level(logging.WARNING):
            await registry.register("run-1", second_token, _owner())

        outcome = await registry.cancel("run-1", _owner())

        assert outcome == "cancelled"
        assert first_token.is_cancelled is False
        assert second_token.is_cancelled is True
        assert any("already registered and still active" in r.message for r in caplog.records)

    async def test_first_register_for_a_run_id_does_not_warn(
        self, caplog: "pytest.LogCaptureFixture",
    ) -> None:
        registry = InProcessRunCancellationRegistry()
        with caplog.at_level(logging.WARNING):
            await registry.register("run-1", CancellationToken(), _owner())

        assert caplog.records == []
