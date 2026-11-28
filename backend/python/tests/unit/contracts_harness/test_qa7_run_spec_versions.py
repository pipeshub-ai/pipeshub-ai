"""QA7-41 (CON-13, Python half): every RunSpec schemaVersion a reader accepts has a golden, and Python reads it.

Node's half (``qa7-run-spec-versions.test.ts``) upcasts each golden; together they make a schemaVersion
bump without an upcaster and an older golden fail.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver.capabilities import SUPPORTED_SPEC_SCHEMA_VERSIONS
from harness_core.driver.kernels import executor_kind

from .support import CONTRACTS

VECTOR: dict[str, Any] = json.loads((CONTRACTS / "vectors" / "run-spec-versions.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((CONTRACTS / "core" / "run-spec.schema.json").read_text(encoding="utf-8"))


def test_qa7_41_the_schema_python_and_the_goldens_name_the_same_versions() -> None:
    schema_versions = tuple(SCHEMA["properties"]["schemaVersion"]["enum"])
    golden_versions = tuple(g["schemaVersion"] for g in VECTOR["goldens"])
    assert schema_versions == SUPPORTED_SPEC_SCHEMA_VERSIONS == golden_versions
    assert VECTOR["current"] == max(schema_versions)


@pytest.mark.parametrize("golden", VECTOR["goldens"], ids=lambda g: f"v{g['schemaVersion']}")
def test_qa7_41_python_reads_each_golden_and_its_upcast_form(golden: dict[str, Any]) -> None:
    spec = RunSpec.model_validate(golden["spec"])
    assert spec.schema_version == golden["schemaVersion"]
    assert executor_kind(spec) == golden["executorKind"]
    upcast = RunSpec.model_validate(golden["upcast"])
    assert upcast.schema_version == VECTOR["current"]
    assert executor_kind(upcast) == golden["executorKind"]
