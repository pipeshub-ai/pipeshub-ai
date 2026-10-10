import pytest
from starlette.requests import Request

from app.config.redaction import reveal_requested


def _request(query: str, user: dict | None = None) -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [],
                       "query_string": query.encode()})
    request.state.user = user if user is not None else {"userId": "u1"}
    return request


def test_reveal_true_is_a_reveal() -> None:
    assert reveal_requested(_request("reveal=true")) is True


@pytest.mark.parametrize("query", [
    "", "reveal=false", "reveal=TRUE", "reveal=1", "reveal=yes", "reveal=",
    "reveal=false&reveal=true", "reveal=true&reveal=true", "reveal[]=true",
])
def test_anything_else_is_not_a_reveal(query: str) -> None:
    assert reveal_requested(_request(query)) is False


def test_oauth_app_tokens_cannot_reveal() -> None:
    assert reveal_requested(_request("reveal=true", {"userId": "u1", "isOAuth": True})) is False
