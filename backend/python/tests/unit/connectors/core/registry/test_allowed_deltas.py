"""Each listed behaviour difference holds on both sides: main answered ``old``, the live scope ``new``."""

from __future__ import annotations

import pytest
from _main_folder_scope import FolderScope as MainFolderScope
from _main_folder_scope import folder_path_problems as main_folder_path_problems
from allowed_deltas import DELTAS, SAVE_DELTAS, SAVE_UNCHANGED

from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterType,
    ListOperator,
)
from app.connectors.core.registry.folder_scope import (
    RULES_BY_PATH_STYLE,
    FolderScope,
    folder_path_problems,
    saved_folder_values,
)


def _filters(values: list[str], operator: str) -> FilterCollection:
    return FilterCollection(filters=[
        Filter(key="folder_paths", value=values, type=FilterType.LIST,
               operator=ListOperator.NOT_IN if operator == "not_in" else ListOperator.IN),
    ])


def _answer(scope: object, function: str, arg: object) -> object:
    attr = getattr(scope, function)
    return attr(arg) if callable(attr) else attr


@pytest.mark.parametrize("row", DELTAS, ids=[f"{d['id']}-{d['function']}" for d in DELTAS])
def test_delta(row: dict[str, object]) -> None:
    filters = _filters(row["values"], row["operator"])
    main = MainFolderScope.from_filters(filters)
    live = FolderScope.from_filters(filters, rules=RULES_BY_PATH_STYLE[row["rules"]], wildcards=row["wildcards"])
    assert _answer(main, row["function"], row["arg"]) == row["old"]
    assert _answer(live, row["function"], row["arg"]) == row["new"]
    assert row["old"] != row["new"]


@pytest.mark.parametrize("row", DELTAS, ids=[f"{d['id']}-{d['function']}" for d in DELTAS])
def test_no_delta_until_a_connector_reads_its_rules(row: dict[str, object]) -> None:
    """With the default rules and no wildcards, which every connector uses today, nothing changes."""
    filters = _filters(row["values"], row["operator"])
    assert _answer(FolderScope.from_filters(filters), row["function"], row["arg"]) == row["old"]


def _sync_values(row: dict[str, object]) -> dict[str, object]:
    return {"folder_paths": {"operator": row["operator"], "value": [row["value"]]}}


@pytest.mark.parametrize("row", SAVE_DELTAS, ids=[f"{d['id']}-{d['path_style']}" for d in SAVE_DELTAS])
def test_a_save_refuses_the_new_value(row: dict[str, object]) -> None:
    values = _sync_values(row)
    assert main_folder_path_problems(values) == []
    new = folder_path_problems(values, path_style=row["path_style"], wildcards=row["wildcards"], new_rules=True)
    assert len(new) == 1
    kept = folder_path_problems(
        values, path_style=row["path_style"], wildcards=row["wildcards"],
        stored=saved_folder_values(values, values), new_rules=True,
    )
    assert kept == []
    assert folder_path_problems(values) == []


@pytest.mark.parametrize("row", SAVE_UNCHANGED, ids=[f"{d['value']}-{d['path_style']}" for d in SAVE_UNCHANGED])
def test_a_save_still_accepts_the_new_value(row: dict[str, object]) -> None:
    values = _sync_values(row)
    assert main_folder_path_problems(values) == []
    assert folder_path_problems(values, path_style=row["path_style"], new_rules=True) == []


def test_a_new_value_with_a_leading_double_slash_keeps_its_meaning_on_a_share() -> None:
    scope = FolderScope.from_filters(_filters(["//reports"], "in"), rules=RULES_BY_PATH_STYLE["windows"])
    assert scope.list_prefixes == ["reports/"]
    assert scope.includes_file("reports/q.pdf")
