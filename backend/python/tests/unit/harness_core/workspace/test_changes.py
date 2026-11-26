"""Pure change-set, diff and merge planning rules."""

from __future__ import annotations

import pytest

from harness_core.testing.contracts.workspace import file
from harness_core.workspace import (
    EXECUTABLE_MODE,
    FILE_MODE,
    SYMLINK_MODE,
    ChangeSet,
    InvalidChangeSet,
    ObjectScope,
    WorkspaceFile,
    diff_files,
    plan_merge,
    repository_of,
    workspace_manifest_content_hash,
)


@pytest.mark.parametrize(
    ("path", "repository"),
    [
        (".git/HEAD", ".git"),
        ("src/.git/refs/heads/main", "src/.git"),
        (".git", ".git"),
        ("src/.gitignore", None),
        ("a/b.txt", None),
    ],
)
def test_repository_of(path: str, repository: str | None) -> None:
    assert repository_of(path) == repository


def test_merge_takes_one_sided_changes_and_deletes() -> None:
    base = {"a": file("a", "1"), "b": file("b", "1")}
    ours = {"a": file("a", "2"), "b": file("b", "1")}
    theirs = {"a": file("a", "1"), "c": file("c", "1")}
    plan = plan_merge(base, ours, theirs)
    assert plan.clean
    assert set(plan.changes.upserts) == {"c"}
    assert plan.changes.deletes == frozenset({"b"})


def test_delete_against_edit_is_a_conflict() -> None:
    base = {"a": file("a", "1")}
    plan = plan_merge(base, {"a": file("a", "2")}, {})
    assert [(c.path, c.theirs) for c in plan.conflicts] == [("a", None)]


def test_repositories_are_reported_per_git_directory() -> None:
    base = {"x/.git/HEAD": file("x/.git/HEAD", "h"), "y/.git/HEAD": file("y/.git/HEAD", "h")}
    ours = {**base, "x/.git/ORIG_HEAD": file("x/.git/ORIG_HEAD", "1")}
    theirs = {**base, "x/.git/FETCH_HEAD": file("x/.git/FETCH_HEAD", "2"), "y/.git/HEAD": file("y/.git/HEAD", "z")}
    plan = plan_merge(base, ours, theirs)
    assert plan.repository_conflicts == ("x/.git",)
    assert set(plan.changes.upserts) == {"y/.git/HEAD"}


def test_change_set_refuses_non_canonical_input() -> None:
    with pytest.raises(InvalidChangeSet):
        ChangeSet({"a/../b": file("b", "1")})
    with pytest.raises(InvalidChangeSet):
        ChangeSet(deletes=frozenset({"café"}))


def test_scope_ids_are_validated() -> None:
    with pytest.raises(ValueError, match="tenant"):
        ObjectScope("../other")
    with pytest.raises(ValueError, match="partition"):
        ObjectScope("t", "ws/../x")
    assert ObjectScope("org-1", "ws:s1:child").partition == "ws:s1:child"


@pytest.mark.parametrize(
    ("mode", "valid"),
    [
        (0o100644, True),
        (0o100755, True),
        (0o100600, True),
        (0o100000, True),
        (0o120777, True),
        (0o104755, False),
        (0o102755, False),
        (0o101755, False),
        (0o040755, False),
        (0o120755, False),
        (0o644, False),
        (-1, False),
    ],
)
def test_md1_file_modes(mode: int, valid: bool) -> None:
    entry = file("a", "1")
    if valid:
        assert WorkspaceFile(entry.path, entry.content, entry.mime_type, mode).mode == mode
    else:
        with pytest.raises(ValueError, match="mode"):
            _ = WorkspaceFile(entry.path, entry.content, entry.mime_type, mode)


def test_md2_the_default_mode_leaves_the_hash_unchanged() -> None:
    plain = file("a", "1")
    explicit = WorkspaceFile(plain.path, plain.content, plain.mime_type, FILE_MODE)
    executable = WorkspaceFile(plain.path, plain.content, plain.mime_type, EXECUTABLE_MODE)
    assert workspace_manifest_content_hash({"a": plain}) == workspace_manifest_content_hash({"a": explicit})
    assert workspace_manifest_content_hash({"a": plain}) != workspace_manifest_content_hash({"a": executable})


def test_md3_a_mode_only_change_is_a_modification_and_merges() -> None:
    base = {"run.sh": file("run.sh", "1")}
    executable = WorkspaceFile("run.sh", base["run.sh"].content, base["run.sh"].mime_type, EXECUTABLE_MODE)
    diff = diff_files(base, {"run.sh": executable})
    assert set(diff.modified) == {"run.sh"}
    plan = plan_merge(base, base, {"run.sh": executable})
    assert plan.clean
    assert plan.changes.upserts["run.sh"].mode == EXECUTABLE_MODE
    link = WorkspaceFile("run.sh", base["run.sh"].content, base["run.sh"].mime_type, SYMLINK_MODE)
    assert link.is_symlink
    assert not plan_merge(base, {"run.sh": link}, {"run.sh": executable}).clean
