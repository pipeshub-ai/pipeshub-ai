"""Builds the HTTP request for one tool call from the model's arguments.

Parameter values are serialized the way OpenAPI's `style`/`explode` rules say. A path
parameter is percent-encoded whole, `/` included, so whatever the model sends stays one
path segment of the admin's base URL.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from app.agents.openapi.models import (
    CompiledOperation,
    OperationParameter,
    ParameterLocation,
)


class RequestBuildError(ValueError):
    """The arguments can't make a valid request. The message is for the model to correct."""


_DEFAULT_STYLE = {
    ParameterLocation.PATH: "simple",
    ParameterLocation.QUERY: "form",
    ParameterLocation.HEADER: "simple",
    ParameterLocation.COOKIE: "form",
}
_DELIMITERS = {"form": ",", "simple": ",", "spaceDelimited": " ", "pipeDelimited": "|"}


def build_request(op: CompiledOperation, arguments: Mapping[str, Any], base_url: str) -> httpx.Request:
    path = op.path
    query: list[tuple[str, str]] = []
    headers: dict[str, str] = {"Accept": "application/json"}
    cookies: list[str] = []

    for param in op.parameters:
        value = arguments.get(param.name)
        if value is None:
            if param.required:
                raise RequestBuildError(f"missing required argument '{param.name}'")
            continue
        if param.location is ParameterLocation.PATH:
            path = path.replace("{" + param.name + "}", _path_segment(param, value))
        elif param.location is ParameterLocation.QUERY:
            query.extend(_query_pairs(param, value))
        elif param.location is ParameterLocation.HEADER:
            headers[param.name] = _joined(param, value)
        else:
            cookies.append(f"{param.name}={quote(_joined(param, value), safe='')}")

    if "{" in path:
        raise RequestBuildError(f"the path {op.path} still has an unfilled parameter")
    if cookies:
        headers["Cookie"] = "; ".join(cookies)

    body = _body(op, arguments)
    return httpx.Request(
        op.method,
        base_url.rstrip("/") + path,
        params=query,
        headers=headers,
        **({"json": body} if body is not _NO_BODY else {}),
    )


_NO_BODY = object()


def _body(op: CompiledOperation, arguments: Mapping[str, Any]) -> Any:  # noqa: ANN401
    if op.body_argument is None:
        return _NO_BODY
    value = arguments.get(op.body_argument)
    if value is None:
        if op.body_required:
            raise RequestBuildError(f"missing required argument '{op.body_argument}'")
        return _NO_BODY
    if isinstance(value, str):
        # Models sometimes send the JSON body as a JSON string.
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        return parsed if isinstance(parsed, (dict, list)) else value
    return value


def _path_segment(param: OperationParameter, value: Any) -> str:  # noqa: ANN401
    text = _joined(param, value)
    if text in ("", ".", ".."):
        raise RequestBuildError(f"argument '{param.name}' can't be empty, '.' or '..'")
    return quote(text, safe="")


def _query_pairs(param: OperationParameter, value: Any) -> list[tuple[str, str]]:  # noqa: ANN401
    style = param.style or _DEFAULT_STYLE[param.location]
    explode = param.explode if param.explode is not None else style == "form"
    if isinstance(value, Mapping):
        if style == "deepObject":
            return [(f"{param.name}[{k}]", _scalar(v)) for k, v in value.items()]
        if explode:
            return [(str(k), _scalar(v)) for k, v in value.items()]
        return [(param.name, ",".join(f"{k},{_scalar(v)}" for k, v in value.items()))]
    if isinstance(value, (list, tuple)):
        if explode and style == "form":
            return [(param.name, _scalar(item)) for item in value]
        return [(param.name, _DELIMITERS.get(style, ",").join(_scalar(item) for item in value))]
    return [(param.name, _scalar(value))]


def _joined(param: OperationParameter, value: Any) -> str:  # noqa: ANN401
    """Simple-style serialization, used for path, header and cookie values."""
    if isinstance(value, Mapping):
        separator = "=" if param.explode else ","
        return ",".join(f"{k}{separator}{_scalar(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return ",".join(_scalar(item) for item in value)
    return _scalar(value)


def _scalar(value: Any) -> str:  # noqa: ANN401
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)
