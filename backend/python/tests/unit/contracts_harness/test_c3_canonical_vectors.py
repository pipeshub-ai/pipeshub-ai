"""C3: RFC 8785 golden vectors give identical bytes in Python and TypeScript."""

from __future__ import annotations

import json
import random
import struct

import pytest

from harness_core.contracts import validate_contract
from harness_core.contracts.canonical_json import CanonicalJsonError, canonical_json
from harness_core.contracts.generated.labels import Labels

from .support import VECTORS, node_available, run_node

DOC = json.loads(VECTORS.read_text(encoding="utf-8"))
needs_node = pytest.mark.skipif(node_available() is not None, reason=str(node_available()))


@pytest.mark.parametrize("vector", DOC["vectors"], ids=lambda v: v["name"])
def test_python_matches_golden(vector) -> None:
    assert canonical_json(json.loads(vector["input"])) == vector["expected"].encode("utf-8")


@pytest.mark.parametrize("case", DOC["errors"], ids=lambda c: c["name"])
def test_python_rejects(case) -> None:
    if "python" not in case["languages"]:
        pytest.skip("not observable in Python")
    with pytest.raises(CanonicalJsonError):
        canonical_json(json.loads(case["input"]))


@needs_node
def test_typescript_matches_golden_and_python() -> None:
    results = run_node("canonical", [v["input"] for v in DOC["vectors"]])
    for vector, result in zip(DOC["vectors"], results, strict=True):
        assert result == {"ok": True, "canonical": vector["expected"]}, vector["name"]
    errors = [c for c in DOC["errors"] if "typescript" in c["languages"]]
    for case, result in zip(errors, run_node("canonical", [c["input"] for c in errors]), strict=True):
        assert not result["ok"], case["name"]


@needs_node
def test_random_doubles_format_identically() -> None:
    rng = random.Random(8785)
    values: list[float] = []
    while len(values) < 3000:
        (f,) = struct.unpack("<d", rng.getrandbits(64).to_bytes(8, "little"))
        if f == f and abs(f) != float("inf"):
            values.append(f)
    values += [rng.uniform(-1e6, 1e6) for _ in range(1000)] + [rng.randint(-(2**53), 2**53) / 1000 for _ in range(1000)]
    texts = [repr(v) for v in values]
    results = run_node("canonical", texts)
    mismatches = [
        (t, canonical_json(v).decode(), r)
        for t, v, r in zip(texts, values, results, strict=True)
        if r != {"ok": True, "canonical": canonical_json(v).decode()}
    ]
    assert mismatches[:5] == []


_RESERVED = '{"integrity":"external","x":[{"__proto__":{}}]}'


def test_python_contract_validation_rejects_reserved_key() -> None:
    # Checked before the model, so even a closed schema reports the reserved key, not "extra field".
    with pytest.raises(CanonicalJsonError, match="__proto__"):
        validate_contract(Labels, json.loads(_RESERVED))


@needs_node
def test_typescript_contract_validation_rejects_reserved_key() -> None:
    [result] = run_node("parse", [{"schema": "core/labels.schema.json", "json": _RESERVED}])
    assert not result["ok"]
    assert "__proto__" in result["error"]
