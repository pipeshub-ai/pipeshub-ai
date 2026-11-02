import json
from typing import Any, Optional

import pytest

from app.agents.openapi.models import (
    CompiledOperation,
    OperationParameter,
    ParameterLocation,
)
from app.agents.openapi.request_builder import RequestBuildError, build_request

BASE = "https://api.acme.test/v2"


def _param(name: str, location: str, *, required: bool = False, style: Optional[str] = None,
           explode: Optional[bool] = None) -> OperationParameter:
    return OperationParameter(name=name, location=ParameterLocation(location), required=required, style=style, explode=explode)


def _op(path: str = "/items", method: str = "GET", params: tuple[OperationParameter, ...] = (),
        body_argument: Optional[str] = None, body_required: bool = False) -> CompiledOperation:
    return CompiledOperation(tool_name="t", method=method, path=path, parameters=list(params),
                             body_argument=body_argument, body_required=body_required, input_schema={"type": "object"})


def _query(request: Any) -> list[tuple[str, str]]:  # noqa: ANN401
    return list(request.url.params.multi_items())


class TestPath:
    def test_base_url_prefix_and_trailing_slash_are_kept_once(self) -> None:
        request = build_request(_op("/items"), {}, BASE + "/")
        assert str(request.url) == "https://api.acme.test/v2/items"

    def test_path_values_are_encoded_whole(self) -> None:
        op = _op("/tickets/{ticketId}/comments", params=(_param("ticketId", "path", required=True),))
        request = build_request(op, {"ticketId": "ACME-1/../../admin?x=1#y"}, BASE)
        assert request.url.raw_path.decode() == "/v2/tickets/ACME-1%2F..%2F..%2Fadmin%3Fx%3D1%23y/comments"
        assert request.url.host == "api.acme.test"

    @pytest.mark.parametrize("value", ["", ".", ".."])
    def test_dot_segments_and_empty_values_are_refused(self, value: str) -> None:
        op = _op("/tickets/{id}", params=(_param("id", "path", required=True),))
        with pytest.raises(RequestBuildError, match="can't be empty"):
            build_request(op, {"id": value}, BASE)

    def test_missing_path_value_is_refused(self) -> None:
        op = _op("/tickets/{id}", params=(_param("id", "path", required=True),))
        with pytest.raises(RequestBuildError, match="missing required argument 'id'"):
            build_request(op, {}, BASE)

    def test_numbers_and_booleans_in_the_path(self) -> None:
        op = _op("/a/{n}/{b}", params=(_param("n", "path", required=True), _param("b", "path", required=True)))
        assert build_request(op, {"n": 7, "b": False}, BASE).url.path == "/v2/a/7/false"

    def test_braces_in_a_value_do_not_count_as_unfilled(self) -> None:
        op = _op("/a/{x}", params=(_param("x", "path", required=True),))
        assert build_request(op, {"x": "{y}"}, BASE).url.raw_path.decode() == "/v2/a/%7By%7D"


class TestQuery:
    def test_arrays_explode_by_default(self) -> None:
        op = _op(params=(_param("tag", "query"),))
        assert _query(build_request(op, {"tag": ["a", "b"]}, BASE)) == [("tag", "a"), ("tag", "b")]

    @pytest.mark.parametrize(
        ("style", "explode", "expected"),
        [("form", False, "a,b"), ("spaceDelimited", None, "a b"), ("pipeDelimited", None, "a|b")],
    )
    def test_delimited_arrays(self, style: str, explode: Optional[bool], expected: str) -> None:
        op = _op(params=(_param("tag", "query", style=style, explode=explode),))
        assert _query(build_request(op, {"tag": ["a", "b"]}, BASE)) == [("tag", expected)]

    def test_objects(self) -> None:
        exploded = _op(params=(_param("f", "query"),))
        assert _query(build_request(exploded, {"f": {"a": 1, "b": True}}, BASE)) == [("a", "1"), ("b", "true")]
        deep = _op(params=(_param("f", "query", style="deepObject", explode=True),))
        assert _query(build_request(deep, {"f": {"a": 1}}, BASE)) == [("f[a]", "1")]
        flat = _op(params=(_param("f", "query", explode=False),))
        assert _query(build_request(flat, {"f": {"a": 1, "b": 2}}, BASE)) == [("f", "a,1,b,2")]

    def test_missing_optional_and_unknown_arguments_are_left_out(self) -> None:
        op = _op(params=(_param("limit", "query"),))
        request = build_request(op, {"limit": None, "surprise": 1}, BASE)
        assert _query(request) == []


class TestHeadersAndCookies:
    def test_header_and_cookie_parameters(self) -> None:
        op = _op(params=(_param("X-Trace", "header"), _param("session", "cookie"), _param("ids", "header")))
        request = build_request(op, {"X-Trace": "t1", "session": "a b", "ids": [1, 2]}, BASE)
        assert request.headers["X-Trace"] == "t1"
        assert request.headers["ids"] == "1,2"
        assert request.headers["Cookie"] == "session=a%20b"
        assert request.headers["Accept"] == "application/json"


class TestBody:
    def test_json_body(self) -> None:
        op = _op(method="POST", body_argument="body", body_required=True)
        request = build_request(op, {"body": {"title": "x", "tags": ["a"]}}, BASE)
        assert json.loads(request.content) == {"title": "x", "tags": ["a"]}
        assert request.headers["Content-Type"] == "application/json"

    def test_body_sent_as_a_json_string_is_parsed(self) -> None:
        op = _op(method="POST", body_argument="body")
        assert json.loads(build_request(op, {"body": '{"title": "x"}'}, BASE).content) == {"title": "x"}

    def test_plain_string_body_stays_a_string(self) -> None:
        op = _op(method="POST", body_argument="body")
        assert json.loads(build_request(op, {"body": "hello"}, BASE).content) == "hello"

    def test_required_body_missing(self) -> None:
        with pytest.raises(RequestBuildError, match="missing required argument 'body'"):
            build_request(_op(method="POST", body_argument="body", body_required=True), {}, BASE)

    def test_optional_body_missing_sends_no_body(self) -> None:
        request = build_request(_op(method="POST", body_argument="body"), {}, BASE)
        assert request.content == b"" and "Content-Type" not in request.headers
