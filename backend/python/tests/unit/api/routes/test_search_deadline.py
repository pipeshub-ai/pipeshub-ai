"""POST /api/v1/search answers inside its deadline (SEARCH_DEADLINE_SECONDS), so
a caller that stops waiting (the MCP server, after 60 s by default) hears why
instead of timing out; and its log line says which step the time went to."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import app.edition_config  # noqa: F401
from app.api.routes.search import SEARCH_TIMED_OUT_MESSAGE, SearchQuery, search
from app.utils.request_deadline import seconds_left
from app.utils.stage_timer import span

pytestmark = pytest.mark.asyncio

_FOUND = {
    "searchResults": [], "records": [], "status": "success", "status_code": 200,
    "message": "Query processed successfully. Relevant records retrieved.",
}


def _request() -> tuple[MagicMock, MagicMock]:
    request = MagicMock()
    request.state.user = {"userId": "user1", "orgId": "org1"}
    logger = MagicMock()
    request.app.container.logger.return_value = logger
    return request, logger


async def _search(search_with_filters: Any) -> tuple[int, dict[str, Any], MagicMock]:  # noqa: ANN401
    retrieval = MagicMock()
    retrieval.search_with_filters = search_with_filters
    request, logger = _request()
    response = await search(
        request=request, body=SearchQuery(query="rollout", rewriteQuery=False),
        retrieval_service=retrieval, graph_provider=MagicMock(),
    )
    return response.status_code, json.loads(response.body), logger


def _timing_line(logger: MagicMock) -> str:
    for call in logger.info.call_args_list:
        if call.args and str(call.args[0]).startswith("⏱"):
            return call.args[0] % call.args[1:]
    raise AssertionError("no timing line logged")


async def test_a_search_past_its_deadline_answers_with_why(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_DEADLINE_SECONDS", "0.2")

    async def stuck(**_: Any) -> dict[str, Any]:  # noqa: ANN401
        await asyncio.Event().wait()
        return _FOUND

    started = time.monotonic()
    status, body, logger = await _search(stuck)

    assert time.monotonic() - started < 5
    assert status == 503
    assert body["status"] == "search_timed_out"
    assert body["message"] == SEARCH_TIMED_OUT_MESSAGE
    assert body["searchResults"] == []
    assert "timed_out=0.2s" in _timing_line(logger)


async def test_retrieval_sees_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_DEADLINE_SECONDS", "30")
    seen: list[float | None] = []

    async def found(**_: Any) -> dict[str, Any]:  # noqa: ANN401
        seen.append(seconds_left())
        return _FOUND

    status, _, _ = await _search(found)

    assert status == 200
    assert seen and seen[0] is not None and 0 < seen[0] <= 30


async def test_no_deadline_when_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_DEADLINE_SECONDS", "0")
    seen: list[float | None] = []

    async def found(**_: Any) -> dict[str, Any]:  # noqa: ANN401
        seen.append(seconds_left())
        return _FOUND

    status, _, _ = await _search(found)

    assert status == 200
    assert seen == [None]


@pytest.mark.parametrize("deadline", ["30", "0"])
async def test_a_steps_own_timeout_is_not_reported_as_the_deadline(
    monkeypatch: pytest.MonkeyPatch, deadline: str,
) -> None:
    monkeypatch.setenv("SEARCH_DEADLINE_SECONDS", deadline)

    async def times_out(**_: Any) -> dict[str, Any]:  # noqa: ANN401
        raise TimeoutError("the embedding call timed out")

    with pytest.raises(HTTPException) as raised:
        await _search(times_out)

    assert raised.value.status_code == 500


async def test_the_timing_line_says_what_retrieval_was_made_of(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PIPESHUB_CHAT_TIMING", raising=False)

    async def found(**_: Any) -> dict[str, Any]:  # noqa: ANN401
        with span("embed"):
            await asyncio.sleep(0)
        # A timeline's periods search side by side and add to the same names.
        async def one_vector_search() -> None:
            with span("vector"):
                await asyncio.sleep(0)

        await asyncio.gather(one_vector_search(), one_vector_search())
        return _FOUND

    _, _, logger = await _search(found)

    line = _timing_line(logger)
    assert "retrieval=" in line
    assert "(embed=" in line
    assert "vector=" in line and "ms/2" in line


def test_the_timed_out_sentence_is_one_node_passes_on() -> None:
    # Node keeps a 503's message only when READER_WRITTEN_MESSAGES lists it word
    # for word; otherwise the caller is told something generic instead.
    from pathlib import Path

    allowlist = Path(__file__).resolve().parents[5] / "nodejs/apps/src/libs/errors/reader-friendly.ts"
    assert SEARCH_TIMED_OUT_MESSAGE in allowlist.read_text(encoding="utf-8")
