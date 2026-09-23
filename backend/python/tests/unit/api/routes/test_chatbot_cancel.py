"""`POST /chat/cancel` (`cancel_chat_stream`, `app/api/routes/chatbot.py`) —
one endpoint for both assistant and agent runs (Stop Generation Phase 3a).
Called directly (bypassing FastAPI's `Depends`/scope wiring, matching
`test_chatbot_agent_loop_dispatch.py`'s convention) with a fake
`RunCancellationRegistry` so these tests exercise exactly the route's own
logic: JSON/`runId` validation, building `RunOwner` from `request.state.user`,
and mapping the registry's `CancelOutcome` to an HTTP response."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.agents.agent_loop.cancellation.registry import CancelOutcome, RunOwner


class _FakeRegistry:
    def __init__(self, outcome: CancelOutcome = "cancelled") -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, RunOwner]] = []

    async def is_active(self, run_id: str) -> bool:
        return False

    async def register(self, run_id: str, token: object, owner: RunOwner) -> None:
        raise NotImplementedError

    async def cancel(self, run_id: str, requester: RunOwner) -> CancelOutcome:
        self.calls.append((run_id, requester))
        return self.outcome

    async def unregister(self, run_id: str) -> None:
        raise NotImplementedError


def _mock_request(body: dict, user: dict | None = None) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    request.state.user = user if user is not None else {"userId": "user-1", "orgId": "org-1"}
    return request


class TestCancelChatStream:
    async def test_happy_path_returns_cancelled_true(self) -> None:
        from app.api.routes.chatbot import cancel_chat_stream

        run_id = str(uuid.uuid4())
        registry = _FakeRegistry(outcome="cancelled")
        request = _mock_request({"runId": run_id})

        result = await cancel_chat_stream(request, registry)

        assert result == {"cancelled": True}
        assert registry.calls == [(run_id, RunOwner(user_id="user-1", org_id="org-1"))]

    async def test_conversation_id_from_the_body_is_forwarded_into_run_owner(self) -> None:
        """Node's cancel routes forward the already-ownership-checked path
        conversationId — this must reach the registry's RunOwner so it can
        reject a runId registered under a different conversation."""
        from app.api.routes.chatbot import cancel_chat_stream

        run_id = str(uuid.uuid4())
        registry = _FakeRegistry(outcome="cancelled")
        request = _mock_request({"runId": run_id, "conversationId": "conv-1"})

        result = await cancel_chat_stream(request, registry)

        assert result == {"cancelled": True}
        assert registry.calls == [
            (run_id, RunOwner(user_id="user-1", org_id="org-1", conversation_id="conv-1"))
        ]

    async def test_missing_conversation_id_builds_a_run_owner_with_none(self) -> None:
        """An older/rolling-deploy Node build without this field must not
        raise — RunOwner.conversation_id defaults to None and the registry
        only enforces the check when both sides carry one."""
        from app.api.routes.chatbot import cancel_chat_stream

        run_id = str(uuid.uuid4())
        registry = _FakeRegistry(outcome="cancelled")
        request = _mock_request({"runId": run_id})

        result = await cancel_chat_stream(request, registry)

        assert result == {"cancelled": True}
        assert registry.calls == [(run_id, RunOwner(user_id="user-1", org_id="org-1"))]

    async def test_not_found_or_already_finished_returns_cancelled_false_not_a_4xx(self) -> None:
        from app.api.routes.chatbot import cancel_chat_stream

        registry = _FakeRegistry(outcome="not_found")
        request = _mock_request({"runId": str(uuid.uuid4())})

        result = await cancel_chat_stream(request, registry)

        assert result == {"cancelled": False}

    async def test_owner_mismatch_raises_403(self) -> None:
        from app.api.routes.chatbot import cancel_chat_stream

        registry = _FakeRegistry(outcome="forbidden")
        request = _mock_request({"runId": str(uuid.uuid4())})

        with pytest.raises(HTTPException) as exc_info:
            await cancel_chat_stream(request, registry)

        assert exc_info.value.status_code == 403

    async def test_invalid_json_body_raises_400(self) -> None:
        from app.api.routes.chatbot import cancel_chat_stream

        registry = _FakeRegistry()
        request = MagicMock()
        request.json = AsyncMock(side_effect=ValueError("bad json"))
        request.state.user = {"userId": "user-1", "orgId": "org-1"}

        with pytest.raises(HTTPException) as exc_info:
            await cancel_chat_stream(request, registry)

        assert exc_info.value.status_code == 400

    async def test_non_uuid_run_id_raises_400(self) -> None:
        from app.api.routes.chatbot import cancel_chat_stream

        registry = _FakeRegistry()
        request = _mock_request({"runId": "not-a-uuid"})

        with pytest.raises(HTTPException) as exc_info:
            await cancel_chat_stream(request, registry)

        assert exc_info.value.status_code == 400

    async def test_missing_run_id_raises_400(self) -> None:
        from app.api.routes.chatbot import cancel_chat_stream

        registry = _FakeRegistry()
        request = _mock_request({})

        with pytest.raises(HTTPException) as exc_info:
            await cancel_chat_stream(request, registry)

        assert exc_info.value.status_code == 400

    async def test_missing_user_state_builds_an_empty_owner_rather_than_raising(self) -> None:
        """`request.state.user` is set by auth middleware upstream of this
        route (never absent in production, given `require_scopes`), but the
        route defensively falls back to `{}` — confirm that path doesn't
        blow up and simply produces a `RunOwner` no real run was ever
        registered under (so `cancel()` naturally reports `not_found`)."""
        from app.api.routes.chatbot import cancel_chat_stream

        registry = _FakeRegistry(outcome="not_found")
        request = _mock_request({"runId": str(uuid.uuid4())}, user={})

        result = await cancel_chat_stream(request, registry)

        assert result == {"cancelled": False}
        assert registry.calls[0][1] == RunOwner(user_id="", org_id="")
