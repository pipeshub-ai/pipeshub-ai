"""Relative links in docs/ point at files that exist.

A moved or deleted document leaves its links behind; this fails on the first one, so the reader never follows a
link to nothing. Links to other sites, anchors within a page and examples without a file extension are not checked.

Standard library only.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# One level of parentheses inside a target, as in ``frontend/app/(main)/…``.
LINK = re.compile(r"\]\(((?:[^()\s]|\([^()\s]*\))+)\)")
CODE_SPAN = re.compile(r"`[^`]*`")
EXTERNAL = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
# A path the harness docs name in prose for a record that was moved out: impl/, notes/, history/ or research/.
RECORD = re.compile(r"(?<![\w/.-])((?:docs/agent-harness-v2/)?(?:impl|notes|history|research)/[A-Za-z0-9_.-]+\.md)")
HARNESS_DOCS = ("docs/agent-harness-v2/", "docs/test-plan/")


def markdown_files() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "docs"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return [path for path in listed if path.endswith(".md") and (ROOT / path).is_file()]


def dangling(path: str) -> list[str]:
    found = []
    folder = (ROOT / path).parent
    in_fence = False
    for number, line in enumerate((ROOT / path).read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if in_fence:
            continue
        for match in LINK.finditer(CODE_SPAN.sub("", line)):
            target = match.group(1).split("#", 1)[0]
            if not target or EXTERNAL.match(target) or target.startswith(("/", "<")):
                continue
            if "." not in Path(target).name and not target.endswith("/"):
                continue
            if not (folder / target).exists():
                found.append(f"{path}:{number}: {target}")
        if path.startswith(HARNESS_DOCS):
            for match in RECORD.finditer(line):
                target = match.group(1)
                if not target.startswith("docs/"):
                    target = "docs/agent-harness-v2/" + target
                if not (ROOT / target).exists():
                    found.append(f"{path}:{number}: {match.group(1)}")
    return found


class DocLinks(unittest.TestCase):
    def test_every_relative_link_in_docs_reaches_a_file(self) -> None:
        found = [entry for path in markdown_files() for entry in dangling(path)]
        self.assertEqual(found, [], "links to files that do not exist:\n" + "\n".join(found))

    def test_a_link_to_a_missing_file_is_found(self) -> None:
        page = ROOT / "docs" / "agent-harness-v2" / "README.md"
        self.assertTrue(page.is_file())
        self.assertTrue(LINK.search("[x](no-such-file.md)"))
        self.assertEqual(LINK.search("[a](frontend/app/(main)/page.tsx)").group(1), "frontend/app/(main)/page.tsx")
        self.assertTrue(RECORD.search("see impl/P9-production-readiness.md §8"))


if __name__ == "__main__":
    unittest.main()
