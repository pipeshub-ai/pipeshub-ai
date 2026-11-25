"""CPD2-10, CPD2-11: the catalog item contract, its examples and invalid fixtures, and every release item."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tests.unit.contracts_harness.support import registry

REPO = Path(__file__).resolve().parents[5]
CONTROL_PLANE = REPO / "contracts" / "control-plane"
CATALOG_SCHEMAS = ("catalog-item", "descriptors/tool", "descriptors/skill-bundle", "descriptors/engine")
SCHEMAS = [CONTROL_PLANE / f"{name}.schema.json" for name in CATALOG_SCHEMAS]


def _validator(path: Path) -> Draft202012Validator:
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")), registry=registry())


def test_cpd2_10_the_catalog_schemas_exist() -> None:
    assert all(path.exists() for path in SCHEMAS)


@pytest.mark.parametrize("path", SCHEMAS, ids=lambda p: p.relative_to(CONTROL_PLANE).as_posix())
def test_cpd2_10_examples_validate_and_invalid_fixtures_are_refused(path: Path) -> None:
    validator = _validator(path)
    Draft202012Validator.check_schema(validator.schema)
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema.get("examples"), f"{path.name} has no examples"
    for example in schema["examples"]:
        validator.validate(example)
    invalid = path.with_name(path.name.replace(".schema.json", ".invalid.json"))
    fixtures = json.loads(invalid.read_text(encoding="utf-8"))
    assert fixtures
    for fixture in fixtures:
        assert not validator.is_valid(fixture["instance"]), fixture["description"]


@pytest.mark.parametrize("path", sorted((CONTROL_PLANE / "catalog").glob("*.json")), ids=lambda p: p.name)
def test_cpd2_11_every_release_item_is_a_valid_catalog_item(path: Path) -> None:
    validator = _validator(CONTROL_PLANE / "catalog-item.schema.json")
    for item in json.loads(path.read_text(encoding="utf-8"))["items"]:
        errors = sorted(validator.iter_errors(item), key=lambda e: e.path)
        assert not errors, f"{item['ref']}: {errors[0].message}"
