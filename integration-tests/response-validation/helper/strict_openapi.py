"""Strict check of a live response against ``pipeshub-openapi.yaml``.

``assert_response_matches_openapi_operation`` fails only on what the spec
forbids, and the spec's object schemas allow extra properties. This check also
fails on what the spec leaves out: a route, status code, content type or
response field that the API returns and the spec does not describe.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from typing import Any

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
    doc: dict[str, Any], instance: Any, schema: Any, where: str, out: list[str]
) -> None:
    if len(out) >= _MAX_PROBLEMS:
        return
    parts = _flatten(doc, schema)
    if isinstance(instance, list):
        items = [p["items"] for p in parts if isinstance(p.get("items"), dict)]
        if items:
            for element in instance[:_MAX_ARRAY_ITEMS]:
                _undocumented(doc, element, {"anyOf": items}, f"{where}[]", out)
        return
    if not isinstance(instance, dict) or not instance:
        return
    properties: dict[str, Any] = {}
    extra: list[dict[str, Any]] = []
    is_open = False
    for part in parts:
        properties.update(part.get("properties") or {})
        additional = part.get("additionalProperties")
        if additional is True or isinstance(additional, dict):
            is_open = True
            if additional and additional is not True:
                extra.append(additional)
    if not properties and not is_open:
        _add(out, f"{where}: object is returned with fields {sorted(instance)[:8]} but the spec describes none")
        return
    for key, value in instance.items():
        if key in properties:
            _undocumented(doc, value, properties[key], f"{where}.{key}", out)
        elif extra:
            _undocumented(doc, value, {"anyOf": extra}, f"{where}.{key}", out)
        elif not is_open:
            _add(out, f"{where}.{key}: field is returned but is not in the spec")


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
