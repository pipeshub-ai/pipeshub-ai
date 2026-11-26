"""The v2 spec trackers stay true to the tree (C1.01, C18.01, C20.01, DP7-P0-4).

Every one-way door in ``docs/agent-harness-v2/one-way-doors.md`` has an ADR under
``docs/agent-harness-v2/adr/`` with the standard sections and a Status, and its
``**Status.**`` line links that ADR. What the ADRs and the scenario/gap status
tables cite must exist: relative links, repository paths,
and the Python tests they name as passing.
"""

from __future__ import annotations

import re
from functools import cache
from typing import TYPE_CHECKING, cast

import pytest

from tests.cohesion.support import PYTHON_ROOT, REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path

V2 = REPO_ROOT / "docs" / "agent-harness-v2"
OWD_DOC = V2 / "one-way-doors.md"
ADR_DIR = V2 / "adr"
STATUS_TABLES = (V2 / "test-scenarios.md", V2 / "gaps-and-additions.md")

SECTIONS = (
    "Context",
    "Decision",
    "As built",
    "Deviations",
    "Owner amendments",
    "Status",
)
STATES = ("Accepted as amended", "Accepted", "Proposed", "Superseded")
ADR_NAME = re.compile(r"^ADR-OWD-(\d{2})-[a-z0-9-]+\.md$")
# Design ADRs that are not one-way doors (ADR-RUN-01, ADR-OPS-01): the same sections and Status, no OWD entry.
DESIGN_ADR_NAME = re.compile(r"^ADR-(?!OWD-)[A-Z]{2,8}-(\d{2})-[a-z0-9-]+\.md$")
OWD_HEADING = re.compile(r"^## OWD-(\d+) — ", re.M)
LINK = re.compile(r"\]\(([^)\s]+)\)")
REPO_PATH = re.compile(r"`((?:backend|contracts|deployment|docs)/[^`\s]+?)`")


def _owd_sections() -> dict[int, str]:
    text = OWD_DOC.read_text(encoding="utf-8")
    heads = list(OWD_HEADING.finditer(text))
    sections: dict[int, str] = {}
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[head.end() : end]
        nxt = re.search(r"^## ", body, re.M)
        sections[int(head.group(1))] = body[: nxt.start()] if nxt else body
    return sections


@cache
def _adrs() -> dict[int, Path]:
    found: dict[int, Path] = {}
    for path in sorted(ADR_DIR.glob("*.md")):
        if DESIGN_ADR_NAME.match(path.name):
            continue
        match = ADR_NAME.match(path.name)
        assert match, f"{path.name}: ADR files are named ADR-OWD-NN-<slug>.md or ADR-<FAMILY>-NN-<slug>.md"
        number = int(match.group(1))
        assert number not in found, (
            f"two ADRs for OWD-{number}: {found[number].name}, {path.name}"
        )
        found[number] = path
    return found


def _sections(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"^## (.+)$", text, flags=re.M)
    return [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts), 2)]


def _relative_links(path: Path) -> list[str]:
    links: list[str] = []
    for target in cast("list[str]", LINK.findall(path.read_text(encoding="utf-8"))):
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            continue
        links.append(target.split("#", 1)[0])
    return links


def _module_file(module: str) -> Path | None:
    base = PYTHON_ROOT.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _defines(path: Path, name: str, depth: int = 2) -> bool:
    """``name`` is defined in ``path`` or in a module it imports from: contract suites are inherited."""
    source = path.read_text(encoding="utf-8")
    # A trailing ``*`` cites a family of tests by prefix: at least one must be defined.
    pattern = re.escape(name[:-1]) + r"\w*" if name.endswith("*") else re.escape(name)
    if re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{pattern}\b", source, re.M):
        return True
    if depth == 0:
        return False
    for module in re.findall(
        r"^from\s+((?:tests|harness_core|harness_engines|app)[\w.]*)\s+import",
        source,
        re.M,
    ):
        found = _module_file(module)
        if found is not None and _defines(found, name, depth - 1):
            return True
    return False


def _python_test_exists(ref: str) -> bool:
    file_part, _, name = ref.partition("::")
    return _defines(REPO_ROOT / file_part, name)


def test_every_owd_has_exactly_one_adr_and_no_adr_is_orphaned() -> None:
    owds = set(_owd_sections())
    assert owds == set(range(1, 20)), f"one-way-doors.md lists OWD-{sorted(owds)}"
    assert set(_adrs()) == owds


@pytest.mark.parametrize("number", range(1, 20), ids=lambda n: f"OWD-{n}")
def test_the_owd_status_line_links_its_adr(number: int) -> None:
    status = [
        line
        for line in _owd_sections()[number].splitlines()
        if line.startswith("**Status.**")
    ]
    assert len(status) == 1, f"OWD-{number} needs exactly one **Status.** line"
    adr = _adrs()[number]
    assert f"](adr/{adr.name})" in status[0], (
        f"OWD-{number}'s Status line must link adr/{adr.name}"
    )


@pytest.mark.parametrize("number", range(1, 20), ids=lambda n: f"OWD-{n}")
def test_the_adr_has_every_section_in_order_and_a_stated_status(number: int) -> None:
    sections = _sections(_adrs()[number].read_text(encoding="utf-8"))
    assert tuple(title for title, _ in sections) == SECTIONS
    status = dict(sections)["Status"].strip()
    assert status.startswith(tuple(f"**{state}**" for state in STATES)), status[:80]
    owd_text = _owd_sections()[number]
    amended = "**Amendment" in owd_text or "**Exception" in owd_text
    amendments = dict(sections)["Owner amendments"].strip()
    if amended:
        assert not amendments.startswith("None"), (
            f"OWD-{number} is amended in one-way-doors.md; its ADR must say how"
        )


def _design_adrs() -> list[Path]:
    return [p for p in sorted(ADR_DIR.glob("*.md")) if DESIGN_ADR_NAME.match(p.name)]


@pytest.mark.parametrize("adr", _design_adrs(), ids=lambda p: p.name)
def test_a_design_adr_has_every_section_in_order_and_a_stated_status(adr: Path) -> None:
    sections = _sections(adr.read_text(encoding="utf-8"))
    assert tuple(title for title, _ in sections) == SECTIONS, adr.name
    status = dict(sections)["Status"].strip()
    assert status.startswith(tuple(f"**{state}**" for state in STATES)), status[:80]


@pytest.mark.parametrize(
    "doc", [OWD_DOC, *sorted(ADR_DIR.glob("*.md"))], ids=lambda p: p.name
)
def test_relative_links_resolve(doc: Path) -> None:
    missing = [t for t in _relative_links(doc) if not (doc.parent / t).exists()]
    assert not missing, f"{doc.name}: {missing}"


@pytest.mark.parametrize("adr", sorted(ADR_DIR.glob("*.md")), ids=lambda p: p.name)
def test_cited_repository_paths_and_python_tests_exist(adr: Path) -> None:
    text = adr.read_text(encoding="utf-8")
    for ref in REPO_PATH.findall(text):
        path = ref.split("::", 1)[0]
        assert (REPO_ROOT / path).exists(), (
            f"{adr.name} cites {path}, which does not exist"
        )
        if "::" in ref and path.endswith(".py"):
            assert _python_test_exists(ref), (
                f"{adr.name} cites {ref}, which is not defined"
            )


@pytest.mark.parametrize("doc", STATUS_TABLES, ids=lambda p: p.name)
def test_status_table_rows_cite_real_tests(doc: Path) -> None:
    table = doc.read_text(encoding="utf-8").split("\n## Status", 1)[1]
    rows = re.findall(
        r"^\| ([A-Z][A-Z0-9-]*-\d+) \| (\S+) \|\s*(.*?)\s*\|$", table, re.M
    )
    assert rows, f"{doc.name} has no Status table rows"
    for row_id, mark, evidence in rows:
        assert mark in {"☐", "◐", "✅", "⏸"}, (
            f"{doc.name} {row_id}: unknown mark {mark}"
        )
        if mark != "✅":
            continue
        refs = REPO_PATH.findall(evidence) or re.findall(
            r"`((?:frontend)/[^`]+)`", evidence
        )
        assert refs, f"{doc.name} {row_id} is ✅ without a test path"
        for ref in refs:
            path = ref.split("::", 1)[0]
            assert (REPO_ROOT / path).exists(), (
                f"{doc.name} {row_id}: {path} does not exist"
            )
            if "::" in ref and path.endswith(".py"):
                assert _python_test_exists(ref), (
                    f"{doc.name} {row_id}: {ref} is not defined"
                )
