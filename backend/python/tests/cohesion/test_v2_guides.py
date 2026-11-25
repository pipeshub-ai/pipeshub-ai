"""The harness guides cite only what exists (C18.03-C18.08, C6.41, C16.04, C20.48, C20.51).

The guides name files, tests, routes, switches and commands. A guide that links a
moved page or cites a deleted file misleads its reader, so every relative link must
resolve, every repository path in backticks must exist, and every test a guide names
must be defined. The facts a guide restates from code (switch defaults, the `ph`
commands and exit codes, the threats of design-plan §9, the dev-harness command) are
checked against that code, so the guide cannot drift from it.
"""

from __future__ import annotations

import json
import re
from functools import cache
from typing import TYPE_CHECKING

import pytest

from tests.cohesion.support import REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path

V2 = REPO_ROOT / "docs" / "agent-harness-v2"
GUIDES = (
    V2 / "guides" / "builder-guide.md",
    V2 / "guides" / "user-guide.md",
    V2 / "guides" / "vendor-dpa-checklist.md",
    V2 / "guides" / "admin-guide.md",
    V2 / "guides" / "pipeshub-contract-reference.md",
    V2 / "guides" / "adding-a-harness-adapter.md",
    V2 / "guides" / "security-whitepaper.md",
    V2 / "guides" / "threat-model.md",
    V2 / "guides" / "SUPPLY-CHAIN.md",
    V2 / "guides" / "operator-guide.md",
    V2 / "guides" / "runbooks.md",
    V2 / "guides" / "sizing.md",
    V2 / "guides" / "air-gapped-install.md",
    V2 / "SLO.md",
    V2 / "DR-DRILL.md",
)
# `legacy-2026-09.{json,md}` or `<label>-harness.json` name a family of files, not one path.
TEMPLATE = re.compile(r"[{}<>*]")
LINK = re.compile(r"\]\(([^)\s]+)\)")
REPO_PATH = re.compile(
    r"`((?:backend|contracts|deployment|docs|frontend|scripts|integration-tests)/[^`\s]+?)`"
)


def _relative_links(doc: Path) -> list[str]:
    targets = LINK.findall(doc.read_text(encoding="utf-8"))
    return [
        t.split("#", 1)[0]
        for t in targets
        if not re.match(r"^[a-z]+:", t) and not t.startswith("#")
    ]


@pytest.mark.parametrize("doc", GUIDES, ids=lambda p: p.name)
def test_relative_links_resolve(doc: Path) -> None:
    missing = [t for t in _relative_links(doc) if not (doc.parent / t).exists()]
    assert not missing, f"{doc.name}: {missing}"


@pytest.mark.parametrize("doc", GUIDES, ids=lambda p: p.name)
def test_cited_repository_paths_exist(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    missing = [
        ref
        for ref in REPO_PATH.findall(text)
        if not TEMPLATE.search(ref)
        and not (REPO_ROOT / ref.split("::", 1)[0].rstrip("/")).exists()
    ]
    assert not missing, f"{doc.name} cites paths that do not exist: {missing}"


def test_the_guides_are_in_the_docs_index() -> None:
    index = (V2 / "README.md").read_text(encoding="utf-8")
    for doc in GUIDES:
        name = doc.relative_to(V2).as_posix()
        assert f"]({name})" in index, f"README.md does not link {name}"


PYTHON = REPO_ROOT / "backend" / "python"
TEST_NAME = re.compile(r"`(?:[^`\s]*::)?(test_[a-z0-9_]+)`")
TEST_REF = re.compile(r"`((?:backend|integration-tests)/[^`\s]+\.py)::(test_[a-z0-9_]+)`")


@cache
def _python_test_names() -> frozenset[str]:
    names: set[str] = set()
    for root in (PYTHON / "tests", PYTHON / "harness_core" / "testing"):
        for path in root.rglob("*.py"):
            names.update(re.findall(r"def (test_[a-z0-9_]+)", path.read_text(encoding="utf-8", errors="replace")))
    return frozenset(names)


def _table(text: str, heading: str) -> list[list[str]]:
    """The rows of the first table after ``heading``, as stripped cells (header and rule dropped)."""
    section = text.split(heading, 1)[1]
    rows = []
    for line in section.splitlines():
        if line.startswith("|"):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
        elif rows:
            break
    return rows[2:]


@pytest.mark.parametrize("doc", GUIDES, ids=lambda p: p.name)
def test_named_tests_are_defined(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    pinned = [
        f"{path}::{name}" for path, name in TEST_REF.findall(text)
        if f"def {name}" not in (REPO_ROOT / path).read_text(encoding="utf-8")
    ]
    assert not pinned, f"{doc.name} names tests its file does not define: {pinned}"
    bare = {name for name in TEST_NAME.findall(text) if not name.endswith("_py")}
    missing = sorted(bare - _python_test_names())
    assert not missing, f"{doc.name} names tests nothing defines: {missing}"


def test_c18_03_the_admin_guides_switches_are_flags_with_these_defaults() -> None:
    flags = {f["name"]: f for f in json.loads((REPO_ROOT / "contracts/harness/flags.json").read_text())["flags"]}
    rows = _table((V2 / "guides" / "admin-guide.md").read_text(encoding="utf-8"), "### Switches")
    assert len(rows) >= 10
    for name, kind, default, _ in rows:
        flag = flags.get(name.strip("`"))
        assert flag is not None, f"{name} is not in flags.json"
        assert kind == flag["kind"], (name, kind, flag["kind"])
        assert default == str(flag["default"]).lower(), (name, default, flag["default"])


def test_c18_03_the_admin_guides_pages_exist() -> None:
    text = (V2 / "guides" / "admin-guide.md").read_text(encoding="utf-8")
    pages = set(re.findall(r"\(`/workspace/([a-z-]+)`\)", text))
    # AI settings, Labs, Schedules and Services: the org-wide settings pages this repository has. The guide names
    # each one; a guide that stops naming one should fail here rather than pass on any non-empty set.
    assert len(pages) >= 4, f"admin-guide.md names {sorted(pages)}"
    missing = [p for p in sorted(pages) if not (REPO_ROOT / "frontend/app/(main)/workspace" / p / "page.tsx").is_file()]
    assert not missing, f"admin-guide.md names pages the app does not have: {missing}"


def _ph() -> object:
    from harness_engines.pipeshub import ph

    return ph


def _parser_commands() -> set[str]:
    """Every command ph's parser accepts, as ``tools.call``-style names (what ``ph --help`` lists)."""
    import argparse

    root, _ = _ph()._parsers()  # type: ignore[attr-defined]
    names: set[str] = set()

    def walk(parser: argparse.ArgumentParser, prefix: str) -> None:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    full = f"{prefix}{name}"
                    before = len(names)
                    walk(sub, f"{full} ")
                    if len(names) == before:
                        names.add(full)

    walk(root, "")
    return names


def test_c18_06_the_reference_names_the_shipped_versions() -> None:
    ph = _ph()
    text = (V2 / "guides" / "pipeshub-contract-reference.md").read_text(encoding="utf-8")
    contract = (PYTHON / "harness_engines/pipeshub/VERSION").read_text().strip()
    current = {row[0]: row[1] for row in _table(text, "## Versions")}
    assert current["`/pipeshub` contract (`/pipeshub/VERSION`)"] == contract
    assert current["`ph`"] == ph.PH_VERSION  # type: ignore[attr-defined]
    history = _table(text, "### History")
    assert history[-1][1] == contract and history[-1][2] == ph.PH_VERSION  # type: ignore[attr-defined]


def test_c18_06_every_ph_command_has_a_row_and_every_row_a_command() -> None:
    text = (V2 / "guides" / "pipeshub-contract-reference.md").read_text(encoding="utf-8")
    rows = _table(text, "## Commands")
    documented = set()
    for command, json_name, _, _ in rows:
        words = command.strip("`").split()
        assert words[0] == "ph", command
        documented.add(" ".join(w for w in words[1:3] if re.fullmatch(r"[a-z]+", w)) if words[1] != "--version"
                       else "--version")
        assert json_name.strip("`"), command
    parsed = _parser_commands() | {"--version"}
    # One-word commands with arguments (``ph search <words>``) read as their first word.
    documented = {d if d in parsed else d.split()[0] for d in documented}
    assert documented == parsed, f"documented {sorted(documented ^ parsed)} differ from ph's parser"


def test_c18_06_the_exit_codes_match_ph() -> None:
    codes = _ph().ERROR_CODES  # type: ignore[attr-defined]
    expected = {str(exit_): name for exit_, name in codes.items()} | {"0": ""}
    for doc, heading in (
        (V2 / "guides" / "pipeshub-contract-reference.md", "## Exit codes"),
        (PYTHON / "harness_engines/pipeshub/AGENTS.md", "## Exit codes"),
    ):
        rows = _table(doc.read_text(encoding="utf-8"), heading)
        assert {row[0]: row[1].strip("`") for row in rows} == expected, doc.name


DESIGN_THREAT = re.compile(r"^\| ([^|]+?) \| [^|]+ \|$")


def test_c6_41_every_design_plan_threat_has_a_row_in_the_threat_model() -> None:
    plan = (V2 / "design-plan.md").read_text(encoding="utf-8").split("## 9. Security architecture summary", 1)[1]
    plan = plan.split("\n## ", 1)[0]
    threats = [m.group(1) for line in plan.splitlines() if (m := DESIGN_THREAT.match(line))]
    threats = [t for t in threats if t not in {"Threat", "---"} and not t.startswith("-")]
    assert len(threats) >= 10
    rows = _table((V2 / "guides" / "threat-model.md").read_text(encoding="utf-8"), "## Threats")
    modelled = {row[0] for row in rows}
    assert set(threats) <= modelled, f"threats with no row: {sorted(set(threats) - modelled)}"
    for row in rows:
        assert "`" in row[1] or "](" in row[1], f"{row[0]}: no proof cited"


def test_c6_41_the_threat_model_keeps_a_dated_review_record() -> None:
    record = _table((V2 / "guides" / "threat-model.md").read_text(encoding="utf-8"), "### Review record")
    assert record and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", row[0]) for row in record)


def test_c20_51_the_dev_harness_command_in_contributing_exists() -> None:
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    run = (REPO_ROOT / "deployment/e2e/run.sh").read_text(encoding="utf-8")
    for command in re.findall(r"bash deployment/e2e/run\.sh ([a-z-]+)", contributing):
        assert f"\n  {command})" in run, f"run.sh has no {command} command"
    assert "bash deployment/e2e/run.sh dev-up" in contributing
    assert "\n    dev-harness) printf 'COMPOSE_PROFILES=sandbox,graph-neo4j,dev-harness" in run


def test_c20_51_the_dev_overlay_runs_its_own_stores_in_the_profile_on_explicit_subnets() -> None:
    import yaml

    overlay = yaml.safe_load((REPO_ROOT / "deployment/e2e/docker-compose.dev-harness.yml").read_text())
    for store in ("mongodb", "redis"):
        assert overlay["services"][store]["profiles"] == ["dev-harness"], store
    # R15-DEV: the subnets are the stack's own (WP-71 ``assign_subnets``), as every lane stack's are.
    assert "networks" not in overlay, "the overlay's subnets are replaced by the stack's networks.yml"


def test_r15_dev_1_every_stack_dev_up_included_takes_free_subnets_and_says_so() -> None:
    """R15-DEV (WP-74 dev-up x WP-71 lane subnets): WP-71 gives every stack's networks a free /26 of 10.216/16,
    written to the run's networks.yml and passed after every overlay, so the dev overlay's fixed subnets (and the
    ``E2E_DEV_SUBNET_*`` overrides CONTRIBUTING.md offered) never took effect."""
    run = (REPO_ROOT / "deployment/e2e/run.sh").read_text(encoding="utf-8")
    compose = run[run.index("\ncompose() {"):]
    compose = compose[: compose.index("\n}\n")]
    assert compose.index('"$DEV_OVERLAY"') < compose.index('"$WORK/networks.yml"'), "networks.yml comes after the overlays"
    up = run[run.index("\nup_stack() {"):]
    assert up.index("assign_subnets") < up.index("compose up"), "the stack's subnets are chosen before it is created"
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "E2E_DEV_SUBNET" not in contributing
    assert "10.216.0.0/16" in contributing


# ---- R15-DOC: the admin guide describes what rounds 13 to 15 shipped (checked against the code it restates) ----


def _admin_guide() -> str:
    return (V2 / "guides" / "admin-guide.md").read_text(encoding="utf-8")


def test_r15_doc_1_the_release_run_settings_are_described() -> None:
    text = _admin_guide()
    sections = ["Runs at once", "Tool results", "Waiting for people", "Recovery", "Sandbox resources",
                "What conversations show", "Sandbox snapshots", "Memories"]
    missing = [title for title in sections if f"**{title}**" not in text]
    assert not missing, f"admin-guide.md does not name the release run settings {missing}"
    assert "No automatic expiry" in text


def test_r15_doc_2_the_engine_step_limits_are_the_releases_and_the_budget_scale() -> None:
    from harness_core.budget.run_budget import (
        MIN_BUDGET_STEPS,
        STEPS_PER_USD,
        SYNTHESIS_SHARE,
    )

    release = json.loads((REPO_ROOT / "contracts/harness/core/policy/release.json").read_text(encoding="utf-8"))
    steps = release["engineProfile"]["limits"]["maxSteps"]
    text = _admin_guide()
    assert f"{steps:,} steps" in text, f"the release's engine step ceiling is {steps}"
    assert f"{STEPS_PER_USD} steps a dollar" in text and f"at least {MIN_BUDGET_STEPS}" in text
    assert f"{round(SYNTHESIS_SHARE * 100)}%" in text


def test_r15_doc_3_budget_holds_and_the_input_limit_are_described_with_their_switches() -> None:
    text = _admin_guide()
    assert "There is no separate \"hold\" setting" not in text
    for name in ("MODEL_GATEWAY_CUT_HOLD_TTL_S", "HARNESS_INPUT_MAX_MB"):
        assert f"`{name}`" in text, name
    assert "estimated (stopped early)" in text
