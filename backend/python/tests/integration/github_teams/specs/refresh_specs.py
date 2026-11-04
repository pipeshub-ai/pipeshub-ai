"""Rebuild the two spec files the GitHub simulator is held to, from GitHub's own sources.

    python tests/integration/github_teams/specs/refresh_specs.py           # download
    python tests/integration/github_teams/specs/refresh_specs.py REST.json SCHEMA.graphql

``api.github.com.trimmed.json.gz``: GitHub's REST OpenAPI description, trimmed to the
operations the simulator serves (``sim/rest.py``'s routes) and every schema they, or the
simulator's objects, reference. Every other path keeps its template with empty methods, so
a request to a real but unmodelled operation still matches GitHub's path and is reported
as a gap. ``schema.docs.graphql.gz``: GitHub's public GraphQL schema, whole.

After a refresh, run the suite: a field GitHub renamed or removed fails the test that
uses it.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

REST_URL = "https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json"
SCHEMA_URL = "https://docs.github.com/public/fpt/schema.docs.graphql"
HERE = Path(__file__).resolve().parent
SIM = HERE.parent / "sim"


def _served_operations() -> set[tuple[str, str]]:
    text = (SIM / "rest.py").read_text()
    return {(m.lower(), p) for m, p in re.findall(r'@route\("([A-Z]+)", "([^"]+)"\)', text)}


def _named_components() -> set[str]:
    """Components the simulator builds by name (``spec.make("issue", ...)``, ``repo_obj(r, "repository")``)."""
    names: set[str] = set()
    for path in SIM.glob("*.py"):
        names.update(re.findall(r'"([a-z][a-z0-9]*(?:-[a-z0-9]+)+|[a-z]+)"', path.read_text()))
    return names


def _refs(node: object) -> set[str]:
    out: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            out.add(ref)
        for value in node.values():
            out |= _refs(value)
    elif isinstance(node, list):
        for value in node:
            out |= _refs(value)
    return out


def trim(spec: dict) -> dict:
    served = _served_operations()
    paths: dict[str, dict] = {}
    for template, item in spec["paths"].items():
        paths[template] = {
            method: (op if (method, template) in served else {"x-not-modelled": True})
            for method, op in item.items() if method in ("get", "post", "put", "patch", "delete")
        }
    missing = served - {(m, p) for p, item in paths.items() for m, op in item.items() if "x-not-modelled" not in op}
    if missing:
        raise SystemExit(f"GitHub's spec no longer has these served operations: {sorted(missing)}")

    components = spec["components"]
    kept: dict[str, dict] = {kind: {} for kind in components}
    pending = _refs(paths) | {
        f"#/components/schemas/{name}" for name in _named_components() if name in components.get("schemas", {})
    }
    while pending:
        ref = pending.pop()
        _, _, kind, name = ref.split("/", 3)
        if name in kept[kind]:
            continue
        kept[kind][name] = components[kind][name]
        pending |= _refs(components[kind][name])
    return {
        "openapi": spec["openapi"],
        "info": {**spec["info"], "x-trimmed-for": "PipesHub GitHub Teams simulator"},
        "paths": paths,
        "components": {kind: value for kind, value in kept.items() if value},
    }


def _write(name: str, data: bytes) -> None:
    (HERE / name).write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    print(f"{name}: {len(data):,} bytes before compression, sha256 {hashlib.sha256(data).hexdigest()}")


def main(argv: list[str]) -> None:
    if argv:
        rest, schema = Path(argv[0]).read_bytes(), Path(argv[1]).read_bytes()
    else:
        rest = urllib.request.urlopen(REST_URL, timeout=120).read()
        schema = urllib.request.urlopen(SCHEMA_URL, timeout=120).read()
    print(f"source REST description sha256 {hashlib.sha256(rest).hexdigest()}")
    print(f"source GraphQL schema sha256 {hashlib.sha256(schema).hexdigest()}")
    _write("api.github.com.trimmed.json.gz", json.dumps(trim(json.loads(rest)), sort_keys=True).encode())
    _write("schema.docs.graphql.gz", schema)


if __name__ == "__main__":
    main(sys.argv[1:])
