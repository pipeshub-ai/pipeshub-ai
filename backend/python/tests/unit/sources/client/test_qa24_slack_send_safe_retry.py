"""QA24-6 (QA24-F1): the Slack client never re-sends a write that may have reached Slack, and reports it.

A real HTTP server on loopback plays Slack: it reads each request whole, then either answers or closes the
connection without a response (the post landed, its acknowledgement did not). The client is the production
``SlackRESTClientViaToken`` pointed at it, called through ``SlackDataSource`` inside a tracked tool call.
"""

from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass, field
from http.client import RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, override
from urllib.error import URLError

import pytest

from app.sources.client.http.send_ambiguity import classify_urllib_failure
from app.sources.client.slack.send_safe_retry import slack_method_writes
from app.sources.client.slack.slack import SlackClient, SlackRESTClientViaToken
from app.sources.external.slack.slack import SlackDataSource
from harness_core.effects.ambiguity import track_sends

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass
class FakeSlack:
    """Counts each Web API method's requests; drops the answer to the first ``drop`` of them."""

    drop: int = 0
    received: list[str] = field(default_factory=list[str])

    def handler(self) -> type[BaseHTTPRequestHandler]:
        slack = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                _ = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                slack.received.append(self.path.rsplit("/", 1)[-1])
                if slack.drop > 0:
                    slack.drop -= 1
                    self.close_connection = True
                    return
                body = json.dumps({"ok": True, "channel": "C1", "ts": "1.0", "messages": []}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                _ = self.wfile.write(body)

            @override
            def log_message(self, format: str, *args: object) -> None:
                return

        return Handler


@pytest.fixture
def slack() -> Iterator[tuple[FakeSlack, str]]:
    fake = FakeSlack()
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_address[1]}/api/"
    finally:
        server.shutdown()
        server.server_close()


def _source(base_url: str) -> SlackDataSource:
    rest = SlackRESTClientViaToken("xoxb-qa24")
    rest.get_web_client().base_url = base_url
    return SlackDataSource(SlackClient(rest))


async def test_qa24_6_a_post_whose_answer_is_lost_is_sent_once_and_reported(slack: tuple[FakeSlack, str]) -> None:
    fake, url = slack
    fake.drop = 1
    with track_sends() as sends:
        response = await _source(url).chat_post_message(channel="C1", text="hi")  # pyright: ignore[reportUnknownMemberType]
    assert fake.received == ["chat.postMessage"]
    assert not response.success
    assert sends.ambiguous


async def test_qa24_6_a_read_whose_answer_is_lost_is_retried_as_before(slack: tuple[FakeSlack, str]) -> None:
    fake, url = slack
    fake.drop = 1
    with track_sends() as sends:
        response = await _source(url).conversations_history(channel="C1")  # pyright: ignore[reportUnknownMemberType]
    assert fake.received == ["conversations.history", "conversations.history"]
    assert response.success and not sends.ambiguous


async def test_qa24_6_a_post_that_never_left_is_retried_and_not_reported() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = f"http://127.0.0.1:{probe.getsockname()[1]}/api/"
    with track_sends() as sends:
        response = await _source(closed).chat_post_message(channel="C1", text="hi")  # pyright: ignore[reportUnknownMemberType]
    assert not response.success and not sends.ambiguous


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (URLError(ConnectionRefusedError()), "not_sent"),
        (URLError(socket.gaierror()), "not_sent"),
        (URLError("unknown url type: slack"), "not_sent"),
        (URLError(TimeoutError()), "maybe_sent"),
        (URLError(BrokenPipeError()), "maybe_sent"),
        (RemoteDisconnected("closed"), "maybe_sent"),
        (ConnectionResetError(), "maybe_sent"),
        (TimeoutError(), "maybe_sent"),
        (ValueError("bad json"), "definite"),
    ],
)
def test_qa24_6_urllib_failures(error: BaseException, expected: str) -> None:
    assert classify_urllib_failure(error, unsafe=True) == expected
    assert classify_urllib_failure(error, unsafe=False) == ("not_sent" if expected == "not_sent" else "definite")


@pytest.mark.parametrize(
    ("method", "writes"),
    [
        ("chat.postMessage", True),
        ("chat.update", True),
        ("conversations.open", True),
        ("reactions.add", True),
        ("files.upload", True),
        ("conversations.history", False),
        ("conversations.replies", False),
        ("conversations.list", False),
        ("users.lookupByEmail", False),
        ("chat.getPermalink", False),
        ("search.messages", False),
        ("auth.test", False),
    ],
)
def test_qa24_6_which_slack_methods_write(method: str, writes: bool) -> None:
    assert slack_method_writes(f"https://slack.com/api/{method}?x=1") is writes
