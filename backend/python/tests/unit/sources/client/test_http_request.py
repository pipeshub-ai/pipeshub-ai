"""Unit tests for app.sources.client.http.http_request.HTTPRequest."""

import base64
import json

import pytest

from app.sources.client.http.http_request import HTTPRequest


# ---------------------------------------------------------------------------
# Construction / defaults
# ---------------------------------------------------------------------------
class TestHTTPRequestInit:
    def test_minimal_request(self):
        req = HTTPRequest(url="https://example.com")
        assert req.url == "https://example.com"
        assert req.method == "GET"
        assert req.headers == {}
        assert req.body is None
        assert req.path_params == {}
        assert req.query_params == {}

    def test_all_fields(self):
        req = HTTPRequest(
            url="https://example.com/{id}",
            method="POST",
            headers={"X-Custom": "val"},
            body={"key": "value"},
            path={"id": "42"},
            query={"page": "1"},
        )
        assert req.url == "https://example.com/{id}"
        assert req.method == "POST"
        assert req.headers == {"X-Custom": "val"}
        assert req.body == {"key": "value"}
        assert req.path_params == {"id": "42"}
        assert req.query_params == {"page": "1"}

    def test_bytes_body(self):
        req = HTTPRequest(url="https://example.com", body=b"binary data")
        assert req.body == b"binary data"

    def test_query_params_as_list_of_tuples(self):
        req = HTTPRequest(
            url="https://example.com",
            query=[("key", "val1"), ("key", "val2")],
        )
        assert req.query_params == [("key", "val1"), ("key", "val2")]

    def test_method_defaults_to_get(self):
        req = HTTPRequest(url="https://example.com")
        assert req.method == "GET"

    def test_custom_method(self):
        req = HTTPRequest(url="https://example.com", method="PATCH")
        assert req.method == "PATCH"


# ---------------------------------------------------------------------------
# to_json - dict body
# ---------------------------------------------------------------------------
class TestToJsonDictBody:
    def test_dict_body_serialized(self):
        req = HTTPRequest(
            url="https://example.com",
            method="POST",
            body={"name": "test", "count": 3},
        )
        result = json.loads(req.to_json())
        assert result["body"] == {"name": "test", "count": 3}

    def test_null_body(self):
        req = HTTPRequest(url="https://example.com")
        result = json.loads(req.to_json())
        assert result["body"] is None

    def test_url_preserved(self):
        req = HTTPRequest(url="https://api.example.com/v1/items")
        result = json.loads(req.to_json())
        assert result["url"] == "https://api.example.com/v1/items"

    def test_method_preserved(self):
        req = HTTPRequest(url="https://example.com", method="DELETE")
        result = json.loads(req.to_json())
        assert result["method"] == "DELETE"

    def test_headers_preserved(self):
        req = HTTPRequest(
            url="https://example.com",
            headers={"Accept": "application/json"},
        )
        result = json.loads(req.to_json())
        assert result["headers"] == {"Accept": "application/json"}

    def test_path_params_preserved_with_alias(self):
        req = HTTPRequest(url="https://example.com/{id}", path={"id": "99"})
        result = json.loads(req.to_json())
        assert result["path"] == {"id": "99"}

    def test_query_params_preserved_with_alias(self):
        req = HTTPRequest(url="https://example.com", query={"limit": "10"})
        result = json.loads(req.to_json())
        assert result["query"] == {"limit": "10"}


# ---------------------------------------------------------------------------
# to_json - bytes body (base64 encoding)
# ---------------------------------------------------------------------------
class TestToJsonBytesBody:
    def test_bytes_encoded_as_base64(self):
        raw = b"hello binary world"
        req = HTTPRequest(url="https://example.com", body=raw)
        result = json.loads(req.to_json())

        assert result["body"]["type"] == "bytes"
        assert result["body"]["encoding"] == "base64"
        decoded = base64.b64decode(result["body"]["data"])
        assert decoded == raw

    def test_empty_bytes(self):
        req = HTTPRequest(url="https://example.com", body=b"")
        result = json.loads(req.to_json())
        assert result["body"]["type"] == "bytes"
        decoded = base64.b64decode(result["body"]["data"])
        assert decoded == b""

    def test_binary_data_integrity(self):
        raw = bytes(range(256))
        req = HTTPRequest(url="https://example.com", body=raw)
        result = json.loads(req.to_json())
        decoded = base64.b64decode(result["body"]["data"])
        assert decoded == raw

    def test_to_json_returns_valid_json_string(self):
        req = HTTPRequest(url="https://example.com", body=b"\xff\xfe")
        raw_json = req.to_json()
        # Must be parseable
        parsed = json.loads(raw_json)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# to_json - output format
# ---------------------------------------------------------------------------
class TestToJsonFormat:
    def test_output_is_indented(self):
        req = HTTPRequest(url="https://example.com")
        raw = req.to_json()
        # json.dumps with indent=2 produces newlines
        assert "\n" in raw

    def test_roundtrip_all_fields(self):
        req = HTTPRequest(
            url="https://example.com/{id}",
            method="PUT",
            headers={"Content-Type": "application/json"},
            body={"data": [1, 2, 3]},
            path={"id": "7"},
            query={"verbose": "true"},
        )
        result = json.loads(req.to_json())
        assert result["url"] == "https://example.com/{id}"
        assert result["method"] == "PUT"
        assert result["headers"] == {"Content-Type": "application/json"}
        assert result["body"] == {"data": [1, 2, 3]}
        assert result["path"] == {"id": "7"}
        assert result["query"] == {"verbose": "true"}
