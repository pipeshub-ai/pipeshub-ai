"""Unified diffs into the lines each file changed, on the old side.

The graph holds the old side (the indexed commit), so a change is located by the old-side lines it removed and the
old-side line each insertion follows. Context lines in a hunk are not changes.
"""
import pytest

from app.agents.actions.code_graph.diff_parse import FileDiff, parse_unified_diff

MODIFY = """\
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,7 +10,8 @@ def handler(event):
     a = 1
     b = 2
-    c = 3
+    c = 4
+    d = 5
     e = 6
     f = 7
     g = 8
@@ -40,3 +41,2 @@ def other():
     x = 1
-    y = 2
     z = 3
"""


def test_a_modified_file_reports_removed_lines_and_insertion_points_not_context() -> None:
    [f] = parse_unified_diff(MODIFY)
    assert (f.path, f.old_path, f.status, f.binary) == ("src/app.py", "src/app.py", "modified", False)
    assert f.removed == (12, 41)
    # both added lines replace line 12, so line 12 locates them: anchoring them on line 11 too would blame
    # whatever holds the line above
    assert f.inserted_after == ()
    assert f.added == (12, 13)
    assert [(h.old_start, h.old_len, h.new_start, h.new_len) for h in f.hunks] == [(10, 7, 10, 8), (40, 3, 41, 2)]


def test_a_pure_insertion_is_located_by_the_line_it_follows() -> None:
    [f] = parse_unified_diff("--- a/x.py\n+++ b/x.py\n@@ -3,2 +3,3 @@\n c\n+new\n d\n")
    assert f.removed == () and f.inserted_after == (3,) and f.added == (4,)


def test_lines_added_after_a_replacement_and_its_context_are_an_insertion() -> None:
    [f] = parse_unified_diff("--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,4 @@\n-a\n+A\n b\n+new\n c\n")
    assert f.removed == (1,) and f.inserted_after == (2,)


def test_a_plain_recursive_diff_strips_the_tree_names() -> None:
    """`diff -ruN old/ new/` names each file under its tree; the trees are not part of the path."""
    files = parse_unified_diff(
        "diff -ruN old/pkg/a.py new/pkg/a.py\n--- old/pkg/a.py\t2026-10-01\n+++ new/pkg/a.py\t2026-10-02\n"
        "@@ -1 +1 @@\n-a\n+b\n"
        "diff -ruN old/pkg/n.py new/pkg/n.py\n--- old/pkg/n.py\t1970-01-01\n+++ new/pkg/n.py\t2026-10-02\n"
        "@@ -0,0 +1 @@\n+x\n")
    assert [(f.path, f.old_path, f.status) for f in files] == [
        ("pkg/a.py", "pkg/a.py", "modified"), ("pkg/n.py", "pkg/n.py", "modified")]


def test_a_plain_diff_of_a_real_rename_keeps_both_paths() -> None:
    [f] = parse_unified_diff("--- src/old.py\n+++ lib/new.py\n@@ -1 +1 @@\n-a\n+b\n")
    assert (f.old_path, f.path, f.status) == ("src/old.py", "lib/new.py", "renamed")


def test_a_new_file_has_no_old_side() -> None:
    [f] = parse_unified_diff("""\
diff --git a/src/new.py b/src/new.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+def fresh():
+    return 1
""")
    assert (f.path, f.old_path, f.status) == ("src/new.py", None, "added")
    assert f.removed == () and f.added == (1, 2)


def test_a_deleted_file_removes_every_line() -> None:
    [f] = parse_unified_diff("""\
diff --git a/src/gone.py b/src/gone.py
deleted file mode 100644
index 3333333..0000000
--- a/src/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def gone():
-    return 1
""")
    assert (f.path, f.old_path, f.status) == ("src/gone.py", "src/gone.py", "deleted")
    assert f.removed == (1, 2)


def test_a_pure_rename_has_both_paths_and_no_hunks() -> None:
    [f] = parse_unified_diff("""\
diff --git a/src/old_name.py b/src/new_name.py
similarity index 100%
rename from src/old_name.py
rename to src/new_name.py
""")
    assert (f.path, f.old_path, f.status, f.hunks) == ("src/new_name.py", "src/old_name.py", "renamed", ())


def test_a_rename_with_edits_keeps_the_old_path_for_the_graph() -> None:
    [f] = parse_unified_diff("""\
diff --git a/a/x.py b/b/x.py
similarity index 90%
rename from a/x.py
rename to b/x.py
index 1..2 100644
--- a/a/x.py
+++ b/b/x.py
@@ -1 +1 @@
-old
+new
""")
    assert (f.path, f.old_path, f.status, f.removed) == ("b/x.py", "a/x.py", "renamed", (1,))


def test_a_no_newline_marker_is_not_a_line() -> None:
    [f] = parse_unified_diff("""\
diff --git a/t.txt b/t.txt
--- a/t.txt
+++ b/t.txt
@@ -1,2 +1,2 @@
 keep
-last
\\ No newline at end of file
+last
""")
    assert f.removed == (2,) and f.added == (2,) and f.inserted_after == ()


def test_a_binary_file_is_a_file_level_change() -> None:
    [f] = parse_unified_diff("""\
diff --git a/img/logo.png b/img/logo.png
index 4444444..5555555 100644
Binary files a/img/logo.png and b/img/logo.png differ
""")
    assert (f.path, f.binary, f.hunks) == ("img/logo.png", True, ())


def test_a_removed_line_that_looks_like_a_header_stays_in_its_hunk() -> None:
    """``--- x`` inside a hunk is a removed line ``-- x``; only the hunk's counts say where it ends."""
    files = parse_unified_diff("""\
diff --git a/notes.md b/notes.md
--- a/notes.md
+++ b/notes.md
@@ -1,2 +1,1 @@
--- not a header
 kept
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -3 +3 @@
-x
+y
""")
    assert [(f.path, f.removed) for f in files] == [("notes.md", (1,)), ("b.py", (3,))]


def test_a_plain_unified_diff_without_git_headers() -> None:
    [f] = parse_unified_diff("""\
--- src/app.py\t2026-10-01 10:00:00
+++ src/app.py\t2026-10-02 10:00:00
@@ -5,1 +5,1 @@
-a
+b
""")
    assert (f.path, f.old_path, f.removed) == ("src/app.py", "src/app.py", (5,))


def test_quoted_paths_lose_their_quotes() -> None:
    [f] = parse_unified_diff("""\
diff --git "a/dir with space/f.py" "b/dir with space/f.py"
--- "a/dir with space/f.py"
+++ "b/dir with space/f.py"
@@ -1 +1 @@
-a
+b
""")
    assert f.path == "dir with space/f.py"


@pytest.mark.parametrize("text", ["", "not a diff at all\n", "@@ -1 +1 @@\n-a\n+b\n"])
def test_text_that_names_no_file_gives_nothing(text) -> None:
    assert parse_unified_diff(text) == []


def test_a_truncated_hunk_keeps_what_it_read() -> None:
    [f] = parse_unified_diff("--- a/x.py\n+++ b/x.py\n@@ -1,5 +1,5 @@\n-a\n")
    assert f.removed == (1,)


def test_file_diff_is_immutable() -> None:
    [f] = parse_unified_diff(MODIFY)
    assert isinstance(f, FileDiff)
    with pytest.raises(AttributeError):
        f.path = "x"  # type: ignore[misc]


def test_a_form_feed_or_unicode_separator_inside_a_line_does_not_split_it() -> None:
    """Only newlines end a diff line: a form feed (GNU sources) or U+2028 is content, and the hunk keeps counting."""
    diff = ("--- a/x.c\n+++ b/x.c\n@@ -1,4 +1,4 @@\n a\x0c\n-b c\n+b\n d\n-e\n+f\n")
    (f,) = parse_unified_diff(diff)
    assert f.removed == (2, 4) and f.added == (2, 4)


def test_crlf_line_ends_read_like_lf() -> None:
    (f,) = parse_unified_diff("--- a/x.py\r\n+++ b/x.py\r\n@@ -1 +1 @@\r\n-a\r\n+b\r\n")
    assert (f.path, f.removed, f.added) == ("x.py", (1,), (1,))


def test_a_binary_line_with_and_in_a_path_splits_at_the_last_and() -> None:
    (f,) = parse_unified_diff("diff --git a/x and y.png b/x and y.png\nBinary files a/x and y.png and b/x and y.png differ\n")
    assert (f.path, f.binary) == ("x and y.png", True)


def test_a_huge_malformed_header_line_parses_in_linear_time() -> None:
    """A pathological header (a backtracking pattern takes minutes on it; the suite's timeout fails that)."""
    line = "Binary files " + "a and " * 200_000 + "x"
    assert parse_unified_diff(f"diff --git a/p b/p\n{line}\n") == parse_unified_diff("diff --git a/p b/p\n")
    assert isinstance(parse_unified_diff("diff --git " + '" ' * 300_000 + "\n"), list)
