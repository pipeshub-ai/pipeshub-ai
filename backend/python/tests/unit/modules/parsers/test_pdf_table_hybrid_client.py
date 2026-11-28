"""The plain client of ``/table-structure``: one attempt, no loop on 429, nothing reported to the governor."""
from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Never

import httpx
import pytest

from app.modules.parsers.pdf.table_hybrid_client import TableStructureClient
from app.services.docling.table_structure_schema import ModelInfo, config_hash
from app.services.resource_governor import (
    get_default_downstream_feedback,
    set_default_downstream_feedback,
)
from app.services.resource_governor.feedback import DownstreamFeedback

if TYPE_CHECKING:
    from collections.abc import Iterator

OK_BODY = {
    "v": 1,
    "tables": [{"id": "a", "status": "ok", "n_rows": 1, "n_cols": 1, "cells": [{"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": [0]}]}],
    "model": ModelInfo(docling_ibm_models="4.0.4", mode="accurate", weights_sha256="w", config_sha256=config_hash("4.0.4", "accurate", "w")).model_dump(),
}
META = {"v": 1, "tables": [{"id": "a"}]}


@pytest.fixture(autouse=True)
def feedback() -> Iterator[DownstreamFeedback]:
    fb = DownstreamFeedback()
    set_default_downstream_feedback(fb)
    yield fb
    set_default_downstream_feedback(None)


def client(handler) -> tuple[TableStructureClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return TableStructureClient("http://svc", httpx.MockTransport(wrapped)), seen


async def send(c: TableStructureClient):  # noqa: ANN201
    return await c.post_batch(META, {"c0": b"png"}, deadline_ms=5000, timeout_s=5.0)


async def test_an_answer_is_parsed_and_the_deadline_header_is_sent() -> None:
    c, seen = client(lambda r: httpx.Response(200, json=OK_BODY))
    out = await send(c)
    assert out.kind == "ok" and out.response.tables[0].status == "ok"
    assert seen[0].headers["X-Table-Deadline-Ms"] == "5000" and seen[0].url.path == "/table-structure"


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (httpx.Response(429, headers={"Retry-After": "5"}, json={}), "service_busy"),
        (httpx.Response(404, json={}), "unsupported"),
        (httpx.Response(409, json={"errorCode": "mode_not_supported"}), "mode_fast"),
        (httpx.Response(409, json={"errorCode": "unsupported_workers"}), "unsupported"),
        (httpx.Response(500, json={}), "error"),
        (httpx.Response(503, json={}), "error"),
        (httpx.Response(200, json={"nonsense": True}), "error"),
        (httpx.Response(200, content=b"not json"), "error"),
    ],
)
async def test_every_answer_is_an_outcome_with_one_attempt_and_no_retry(response, kind) -> None:
    c, seen = client(lambda r: response)
    assert (await send(c)).kind == kind
    assert len(seen) == 1


async def test_a_429_is_not_retried_or_waited_on() -> None:
    c, seen = client(lambda r: httpx.Response(429, headers={"Retry-After": "30"}, json={}))
    import time

    t0 = time.monotonic()
    out = await send(c)
    assert out.kind == "service_busy" and len(seen) == 1 and time.monotonic() - t0 < 2


async def test_a_refused_connection_is_service_down_and_timeouts_are_timeouts() -> None:
    def refuse(request) -> Never:
        raise httpx.ConnectError("refused", request=request)

    def slow(request) -> Never:
        raise httpx.ReadTimeout("slow", request=request)

    c, seen = client(refuse)
    assert (await send(c)).kind == "service_down" and len(seen) == 1
    c, seen = client(slow)
    assert (await send(c)).kind == "timeout" and len(seen) == 1


@pytest.mark.parametrize("status", [429, 500, 503, 404])
async def test_nothing_reaches_the_downstream_feedback(feedback, status) -> None:
    c, _ = client(lambda r: httpx.Response(status, json={}))
    await send(c)

    def refuse(request) -> Never:
        raise httpx.ConnectError("refused", request=request)

    def slow(request) -> Never:
        raise httpx.ReadTimeout("slow", request=request)

    for h in (refuse, slow):
        c2, _ = client(h)
        await send(c2)
    assert get_default_downstream_feedback().drain().is_empty


def test_the_client_is_not_a_base_service_client() -> None:
    from app.services.base_client import BaseServiceClient

    assert not issubclass(TableStructureClient, BaseServiceClient)


async def test_negative_control_a_base_service_client_on_the_same_failures_does_report(feedback) -> None:
    """The assertion above can fail: the repo's ordinary client reports a refused connection to the governor's feedback."""
    from app.services.base_client import BaseServiceClient

    base = BaseServiceClient("http://127.0.0.1:9", service_name="docling", max_retries=0, read_timeout=0.5, connect_timeout=0.5, retry_delay=0.01)
    with contextlib.suppress(Exception):  # whichever error the base client raises for a refused connection
        await base._post_json("/table-structure", {"x": 1})
    assert not get_default_downstream_feedback().drain().is_empty
    plain = TableStructureClient("http://127.0.0.1:9")
    await plain.post_batch({"v": 1, "tables": []}, {}, deadline_ms=1000, timeout_s=0.5)
    assert get_default_downstream_feedback().drain().is_empty
