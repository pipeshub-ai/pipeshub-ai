"""WP-26 GIT-FLOW (UI-4): the repository Node pins reaches the worker, on the engine run and on the code_task child.

``contracts/harness/vectors/repository-pin.json`` is what Node pins for a chat whose owner chose a repository (its
Node test, ``wp26-repository.test.ts`` RA-2, holds the file to Node's own output): the loop parent that may hand off,
and its code_task engine child. Here the worker reads both as it would at a claim.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.harness.assembly.context import RunContext
from app.harness.assembly.tool_environment import (
    _grants,
    _narrow_toolsets,
    parse_tool_id,
    with_repository_toolset,
)
from app.harness.engines.git_flow import run_checkout, run_repository
from app.harness.engines.progress import progress_for
from app.harness.engines.pull_request import GITHUB_CREATE_PULL_REQUEST, github_target
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.engines.events import EngineEventMeta, RepositoryReady
from harness_core.engines.git_checkout import session_branch
from harness_core.logs import HarnessLog
from tests.unit.harness.assembly import world as w

VECTOR = Path(__file__).resolve().parents[6] / "contracts" / "harness" / "vectors" / "repository-pin.json"
URL = "https://github.com/acme/widgets"
INSTANCE = "gh-1"


def _specs() -> dict[str, RunSpec]:
    raw: dict[str, Any] = json.loads(VECTOR.read_text())
    return {name: RunSpec.model_validate(spec) for name, spec in raw.items()}


def _pull_request_grants(spec: RunSpec) -> list[tuple[str, str, str]]:
    return [p for p in (parse_tool_id(g.tool_id) for g in spec.grants.tools) if p[0] == "github"]


class TestTheRepositoryPinReachesTheWorker:
    def test_rp_w1_the_code_task_child_runs_on_an_engine_with_the_parents_repository(self) -> None:
        specs = _specs()
        child = specs["child"]
        assert child.run_kind == "child" and child.executor is not None and child.executor.kind == "engine"
        repo = run_repository(child)
        assert repo is not None
        assert (repo.url, repo.base_ref, repo.toolset_instance) == (URL, "main", INSTANCE)
        checkout = run_checkout(repo, session_id="ses-1", context=None, owner="user-1", sealer=None)
        assert checkout.repository == "acme/widgets" and checkout.session_branch == session_branch("ses-1")

    def test_rp_w2_the_loop_parent_pins_it_for_its_child_and_both_hold_the_pull_request_grant(self) -> None:
        specs = _specs()
        parent = run_repository(specs["parent"])
        assert parent is not None and parent.url == URL
        assert specs["parent"].executor is not None and specs["parent"].executor.kind == "loop"
        for name in ("parent", "child"):
            # The GitHub toolset's create_pull_request on the repository's instance, which open_pull_request wraps.
            assert _pull_request_grants(specs[name]) == [("github", INSTANCE, "create_pull_request")], name
        assert GITHUB_CREATE_PULL_REQUEST.endswith("/github/create_pull_request")
        assert github_target(URL, session_branch("ses-1"), "main") is not None

    def test_rp_w3_the_setup_phase_line_is_the_chats_copied_row_with_names_only(self) -> None:
        shown = progress_for(RepositoryReady(EngineEventMeta("trn-1", 900_000), "acme/widgets", "pipeshub/session-ses-1"))
        assert shown is not None
        assert (shown.kind, shown.text, dict(shown.data)) == (
            "repository",
            "acme/widgets",
            {"action": "copied", "branch": "pipeshub/session-ses-1"},
        )


class TestThePullRequestGrantResolvesFromTheChatsRepository:
    """The grant Node joined resolves on the worker even where the agent's saved toolsets lack GitHub."""

    def _granted(self, spec: RunSpec) -> dict[tuple[str, str], frozenset[str]]:
        run = RunContext.from_claim(w.claim(spec))  # type: ignore[arg-type]
        assert run.repository_toolset == INSTANCE
        return _grants(run)[0]

    def test_rp_w4_an_agent_without_the_github_toolset_still_gets_the_pull_request_tool(self) -> None:
        granted = self._granted(_specs()["child"])
        jira = {"instanceId": "jira-1", "name": "jira", "tools": [{"name": "search_issues"}]}
        narrowed = _narrow_toolsets([jira], granted, HarnessLog.named("test"))
        assert all(t["instanceId"] != INSTANCE for t in narrowed)
        toolsets = with_repository_toolset(narrowed, granted, INSTANCE)
        github = [t for t in toolsets if t["instanceId"] == INSTANCE]
        assert len(github) == 1 and github[0]["name"] == "github"
        assert [t["name"] for t in github[0]["tools"]] == ["create_pull_request"]

    def test_rp_w5_an_agent_with_github_but_not_the_tool_gains_only_that_tool(self) -> None:
        granted = self._granted(_specs()["parent"])
        own = {"instanceId": INSTANCE, "name": "github", "tools": [{"name": "get_issue"}]}
        toolsets = with_repository_toolset([own], granted, INSTANCE)
        assert [t["name"] for t in toolsets[0]["tools"]] == ["get_issue", "create_pull_request"]
        assert with_repository_toolset(toolsets, granted, INSTANCE) == toolsets

    def test_rp_w6_no_repository_or_no_grant_adds_nothing(self) -> None:
        granted = self._granted(_specs()["child"])
        assert with_repository_toolset([], granted, None) == []
        assert with_repository_toolset([], granted, "gh-other") == []
        assert with_repository_toolset([], {}, INSTANCE) == []
