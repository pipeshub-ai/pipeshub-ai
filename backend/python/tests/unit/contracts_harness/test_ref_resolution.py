"""Every ``$ref`` of every contract root resolves, by ``$id`` and by file, to the same definition.

JSON Schema validators resolve a ref against the schema's ``$id``; the generators resolve it by file. A ref
that only works one way (CPD-1 found ``../harness/core/…`` in the control plane resolving under
``…/control-plane/harness/…`` by ``$id``) passes one language and breaks another, so both are checked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import pytest
from referencing.exceptions import Unresolvable

from .support import all_schema_files, generator, load_schema, registry

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

GENERATE = generator()


def _refs(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


def _pointer(doc: Any, frag: str) -> Any:  # noqa: ANN401 - a JSON document
    for part in [p for p in frag.split("/") if p]:
        doc = doc[part.replace("~1", "/").replace("~0", "~")]
    return doc


CASES = [(path, ref) for path in all_schema_files() for ref in sorted(set(_refs(load_schema(path))))]


@pytest.mark.parametrize(("path", "ref"), CASES, ids=[f"{p.name}:{r}" for p, r in CASES])
def test_a_ref_resolves_to_one_definition_by_id_and_by_file(path: Path, ref: str) -> None:
    schema = load_schema(path)
    try:
        by_id = registry().resolver(base_uri=schema["$id"]).lookup(ref).contents
    except Unresolvable as exc:
        pytest.fail(f"{path.name}: {ref} does not resolve against $id {schema['$id']}: {exc}")
    target, _, frag = ref.partition("#")
    file = GENERATE.ref_target(path.resolve(), target, GENERATE.schema_ids(GENERATE.all_groups())) if target else path.resolve()
    assert file.exists(), f"{path.name}: {ref} names no file"
    by_file = _pointer(load_schema(file), frag)
    assert by_id == by_file, f"{path.name}: {ref} resolves to different definitions by $id and by file"


def test_the_check_sees_cross_root_refs() -> None:
    assert any(ref.startswith("https://") for _, ref in CASES)


def test_a_relative_ref_across_roots_is_refused() -> None:
    base = "https://schemas.pipeshub.com/control-plane/v1/common.schema.json"
    wrong = urljoin(base, "../harness/core/common.schema.json")
    assert wrong not in {load_schema(p)["$id"] for p in all_schema_files()}
