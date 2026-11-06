"""A malformed chat or cancel request gets a 400 whose text names no field and echoes no value.
The reason stays in the server log, without the values sent."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.chatbot import _parse_chat_query, cancel_chat_stream

SECRET = "quarterly numbers for the board"
GENERIC = (400, "Invalid request parameters")


def _request(body: object) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    request.state.user = {"userId": "user-1", "orgId": "org-1"}
    return request


class _Registry:
    async def is_active(self, run_id: str) -> bool:
        return False


async def _refused(body: object) -> HTTPException:
    with pytest.raises(HTTPException) as err:
        await _parse_chat_query(_request(body), _Registry(), streaming=True)
    return err.value


class TestChatRequest:
    @pytest.mark.parametrize(
        "body",
        [
            {"query": SECRET, "limit": "not a number"},
            {"query": SECRET, "filters": SECRET},
            {"limit": 3, "conversationId": SECRET},
            [SECRET],
        ],
        ids=["bad-field", "wrong-type", "no-query", "not-an-object"],
    )
    async def test_the_400_is_generic_and_the_log_holds_no_value(
        self, body: object, caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="app.api.routes.chatbot"):
            err = await _refused(body)
        assert (err.status_code, err.detail) == GENERIC
        assert "Invalid request parameters" in caplog.text
        assert SECRET not in caplog.text

    async def test_the_log_names_the_field_that_failed(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="app.api.routes.chatbot"):
            await _refused({"query": SECRET, "limit": "not a number"})
        assert "'limit'" in caplog.text

    async def test_a_request_model_that_refuses_with_its_own_error_keeps_it(self) -> None:
        own = HTTPException(status_code=400, detail={"code": "SOME_CODE", "message": "Invalid request parameters"})
        extension = MagicMock()
        extension.chat_query_model.return_value = MagicMock(side_effect=own)
        with patch("app.api.routes.chatbot.get_chat_extension", return_value=extension):
            assert await _refused({"query": "q"}) is own


class TestCancelRequest:
    @pytest.mark.parametrize("body", [{"runId": "not-a-uuid"}, {}, {"runId": 7}], ids=["not-a-uuid", "no-run-id", "number"])
    async def test_the_400_is_generic(self, body: dict) -> None:
        with pytest.raises(HTTPException) as err:
            await cancel_chat_stream(_request(body), _Registry())
        assert (err.value.status_code, err.value.detail) == GENERIC
