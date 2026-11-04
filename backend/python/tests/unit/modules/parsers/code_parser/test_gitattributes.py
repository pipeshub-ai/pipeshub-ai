"""linguist-generated / linguist-vendored rules from .gitattributes (P-1)."""
from __future__ import annotations

import pytest

from app.modules.parsers.code_parser.file_role import should_index_code_folder
from app.modules.parsers.code_parser.gitattributes import parse_linguist_exclusions


@pytest.mark.parametrize(
    "rules, path, excluded",
    [
        # A slash-free pattern matches the basename at any depth.
        ("*.pb.ts linguist-generated", "src/api/user.pb.ts", True),
        ("*.pb.ts linguist-generated", "src/api/user.ts", False),
        # A pattern with a slash is anchored at the repo root.
        ("static/*.js linguist-vendored", "static/jquery.js", True),
        ("static/*.js linguist-vendored", "app/static/jquery.js", False),
        ("static/*.js linguist-vendored", "static/sub/jquery.js", False),
        ("/static/** linguist-vendored", "static/sub/jquery.js", True),
        ("**/fixtures/** linguist-generated", "a/b/fixtures/data.json", True),
        ("docs/api.md linguist-generated=true", "docs/api.md", True),
        ("docs/api.md linguist-generated=false", "docs/api.md", False),
        ("docs/api.md linguist-documentation", "docs/api.md", False),
        # A later line overrides an earlier one, as in git.
        ("vendor/** linguist-vendored\nvendor/ours.py -linguist-vendored", "vendor/ours.py", False),
        ("vendor/** linguist-vendored\nvendor/ours.py -linguist-vendored", "vendor/theirs.py", True),
        # A directory pattern matches no file in gitattributes.
        ("vendor/ linguist-vendored", "vendor/lib.py", False),
        ("# comment\n\n*.min.css linguist-generated", "a.min.css", True),
        ("*.ts text eol=lf", "a.ts", False),
        # `**` inside a name is one `*`, and a class never matches `/`, as in git.
        ("foo**.js linguist-generated", "foobar.js", True),
        ("foo**.js linguist-generated", "foo/bar.js", False),
        ("a[!x]b linguist-generated", "a/b", False),
        ("a[!x]b linguist-generated", "ayb", True),
        ("*a*b*.js linguist-generated", "src/xaybz.js", True),
    ],
)
def test_linguist_rules(rules: str, path: str, excluded: bool) -> None:
    assert parse_linguist_exclusions(rules).excludes(path) is excluded


def test_a_crafted_pattern_matches_in_linear_time() -> None:
    """Repository content writes these rules; `*a*a…*b` must not backtrack over every split.

    Timed as a ratio, not against a limit: doubling the text costs about twice as much when the
    match is linear, and thousands of times as much when it backtracks over every split."""
    import time

    rules = parse_linguist_exclusions("*a" * 8 + "*b linguist-generated")

    def best_of_three(size: int, reps: int) -> float:
        runs = []
        for _ in range(3):
            started = time.perf_counter()
            for _ in range(reps):
                assert rules.excludes("a" * size) is False
            runs.append(time.perf_counter() - started)
        return min(runs)

    # Repeat the call until the small input takes at least 5 ms, so the comparison is never against noise.
    one = best_of_three(14, 1)
    reps = max(1, int(0.005 / max(one, 1e-7)) + 1)
    small, large = best_of_three(14, reps), best_of_three(28, reps)
    assert large <= small * 40, (reps, small, large)  # linear is about 2-4x; backtracking is thousands of times


def test_no_linguist_rules_is_falsy() -> None:
    assert not parse_linguist_exclusions("*.sh text eol=lf\n")


@pytest.mark.parametrize(
    "folder, kept",
    [
        ("src", True),
        ("src/components", True),
        ("node_modules", False),
        ("packages/web/node_modules/react", False),
        ("dist", False),
        ("src/__generated__", False),
        ("src/builder", True),
    ],
)
def test_should_index_code_folder(folder: str, kept: bool) -> None:
    assert should_index_code_folder(folder) is kept
