"""Which paths get records: built-in rules, .gitattributes and admin excludes (P-1, P-6, P-7)."""
import pytest

from app.connectors.core.registry.code_indexing_flags import (
    code_exclude_globs,
    code_indexing_filters_changed,
)
from app.connectors.core.registry.filters import FilterCollection, SyncFilterKey
from app.modules.parsers.code_parser.file_role import (
    FileRole,
    classify_file_role,
    should_index_code_file,
    should_index_code_folder,
)
from app.modules.parsers.code_parser.gitattributes import parse_linguist_exclusions
from app.modules.parsers.code_parser.path_policy import PathPolicy


@pytest.mark.parametrize("path", [
    "Tests/CartTests.swift", "src/UnitTests/Parser.cs", "pkg/testdata/sample.go",
    "app/tests.py", "lib/cart_test.dart", "src/ParserSpec.scala", "src/CartIT.java",
    "e2e/login.e2e-spec.ts", "tests/stub.pyi",
])
def test_more_test_layouts_are_tests(path: str) -> None:
    assert classify_file_role(path) is FileRole.TEST


@pytest.mark.parametrize(("path", "indexed"), [
    ("dist/app.js", False),
    ("bin/run.sh", False),
    ("packages/web/dist/bundle.js", False),
    ("packages/web/build/main.css", False),
    # A package named like an output dir, holding source, is source.
    ("com/acme/build/Builder.java", True),
    ("tools/build/make_release.py", True),
    ("src/main/java/com/acme/build/Step.java", True),
    # bin/ and env/ are output only at the top.
    ("scripts/bin/deploy.sh", True),
    ("config/env/settings.py", True),
])
def test_output_dirs_are_judged_by_depth_and_content(path: str, indexed: bool) -> None:
    assert should_index_code_file(path)[0] is indexed


def test_a_deeper_build_folder_is_walked_not_pruned() -> None:
    assert not should_index_code_folder("dist")
    assert should_index_code_folder("packages/web/build")
    assert not should_index_code_folder("packages/web/dist")
    assert not should_index_code_folder("node_modules/x")


def test_admin_globs_exclude_files_and_prune_folders() -> None:
    policy = PathPolicy(exclude_globs=("docs/", "**/fixtures/**", "*.snap", " "))
    assert not policy.keeps_file("docs/conf.py")
    assert not policy.keeps_file("src/a/fixtures/data.py")
    assert not policy.keeps_file("ui/__snapshots__/a.snap")
    assert policy.keeps_file("src/app.py")
    assert not policy.keeps_folder("docs")
    assert policy.keeps_folder("src")


def test_a_credential_file_stays_out_whatever_the_repo_says() -> None:
    policy = PathPolicy(linguist=parse_linguist_exclusions(".env -linguist-generated\n"))
    assert not policy.keeps_file(".env")


def test_gitattributes_can_force_a_path_back_in() -> None:
    rules = parse_linguist_exclusions(
        "dist/** linguist-generated\n"
        "dist/sdk.js -linguist-generated\n"
        "vendor/patched/** -linguist-vendored\n"
    )
    assert rules.forces_in("dist/sdk.js") and not rules.forces_in("dist/other.js")
    assert not rules.forces_in("src/app.py"), "no matching rule is not a force"
    policy = PathPolicy(linguist=rules)
    assert policy.keeps_file("dist/sdk.js")
    assert not policy.keeps_file("dist/other.js")
    assert policy.keeps_folder("dist"), "walked to reach the forced file"
    assert policy.keeps_folder("vendor/patched")
    assert not PathPolicy().keeps_folder("dist")


def test_exclude_globs_come_from_the_sync_filter() -> None:
    filters = FilterCollection.model_validate({"filters": [{
        "key": SyncFilterKey.EXCLUDE_PATHS.value, "type": "list", "operator": "in",
        "value": ["docs/", "  ", "*.snap"],
    }]})
    assert code_exclude_globs(filters) == ("docs/", "*.snap")
    assert code_exclude_globs(None) == ()


def test_a_test_files_toggle_is_a_code_filter_change() -> None:
    old = {"test_files": {"operator": "is", "value": False}}
    assert code_indexing_filters_changed(old, {"test_files": {"operator": "is", "value": True}})
    assert not code_indexing_filters_changed(old, dict(old))
    assert not code_indexing_filters_changed({}, {"documents": {"value": True}})
