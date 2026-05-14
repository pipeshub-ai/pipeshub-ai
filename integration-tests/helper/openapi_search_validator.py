"""
Spec-driven response validator for the enterprise_search integration tests.

Loads `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml` once,
normalizes OpenAPI 3.0 `nullable: true` into valid JSON Schema **in place**,
and validates response bodies against the schema declared for a
`(path, method, status_code)` triple.

In-place mutation of the cached spec is deliberate. `jsonschema`'s
`RefResolver` returns `$ref`-resolved nodes straight from the document we
hand it, so any nullable rewriting we want applied to schemas reached
through a `$ref` must be visible in the cached document itself. Building
a separate translated copy doubles allocations on a ~18k-line YAML for no
benefit — nothing else in the test process consumes the raw spec.

Public entry point:

    assert_response_matches_spec(body, spec_path, method, status_code=200)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from jsonschema import RefResolver

_REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = (
    _REPO_ROOT
    / "backend"
    / "nodejs"
    / "apps"
    / "src"
    / "modules"
    / "api-docs"
    / "pipeshub-openapi.yaml"
)

_spec_cache: dict | None = None
_spec_lock = threading.Lock()
_MAX_VALUE_REPR_BYTES = 2048


def _normalize_in_place(node: Any) -> None:
    """Translate OpenAPI 3.0 `nullable: true` into JSON Schema, in place.

    - Type-only nullable: `{type: "string", nullable: true}` ->
      `{type: ["string", "null"]}`.
    - Nullable combined with `$ref` / `oneOf` / `anyOf` / `allOf` is rewritten
      to `{anyOf: [<original>, {type: "null"}]}`. Folding `null` into a `type`
      list would not work — there is no `type` to extend, and adding one
      would contradict the combinator.
    """
    if isinstance(node, dict):
        for value in node.values():
            _normalize_in_place(value)
        if node.pop("nullable", None) is not True:
            return
        if any(k in node for k in ("$ref", "oneOf", "anyOf", "allOf")):
            inner = dict(node)
            node.clear()
            node["anyOf"] = [inner, {"type": "null"}]
            return
        t = node.get("type")
        if isinstance(t, str) and t != "null":
            node["type"] = [t, "null"]
        elif isinstance(t, list) and "null" not in t:
            node["type"] = [*t, "null"]
        elif t is None:
            node["type"] = ["null"]
    elif isinstance(node, list):
        for item in node:
            _normalize_in_place(item)


def _load_spec() -> dict:
    """Parsed, normalized spec — cached, normalized once."""
    global _spec_cache
    if _spec_cache is not None:
        return _spec_cache
    with _spec_lock:
        if _spec_cache is None:
            if not SPEC_PATH.exists():
                raise FileNotFoundError(
                    f"OpenAPI spec not found at {SPEC_PATH}. "
                    "Make sure the backend source tree is checked out."
                )
            with SPEC_PATH.open(encoding="utf-8") as fh:
                _spec_cache = yaml.safe_load(fh)
            _normalize_in_place(_spec_cache)
    return _spec_cache


def _get_response_schema(spec_path: str, method: str, status_code: int) -> dict:
    spec = _load_spec()
    paths = spec.get("paths", {})
    if spec_path not in paths:
        raise KeyError(
            f"Path {spec_path!r} not found in OpenAPI spec at {SPEC_PATH}"
        )
    method_key = method.lower()
    path_item = paths[spec_path]
    if method_key not in path_item:
        declared = [k for k in path_item if k in {"get", "post", "put", "patch", "delete"}]
        raise KeyError(
            f"{method.upper()} not declared for {spec_path!r}. Declared: {declared}"
        )
    responses = path_item[method_key].get("responses", {})
    response = responses.get(str(status_code))
    if response is None:
        raise KeyError(
            f"HTTP {status_code} not declared for {method.upper()} {spec_path}. "
            f"Declared: {list(responses.keys())}"
        )
    content = response.get("content", {})
    json_block = content.get("application/json")
    if json_block is None:
        raise ValueError(
            f"No application/json response declared for "
            f"{method.upper()} {spec_path} -> {status_code}. "
            f"Content-types: {list(content.keys()) or '(none)'}"
        )
    schema = json_block.get("schema")
    if schema is None:
        raise ValueError(
            f"application/json block has no schema for "
            f"{method.upper()} {spec_path} -> {status_code}"
        )
    return schema


def _truncate_for_msg(value: Any) -> str:
    text = json.dumps(value, indent=2, default=str)
    if len(text.encode("utf-8")) > _MAX_VALUE_REPR_BYTES:
        return text[:_MAX_VALUE_REPR_BYTES] + "\n... (truncated)"
    return text


def assert_response_matches_spec(
    body: Any,
    spec_path: str,
    method: str,
    status_code: int = 200,
) -> None:
    """Assert `body` matches the OpenAPI response schema for the given route.

    `spec_path` is the path as it appears in `pipeshub-openapi.yaml` (no
    `/api/v1` prefix), e.g. `"/search"`.
    """
    schema = _get_response_schema(spec_path, method, status_code)
    spec = _load_spec()
    resolver = RefResolver(base_uri=SPEC_PATH.as_uri(), referrer=spec)

    try:
        jsonschema.validate(instance=body, schema=schema, resolver=resolver)
    except jsonschema.ValidationError as exc:
        location = " -> ".join(str(p) for p in exc.absolute_path) or "(root)"
        raise AssertionError(
            "Response does not match OpenAPI spec\n"
            f"  Route   : {method.upper()} {spec_path} -> HTTP {status_code}\n"
            f"  Location: {location}\n"
            f"  Error   : {exc.message}\n"
            f"  Value   : {_truncate_for_msg(exc.instance)}"
        ) from exc
    except jsonschema.SchemaError as exc:
        raise AssertionError(
            f"OpenAPI schema is itself invalid for "
            f"{method.upper()} {spec_path}: {exc.message}"
        ) from exc


def assert_matches_component_schema(value: Any, component_name: str) -> None:
    """Assert `value` matches `#/components/schemas/<component_name>`.

    Use this to validate individual array items (or sub-objects) against
    their declared component schema. Envelope-level validation already
    walks into arrays, but only when those arrays are non-empty — and it
    reports failures by absolute JSON path. Calling this per item makes
    the intent explicit ("every hit must match SemanticSearchHit") and
    surfaces the offending item's index in the error.
    """
    spec = _load_spec()
    schema = spec.get("components", {}).get("schemas", {}).get(component_name)
    if schema is None:
        raise KeyError(
            f"Component schema {component_name!r} not found in {SPEC_PATH}"
        )
    resolver = RefResolver(base_uri=SPEC_PATH.as_uri(), referrer=spec)

    try:
        jsonschema.validate(instance=value, schema=schema, resolver=resolver)
    except jsonschema.ValidationError as exc:
        location = " -> ".join(str(p) for p in exc.absolute_path) or "(root)"
        raise AssertionError(
            f"Value does not match #/components/schemas/{component_name}\n"
            f"  Location: {location}\n"
            f"  Error   : {exc.message}\n"
            f"  Value   : {_truncate_for_msg(exc.instance)}"
        ) from exc
    except jsonschema.SchemaError as exc:
        raise AssertionError(
            f"OpenAPI component schema {component_name!r} is itself invalid: "
            f"{exc.message}"
        ) from exc
