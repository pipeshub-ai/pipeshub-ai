"""C4: string length bounds count Unicode code points in every validator of the contracts.

JSON Schema's minLength/maxLength count code points, and so do jsonschema and pydantic. zod's
.min()/.max() count UTF-16 code units, so a character outside the BMP counts twice there: an
instance the schema admits was rejected by the generated zod models whenever hypothesis happened
to generate enough astral characters near a bound. The generator now refines every bounded string
with the shared code-point helper; these tests pin that for every schema, not only the one seen failing.
"""

from __future__ import annotations

import copy
import json
import re
from typing import TYPE_CHECKING, Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from harness_core.contracts import validate_contract

from .support import (
    REPO,
    ContractSchema,
    bundle,
    generator,
    instance_schemas,
    node_available,
    run_node,
    validator,
)
from .test_c4_generative import _merge_all_of

if TYPE_CHECKING:
    from collections.abc import Iterator

ASTRAL = "\U0001f600"  # one code point, two UTF-16 code units
# Bounds a free-form string cannot be filled with any character: these constrain the alphabet.
_SHAPED = {"pattern", "format", "enum", "const"}

Path = tuple[str | int, ...]


def _is_string(node: dict[str, Any]) -> bool:
    kind = node.get("type")
    return kind == "string" or (isinstance(kind, list) and "string" in kind)


def _bounded_leaves(
    node: object, value: object, path: Path = ()
) -> Iterator[tuple[Path, int]]:
    """Paths in ``value`` to free-form strings whose schema node has a maxLength."""
    if not isinstance(node, dict):
        return
    node = _merge_all_of(node)
    for union in ("anyOf", "oneOf"):
        for branch in node.get(union, []):
            if Draft202012Validator(branch).is_valid(value):
                yield from _bounded_leaves(
                    {**{k: v for k, v in node.items() if k != union}, **branch},
                    value,
                    path,
                )
                return
    if (
        isinstance(value, str)
        and _is_string(node)
        and "maxLength" in node
        and not _SHAPED & node.keys()
    ):
        yield path, node["maxLength"]
    elif isinstance(value, dict):
        for key, child in value.items():
            sub = node.get("properties", {}).get(key, node.get("additionalProperties"))
            yield from _bounded_leaves(sub, child, (*path, key))
    elif isinstance(value, list) and isinstance(node.get("items"), dict):
        for i, child in enumerate(value):
            yield from _bounded_leaves(node["items"], child, (*path, i))


def _with(instance: object, path: Path, text: str) -> object:
    out = copy.deepcopy(instance)
    target: Any = out
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = text
    return out


def _python_accepts(schema: ContractSchema, instance: object) -> bool:
    jsonschema_ok = validator(schema).is_valid(instance)
    try:
        validate_contract(schema.model(), instance)
        pydantic_ok = True
    except ValidationError:
        pydantic_ok = False
    assert jsonschema_ok == pydantic_ok, (
        schema.rel,
        "jsonschema and pydantic disagree",
    )
    return jsonschema_ok


def _cases() -> list[tuple[ContractSchema, Path, int, str, object]]:
    cases = []
    for schema in instance_schemas():
        bundled = bundle(schema)
        for example in schema.examples:
            for path, bound in dict(_bounded_leaves(bundled, example)).items():
                for label, length in (("at", bound), ("over", bound + 1)):
                    cases.append(
                        (
                            schema,
                            path,
                            bound,
                            label,
                            _with(example, path, ASTRAL * length),
                        )
                    )
    return cases


@pytest.mark.timeout(180)
@pytest.mark.skipif(node_available() is not None, reason=str(node_available()))
def test_c4_astral_strings_at_and_over_max_length_get_one_verdict() -> None:
    cases = _cases()
    seen = {(s.rel, p) for s, p, *_ in cases}
    assert (
        "control-plane/legacy-capabilities.schema.json",
        ("toolsetInstances", 0, "authType"),
    ) in seen
    assert len(seen) > 50, "the walk found almost no bounded strings: it is broken"

    python = [_python_accepts(schema, instance) for schema, _, _, _, instance in cases]
    zod = run_node(
        "parse",
        [
            {"schema": s.rel, "json": json.dumps(i, ensure_ascii=False)}
            for s, *_, i in cases
        ],
    )
    mismatches = [
        (s.rel, p, bound, label, py, z.get("error", "")[:200])
        for (s, p, bound, label, _), py, z in zip(cases, python, zod, strict=True)
        if py != z["ok"]
    ]
    assert mismatches == []
    # The bound itself is what decides: exactly maxLength code points passes, one more fails.
    wrong = [
        (s.rel, p, label)
        for (s, p, _, label, _), py in zip(cases, python, strict=True)
        if py != (label == "at")
    ]
    assert wrong == []


# `z.string()`, an optional `.regex(...)`, then a code-unit length check.
_CODE_UNIT_LENGTH = re.compile(
    r'z\.string\(\)(?:\.regex\(new RegExp\("(?:[^"\\]|\\.)*"\)\))?\.(?:min|max|length)\('
)


def test_c4_generated_zod_never_bounds_a_string_in_code_units() -> None:
    refined = 0
    for target in generator().TS_TARGETS:
        for path in sorted((REPO / target.out).rglob("*.ts")):
            text = path.read_text(encoding="utf-8")
            assert not _CODE_UNIT_LENGTH.search(text), path
            refined += text.count(".superRefine(codePointLength(")
    assert refined > 100, (
        "no bounded strings refined by code points: the generator or this check is broken"
    )
