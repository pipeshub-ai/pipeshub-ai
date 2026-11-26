"""SR-3 (QA24-O2): the Microsoft Graph clients never re-send a write that may have reached Graph, and report it.

The client is the production ``MSGraphClientWithDelegatedAuth`` (the adapter the agent toolsets get), its base URL
pointed at a loopback fake; a mail send is a ``POST``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from kiota_http.middleware.options import RetryHandlerOption  # type: ignore
from msgraph.generated.users.item.send_mail.send_mail_post_request_body import (  # type: ignore
    SendMailPostRequestBody,
)

from app.sources.client.microsoft.microsoft import MSGraphClientWithDelegatedAuth
from app.sources.client.microsoft.send_safe_middleware import SendSafeRetryHandler
from harness_core.effects.ambiguity import track_sends
from tests.unit.sources.client.saferetry_fakes import DROP, FakeHTTP, http_fake

if TYPE_CHECKING:
    from collections.abc import Iterator

SEND = ("POST", "/v1.0/users/u1/sendMail")


@pytest.fixture
def graph() -> Iterator[FakeHTTP]:
    with http_fake(id="m1") as fake:
        yield fake


def graph_client(address: str) -> Any:  # noqa: ANN401 - the SDK's untyped request builders
    client = MSGraphClientWithDelegatedAuth("h.e.s", "tenant", logging.getLogger("tests.saferetry")).client
    base = f"http://{address}/v1.0"
    client.request_adapter.base_url = base
    client.path_parameters["baseurl"] = base
    return client


async def send_mail(address: str) -> None:
    await graph_client(address).users.by_user_id("u1").send_mail.post(SendMailPostRequestBody())


async def test_sr_3_a_send_whose_answer_is_lost_is_sent_once_and_reported(graph: FakeHTTP) -> None:
    graph.plan = [DROP]
    with track_sends() as sends, pytest.raises(httpx.RemoteProtocolError):
        await send_mail(graph.address)
    assert graph.writes() == [SEND]
    assert sends.ambiguous


@pytest.mark.parametrize("status", [504, 503])
async def test_sr_3_a_send_answered_5xx_is_sent_once_and_reported(graph: FakeHTTP, status: int) -> None:
    graph.plan = [status]
    with track_sends() as sends, pytest.raises(Exception, match=str(status)):
        await send_mail(graph.address)
    assert graph.writes() == [SEND]
    assert sends.ambiguous


@pytest.mark.parametrize(
    ("method", "status", "retried"),
    [
        ("GET", 503, True),
        ("GET", 504, True),
        ("GET", 429, True),
        ("POST", 429, True),
        ("PATCH", 429, True),
        ("POST", 503, False),
        ("POST", 504, False),
        ("PATCH", 504, False),
        ("DELETE", 503, False),
        ("PUT", 504, False),
    ],
)
def test_sr_3_only_a_refused_write_is_retried(method: str, status: int, retried: bool) -> None:
    request = httpx.Request(method, "https://graph.microsoft.com/v1.0/me/messages", content=b"{}")
    response = httpx.Response(status, request=request)
    assert SendSafeRetryHandler().should_retry(request, RetryHandlerOption(), response) is retried
