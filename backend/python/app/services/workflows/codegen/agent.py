"""WorkflowBuilderAgent — generates workflow code from natural language.

Pipeline (clarify → generate → verify → repair → save):
1. Clarify: Ask the user any missing requirements (via ask_user_question).
2. Generate: Call the LLM with the workflow-sdk skill in context.
3. Verify: Run security lint, syntax check, policy scan (verifier.py).
4. Repair: If verification fails, inject machine-actionable errors and retry.
   Bounded: max_repair_attempts (default 3).
5. Save: On success, extract IR, create WorkflowVersion, return.

This agent is NOT a full LLM loop — it's a structured pipeline that
calls the LLM once per generate/repair attempt. The outer chat agent
orchestrates when to invoke it.
"""
from __future__ import annotations

import logging
import re
from typing import Any

__all__ = ["WorkflowBuilderAgent"]

logger = logging.getLogger(__name__)

_MAX_REPAIR_ATTEMPTS = 3

# Tier 1 (see `_sdk_reference_prompt_text`): bounded core symbols, always
# included regardless of what the workflow is granted.
#
# `ctx.agent`/`ctx.create_agent` are the PRIMARY mechanism for external I/O:
# the workflow builder generates code that creates/orchestrates agents rather
# than calling `ctx.tool()` directly. This avoids the class of errors where
# the builder LLM guesses tool argument schemas (names, formats, required
# fields) without having them — agents handle that correctly.
#
# `ctx.tool` is deliberately absent from the codegen prompt (but remains in
# `_SDK_SYMBOLS` for the interactive `SdkReferenceTool`). Generated code
# should never call tools directly.
_CORE_SDK_SYMBOLS = (
    "workflow", "step", "triggers", "ctx.agent", "ctx.create_agent",
    "ctx.search", "ctx.state", "ctx.emit", "ctx.map", "ctx.wait_for_event",
    "ctx.request_approval", "ctx.now", "ctx.random", "ctx.uuid", "ctx.sleep",
)

_SYSTEM_PROMPT = """You are the PipesHub Workflow Builder. Generate a code workflow that fulfills the user's request.

Every file must start with this import line (add trigger helpers only if used):

    from app.services.workflows.sdk import workflow, step, Ctx, SideEffect

Rules:
- Use ONLY the PipesHub Workflow SDK, exactly as documented in the "SDK reference" section below —
  do not assume conventions from other workflow frameworks (Prefect/Dagster/Airflow/etc). If a
  parameter isn't listed in the reference for a symbol, it doesn't exist; do not invent one.
- ALWAYS await ctx methods — they are all async: `await ctx.now()`, `await ctx.random()`,
  `await ctx.uuid()`, `await ctx.agent(...)`, `await ctx.create_agent(...)`, `await ctx.sleep(...)`.
  Never call them without `await` — an unawaited call returns a coroutine object, not the value,
  and fails at runtime.
- Use ctx.now(), ctx.random(), ctx.uuid() — never datetime.now(), random.random(), uuid.uuid4()
- NEVER import subprocess, socket, requests, httpx, urllib, shutil, pickle; never call eval/exec/open/__import__
- DO NOT call ctx.tool() directly. Instead, create agents to handle external I/O:
      agent = await ctx.create_agent("descriptive-name", tools=["app__action"], instructions="What the agent should do")
      result = await agent.run(goal="Specific task description")
  The agent handles tool calling, argument formatting, error recovery, and retries internally.
  You orchestrate one or more agents plus programming logic (branching, loops, data transformation).
- agent.run() returns a FREE-FORM TEXT string (the agent's natural-language answer), NOT a structured
  dict or object. Do NOT try to index it (result["key"]), parse it as JSON, or call methods on it.
  Use the text directly — pass it to ctx.emit(), return it, or include it in the next agent's goal.
  If you need data from multiple agents, let each agent produce a self-contained text answer and
  compose the results as strings.
- Every workflow must have exactly one `async def` @workflow-decorated function
- Steps that create or run agents must use @step, with side_effect=SideEffect.WRITE
- For recurring or event-driven work, declare it on the entry point:
      @workflow(name="...", triggers=[cron("0 9 * * 1-5", tz="UTC")])
  importing the helpers you use: `from app.services.workflows.sdk import cron, interval, once_at, on_event`

Return ONLY the Python code. No markdown code fences. No explanation.
"""

_REPAIR_PROMPT_TEMPLATE = """The previous code had these verification errors:
{errors}

Fix ALL errors and return the corrected Python code. No markdown fences. No explanation."""


class WorkflowBuilderAgent:
    """Generates and validates workflow code from natural language."""

    def __init__(
        self,
        *,
        llm_caller: Any,
        max_repair_attempts: int = _MAX_REPAIR_ATTEMPTS,
    ) -> None:
        self._llm = llm_caller
        self._max_repair = max_repair_attempts

    async def generate(
        self,
        *,
        spec: str,
        org_id: str,
        user_id: str,
        workflow_id: str | None = None,
        existing_source: str | None = None,
        connected_apps: list[str] | None = None,
        tool_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate or edit a workflow. Returns {ok, source, ir, errors}."""
        from app.services.workflows.codegen.verifier import verify_workflow_source
        from app.services.workflows.ir.extractor import extract_ir

        manifest_text = _build_manifest_prompt_text(
            tool_names=tool_names,
            connected_apps=connected_apps,
        )
        sdk_reference_text = _sdk_reference_prompt_text(tool_names=tool_names)

        prompt_parts = [_SYSTEM_PROMPT]
        prompt_parts.append(f"\n## SDK reference\n{sdk_reference_text}")
        prompt_parts.append(f"\n## Platform capabilities\n{manifest_text}")
        if existing_source:
            prompt_parts.append(
                f"\n## Existing workflow code (edit this)\n```python\n{existing_source}\n```"
            )
        prompt_parts.append(f"\n## User request\n{spec}")
        prompt_parts.append("\nGenerate the workflow code now:")

        source: str | None = None
        last_result = None
        attempts_used = 0
        for attempt in range(self._max_repair + 1):
            attempts_used = attempt + 1
            if attempt == 0:
                source = await self._llm("\n".join(prompt_parts), _SYSTEM_PROMPT)
            else:
                assert last_result is not None
                errors_text = "\n".join(
                    f"- [{e['code']}] {e['field']}: {e['fix_hint']}"
                    for e in last_result.to_dict()["errors"]
                )
                # The repair prompt used to omit the SDK reference entirely,
                # so a repair attempt would re-invent the same hallucinated
                # kwargs/signatures the first attempt got wrong -- the model
                # had the error but not the ground truth needed to fix it
                # correctly rather than by guessing again.
                repair_prompt = (
                    _REPAIR_PROMPT_TEMPLATE.format(errors=errors_text)
                    + f"\n\n## SDK reference\n{sdk_reference_text}"
                    + f"\n\nPrevious code:\n```python\n{source}\n```"
                )
                source = await self._llm(repair_prompt, _SYSTEM_PROMPT)

            source = _strip_fences(source)
            last_result = verify_workflow_source(source, allowed_tools=tool_names)
            error_codes = [e["code"] for e in last_result.to_dict()["errors"]]
            logger.info(
                "workflow_codegen_attempt workflow_id=%s attempt=%d/%d ok=%s error_codes=%s",
                workflow_id, attempt + 1, self._max_repair + 1, last_result.ok, error_codes,
            )

            if last_result.ok:
                break
            errors = last_result.to_dict()["errors"]
            if attempt >= self._max_repair:
                logger.warning(
                    "workflow_codegen_outcome workflow_id=%s outcome=exhausted attempts_used=%d "
                    "error_codes=%s errors=%s",
                    workflow_id, attempts_used, error_codes, errors,
                )
                return {
                    "ok": False,
                    "source": source,
                    "errors": errors,
                }
            logger.warning(
                "Codegen for workflow %s failed verification on attempt %d/%d, retrying: %s",
                workflow_id, attempt + 1, self._max_repair + 1, errors,
            )

        if not last_result or not last_result.ok:
            logger.warning(
                "workflow_codegen_outcome workflow_id=%s outcome=failed attempts_used=%d",
                workflow_id, attempts_used,
            )
            return {
                "ok": False,
                "source": source,
                "errors": last_result.to_dict()["errors"] if last_result else [],
            }

        ir = extract_ir(source)  # type: ignore[arg-type]
        logger.info(
            "workflow_codegen_outcome workflow_id=%s outcome=success attempts_used=%d",
            workflow_id, attempts_used,
        )
        return {
            "ok": True,
            "source": source,
            "ir": ir.model_dump(),
            "errors": [],
        }


def _strip_fences(text: str | list) -> str:
    """Remove markdown code fences if the LLM returned them.

    Handles the case where the LLM returns a list of content blocks
    (OpenAI Responses API) instead of a plain string.
    """
    if isinstance(text, list):
        text = "\n".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in text
        )
    text = str(text).strip()
    text = re.sub(r"^```(?:python)?\n", "", text)
    text = re.sub(r"\n```$", "", text)
    return text.strip()


def _sdk_reference_prompt_text(*, tool_names: list[str] | None = None) -> str:
    """The exact signatures/docs from `sdk_reference_tool.py` — the same
    text the interactive chat agent gets back from calling
    `sdk_reference(symbol)`. Inlining it here (rather than only the terse
    bullet points in `_SYSTEM_PROMPT`) closes the gap that let an LLM guess
    a plausible-but-nonexistent kwarg (`@step(name=...)`): this pipeline is
    a single-shot completion with no tool-calling loop, so `sdk_reference`
    is never reachable during generation — the accurate reference has to be
    in the prompt up front, not just available for lookup.

    Sharing `_SDK_SYMBOLS` as the one source of truth means this can't
    silently drift from what `sdk_reference` reports elsewhere.

    Tiered rather than dumping everything:
    - Tier 1 (`_CORE_SDK_SYMBOLS`) is bounded and always included.
      Includes `ctx.agent`/`ctx.create_agent` as the primary mechanism for
      external I/O — workflows orchestrate agents instead of calling tools
      directly. `ctx.tool` is deliberately excluded from the codegen prompt
      (but remains in ``_SDK_SYMBOLS`` for the interactive
      ``SdkReferenceTool``).
    - Tier 2 (event schemas, keyed `events/<app>.<name>`) is unbounded as
      connectors grow, so it's scoped to the apps present in `tool_names` —
      the workflow's own tool grant, which is already the authoritative
      statement of which apps are in play.
    """
    from app.services.workflows.codegen.sdk_reference_tool import _SDK_SYMBOLS

    sections = [_SDK_SYMBOLS[symbol] for symbol in _CORE_SDK_SYMBOLS if symbol in _SDK_SYMBOLS]

    granted_apps = {name.split("__", 1)[0] for name in (tool_names or []) if "__" in name}
    if granted_apps:
        sections += [
            text
            for key, text in _SDK_SYMBOLS.items()
            if key.startswith("events/") and key.split("/", 1)[1].split(".", 1)[0] in granted_apps
        ]
    return "\n\n".join(sections)


def _build_manifest_prompt_text(
    *, tool_names: list[str] | None, connected_apps: list[str] | None,
) -> str:
    """Renders the caller's connected apps / available tools as prompt text.

    These tools are passed to agents via ``ctx.create_agent(tools=[...])``,
    NOT called directly via ``ctx.tool()``. The generated workflow code
    creates agents and gives them the tools they need."""
    lines: list[str] = []
    if connected_apps:
        lines.append("Connected apps: " + ", ".join(sorted(connected_apps)))
    if tool_names:
        lines.append("Available tools (pass these to ctx.create_agent via the tools= parameter):")
        lines.extend(f"  - {name}" for name in sorted(tool_names))
    if not lines:
        lines.append("No connected apps or tools are currently available to this workflow.")
    return "\n".join(lines)
