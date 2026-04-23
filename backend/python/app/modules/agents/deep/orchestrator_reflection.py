"""
Orchestrator Reflection — drop-in replacement for the relevant sections of
orchestrator.py.

Adds two reflection layers:
  1. Parse-time reflection  — JSON came back malformed → retry LLM with the
                              bad output shown back and a corrective prompt.
  2. Plan-time reflection   — JSON parsed but plan has logical issues → retry
                              LLM with an itemised list of every problem found.

Both share the same retry loop (max MAX_REFLECTION_RETRIES = 2).
After exhausting retries, a hard error is surfaced to the user.
Plan validation is STRICT — any issue blocks dispatch.

Fixes applied vs v1
-------------------
* Fix 1  available_domains now threaded into _append_plan_reflection so the
         retry prompt shows the real domain list instead of "(same as before)".
* Fix 2  config (RunnableConfig / Opik) is an optional param on
         run_orchestrator_with_reflection and passed to every llm.ainvoke call,
         including reflection retries — tracing is never silently dropped.
* Fix 3  Docstring item 10 ("trailing garbage detection") removed from
         _validate_plan — _parse_plan already drops trailing content via
         raw_decode; the validator never sees the raw string so this check
         was misleading and unimplemented.
* Fix 4  isinstance(task, dict) guard added before task.get() calls to
         prevent AttributeError when the model puts a non-dict in tasks[].

Prompt improvements (v3)
------------------------
* Parse-time reflection now asks the model to re-reason about the query from
  scratch rather than just patch broken JSON syntax.
* Plan-time reflection leads with a structured self-examination checklist
  (query coverage, ordering, domain fit, complexity) before listing errors,
  so the model improves the plan's logic, not just its formatting.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

MAX_REFLECTION_RETRIES = 2


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ParseResult:
    """Outcome of attempting to parse the raw LLM string."""
    ok: bool
    plan: dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    error: str = ""


@dataclass
class ValidationResult:
    """Outcome of validating a parsed plan."""
    ok: bool
    issues: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class OrchestratorReflectionError(Exception):
    """Raised when reflection retries are exhausted without a valid plan."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_orchestrator_with_reflection(
    llm,
    messages: list,
    available_domains: set[str],
    log: logging.Logger,
    config: Optional[RunnableConfig] = None,
) -> dict[str, Any]:
    """
    Call the LLM, parse the response, validate the plan, and reflect/retry
    on any failure up to MAX_REFLECTION_RETRIES times.

    Parameters
    ----------
    llm               : LangChain chat model (supports .ainvoke)
    messages          : Full message list already built by orchestrator_node
                        (system + history + user query). This list is extended
                        in-place on reflection retries with corrective turns.
    available_domains : Set of domain strings that actually have tools assigned
                        (e.g. {"mariadb", "retrieval"}). Used for validation
                        AND threaded into reflection prompts so retry messages
                        show the real domain list.
    log               : Logger from state.
    config            : Optional RunnableConfig (e.g. get_opik_config()).
                        Passed to every llm.ainvoke call including retries so
                        Opik / LangSmith tracing is never silently dropped.

    Returns
    -------
    Validated plan dict with keys: can_answer_directly, reasoning, tasks.

    Raises
    ------
    OrchestratorReflectionError  after MAX_REFLECTION_RETRIES exhausted.
    """
    attempt = 0

    while attempt <= MAX_REFLECTION_RETRIES:
        # ── LLM call — config forwarded on every attempt including retries ──
        invoke_kwargs: dict[str, Any] = {}
        if config is not None:
            invoke_kwargs["config"] = config

        response = await llm.ainvoke(messages, **invoke_kwargs)
        raw = response.content if hasattr(response, "content") else str(response)

        log.debug(
            "Orchestrator LLM response (attempt %d, %d chars): %s...",
            attempt + 1, len(raw), raw[:200],
        )

        # ── Parse ────────────────────────────────────────────────────────────
        parse_result = _parse_plan(raw, log)

        if not parse_result.ok:
            attempt += 1
            if attempt > MAX_REFLECTION_RETRIES:
                raise OrchestratorReflectionError(
                    f"JSON parse failed after {MAX_REFLECTION_RETRIES} reflection "
                    f"retries. Last error: {parse_result.error}\n"
                    f"Raw output (first 500 chars): {raw[:500]}"
                )
            log.warning(
                "Orchestrator parse failure (attempt %d/%d): %s — reflecting...",
                attempt, MAX_REFLECTION_RETRIES, parse_result.error,
            )
            messages = _append_parse_reflection(messages, raw, parse_result.error)
            continue

        plan = parse_result.plan

        # ── Validate ─────────────────────────────────────────────────────────
        validation = _validate_plan(plan, available_domains, log)

        if not validation.ok:
            attempt += 1
            if attempt > MAX_REFLECTION_RETRIES:
                raise OrchestratorReflectionError(
                    f"Plan validation failed after {MAX_REFLECTION_RETRIES} reflection "
                    f"retries. Final issues:\n"
                    + "\n".join(f"  - {i}" for i in validation.issues)
                )
            log.warning(
                "Orchestrator plan invalid (attempt %d/%d): %d issue(s) — reflecting...",
                attempt, MAX_REFLECTION_RETRIES, len(validation.issues),
            )
            # FIX 1: pass available_domains so the retry prompt shows the real list
            messages = _append_plan_reflection(
                messages, plan, validation.issues, available_domains
            )
            continue

        # ── Success ──────────────────────────────────────────────────────────
        if attempt > 0:
            log.info("Orchestrator reflection succeeded after %d retry(ies).", attempt)
        return plan

    # Should never be reached, but satisfies type checkers
    raise OrchestratorReflectionError("Reflection loop exited without a valid plan.")


# ---------------------------------------------------------------------------
# Parser — extracts the FIRST valid JSON object, ignores trailing garbage
# ---------------------------------------------------------------------------

def _parse_plan(raw: str, log: logging.Logger) -> ParseResult:
    """
    Extract the first valid JSON object from raw LLM output.

    Uses json.JSONDecoder.raw_decode so that trailing tool-call traces,
    status strings, or hallucinated execution output are simply ignored.
    """
    text = raw.strip()

    # Strip markdown fences
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

    decoder = json.JSONDecoder()

    # Walk forward until we find a parseable JSON object
    for start in range(len(text)):
        if text[start] != "{":
            continue
        try:
            plan, _ = decoder.raw_decode(text, start)
            if isinstance(plan, dict):
                plan.setdefault("can_answer_directly", False)
                plan.setdefault("tasks", [])
                plan.setdefault("reasoning", "")
                return ParseResult(ok=True, plan=plan, raw=raw)
        except json.JSONDecodeError:
            continue

    return ParseResult(
        ok=False,
        raw=raw,
        error=(
            "No valid JSON object found. The response contained: "
            + repr(raw[:300])
        ),
    )


# ---------------------------------------------------------------------------
# Validator — strict, blocks on any issue
# ---------------------------------------------------------------------------

def _validate_plan(
    plan: dict[str, Any],
    available_domains: set[str],
    log: logging.Logger,
) -> ValidationResult:
    """
    Validate the parsed plan strictly.

    Checks performed:
    1.  Top-level keys present and correctly typed.
    2.  If can_answer_directly=False, at least one task must exist.
    3.  Every task is a dict and has task_id, description, domains (non-empty list).
    4.  Every domain in every task exists in available_domains.
    5.  depends_on references only task_ids that exist earlier in the list
        (no forward references, no self-references, no unknown ids).
    6.  No duplicate task_ids.
    7.  complex tasks must have batch_strategy with required keys.
    8.  multi_step tasks must have sub_steps (non-empty list of strings).
    9.  Dependency graph is acyclic (no circular depends_on chains).

    Note: trailing-garbage detection is intentionally omitted here.
    _parse_plan uses raw_decode which already discards everything after the
    first valid JSON object, so the validator never sees the raw tail.
    """
    issues: list[str] = []

    # 1. Top-level structure
    if not isinstance(plan.get("can_answer_directly"), bool):
        issues.append(
            "'can_answer_directly' must be a boolean; "
            f"got {type(plan.get('can_answer_directly')).__name__!r}"
        )
    if not isinstance(plan.get("tasks"), list):
        issues.append("'tasks' must be a list")
        return ValidationResult(ok=False, issues=issues)

    # 2. Tasks required when not answering directly
    if not plan.get("can_answer_directly") and not plan["tasks"]:
        issues.append(
            "can_answer_directly=false but 'tasks' is empty — "
            "at least one task is required"
        )

    tasks: list = plan["tasks"]
    seen_ids: dict[str, int] = {}  # task_id → index

    for idx, task in enumerate(tasks):
        prefix = f"tasks[{idx}]"

        # FIX 4: guard against non-dict entries (e.g. model put a string in tasks[])
        if not isinstance(task, dict):
            issues.append(
                f"{prefix}: expected a dict, got {type(task).__name__!r} — "
                "every task must be a JSON object"
            )
            continue

        # 3a. task_id
        task_id = task.get("task_id")
        if not task_id or not isinstance(task_id, str):
            issues.append(f"{prefix}: missing or non-string 'task_id'")
            task_id = f"<unknown_{idx}>"

        # 3b. description
        if not task.get("description") or not isinstance(task["description"], str):
            issues.append(f"{prefix} ({task_id}): missing or empty 'description'")

        # 3c. domains
        domains = task.get("domains")
        if not domains or not isinstance(domains, list):
            issues.append(
                f"{prefix} ({task_id}): 'domains' must be a non-empty list"
            )
            domains = []

        # 4. Domain existence
        for d in domains:
            if d not in available_domains:
                issues.append(
                    f"{prefix} ({task_id}): domain '{d}' is not available. "
                    f"Available domains: {sorted(available_domains)}"
                )

        # 5. depends_on — only backward references allowed
        depends_on = task.get("depends_on", [])
        if not isinstance(depends_on, list):
            issues.append(f"{prefix} ({task_id}): 'depends_on' must be a list")
            depends_on = []
        for dep in depends_on:
            if dep == task_id:
                issues.append(
                    f"{prefix} ({task_id}): task cannot depend on itself"
                )
            elif dep not in seen_ids:
                issues.append(
                    f"{prefix} ({task_id}): depends_on '{dep}' which is either "
                    "unknown or appears later in the task list (forward reference). "
                    "Reorder tasks so dependencies come first."
                )

        # 6. Duplicate task_ids
        if task_id in seen_ids:
            issues.append(
                f"{prefix}: duplicate task_id '{task_id}' "
                f"(first seen at tasks[{seen_ids[task_id]}])"
            )
        else:
            seen_ids[task_id] = idx

        # 7. complex + batch_strategy
        if task.get("complexity") == "complex":
            bs = task.get("batch_strategy")
            if not bs or not isinstance(bs, dict):
                issues.append(
                    f"{prefix} ({task_id}): complexity='complex' requires a "
                    "'batch_strategy' dict"
                )
            else:
                for key in ("page_size", "max_pages", "scope_query"):
                    if key not in bs:
                        issues.append(
                            f"{prefix} ({task_id}): batch_strategy missing '{key}'"
                        )

        # 8. multi_step + sub_steps
        if task.get("multi_step"):
            sub_steps = task.get("sub_steps")
            if not sub_steps or not isinstance(sub_steps, list):
                issues.append(
                    f"{prefix} ({task_id}): multi_step=true requires a non-empty "
                    "'sub_steps' list"
                )
            elif not all(isinstance(s, str) for s in sub_steps):
                issues.append(
                    f"{prefix} ({task_id}): all sub_steps must be strings"
                )

    # 9. Cycle detection (DFS) — only run when graph is otherwise coherent
    if not issues:
        id_to_deps = {
            t.get("task_id", ""): t.get("depends_on", [])
            for t in tasks
            if isinstance(t, dict)
        }
        cycle = _find_cycle(id_to_deps)
        if cycle:
            issues.append(
                f"Circular dependency detected: {' → '.join(cycle)}. "
                "Reorder or restructure tasks to remove the cycle."
            )

    ok = len(issues) == 0
    if not ok:
        log.warning(
            "Plan validation found %d issue(s):\n%s",
            len(issues),
            "\n".join(f"  [{i+1}] {issue}" for i, issue in enumerate(issues)),
        )
    return ValidationResult(ok=ok, issues=issues)


def _find_cycle(id_to_deps: dict[str, list[str]]) -> list[str] | None:
    """DFS cycle detection. Returns the cycle path or None."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in id_to_deps}
    path: list[str] = []

    def dfs(node: str) -> bool:
        color[node] = GRAY
        path.append(node)
        for dep in id_to_deps.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GRAY:
                path.append(dep)
                return True
            if color[dep] == WHITE and dfs(dep):
                return True
        path.pop()
        color[node] = BLACK
        return False

    for tid in list(id_to_deps):
        if color[tid] == WHITE:
            if dfs(tid):
                return path
    return None


# ---------------------------------------------------------------------------
# Reflection prompt builders
# ---------------------------------------------------------------------------

_PARSE_REFLECTION_TEMPLATE = """\
Your previous response could not be parsed as JSON. Before you retry, \
re-read the original user query and system instructions carefully and \
think through a fresh plan from scratch — do not simply patch the broken output.

## Parse error
{error}

## What you returned (first 600 characters) — for reference only
{raw}

## What went wrong (JSON format)
Your response contained content AFTER the closing `}}` of the JSON object, \
which broke the parser. This is likely one of:
- Tool invocation lines like {{"tool": "...", "params": {{...}}}}
- Execution status strings like "running"
- Hallucinated tool results

## How to retry
1. Re-read the user query and think: what is really being asked? What is the \
right set of tasks to answer it fully?
2. Write a fresh, logically sound plan — do not copy-paste the broken output.
3. Emit ONLY a single valid JSON object. After the closing `}}` output \
NOTHING — no tool calls, no status strings, no explanations.

Retry now. Output only the corrected JSON object."""


_PLAN_REFLECTION_TEMPLATE = """\
Your previous response was valid JSON but the plan has {issue_count} \
validation error(s) AND may not fully address the user's query. \
Before fixing the errors, step back and re-examine your reasoning.

## Re-examine your plan
Ask yourself:
- Does my plan actually answer the user's full query, or did I miss any aspect?
- Are the tasks in the right order? Do dependencies make logical sense?
- Am I using the right domains for each task? Available domains: {available_domains}
- Is the complexity / batch_strategy appropriate for the data volume expected?
- Could I simplify or merge tasks without losing coverage?

## Previous plan (for reference — do not copy blindly)
{plan_json}

## Validation errors that MUST be fixed
{issues_list}

## Rules reminder
- Every domain in 'domains' must be one of: {available_domains}
- 'depends_on' may only reference task_ids that appear EARLIER in the tasks \
list — no forward references, no self-references
- Duplicate task_ids are not allowed
- complexity='complex' requires a batch_strategy with page_size, max_pages, \
and scope_query
- multi_step=true requires a non-empty sub_steps list of strings
- can_answer_directly=false requires at least one task
- Every entry in 'tasks' must be a JSON object (dict), not a string or array

## How to retry
1. Re-read the original user query and the full system prompt.
2. Reconsider the plan from first principles — fix the errors above AND \
improve the reasoning if needed.
3. Output ONLY the corrected JSON object.

Retry now. Output only the corrected JSON object."""


def _append_parse_reflection(
    messages: list,
    raw: str,
    error: str,
) -> list:
    """
    Append a corrective turn for a parse failure.

    The model's bad output is injected as an AIMessage so it sees exactly
    what it produced, followed by a HumanMessage explaining the error and
    asking for fresh re-reasoning, not just a syntax patch.
    """
    corrective_human = _PARSE_REFLECTION_TEMPLATE.format(
        raw=raw[:600],
        error=error,
    )
    return [
        *messages,
        AIMessage(content=raw),
        HumanMessage(content=corrective_human),
    ]


def _append_plan_reflection(
    messages: list,
    plan: dict[str, Any],
    issues: list[str],
    available_domains: set[str],          # FIX 1: required, not optional
) -> list:
    """
    Append a corrective turn for a plan validation failure.

    Leads with a structured self-examination checklist so the model
    improves its reasoning and query coverage — not just the syntax.
    Then shows every validation error and the real available domain list.
    """
    issues_list = "\n".join(f"  [{i+1}] {issue}" for i, issue in enumerate(issues))
    plan_json = json.dumps(plan, indent=2)[:1500]
    domains_str = str(sorted(available_domains))   # FIX 1: always the real list

    corrective_human = _PLAN_REFLECTION_TEMPLATE.format(
        issue_count=len(issues),
        plan_json=plan_json,
        issues_list=issues_list,
        available_domains=domains_str,
    )
    return [
        *messages,
        AIMessage(content=json.dumps(plan)),
        HumanMessage(content=corrective_human),
    ]