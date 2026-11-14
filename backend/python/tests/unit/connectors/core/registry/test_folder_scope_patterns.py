"""Folder patterns, roles, listing prefixes, spelling and the newer folder value checks.

Patterns are read only by a connector that asks for them
(``FolderScope.from_filters(..., wildcards=True)``); every connector still reads
values as literal folders, which ``test_folder_scope_compat.py`` holds to the
behaviour before patterns existed.
"""

from __future__ import annotations

import time
import unicodedata

import pytest
from _main_folder_scope import FolderScope as MainFolderScope

from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterType,
    ListOperator,
)
from app.connectors.core.registry.folder_scope import (
    FILE_SHARE_PATHS,
    FOLDER_PATH_MESSAGES,
    OBJECT_STORE_PATHS,
    RULES_BY_PATH_STYLE,
    FolderPattern,
    FolderRole,
    FolderScope,
    PathRules,
    folder_path_problems,
    folder_value_problems,
    saved_folder_values,
)

KEEP, PASS, SKIP = FolderRole.KEEP, FolderRole.PASS_THROUGH, FolderRole.SKIP
OS, FS = "os", "fs"
RULES = {OS: OBJECT_STORE_PATHS, FS: FILE_SHARE_PATHS}
NFD_CAFE = unicodedata.normalize("NFD", "café")
NFC_CAFE = unicodedata.normalize("NFC", "café")


def _filters(values: list[str], op: str = "in") -> FilterCollection:
    return FilterCollection(filters=[
        Filter(
            key="folder_paths", value=values, type=FilterType.LIST,
            operator=ListOperator.NOT_IN if op == "not_in" else ListOperator.IN,
        ),
    ])


def scope(values: list[str], op: str = "in", rules: str = OS, *, wildcards: bool = True) -> FolderScope:
    return FolderScope.from_filters(_filters(values, op), rules=RULES[rules], wildcards=wildcards)


# The truth table. Each check is (kind, path, expected): kind "file" asks
# includes_file, "role" asks folder_role. Rows marked main=True decide as before
# patterns existed, so they also hold with wildcards off and on main's scope.
TRUTH_TABLE = [
    (1, OS, "in", ["reports"], [("file", "reports/q1.pdf", True)], True),
    (2, OS, "in", ["reports"], [("file", "reports-old/a.pdf", False)], True),
    (3, OS, "in", ["a/b"], [("role", "a", PASS)], True),
    (4, OS, "in", ["customer/*/quotes"], [("file", "customer/acme/quotes/q.pdf", True)], False),
    (5, OS, "in", ["customer/*/quotes"], [("file", "customer/acme/quotes/2026/q.pdf", True)], False),
    (6, OS, "in", ["customer/*/quotes"], [("file", "customer/acme/invoices/i.pdf", False)], False),
    (7, OS, "in", ["customer/*/quotes"], [("file", "customer/acme/quotes", False)], False),
    (8, OS, "in", ["customer/*/quotes"], [("role", "customer/acme", PASS)], False),
    (9, OS, "in", ["customer/*/quotes"], [("role", "customer/acme/invoices", SKIP)], False),
    (10, OS, "in", ["customer/*/quotes"], [("file", "customer/a/b/quotes/x", False)], False),
    (11, OS, "in", ["customer/**/quotes"],
     [("file", "customer/quotes/x", True), ("file", "customer/a/b/quotes/x", True)], False),
    (12, OS, "in", ["**/quotes"],
     [("file", "quotes/x", True), ("file", "a/quotes/x", True), ("file", "a/quotesx/y", False),
      ("role", "a/b", PASS)], False),
    (13, OS, "in", ["a/**"], [("file", "a/x", True), ("role", "a", KEEP), ("file", "b/x", False)], False),
    (16, OS, "not_in", ["archive/**"],
     [("file", "archive/x", False), ("role", "archive", SKIP), ("file", "docs/x", True),
      ("file", "r.txt", True)], False),
    (17, OS, "not_in", ["**/tmp"],
     [("file", "a/tmp/x", False), ("file", "a/tmpx/y", True), ("role", "a", KEEP)], False),
    (18, OS, "in", ["q?"], [("file", "q1/x", True), ("file", "q12/x", False)], False),
    (19, OS, "in", ["rep*"],
     [("file", "reports/x", True), ("file", "rep/x", True), ("file", "x/reports/y", False)], False),
    (20, OS, "in", ["quotes"], [("file", "x/quotes/y", False), ("file", "quotes/y", True)], True),
    (21, OS, "in", ["Reports"], [("file", "reports/x", False)], True),
    (22, FS, "in", ["Reports"], [("file", "reports/x", True), ("role", "REPORTS", KEEP)], False),
    (23, FS, "not_in", ["Reports"], [("role", "reports", SKIP), ("file", "REPORTS/x", False)], False),
    (24, FS, "in", ["reports\\2026"], [("file", "reports/2026/x", True), ("role", "reports", PASS)], False),
    (25, OS, "in", ["reports\\2026"], [("file", "reports\\2026/x", True), ("file", "reports/2026/x", False)], True),
    (26, OS, "in", ["a\\*b"], [("file", "a*b/x", True), ("file", "axxb/x", False)], False),
    (27, OS, "in", ["a*b"], [("file", "axxb/x", True), ("file", "a*b/x", True)], False),
    (28, OS, "in", ["Project [2024]"], [("file", "Project [2024]/x", True), ("file", "Project 2/x", False)], True),
    (28, FS, "in", ["Project [2024]"], [("file", "Project [2024]/x", True), ("file", "Project 2/x", False)], False),
    (29, FS, "in", [NFD_CAFE], [("file", f"{NFC_CAFE}/x", True)], False),
    (30, OS, "in", [NFC_CAFE], [("file", f"{NFD_CAFE}/x", False), ("file", f"{NFC_CAFE}/x", True)], True),
    (32, OS, "in", ["a//b"], [("file", "a//b/x", True), ("file", "a/b/x", False)], True),
    (32, FS, "in", ["a//b"], [("file", "a//b/x", True), ("file", "a/b/x", False)], False),
    (33, OS, "in", ["customer/*/quotes", "customer/acme"],
     [("file", "customer/acme/invoices/x", True), ("file", "customer/beta/quotes/x", True),
      ("file", "customer/beta/invoices/x", False)], False),
    (35, OS, "not_in", ["*"], [("file", "a/x", False), ("file", "r.txt", True)], False),
    (35, FS, "not_in", ["*"], [("file", "a/x", False), ("file", "r.txt", True)], False),
    (36, OS, "in", ["*/quotes"], [("file", ".hidden/quotes/x", True)], False),
    (36, FS, "in", ["*/quotes"], [("file", ".hidden/quotes/x", True)], False),
    (38, OS, "in", ["quotes"], [("file", "quotes\n/x", False)], True),
    (45, OS, "in", ["report?"],
     [("file", "report /q.pdf", True), ("role", "report ", KEEP), ("role", "report /2026", KEEP)], False),
    (45, FS, "in", ["*/ quotes"],
     [("file", "a/ quotes/q.pdf", True), ("role", "a/ quotes", KEEP), ("role", "a", PASS)], False),
    (45, OS, "not_in", ["tmp?"], [("file", "tmp /x", False), ("role", "tmp ", SKIP)], False),
    # An empty name between two slashes is a folder level of its own; outer slashes are trimmed.
    (46, OS, "in", ["a/*/quotes"],
     [("file", "a//b/quotes/f", False), ("role", "a//b/quotes", SKIP), ("role", "a//b", SKIP),
      ("file", "a/b/quotes/f", True), ("file", "/a/b/quotes/f", True), ("role", "/a/b/quotes/", KEEP)], False),
    (46, OS, "not_in", ["a/*/quotes"], [("file", "a//b/quotes/f", True), ("role", "a//b/quotes", KEEP)], False),
    (47, OS, "in", ["a/*/b/quotes"], [("file", "a//b/quotes/f", True), ("role", "a//b/quotes", KEEP)], False),
    (48, OS, "in", ["a/**/quotes"], [("file", "a//quotes/f", True), ("file", "a///x/quotes/f", True)], False),
    (49, OS, "in", ["a/?"], [("file", "a//f", False), ("file", "a/b/f", True)], False),
    (50, FS, "in", ["a/*/quotes"],
     [("file", "a\\\\b\\quotes\\f", False), ("file", "a\\b\\quotes\\f", True)], False),
    (38, OS, "in", ["quote?"], [("file", "quotes\n/x", False), ("file", "quotes/x", True)], False),
]


def _ask(s: FolderScope | MainFolderScope, kind: str, path: str) -> object:
    if kind == "file":
        return s.includes_file(path)
    if isinstance(s, MainFolderScope):
        return s.includes_folder(path)
    return s.folder_role(path)


class TestPatternGrammar:
    @pytest.mark.parametrize(("row", "rules", "op", "values", "checks", "main"), TRUTH_TABLE,
                             ids=[f"row{r[0]}-{r[1]}" for r in TRUTH_TABLE])
    def test_truth_table(self, row, rules, op, values, checks, main) -> None:
        live = scope(values, op, rules)
        for kind, path, expected in checks:
            assert _ask(live, kind, path) == expected, (row, kind, path)

    @pytest.mark.parametrize(("row", "rules", "op", "values", "checks", "main"),
                             [r for r in TRUTH_TABLE if r[5]], ids=[f"row{r[0]}" for r in TRUTH_TABLE if r[5]])
    def test_rows_marked_main_decide_as_main_with_wildcards_off(self, row, rules, op, values, checks, main) -> None:
        main_scope = MainFolderScope.from_filters(_filters(values, op))
        for wildcards in (False, True):
            live = scope(values, op, rules, wildcards=wildcards)
            for kind, path, expected in checks:
                want = expected if kind == "file" else expected is not SKIP
                assert _ask(main_scope, kind, path) == want
                got = _ask(live, kind, path)
                assert (got if kind == "file" else got is not SKIP) == want, (row, wildcards, path)

    @pytest.mark.parametrize("rules", [OS, FS])
    @pytest.mark.parametrize("values", [["**"], ["/"], [""], ["**/**"]])
    def test_row14_the_root_includes_everything(self, rules, values) -> None:
        live = scope(values, "in", rules)
        assert live.is_everything
        assert live.list_prefixes == [""]
        assert live.includes_file("any/where.txt")
        assert live.folder_role("any") is KEEP

    @pytest.mark.parametrize("rules", [OS, FS])
    def test_row15_a_stored_exclude_of_all_folders_excludes_everything(self, rules) -> None:
        live = scope(["**"], "not_in", rules)
        assert not live.includes_file("r.txt")
        assert not live.includes_file("a/x")
        assert live.folder_role("a") is SKIP
        assert live.key() == MainFolderScope.from_filters(_filters(["/"], "not_in")).key()

    @pytest.mark.parametrize("rules", [OS, FS])
    def test_row31_outer_slashes_do_not_matter(self, rules) -> None:
        base = scope(["reports"], "in", rules)
        for values in (["reports/"], ["/reports"], [" /reports/ "]):
            other = scope(values, "in", rules)
            assert other.key() == base.key()
            assert other.list_prefixes == base.list_prefixes
            assert other.includes_file("reports/x") and not other.includes_file("reportsx/y")

    def test_row34_an_inner_value_is_walked_under_the_outer_spelling(self) -> None:
        live = scope(["Reports", "reports/2026"], "in", FS)
        assert live.list_prefixes == ["Reports/"]
        assert live.spelled_root("Reports/") == "Reports/"
        assert live.spelled_child("Reports", "2026") == "Reports/2026"

    def test_row39_a_pattern_inside_a_literal_is_dropped(self) -> None:
        for values in (["customer", "customer/*/quotes"], ["customer/*/quotes", "customer"]):
            for op in ("in", "not_in"):
                live = scope(values, op)
                assert live.patterns == ()
                assert live.key() == MainFolderScope.from_filters(_filters(["customer"], op)).key()

    def test_row39_a_pattern_inside_a_literal_is_dropped_whatever_the_case_on_a_share(self) -> None:
        assert scope(["Customer", "customer/*/quotes"], "in", FS).patterns == ()
        assert len(scope(["Customer", "customer/*/quotes"], "in", OS).patterns) == 1

    def test_a_pattern_names_folders_never_files(self) -> None:
        live = scope(["*.pdf"])
        assert not live.includes_file("a.pdf")
        assert live.includes_file("x.pdf/a.txt")

    def test_patterns_are_off_unless_asked_for(self) -> None:
        live = scope(["customer/*/quotes"], wildcards=False)
        assert live.patterns == ()
        assert live.folders == ("customer/*/quotes/",)
        assert not live.includes_file("customer/acme/quotes/q.pdf")
        assert live.includes_file("customer/*/quotes/q.pdf")


class TestFolderRole:
    def test_include_roles(self) -> None:
        live = scope(["customer/*/quotes", "legal"])
        assert live.folder_role("") is PASS
        assert live.folder_role("customer") is PASS
        assert live.folder_role("customer/acme/quotes") is KEEP
        assert live.folder_role("customer/acme/quotes/2026") is KEEP
        assert live.folder_role("legal/x") is KEEP
        assert live.folder_role("other") is SKIP

    def test_exclude_never_passes_through(self) -> None:
        live = scope(["customer/*/quotes"], "not_in")
        assert live.folder_role("customer") is KEEP
        assert live.folder_role("customer/acme") is KEEP
        assert live.folder_role("customer/acme/quotes") is SKIP

    def test_includes_folder_is_not_skip(self) -> None:
        live = scope(["customer/*/quotes"])
        for path in ("customer", "customer/acme", "customer/acme/quotes", "customer/acme/x", "z"):
            assert live.includes_folder(path) == (live.folder_role(path) is not SKIP)

    def test_the_file_memo_is_read_once(self) -> None:
        """The memo is one tuple swapped whole, so a reader never pairs one parent with another's answer."""
        live = scope(["customer/*/quotes"])
        assert live.includes_file("customer/a/quotes/f.pdf")

        class Racing(list):
            # Another caller replaces the entry between two reads of it.
            def __getitem__(self, i: int) -> tuple[str | None, bool]:
                value = super().__getitem__(i)
                super().__setitem__(i, ("customer/b/quotes", False))
                return value

        object.__setattr__(live, "_file_memo", Racing(live._file_memo))
        assert live.includes_file("customer/a/quotes/g.pdf") is True
        assert live.includes_file("customer/b/x/g.pdf") is False

    def test_a_double_star_pattern_is_a_state_set_not_backtracking(self) -> None:
        live = scope(["**/a/**/b/**/c/**/d"])
        deep = "/".join(["a"] * 400)
        start = time.perf_counter()
        assert live.folder_role(deep) is PASS
        assert time.perf_counter() - start < 1.0


class TestListPrefixes:
    @pytest.mark.parametrize("rules", [OS, FS])
    def test_row42_a_literal_inside_a_pattern_start_is_not_listed_twice(self, rules) -> None:
        for values in (["customer/acme/x", "customer/*/quotes"], ["customer/*/quotes", "customer/acme/x"]):
            assert scope(values, "in", rules).list_prefixes == ["customer/"]

    def test_containment_is_checked_against_every_candidate(self) -> None:
        assert scope(["a/b/c/*", "a/b"]).list_prefixes == ["a/b/"]
        assert scope(["a/b/*", "a/b/c"]).list_prefixes == ["a/b/"]

    def test_equal_values_on_a_share_keep_the_first_saved(self) -> None:
        assert scope(["reports", "Reports"], "in", FS).list_prefixes == ["reports/"]
        assert scope(["Reports", "reports"], "in", FS).list_prefixes == ["Reports/"]

    def test_a_literal_wins_a_tie_with_a_pattern_start(self) -> None:
        assert scope(["Customer/*/x", "customer"], "in", FS).list_prefixes == ["customer/"]

    def test_raw_forms_sorted(self) -> None:
        assert scope(["zeta", "Alpha/*", "beta/x"]).list_prefixes == ["Alpha/", "beta/x/", "zeta/"]

    def test_a_pattern_without_a_fixed_start_lists_everything(self) -> None:
        assert scope(["legal", "*/quotes"]).list_prefixes == [""]

    def test_a_backslash_separates_on_a_share(self) -> None:
        assert scope(["reports\\2026"], "in", FS).list_prefixes == ["reports/2026/"]

    def test_an_escape_is_resolved_in_the_listing_prefix(self) -> None:
        assert scope(["a\\*b/*/x"]).list_prefixes == ["a*b/"]

    def test_exclude_lists_everything(self) -> None:
        assert scope(["a/*"], "not_in").list_prefixes == [""]


class TestSpelling:
    def test_object_stores_keep_the_path(self) -> None:
        live = scope(["A/x", "a/y", "*/z"])
        assert live.spelled_root("a/") == "a/"
        assert live.spelled_child("", "a") == "a"
        assert live.spelled_child("a", "Y") == "a/Y"

    def test_a_literal_root_is_its_own_text(self) -> None:
        live = scope(["A/x", "a/y"], "in", FS)
        assert live.list_prefixes == ["A/x/", "a/y/"]
        assert live.spelled_root("A/x/") == "A/x/"
        assert live.spelled_root("a/y/") == "a/y/"

    def test_an_equal_later_value_is_spelled_as_the_first(self) -> None:
        live = scope(["Reports", "reports"], "in", FS)
        assert live.spelled_root("reports/") == "Reports/"

    def test_row43_a_literal_spells_a_pattern_start(self) -> None:
        live = scope(["customer/acme", "Customer/*/quotes"], "in", FS)
        assert live.list_prefixes == ["Customer/"]
        assert live.spelled_root("Customer/") == "customer/"
        assert live.spelled_child("customer", "ACME") == "customer/acme"
        assert live.spelled_child("customer/acme", "Docs") == "customer/acme/Docs"
        assert live.spelled_child("customer", "beta") == "customer/beta"

    def test_a_pattern_start_with_no_literal_keeps_its_text(self) -> None:
        live = scope(["Customer/*/quotes"], "in", FS)
        assert live.spelled_root("Customer/") == "Customer/"

    def test_row40_ids_under_a_literal_stay_as_main_wrote_them(self) -> None:
        live = scope(["Reports", "*/archive"], "in", FS)
        assert live.list_prefixes == [""]
        assert live.spelled_child("", "reports") == "Reports"
        assert live.spelled_child("Reports", "x") == "Reports/x"
        assert live.spelled_child("", "other") == "other"

    def test_row41_ancestors_are_spelled_from_the_literal(self) -> None:
        live = scope(["a/Reports", "*/archive"], "in", FS)
        assert live.spelled_child("", "A") == "a"
        assert live.spelled_child("a", "reports") == "a/Reports"
        assert live.spelled_child("a/Reports", "x") == "a/Reports/x"
        assert live.spelled_child("a", "archive") == "a/archive"

    def test_row44_a_shared_segment_takes_the_first_saved_spelling(self) -> None:
        live = scope(["A/x", "a/y", "*/z"], "in", FS)
        assert live.spelled_child("", "a") == "A"
        assert live.spelled_child("A", "y") == "A/y"
        assert live.spelled_child("A/y", "f") == "A/y/f"

    def test_an_inner_literal_never_respells_inside_an_outer_one(self) -> None:
        live = scope(["Reports", "*/x", "reports/Inner"], "in", FS)
        assert live.spelled_child("Reports", "inner") == "Reports/inner"

    def test_exclude_never_respells(self) -> None:
        live = scope(["a/Reports"], "not_in", FS)
        assert live.spelled_child("", "A") == "A"
        assert live.spelled_root("") == ""

    def test_a_spelled_child_is_its_parent_plus_one_name(self) -> None:
        live = scope(["a/Reports", "B/c", "*/archive"], "in", FS)
        for parent, name in (("", "A"), ("a", "REPORTS"), ("a/Reports", "x"), ("", "b"), ("B", "C"), ("x", "y")):
            out = live.spelled_child(parent, name)
            head, _, last = out.rpartition("/")
            assert head == parent
            assert FILE_SHARE_PATHS.normalise(last) == FILE_SHARE_PATHS.normalise(name)


class TestKeyStability:
    @pytest.mark.parametrize("rules", [OS, FS])
    def test_a_literal_scope_keeps_mains_key_and_text(self, rules) -> None:
        values = ["reports/2026", "Legal", " /x/ "]
        for op in ("in", "not_in"):
            main = MainFolderScope.from_filters(_filters(values, op))
            for wildcards in (False, True):
                live = scope(values, op, rules, wildcards=wildcards)
                assert live.key() == main.key()
                assert live.describe() == main.describe()

    def test_patterns_are_added_to_the_key(self) -> None:
        live = scope(["legal", "customer/*/quotes"])
        assert live.key() == '{"exclude": false, "folders": ["legal/"], "patterns": ["customer/*/quotes"]}'
        assert live.describe() == "only legal/, customer/*/quotes"
        assert scope(["customer/*/quotes", "legal"]).key() == live.key()
        assert scope(["customer/*/quotes"], "not_in").describe() == "all folders except customer/*/quotes"

    def test_a_changed_pattern_changes_the_key(self) -> None:
        assert scope(["customer/*/quotes"]).key() != scope(["customer/acme/quotes"]).key()
        assert scope(["customer/*/quotes"]).key() != scope(["customer/*/quotes"], "not_in").key()


class TestEquality:
    @pytest.mark.parametrize("rules", [OS, FS])
    def test_the_order_of_values_does_not_matter(self, rules) -> None:
        for op in ("in", "not_in"):
            a = scope(["legal", "reports/2026", "customer/*/quotes", "**/tmp"], op, rules)
            b = scope(["**/tmp", "reports/2026", "customer/*/quotes", "legal", "legal"], op, rules)
            assert a == b
            assert hash(a) == hash(b)
            assert a.key() == b.key()

    def test_scopes_that_spell_differently_are_not_equal(self) -> None:
        a = scope(["Reports/x", "reports/y", "*/z"], "in", FS)
        b = scope(["reports/y", "Reports/x", "*/z"], "in", FS)
        assert a.spelled_child("", "REPORTS") != b.spelled_child("", "REPORTS")
        assert a != b

    def test_different_scopes_are_not_equal(self) -> None:
        assert scope(["a"]) != scope(["b"])
        assert scope(["a"]) != scope(["a"], "not_in")
        assert scope(["a/*"]) != scope(["a/?"])
        assert scope(["A"], "in", FS) != scope(["A"], "in", OS)

    def test_a_literal_scope_equals_one_built_directly(self) -> None:
        assert scope(["b", "a"], wildcards=False) == FolderScope(("a/", "b/"))
        assert FolderScope.from_filters(None) == FolderScope()


class TestMatchTracker:
    def test_include_records_kept_folders_and_files(self) -> None:
        live = scope(["customer/*/quotes", "custmer/*/quotes", "**/x", "legal"])
        tracker = live.tracker()
        tracker.note_folder("customer/acme", live.folder_role("customer/acme"))
        assert tracker.matched() == frozenset()
        tracker.note_folder("customer/acme/quotes", live.folder_role("customer/acme/quotes"))
        tracker.note_file("a/x/f.txt")
        tracker.note_file("legal/f.txt")
        assert tracker.matched() == frozenset({"customer/*/quotes", "**/x"})

    def test_exclude_records_skipped_folders(self) -> None:
        live = scope(["archive/**", "tmp*"], "not_in")
        tracker = live.tracker()
        tracker.note_folder("docs", live.folder_role("docs"))
        tracker.note_folder("archive", live.folder_role("archive"))
        assert tracker.matched() == frozenset({"archive/**"})

    def test_a_file_path_on_a_share_is_read_with_backslashes_as_separators(self) -> None:
        live = scope(["customer/*/quotes"], "in", FS)
        tracker = live.tracker()
        tracker.note_file("customer\\acme\\quotes\\q.pdf")
        assert tracker.matched() == frozenset({"customer/*/quotes"})

    def test_a_backslash_stays_a_character_on_an_object_store(self) -> None:
        live = scope(["customer/*/quotes"])
        tracker = live.tracker()
        tracker.note_file("customer\\acme\\quotes\\q.pdf")
        assert tracker.matched() == frozenset()

    def test_a_folder_name_with_a_trailing_blank_is_matched_as_listed(self) -> None:
        live = scope(["report?"])
        tracker = live.tracker()
        tracker.note_folder("report ", live.folder_role("report "))
        assert tracker.matched() == frozenset({"report?"})

    def test_a_share_matches_whatever_the_case(self) -> None:
        live = scope(["Customer/*/Quotes"], "in", FS)
        tracker = live.tracker()
        tracker.note_file("customer/ACME/quotes/q.pdf")
        assert tracker.matched() == frozenset({"Customer/*/Quotes"})


class TestPathRules:
    def test_rules_by_path_style(self) -> None:
        assert RULES_BY_PATH_STYLE == {"posix": OBJECT_STORE_PATHS, "windows": FILE_SHARE_PATHS}
        assert OBJECT_STORE_PATHS.normalise("A\\B/" + NFD_CAFE) == "A\\B/" + NFD_CAFE
        assert FILE_SHARE_PATHS.normalise("A\\B/" + NFD_CAFE) == "a/b/" + NFC_CAFE
        assert PathRules().is_identity and not FILE_SHARE_PATHS.is_identity

    def test_a_pattern_keeps_its_fixed_start(self) -> None:
        assert FolderPattern.parse("customer/acme/*/q", OBJECT_STORE_PATHS).listing_prefix == "customer/acme/"
        assert FolderPattern.parse("*/q", OBJECT_STORE_PATHS).listing_prefix == ""
        assert FolderPattern.parse("**", OBJECT_STORE_PATHS) is None


# --- validation ---------------------------------------------------------------

# (value, operator, path style, wildcards, codes for a new value)
VALIDATION_TABLE = [
    ("reports/2026", "in", "posix", False, []),
    ("../etc", "in", "posix", False, ["climbs_out"]),
    ("a\\..\\b", "in", "posix", False, ["climbs_out"]),
    ("a//b", "in", "posix", False, ["empty_segment"]),
    ("a//b", "in", "windows", True, ["empty_segment"]),
    ("a\\\\b", "in", "windows", False, ["empty_segment"]),
    ("a\\\\b", "in", "posix", False, []),
    ("/a/b/", "in", "posix", False, []),
    ("a/\\*", "in", "posix", True, []),
    ("/", "not_in", "posix", False, ["excludes_everything"]),
    ("", "not_in", "windows", False, ["excludes_everything"]),
    ("**", "not_in", "posix", False, []),
    ("**", "not_in", "posix", True, ["excludes_everything"]),
    ("**/**", "not_in", "windows", False, []),
    ("/**/", "not_in", "posix", True, ["excludes_everything"]),
    ("**/**", "not_in", "windows", True, ["excludes_everything"]),
    ("\\", "not_in", "windows", False, ["excludes_everything"]),
    ("\\", "not_in", "posix", False, []),
    ("/", "in", "posix", False, []),
    ("**", "in", "posix", True, []),
    ("archive/**", "not_in", "posix", True, []),
    ("a" * 1025, "in", "posix", False, ["too_long"]),
    ("a" * 1024, "in", "posix", False, []),
    ("\\\\server\\share\\x", "in", "windows", False, ["unc_path"]),
    ("//server/share", "in", "windows", False, []),
    ("//server/share", "in", "posix", False, []),
    ("//reports", "in", "windows", False, []),
    ("\\\\reports", "in", "windows", False, []),
    ("\\\\server\\share", "in", "windows", False, ["unc_path"]),
    (" \\\\server\\share\\", "in", "windows", False, ["unc_path"]),
    ("a**b", "in", "posix", True, ["double_star_in_name"]),
    ("a**b", "in", "windows", True, ["double_star_in_name"]),
    ("a**b", "in", "posix", False, []),
    ("***", "in", "posix", True, ["double_star_in_name"]),
    ("a/\\**", "in", "posix", True, []),
    ("reports/**/2026", "in", "posix", True, []),
    ("/".join(["**"] * 5), "in", "posix", True, ["too_many_double_stars"]),
    ("/".join(["**"] * 4) + "/x", "in", "posix", True, []),
    ("*a" * 17, "in", "posix", True, ["too_many_stars"]),
    ("*a" * 16, "in", "posix", True, []),
    ("*a" * 17, "in", "posix", False, []),
]


class TestFolderPathProblems:
    @pytest.mark.parametrize(("value", "op", "style", "wildcards", "codes"), VALIDATION_TABLE)
    def test_codes_for_a_new_value(self, value, op, style, wildcards, codes) -> None:
        assert folder_value_problems(value, exclude=op == "not_in", path_style=style, wildcards=wildcards) == codes

    @pytest.mark.parametrize(("value", "op", "style", "wildcards", "codes"), VALIDATION_TABLE)
    def test_a_save_refuses_a_new_value_with_the_message(self, value, op, style, wildcards, codes) -> None:
        sync_values = {"folder_paths": {"operator": op, "value": [value]}}
        problems = folder_path_problems(sync_values, path_style=style, wildcards=wildcards, new_rules=True)
        if "climbs_out" in codes:
            assert problems == [FOLDER_PATH_MESSAGES["climbs_out"].format(v=value)]
        else:
            assert problems == [FOLDER_PATH_MESSAGES[c].format(v=value) for c in codes]

    @pytest.mark.parametrize(("value", "op", "style", "wildcards", "codes"), VALIDATION_TABLE)
    def test_a_stored_value_is_only_held_to_climbing_out(self, value, op, style, wildcards, codes) -> None:
        sync_values = {"folder_paths": {"operator": op, "value": [value]}}
        stored = saved_folder_values(sync_values, sync_values)
        problems = folder_path_problems(sync_values, path_style=style, wildcards=wildcards, stored=stored, new_rules=True)
        assert bool(problems) == ("climbs_out" in codes)

    @pytest.mark.parametrize(("value", "op", "style", "wildcards", "codes"), VALIDATION_TABLE)
    def test_without_the_new_rules_only_climbing_out_counts(self, value, op, style, wildcards, codes) -> None:
        sync_values = {"folder_paths": {"operator": op, "value": [value]}}
        assert bool(folder_path_problems(sync_values)) == ("climbs_out" in codes)

    def test_saved_values_count_only_under_the_same_operator(self) -> None:
        saved = {"folder_paths": {"operator": "in", "value": ["/", "a//b"]}}
        assert saved_folder_values(saved, {"folder_paths": {"operator": "in", "value": []}}) == {"/", "a//b"}
        assert saved_folder_values(saved, {"folder_paths": {"operator": "not_in", "value": ["/"]}}) == frozenset()
        assert saved_folder_values({}, saved) == frozenset()
        assert saved_folder_values(None, saved) == frozenset()

    def test_a_missing_operator_is_include(self) -> None:
        assert saved_folder_values({"folder_paths": {"value": ["a//b"]}},
                                   {"folder_paths": {"operator": "in", "value": ["a//b"]}}) == {"a//b"}

    def test_only_the_new_value_is_refused(self) -> None:
        sync_values = {"folder_paths": {"operator": "in", "value": ["a//b", "c//d"]}}
        problems = folder_path_problems(sync_values, stored=frozenset({"a//b"}), new_rules=True)
        assert problems == [FOLDER_PATH_MESSAGES["empty_segment"].format(v="c//d")]

    def test_the_messages_name_the_value(self) -> None:
        for code, text in FOLDER_PATH_MESSAGES.items():
            assert "{v}" in text, code


class TestSchema:
    def test_file_share_fields_carry_the_windows_path_style(self) -> None:
        field = CommonFields.folder_paths_filter("share", path_style="windows").to_schema_dict()
        assert field["pathStyle"] == "windows"
        assert "folderWildcards" not in field

    def test_object_store_fields_are_unchanged(self) -> None:
        field = CommonFields.folder_paths_filter("bucket").to_schema_dict()
        assert "pathStyle" not in field
        assert "folderWildcards" not in field

    def test_wildcards_are_announced_only_when_set(self) -> None:
        field = CommonFields.folder_paths_filter("bucket", path_style="posix", wildcards=True).to_schema_dict()
        assert field["pathStyle"] == "posix"
        assert field["folderWildcards"] is True

    @pytest.mark.parametrize(("module", "cls", "style"), [
        ("app.connectors.sources.smb.connector", "SmbConnector", "windows"),
        ("app.connectors.sources.cifs.connector", "CifsConnector", "windows"),
        ("app.connectors.sources.azure_files.connector", "AzureFilesConnector", "windows"),
        ("app.connectors.sources.s3.connector", "S3Connector", None),
        ("app.connectors.sources.minio.connector", "MinIOConnector", None),
        ("app.connectors.sources.google_cloud_storage.connector", "GCSConnector", None),
        ("app.connectors.sources.azure_blob.connector", "AzureBlobConnector", None),
    ])
    def test_each_connector_declares_its_path_style_and_no_wildcards_yet(self, module, cls, style) -> None:
        import importlib

        connector = getattr(importlib.import_module(module), cls)
        fields = connector._connector_metadata["config"]["filters"]["sync"]["schema"]["fields"]
        field = next(f for f in fields if f["name"] == "folder_paths")
        assert field.get("pathStyle") == style
        assert "folderWildcards" not in field
