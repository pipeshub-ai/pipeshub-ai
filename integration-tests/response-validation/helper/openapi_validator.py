"""
OpenAPI-based response validator for PipesHub integration tests.

Loads the canonical ``pipeshub-openapi.yaml`` spec once (cached) and validates
HTTP response bodies against the declared response schema for a given
path / method / status-code combination, with full ``$ref`` resolution.

Usage
-----
    from openapi_validator import assert_openapi_response

    # validates body against the spec's GET /conversations 200 schema
    assert_openapi_response(resp.json(), "/conversations", "GET")

The spec path is written exactly as it appears in the YAML (after the ``/api/v1``
server prefix).  The actual HTTP URL you call is::

    {PIPESHUB_BASE_URL}/api/v1{spec_path}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
import jsonschema
from jsonschema import RefResolver

# ---------------------------------------------------------------------------
# Locate the spec relative to this file
#
# Layout:
#   integration-tests/response-validation/helper/openapi_validator.py
#                                                        ↑ this file
#   integration-tests/                        ← parents[2]
#   <repo-root>/                              ← parents[3]
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
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


def _load_spec() -> dict:
    global _spec_cache
    if _spec_cache is None:
        if not SPEC_PATH.exists():
            raise FileNotFoundError(
                f"OpenAPI spec not found at:\n  {SPEC_PATH}\n"
                "Make sure the backend source tree is checked out."
            )
        with open(SPEC_PATH, encoding="utf-8") as fh:
            _spec_cache = yaml.safe_load(fh)
    return _spec_cache


def get_response_schema(openapi_path: str, method: str, status_code: int = 200) -> dict:
    """
    Return the JSON Schema for the given path / method / status_code.

    Raises ``KeyError`` if the combination is not declared in the spec.
    Raises ``ValueError`` if the response has no application/json content.
    """
    spec = _load_spec()
    paths = spec.get("paths", {})

    if openapi_path not in paths:
        raise KeyError(
            f"Path {openapi_path!r} not found in spec. "
            f"Sample paths: {list(paths.keys())[:5]}"
        )

    path_item = paths[openapi_path]
    method_key = method.lower()
    if method_key not in path_item:
        available = [k for k in path_item if k in {"get", "post", "put", "patch", "delete"}]
        raise KeyError(
            f"{method.upper()} not declared for {openapi_path!r}. "
            f"Declared methods: {available}"
        )

    responses = path_item[method_key].get("responses", {})
    response = responses.get(str(status_code))
    if response is None:
        raise KeyError(
            f"HTTP {status_code} not declared for {method.upper()} {openapi_path}. "
            f"Declared: {list(responses.keys())}"
        )

    # resolve top-level $ref on the response object (rare but valid)
    if "$ref" in response:
        ref_parts = response["$ref"].lstrip("#/").split("/")
        node: Any = spec
        for part in ref_parts:
            node = node[part]
        response = node

    content = response.get("content", {})
    json_block = content.get("application/json", {})
    schema = json_block.get("schema")
    if schema is None:
        raise ValueError(
            f"No application/json schema for "
            f"{method.upper()} {openapi_path} → HTTP {status_code}. "
            f"Content-types declared: {list(content.keys()) or '(none)'}"
        )

    return schema


def assert_openapi_response(
    body: Any,
    openapi_path: str,
    method: str,
    status_code: int = 200,
) -> None:
    """
    Assert that *body* matches the OpenAPI spec's response schema for the
    given path / method / status_code.

    All ``$ref`` pointers are resolved against the spec file URI so that
    ``#/components/schemas/...`` references work correctly.

    Raises ``AssertionError`` with a human-readable diff on failure.
    """
    schema = get_response_schema(openapi_path, method, status_code)
    spec = _load_spec()
    resolver = RefResolver(base_uri=SPEC_PATH.as_uri(), referrer=spec)

    try:
        jsonschema.validate(instance=body, schema=schema, resolver=resolver)
    except jsonschema.ValidationError as exc:
        field = " → ".join(str(p) for p in exc.absolute_path) or "(root)"
        raise AssertionError(
            f"\nResponse does not match OpenAPI spec:\n"
            f"  Route  : {method.upper()} {openapi_path}  →  HTTP {status_code}\n"
            f"  Field  : {field}\n"
            f"  Error  : {exc.message}\n"
            f"  Schema : {json.dumps(exc.schema, indent=4)}\n"
            f"  Value  : {json.dumps(exc.instance, indent=4, default=str)}"
        ) from exc
    except jsonschema.SchemaError as exc:
        raise AssertionError(
            f"Spec schema is invalid for {method.upper()} {openapi_path}: {exc.message}"
        ) from exc
