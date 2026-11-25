"""ANSWER-CONTRACT-SOURCES: the grounding check searched only PipesHub knowledge, while the person had Atlassian Rovo connected.

Now it covers every connected source that can search for the subject, picked by what its tools declare (read-only, a
search word in the name, one text argument), called in the run's tool scope only where the person's rules let it run
unasked. Found in one, the subject is found there, and the job is told where; found nowhere, the words name every
place looked in, never "in your knowledge" alone.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from app.harness.assembly.code_task import found_line
from app.harness.assembly.grounding_gate import GroundingGate
from app.harness.assembly.grounding_sources import (
    hits_of,
    runs_without_asking,
    source_searches,
)
from app.harness.assembly.loop import GOAL_SLOT
from app.harness.assembly.task_spec import TaskSpec, request_of
from app.harness.grounding import (
    GROUNDING_KEY,
    Grounding,
    Place,
    grounding_answer,
    grounding_card,
    grounding_question,
    not_found_line,
    not_found_view,
)
from harness_core.logs import HarnessLog
from harness_core.tools.base import ParameterType, ToolOutput, ToolParameter

from .test_answer_contract import _run

LOG = HarnessLog(logging.getLogger("test.answer_contract_sources"), {})
REQUEST = 'Prepare a risk report for "SAAS launch"'
FAQ = {"content": "PipesHub Orbit is a SaaS product.", "metadata": {"recordName": "product-faq", "recordId": "r1"}}


class _Tool:
    def __init__(self, name: str, *, kind: str = "read", server: str = "Atlassian Rovo", params: list[ToolParameter] | None = None,
                 result: Any = None, context: Any = None, knowledge: bool = False) -> None:  # noqa: ANN401
        self.tool_name = name
        self.kind = kind
        self.server = SimpleNamespace(display_name=server) if not knowledge else None
        self.parameters = params if params is not None else [ToolParameter("query", ParameterType.STRING, "q")]
        self._result = result
        self._context = context
        self._knowledge = knowledge
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        self.calls.append(kwargs)
        if self._knowledge:
            self._context.tool_state["final_results"] = [FAQ]
        return ToolOutput(success=True, data=self._result)


class _Registry:
    def __init__(self, tools: dict[str, _Tool]) -> None:
        self.tools = tools

    def has(self, name: str) -> bool:
        return name in self.tools

    def resolve_by_name(self, name: str) -> _Tool:
        return self.tools[name]

    def path_for_name(self, name: str) -> str:
        return f"/tools/{name}"


def _context() -> Any:  # noqa: ANN401
    return SimpleNamespace(has_ui_client=True, org_id="org", user_id="u", user_info={"userId": "u"}, tool_state={}, query="")


def _world(rovo_result: Any) -> tuple[GroundingGate, _Registry]:  # noqa: ANN401
    context = _context()
    tools = {
        "knowledgegraph__search": _Tool("search", knowledge=True, context=context),
        "mcp_rovo_search": _Tool("search", result=rovo_result),
        "mcp_rovo_createJiraIssue": _Tool("createJiraIssue", kind="write"),
    }
    registry = _Registry(tools)
    gate = GroundingGate(_run(REQUEST), context, None, LOG, registry=registry, offered=list(tools))  # type: ignore[arg-type]
    return gate, registry


def test_sources_are_picked_by_what_their_tools_declare() -> None:
    string = ParameterType.STRING
    tools = {
        "mcp_rovo_search": _Tool("search"),
        "mcp_rovo_searchJiraIssuesUsingJql": _Tool(
            "searchJiraIssuesUsingJql",
            params=[ToolParameter("jql", string, "j"), ToolParameter("cloudId", string, "c")],
        ),
        "mcp_rovo_createJiraIssue": _Tool("createJiraIssue", kind="write"),
        "mcp_saas_lookup_note": _Tool("lookup_note", server="e2esaas"),
        "mcp_saas_find_pages": _Tool("find_pages", server="e2esaas", params=[ToolParameter("text", string, "t", required=False)]),
        "knowledgegraph__search": _Tool("search", knowledge=True),
    }
    picked = source_searches(_context(), _Registry(tools), list(tools))  # type: ignore[arg-type]
    assert [(s.name, s.place, s.argument) for s in picked] == [
        ("mcp_rovo_search", "Atlassian Rovo", "query"),
        ("mcp_saas_find_pages", "e2esaas", "text"),
    ]


def test_a_result_reads_as_hits_and_saying_nothing_was_found_is_none() -> None:
    results = json.dumps({"results": [{"title": "SAAS launch plan", "body": "go-live 1 December"}, {"title": "Other"}]})
    assert [h.name for h in hits_of(results, "Atlassian Rovo")] == ["Atlassian Rovo: SAAS launch plan", "Atlassian Rovo: Other"]
    assert hits_of("No results found for SAAS launch.", "Atlassian Rovo") == []
    assert hits_of(json.dumps({"results": []}), "Atlassian Rovo") == []
    assert len(hits_of("SAAS launch plan\n\nThe go-live is 1 December.", "e2esaas")) == 2


async def test_found_in_a_connected_source_is_found_there_and_the_job_is_told_where() -> None:
    gate, registry = _world(json.dumps({"results": [{"title": "SAAS launch plan", "body": "go-live 1 December"}]}))
    checked = await gate.check(None, delegating=True)
    assert checked is not None and checked.question is None
    grounding = checked.grounding
    assert (grounding.status, grounding.places, grounding.found_in) == (
        "found", ("Atlassian Rovo",), (Place("Atlassian Rovo", "search"),)
    )
    assert registry.tools["mcp_rovo_search"].calls == [{"query": "SAAS launch"}]
    assert registry.tools["mcp_rovo_createJiraIssue"].calls == []
    line = found_line({GOAL_SLOT: {GROUNDING_KEY: grounding.to_json()}})
    assert line == "Where PipesHub found “SAAS launch”: Atlassian Rovo (its `search` tool)"
    task = TaskSpec.compose(request=REQUEST, goal="", context="", found=line).render()
    assert line in task and request_of(task) == REQUEST


async def test_found_nowhere_the_words_name_every_place_looked_in() -> None:
    gate, _ = _world("No results found.")
    checked = await gate.check(None, delegating=True)
    assert checked is not None and checked.question is not None
    grounding = checked.grounding
    assert (grounding.status, grounding.places, grounding.found_in) == ("not_found", ("Atlassian Rovo",), ())
    question = str(checked.question["question"])
    assert question.startswith("I looked in your PipesHub knowledge and Atlassian Rovo and couldn't find anything about “SAAS launch”")
    assert "in your knowledge" not in question
    assert "Atlassian Rovo" in str(not_found_line(grounding)) and "in your knowledge" not in str(not_found_line(grounding))
    assert not_found_view(grounding)["places"] == ["Atlassian Rovo"]  # type: ignore[index]
    assert grounding_card(grounding)["places"] == ["Atlassian Rovo"]
    # The answer to either wording reads back, Skip included.
    answer = grounding_answer(f'User selections:\n1. "{question}" → [No preference]')
    assert answer is not None and (answer.subject, answer.choice) == ("SAAS launch", "use_found")


async def test_a_search_the_person_s_rules_do_not_let_run_unasked_is_never_called() -> None:
    gate, registry = _world(json.dumps({"results": [{"title": "SAAS launch plan"}]}))
    rule = SimpleNamespace(rules=SimpleNamespace(tools={"search": "ask"}), unavailable=False)
    target = SimpleNamespace(owner="Atlassian Rovo", tool_name="search", kind="read", source="mcp", instance_id="i1",
                             personal=False, defaults=None)

    async def rules(*_: Any, **__: Any) -> Any:  # noqa: ANN401
        return rule

    with patch("app.agents.agent_loop.tool_approvals._target", lambda _c, p: target if "rovo" in p else None), \
            patch("app.agents.agent_loop.tool_approvals._rules_for", rules):
        search = source_searches(gate._context, registry, ["mcp_rovo_search"])  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert [s.name for s in search] == ["mcp_rovo_search"]
        assert not await runs_without_asking(gate._context, registry, search[0])  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        checked = await gate.check(None, delegating=True)
    assert registry.tools["mcp_rovo_search"].calls == []
    assert checked is not None and checked.grounding.places == ()


def test_an_older_grounding_reads_as_before() -> None:
    old = Grounding.from_json({"status": "not_found", "subject": "SAAS launch", "searches": 1, "hits": 2, "relevant": 0, "near": []})
    assert old is not None and old.places == () and "in your knowledge" in str(not_found_line(old))
    _, question = grounding_question(old)
    assert "in your knowledge" in str(question["question"])
