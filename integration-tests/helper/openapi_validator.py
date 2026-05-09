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
#   integration-tests/helper/openapi_validator.py
#                                       ↑ this file
#   integration-tests/                  ← parents[1]
#   <repo-root>/                        ← parents[2]
# ---------------------------------------------------------------------------
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


def _normalize_nullable(node: Any) -> None:
    """
    Translate OpenAPI 3.0 'nullable: true' into JSON Schema 'type: [..., "null"]'
    so that pure ``jsonschema`` validation accepts null values where the spec
    declares them. Mutates *node* in place.
    """
    if isinstance(node, dict):
        if node.get("nullable") is True:
            t = node.get("type")
            if isinstance(t, str) and t != "null":
                node["type"] = [t, "null"]
            elif isinstance(t, list) and "null" not in t:
                node["type"] = [*t, "null"]
            node.pop("nullable", None)
        for value in node.values():
            _normalize_nullable(value)
    elif isinstance(node, list):
        for item in node:
            _normalize_nullable(item)


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
        _normalize_nullable(_spec_cache)
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

    Parameters
    ----------
    body:
        Parsed JSON response body.
    openapi_path:
        Path as declared in the spec (e.g. ``"/conversations"``).
    method:
        HTTP verb (case-insensitive).
    status_code:
        Expected HTTP status code (default 200).

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


# ---------------------------------------------------------------------------
# SSE stream utilities
# ---------------------------------------------------------------------------

def parse_sse_stream(text: str) -> list[dict[str, Any]]:
    """
    Parse a raw ``text/event-stream`` body into a list of frames.

    Each frame is a dict with keys:
    - ``event`` (str): the event name from the ``event:`` line, or
      ``"message"`` when no explicit event line is present.
    - ``data`` (Any): the parsed JSON payload from the ``data:`` line(s),
      or the raw string when JSON parsing fails.

    Blank-line–separated chunks are the frame boundaries per the SSE spec.
    """
    frames: list[dict[str, Any]] = []
    current_event: str | None = None
    current_data_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")

        if line == "":
            # Frame boundary — emit if we have data.
            if current_data_lines:
                raw_data = "\n".join(current_data_lines)
                try:
                    data = json.loads(raw_data)
                except (json.JSONDecodeError, ValueError):
                    data = raw_data
                frames.append(
                    {"event": current_event or "message", "data": data}
                )
            current_event = None
            current_data_lines = []
        elif line.startswith("event:"):
            current_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            current_data_lines.append(line[len("data:"):].strip())
        # ignore "id:", "retry:", and comment lines

    # Flush a final frame if the stream does not end with a blank line.
    if current_data_lines:
        raw_data = "\n".join(current_data_lines)
        try:
            data = json.loads(raw_data)
        except (json.JSONDecodeError, ValueError):
            data = raw_data
        frames.append({"event": current_event or "message", "data": data})

    return frames


def assert_openapi_sse_stream(
    frames: list[dict[str, Any]],
    openapi_path: str,
    method: str,
) -> None:
    """
    Validate a list of SSE frames (as returned by ``parse_sse_stream``) against
    the SSE event schema declared in the spec for the given path/method.

    The spec for SSE endpoints declares a ``text/event-stream`` schema whose
    envelope is a discriminated union over ``event``. The envelope MUST declare
    a ``discriminator`` block of the form::

        discriminator:
          propertyName: event
          mapping:
            <event-name>: '#/components/schemas/<DataSchema>'
            ...

    For each frame this function:

    1. Checks that ``frame["event"]`` is a member of the envelope's ``event`` enum.
    2. Looks up the matching ``data`` payload schema via the discriminator
       mapping and validates ``frame["data"]`` against it. Every event in the
       enum must have a mapping entry — there are no pass-through events.

    Parameters
    ----------
    frames:
        Parsed SSE frames from ``parse_sse_stream``.
    openapi_path:
        Spec path (e.g. ``"/conversations/stream"``).
    method:
        HTTP verb.

    Raises ``AssertionError`` if any validation check fails.
    """
    spec = _load_spec()
    paths = spec.get("paths", {})

    if openapi_path not in paths:
        raise KeyError(f"SSE path {openapi_path!r} not found in spec.")

    method_obj = paths[openapi_path].get(method.lower(), {})
    responses = method_obj.get("responses", {})
    response_200 = responses.get("200", {})
    content = response_200.get("content", {})
    sse_block = content.get("text/event-stream", {})
    event_schema_ref = sse_block.get("schema", {})

    # Resolve $ref on the top-level SSE schema.
    if "$ref" in event_schema_ref:
        ref_parts = event_schema_ref["$ref"].lstrip("#/").split("/")
        node: Any = spec
        for part in ref_parts:
            node = node[part]
        event_schema = node
    else:
        event_schema = event_schema_ref

    # Collect the declared event enum from the spec.
    event_enum: set[str] = set(
        event_schema.get("properties", {})
        .get("event", {})
        .get("enum", [])
    )

    # Discriminator mapping: event-name -> schema $ref.
    discriminator = event_schema.get("discriminator", {})
    mapping: dict[str, str] = discriminator.get("mapping", {})
    if not mapping:
        raise AssertionError(
            f"SSE envelope for {method.upper()} {openapi_path} is missing "
            f"`discriminator.mapping`. The validator requires a spec-driven "
            f"event-to-schema map; the heuristic substring match was removed."
        )

    resolver = RefResolver(base_uri=SPEC_PATH.as_uri(), referrer=spec)

    for i, frame in enumerate(frames):
        event_name = frame["event"]
        data = frame["data"]

        # Check event name is in spec enum.
        if event_enum and event_name not in event_enum:
            raise AssertionError(
                f"SSE frame #{i}: event {event_name!r} is not in the spec enum "
                f"{sorted(event_enum)} for {method.upper()} {openapi_path}"
            )

        # Look up the data schema via the discriminator mapping.
        ref_str = mapping.get(event_name)
        if ref_str is None:
            raise AssertionError(
                f"SSE frame #{i}: event {event_name!r} has no entry in "
                f"`discriminator.mapping` for {method.upper()} {openapi_path}. "
                f"Mapped events: {sorted(mapping.keys())}"
            )

        ref_parts = ref_str.lstrip("#/").split("/")
        node = spec
        for part in ref_parts:
            node = node[part]
        payload_schema = node

        try:
            jsonschema.validate(
                instance=data,
                schema=payload_schema,
                resolver=resolver,
            )
        except jsonschema.ValidationError as exc:
            field = " → ".join(str(p) for p in exc.absolute_path) or "(root)"
            raise AssertionError(
                f"\nSSE frame #{i} (event={event_name!r}) data does not match spec:\n"
                f"  Route  : {method.upper()} {openapi_path}\n"
                f"  Field  : {field}\n"
                f"  Error  : {exc.message}\n"
                f"  Schema : {json.dumps(exc.schema, indent=4)}\n"
                f"  Value  : {json.dumps(exc.instance, indent=4, default=str)}"
            ) from exc
