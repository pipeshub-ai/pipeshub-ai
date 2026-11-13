"""Literal folder values decide exactly as main's FolderScope (``_main_folder_scope.py``).

Values here hold no ``*`` or ``?``. Every decision a sync or a clean-up makes
from them, and the key and text it stores or logs, must stay as on main.
"""

from __future__ import annotations

from _main_folder_scope import FolderScope as MainFolderScope
from _main_folder_scope import folder_path_problems as main_folder_path_problems
from hypothesis import given, settings
from hypothesis import strategies as st

from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterType,
    ListOperator,
    SyncFilterKey,
)
from app.connectors.core.registry.folder_scope import FolderScope, folder_path_problems

# Pieces that collide: case and NFC/NFD twins, brackets, a backslash, empty
# segments, a newline, a dot and a space, and `..`.
PIECES = ("a", "A", "\u00e9", "e\u0301", " ", ".", "..", "[", "]", "\\", "/", "//", "\n")
text = st.lists(st.sampled_from(PIECES), max_size=6).map("".join)


def _filters(values: list[str], exclude: bool) -> FilterCollection:
    return FilterCollection(filters=[
        Filter(
            key=SyncFilterKey.FOLDER_PATHS.value,
            value=values,
            type=FilterType.LIST,
            operator=ListOperator.NOT_IN if exclude else ListOperator.IN,
        )
    ])


@settings(max_examples=1500, deadline=None)
@given(st.lists(text, min_size=1, max_size=4), st.booleans(), st.lists(text, max_size=8))
def test_literal_values_decide_as_on_main(values: list[str], exclude: bool, paths: list[str]) -> None:
    filters = _filters(values, exclude)
    live, main = FolderScope.from_filters(filters), MainFolderScope.from_filters(filters)
    assert live.is_everything == main.is_everything
    assert live.list_prefixes == main.list_prefixes
    assert live.key() == main.key()
    assert live.describe() == main.describe()
    for path in paths:
        assert live.includes_file(path) == main.includes_file(path), path
        assert live.includes_folder(path) == main.includes_folder(path), path


@settings(max_examples=500, deadline=None)
@given(st.lists(st.one_of(text, st.none()), max_size=4), st.sampled_from(["in", "not_in"]))
def test_literal_values_are_refused_as_on_main(values: list[str | None], operator: str) -> None:
    sync_values = {SyncFilterKey.FOLDER_PATHS.value: {"operator": operator, "value": values}}
    assert folder_path_problems(sync_values) == main_folder_path_problems(sync_values)


def test_no_folder_filter_is_everything_as_on_main() -> None:
    assert FolderScope.from_filters(None) == FolderScope()
    assert MainFolderScope.from_filters(None).is_everything
    assert FolderScope.from_filters(FilterCollection()).key() == MainFolderScope.from_filters(FilterCollection()).key()
