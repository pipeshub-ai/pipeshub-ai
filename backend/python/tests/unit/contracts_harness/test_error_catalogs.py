"""CPD0-4/5: the error catalogues are well formed, and every language gets them as typed constants.

``contracts/control-plane/errors.json`` is 02-lld §10's table; ``contracts/harness/ext/pipeshub/errors.json``
is the harness wire codes Node answers the worker with. Node's and the frontend's constants are checked by
their own suites against the same files.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from .support import CONTRACTS, CONTROL_PLANE, generator, load_schema

CATALOG_SCHEMA = load_schema(CONTRACTS / "core" / "error-catalog.schema.json")
CATALOGS = {
    "control-plane": CONTROL_PLANE / "errors.json",
    "harness": CONTRACTS / "ext" / "pipeshub" / "errors.json",
}

# 02-lld §10, row by row: code -> HTTP status.
LLD_TABLE = {
    "CP_REVISION_CONFLICT": 409, "CP_PRECONDITION_REQUIRED": 428, "CP_WIDENING": 422, "CP_NOT_EXPOSED": 422,
    "CP_OUTSIDE_CEILING": 422, "CP_LOCKED": 422, "CP_UNKNOWN_REFERENCE": 422,
    "CP_INVALID_CONFIG": 422, "CP_FORBIDDEN": 403, "CP_NOT_FOUND": 404, "CP_NOT_INSTALLED": 409, "CP_DISABLED": 409,
    "CP_NOT_APPROVED": 409, "CP_NOT_VISIBLE": 409, "CP_NOT_GRANTED": 409, "CP_CREDENTIAL_MISSING": 409,
    "CP_CREDENTIAL_EXPIRED": 409, "CP_CREDENTIAL_REVOKED": 409, "CP_POLICY_CEILING": 409,
    "CP_APPROVAL_SUPERSEDED": 409, "CP_DESCRIPTOR_CHANGED": 409, "CP_EFFECTIVE_SET_TOO_LARGE": 422,
    "CP_STALE": 409, "CP_UNAVAILABLE": 503, "CP_KILL_SWITCH": 503,
}


def _catalog(name: str) -> dict[str, Any]:
    return json.loads(CATALOGS[name].read_text(encoding="utf-8"))


def _generated_catalog(name: str) -> dict[str, Any]:
    """The catalog as the generated constants hold it: with the codes an edition's overlay adds (``extended_errors``)."""
    gen = generator()
    group = next(g for g in gen.GROUPS if g.key == ("ext/pipeshub" if name == "harness" else "control-plane"))
    extended: dict[str, Any] = gen.extended_errors(group, CATALOG_SCHEMA, gen.OVERLAY_ROOT)
    return extended


@pytest.mark.parametrize("name", sorted(CATALOGS))
def test_catalog_is_an_error_catalog_with_unique_codes(name: str) -> None:
    catalog = _catalog(name)
    assert list(Draft202012Validator(CATALOG_SCHEMA).iter_errors(catalog)) == []
    codes = [entry["code"] for entry in catalog["codes"]]
    assert len(codes) == len(set(codes))


def test_control_plane_catalog_is_the_lld_table() -> None:
    entries = {entry["code"]: entry for entry in _catalog("control-plane")["codes"]}
    assert {code: entry["status"] for code, entry in entries.items()} == LLD_TABLE


def test_control_plane_remediation_actors_are_the_problem_s_actors() -> None:
    from app.control_plane.contracts.generated.enums import REMEDIATION_ACTORS

    actors = {entry.get("remediation") for entry in _catalog("control-plane")["codes"]} - {None}
    assert actors <= set(REMEDIATION_ACTORS)


def test_aliases_are_unique_and_never_a_code() -> None:
    codes = {entry["code"] for name in CATALOGS for entry in _catalog(name)["codes"]}
    aliases = [alias for name in CATALOGS for entry in _catalog(name)["codes"] for alias in entry.get("aliases", [])]
    assert len(aliases) == len(set(aliases))
    assert set(aliases).isdisjoint(codes)


@pytest.mark.parametrize(
    ("name", "module"),
    [
        ("control-plane", "app.control_plane.contracts.generated.errors"),
        ("harness", "app.harness.contracts.generated.pipeshub.errors"),
    ],
)
def test_python_constants_are_the_catalog(name: str, module: str) -> None:
    import importlib

    generated = importlib.import_module(module)
    catalog = _generated_catalog(name)
    assert generated.WIRE_PREFIX == catalog.get("wirePrefix", "")
    assert list(generated.ERROR_CODES) == [entry["code"] for entry in catalog["codes"]]
    for entry in catalog["codes"]:
        assert generated.ERROR_CODES[entry["code"]].model_dump(mode="json", by_alias=True, exclude_none=True) == entry
