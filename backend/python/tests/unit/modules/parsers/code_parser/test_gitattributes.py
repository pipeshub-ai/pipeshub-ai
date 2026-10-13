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
    ],
)
def test_linguist_rules(rules: str, path: str, excluded: bool) -> None:
    assert parse_linguist_exclusions(rules).excludes(path) is excluded


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
