"""Unit tests for app.sources.client.http.http_response.HTTPResponse."""

from unittest.mock import MagicMock, PropertyMock

import pytest

from app.sources.client.http.http_response import HTTPResponse


def _make_mock_response(
    status_code=200,
    headers=None,
    url="https://api.example.com/items",
    text="hello",
    content=b"hello",
    json_data=None,
):
    """Build a mock httpx.Response with the given attributes."""
    mock = MagicMock()
    mock.status_code = status_code

    if headers is None:
        headers = {"content-type": "application/json; charset=utf-8"}
    mock.headers = headers
    mock.url = url
    mock.text = text
    mock.content = content
    mock.json.return_value = json_data if json_data is not None else {}
    return mock


# ---------------------------------------------------------------------------
# status property
# ---------------------------------------------------------------------------
class TestStatus:
    def test_status_200(self):
        resp = HTTPResponse(_make_mock_response(status_code=200))
        assert resp.status == 200

    def test_status_404(self):
        resp = HTTPResponse(_make_mock_response(status_code=404))
        assert resp.status == 404

    def test_status_500(self):
        resp = HTTPResponse(_make_mock_response(status_code=500))
        assert resp.status == 500


# ---------------------------------------------------------------------------
# headers property
# ---------------------------------------------------------------------------
class TestHeaders:
    def test_returns_dict(self):
        resp = HTTPResponse(_make_mock_response(headers={"X-Custom": "val"}))
        result = resp.headers
        assert isinstance(result, dict)
        assert result["X-Custom"] == "val"

    def test_multiple_headers(self):
        resp = HTTPResponse(
            _make_mock_response(headers={"X-A": "1", "X-B": "2", "content-type": "text/plain"})
        )
        h = resp.headers
        assert h["X-A"] == "1"
        assert h["X-B"] == "2"
        assert h["content-type"] == "text/plain"


# ---------------------------------------------------------------------------
# url property
# ---------------------------------------------------------------------------
class TestUrl:
    def test_returns_string(self):
        resp = HTTPResponse(_make_mock_response(url="https://example.com/foo"))
        assert resp.url == "https://example.com/foo"

    def test_non_string_url_coerced(self):
        mock = _make_mock_response()
        mock.url = 12345  # something unusual
        resp = HTTPResponse(mock)
        assert resp.url == "12345"


# ---------------------------------------------------------------------------
# content_type property
# ---------------------------------------------------------------------------
class TestContentType:
    def test_json_with_charset(self):
        resp = HTTPResponse(
            _make_mock_response(headers={"content-type": "application/json; charset=utf-8"})
        )
        assert resp.content_type == "application/json"

    def test_plain_text(self):
        resp = HTTPResponse(
            _make_mock_response(headers={"content-type": "text/plain"})
        )
        assert resp.content_type == "text/plain"

    def test_no_content_type_header(self):
        resp = HTTPResponse(_make_mock_response(headers={}))
        assert resp.content_type == ""

    def test_content_type_with_spaces(self):
        resp = HTTPResponse(
            _make_mock_response(headers={"content-type": " application/octet-stream ; boundary=something"})
        )
        assert resp.content_type == "application/octet-stream"


# ---------------------------------------------------------------------------
# is_json property
# ---------------------------------------------------------------------------
class TestIsJson:
    def test_true_for_json(self):
        resp = HTTPResponse(
            _make_mock_response(headers={"content-type": "application/json; charset=utf-8"})
        )
        assert resp.is_json is True

    def test_false_for_text(self):
        resp = HTTPResponse(
            _make_mock_response(headers={"content-type": "text/html"})
        )
        assert resp.is_json is False

    def test_false_for_missing(self):
        resp = HTTPResponse(_make_mock_response(headers={}))
        assert resp.is_json is False


# ---------------------------------------------------------------------------
# is_binary property
# ---------------------------------------------------------------------------
class TestIsBinary:
    def test_true_for_octet_stream(self):
        resp = HTTPResponse(
            _make_mock_response(headers={"content-type": "application/octet-stream"})
        )
        assert resp.is_binary is True

    def test_false_for_json(self):
        resp = HTTPResponse(
            _make_mock_response(headers={"content-type": "application/json"})
        )
        assert resp.is_binary is False

    def test_false_for_missing(self):
        resp = HTTPResponse(_make_mock_response(headers={}))
        assert resp.is_binary is False


# ---------------------------------------------------------------------------
# json() method
# ---------------------------------------------------------------------------
class TestJsonMethod:
    def test_returns_parsed_json(self):
        resp = HTTPResponse(
            _make_mock_response(json_data={"key": "value", "num": 42})
        )
        result = resp.json()
        assert result == {"key": "value", "num": 42}

    def test_delegates_to_underlying_response(self):
        mock = _make_mock_response()
        resp = HTTPResponse(mock)
        resp.json()
        mock.json.assert_called_once()


# ---------------------------------------------------------------------------
# text() method
# ---------------------------------------------------------------------------
class TestTextMethod:
    def test_returns_text(self):
        resp = HTTPResponse(_make_mock_response(text="Hello, World!"))
        assert resp.text() == "Hello, World!"

    def test_empty_text(self):
        resp = HTTPResponse(_make_mock_response(text=""))
        assert resp.text() == ""


# ---------------------------------------------------------------------------
# bytes() method
# ---------------------------------------------------------------------------
class TestBytesMethod:
    def test_returns_bytes(self):
        resp = HTTPResponse(_make_mock_response(content=b"\x89PNG\r\n"))
        assert resp.bytes() == b"\x89PNG\r\n"

    def test_empty_content(self):
        resp = HTTPResponse(_make_mock_response(content=b""))
        assert resp.bytes() == b""


# ---------------------------------------------------------------------------
# raise_for_status()
# ---------------------------------------------------------------------------
class TestRaiseForStatus:
    def test_delegates_to_underlying(self):
        mock = _make_mock_response()
        resp = HTTPResponse(mock)
        resp.raise_for_status()
        mock.raise_for_status.assert_called_once()

    def test_propagates_exception(self):
        mock = _make_mock_response()
        mock.raise_for_status.side_effect = Exception("404 Not Found")
        resp = HTTPResponse(mock)
        with pytest.raises(Exception, match="404 Not Found"):
            resp.raise_for_status()
