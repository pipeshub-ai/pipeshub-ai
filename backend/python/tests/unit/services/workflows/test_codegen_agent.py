"""`WorkflowBuilderAgent.generate()` is a single-shot completion — it has no
tool-calling loop, so it can never call `sdk_reference()` mid-generation to
look up an accurate signature. These tests guard the fix for that gap: the
same reference text `sdk_reference_tool.py` serves interactively must also
be present in the initial generation prompt, not just discoverable later
via the repair loop.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.workflows.codegen.agent import (
    WorkflowBuilderAgent,
    _sdk_reference_prompt_text,
)
from app.services.workflows.codegen.sdk_reference_tool import _SDK_SYMBOLS


class _RecordingLLM:
    """Captures every prompt it's called with and returns canned sources in
    order — the first is the "generation" response, subsequent ones are
    "repair" responses."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str, system_prompt: str) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0)


_VALID_SOURCE = """
from app.services.workflows.sdk import workflow, Ctx

@workflow
async def my_workflow(ctx: Ctx) -> str:
    return "done"
"""


class TestSdkReferencePromptText:
    def test_includes_the_exact_step_signature_reference(self) -> None:
        """Same source of truth as `sdk_reference("step")` -- if these ever
        diverge, the model gets a different answer from the prompt than
        from calling the tool."""
        text = _sdk_reference_prompt_text()
        assert _SDK_SYMBOLS["step"] in text

    def test_includes_the_exact_workflow_signature_reference(self) -> None:
        text = _sdk_reference_prompt_text()
        assert _SDK_SYMBOLS["workflow"] in text

    def test_does_not_silently_drop_a_missing_symbol(self) -> None:
        """Every symbol name it asks for must exist in `_SDK_SYMBOLS`, or a
        typo here would silently omit a signature from the prompt."""
        text = _sdk_reference_prompt_text()
        for symbol in ("workflow", "step", "triggers", "ctx.agent", "ctx.create_agent"):
            assert _SDK_SYMBOLS[symbol] in text

    def test_ctx_agent_is_advertised(self) -> None:
        """``ctx.agent``/``ctx.create_agent`` are the primary mechanism for
        external I/O in generated workflows. The builder generates code that
        orchestrates agents instead of calling ``ctx.tool()`` directly."""
        text = _sdk_reference_prompt_text()
        assert _SDK_SYMBOLS["ctx.agent"] in text
        assert _SDK_SYMBOLS["ctx.create_agent"] in text

    def test_ctx_tool_is_not_in_codegen_prompt(self) -> None:
        """Workflows should use ``ctx.create_agent()`` / ``ctx.agent()`` for
        external I/O, not ``ctx.tool()`` directly. ``ctx.tool`` remains in
        ``_SDK_SYMBOLS`` for the interactive ``SdkReferenceTool`` but is
        excluded from the codegen prompt."""
        text = _sdk_reference_prompt_text()
        assert _SDK_SYMBOLS["ctx.tool"] not in text

    def test_event_schemas_are_scoped_to_the_granted_apps(self) -> None:
        """Tier 2 (event schemas) is unbounded as connectors grow, so it's
        selected from the workflow's own tool grant rather than included
        unconditionally."""
        text = _sdk_reference_prompt_text(tool_names=["slack__send_message"])
        assert _SDK_SYMBOLS["events/slack.message.posted"] in text
        assert _SDK_SYMBOLS["events/jira.issue.created"] not in text

    def test_no_tool_grant_means_no_event_schemas(self) -> None:
        text = _sdk_reference_prompt_text(tool_names=None)
        assert _SDK_SYMBOLS["events/slack.message.posted"] not in text
        assert _SDK_SYMBOLS["events/jira.issue.created"] not in text


class TestGeneratePromptIncludesSdkReference:
    @pytest.mark.asyncio
    async def test_first_generation_call_includes_step_signature(self) -> None:
        """The actual regression: without this, the model has no way to
        learn `@step` takes no `name` kwarg until *after* generating
        invalid code and going through a repair round-trip."""
        llm = _RecordingLLM([_VALID_SOURCE])
        agent = WorkflowBuilderAgent(llm_caller=llm)

        result = await agent.generate(spec="do something", org_id="org-1", user_id="user-1")

        assert result["ok"] is True
        assert len(llm.prompts) == 1
        assert "@step(retries=0, timeout_s=None, side_effect=SideEffect.NONE)" in llm.prompts[0]

    @pytest.mark.asyncio
    async def test_repair_prompt_includes_sdk_reference(self) -> None:
        """Regression: the repair prompt used to be built from
        `_REPAIR_PROMPT_TEMPLATE` + the error text + the previous code only
        -- no SDK reference. The model would lose access to accurate
        signatures on exactly the call where it's proven it needs them
        (it just got something wrong), and would re-invent the same
        hallucinated kwarg instead of fixing it."""
        bad_source = _VALID_SOURCE.replace(
            "@workflow\n", '@step(name="x")\n@workflow\n',
        )
        llm = _RecordingLLM([bad_source, _VALID_SOURCE])
        agent = WorkflowBuilderAgent(llm_caller=llm, max_repair_attempts=1)

        result = await agent.generate(spec="do something", org_id="org-1", user_id="user-1")

        assert result["ok"] is True
        assert len(llm.prompts) == 2
        repair_prompt = llm.prompts[1]
        assert "## SDK reference" in repair_prompt
        assert "@step(retries=0, timeout_s=None, side_effect=SideEffect.NONE)" in repair_prompt

    @pytest.mark.asyncio
    async def test_an_invented_kwarg_triggers_a_repair_round_with_a_precise_hint(self) -> None:
        bad_source = _VALID_SOURCE.replace(
            "@workflow\n", '@workflow(concurrency=4)\n',
        )
        llm = _RecordingLLM([bad_source, _VALID_SOURCE])
        agent = WorkflowBuilderAgent(llm_caller=llm, max_repair_attempts=1)

        await agent.generate(spec="do something", org_id="org-1", user_id="user-1")

        repair_prompt = llm.prompts[1]
        assert "INVALID_DECORATOR_ARG" in repair_prompt
        assert "concurrency" in repair_prompt
