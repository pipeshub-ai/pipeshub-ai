"""
Validate JSON response bodies against schemas defined in ``pipeshub-openapi.yaml``.

Uses ``jsonschema`` Draft 7 with a ``referencing.Registry`` rooted at the full
OpenAPI document so ``#/components/schemas/...`` resolves. Adapts OpenAPI 3.0
``nullable`` into JSON Schema compatible ``anyOf`` / ``type`` shapes.
"""

from __future__ import annotations

import copy
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import referencing.exceptions
import yaml
from jsonschema import Draft7Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

# integration-tests/response-validation/helper/ → repo root is parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_OPENAPI_PATH = (
    _REPO_ROOT
    / "backend"
    / "nodejs"
    / "apps"
    / "src"
    / "modules"
    / "api-docs"
    / "pipeshub-openapi.yaml"
)

SPEC_URI = "urn:pipeshub:openapi"


def _strip_nonvalidation_keys(obj: Any) -> Any:
    """Remove OpenAPI-only keys that confuse strict validators."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("example", "examples", "discriminator", "xml", "externalDocs"):
                continue
            out[k] = _strip_nonvalidation_keys(v)
        return out
    if isinstance(obj, list):
        return [_strip_nonvalidation_keys(i) for i in obj]
    return obj


def adapt_openapi_nullable(obj: Any) -> Any:
    """Rewrite ``nullable: true`` into Draft-7-compatible schemas (recursive)."""
    if isinstance(obj, list):
        return [adapt_openapi_nullable(x) for x in obj]
    if not isinstance(obj, dict):
        return obj

    if obj.get("nullable") is True:
        node = copy.deepcopy(obj)
        node.pop("nullable", None)
        node = adapt_openapi_nullable(node)
        # Use anyOf: instance must satisfy the non-null schema OR be null. Using oneOf
        # breaks when the non-null branch is `{}` (no constraints): null matches both
        # `{}` and `{type: null}`, and oneOf requires exactly one match.
        return {"anyOf": [node, {"type": "null"}]}

    result: dict[str, Any] = {}
    for k, v in obj.items():
        if k == "nullable":
            continue
        result[k] = adapt_openapi_nullable(v)
    return result


@lru_cache(maxsize=1)
def load_openapi_document() -> dict[str, Any]:
    if not _OPENAPI_PATH.is_file():
        raise FileNotFoundError(f"OpenAPI spec not found: {_OPENAPI_PATH}")
    with _OPENAPI_PATH.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError("OpenAPI YAML root must be a mapping")
    return raw


def _make_registry(adapted_root: dict[str, Any]) -> Registry:
    return Registry().with_resources(
        [
            (
                SPEC_URI,
                Resource.from_contents(adapted_root, default_specification=DRAFT7),
            ),
        ]
    )


def _validator_for_schema(schema: dict[str, Any], registry: Registry) -> Draft7Validator:
    return Draft7Validator(schema, registry=registry)


def validate_instance_with_root_schema(instance: Any, schema: dict[str, Any]) -> None:
    """Validate ``instance`` against a schema fragment that may contain ``$ref``."""
    doc = load_openapi_document()
    adapted = adapt_openapi_nullable(_strip_nonvalidation_keys(copy.deepcopy(doc)))
    registry = _make_registry(adapted)
    validator = _validator_for_schema(schema, registry)
    validator.validate(instance)


def validate_instance_with_ref(instance: Any, json_pointer: str) -> None:
    """
    Validate against a schema located at ``json_pointer`` in the OpenAPI doc,
    e.g. ``#/components/schemas/Organization``.
    """
    if not json_pointer.startswith("#/"):
        raise ValueError(f"ref must start with #/: {json_pointer!r}")
    schema = {"$ref": SPEC_URI + json_pointer}
    validate_instance_with_root_schema(instance, schema)


_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head")


def _normalize_status(status: str) -> str:
    s = str(status).strip()
    if re.fullmatch(r"\d\d\d", s):
        return s
    return str(int(s))


def operation_response_json_pointer(operation_id: str, status_code: str) -> str:
    """
    Build a JSON Pointer (fragment) into the OpenAPI document for the
    ``application/json`` response schema of an operation.

    Example: ``#/paths/~1org/put/responses/200/content/application~1json/schema``
    """
    doc = load_openapi_document()
    paths = doc.get("paths") or {}
    want_status = _normalize_status(status_code)
    for raw_path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in _HTTP_METHODS:
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            if op.get("operationId") != operation_id:
                continue
            path_seg = str(raw_path).replace("~", "~0").replace("/", "~1")
            ptr = (
                f"#/paths/{path_seg}/{method}/responses/{want_status}/"
                f"content/application~1json/schema"
            )
            responses = op.get("responses") or {}
            if want_status not in responses:
                raise KeyError(
                    f"No response {want_status} for operationId={operation_id!r}"
                )
            content = (responses[want_status].get("content") or {}).get(
                "application/json"
            ) or {}
            if content.get("schema") is None:
                raise KeyError(
                    f"No application/json schema for operationId={operation_id!r} "
                    f"status={want_status}"
                )
            return ptr

    raise KeyError(f"operationId not found: {operation_id!r}")


def operation_request_body_json_pointer(operation_id: str) -> str:
    """
    Build a JSON Pointer into the OpenAPI document for the ``application/json``
    request body schema of an operation.

    Raises:
        KeyError: No matching operation, no request body, or no JSON schema.
    """
    doc = load_openapi_document()
    paths = doc.get("paths") or {}
    for raw_path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in _HTTP_METHODS:
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            if op.get("operationId") != operation_id:
                continue
            request_body = op.get("requestBody")
            if not isinstance(request_body, dict):
                raise KeyError(
                    f"No requestBody for operationId={operation_id!r}"
                )
            content = (request_body.get("content") or {}).get("application/json") or {}
            if content.get("schema") is None:
                raise KeyError(
                    f"No application/json schema on requestBody for "
                    f"operationId={operation_id!r}"
                )
            path_seg = str(raw_path).replace("~", "~0").replace("/", "~1")
            return (
                f"#/paths/{path_seg}/{method}/requestBody/content/"
                f"application~1json/schema"
            )

    raise KeyError(f"operationId not found: {operation_id!r}")


def assert_request_body_matches_openapi_operation(data: object, operation_id: str) -> None:
    """Validate a request JSON body against the operation's documented schema."""
    try:
        ptr = operation_request_body_json_pointer(operation_id)
        validate_instance_with_ref(data, ptr)
    except referencing.exceptions.Unresolvable as e:
        raise AssertionError(f"OpenAPI $ref could not be resolved: {e}") from e
    except KeyError as e:
        raise AssertionError(str(e)) from e
    except Exception as e:
        raise AssertionError(
            f"Request body does not match OpenAPI schema for "
            f"operationId={operation_id!r}: {e}"
        ) from e


def assert_response_matches_openapi_operation(
    data: object,
    operation_id: str,
    *,
    status_code: str = "200",
) -> None:
    try:
        ptr = operation_response_json_pointer(operation_id, status_code)
        validate_instance_with_ref(data, ptr)
    except referencing.exceptions.Unresolvable as e:
        raise AssertionError(f"OpenAPI $ref could not be resolved: {e}") from e
    except KeyError as e:
        raise AssertionError(str(e)) from e
    except Exception as e:
        raise AssertionError(
            f"Response does not match OpenAPI schema for "
            f"operationId={operation_id!r} status={status_code!r}: {e}"
        ) from e


def assert_response_matches_openapi_ref(data: object, json_pointer: str) -> None:
    try:
        validate_instance_with_ref(data, json_pointer)
    except Exception as e:
        raise AssertionError(
            f"Response does not match OpenAPI schema {json_pointer!r}: {e}"
        ) from e
