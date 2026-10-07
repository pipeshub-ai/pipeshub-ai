"""Strict check of a live exchange against ``pipeshub-openapi.yaml``.

``assert_response_matches_openapi_operation`` fails only on what the spec
forbids, and the spec's object schemas allow extra properties. This check also
fails on what the spec leaves out: a route, status code, content type or
response field that the API returns and the spec does not describe.

The request side is judged by what the API did with it: a request the API
accepted must be one the spec allows, and a request the API's validator
rejected must be one the spec forbids.
"""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests
from jsonschema import Draft7Validator
from openapi_schema_validator import (
    SPEC_URI,
    _make_registry,
    _strip_nonvalidation_keys,
    adapt_openapi_nullable,
    load_openapi_document,
)
from referencing import Registry

_PARAM = re.compile(r":\w+|\{[^}/]+\}")
_API_PREFIX = "/api/v1"
_COMPOSITION_KEYS = ("allOf", "anyOf", "oneOf")
_MAX_PROBLEMS = 40
_MAX_ARRAY_ITEMS = 50


def adapt_document(doc: dict[str, Any]) -> dict[str, Any]:
    return adapt_openapi_nullable(_strip_nonvalidation_keys(copy.deepcopy(doc)))


@lru_cache(maxsize=1)
def _spec() -> tuple[dict[str, Any], Registry]:
    adapted = adapt_document(load_openapi_document())
    return adapted, _make_registry(adapted)


def _template_key(path: str) -> str:
    """Spec paths omit ``/api/v1`` and name their parameters differently from Express."""
    path = path.split("?", 1)[0]
    if path == _API_PREFIX or path.startswith(_API_PREFIX + "/"):
        path = path[len(_API_PREFIX):]
    return _PARAM.sub("{}", path).rstrip("/") or "/"


def _escape(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def _deref(doc: dict[str, Any], ref: str) -> Any:
    node: Any = doc
    for part in ref.removeprefix(SPEC_URI).removeprefix("#/").split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _flatten(doc: dict[str, Any], schema: Any) -> list[dict[str, Any]]:
    """Every schema that applies to an instance: ``$ref`` targets and composition branches."""
    out: list[dict[str, Any]] = []
    stack, seen = [schema], set()
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        ref = node.get("$ref")
        if isinstance(ref, str):
            if ref not in seen:
                seen.add(ref)
                stack.append(_deref(doc, ref))
            continue
        out.append(node)
        for key in _COMPOSITION_KEYS:
            stack.extend(node.get(key) or [])
    return out


def _undocumented(
    doc: dict[str, Any], instance: Any, schema: Any, where: str, out: list[str], what: str = "returned"
) -> None:
    if len(out) >= _MAX_PROBLEMS:
        return
    parts = _flatten(doc, schema)
    if isinstance(instance, list):
        items = [p["items"] for p in parts if isinstance(p.get("items"), dict)]
        if items:
            for element in instance[:_MAX_ARRAY_ITEMS]:
                _undocumented(doc, element, {"anyOf": items}, f"{where}[]", out, what)
        return
    if not isinstance(instance, dict) or not instance:
        return
    properties: dict[str, list[Any]] = {}
    extra: list[dict[str, Any]] = []
    is_open = False
    for part in parts:
        for key, described in (part.get("properties") or {}).items():
            properties.setdefault(key, []).append(described)
        additional = part.get("additionalProperties")
        if additional is True or isinstance(additional, dict):
            is_open = True
            if additional and additional is not True:
                extra.append(additional)
    if not properties and not is_open:
        _add(out, f"{where}: object is {what} with fields {sorted(instance)[:8]} but the spec describes none")
        return
    for key, value in instance.items():
        if key in properties:
            _undocumented(doc, value, {"anyOf": properties[key]}, f"{where}.{key}", out, what)
        elif extra:
            _undocumented(doc, value, {"anyOf": extra}, f"{where}.{key}", out, what)
        elif not is_open:
            _add(out, f"{where}.{key}: field is {what} but is not in the spec")


def _add(out: list[str], problem: str) -> None:
    if problem not in out and len(out) < _MAX_PROBLEMS:
        out.append(problem)


def find_operation(
    doc: dict[str, Any], method: str, path: str
) -> tuple[str, dict[str, Any]] | None:
    want = _template_key(path)
    for spec_path, item in (doc.get("paths") or {}).items():
        if isinstance(item, dict) and _template_key(spec_path) == want:
            operation = item.get(method.lower())
            if isinstance(operation, dict):
                return spec_path, operation
    return None


_EXPRESS_NO_ROUTE = re.compile(rb"<pre>Cannot (GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) [^<]*</pre>")


def _is_express_no_route_page(method: str, status: int, content_type: str, body: bytes) -> bool:
    """Express's own 404 page for a method and path no route handles: there is no operation to describe.

    The spec says so once, in ``info.description``; an operation that does exist must still answer as documented.
    """
    match = _EXPRESS_NO_ROUTE.search(body or b"")
    return (
        status == 404
        and content_type.split(";", 1)[0].strip().lower() == "text/html"
        and match is not None
        and match.group(1).decode().lower() == method.lower()
    )


def strict_response_problems(
    doc: dict[str, Any],
    registry: Registry,
    method: str,
    path: str,
    status: int,
    content_type: str,
    body: bytes,
) -> list[str]:
    """Everything the response shows that ``doc`` (already adapted) does not describe."""
    method = method.lower()
    found = find_operation(doc, method, path)
    if found is None:
        if _is_express_no_route_page(method, status, content_type, body):
            return []
        return [f"{method.upper()} {path} is not in the OpenAPI spec"]
    spec_path, operation = found
    label = f"{method.upper()} {spec_path} -> {status}"

    responses = operation.get("responses") or {}
    key = next((k for k in (str(status), f"{status // 100}XX", "default") if k in responses), None)
    if key is None:
        return [f"{label}: status is not documented (spec has {sorted(map(str, responses))})"]
    pointer = f"#/paths/{_escape(spec_path)}/{method}/responses/{key}"
    response = responses[key]
    if isinstance(response.get("$ref"), str):
        pointer = response["$ref"]
        response = _deref(doc, pointer)
    content = response.get("content") or {}

    media = content_type.split(";", 1)[0].strip().lower()
    if not body:
        if content:
            return [f"{label}: the spec documents a {sorted(content)} body but the response is empty"]
        return []
    if not content:
        return [f"{label}: a {media or 'untyped'} body is returned but the spec documents none"]
    media_key = next((k for k in content if k.lower() == media), None)
    if media_key is None:
        return [f"{label}: content type {media!r} is not documented (spec has {sorted(content)})"]
    if media != "application/json":
        return []
    if not isinstance(content[media_key], dict) or content[media_key].get("schema") is None:
        return [f"{label}: the spec has no schema for the JSON body"]
    try:
        data = json.loads(body)
    except ValueError:
        return [f"{label}: body is declared JSON but does not parse"]

    schema = {"$ref": f"{SPEC_URI}{pointer}/content/{_escape(media_key)}/schema"}
    problems: list[str] = []
    errors = sorted(Draft7Validator(schema, registry=registry).iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))
    for error in errors:
        at = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path)
        _add(problems, f"${at}: {error.message[:300]}")
    _undocumented(doc, data, content[media_key]["schema"], "$", problems)
    return [f"{label}: {p}" for p in problems]


def assert_strict_openapi_response(
    resp: requests.Response, path: str, *, method: str | None = None
) -> None:
    """Fail on anything in ``resp`` the spec does not describe.

    ``path`` is the route template, Express or OpenAPI style, with or without
    ``/api/v1`` (``/api/v1/teams/:teamId`` and ``/teams/{teamId}`` are the same).
    """
    doc, registry = _spec()
    problems = strict_response_problems(
        doc,
        registry,
        method or resp.request.method or "",
        path,
        resp.status_code,
        resp.headers.get("Content-Type", ""),
        resp.content,
    )
    if problems:
        raise AssertionError(f"{len(problems)} OpenAPI problem(s):\n" + "\n".join(problems))


_SCALARS = {"string", "integer", "number", "boolean"}
_JSON = "application/json"
_VALIDATION_CODE = "VALIDATION_ERROR"


def _schema_errors(registry: Registry, pointer: str, instance: Any) -> list[str]:
    errors = Draft7Validator({"$ref": f"{SPEC_URI}{pointer}"}, registry=registry).iter_errors(instance)
    out = []
    for error in sorted(errors, key=lambda e: list(map(str, e.absolute_path))):
        at = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path)
        out.append(f"{at}: {error.message[:300]}")
    return out


def _query_parameters(
    doc: dict[str, Any], spec_path: str, method: str, operation: dict[str, Any]
) -> dict[str, tuple[dict[str, Any], str]]:
    """Documented query parameters by name, each with a pointer to its schema."""
    base = f"#/paths/{_escape(spec_path)}"
    listed = [(f"{base}/parameters/{i}", p) for i, p in enumerate(doc["paths"][spec_path].get("parameters") or [])]
    listed += [(f"{base}/{method}/parameters/{i}", p) for i, p in enumerate(operation.get("parameters") or [])]
    out: dict[str, tuple[dict[str, Any], str]] = {}
    for pointer, param in listed:
        if isinstance(param.get("$ref"), str):
            pointer = param["$ref"].removeprefix(SPEC_URI)
            param = _deref(doc, pointer)
        if param.get("in") == "query":
            out[param["name"]] = (param, f"{pointer}/schema")
    return out


def _coerce(value: str, types: set[str]) -> Any:
    """A query string value as the documented type would read it."""
    if "boolean" in types and value in ("true", "false"):
        return value == "true"
    if "string" in types:
        return value
    if types & {"integer", "number"} and re.fullmatch(r"-?\d+", value):
        return int(value)
    if "number" in types and re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def _query_problems(
    doc: dict[str, Any], registry: Registry, documented: dict[str, tuple[dict[str, Any], str]], query: str
) -> tuple[list[str], list[str]]:
    """What the spec forbids in ``query``, and what the query has that the spec leaves out."""
    sent = parse_qs(query, keep_blank_values=True)
    forbidden: list[str] = []
    undocumented: list[str] = []
    for name, (param, _) in documented.items():
        if param.get("required") and name not in sent:
            forbidden.append(f"query.{name}: the spec says it is required")
    for name, values in sent.items():
        if name not in documented:
            undocumented.append(f"query.{name}: parameter is sent but is not in the spec")
            continue
        param, pointer = documented[name]
        types = {t for part in _flatten(doc, param.get("schema") or {}) for t in _as_list(part.get("type"))}
        if not types or not types <= _SCALARS | {"null"}:
            continue
        if len(values) != 1:
            forbidden.append(f"query.{name}: sent {len(values)} times but the spec describes a single value")
            continue
        if values[0] == "" and param.get("allowEmptyValue"):
            continue
        for error in _schema_errors(registry, pointer, _coerce(values[0], types)):
            forbidden.append(f"query.{name}{error}")
    return forbidden, undocumented


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else ([] if value is None else [value])


def _body_problems(
    doc: dict[str, Any], registry: Registry, spec_path: str, method: str, operation: dict[str, Any],
    content_type: str, body: bytes,
) -> tuple[list[str], list[str]]:
    """Same pair as ``_query_problems`` for a JSON request body; other media types are not inspected."""
    pointer = f"#/paths/{_escape(spec_path)}/{method}/requestBody"
    request_body = operation.get("requestBody") or {}
    if isinstance(request_body.get("$ref"), str):
        pointer = request_body["$ref"].removeprefix(SPEC_URI)
        request_body = _deref(doc, pointer)
    content = request_body.get("content") or {}
    media = content_type.split(";", 1)[0].strip().lower()
    if not body:
        return (["body: the spec says a request body is required"] if request_body.get("required") else []), []
    if media != _JSON:
        return [], []
    try:
        data = json.loads(body)
    except ValueError:
        return ["body: is not valid JSON"], []
    media_key = next((k for k in content if k.lower() == _JSON), None)
    if media_key is None:
        if data in ({}, [], None):
            return [], []
        return [], ["body: a JSON body is sent but the spec documents " + (f"only {sorted(content)}" if content else "no request body")]
    schema = content[media_key].get("schema") if isinstance(content[media_key], dict) else None
    if schema is None:
        return [], ["body: the spec has no schema for the JSON request body"]
    schema_pointer = f"{pointer}/content/{_escape(media_key)}/schema"
    undocumented: list[str] = []
    _undocumented(doc, data, schema, "body", undocumented, "sent")
    return [f"body{e}" for e in _schema_errors(registry, schema_pointer, data)], undocumented


def _rejected_fields(status: int, response_body: bytes) -> list[tuple[str, str]] | None:
    """Fields the Node request validator refused, or None when the response is not from it."""
    if status != 400:
        return None
    try:
        error = json.loads(response_body).get("error")
        if error.get("code") != _VALIDATION_CODE:
            return None
        return [(str(e.get("field", "")), str(e.get("message", ""))) for e in error["metadata"]["errors"]]
    except (ValueError, AttributeError, KeyError, TypeError):
        return None


def strict_request_problems(
    doc: dict[str, Any],
    registry: Registry,
    method: str,
    path: str,
    query: str,
    content_type: str,
    body: bytes,
    status: int,
    response_body: bytes,
) -> list[str]:
    """Where the spec's request contract disagrees with what the API did with this request."""
    method = method.lower()
    found = find_operation(doc, method, path)
    if found is None:
        return []
    spec_path, operation = found
    label = f"{method.upper()} {spec_path} request"
    query_forbidden, query_extra = _query_problems(doc, registry, _query_parameters(doc, spec_path, method, operation), query)
    body_forbidden, body_extra = _body_problems(doc, registry, spec_path, method, operation, content_type, body)

    if status < 400:
        problems = [f"{p} (the API accepted the request)" for p in query_forbidden + body_forbidden]
        problems += query_extra + body_extra
        return [f"{label}, accepted with {status}: {p}" for p in problems[:_MAX_PROBLEMS]]

    rejected = _rejected_fields(status, response_body)
    if not rejected:
        return []
    problems = []
    for field, message in rejected:
        part = field.split(".", 1)[0]
        if (part == "body" and not body_forbidden) or (part == "query" and not query_forbidden):
            problems.append(f"{field}: the API rejects it ({message[:160]}) but the spec allows this request")
    return [f"{label}, rejected with {status}: {p}" for p in problems[:_MAX_PROBLEMS]]


def request_problems_for(resp: requests.Response, path: str, *, method: str | None = None) -> list[str]:
    doc, registry = _spec()
    request = resp.request
    body = request.body
    if isinstance(body, str):
        body = body.encode()
    if not isinstance(body, bytes | type(None)):
        return []
    return strict_request_problems(
        doc,
        registry,
        method or request.method or "",
        path,
        urlsplit(request.url or "").query,
        request.headers.get("Content-Type", ""),
        body or b"",
        resp.status_code,
        resp.content,
    )


def assert_strict_openapi_request(
    resp: requests.Response, path: str, *, method: str | None = None
) -> None:
    """Fail when the spec's request contract disagrees with how the API treated ``resp.request``."""
    problems = request_problems_for(resp, path, method=method)
    if problems:
        raise AssertionError(f"{len(problems)} OpenAPI request problem(s):\n" + "\n".join(problems))


def assert_spec_forbids_request(
    resp: requests.Response, path: str, *, method: str | None = None
) -> None:
    """For a request the API refused without the Node validator's error: the spec must refuse it too.

    The gate only recognises ``400 VALIDATION_ERROR``. Where a route has no validator and answers a
    bad request some other way, this is how a test ties that refusal to the spec's request contract.
    """
    doc, registry = _spec()
    request = resp.request
    method = (method or request.method or "").lower()
    found = find_operation(doc, method, path)
    assert found is not None, f"{method.upper()} {path} is not in the OpenAPI spec"
    spec_path, operation = found
    body = request.body.encode() if isinstance(request.body, str) else request.body
    assert isinstance(body, bytes | type(None)), "the request body is a stream and cannot be inspected"
    query_forbidden, _ = _query_problems(
        doc, registry, _query_parameters(doc, spec_path, method, operation), urlsplit(request.url or "").query
    )
    body_forbidden, _ = _body_problems(
        doc, registry, spec_path, method, operation, request.headers.get("Content-Type", ""), body or b""
    )
    if not query_forbidden and not body_forbidden:
        raise AssertionError(
            f"{method.upper()} {spec_path}: the API refused this request with {resp.status_code} "
            "but the spec's parameters and request body schema allow it"
        )


def assert_strict_openapi_exchange(
    resp: requests.Response, path: str, *, method: str | None = None
) -> None:
    """Request and response of one call, both checked strictly."""
    doc, registry = _spec()
    request_side = [] if _request_contract_suspended else request_problems_for(resp, path, method=method)
    problems = request_side + strict_response_problems(
        doc,
        registry,
        method or resp.request.method or "",
        path,
        resp.status_code,
        resp.headers.get("Content-Type", ""),
        resp.content,
    )
    if problems:
        raise AssertionError(f"{len(problems)} OpenAPI problem(s):\n" + "\n".join(problems))


_MAX_GATED_BODY = 2_000_000
_request_contract_suspended = 0


@contextmanager
def outside_request_contract(reason: str) -> Iterator[None]:
    """Calls made inside send something the spec does not describe on purpose (``reason`` says why).

    Their responses are still checked; only the request side is left alone.
    """
    global _request_contract_suspended
    assert reason.strip(), "say why the request is outside the documented contract"
    _request_contract_suspended += 1
    try:
        yield
    finally:
        _request_contract_suspended -= 1


@lru_cache(maxsize=1)
def _path_matchers() -> list[tuple[re.Pattern[str], str]]:
    """Spec path templates as patterns, the most literal first so ``/x/me`` wins over ``/x/{id}``."""
    doc, _ = _spec()
    ranked = []
    for spec_path in doc.get("paths") or {}:
        pattern = re.sub(r"\\\{[^/]+?\\\}", "[^/]+", re.escape(spec_path))
        literal = sum(1 for segment in spec_path.split("/") if segment and not segment.startswith("{"))
        ranked.append((-literal, re.compile(f"^{pattern}/?$"), spec_path))
    return [(regex, spec_path) for _, regex, spec_path in sorted(ranked, key=lambda r: r[0])]


@lru_cache(maxsize=1)
def _catch_all_matchers() -> list[tuple[re.Pattern[str], str]]:
    """Path items marked ``x-catch-all: true``: their last parameter also matches deeper paths."""
    doc, _ = _spec()
    out = []
    for spec_path, item in (doc.get("paths") or {}).items():
        if isinstance(item, dict) and item.get("x-catch-all") is True and spec_path.endswith("}"):
            head = spec_path[: spec_path.rindex("{")]
            pattern = re.sub(r"\\\{[^/]+?\\\}", "[^/]+", re.escape(head))
            out.append((re.compile(f"^{pattern}.+$"), spec_path))
    return out


def _template_for(url_path: str) -> str | None:
    # Express collapses repeated slashes before routing: /knowledgeBase//permissions is GET /knowledgeBase/{kbId}.
    url_path = re.sub(r"/{2,}", "/", url_path)
    bare = (url_path.removeprefix(_API_PREFIX) if url_path.startswith(_API_PREFIX + "/") else url_path) or "/"
    for matchers in (_path_matchers(), _catch_all_matchers()):
        found = next((spec_path for regex, spec_path in matchers if regex.match(bare)), None)
        if found:
            return found
    return None


@contextmanager
def record_exchanges() -> Iterator[list[str]]:
    """Strict-check every call to the PipesHub API made inside, setup and cleanup calls included."""
    problems: list[str] = []
    base = urlsplit(os.environ.get("PIPESHUB_BASE_URL", "")).netloc
    original = requests.Session.send

    def send(session: requests.Session, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        response = original(session, request, **kwargs)
        url = urlsplit(request.url or "")
        if not base or url.netloc != base or kwargs.get("stream"):
            return response
        content_type = response.headers.get("Content-Type", "")
        if "text/event-stream" in content_type or len(response.content) > _MAX_GATED_BODY:
            return response
        doc, registry = _spec()
        method = request.method or ""
        path = _template_for(url.path) or url.path
        found = strict_response_problems(
            doc, registry, method, path, response.status_code, content_type, response.content
        )
        if not _request_contract_suspended:
            found += request_problems_for(response, path)
        for problem in found:
            _add(problems, problem)
        return response

    requests.Session.send = send  # type: ignore[method-assign]
    try:
        yield problems
    finally:
        requests.Session.send = original  # type: ignore[method-assign]
