"""GitHub's published REST OpenAPI document, used three ways by the simulator.

1. Requests: a path is matched to its operation; a query parameter GitHub does not
   define, or an enum value it would refuse, is reported (422 for a bad enum, as GitHub
   answers).
2. Responses: every JSON body the simulator returns is validated against the
   operation's 200 schema, so the simulator cannot invent or drop a required field.
3. Filling: a response object is built from its schema. Required fields the simulator
   does not model get a sentinel value (``FILLER_STR`` / ``FILLER_INT``); the harness
   fails any test in which the connector reads one.

The document is GitHub's (github/rest-api-description), trimmed to the operations the
simulator serves: ``specs/api.github.com.trimmed.json.gz``, rebuilt by
``specs/refresh_specs.py``.
"""

from __future__ import annotations

import copy
import gzip
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

SPEC_PATH = Path(__file__).resolve().parent.parent / "specs" / "api.github.com.trimmed.json.gz"
FILLER_STR = "__sim_unmodelled__"
FILLER_INT = -987654321


def _nullable_to_jsonschema(node: Any) -> Any:  # noqa: ANN401
    """OpenAPI 3.0 ``nullable: true`` -> JSON Schema null union, recursively."""
    if isinstance(node, list):
        return [_nullable_to_jsonschema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {k: _nullable_to_jsonschema(v) for k, v in node.items()}
    if out.pop("nullable", False):
        return {"anyOf": [out, {"type": "null"}]}
    return out


@lru_cache(maxsize=1)
def spec() -> dict[str, Any]:
    raw = json.loads(gzip.decompress(SPEC_PATH.read_bytes()))
    raw["components"]["schemas"] = _nullable_to_jsonschema(raw["components"]["schemas"])
    return raw


@lru_cache(maxsize=1)
def _registry() -> Registry:
    return Registry().with_resource("spec", Resource.from_contents(spec(), default_specification=DRAFT7))


def _compile(path: str) -> re.Pattern[str]:
    pattern = re.escape(path).replace(r"\{", "{").replace(r"\}", "}")
    pattern = pattern.replace("{path}", ".+")  # a file path spans segments
    return re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", pattern) + "$")


@lru_cache(maxsize=1)
def _routes() -> list[tuple[re.Pattern[str], str]]:
    paths = sorted(spec()["paths"], key=lambda p: (p.count("{"), -len(p)))
    return [(_compile(p), p) for p in paths]


def match(path: str) -> str | None:
    """The spec's path template for a concrete path (fewest parameters wins)."""
    for pattern, template in _routes():
        if pattern.match(path):
            return template
    return None


def operation(template: str, method: str) -> dict[str, Any] | None:
    return spec()["paths"].get(template, {}).get(method.lower())


def query_params(op: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for declared in op.get("parameters", []):
        prm = spec()["components"]["parameters"][declared["$ref"].split("/")[-1]] if "$ref" in declared else declared
        if prm["in"] == "query":
            out[prm["name"]] = prm
    return out


# Query parameters GitHub accepts although its OpenAPI description leaves them out.
# Each is live-verified; see README.md.
UNDOCUMENTED_QUERY: dict[tuple[str, str], frozenset[str]] = {
    # The issues listing's own 422 asks for ``after``, and its Link header uses it.
    ("/repos/{owner}/{repo}/issues", "get"): frozenset({"after"}),
}


def check_query(template: str, method: str, query: dict[str, str]) -> tuple[list[str], str | None]:
    """(problems for the gap list, a 422 message if GitHub would refuse the request)."""
    op = operation(template, method)
    if op is None:
        return [f"{method} {template}: no such operation in GitHub's spec"], None
    if op.get("x-not-modelled"):
        return [], None  # a real operation the simulator does not serve: reported by the router
    declared = query_params(op)
    problems: list[str] = []
    for name, value in query.items():
        prm = declared.get(name)
        if prm is None and name in UNDOCUMENTED_QUERY.get((template, method.lower()), frozenset()):
            continue
        if prm is None:
            problems.append(f"{method} {template}: query parameter {name!r} is not defined by GitHub")
            continue
        enum = (prm.get("schema") or {}).get("enum")
        if enum and value not in [str(e) for e in enum]:
            return problems, f"Invalid value {value!r} for {name}"
    return problems, None


def response_schema(template: str, method: str = "get", status: str = "200") -> dict[str, Any] | None:
    op = operation(template, method)
    if op is None:
        return None
    resp = op["responses"].get(status)
    if resp is None:
        return None
    if "$ref" in resp:
        resp = spec()["components"]["responses"][resp["$ref"].split("/")[-1]]
    content = (resp.get("content") or {}).get("application/json")
    return content["schema"] if content else None


def _resolve(schema: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = spec()["components"]["schemas"][schema["$ref"].split("/")[-1]]
    return schema


def component(name: str) -> dict[str, Any]:
    return spec()["components"]["schemas"][name]


def _with_ref_base(schema: dict[str, Any]) -> dict[str, Any]:
    """Rewrite local refs so they resolve against the registered spec resource."""
    text = json.dumps(schema).replace('"#/components/', '"spec#/components/')
    return json.loads(text)


@lru_cache(maxsize=256)
def _validator(key: str) -> jsonschema.protocols.Validator:
    schema = json.loads(key)
    return jsonschema.Draft7Validator(_with_ref_base(schema), registry=_registry())


def validate(schema: dict[str, Any], instance: Any) -> list[str]:  # noqa: ANN401
    validator = _validator(json.dumps(schema, sort_keys=True))
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message[:200]}" for e in errors[:5]]


def fill(schema: dict[str, Any], overrides: dict[str, Any] | None = None, depth: int = 0) -> Any:  # noqa: ANN401
    """An instance of ``schema`` with every required field present.

    ``overrides`` supplies the modelled values (shallow, keyed by property). Anything
    else that is required gets a sentinel, null when nullable, or the first enum value.
    """
    overrides = overrides or {}
    schema = _resolve(schema)
    for combinator in ("anyOf", "oneOf", "allOf"):
        if combinator in schema:
            options = [_resolve(s) for s in schema[combinator]]
            if combinator == "allOf":
                merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
                for o in options:
                    merged["properties"].update(o.get("properties", {}))
                    merged["required"] += o.get("required", [])
                return fill(merged, overrides, depth)
            non_null = [o for o in options if o.get("type") != "null"]
            if any(o.get("type") == "null" for o in options):
                return None if not overrides else fill(non_null[0], overrides, depth)
            return fill(non_null[0], overrides, depth)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), None)
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "object" or "properties" in schema:
        out: dict[str, Any] = {}
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name in overrides:
                continue
            out[name] = fill(props.get(name, {}), None, depth + 1) if depth < 6 else None
        out.update(overrides)
        return out
    if kind == "array":
        return []
    if kind == "integer":
        return FILLER_INT
    if kind == "number":
        return float(FILLER_INT)
    if kind == "boolean":
        return False
    if kind == "string":
        fmt = schema.get("format")
        if fmt == "date-time":
            return "2000-01-01T00:00:00Z"
        if fmt == "uri":
            return "https://example.invalid/" + FILLER_STR
        return FILLER_STR
    return None


def make(component_name: str, **values: Any) -> dict[str, Any]:  # noqa: ANN401
    """A full ``component_name`` object with ``values`` as its modelled fields."""
    return fill(component(component_name), values)


def deep(value: Any) -> Any:  # noqa: ANN401
    return copy.deepcopy(value)
