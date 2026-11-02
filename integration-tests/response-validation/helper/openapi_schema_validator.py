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


def _strip_nonvalidation_keys(obj: Any, *, under_properties: bool = False) -> Any:
    """Remove OpenAPI-only keys that confuse strict validators.

    Keys directly under ``properties`` are field names, not keywords, so a field called
    ``examples`` keeps its schema.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if not under_properties and k in ("example", "examples", "discriminator", "xml", "externalDocs"):
                continue
            out[k] = _strip_nonvalidation_keys(v, under_properties=k == "properties" and not under_properties)
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
            response = responses[want_status]
            ref = response.get("$ref") if isinstance(response, dict) else None
            if isinstance(ref, str) and ref.startswith("#/components/responses/"):
                # A shared response (XssRejected, InsufficientScope, ...) keeps its schema there.
                name = ref.rsplit("/", 1)[-1]
                response = (doc.get("components") or {}).get("responses", {}).get(name) or {}
                ptr = f"{ref}/content/application~1json/schema"
            content = (response.get("content") or {}).get("application/json") or {}
            if content.get("schema") is None:
                raise KeyError(
                    f"No application/json schema for operationId={operation_id!r} "
                    f"status={want_status}"
                )
            return ptr

    raise KeyError(f"operationId not found: {operation_id!r}")


def _operation_responses(operation_id: str) -> dict[str, object]:
    doc = load_openapi_document()
    paths = doc.get("paths") or {}
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for method in _HTTP_METHODS:
            op = path_item.get(method)
            if isinstance(op, dict) and op.get("operationId") == operation_id:
                responses = op.get("responses")
                if isinstance(responses, dict):
                    return responses
    raise KeyError(f"operationId not found: {operation_id!r}")


def assert_operation_documents_response(operation_id: str, status_code: str) -> None:
    """Assert the OpenAPI spec lists ``status_code`` for ``operation_id``."""
    want_status = _normalize_status(status_code)
    try:
        responses = _operation_responses(operation_id)
    except KeyError as e:
        raise AssertionError(str(e)) from e
    if want_status not in responses:
        raise AssertionError(
            f"OpenAPI spec for operationId={operation_id!r} "
            f"does not document response {want_status!r}"
        )


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


# ---------------------------------------------------------------------------
# Strict checks: the body is fully described by the spec
# ---------------------------------------------------------------------------
#
# Draft-7 validation alone accepts any key an object schema does not list,
# unless the schema sets ``additionalProperties: false``. Most schemas leave it
# unset, so a response whose fields are all different from the documented ones
# still "matches". A generated SDK reads only the documented properties, so an
# undocumented field is data the SDK drops. The strict helpers below report
# every key the spec does not declare wherever the schema lists ``properties``
# and does not explicitly allow extra keys (``additionalProperties: true`` or a
# schema). An object schema with no ``properties`` is a free-form map and is
# not walked.

_MAX_REPORTED = 40


@lru_cache(maxsize=1)
def _adapted_document() -> dict[str, Any]:
    return adapt_openapi_nullable(
        _strip_nonvalidation_keys(copy.deepcopy(load_openapi_document()))
    )


def _deref(schema: Any) -> Any:
    root = _adapted_document()
    seen: set[str] = set()
    while isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
        ref = schema["$ref"]
        if ref.startswith(SPEC_URI):
            ref = ref[len(SPEC_URI):]
        if ref in seen or not ref.startswith("#/"):
            return {}
        seen.add(ref)
        node: Any = root
        for part in ref[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
        schema = node
    return schema


def _flatten_all_of(schema: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    """Properties and the additionalProperties rule of a schema and its allOf branches."""
    properties: dict[str, Any] = dict(schema.get("properties") or {})
    extra = schema.get("additionalProperties")
    for branch in schema.get("allOf") or []:
        branch_props, branch_extra = _flatten_all_of(_deref(branch))
        properties.update(branch_props)
        if branch_extra is True or isinstance(branch_extra, dict):
            extra = branch_extra
    return properties, extra


@lru_cache(maxsize=1)
def _adapted_registry() -> Registry:
    return _make_registry(_adapted_document())


def _absolute_refs(node: Any) -> Any:
    """A schema fragment validated on its own resolves ``#/...`` against itself, not the spec."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            local_ref = k == "$ref" and isinstance(v, str) and v.startswith("#/")
            out[k] = SPEC_URI + v if local_ref else _absolute_refs(v)
        return out
    if isinstance(node, list):
        return [_absolute_refs(x) for x in node]
    return node


def _matches(instance: Any, schema: dict[str, Any]) -> bool:
    return _validator_for_schema(_absolute_refs(schema), _adapted_registry()).is_valid(instance)


def _undocumented(instance: Any, schema: Any, path: str, opaque: bool = False) -> set[str]:
    schema = _deref(schema)
    if not isinstance(schema, dict):
        return set()

    alternatives = (schema.get("anyOf") or []) + (schema.get("oneOf") or [])
    if alternatives:
        branches = [_deref(b) for b in alternatives]
        candidates = [b for b in branches if _matches(instance, b)] or branches
        findings = [_undocumented(instance, b, path, opaque) for b in candidates]
        return min(findings, key=len) if findings else set()

    found: set[str] = set()
    if isinstance(instance, dict):
        properties, extra = _flatten_all_of(schema)
        if not properties and not isinstance(extra, dict):
            # A bare `type: object` reaches an SDK as an untyped dict.
            if opaque and extra is not True:
                found |= {f"{path}.{key}" if path else key for key in instance}
            return found
        for key, value in instance.items():
            child = f"{path}.{key}" if path else key
            if key in properties:
                found |= _undocumented(value, properties[key], child, opaque)
            elif isinstance(extra, dict):
                found |= _undocumented(
                    value, extra, f"{path}.<{key}>" if path else f"<{key}>", opaque
                )
            elif extra is not True:
                found.add(child)
    elif isinstance(instance, list) and isinstance(schema.get("items"), dict):
        for item in instance:
            found |= _undocumented(item, schema["items"], f"{path}[*]", opaque)
    return found


def undocumented_fields(instance: Any, json_pointer: str, *, opaque: bool = False) -> list[str]:
    """Paths of keys in ``instance`` that the schema at ``json_pointer`` does not declare.

    With ``opaque``, keys of an object documented only as ``type: object`` count too.
    """
    return sorted(_undocumented(instance, {"$ref": json_pointer}, "", opaque))


def _schema_errors(instance: Any, json_pointer: str) -> list[str]:
    validator = _validator_for_schema({"$ref": SPEC_URI + json_pointer}, _adapted_registry())
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    return [
        f"{'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message[:300]}"
        for e in errors
    ]


def _strict_report(instance: Any, json_pointer: str, opaque: bool = False) -> str | None:
    errors = _schema_errors(instance, json_pointer)
    extra = undocumented_fields(instance, json_pointer, opaque=opaque)
    if not errors and not extra:
        return None
    lines: list[str] = []
    if errors:
        lines.append(f"{len(errors)} schema violation(s):")
        lines += [f"  - {e}" for e in errors[:_MAX_REPORTED]]
    if extra:
        lines.append(f"{len(extra)} field(s) the spec does not document:")
        lines += [f"  - {p}" for p in extra[:_MAX_REPORTED]]
    hidden = max(0, len(errors) - _MAX_REPORTED) + max(0, len(extra) - _MAX_REPORTED)
    if hidden:
        lines.append(f"  ... and {hidden} more")
    return "\n".join(lines)


def assert_response_strictly_matches_openapi_operation(
    data: object,
    operation_id: str,
    *,
    status_code: str = "200",
    opaque: bool = False,
) -> None:
    """The documented status has a JSON schema, the body satisfies it, and every
    field in the body is documented. With ``opaque``, a bare ``type: object`` does
    not document the fields inside it."""
    try:
        ptr = operation_response_json_pointer(operation_id, status_code)
    except KeyError as e:
        raise AssertionError(f"OpenAPI spec: {e}") from e
    report = _strict_report(data, ptr, opaque)
    if report:
        raise AssertionError(
            f"Response does not match OpenAPI spec for operationId={operation_id!r} "
            f"status={status_code!r}:\n{report}"
        )


def assert_request_body_strictly_matches_openapi_operation(
    data: object, operation_id: str
) -> None:
    """The body a working client sends satisfies the documented request schema,
    and every field in it is documented."""
    try:
        ptr = operation_request_body_json_pointer(operation_id)
    except KeyError as e:
        raise AssertionError(f"OpenAPI spec: {e}") from e
    report = _strict_report(data, ptr)
    if report:
        raise AssertionError(
            f"Request body the server accepts does not match the OpenAPI spec for "
            f"operationId={operation_id!r}:\n{report}"
        )


def assert_error_response_matches_openapi_operation(
    data: object, operation_id: str, status_code: int | str
) -> None:
    """An error status is documented, and the body satisfies its JSON schema if it has one."""
    status = _normalize_status(str(status_code))
    assert_operation_documents_response(operation_id, status)
    try:
        ptr = operation_response_json_pointer(operation_id, status)
    except KeyError:
        return
    errors = _schema_errors(data, ptr)
    if errors:
        raise AssertionError(
            f"Error response does not match OpenAPI spec for operationId={operation_id!r} "
            f"status={status!r}:\n" + "\n".join(f"  - {e}" for e in errors[:_MAX_REPORTED])
        )


def _find_operation(operation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    doc = load_openapi_document()
    for path_item in (doc.get("paths") or {}).values():
        if not isinstance(path_item, dict):
            continue
        for method in _HTTP_METHODS:
            op = path_item.get(method)
            if isinstance(op, dict) and op.get("operationId") == operation_id:
                return op, path_item
    raise KeyError(f"operationId not found: {operation_id!r}")


def operation_security(operation_id: str) -> list[dict[str, list[str]]]:
    """The operation's security requirements, falling back to the document default."""
    op, _ = _find_operation(operation_id)
    if "security" in op:
        return list(op["security"] or [])
    return list(load_openapi_document().get("security") or [])


def operation_oauth2_scopes(operation_id: str) -> list[str]:
    """Scopes of the operation's ``oauth2`` security requirement ([] when it has none)."""
    for requirement in operation_security(operation_id):
        if "oauth2" in requirement:
            return list(requirement["oauth2"] or [])
    return []


def assert_operation_requires_authentication(operation_id: str) -> None:
    """The spec lists at least one non-empty security requirement for the operation."""
    if not any(operation_security(operation_id)):
        raise AssertionError(
            f"OpenAPI spec declares no security for operationId={operation_id!r}, "
            "but the endpoint rejects requests that carry no credentials"
        )


def assert_query_params_documented(operation_id: str, params: dict[str, object]) -> None:
    """Every query parameter a working client sends is declared on the operation."""
    op, path_item = _find_operation(operation_id)
    declared = set()
    for param in list(path_item.get("parameters") or []) + list(op.get("parameters") or []):
        param = _deref(param)
        if isinstance(param, dict) and param.get("in") == "query":
            declared.add(param.get("name"))
    missing = sorted(set(params) - declared)
    if missing:
        raise AssertionError(
            f"OpenAPI spec for operationId={operation_id!r} does not declare "
            f"query parameter(s) {missing}"
        )


def operation_parameter_default(operation_id: str, name: str) -> object:
    """A parameter's documented default, through ``$ref`` and ``allOf``; None if unset."""
    op, path_item = _find_operation(operation_id)
    for param in list(path_item.get("parameters") or []) + list(op.get("parameters") or []):
        param = _deref(param)
        if not isinstance(param, dict) or param.get("name") != name:
            continue
        pending = [param.get("schema") or {}]
        while pending:
            schema = pending.pop()
            if isinstance(schema, dict) and "default" in schema:
                return schema["default"]
            resolved = _deref(schema)
            if isinstance(resolved, dict):
                if "default" in resolved:
                    return resolved["default"]
                pending.extend(resolved.get("allOf") or [])
        return None
    raise AssertionError(f"OpenAPI spec for operationId={operation_id!r} has no parameter {name!r}")


def operation_parameter_accepts(operation_id: str, name: str, value: object) -> bool:
    """Whether ``value`` satisfies the documented schema of parameter ``name``."""
    op, path_item = _find_operation(operation_id)
    for param in list(path_item.get("parameters") or []) + list(op.get("parameters") or []):
        param = _deref(param)
        if isinstance(param, dict) and param.get("name") == name:
            if value is None:
                return not param.get("required", False)
            return _matches(value, param.get("schema") or {})
    raise AssertionError(f"OpenAPI spec for operationId={operation_id!r} has no parameter {name!r}")


def request_body_violations(data: object, operation_id: str) -> str | None:
    """Why ``data`` is not a valid documented request body (None when it is)."""
    return _strict_report(data, operation_request_body_json_pointer(operation_id))


def operation_request_examples(operation_id: str) -> dict[str, Any]:
    """The documented ``application/json`` request examples of an operation, by name."""
    op, _ = _find_operation(operation_id)
    content = ((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}
    examples = content.get("examples") or {}
    return {name: copy.deepcopy(ex.get("value")) for name, ex in examples.items()}
