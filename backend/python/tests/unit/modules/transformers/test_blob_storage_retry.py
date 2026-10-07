"""Transient storage failures are retried, briefly, and reported downstream."""
from __future__ import annotations

import errno
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


def _body_write_error(cause: OSError) -> aiohttp.ClientOSError:
    """Shaped like aiohttp's own: the wrapper, with the transport error as its cause."""
    error = aiohttp.ClientOSError(cause.errno, "Can not write request body for http://gw/upload")
    error.__cause__ = cause
    return error


# Causes aiohttp wraps while the body is still being written: a chunk refused
# by an already-closing transport, and a reset while a drain waited on unsent bytes.
_CLOSING_TRANSPORT = aiohttp.ClientConnectionResetError("Cannot write to closing transport")
_PEER_RESET = ConnectionResetError(errno.ECONNRESET, "Connection reset by peer")


def test_aiohttp_reports_a_body_write_failure_only_before_eof() -> None:
    """_request_body_not_delivered rests on this ordering in aiohttp.

    EOF is written after the block that reports "Can not write request body",
    so that error always means the body never fully left. An aiohttp upgrade
    that moved write_eof inside it would make resending an upload unsafe.
    """
    import inspect

    from aiohttp.client_reqrep import ClientRequest

    source = inspect.getsource(ClientRequest.write_bytes)
    assert source.index("Can not write request body") < source.index("write_eof")


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

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            _body_write_error(_CLOSING_TRANSPORT),
            _body_write_error(_PEER_RESET),
            # Surfaced by the connection instead of the body writer: only a send
            # can fail with EPIPE.
            aiohttp.ClientOSError(errno.EPIPE, "Broken pipe"),
        ],
        ids=["write-refused", "reset-during-write", "broken-pipe"],
    )
    async def test_a_body_that_never_left_is_resent_even_when_not_idempotent(self, feedback, error) -> None:
        """A pooled socket the server closed as we reused it dies mid-write.

        The server cannot have created a document from a body it never fully
        received, so the upload is safe to send again.
        """
        outcomes = [error, {"_id": "doc"}]

        async def attempt() -> object:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        assert await _blob()._with_storage_retry("upload", attempt, idempotent=False) == {"_id": "doc"}

    @pytest.mark.asyncio
    async def test_a_failure_after_the_body_left_is_not_resent(self, feedback) -> None:
        """A bare reset may come from reading the response: the upload may already exist."""
        calls = 0

        async def attempt() -> object:
            nonlocal calls
            calls += 1
            raise aiohttp.ClientOSError(errno.ECONNRESET, "Connection reset by peer")

        with pytest.raises(aiohttp.ClientOSError):
            await _blob()._with_storage_retry("upload", attempt, idempotent=False)
        assert calls == 1


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
    async def test_placeholder_creation_is_retried_under_one_idempotency_key(self, feedback) -> None:
        keys: list[str] = []
        responses = [_response(503), _response(200, {"_id": "d"})]

        @asynccontextmanager
        async def _post(url: str, *, json: dict, headers: dict) -> AsyncIterator[object]:
            keys.append(headers["Idempotency-Key"])
            yield responses.pop(0)

        session = MagicMock()
        session.post = _post

        assert await _blob()._create_placeholder(session, "http://gw/p", {}, {"Authorization": "t"}) == {"_id": "d"}
        assert len(keys) == 2 and keys[0] == keys[1]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "first_outcome",
        [
            # The server may already have stored it: only the key makes this safe.
            aiohttp.ClientOSError(errno.ECONNRESET, "Connection reset by peer"),
            aiohttp.ServerDisconnectedError(),
            # Our own first attempt is still storing it.
            _response(409),
        ],
        ids=["reset", "disconnected", "in-progress"],
    )
    async def test_local_record_upload_is_retried_under_one_idempotency_key(self, feedback, first_outcome) -> None:
        sent_forms: list[aiohttp.FormData] = []
        keys: list[str] = []
        outcomes: list[object] = [first_outcome, _response(200, {"_id": "doc-1"})]

        @asynccontextmanager
        async def _post(url: str, *, data: aiohttp.FormData, headers: dict) -> AsyncIterator[object]:
            sent_forms.append(data)
            keys.append(headers["Idempotency-Key"])
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            yield outcome

        session = MagicMock()
        session.post = _post
        blob = _blob()
        blob._get_auth_and_config = AsyncMock(return_value=({}, "http://gw", "local"))

        with patch.object(bs_mod, "get_shared_session", return_value=session):
            document_id, _ = await blob.save_record_to_storage("org-1", "r1", "vr-1", {"a": 1})

        assert document_id == "doc-1"
        assert len(keys) == 2 and keys[0] == keys[1], "one logical upload, one key"
        # A FormData can be sent only once; each attempt must build its own.
        assert sent_forms[0] is not sent_forms[1]

    @pytest.mark.asyncio
    async def test_each_logical_upload_gets_its_own_key(self, feedback) -> None:
        keys: list[str] = []

        @asynccontextmanager
        async def _post(url: str, *, data: aiohttp.FormData, headers: dict) -> AsyncIterator[object]:
            keys.append(headers["Idempotency-Key"])
            yield _response(200, {"_id": f"doc-{len(keys)}"})

        session = MagicMock()
        session.post = _post
        blob = _blob()
        blob._get_auth_and_config = AsyncMock(return_value=({}, "http://gw", "local"))

        with patch.object(bs_mod, "get_shared_session", return_value=session):
            await blob.save_record_to_storage("org-1", "r1", "vr-1", {"a": 1})
            await blob.save_record_to_storage("org-1", "r1", "vr-1", {"a": 1})

        assert len(set(keys)) == 2


def _local_upload_blob(responses: list[object]) -> tuple[BlobStorage, list[str], MagicMock]:
    """A local-storage BlobStorage whose POSTs answer with *responses*, in order."""
    keys: list[str] = []

    @asynccontextmanager
    async def _post(url: str, *, data: aiohttp.FormData, headers: dict) -> AsyncIterator[object]:
        keys.append(headers["Idempotency-Key"])
        yield responses.pop(0)

    session = MagicMock()
    session.post = _post
    blob = _blob()
    blob._get_auth_and_config = AsyncMock(return_value=({}, "http://gw", "local"))
    return blob, keys, session


async def _local_upload(blob: BlobStorage, kind: str) -> str | None:
    if kind == "record":
        return (await blob.save_record_to_storage("org-1", "r1", "vr-1", {"a": 1}))[0]
    if kind == "metadata":
        return await blob._create_metadata_document("org-1", "r1", "vr-1", {"a": 1})
    return (await blob.save_binary_to_storage("org-1", "r1", "f.pdf", "pdf", "application/pdf", b"%PDF"))[0]


class TestLocalUploadStatuses:
    """A storage gateway error on a local upload is retried like any other transient failure."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["record", "metadata", "binary"])
    async def test_a_gateway_503_is_retried_under_the_same_key(self, feedback, kind: str) -> None:
        blob, keys, session = _local_upload_blob([_response(503), _response(200, {"_id": "doc-1"})])

        with patch.object(bs_mod, "get_shared_session", return_value=session):
            assert await _local_upload(blob, kind) == "doc-1"

        assert len(keys) == 2 and keys[0] == keys[1]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["record", "metadata"])
    async def test_a_rejected_upload_fails_after_one_attempt(self, feedback, kind: str) -> None:
        blob, keys, session = _local_upload_blob([_response(400)])

        with patch.object(bs_mod, "get_shared_session", return_value=session), pytest.raises(
            aiohttp.ClientError, match="Failed to"
        ):
            await _local_upload(blob, kind)

        assert len(keys) == 1

    @pytest.mark.asyncio
    async def test_a_rejected_binary_upload_still_returns_nothing(self, feedback) -> None:
        blob, keys, session = _local_upload_blob([_response(400)])

        with patch.object(bs_mod, "get_shared_session", return_value=session):
            assert await blob.save_binary_to_storage(
                "org-1", "r1", "f.pdf", "pdf", "application/pdf", b"%PDF"
            ) == (None, None)

        assert len(keys) == 1
