"""CPD0-60: the contracts workflow runs both halves of C-5 and fails rather than skips the TypeScript one."""

from __future__ import annotations

import re
from typing import Any

import yaml

from tests.cohesion.support import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "contracts-codegen.yml"


def _workflow() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict[str, Any]]:
    return _workflow()["jobs"]["codegen"]["steps"]


def _run(name_part: str) -> dict[str, Any]:
    return next(step for step in _steps() if name_part in step.get("name", ""))


def test_both_node_trees_are_installed_before_the_generator_runs() -> None:
    names = [step.get("name", "") for step in _steps()]
    regenerate = names.index("Regenerate every target; any diff fails")
    assert names.index("Install Node dependencies") < regenerate
    assert names.index("Install frontend dependencies") < regenerate
    assert "generate.sh" in _run("Regenerate")["run"] and "git status --porcelain" in _run("Regenerate")["run"]


def test_the_typescript_half_of_c5_cannot_skip() -> None:
    step = _run("Python contract suites")
    assert step["env"]["PXH_REQUIRE_NODE"] == "1"
    assert "tests/unit/contracts_harness" in step["run"]


def test_layering_and_import_contracts_run() -> None:
    assert "lint-imports" in _run("Import-linter")["run"]
    assert "tests/cohesion/cp-layering.test.ts" in _run("Node contract suites")["run"]


def test_the_path_filter_covers_every_root_and_target() -> None:
    on = _workflow()[True]  # YAML 1.1 reads the `on` key as true
    paths = set(on["pull_request"]["paths"])
    for needed in (
        "contracts/**",
        "backend/python/app/control_plane/**",
        "backend/nodejs/apps/src/modules/control-plane/**",
        "frontend/lib/contracts/**",
        ".github/workflows/contracts-codegen.yml",
    ):
        assert needed in paths, needed


# QA7-10: a hand edit to any file a QA-7 guard reads, in any language, runs the job.
GUARDED = (
    "backend/python/app/harness/contracts/state.py",
    "backend/python/app/harness/worker/capabilities.py",
    "backend/python/app/harness/workspace/node_store.py",
    "backend/python/app/harness/assembly/skill_learning.py",
    "backend/python/app/harness/access/offering.py",
    "backend/python/app/agents/run_definition.py",
    "backend/python/app/control_plane/problems.py",
    "backend/python/harness_core/driver/capabilities.py",
    "backend/python/harness_core/engines/types.py",
    "backend/python/harness_core/journal/children.py",
    "backend/python/harness_core/memories/memory.py",
    "backend/python/harness_engines/opencode/launch.py",
    "backend/nodejs/apps/src/libs/repositories/agent-harness/execution/schema/execution.schema.ts",
    "backend/nodejs/apps/src/modules/agent-harness/domain/worker-compat.ts",
    "backend/nodejs/apps/src/modules/agent-harness/domain/engine-profiles.ts",
    "backend/nodejs/apps/src/modules/agent-harness/projection/activity.ts",
    "backend/nodejs/apps/src/modules/agent-harness/transport/schemas.ts",
    "backend/nodejs/apps/src/modules/control-plane/domain/problem.ts",
    "backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml",
    "frontend/app/(main)/agents/types.ts",
    "frontend/app/(main)/chat/durable-run-ui.ts",
    "frontend/app/(main)/chat/components/message-area/approval-card.tsx",
    "frontend/app/(main)/workspace/memories/api.ts",
    "frontend/lib/contracts/control-plane/problem.ts",
)


def _glob(pattern: str) -> re.Pattern[str]:
    parts = re.split(r"(\*\*|\*)", pattern)
    return re.compile("".join(".*" if p == "**" else "[^/]*" if p == "*" else re.escape(p) for p in parts) + r"\Z")


def test_qa7_10_the_path_filter_covers_every_guarded_file() -> None:
    globs = [_glob(p) for p in _workflow()[True]["pull_request"]["paths"]]
    for path in GUARDED:
        assert (REPO_ROOT / path).exists(), path
        assert any(g.match(path) for g in globs), f"{path}: a hand edit here would not run the contract guards"


def test_qa7_10_the_job_runs_the_qa7_guards_in_every_language() -> None:
    python = _run("Python contract suites")["run"]
    for suite in ("test_c33_vector_readers.py", "test_c34_one_definition.py", "test_c3_c7_contracts_and_versions.py"):
        assert suite in python, suite
    assert "tests/modules/agent-harness/contracts/*.test.ts" in _run("Node contract suites")["run"]
    assert "tests/modules/control-plane/**/*.test.ts" in _run("Node contract suites")["run"]
    assert "npx vitest run lib/contracts" in _run("Frontend contract constants")["run"]
