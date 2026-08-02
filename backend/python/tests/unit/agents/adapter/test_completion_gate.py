"""Tests for `app.agents.agent_loop.hooks.completion_gate` — the POST_MODEL
middleware that stops a weak model from ending a run with a text-only
answer when the request needed a generated file, or with an empty
response. See the module docstring for the full rationale."""

from __future__ import annotations

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall, UserMessage
from app.agent_loop_lib.core.scope import RunScope, TurnScope
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.completion_gate import (
    completion_gate,
    looks_like_file_generation_request,
)
from tests.unit.agents.adapter.support.hook_helpers import run_post_model


def _make_context(**overrides) -> AgentContext:
    defaults: dict = {"org_id": "org-1", "user_id": "user-1", "user_email": "u@example.com"}
    defaults.update(overrides)
    return AgentContext(**defaults)


def _turn_scope(tool_names: list[str]) -> TurnScope:
    spec = AgentSpec(
        name="agent-under-test", system_prompt="x", tool_names=tool_names,
        model=ModelSpec(provider="scripted", model="m"),
    )
    run_scope = RunScope(
        identity=RunContext(role_name="agent-under-test", model="m"),
        spec=spec, runtime=AgentRuntime(), goal=Goal(description="g"),
    )
    return TurnScope(run=run_scope, turn_index=0)


class TestLooksLikeFileGenerationRequest:
    def test_detects_pdf_by_name(self) -> None:
        assert looks_like_file_generation_request("please create a PDF report") is True

    def test_detects_file_extension(self) -> None:
        assert looks_like_file_generation_request("export the results.csv") is True

    def test_no_match_on_unrelated_request(self) -> None:
        assert looks_like_file_generation_request("what is the capital of France?") is False

    def test_checks_every_text_argument(self) -> None:
        assert looks_like_file_generation_request("hi", "generate a downloadable file please") is True

    def test_ignores_empty_strings(self) -> None:
        assert looks_like_file_generation_request("", "") is False

    def test_no_match_on_attached_pdf_analysis(self) -> None:
        """The exact false-positive that triggered the bug: user uploads a PDF
        for analysis and the intent model mentions the format in its rewrite."""
        assert looks_like_file_generation_request(
            "explain me skills of prabal and wether he is fit for an agentic ai role ?",
            "Analyze the attached PDF resume of Prabal Kaushik and assess fit for an agentic AI role",
        ) is False

    def test_no_match_on_bare_format_keyword(self) -> None:
        assert looks_like_file_generation_request("summarize this pdf") is False

    def test_matches_as_a_pdf(self) -> None:
        assert looks_like_file_generation_request("save this as a pdf") is True

    def test_matches_convert_to_csv(self) -> None:
        assert looks_like_file_generation_request("convert the data to csv") is True

    def test_matches_give_me_xlsx(self) -> None:
        assert looks_like_file_generation_request("give me an xlsx with the numbers") is True

    def test_no_match_when_verb_targets_different_object(self) -> None:
        """Creation verbs targeting tickets/meetings/etc should not trigger
        just because a format keyword appears later in the sentence."""
        assert looks_like_file_generation_request("create a ticket for the pdf upload bug") is False
        assert looks_like_file_generation_request("make a meeting to discuss csv imports") is False
        assert looks_like_file_generation_request("create a jira issue about the xlsx parser") is False

    def test_matches_regenerate_the_pdf(self) -> None:
        """'regenerate' must be recognized on its own — `\\bgenerate\\b`
        does not match inside "regenerate" (no word boundary before
        "generate"), so relying on the substring alone would miss the
        exact "on existing artifact update" phrasing this covers."""
        assert looks_like_file_generation_request("please regenerate the pdf with the fix") is True

    def test_matches_update_the_csv(self) -> None:
        assert looks_like_file_generation_request("update the csv with the new totals") is True

    def test_matches_refresh_and_revise(self) -> None:
        assert looks_like_file_generation_request("refresh the xlsx report") is True
        assert looks_like_file_generation_request("revise the docx with my comments") is True

    def test_matches_fix_the_pdf(self) -> None:
        assert looks_like_file_generation_request("fix the pdf table formatting") is True
        assert looks_like_file_generation_request("redo the pptx slides") is True

    def test_no_match_bare_fix_without_format_keyword(self) -> None:
        """'fix'/'redo' are too generic (bug reports, general redo requests)
        to trigger without a format keyword immediately adjacent — unlike
        the broader verb list, which tolerates 0-2 modifier words."""
        assert looks_like_file_generation_request("fix the login bug") is False
        assert looks_like_file_generation_request("redo this calculation") is False

    def test_no_match_update_me_without_format_keyword(self) -> None:
        assert looks_like_file_generation_request("update me on the project status") is False

    def test_no_match_on_knowledge_graph(self) -> None:
        """'graph' used to be an unconditional trigger, so any mention of a
        knowledge graph, graph database, etc. false-positived as a request
        to generate a chart/plot."""
        assert looks_like_file_generation_request(
            "Best way to implement Knowledge graph system",
        ) is False
        assert looks_like_file_generation_request("how does the graph database index edges?") is False
        assert looks_like_file_generation_request("explain graph traversal algorithms") is False

    def test_matches_create_a_bar_graph(self) -> None:
        assert looks_like_file_generation_request("create a bar graph of the results") is True
        assert looks_like_file_generation_request("plot a chart of monthly sales") is True

    def test_matches_convert_to_a_chart(self) -> None:
        assert looks_like_file_generation_request("turn this data into a chart") is True

    def test_no_match_bare_plot_without_visualization_noun(self) -> None:
        assert looks_like_file_generation_request("plot the next chapter of the story") is False


class TestCompletionGate:
    async def test_noop_when_tool_calls_present(self) -> None:
        context = _make_context(file_generation_requested=True)
        gate = completion_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content=""),
            tool_calls=[ToolCall(id="1", name="run_code", arguments={})],
            scope=_turn_scope(["run_code"]),
        )
        assert ctx.recovery_message is None

    async def test_nudges_on_empty_response_regardless_of_file_flag(self) -> None:
        context = _make_context(file_generation_requested=False)
        gate = completion_gate(context)
        ctx = await run_post_model(gate, AssistantMessage(content=""), scope=_turn_scope([]))
        assert ctx.recovery_message is not None
        assert context.completion_gate_nudges == 1

    async def test_nudges_when_file_requested_and_no_artifact_yet(self) -> None:
        context = _make_context(file_generation_requested=True)
        gate = completion_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content="Here is a description of the PDF I would make."),
            scope=_turn_scope(["run_code"]),
        )
        assert ctx.recovery_message is not None
        assert isinstance(ctx.recovery_message, UserMessage)
        assert ctx.recovery_message.injected is True

    async def test_no_nudge_once_artifact_produced(self) -> None:
        context = _make_context(file_generation_requested=True, artifacts_produced_this_run=True)
        gate = completion_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content="Here is the PDF you asked for."),
            scope=_turn_scope(["run_code"]),
        )
        assert ctx.recovery_message is None

    async def test_no_nudge_for_non_file_generation_request(self) -> None:
        context = _make_context(file_generation_requested=False)
        gate = completion_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content="The answer is 42."), scope=_turn_scope(["run_code"]),
        )
        assert ctx.recovery_message is None

    async def test_no_nudge_when_agent_has_no_file_generation_tool(self) -> None:
        """A domain agent with no run_code/coding_agent tool (e.g. a
        calculator sub-agent) must never be nudged to call a tool it
        doesn't have, even if the shared context's file-generation flag
        is set from the top-level request."""
        context = _make_context(file_generation_requested=True)
        gate = completion_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content="42."), scope=_turn_scope(["calculator"]),
        )
        assert ctx.recovery_message is None

    async def test_bounded_by_max_nudges(self) -> None:
        context = _make_context(file_generation_requested=True)
        gate = completion_gate(context, max_nudges=1)
        scope = _turn_scope(["run_code"])

        first = await run_post_model(gate, AssistantMessage(content="describing the file"), scope=scope)
        second = await run_post_model(gate, AssistantMessage(content="describing the file again"), scope=scope)

        assert first.recovery_message is not None
        assert second.recovery_message is None

    async def test_skips_truncated_response(self) -> None:
        context = _make_context(file_generation_requested=True)
        gate = completion_gate(context)
        message = AssistantMessage(content="", truncated=True)
        ctx = await run_post_model(gate, message, scope=_turn_scope(["run_code"]))
        assert ctx.recovery_message is None

    async def test_conservative_without_a_scope(self) -> None:
        """No `TurnScope` means the middleware can't tell whether the
        agent even has a file-generation tool — treated as "no", so a
        non-empty text response is left alone rather than nudged blindly."""
        context = _make_context(file_generation_requested=True)
        gate = completion_gate(context)
        ctx = await run_post_model(gate, AssistantMessage(content="some text, no tool call"))
        assert ctx.recovery_message is None
