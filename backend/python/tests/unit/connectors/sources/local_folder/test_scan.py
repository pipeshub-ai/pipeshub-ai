"""Ignore rules, symlinks and traversal refusal for the Folder connector's filesystem layer."""

# ruff: noqa: ANN201, ANN202

import os
from unittest.mock import patch

import pytest

from app.connectors.sources.local_folder import scan


def write(root, rel, data="x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data) if isinstance(data, str) else p.write_bytes(data)
    return p


def names(root, **kw):
    return sorted(e.rel for e in scan.walk(root, **kw))


def test_default_ignores_skip_vcs_deps_and_binaries(tmp_path):
    write(tmp_path, "src/app.py")
    write(tmp_path, ".git/config")
    write(tmp_path, "node_modules/x/index.js")
    write(tmp_path, "venv/lib/a.py")
    write(tmp_path, "dist/out.js")
    write(tmp_path, "logo.png")
    assert names(tmp_path) == ["src/app.py"]


def test_gitignore_negation_anchor_and_nested(tmp_path):
    write(tmp_path, ".gitignore", "*.log\n!keep.log\n/secret.txt\ngen/\n")
    write(tmp_path, "a.log")
    write(tmp_path, "keep.log")
    write(tmp_path, "secret.txt")
    write(tmp_path, "sub/secret.txt")  # anchored pattern: only the root one is ignored
    write(tmp_path, "gen/x.py")
    write(tmp_path, "sub/.gitignore", "local.txt\n")
    write(tmp_path, "sub/local.txt")
    write(tmp_path, "other/local.txt")  # nested .gitignore does not reach siblings
    assert names(tmp_path) == [".gitignore", "keep.log", "other/local.txt", "sub/.gitignore", "sub/secret.txt"]


def test_gitignore_can_be_turned_off_and_user_patterns_win(tmp_path):
    write(tmp_path, ".gitignore", "a.txt\n")
    write(tmp_path, "a.txt")
    write(tmp_path, "b.txt")
    write(tmp_path, "docs/gen/c.md")
    assert "a.txt" in names(tmp_path, use_gitignore=False)
    assert "a.txt" not in names(tmp_path)
    assert names(tmp_path, user_patterns=["docs/gen/", "!a.txt", "b.*"]) == [".gitignore", "a.txt"]


def test_double_star_patterns(tmp_path):
    write(tmp_path, "a/b/c/x.snap")
    write(tmp_path, "a/y.py")
    assert names(tmp_path, user_patterns=["**/*.snap"]) == ["a/y.py"]
    assert names(tmp_path, user_patterns=["a/**/c/"]) == ["a/y.py"]


def test_size_cap(tmp_path):
    write(tmp_path, "small.txt", "x" * 10)
    write(tmp_path, "big.txt", "x" * 100)
    assert names(tmp_path, max_bytes=50) == ["small.txt"]


def test_symlinks_outside_root_and_to_dirs_are_skipped(tmp_path):
    root = tmp_path / "root"
    outside = write(tmp_path, "outside/secret.txt")
    write(root, "inside.txt")
    os.symlink(outside, root / "evil.txt")
    os.symlink(outside.parent, root / "evildir")
    os.symlink(root / "inside.txt", root / "alias.txt")
    os.symlink(root, root / "loop")
    assert names(root.resolve()) == ["alias.txt", "inside.txt"]


def test_fifo_and_dangling_links_are_skipped(tmp_path):
    write(tmp_path, "ok.txt")
    os.mkfifo(tmp_path / "pipe")
    os.symlink(tmp_path / "nope", tmp_path / "dangling")
    assert names(tmp_path) == ["ok.txt"]


@pytest.mark.parametrize("rel", ["../x", "a/../../x", "/etc/passwd", "a/./b", "..", "x\x00y"])
def test_safe_open_refuses_traversal(tmp_path, rel):
    root = (tmp_path / "root").resolve()
    write(root, "a/b")
    with pytest.raises(scan.PathRefused):
        scan.safe_open(root, rel)


def test_safe_open_refuses_escape_via_symlink_devices_and_fifos(tmp_path):
    root = (tmp_path / "root").resolve()
    write(root, "ok.txt", "hello")
    secret = write(tmp_path, "secret.txt")
    os.symlink(secret, root / "leak.txt")
    os.symlink("/dev/zero", root / "zero")
    os.mkfifo(root / "pipe")
    for rel in ["leak.txt", "zero", "pipe", "missing.txt"]:
        with pytest.raises(scan.PathRefused):
            scan.safe_open(root, rel)
    fd = scan.safe_open(root, "ok.txt")
    assert scan.sha256_fd(fd) == __import__("hashlib").sha256(b"hello").hexdigest()


def test_looks_binary(tmp_path):
    t = write(tmp_path, "t", "text")
    b = write(tmp_path, "b", b"ab\x00cd")
    for p, want in [(t, False), (b, True)]:
        fd = os.open(p, os.O_RDONLY)
        assert scan.looks_binary(fd) is want
        os.close(fd)


def test_safe_open_does_not_follow_a_directory_swapped_for_a_symlink_mid_open(tmp_path):
    root = (tmp_path / "ws").resolve()
    (root / "d").mkdir(parents=True)
    (root / "d" / "f.txt").write_text("public")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.txt").write_text("SECRET")
    real_open = os.open

    def racing_open(path, flags, *a, **kw):
        d = root / "d"
        if d.is_dir() and not d.is_symlink():
            (d / "f.txt").unlink()
            d.rmdir()
            os.symlink(outside, d)  # the attacker wins the race here
        return real_open(path, flags, *a, **kw)

    with patch.object(scan.os, "open", racing_open), pytest.raises(scan.PathRefused):
        scan.safe_open(root, "d/f.txt")


def test_hard_links_are_refused_and_not_walked(tmp_path):
    root = (tmp_path / "ws").resolve()
    root.mkdir()
    (tmp_path / "out.txt").write_text("OUTSIDE")
    os.link(tmp_path / "out.txt", root / "h.txt")
    (root / "ok.txt").write_text("fine")
    with pytest.raises(scan.PathRefused):
        scan.safe_open(root, "h.txt")
    assert [e.rel for e in scan.walk(root)] == ["ok.txt"]


def test_malformed_ignore_line_is_skipped_not_fatal(tmp_path):
    (tmp_path / ".gitignore").write_text("[z-a]\n*.log\n")
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.log").write_text("x")
    assert [e.rel for e in scan.walk(tmp_path.resolve())] == [".gitignore", "a.py"]


def test_a_folder_that_cannot_be_read_fails_the_walk_instead_of_vanishing_from_it(tmp_path):
    root = (tmp_path / "root").resolve()
    write(root, "ok/a.py")
    write(root, "locked/b.py")
    real = os.scandir

    def scandir(path):
        if str(path).endswith("locked"):
            raise PermissionError(13, "denied")
        return real(path)

    with patch.object(os, "scandir", scandir), pytest.raises(scan.WalkFailed):
        list(scan.walk(root))


def test_a_folder_that_vanishes_during_the_walk_is_just_gone(tmp_path):
    root = (tmp_path / "root").resolve()
    write(root, "ok/a.py")
    write(root, "gone/b.py")
    real = os.scandir

    def scandir(path):
        if str(path).endswith("gone"):
            raise FileNotFoundError(2, "no such file")
        return real(path)

    with patch.object(os, "scandir", scandir):
        assert [e.rel for e in scan.walk(root)] == ["ok/a.py"]


def test_a_pattern_that_repeats_stars_does_not_stall_the_walk(tmp_path):
    import time

    hostile = "*a" * 40 + "b"
    rules = scan.parse_ignore([hostile, "**/" + "*a" * 40 + "b", "x/" + "*a" * 40 + "b"])
    started = time.monotonic()
    for name in ("a" * 200, "dir/" + "a" * 200, "x/" + "a" * 200):
        assert not scan.IgnoreStack([("", rules)]).ignored(name, False)
    assert time.monotonic() - started < 1.0  # the expression it was before takes minutes
    assert scan.IgnoreStack([("", rules)]).ignored("a" * 40 + "b", False)  # and it still matches what it should


def test_the_matcher_agrees_with_the_expression_it_replaces_on_every_pattern_shape():
    import itertools
    import random
    import re

    patterns = ["*.py", "a*b", "?x", "[abc]z", "[!abc]z", "**/t", "t/**", "a/**/b", "*/x", "\\*lit", "a[", "[]x", "*", "**", "x/*/y", "*a*a*b", "**/*.min.js", "d?r/*"]
    names = ["a.py", "ab", "axb", "bx", "az", "dz", "t", "a/t", "a/b/t", "t/x/y", "a/b", "a/x/b", "a/y/b", "q/x", "x/q/y", "*lit", "a[", "[]x", "aab", "aaaab", "d/r/z", "dir/z", "m/a.min.js", "n.min.js", ""]
    random.Random(7).shuffle(names)
    for pat, name, anchored in itertools.product(patterns, names, (True, False)):
        line = ("/" + pat) if anchored and "/" not in pat else pat
        rules = scan.parse_ignore([line])
        if not rules:
            continue
        body = scan._glob_to_regex(line.rstrip("/").lstrip("/"))
        rx = f"^{body}$" if "/" in line.rstrip("/") else f"^(?:.*/)?{body}$"
        assert rules[0].regex.match(name) == bool(re.compile(rx).match(name)), (line, name)
