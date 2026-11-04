"""GitLab's published OpenAPI (Swagger 2.0) document, used three ways by the simulator.

1. Requests: a path is matched to an operation. A query parameter the document
   defines but the simulator does not model is a gap (the test fails). A parameter
   the document does not define is ignored, as GitLab's Grape API ignores it. An enum
   value GitLab would refuse gets GitLab's 400.
2. Responses: every JSON body is validated against the operation's 200 schema.
3. Filling: a response object is built from its definition. Required fields the
   simulator does not model get a sentinel (``FILLER_STR`` / ``FILLER_INT``); the
   harness fails any test in which the connector reads one.

``specs/openapi_v2.yaml`` is GitLab's ``doc/api/openapi/openapi_v2.yaml`` (cached as
JSON). ``specs/supplement.json`` adds the few documented operations it leaves out, each
with the docs page it comes from.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parent.parent / "specs"
FILLER_STR = "__sim_unmodelled__"
FILLER_INT = -987654321


@lru_cache(maxsize=1)
def spec() -> dict[str, Any]:
    raw = json.loads((ROOT / "openapi_gitlab_subset.json").read_text())
    sup = json.loads((ROOT / "supplement.json").read_text())
    for path, ops in sup["paths"].items():
        raw["paths"].setdefault(path, {}).update(ops)
    for name, props in sup.get("definition_fixes", {}).items():
        if not name.startswith("_"):
            raw["definitions"][name]["properties"].update(props)
    for key, params in sup["extra_query_params"].items():
        method, path = key.split(" ", 1)
        op = raw["paths"][path][method]
        op.setdefault("parameters", []).extend({**p, "in": "query"} for p in params)
    return raw


def _compile(path: str) -> re.Pattern[str]:
    pattern = re.escape(path).replace(r"\{", "{").replace(r"\}", "}")
    return re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", pattern) + "$")


@lru_cache(maxsize=1)
def _routes() -> list[tuple[re.Pattern[str], str]]:
    paths = sorted(spec()["paths"], key=lambda p: (p.count("{"), -len(p)))
    return [(_compile(p), p) for p in paths]


def match(path: str) -> str | None:
    """The spec's path template for a concrete (still percent-encoded) path."""
    for pattern, template in _routes():
        if pattern.match(path):
            return template
    return None


def operation(template: str, method: str) -> dict[str, Any] | None:
    return spec()["paths"].get(template, {}).get(method.lower())


def query_params(op: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in op.get("parameters", []) if p.get("in") == "query"}


def check_query(template: str, method: str, query: dict[str, str], modelled: set[str]) -> tuple[list[str], str | None]:
    """(gaps, a 400 message if GitLab would refuse the request).

    ``modelled`` is the set of parameters the simulator's handler implements.
    """
    op = operation(template, method)
    if op is None:
        return [f"{method} {template}: no such operation in GitLab's spec"], None
    declared = query_params(op)
    gaps: list[str] = []
    for name, value in query.items():
        prm = declared.get(name)
        if prm is None:
            continue  # GitLab ignores parameters it does not declare
        enum = prm.get("enum") or (prm.get("items") or {}).get("enum")
        if enum and prm.get("type") != "array" and value not in [str(e) for e in enum]:
            return gaps, f"{name} does not have a valid value"
        if prm.get("type") == "integer" and not re.fullmatch(r"-?\d+", value):
            return gaps, f"{name} is invalid"
        if prm.get("type") == "boolean" and value.lower() not in ("true", "false", "1", "0", "yes", "no", "t", "f"):
            return gaps, f"{name} is invalid"
        if name not in modelled:
            gaps.append(f"{method} {template}: query parameter {name!r} is defined by GitLab but not modelled")
    return gaps, None


def _nullable(node: Any) -> Any:  # noqa: ANN401
    """Grape does not say which fields may be null; every property may be."""
    if isinstance(node, list):
        return [_nullable(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {k: _nullable(v) for k, v in node.items() if not k.startswith("x-") and k != "example"}
    if isinstance(out.get("properties"), dict) and all(isinstance(v, dict) for v in out["properties"].values()):
        out["properties"] = {k: _allow_null(v) for k, v in out["properties"].items()}
    return out


def _allow_null(prop: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in prop:
        return {"anyOf": [prop, {"type": "null"}]}
    t = prop.get("type")
    if isinstance(t, str) and t not in ("file",):
        return {**prop, "type": [t, "null"]}
    return prop


@lru_cache(maxsize=1)
def _definitions() -> dict[str, Any]:
    return {k: _nullable(v) for k, v in spec()["definitions"].items()}


def definition(name: str) -> dict[str, Any]:
    return _definitions()[name]


def response_schema(template: str, method: str = "get") -> dict[str, Any] | None:
    op = operation(template, method)
    if op is None:
        return None
    resp = op["responses"].get("200") or op["responses"].get("201")
    return (resp or {}).get("schema")


@lru_cache(maxsize=512)
def _validator(key: str) -> Any:  # noqa: ANN401
    schema = json.loads(key)
    schema["definitions"] = _definitions()
    return jsonschema.Draft7Validator(schema)


def validate(schema: dict[str, Any], instance: Any, *, listing: bool = False) -> list[str]:  # noqa: ANN401
    schema = copy.deepcopy(schema)
    if schema.get("type") == "file":
        return []
    if listing and schema.get("type") != "array":
        # Grape documents some listings (issues) with the item schema.
        schema = {"type": "array", "items": schema}
    validator = _validator(json.dumps(schema, sort_keys=True))
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message[:200]}" for e in errors[:5]]


def _resolve(schema: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = _definitions()[schema["$ref"].split("/")[-1]]
    return schema


def fill(schema: dict[str, Any], overrides: dict[str, Any] | None = None, depth: int = 0) -> Any:  # noqa: ANN401
    """An instance of ``schema`` with every required field present.

    ``overrides`` supplies the modelled values. Anything else required gets a sentinel.
    """
    overrides = overrides or {}
    schema = _resolve(schema)
    if "anyOf" in schema:
        options = [o for o in (_resolve(s) for s in schema["anyOf"]) if o.get("type") != "null"]
        return fill(options[0], overrides, depth) if options else None
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
            out[name] = fill(props.get(name, {}), None, depth + 1) if depth < 4 else None
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
            return "2000-01-01T00:00:00.000Z"
        return FILLER_STR
    return None


def make(definition_name: str, **values: Any) -> dict[str, Any]:  # noqa: ANN401
    """A full ``definition_name`` object with ``values`` as its modelled fields."""
    return fill(definition(definition_name), values)
