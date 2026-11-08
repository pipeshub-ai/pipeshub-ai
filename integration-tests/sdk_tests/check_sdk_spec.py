#!/usr/bin/env python3
"""Fail when an SDK operation uses a schema Speakeasy cannot generate.

Usage: check_sdk_spec.py [spec]

Speakeasy leaves out of every SDK, with only a warning, an operation whose parameters,
body or responses reach a `not` schema ("unsupported: not schemas unsupported"). The
SDK then has no method for it, and every test that calls it fails or is skipped.
Fold a refused pattern into `pattern` with a negative lookahead instead, or say the
rule in the description. A property marked `x-speakeasy-ignore: true` is not generated,
so a `not` under it is allowed.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

_SPEC = (
    Path(__file__).resolve().parents[2]
    / "backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml"
)
_HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
# Values under these keys are not schemas. The schema-only ones are fields of a Schema
# Object, so they are skipped only there: `responses.default` is a response, and is
# walked. A discriminator mapping key is a value of the property, not a keyword.
_DATA_KEYS = {"example", "examples"}
_SCHEMA_DATA_KEYS = {"default", "enum", "const", "discriminator", "xml", "externalDocs"}
_SCHEMA_PREFIX = "#/components/schemas/"


def _escape(key: Any) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")


def _resolve(spec: dict, ref: str) -> Any:
    node: Any = spec
    for part in ref.removeprefix("#/").split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _not_schemas(
    spec: dict, node: Any, path: str, refs: frozenset[str], in_schema: bool = False
) -> Iterator[str]:
    if isinstance(node, list):
        for index, item in enumerate(node):
            yield from _not_schemas(spec, item, f"{path}/{index}", refs, in_schema)
        return
    if not isinstance(node, dict) or node.get("x-speakeasy-ignore") is True:
        return
    ref = node.get("$ref")
    # A cycle is checked once: the schemas on it are reached on the first pass.
    if isinstance(ref, str) and ref.startswith("#/") and ref not in refs:
        yield from _not_schemas(
            spec,
            _resolve(spec, ref),
            ref,
            refs | {ref},
            in_schema or ref.startswith(_SCHEMA_PREFIX),
        )
    for key, value in node.items():
        if key in _DATA_KEYS or key.startswith("x-"):
            continue
        if in_schema and key in _SCHEMA_DATA_KEYS:
            continue
        if in_schema and key == "properties" and isinstance(value, dict):
            for name, schema in value.items():
                yield from _not_schemas(
                    spec, schema, f"{path}/properties/{_escape(name)}", refs, True
                )
            continue
        if in_schema and key == "not":
            yield f"{path}/not"
        yield from _not_schemas(
            spec, value, f"{path}/{_escape(key)}", refs, in_schema or key == "schema"
        )


def _parameter_key(spec: dict, parameter: Any) -> tuple[Any, Any]:
    if isinstance(parameter, dict) and isinstance(parameter.get("$ref"), str):
        parameter = _resolve(spec, parameter["$ref"])
    if not isinstance(parameter, dict):
        return (None, None)
    return (parameter.get("name"), parameter.get("in"))


def _inherited_parameters(
    spec: dict, path_item: dict, operation: dict
) -> list[tuple[int, Any]]:
    """Path-level parameters the operation does not override, with their indexes."""
    overridden = {
        _parameter_key(spec, parameter)
        for parameter in operation.get("parameters") or []
    }
    return [
        (index, parameter)
        for index, parameter in enumerate(path_item.get("parameters") or [])
        if _parameter_key(spec, parameter) not in overridden
    ]


def violations(spec: dict) -> list[tuple[str, str]]:
    """(operationId, JSON pointer of the `not`) for every SDK operation that reaches one."""
    found = []
    for route, path_item in (spec.get("paths") or {}).items():
        for method, operation in path_item.items():
            if method not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            if operation.get("x-pipeshub-sdk") is not True:
                continue
            route_pointer = f"#/paths/{_escape(route)}"
            pointers = list(
                _not_schemas(spec, operation, f"{route_pointer}/{method}", frozenset())
            )
            for index, parameter in _inherited_parameters(spec, path_item, operation):
                pointers.extend(
                    _not_schemas(
                        spec,
                        parameter,
                        f"{route_pointer}/parameters/{index}",
                        frozenset(),
                    )
                )
            for pointer in dict.fromkeys(pointers):
                found.append(
                    (operation.get("operationId", f"{method} {route}"), pointer)
                )
    return found


def main(argv: list[str]) -> int:
    spec_path = Path(argv[0]) if argv else _SPEC
    with spec_path.open() as f:
        spec = yaml.load(f, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    found = violations(spec)
    for operation_id, pointer in found:
        print(
            f"::error::{operation_id} reaches a `not` schema at {pointer}; "
            "Speakeasy leaves the operation out of the SDKs."
        )
    if found:
        return 1
    print("No SDK operation uses a schema Speakeasy cannot generate.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
