"""Tag a path with its kind, which drives selection."""

from __future__ import annotations

import re
from collections.abc import Iterable

from qa import globs

TEST_NAME = re.compile(
    r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|integration_test_[^/]*\.py|"
    r"[^/]*\.(test|spec|itest)\.(ts|tsx|js|mjs))$"
)
DOC = re.compile(r"(\.md|\.mdx|\.rst|\.txt)$|^docs/|^changelog/")
INFRA = re.compile(r"^(Dockerfile[^/]*|deployment/|\.github/|install\.sh$)")
CONFIG = re.compile(
    r"(package(-lock)?\.json|pyproject\.toml|uv\.lock|requirements[^/]*\.txt|"
    r"pytest\.ini|tsconfig[^/]*\.json|\.mocharc[^/]*|vitest\.config[^/]*|"
    r"playwright\.config[^/]*|next\.config\.[mc]?js|\.coveragerc|\.c8rc\.json|env\.template)$"
)


def kind(path: str, test_globs: Iterable[str] = ()) -> str:
    if TEST_NAME.search(path) or globs.match_any(path, test_globs):
        return "test"
    if CONFIG.search(path):
        return "config"
    if INFRA.match(path):
        return "infra"
    if DOC.search(path):
        return "docs"
    return "src"
