"""Literal folder values decide exactly as main's FolderScope (``_main_folder_scope.py``).

Values here hold no ``*`` or ``?``. Every decision a sync or a clean-up makes
from them, and the key and text it stores or logs, must stay as on main, with
wildcards off and on. Under file-share rules they decide as main does on the
normalised value and path; the differences are the rows of ``allowed_deltas.py``.
"""

from __future__ import annotations

import unicodedata

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
from app.connectors.core.registry.folder_scope import (
    FILE_SHARE_PATHS,
    OBJECT_STORE_PATHS,
    FolderRole,
    FolderScope,
    folder_path_problems,
)

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


def _twist(piece: str, how: str) -> str:
    if how == "swapcase":
        return piece.swapcase()
    if how in ("NFC", "NFD"):
        return unicodedata.normalize(how, piece)
    return piece


@st.composite
def _values_and_paths(draw: st.DrawFn) -> tuple[list[str], bool, list[str]]:
    """Values, an operator, and paths: free ones and ones built from a value with a case or NFC twist."""
    values = draw(st.lists(text, min_size=1, max_size=4))
    exclude = draw(st.booleans())
    paths = draw(st.lists(text, max_size=4))
    for _ in range(draw(st.integers(0, 4))):
        value = draw(st.sampled_from(values))
        how = draw(st.sampled_from(["identity", "swapcase", "NFC", "NFD"]))
        paths.append(_twist(value, how) + draw(text))
    return values, exclude, paths


@settings(max_examples=1500, deadline=None)
@given(_values_and_paths(), st.booleans())
def test_object_store_literals_decide_as_on_main(drawn: tuple[list[str], bool, list[str]], wildcards: bool) -> None:
    values, exclude, paths = drawn
    filters = _filters(values, exclude)
    live = FolderScope.from_filters(filters, rules=OBJECT_STORE_PATHS, wildcards=wildcards)
    main = MainFolderScope.from_filters(filters)
    assert live.patterns == ()
    assert live.is_everything == main.is_everything
    assert live.list_prefixes == main.list_prefixes
    assert live.key() == main.key()
    assert live.describe() == main.describe()
    for path in paths:
        assert live.includes_file(path) == main.includes_file(path), path
        assert live.includes_folder(path) == main.includes_folder(path), path
        assert live.spelled_child(path, "Name") == (f"{path}/Name" if path else "Name")
    for prefix in live.list_prefixes:
        assert live.spelled_root(prefix) == prefix
        # A listing under a prefix returns no folder that is only on the way to a chosen one.
        # (Leading or trailing blanks are trimmed from a folder path, as on main, so a key
        # such as " /" reads as the top; such keys are left out of this clause.)
        for path in paths:
            key = prefix + path
            if key != key.strip():
                continue
            assert live.includes_folder(key) == (live.folder_role(key) is FolderRole.KEEP), key


def _n(s: str) -> str:
    """The file-share normalisation, written out here rather than imported."""
    s = unicodedata.normalize("NFC", s.replace("\\", "/"))
    return unicodedata.normalize("NFC", s.casefold())


def _entry(value: str) -> str:
    return value.strip().replace("\\", "/").strip("/")


def _is_root(scope: FolderScope | MainFolderScope) -> bool:
    return scope.folders in ((), ("",))


@settings(max_examples=1500, deadline=None)
@given(_values_and_paths(), st.booleans())
def test_file_share_literals_decide_as_main_on_normalised_values(
    drawn: tuple[list[str], bool, list[str]], wildcards: bool,
) -> None:
    values, exclude, paths = drawn
    live = FolderScope.from_filters(_filters(values, exclude), rules=FILE_SHARE_PATHS, wildcards=wildcards)
    on_normalised = MainFolderScope.from_filters(_filters([_n(v) for v in values], exclude))
    on_raw = MainFolderScope.from_filters(_filters(values, exclude))
    assert live.patterns == ()
    assert live.is_everything == on_normalised.is_everything
    for path in paths:
        assert live.includes_file(path) == on_normalised.includes_file(_n(path)), path
        assert live.includes_folder(path) == on_normalised.includes_folder(_n(path)), path

    if _is_root(live) == _is_root(on_raw):
        # The stored key and the logged text are main's, on the values as typed.
        assert live.key() == on_raw.key()
        assert live.describe() == on_raw.describe()
    else:
        # Only a backslash can make a value name the root once it separates folders.
        assert any("\\" in v for v in values)

    # Listed once per folder, under the first saved spelling of it.
    assert sorted({_n(p) for p in live.list_prefixes}) == on_normalised.list_prefixes
    assert len({_n(p) for p in live.list_prefixes}) == len(live.list_prefixes)
    if not (live.is_everything or live.exclude):
        for prefix in live.list_prefixes:
            first = next(_entry(v) for v in values if _n(_entry(v)) + "/" == _n(prefix))
            assert prefix == first + "/"
            assert live.spelled_root(prefix) == prefix
        for v in values:
            entry = _entry(v)
            first = next(_entry(o) for o in values if _n(_entry(o)) == _n(entry))
            assert live.spelled_root(entry + "/") == first + "/"


@settings(max_examples=500, deadline=None)
@given(
    st.lists(st.one_of(text, st.none()), max_size=4),
    st.sampled_from(["in", "not_in"]),
    st.sampled_from(["posix", "windows"]),
    st.booleans(),
)
def test_stored_values_are_refused_only_as_on_main(
    values: list[str | None], operator: str, path_style: str, wildcards: bool,
) -> None:
    """A save that keeps every stored value is refused exactly when main refused it."""
    sync_values = {SyncFilterKey.FOLDER_PATHS.value: {"operator": operator, "value": values}}
    stored = frozenset(str(v).strip() for v in values if v is not None)
    assert folder_path_problems(
        sync_values, path_style=path_style, wildcards=wildcards, stored=stored, new_rules=True,
    ) == main_folder_path_problems(sync_values)
