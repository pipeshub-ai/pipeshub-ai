"""Python runner for the golden permissions matrix shared with the Node runner.

Reads the JSON only; never imports Node code. A missing Node tree fails the run
rather than skipping, so a Python-only checkout cannot go falsely green.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tests.unit.modules.authz.matrix_deciders import (
    CURRENT_PHASE,
    DECIDERS,
    Decider,
    is_phase_active,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_BACKEND = Path(__file__).resolve().parents[5]
_MATRIX = _BACKEND / "nodejs/apps/tests/fixtures/authz/permissions-matrix.json"

_DEFAULT_LANGS = ("ts", "py")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Given(_Strict):
    principal: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    grants: list[dict[str, Any]]


class Expect(_Strict):
    allow: bool
    role: str | None = None
    code: str | None = None


class Row(_Strict):
    id: str = Field(min_length=1)
    decider: str = Field(min_length=1)
    since: str = Field(pattern=r"^PH-\d{2}$")
    langs: list[Literal["ts", "py", "fe"]] | None = Field(default=None, min_length=1)
    given: Given
    action: str = Field(min_length=1)
    expect: Expect


class Matrix(_Strict):
    version: Literal[1]
    rows: list[Row]


def validate_matrix(
    doc: object, registry: Mapping[str, Decider], current_phase: str, lang: str = "py"
) -> tuple[list[Row], list[str]]:
    try:
        matrix = Matrix.model_validate(doc)
    except ValidationError as exc:
        return [], [f"schema: {e['loc']}: {e['msg']}" for e in exc.errors()]
    errors: list[str] = []
    seen: set[str] = set()
    for row in matrix.rows:
        if row.id in seen:
            errors.append(f'row "{row.id}": duplicate id')
        seen.add(row.id)
        targets_lang = lang in (row.langs or _DEFAULT_LANGS)
        if targets_lang and is_phase_active(row.since, current_phase) and row.decider not in registry:
            errors.append(
                f'row "{row.id}": no decider "{row.decider}" registered '
                f"(since {row.since} <= {current_phase})"
            )
    return matrix.rows, errors


def _row(id_: str, decider: str = "known", since: str = "PH-00") -> dict[str, Any]:
    return {
        "id": id_,
        "decider": decider,
        "since": since,
        "given": {"principal": "u1", "resource": "r1", "grants": []},
        "action": "read",
        "expect": {"allow": True},
    }


def _load_matrix() -> object:
    assert _MATRIX.is_file(), f"golden matrix not found at {_MATRIX}; the backend/nodejs tree is required"
    return json.loads(_MATRIX.read_text())


_rows, _errors = validate_matrix(_load_matrix(), DECIDERS, CURRENT_PHASE)


def test_committed_matrix_is_valid() -> None:
    assert _errors == []


def test_row_count_matches_node_runner() -> None:
    raw = _load_matrix()
    assert isinstance(raw, dict)
    assert len(_rows) == len(raw["rows"])


@pytest.mark.parametrize("row", _rows, ids=[r.id for r in _rows])
def test_matrix_row(row: Row) -> None:
    if "py" not in (row.langs or _DEFAULT_LANGS):
        pytest.skip("row does not target py")
    if not is_phase_active(row.since):
        pytest.skip(f"enabled in {row.since}")
    decision = DECIDERS[row.decider](row.model_dump())
    assert decision == row.expect.model_dump(exclude_none=True)


class TestValidation:
    registry: dict[str, Decider] = {"known": lambda _row: {"allow": True}}

    def test_accepts_empty_matrix(self) -> None:
        assert validate_matrix({"version": 1, "rows": []}, self.registry, "PH-00") == ([], [])

    def test_names_duplicate_id(self) -> None:
        _, errors = validate_matrix(
            {"version": 1, "rows": [_row("dup"), _row("dup")]}, self.registry, "PH-00"
        )
        assert len(errors) == 1
        assert '"dup"' in errors[0] and "duplicate" in errors[0]

    def test_names_unknown_decider(self) -> None:
        _, errors = validate_matrix(
            {"version": 1, "rows": [_row("r-missing", "nope")]}, self.registry, "PH-00"
        )
        assert len(errors) == 1
        assert '"r-missing"' in errors[0] and '"nope"' in errors[0]

    def test_later_phase_row_needs_no_decider(self) -> None:
        _, errors = validate_matrix(
            {"version": 1, "rows": [_row("r-later", "nope", "PH-03")]}, self.registry, "PH-00"
        )
        assert errors == []

    def test_rejects_malformed_row(self) -> None:
        _, errors = validate_matrix({"version": 1, "rows": [{"id": "x"}]}, self.registry, "PH-00")
        assert errors
