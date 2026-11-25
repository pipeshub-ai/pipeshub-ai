"""SR-4 (QA24-O3): the GitHub client never re-sends a write that may have reached GitHub, and reports it.

The SDK is the production ``GitHubClientViaToken`` (PyGithub over urllib3 with the client's retry), its base URL a
loopback fake.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from github import GithubException
from requests.exceptions import ConnectionError as RequestsConnectionError
from urllib3.exceptions import NewConnectionError, ProtocolError
from urllib3.util.retry import Retry

from app.sources.client.github.github import GitHubClientViaToken
from app.sources.client.http.send_safe_urllib3 import SendSafeRetry
from harness_core.effects.ambiguity import track_sends
from tests.unit.sources.client.saferetry_fakes import DROP, FakeHTTP, http_fake

if TYPE_CHECKING:
    from collections.abc import Iterator

ISSUES = ("POST", "/repos/o/r/issues")
CONTENTS = ("PUT", "/repos/o/r/contents/notes.md")


@pytest.fixture
def github() -> Iterator[FakeHTTP]:
    with http_fake(id=1, number=1, title="t", content={"path": "notes.md"}, commit={"sha": "c1"}) as fake:
        yield fake


def repo(address: str) -> Any:  # noqa: ANN401 - PyGithub's Repository
    client = GitHubClientViaToken("qa-token", base_url=f"http://{address}")
    client.create_client()
    return client.get_sdk().get_repo("o/r", lazy=True)


@pytest.mark.parametrize(
    ("write", "sent"),
    [
        (lambda r: r.create_issue(title="t"), ISSUES),
        (lambda r: r.create_file("notes.md", "add notes", "hi"), CONTENTS),
    ],
    ids=["post", "put"],
)
def test_sr_4_a_write_whose_answer_is_lost_is_sent_once_and_reported(
    github: FakeHTTP, write: Any, sent: tuple[str, str]  # noqa: ANN401
) -> None:
    github.plan = [DROP]
    with track_sends() as sends, pytest.raises(RequestsConnectionError):
        write(repo(github.address))
    assert github.writes() == [sent]
    assert sends.ambiguous


def test_sr_4_a_put_answered_502_is_sent_once_and_reported(github: FakeHTTP) -> None:
    github.plan = [502]
    with track_sends() as sends, pytest.raises(GithubException) as raised:
        repo(github.address).create_file("notes.md", "add notes", "hi")
    assert raised.value.status == 502
    assert github.writes() == [CONTENTS]
    assert sends.ambiguous


def test_sr_4_a_read_whose_answer_is_lost_is_retried_as_before(github: FakeHTTP) -> None:
    github.plan = [DROP]
    with track_sends() as sends:
        assert repo(github.address).get_issue(1).number == 1
    assert github.received == [("GET", "/repos/o/r/issues/1")] * 2
    assert not sends.ambiguous


def test_sr_4_urllib3_decides_by_what_left_the_host() -> None:
    retry = SendSafeRetry(total=3)
    refused = NewConnectionError(None, "refused")  # type: ignore[arg-type]
    reset = ProtocolError("Connection aborted.", ConnectionResetError())
    with track_sends() as sends:
        assert isinstance(retry.increment("POST", "/x", error=refused), Retry)
        assert isinstance(retry.increment("GET", "/x", error=reset), Retry)
    assert not sends.ambiguous
    with track_sends() as sends, pytest.raises(ProtocolError):
        _ = retry.increment("DELETE", "/x", error=reset)
    assert sends.ambiguous
