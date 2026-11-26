"""CPD0-1: the control plane's identifiers (02-lld §1.3) accept their valid vectors and refuse the invalid ones."""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from .support import CONTROL_PLANE, load_schema, registry

COMMON = load_schema(CONTROL_PLANE / "common.schema.json")
VECTORS = json.loads((CONTROL_PLANE / "vectors" / "identifiers.json").read_text(encoding="utf-8"))["cases"]


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"{COMMON['$id']}#/$defs/{name}"}, registry=registry())


def test_every_identifier_has_vectors() -> None:
    identifiers = {"OrgId", "ScopeRef", "ItemRef", "InstallationRef", "BindingRef", "Revision", "ActorRef", "Digest"}
    assert identifiers <= set(COMMON["$defs"])
    assert set(VECTORS) == identifiers


@pytest.mark.parametrize("name", sorted(VECTORS))
def test_valid_identifiers_are_accepted(name: str) -> None:
    validator = _validator(name)
    rejected = [value for value in VECTORS[name]["valid"] if not validator.is_valid(value)]
    assert rejected == []


@pytest.mark.parametrize("name", sorted(VECTORS))
def test_invalid_identifiers_are_refused(name: str) -> None:
    validator = _validator(name)
    accepted = [value for value in VECTORS[name]["invalid"] if validator.is_valid(value)]
    assert accepted == []
