"""GT-04 (C3.81): a run's pull request is opened by the control plane, after approval, with citations and a link
to the session.

The engine calls ``open_pull_request`` with a title, a summary and citation refs; it is approved exactly as the
GitHub toolset's ``create_pull_request`` is (main #4313: it runs unless a rule asks or blocks), and the worker runs it:
the repository, head (the session branch) and base are the control plane's, the refs become links, the session
link comes with the write identity's disclosure, and the run's GitHub toolset creates the pull request.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, override
from unittest.mock import patch

import pytest

from app import edition_config
from app.agents.agent_loop.tool_approvals import TOOLSET_TOOLS_BY_PATH, ApprovalTarget
from app.agents.constants.toolset_constants import get_toolset_tool_rules_path
from app.harness.engines.git_flow import (
    ToolsetGitCredentials,
    ref_links,
    run_checkout,
    run_repository,
    with_pull_request,
)
from app.harness.engines.pull_request import (
    OPEN_PULL_REQUEST,
    OPEN_PULL_REQUEST_PATH,
    OpenPullRequestTool,
    PullRequestTarget,
    cited,
    github_target,
    pull_request_body,
)
from app.harness.tool_rules import ToolRulesPolicy
from app.harness.write_identity.disclosure import WriteIdentitySettings
from app.utils.chat_helpers import CitationRefMapper
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.effects.policy import LayeredApprovalPolicy
from harness_core.engines.git_checkout import GitCheckout, session_branch
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.logs import HarnessLog
from harness_core.sandbox.egress.sealed import CredentialSealer
from harness_core.sandbox.types import TokenRoute
from harness_core.tools.base import Tag, Tool, ToolOutput, ToolParameter
from harness_core.tools.registry import ToolRegistry

LOG = HarnessLog.named("tests.gitflow.pr")
SESSION = "sess-42"
BRANCH = session_branch(SESSION)
URL = "https://github.com/acme/widgets.git"
TARGET = PullRequestTarget("acme", "widgets", BRANCH, "main")
APP = "https://pipeshub.example"
LINK = f"{APP}/chat?conversationId={SESSION}"


class CreatePullRequest(Tool):
    """The GitHub toolset's ``create_pull_request`` as the registry holds it, recording what it was asked."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    @override
    def name(self) -> str:
        return "github__create_pull_request"

    @property
    @override
    def short_description(self) -> str:
        return "Create a pull request"

    @property
    @override
    def description(self) -> str:
        return "Create a pull request"

    @property
    @override
    def path(self) -> str:
        return "/tools/github/create_pull_request"

    @property
    @override
    def tags(self) -> list[Tag]:
        return [Tag("type", "write"), Tag("effect", "write")]

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return []

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        self.calls.append(kwargs)
        return ToolOutput(success=True, data={"number": 7, "html_url": "https://github.com/acme/widgets/pull/7"})


def _settings() -> WriteIdentitySettings:
    return WriteIdentitySettings(session_id=SESSION, agent_name="Builder", app_url=APP)


@dataclass(frozen=True, slots=True)
class _Refusing(WriteIdentitySettings):
    """Settings an edition's write identity port could answer: it refuses the pull request it cannot send as chosen."""

    asked: list[tuple[str, str, str | None]] | None = None

    @override
    def refusal(self, action: str, tool_path: str, what: str | None = None) -> str | None:  # type: ignore[override]
        if self.asked is not None:
            self.asked.append((action, tool_path, what))
        return f"{what} are not sent this way"


def _tool(create: CreatePullRequest, refs: dict[str, str], identity: WriteIdentitySettings | None) -> OpenPullRequestTool:
    return OpenPullRequestTool(create, TARGET, lambda: refs, identity, LOG)


class TestGT04TheControlPlaneOpensIt:
    async def test_gt04_the_head_base_and_repository_are_the_control_planes_with_citations_and_the_session_link(
        self,
    ) -> None:
        create = CreatePullRequest()
        refs = {"ref1": "https://kb.example/doc-1", "ref2": "https://kb.example/doc-2"}
        tool = _tool(create, refs, _settings())
        out = await tool.execute(
            title="Fix the widget", summary="Fixes the widget per the design doc [ref2].", citations=["ref2", "ref9", "ref2"],
            owner="evil", repo="other", head="main", base="release",
        )
        assert out.success
        [call] = create.calls
        assert (call["owner"], call["repo"], call["head"], call["base"]) == ("acme", "widgets", BRANCH, "main")
        body = call["body"]
        assert body.startswith("Fixes the widget per the design doc [ref2].")
        assert "### Sources\n1. <https://kb.example/doc-2>" in body
        assert "doc-1" not in body and "ref9" not in body.split("### Sources")[1]
        # WP-90: a pull request is a ticket-kind write, worded "Written via" (one sentence per action type).
        assert body.endswith(f"_Written via PipesHub agent Builder_ · [View the session]({LINK})")
        assert call["draft"] is False

    async def test_gt04_a_write_identity_that_refuses_it_is_never_posted_as_the_person(self) -> None:
        create = CreatePullRequest()
        asked: list[tuple[str, str, str | None]] = []
        refusing = _Refusing(session_id=SESSION, agent_name="Builder", app_url=APP, asked=asked)
        out = await _tool(create, {}, refusing).execute(title="t", summary="s")
        assert not out.success and create.calls == []
        assert out.error == "pull requests are not sent this way"
        assert asked == [("ticket", OPEN_PULL_REQUEST_PATH, "pull requests")]

    async def test_gt04_a_title_and_a_summary_are_needed(self) -> None:
        create = CreatePullRequest()
        out = await _tool(create, {}, _settings()).execute(title=" ", summary="s")
        assert not out.success and create.calls == []

    def test_gt04_only_the_runs_own_refs_become_links(self) -> None:
        refs = {"ref1": "https://a", "ref3": "https://c"}
        assert cited(["ref3", "x", "ref1", "ref3", "https://evil"], refs) == [("ref3", "https://c"), ("ref1", "https://a")]
        assert pull_request_body(" s ", []) == "s"

    def test_gt04_only_a_github_repository_gets_a_pull_request_target(self) -> None:
        assert github_target(URL, BRANCH, "main") == TARGET
        assert github_target("https://gitlab.com/acme/widgets.git", BRANCH, "main") is None
        assert github_target("https://github.com/acme", BRANCH, "main") is None


class _Config:
    """The config store main's tool rules are read from, keyed by path."""

    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    async def get_config(self, key: str, default: object = None, **_: object) -> object:
        return self.values.get(key, default)


class _RulesContext:
    def __init__(self, rules: dict[str, object]) -> None:
        self.tool_state: dict[str, Any] = {}
        self.config_service = _Config(rules)
        self.is_service_account = False
        self.user_id = "user-1"


def _approvals(rule: str | None) -> tuple[str, str]:
    """main's rules on a run whose GitHub instance ``gh-1`` holds ``create_pull_request``: what each tool gets."""
    registry = ToolRegistry()
    create = CreatePullRequest()
    registry.register_tool(create)
    rules = {get_toolset_tool_rules_path("gh-1"): {"tools": {"create_pull_request": rule}}} if rule else {}
    context = _RulesContext(rules)
    context.tool_state[TOOLSET_TOOLS_BY_PATH] = {
        create.path: ApprovalTarget("toolset", "gh-1", "create_pull_request", "GitHub", "write", create)
    }
    checkout = GitCheckout(url=URL, session_branch=BRANCH, credentials=_Refs())
    granted = with_pull_request(
        registry, {}, checkout, dict, _settings(), lambda _e: True, LOG, tool_state=context.tool_state
    )
    policy = ToolRulesPolicy(LayeredApprovalPolicy(), context, registry)  # type: ignore[arg-type]
    asyncio.run(policy.refresh())
    create_effect = classify_tool_effect(create.name, create.tags)
    decide = lambda name, effect: policy(ToolCall(id="c", name=name, arguments={"title": "t"}), effect)  # noqa: E731
    return decide(create.name, create_effect), decide(OPEN_PULL_REQUEST, granted[OPEN_PULL_REQUEST])


class TestGT04ApprovedAsTheToolsetsOwnPullRequest:
    """Owner rule (main #4313, decisions 2026-10-22): a toolset write runs; only a rule that asks holds it. Opening
    the run's pull request is the GitHub toolset's ``create_pull_request`` and is approved exactly as that is."""

    def test_gt04_it_has_the_effect_class_of_create_pull_request(self) -> None:
        registry = ToolRegistry()
        create = CreatePullRequest()
        registry.register_tool(create)
        checkout = GitCheckout(url=URL, session_branch=BRANCH, credentials=_Refs())
        effect = with_pull_request(registry, {}, checkout, dict, _settings(), lambda _e: True, LOG)[OPEN_PULL_REQUEST]
        own = classify_tool_effect(create.name, create.tags)
        assert (effect.effect, effect.idempotent, effect.sink, effect.mark) == (own.effect, own.idempotent, own.sink, own.mark)

    @pytest.mark.parametrize(("rule", "decision"), [(None, "allow"), ("ask", "ask"), ("block", "deny")])
    def test_gt04_it_runs_unless_a_rule_for_create_pull_request_asks_or_blocks(
        self, rule: str | None, decision: str
    ) -> None:
        with patch.object(edition_config, "organization_toolset_tool_rules", True):
            assert _approvals(rule) == (decision, decision)


    def test_gt04_offered_only_with_the_github_toolset_and_a_credential(self) -> None:
        empty = ToolRegistry()
        checkout = GitCheckout(url=URL, session_branch=BRANCH, credentials=_Refs())
        assert with_pull_request(empty, {}, checkout, dict, None, lambda _e: True, LOG) == {}
        registry = ToolRegistry()
        registry.register_tool(CreatePullRequest())
        public = GitCheckout(url=URL, session_branch=BRANCH)
        assert with_pull_request(registry, {}, public, dict, None, lambda _e: True, LOG) == {}
        gitlab = GitCheckout(url="https://gitlab.com/a/b.git", session_branch=BRANCH, credentials=_Refs())
        assert with_pull_request(registry, {}, gitlab, dict, None, lambda _e: True, LOG) == {}
        assert with_pull_request(registry, {}, checkout, dict, None, lambda _e: False, LOG) == {}


class _Refs:
    async def credential_ref(self, phase: str, placeholder: str) -> str:
        return f"sealed:{phase}:{placeholder}"


class _Credentials:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.asked: list[object] = []

    async def toolset_config(self, reference: dict[str, Any]) -> dict[str, Any]:
        self.asked.append(reference)
        return self.record


class _Context:
    def __init__(self, record: dict[str, Any]) -> None:
        self.toolset_configs = {"gh-1": {"ref": "gh-1/user-1"}}
        self.credentials = _Credentials(record)
        mapper = CitationRefMapper()
        mapper.get_or_create_ref("https://kb.example/doc-1")
        self.tool_state = {"citation_ref_mapper": mapper}

    def tool_credentials(self) -> _Credentials:
        return self.credentials


def _spec(repository: dict[str, object] | None) -> RunSpec:
    wire: dict[str, object] = {
        "schemaVersion": 3, "harnessMajor": 2, "foldVersion": 8, "runKind": "child", "principalRef": "p_user-1",
        "model": {"policy": [{"provider": "openai", "model": "gpt-x"}], "promptVersion": "p-1"},
        "grants": {"tools": [], "sandboxProfile": "none", "egress": "none"},
        "budgets": {"maxTurns": 5, "maxTokens": 1000, "maxCostUsd": 1, "maxChildren": 0, "activeTimeS": 600,
                    "calendarDeadline": "2026-10-26T12:00:00.000Z"},
        "capabilitiesDigest": "sha256:" + "c" * 64,
    }
    if repository is not None:
        wire["extensions"] = {"x.pipeshub": {"repository": repository}}
    return RunSpec.model_validate(wire)


class TestTheRunsRepository:
    def test_the_spec_names_the_repository_its_base_and_toolset(self) -> None:
        repo = run_repository(_spec({"url": URL, "baseRef": "develop", "toolsetInstanceId": "gh-1"}))
        assert repo is not None and (repo.url, repo.base_ref, repo.toolset_instance) == (URL, "develop", "gh-1")
        assert run_repository(_spec(None)) is None

    async def test_gt01_the_toolset_token_is_sealed_for_one_placeholder_and_opens_only_there(self) -> None:
        sealer = CredentialSealer(b"deployment")
        context = _Context({"credentials": {"access_token": "tok-0123456789abcdef"}})
        repo = run_repository(_spec({"url": URL, "toolsetInstanceId": "gh-1"}))
        assert repo is not None
        checkout = run_checkout(repo, session_id=SESSION, context=context, owner="user-1", sealer=sealer)
        assert isinstance(checkout.credentials, ToolsetGitCredentials) and checkout.session_branch == BRANCH
        ref = await checkout.credentials.credential_ref("setup", "pxh-setup-0123456789abcdef")
        assert ref is not None and "tok-" not in ref
        assert sealer.open(TokenRoute("pxh-setup-0123456789abcdef", "github.com", ref)) == "x-access-token:tok-0123456789abcdef"
        with pytest.raises(Exception, match="does not open"):
            _ = sealer.open(TokenRoute("pxh-other-0123456789abcdef", "github.com", ref))
        assert context.credentials.asked == [{"ref": "gh-1/user-1"}]

    async def test_a_toolset_without_a_token_seals_nothing_and_no_key_means_a_public_clone(self) -> None:
        repo = run_repository(_spec({"url": URL, "toolsetInstanceId": "gh-1"}))
        assert repo is not None
        empty = run_checkout(repo, session_id=SESSION, context=_Context({}), owner="u", sealer=CredentialSealer(b"k"))
        assert empty.credentials is not None and await empty.credentials.credential_ref("agent", "pxh-x-0123456789abcdef") is None
        assert run_checkout(repo, session_id=SESSION, context=_Context({}), owner="u", sealer=None).credentials is None

    def test_the_runs_citation_links_are_read_when_the_tool_runs(self) -> None:
        context = _Context({})
        read = ref_links(context)
        assert read() == {"ref1": "https://kb.example/doc-1"}
        context.tool_state["citation_ref_mapper"].get_or_create_ref("https://kb.example/doc-2")
        assert read()["ref2"] == "https://kb.example/doc-2"
