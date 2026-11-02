import httpx
import openai
import pytest

from app.utils.openai_hosts import (
    is_openai_api_host,
    is_openai_api_url,
    openai_wrong_host,
)

_WRONG_HOST = {
    "message": "Attempted to access resource with incorrect regional hostname. "
               "Please make your request to api.openai.com",
    "type": "invalid_request_error",
    "param": None,
    "code": "incorrect_hostname",
}


@pytest.mark.parametrize(("host", "expected"), [
    ("api.openai.com", True),
    ("us.api.openai.com", True),
    ("EU.API.OPENAI.COM.", True),
    ("not-api.openai.com", False),
    ("evilapi.openai.com", False),
    ("api.openai.com.evil.example", False),
    ("openai.azure.com", False),
    ("", False),
    (None, False),
])
def test_is_openai_api_host(host: str | None, expected: bool) -> None:
    assert is_openai_api_host(host) is expected


@pytest.mark.parametrize(("url", "expected"), [
    ("https://us.api.openai.com/v1", True),
    ("https://api.openai.com", True),
    ("http://[::1", False),
    (None, False),
])
def test_is_openai_api_url(url: str | None, expected: bool) -> None:
    assert is_openai_api_url(url) is expected


def _sdk_error(body: dict) -> openai.AuthenticationError:
    request = httpx.Request("POST", "https://us.api.openai.com/v1/responses")
    return openai.AuthenticationError("Error code: 401", response=httpx.Response(401, request=request), body=body)


def test_wrong_host_from_the_sdk_error_body() -> None:
    assert openai_wrong_host(_sdk_error(_WRONG_HOST)) == "api.openai.com"


def test_wrong_host_from_a_wrapped_error() -> None:
    try:
        try:
            raise _sdk_error({"error": {**_WRONG_HOST, "message": _WRONG_HOST["message"].replace(
                "api.openai.com", "us.api.openai.com")}})
        except openai.AuthenticationError as inner:
            raise RuntimeError("model call failed") from inner
    except RuntimeError as outer:
        assert openai_wrong_host(outer) == "us.api.openai.com"


def test_wrong_host_from_a_json_payload() -> None:
    assert openai_wrong_host({"error": _WRONG_HOST}) == "api.openai.com"


def test_wrong_host_without_a_named_openai_host_is_empty() -> None:
    body = {**_WRONG_HOST, "message": "Please make your request to evil.example"}
    assert openai_wrong_host(_sdk_error(body)) == ""


def test_other_auth_errors_are_not_a_wrong_host() -> None:
    assert openai_wrong_host(_sdk_error({"code": "invalid_api_key", "message": "Incorrect API key"})) is None
    assert openai_wrong_host({"error": "nope"}) is None
    assert openai_wrong_host(None) is None
