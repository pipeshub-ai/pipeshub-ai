"""CPD0-14/15/24, CPD3-3/4: ``x-constant`` enumerations and the policy data files become typed constants, and bad
declarations are refused.

The Node and frontend halves are ``tests/modules/agent-harness/contracts/constants.test.ts`` and
``frontend/lib/contracts/__tests__/generated.test.ts``.
"""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any, get_args

import pytest

from .support import GROUPS, generator

if TYPE_CHECKING:
    from pathlib import Path

CONSTANTS = generator().constants


def _declared(schemas: list[Path]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in schemas:
        for node in generator().schema_doc(path).get("$defs", {}).values():
            if "x-constant" in node:
                target = generator().resolve_def_ref(path, node["$ref"])[2] if "$ref" in node else node
                found[node["x-constant"]] = target["enum"]
    return found


@pytest.mark.parametrize("group", GROUPS, ids=lambda g: g.key)
def test_python_constants_are_the_schema_enums(group: Any) -> None:  # noqa: ANN401 - a generator group
    declared = _declared(group.schemas())
    assert declared, group.key
    enums = importlib.import_module(f"{group.python_package}.enums")
    emitted = {name for name in vars(enums) if name.isupper() and not name.startswith("_")}
    assert emitted == set(declared)
    for constant, values in declared.items():
        assert list(getattr(enums, constant)) == values
    for enum in CONSTANTS.enum_constants(group.schemas(), generator().resolve_def_ref, generator().schema_doc):
        assert get_args(getattr(enums, enum.type_name)) == enum.values


def test_offerings_are_generated_once() -> None:
    from app.harness.access import offering
    from harness_core.access import vendor_rules
    from harness_core.contracts.generated import enums

    assert vendor_rules.OFFERINGS is enums.OFFERINGS
    assert offering.DEFAULT_OFFERING in enums.OFFERINGS


def _schema(tmp_path: Path, defs: dict[str, Any]) -> list[Path]:
    path = tmp_path / "x.schema.json"
    path.write_text(json.dumps({"$defs": defs}), encoding="utf-8")
    return [path]


@pytest.mark.parametrize(
    ("defs", "message"),
    [
        ({"Kind": {"x-constant": "kinds", "enum": ["a"]}}, "UPPER_SNAKE"),
        ({"kind": {"x-constant": "KINDS", "enum": ["a"]}}, "PascalCase"),
        ({"Kind": {"x-constant": "KINDS", "enum": [1, 2]}}, "enum of strings"),
        ({"Kind": {"x-constant": "KINDS", "enum": []}}, "enum of strings"),
        ({"Kind": {"x-constant": "KINDS", "enum": ["a", "a"]}}, "repeats"),
        ({"Kind": {"x-constant": "KINDS", "type": "integer", "enum": [1]}}, "plain string enum"),
        ({"Kind": {"x-constant": "KINDS", "enum": ["a"], "pattern": "^a$"}}, "plain string enum"),
        ({"Kind": {"x-constant": "KINDS", "enum": ["a"]}, "Other": {"x-constant": "KINDS", "enum": ["b"]}}, "declared twice"),
    ],
)
def test_a_bad_x_constant_is_refused_with_its_place(tmp_path: Path, defs: dict[str, Any], message: str) -> None:
    with pytest.raises(CONSTANTS.ConstantsError, match=message) as refused:
        CONSTANTS.enum_constants(_schema(tmp_path, defs))
    assert "x.schema.json" in str(refused.value)


def _catalog_schema() -> dict[str, Any]:
    return json.loads((GROUPS[0].source / CONSTANTS.ERROR_CATALOG_SCHEMA).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("catalog", "message"),
    [
        ({"description": "d", "codes": [{"code": "A_B", "status": 409, "retryable": False, "meaning": "m", "httpStatus": 1}]}, "codes/0"),
        ({"description": "d", "codes": [{"code": "A_B", "status": 409, "retryable": False}]}, "meaning"),
        (
            {"description": "d", "codes": [
                {"code": "A_B", "status": 409, "retryable": False, "meaning": "m", "nextAction": {"kind": "reload", "text": "t"}},
                {"code": "A_B", "status": 410, "retryable": False, "meaning": "m", "nextAction": {"kind": "reload", "text": "t"}},
            ]},
            "repeats",
        ),
    ],
)
def test_a_bad_error_catalog_is_refused_with_its_place(tmp_path: Path, catalog: dict[str, Any], message: str) -> None:
    (tmp_path / CONSTANTS.ERRORS_FILE).write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(CONSTANTS.ConstantsError, match=message) as refused:
        CONSTANTS.error_catalog(tmp_path, _catalog_schema())
    assert "errors.json" in str(refused.value)


def test_a_group_without_a_catalog_has_no_errors_module(tmp_path: Path) -> None:
    assert CONSTANTS.error_catalog(tmp_path, _catalog_schema()) is None


def test_the_envelope_s_enums_are_the_named_ones() -> None:
    # The envelope keeps them inline: datamodel-codegen 0.83.0 cannot resolve a ref to a root-model enum
    # reached through a second file (event -> envelope -> common), so equality is held here instead.
    from harness_core.contracts.generated.enums import VISIBILITIES, WRITERS

    envelope = json.loads((GROUPS[0].source / "envelope.schema.json").read_text(encoding="utf-8"))
    for variant in envelope["$defs"].values():
        assert tuple(variant["properties"]["writer"]["enum"]) == WRITERS
        assert tuple(variant["properties"]["visibility"]["enum"]) == VISIBILITIES


def test_cpd3_4_the_layer_chain_is_named_once_and_re_exported_by_the_control_plane() -> None:
    from app.control_plane.contracts.generated import enums as control_plane
    from harness_core.contracts.generated import enums as core

    common = json.loads((GROUPS[2].source / "common.schema.json").read_text(encoding="utf-8"))["$defs"]["LayerName"]
    assert "enum" not in common
    assert common["$ref"].endswith("core/policy/policy-layer.schema.json#/$defs/LayerName")
    assert control_plane.LAYERS == core.LAYERS


def test_cpd3_4_a_re_export_under_another_name_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "a.schema.json"
    source.write_text(json.dumps({"$defs": {"Other": {"x-constant": "OTHERS", "$ref": "b#/$defs/Kind"}}}), encoding="utf-8")
    target = {"x-constant": "KINDS", "enum": ["x"]}
    with pytest.raises(CONSTANTS.ConstantsError, match="another name"):
        CONSTANTS.enum_constants([source], lambda _path, _ref: (source, "Kind", target))


def test_cpd3_3_the_policy_data_files_are_typed_constants() -> None:
    from harness_core.contracts import dump_contract
    from harness_core.contracts.generated.policy_data import (
        POLICY_DIMENSIONS,
        RELEASE_DEFAULTS,
    )

    policy = GROUPS[0].source / "policy"
    gen = generator()
    dimensions = next(d for d in gen.DATA_FILES if d.constant == "POLICY_DIMENSIONS")
    written = json.loads((policy / "policy-dimensions.json").read_text(encoding="utf-8"))
    # With the rows an edition's overlay adds, when present.
    assert dump_contract(POLICY_DIMENSIONS) == gen.extended_data(dimensions, written, gen.OVERLAY_ROOT)
    assert dump_contract(RELEASE_DEFAULTS) == json.loads((policy / "release.json").read_text(encoding="utf-8"))


def test_cpd3_3_a_data_file_its_schema_refuses_stops_the_generator(tmp_path: Path) -> None:
    schema = GROUPS[0].source / "policy" / "release.schema.json"
    data = tmp_path / "release.json"
    data.write_text(json.dumps({"schemaVersion": 1}), encoding="utf-8")
    with pytest.raises(CONSTANTS.ConstantsError, match="release.json"):
        generator().validated(data, schema)
