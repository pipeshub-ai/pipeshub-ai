"""A signed-URL token in the query string never reaches uvicorn's access log, checked
against a real uvicorn server rather than a hand-built log record."""

from __future__ import annotations

import asyncio
import copy
import logging
import logging.config
import socket
from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from uvicorn.config import LOGGING_CONFIG

from app.utils.logger import AccessLogRedactionFilter

if TYPE_CHECKING:
    from collections.abc import Iterator

SENTINEL = "SENTINELTOKEN"
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error")
_STARTUP_TIMEOUT_SECONDS = 10
_SHUTDOWN_TIMEOUT_SECONDS = 5


async def _app(scope: dict, receive: object, send: object) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@contextmanager
def _logging_restored() -> Iterator[None]:
    """Undo what building a uvicorn.Config does to logging in the whole process.

    Its dictConfig would close every handler that exists, the service log files and
    pytest's own among them, so that step is skipped. What it does to loggers (new
    handlers and levels on uvicorn's, ``disabled`` cleared on every other, uvicorn's
    children reset) and the TRACE level name it registers are put back.

    Used inside the test rather than as a fixture: pytest attaches its capture handlers
    to every non-propagating logger for one phase at a time, and handlers saved during
    setup would be put back during teardown, where nothing removes them again.
    """
    for name in _UVICORN_LOGGERS:
        logging.getLogger(name)
    saved = {
        logger: (list(logger.handlers), list(logger.filters), logger.level, logger.propagate, logger.disabled)
        for logger in logging.root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    }
    try:
        with (
            patch.object(logging.config, "_clearExistingHandlers"),
            patch.dict(logging._levelToName),
            patch.dict(logging._nameToLevel),
        ):
            yield
    finally:
        for logger, (handlers, filters, level, propagate, disabled) in saved.items():
            logger.handlers[:] = handlers
            logger.filters[:] = filters
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = disabled


@pytest.fixture
def bound_socket() -> Iterator[socket.socket]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        yield sock
    finally:
        sock.close()


def _redaction_filters() -> list[logging.Filter]:
    # By name: test_logger reloads app.utils.logger, and each reload registers an
    # instance of a new class object that isinstance() against ours would miss.
    return [
        f
        for f in logging.getLogger("uvicorn.access").filters
        if type(f).__name__ == AccessLogRedactionFilter.__name__
    ]


def _config(http: str) -> uvicorn.Config:
    return uvicorn.Config(_app, http=http, ws="none", lifespan="off", log_config=copy.deepcopy(LOGGING_CONFIG))


async def _wait_until_started(server: uvicorn.Server, serving: asyncio.Task[None]) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if serving.done():
            error = None if serving.cancelled() else serving.exception()
            raise AssertionError(f"uvicorn exited before it started serving: {error!r}") from error
        if loop.time() > deadline:
            raise AssertionError(f"uvicorn did not start serving within {_STARTUP_TIMEOUT_SECONDS}s")
        await asyncio.sleep(0.01)


async def _stop(server: uvicorn.Server, serving: asyncio.Task[None]) -> None:
    """Ask uvicorn to exit and cancel it if it does not, so a stuck server cannot hold the test."""
    server.should_exit = True
    _, running = await asyncio.wait({serving}, timeout=_SHUTDOWN_TIMEOUT_SECONDS)
    if running:
        serving.cancel()
        await asyncio.wait({serving}, timeout=_SHUTDOWN_TIMEOUT_SECONDS)


async def _access_lines_for_one_request(http: str, sock: socket.socket, *, redact: bool) -> list[str]:
    """Serve one ``GET /x?token=…&keep=1`` and return what uvicorn.access logged for it."""
    with _logging_restored():
        config = _config(http)
        access_logger = logging.getLogger("uvicorn.access")
        if not redact:
            for redaction_filter in _redaction_filters():
                access_logger.removeFilter(redaction_filter)
        collector = _Collector()
        access_logger.addHandler(collector)

        port = sock.getsockname()[1]
        server = uvicorn.Server(config)
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            await _wait_until_started(server, serving)
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.get(f"http://127.0.0.1:{port}/x?token={SENTINEL}.a.b&keep=1")
            assert response.status_code == 200
        finally:
            await _stop(server, serving)
        assert serving.done() and not serving.cancelled(), "uvicorn did not shut down when asked to"
        serving.result()
    return collector.lines


def test_filter_survives_uvicorn_logging_config() -> None:
    assert _redaction_filters()

    with _logging_restored():
        _config("h11")

        assert _redaction_filters()


@pytest.mark.parametrize("http", ["h11", "httptools"])
async def test_real_request_token_not_in_access_log(http: str, bound_socket: socket.socket) -> None:
    if http == "httptools":
        pytest.importorskip("httptools")

    lines = await _access_lines_for_one_request(http, bound_socket, redact=True)

    assert len(lines) == 1
    assert SENTINEL not in lines[0]
    assert "/x" in lines[0]
    assert "keep=1" in lines[0]
    assert "token=[REDACTED]" in lines[0]


async def test_control_without_filter_logs_the_token(bound_socket: socket.socket) -> None:
    """Without the filter the same request does log the token, so the test above can see a leak."""
    lines = await _access_lines_for_one_request("h11", bound_socket, redact=False)

    assert len(lines) == 1
    assert f"token={SENTINEL}.a.b" in lines[0]
