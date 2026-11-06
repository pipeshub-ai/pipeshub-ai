"""Seeded, deterministic document generator and mutators for the graph schema tests.

`build_doc` makes a document a schema accepts, the way a writer that honours the schema would send
it. `mutations` breaks that document in exactly one way (or, for ACCEPTED kinds, changes it in a way that must stay valid). Both walk the JSON schema only; nothing is
hard-coded per collection.

Supported keywords: type (string or list), enum, properties, required, patternProperties (simple
`^literal[class]{m,n}$` patterns), items, minLength/maxLength, minItems, minimum/maximum, anyOf and
oneOf (first branch). `nullable` is deliberately not honoured: neither ArangoDB's draft-4 validator
nor jsonschema reads it, so a null is only valid where the type list says so. A schema using any
other combinator (allOf, not, $ref) raises `Ungeneratable`; the tests turn that into a failure
that names the collection, never a silent skip.
"""

import copy
import random
import re
from collections.abc import Iterator
from typing import Any

from jsonschema import Draft4Validator, validators

# Kinds that must be rejected, then the one kind that must be accepted (a whole number for a "number").
REJECTED = ("required", "wrong_type", "bad_enum", "bool_for_number", "null", "float_for_integer", "unknown_key")
ACCEPTED = ("int_for_number",)
UNKNOWN_KEY = "zzNotInAnySchema"
_MAX_DEPTH = 6
_UNSUPPORTED = ("allOf", "not", "$ref", "if", "dependencies")


class Ungeneratable(Exception):
    """The schema uses something the generator does not understand."""


def _types(schema: dict) -> set[str]:
    declared = schema.get("type")
    return set(declared) if isinstance(declared, list) else ({declared} if declared else set())


def _matches(value: object, types: set[str]) -> bool:
    if not types:
        return True
    if value is None:
        return "null" in types
    if isinstance(value, bool):
        return "boolean" in types
    if isinstance(value, int):
        return bool(types & {"integer", "number"})
    if isinstance(value, float):
        return "number" in types
    return {str: "string", list: "array", dict: "object"}.get(type(value)) in types


# --- a tiny generator for the anchored patterns the schemas use -------------------------------
_ATOM = re.compile(r"(\[[^\]]+\]|[A-Za-z0-9_])(?:\{(\d+)(?:,(\d+))?\})?")


def _expand_class(body: str) -> list[str]:
    chars: list[str] = []
    i = 0
    while i < len(body):
        if i + 2 < len(body) and body[i + 1] == "-":
            chars += [chr(c) for c in range(ord(body[i]), ord(body[i + 2]) + 1)]
            i += 3
        else:
            chars.append(body[i])
            i += 1
    return chars


def from_pattern(pattern: str, rng: random.Random) -> str:
    body = pattern.removeprefix("^").removesuffix("$")
    out, pos = [], 0
    while pos < len(body):
        m = _ATOM.match(body, pos)
        if not m:
            raise Ungeneratable(f"pattern {pattern!r}")
        atom, lo, hi = m.groups()
        pool = _expand_class(atom[1:-1]) if atom.startswith("[") else [atom]
        count = 1 if lo is None else rng.randint(int(lo), min(int(hi or lo), int(lo) + 8))
        out += rng.choices(pool, k=count)
        pos = m.end()
    value = "".join(out)
    if not re.search(pattern, value):
        raise Ungeneratable(f"pattern {pattern!r}")
    return value


# --- values -----------------------------------------------------------------------------------
def _value(schema: dict, rng: random.Random, full: bool, depth: int = 0) -> object:
    if depth > _MAX_DEPTH:
        raise Ungeneratable("schema nests deeper than the generator allows")
    for word in _UNSUPPORTED:
        if word in schema:
            raise Ungeneratable(f"keyword {word!r}")
    for word in ("anyOf", "oneOf"):
        if word in schema:
            return _value({**{k: v for k, v in schema.items() if k != word}, **schema[word][0]}, rng, full, depth + 1)
    types = _types(schema)
    if "enum" in schema:
        choices = [v for v in schema["enum"] if v is not None and _matches(v, types)]
        if not choices:
            raise Ungeneratable("enum has no non-null value of the declared type")
        return rng.choice(choices)
    kind = rng.choice(sorted(types - {"null"})) if types - {"null"} else ("object" if "properties" in schema else "string")
    if kind == "string":
        if "pattern" in schema:
            return from_pattern(schema["pattern"], rng)
        text = "s" + rng.choice(["", "é", "-x", "_9"]) + format(rng.getrandbits(24), "x")
        text = text.ljust(schema.get("minLength", 0), "a")
        return text[: schema["maxLength"]] if "maxLength" in schema else text
    if kind == "integer":
        lo = schema.get("minimum", 0)
        return min(rng.randint(lo, lo + 1000), schema.get("maximum", 10**9))
    if kind == "number":
        return float(schema.get("minimum", 0)) + rng.randint(0, 1000) + 0.5
    if kind == "boolean":
        return rng.choice([True, False])
    if kind == "array":
        items = schema.get("items") or {"type": "string"}
        count = max(schema.get("minItems", 0), 1 if full else 0)
        return [_value(items, rng, full, depth + 1) for _ in range(count)]
    return _object(schema, rng, full, depth)


def _object(schema: dict, rng: random.Random, full: bool, depth: int = 0) -> dict:
    required = set(schema.get("required") or [])
    doc = {
        name: _value(sub, rng, full, depth + 1)
        for name, sub in (schema.get("properties") or {}).items()
        if full or name in required
    }
    missing = required - set(doc)
    if missing:  # a required key the schema does not declare
        raise Ungeneratable(f"required {sorted(missing)} not declared in properties")
    if full:
        for pattern, sub in (schema.get("patternProperties") or {}).items():
            doc[from_pattern(pattern, rng)] = _value(sub, rng, full, depth + 1)
    return doc


def build_doc(schema: dict, seed: str, full: bool = True) -> dict:
    """A document `schema` accepts. `full` sets every declared property; otherwise only required ones."""
    doc = _object(schema, random.Random(f"{seed}:{full}"), full)
    for validator in (validators.validator_for(schema)(schema), Draft4Validator(schema)):
        errors = sorted(e.message for e in validator.iter_errors(doc))
        if errors:  # draft 4 is what ArangoDB enforces
            raise Ungeneratable(f"generated document is invalid ({type(validator).__name__}): {errors[0]}")
    return doc


# --- mutators ---------------------------------------------------------------------------------
Mutation = tuple[str, dict, str]  # (label, mutated doc, dotted path jsonschema reports)


def _slots(schema: dict, value: object, path: tuple = ()) -> Iterator[tuple[tuple, dict]]:
    """(path, property schema) for every declared property present in `value`, nested included."""
    if isinstance(value, dict):
        for name, sub in (schema.get("properties") or {}).items():
            if name in value:
                yield (*path, name), sub
                yield from _slots(sub, value[name], (*path, name))
    elif isinstance(value, list) and isinstance(schema.get("items"), dict) and value:
        yield from _slots(schema["items"], value[0], (*path, 0))


def _set(doc: dict, path: tuple, new: object) -> dict:
    out = copy.deepcopy(doc)
    node = out
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = new
    return out


def _dotted(path: tuple) -> str:
    return ".".join(str(p) for p in path)


def _wrong_values(types: set[str]) -> list[Any]:
    candidates = [("a string", {"string"}), ([], {"array"}), ({}, {"object"}), (1.5, {"number"}), (True, {"boolean"})]
    return [v for v, _ in candidates if not _matches(v, types)]


def mutations(schema: dict, doc: dict, kind: str, skip: frozenset = frozenset()) -> list[Mutation]:
    """Every way to break `doc` in one `kind` of way. Keys in `skip` (edge endpoints) are left alone."""
    found: list[Mutation] = []
    if kind == "required":
        found.extend(
            (f"drop {name}", {k: v for k, v in doc.items() if k != name}, "root")
            for name in schema.get("required") or []
            if name not in skip
        )
    elif kind == "unknown_key":
        if schema.get("additionalProperties") is False:
            found.append((f"add {UNKNOWN_KEY}", {**doc, UNKNOWN_KEY: "x"}, "root"))
    else:
        for path, sub in _slots(schema, doc):
            if path[0] in skip:
                continue
            types = _types(sub)
            label = _dotted(path)
            if kind == "wrong_type" and types:
                found += [(f"{label}={v!r}", _set(doc, path, v), label) for v in _wrong_values(types)[:2]]
            elif kind == "bad_enum" and "enum" in sub:
                found.append((f"{label}=bad enum", _set(doc, path, "zz-not-in-enum"), label))
            elif kind == "bool_for_number" and types and types <= {"integer", "number", "null"} and types - {"null"}:
                found.append((f"{label}=True", _set(doc, path, True), label))
            elif kind == "null" and types and "null" not in types and None not in sub.get("enum", []):
                found.append((f"{label}=None", _set(doc, path, None), label))
            elif kind == "int_for_number" and "number" in types and "integer" not in types:
                found.append((f"{label}=7", _set(doc, path, 7), label))
            elif kind == "float_for_integer" and "integer" in types and "number" not in types:
                found.append((f"{label}=1.5", _set(doc, path, 1.5), label))
    return found


def partial_subsets(doc: dict, seed: str, count: int = 4) -> list[dict]:
    """The empty doc, each single key, and `count` seeded random subsets."""
    rng = random.Random(seed)
    keys = sorted(doc)
    subsets = [[]] + [[k] for k in keys] + [rng.sample(keys, rng.randint(1, len(keys))) for _ in range(count) if keys]
    return [{k: doc[k] for k in sub} for sub in subsets]
