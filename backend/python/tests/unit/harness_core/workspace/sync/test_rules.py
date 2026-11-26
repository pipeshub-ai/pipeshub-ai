"""ST-1..ST-5: the pure rules of the sync, table by table."""

from __future__ import annotations

import pytest

from harness_core.workspace.sync import (
    ENGINE_CONFIG,
    SHADOWED,
    SYMLINK_ESCAPES,
    UNSTORABLE_PATH,
    UnsafeArchiveMember,
    is_engine_config,
    link_escapes,
    plan_tree,
)
from harness_core.workspace.sync.rules import engine_config_root, member_path


@pytest.mark.parametrize(
    "path",
    [
        ".opencode/plugin/x.ts",
        "a/.opencode/x",
        ".opencode",
        ".claude/settings.json",
        ".claude/settings.local.json",
        ".claude/hooks/h.sh",
        ".claude/commands/c.md",
        ".claude/agents/a.md",
        ".codex/config.toml",
        "repo/.codex/config.toml",
        "opencode.json",
        "opencode.jsonc",
        "sub/opencode.json",
        ".mcp.json",
        "pkg/.mcp.json",
    ],
)
def test_st1_engine_configuration_is_recognised_at_any_depth(path: str) -> None:
    assert is_engine_config(path)
    assert plan_tree([path], {}).hidden == {path: ENGINE_CONFIG}


@pytest.mark.parametrize(
    "path",
    [
        ".opencode-notes.md",
        "claude/x",
        "docs/.claude.md",
        "AGENTS.md",
        "CLAUDE.md",
        ".github/workflows/ci.yml",
        "codex/x",
        "my.opencode.json",
        "opencode.json.bak",
        "mcp.json",
    ],
)
def test_st2_lookalikes_are_kept(path: str) -> None:
    assert not is_engine_config(path)
    assert plan_tree([path], {}).visible == frozenset({path})


@pytest.mark.parametrize(
    ("path", "root"),
    [
        (".opencode/plugin/x.ts", ".opencode"),
        ("a/.claude/hooks/h.sh", "a/.claude"),
        ("pkg/.mcp.json", "pkg/.mcp.json"),
        ("x/.codex", "x/.codex"),
    ],
)
def test_engine_config_is_removed_from_its_outermost_folder(path: str, root: str) -> None:
    assert engine_config_root(path) == root


@pytest.mark.parametrize(
    ("path", "target", "escapes"),
    [
        ("link", "/etc/passwd", True),
        ("link", "../x", True),
        ("a/link", "../../x", True),
        ("a/link", "b/../../../x", True),
        ("link", "", True),
        ("link", "a\x00b", True),
        ("link", "x" * 4097, True),
        ("link", "target.txt", False),
        ("a/link", "../README.md", False),
        ("a/b/link", "../../c/./d", False),
        ("a/link", "./b/", False),
    ],
)
def test_st3_link_targets_resolve_inside_the_root_or_escape(path: str, target: str, escapes: bool) -> None:
    assert link_escapes(path, target) is escapes


def test_st3_a_link_through_another_link_counts_as_escaping() -> None:
    # The kernel resolves `d` first: `d/..` is the parent of wherever `d` points.
    assert link_escapes("x", "d/..", ["d", "x"])
    assert not link_escapes("x", "d", ["d", "x"])


def test_st3_plan_hides_what_would_be_written_through_a_file_or_link() -> None:
    plan = plan_tree(
        ["a", "a/b", "a/b/c", "link", "link/inside", "escape", "escape/under", "ok/file", ".claude/x"],
        {"link": "ok", "escape": "/etc"},
    )
    assert plan.visible == frozenset({"a", "link", "escape/under", "ok/file"})
    assert plan.hidden == {
        "a/b": SHADOWED,
        "a/b/c": SHADOWED,
        "link/inside": SHADOWED,
        "escape": SYMLINK_ESCAPES,
        ".claude/x": ENGINE_CONFIG,
    }


@pytest.mark.parametrize(
    "name",
    ["../x", "/etc/passwd", "ws/../x", "ws/./x", "ws//x", "other/x", "wsx/y", "ws/a\x00b", "ws/a/.."],
)
def test_st4_unsafe_member_names_are_refused(name: str) -> None:
    with pytest.raises(UnsafeArchiveMember):
        member_path(name, "ws")


@pytest.mark.parametrize(
    "name",
    ["ws/" + "x" * 1025, "ws/" + "/".join(["d"] * 33), "ws/a\\b", "ws/café", "ws/bad\udcff"],
)
def test_st5_names_the_store_cannot_hold_are_skipped(name: str) -> None:
    place = member_path(name, "ws")
    assert (place.path, place.skip) == (None, UNSTORABLE_PATH)
    assert place.raw == name[3:]


def test_member_names_map_onto_canonical_paths() -> None:
    assert member_path("ws", "ws").path is None
    assert member_path("ws/", "ws").skip is None
    assert member_path("ws/a/b.txt", "ws").path == "a/b.txt"
    assert member_path("ws/dir/", "ws").path == "dir"
