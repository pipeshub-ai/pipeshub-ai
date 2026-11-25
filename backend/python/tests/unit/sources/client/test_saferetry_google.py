"""SR-2 (QA24-O1): the Google clients never re-send a write that may have reached Google, and report it.

The service is the production ``googleapiclient`` build with OAuth credentials, wrapped by ``GoogleClient`` (which
installs the transport), pointed at a loopback fake; Gmail calls go through ``GoogleGmailDataSource`` as the Gmail
tool makes them (``execute(num_retries=3)`` on the datasource's executor thread).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from google.oauth2.credentials import Credentials  # type: ignore
from googleapiclient.discovery import build  # type: ignore
from googleapiclient.errors import HttpError  # type: ignore

from app.sources.client.google.google import GoogleClient
from app.sources.client.http.send_ambiguity import PossiblySentError
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource
from harness_core.effects.ambiguity import track_sends
from tests.unit.sources.client.saferetry_fakes import (
    CLOSE_AFTER,
    DROP,
    FakeHTTP,
    closed_port,
    http_fake,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

SEND = ("POST", "/gmail/v1/users/me/messages/send")
MESSAGE = {"raw": "aGk="}


@pytest.fixture
def google() -> Iterator[FakeHTTP]:
    with http_fake(id="m1", emailAddress="me@example.test") as fake:
        yield fake


def _service(address: str) -> object:
    service = build(
        "gmail",
        "v1",
        credentials=Credentials(token="qa-token"),
        client_options={"api_endpoint": f"http://{address}/"},
        static_discovery=True,
    )
    return GoogleClient(service).get_client()


def _gmail(fake: FakeHTTP) -> GoogleGmailDataSource:
    return GoogleGmailDataSource(_service(fake.address))  # type: ignore[arg-type]


async def test_sr_2_a_send_whose_answer_is_lost_is_sent_once_and_reported(google: FakeHTTP) -> None:
    google.plan = [DROP]
    with track_sends() as sends, pytest.raises(PossiblySentError):
        await _gmail(google).users_messages_send(userId="me", body=MESSAGE)
    assert google.writes() == [SEND]
    assert sends.ambiguous


@pytest.mark.parametrize("status", [504, 502, 503, 500])
async def test_sr_2_a_send_answered_5xx_is_sent_once_and_reported(google: FakeHTTP, status: int) -> None:
    google.plan = [status]
    with track_sends() as sends, pytest.raises(HttpError) as raised:
        await _gmail(google).users_messages_send(userId="me", body=MESSAGE)
    assert raised.value.resp.status == status
    assert google.writes() == [SEND]
    assert sends.ambiguous


async def test_sr_2_a_throttled_send_is_sent_again(google: FakeHTTP) -> None:
    google.plan = [429]
    request = _service(google.address).users().messages().send(userId="me", body=MESSAGE)  # type: ignore[attr-defined]
    waits: list[float] = []
    request._sleep = waits.append  # googleapiclient's backoff seam
    with track_sends() as sends:
        assert request.execute(num_retries=3)["id"] == "m1"
    assert google.writes() == [SEND, SEND]
    assert len(waits) == 1 and not sends.ambiguous


async def test_sr_2_a_send_after_the_server_closed_an_idle_connection_goes_out_once(google: FakeHTTP) -> None:
    gmail = _gmail(google)
    google.plan = [CLOSE_AFTER]
    _ = await gmail.users_get_profile(userId="me")
    assert google.idle_closed.wait(timeout=10)
    with track_sends() as sends:
        assert (await gmail.users_messages_send(userId="me", body=MESSAGE))["id"] == "m1"
    assert google.writes() == [SEND]
    assert not sends.ambiguous


async def test_sr_2_a_read_whose_answer_is_lost_is_retried_as_before(google: FakeHTTP) -> None:
    google.plan = [DROP]
    with track_sends() as sends:
        assert (await _gmail(google).users_get_profile(userId="me"))["emailAddress"] == "me@example.test"
    assert google.received == [("GET", "/gmail/v1/users/me/profile")] * 2
    assert not sends.ambiguous


def test_sr_2_a_send_that_never_left_is_not_reported() -> None:
    request = _service(f"127.0.0.1:{closed_port()}").users().messages().send(userId="me", body=MESSAGE)  # type: ignore[attr-defined]
    with track_sends() as sends, pytest.raises(ConnectionRefusedError):
        request.execute(num_retries=0)
    assert not sends.ambiguous
