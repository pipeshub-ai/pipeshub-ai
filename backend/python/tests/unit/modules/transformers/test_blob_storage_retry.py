"""Transient storage failures are retried, briefly, and reported downstream."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import BlobStorage, TransientStorageError
from app.services.resource_governor.feedback import (
    DownstreamFeedback,
    set_default_downstream_feedback,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


def _blob() -> BlobStorage:
    return BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=MagicMock())


@pytest.fixture(autouse=True)
def _no_sleep() -> Iterator[AsyncMock]:
    with patch.object(bs_mod.asyncio, "sleep", AsyncMock()) as sleep:
        yield sleep


@pytest.fixture
def feedback() -> Iterator[DownstreamFeedback]:
    fb = DownstreamFeedback()
    set_default_downstream_feedback(fb)
    yield fb
    set_default_downstream_feedback(None)


def _connect_error() -> aiohttp.ClientConnectorError:
    return aiohttp.ClientConnectorError(MagicMock(), OSError("refused"))


class TestWithStorageRetry:
    @pytest.mark.asyncio
    async def test_a_transient_failure_is_retried_then_succeeds(self, feedback) -> None:
        outcomes = [TransientStorageError("503"), aiohttp.ServerDisconnectedError(), "ok"]

        async def attempt() -> object:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        assert await _blob()._with_storage_retry("x", attempt) == "ok"
        assert feedback.drain().is_empty, "a blip that was retried away is not reported"

    @pytest.mark.asyncio
    async def test_gives_up_after_three_attempts_and_reports_unavailable(self, feedback, _no_sleep) -> None:
        calls = 0

        async def attempt() -> object:
            nonlocal calls
            calls += 1
            raise TransientStorageError("503")

        with pytest.raises(TransientStorageError):
            await _blob()._with_storage_retry("x", attempt)
        assert calls == 3
        assert _no_sleep.await_count == 2
        assert feedback.drain().unavailable == {"storage": 1}

    @pytest.mark.asyncio
    async def test_a_4xx_is_not_retried(self, feedback) -> None:
        calls = 0

        async def attempt() -> object:
            nonlocal calls
            calls += 1
            raise aiohttp.ClientError("Failed with status 404")

        with pytest.raises(aiohttp.ClientError):
            await _blob()._with_storage_retry("x", attempt)
        assert calls == 1
        assert feedback.drain().is_empty

    @pytest.mark.asyncio
    async def test_timeouts_are_retried_and_each_one_is_reported(self, feedback) -> None:
        outcomes = [TimeoutError(), "ok"]

        async def attempt() -> object:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        assert await _blob()._with_storage_retry("x", attempt) == "ok"
        assert feedback.drain().timeouts == {"storage": 1}

    @pytest.mark.asyncio
    async def test_a_non_idempotent_request_is_retried_only_before_it_was_sent(self, feedback) -> None:
        blob = _blob()
        for error in (aiohttp.ServerDisconnectedError(), TimeoutError(), TransientStorageError("503")):
            calls = 0

            async def attempt(error: Exception = error) -> object:
                nonlocal calls
                calls += 1
                raise error

            with pytest.raises(type(error)):
                await blob._with_storage_retry("placeholder", attempt, idempotent=False)
            assert calls == 1, type(error).__name__

        outcomes = [_connect_error(), {"id": "doc"}]

        async def attempt_connect() -> object:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        assert await blob._with_storage_retry("placeholder", attempt_connect, idempotent=False) == {"id": "doc"}


def _response(status: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=body or {})
    resp.text = AsyncMock(return_value="")
    return resp


def _session_returning(responses: list) -> MagicMock:
    session = MagicMock()

    @asynccontextmanager
    async def _request(*args: object, **kwargs: object) -> AsyncIterator[MagicMock]:
        yield responses.pop(0)

    session.get = _request
    session.post = _request
    session.put = _request
    return session


class TestRequestPathsRetry:
    @pytest.mark.asyncio
    async def test_record_fetch_retries_a_gateway_503(self, feedback) -> None:
        session = _session_returning([_response(503), _response(200, {"record": {"a": 1}})])
        data = await _blob()._fetch_record_envelope(session, "http://gw/x", {}, "vr-1")
        assert data == {"record": {"a": 1}}

    @pytest.mark.asyncio
    async def test_signed_url_fetch_retries_a_502(self, feedback) -> None:
        session = _session_returning([_response(502), _response(200, {"signedUrl": "s"})])
        assert await _blob()._get_signed_url(session, "http://gw/s", {}, {}) == {"signedUrl": "s"}

    @pytest.mark.asyncio
    async def test_signed_upload_retries_a_503(self, feedback) -> None:
        session = _session_returning([_response(503), _response(200)])
        assert await _blob()._upload_to_signed_url(session, "https://s3/k?sig=a%2Fb", {"x": 1}) == 200

    @pytest.mark.asyncio
    async def test_placeholder_creation_does_not_retry_a_503(self, feedback) -> None:
        session = _session_returning([_response(503), _response(200, {"id": "d"})])
        with pytest.raises(aiohttp.ClientError):
            await _blob()._create_placeholder(session, "http://gw/p", {}, {})
