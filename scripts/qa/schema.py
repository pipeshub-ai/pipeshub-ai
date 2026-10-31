"""Check a document against the JSON Schemas in scripts/qa/schema/.

Implements the subset those schemas use (type, const, enum, required,
properties, additionalProperties, items), so contract checks need no third-party package.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


def load(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _type_ok(value: Any, expected: str | list[str]) -> bool:
    names = expected if isinstance(expected, list) else [expected]
    for n in names:
        if n in ("integer", "number") and isinstance(value, bool):
            continue
        if isinstance(value, TYPES[n]):
            return True
    return False


def errors(doc: Any, schema: dict[str, Any], where: str = "$") -> list[str]:
    out: list[str] = []
    if "type" in schema and not _type_ok(doc, schema["type"]):
        return [f"{where}: expected {schema['type']}, got {type(doc).__name__}"]
    if "const" in schema and doc != schema["const"]:
        out.append(f"{where}: expected {schema['const']!r}")
    if "enum" in schema and doc not in schema["enum"]:
        out.append(f"{where}: {doc!r} not in {schema['enum']}")
    if isinstance(doc, dict):
        out += [f"{where}: missing {k!r}" for k in schema.get("required", []) if k not in doc]
        for key, sub in schema.get("properties", {}).items():
            if key in doc:
                out += errors(doc[key], sub, f"{where}.{key}")
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            for key in doc.keys() - schema.get("properties", {}).keys():
                out += errors(doc[key], extra, f"{where}.{key}")
    if isinstance(doc, list) and "items" in schema:
        for i, item in enumerate(doc):
            out += errors(item, schema["items"], f"{where}[{i}]")
    return out


def check(doc: Any, name: str) -> list[str]:
    return errors(doc, load(name))
