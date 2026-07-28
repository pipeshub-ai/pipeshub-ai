from __future__ import annotations

import json
import logging
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.responses import StructuredResponse
from app.agent_loop_lib.core.structured_output import (
    coerce_dict,
    coerce_list,
    coerce_optional_str,
)
from app.agent_loop_lib.core.types import AgentResult, UserMessage
from app.agent_loop_lib.modules.providers.skills.base import (
    SkillCandidate,
    SkillMetadata,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.eval.decision_trace import DecisionTraceEntry
    from app.agent_loop_lib.models.base import SupportsStructuredComplete

"""Skill extraction ABC + default LLM-backed implementation — the
"learning" half of the dual-loop architecture (see hooks/middleware/builtin/
skill_learning.py, the POST_AGENT caller). `should_extract` is a cheap,
deterministic pre-filter so the (comparatively expensive) `extract_candidates`
LLM call only ever runs on runs actually worth analyzing.

`LLMSkillExtractor` also runs a single bounded reflection retry (Shinn et
al., 'Reflexion', applied to structured-output self-correction rather than
a tool-calling turn loop): if any candidate entry comes back malformed —
see `core/structured_output.py`'s docstring for why `complete_structured`
can't fully guarantee schema conformance — it re-prompts the model ONCE
with exactly what was wrong, instead of silently dropping the entry. This
only adds a second LLM call on the rare occasion something is actually
malformed; a fully well-formed response costs exactly one call, same as
before.
"""

logger = logging.getLogger(__name__)

MIN_TOOL_CALLS_FOR_EXTRACTION = 3


class SkillExtractor(ABC):
    @abstractmethod
    async def should_extract(self, result: AgentResult) -> bool:
        """Cheap, deterministic gate — no LLM call. Typical checks: was the
        run non-trivial (3+ tool calls), did it succeed, is there anything
        resembling a reusable pattern at all."""

    @abstractmethod
    async def extract_candidates(
        self,
        result: AgentResult,
        trajectory: dict[str, Any] | None = None,
        decision_trace: "list[DecisionTraceEntry] | None" = None,
        existing_catalog: list[SkillMetadata] | None = None,
    ) -> list[SkillCandidate]:
        """Analyze a finished run and propose zero or more reusable skills.
        `trajectory`/`decision_trace` (from eval/trajectory.py and
        eval/decision_trace.py respectively) give richer signal when a
        TimelineStore is wired; implementations must tolerate both being
        None (AgentResult-only input) for setups that don't wire one.
        `existing_catalog`, when given, lets an implementation avoid
        proposing a near-duplicate of a skill that already exists."""


def _tool_sequence(result: AgentResult) -> tuple[str, ...]:
    return tuple(call.name for turn in result.turns for call in turn.tool_calls)


_CANDIDATE_SCHEMA = {
    "type": "object",
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "description": (
                "Zero or more reusable skills worth distilling from this run. "
                "Return an empty array if nothing generalizable was found, or if "
                "the pattern duplicates an existing skill in the catalog."
            ),
            "items": {
                "type": "object",
                "required": ["name", "description", "body", "confidence"],
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Lowercase kebab-case name, e.g. 'deploy-to-kubernetes'.",
                    },
                    "description": {
                        "type": "string",
                        "description": "One-line description of WHEN to use this skill (<=1024 chars).",
                    },
                    "body": {
                        "type": "string",
                        "description": "Step-by-step Markdown instructions generalized beyond this one run.",
                    },
                    "category": {"type": "string", "description": "Optional top-level category, e.g. 'devops'."},
                    "subcategory": {"type": "string", "description": "Optional nested category."},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "confidence": {
                        "type": "number",
                        "description": "0.0-1.0 confidence this is a genuinely reusable, well-formed skill.",
                    },
                },
            },
        },
    },
}

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_EXTRACTION_SYSTEM_PROMPT = (
    "You distill successful agent execution traces into reusable, generalizable "
    "skills. Only propose a skill when the pattern would plausibly recur for "
    "other similar goals — not one-off, task-specific steps."
)


class LLMSkillExtractor(SkillExtractor):
    """Default `SkillExtractor`: one structured LLM call via
    `complete_structured` — the same mechanism `RubricGrader` already uses
    (eval/rubric.py) — instead of spawning a full sub-agent. No tool use,
    no turn loop; just "look at this trajectory and propose skills", plus
    one bounded reflection retry on malformed output (see module docstring)."""

    def __init__(
        self,
        model: "SupportsStructuredComplete",
        *,
        min_tool_calls: int = MIN_TOOL_CALLS_FOR_EXTRACTION,
        max_candidates: int = 3,
        reflect_on_malformed_output: bool = True,
    ) -> None:
        self._model = model
        self._min_tool_calls = min_tool_calls
        self._max_candidates = max_candidates
        self._reflect_on_malformed_output = reflect_on_malformed_output

    async def should_extract(self, result: AgentResult) -> bool:
        if not result.success:
            return False
        sequence = _tool_sequence(result)
        return len(sequence) >= self._min_tool_calls

    async def extract_candidates(
        self,
        result: AgentResult,
        trajectory: dict[str, Any] | None = None,
        decision_trace: "list[DecisionTraceEntry] | None" = None,
        existing_catalog: list[SkillMetadata] | None = None,
    ) -> list[SkillCandidate]:
        prompt = self._build_prompt(result, trajectory, decision_trace, existing_catalog)
        response = await self._complete(prompt)
        if response is None:
            return []

        candidates, errors = self._parse_response(response, result, trajectory)
        if errors and self._reflect_on_malformed_output:
            logger.info(
                "LLMSkillExtractor: %d malformed candidate(s), reflecting and retrying once: %s",
                len(errors), "; ".join(errors),
            )
            retry_response = await self._complete(self._build_reflection_prompt(prompt, response, errors))
            if retry_response is not None:
                candidates, retry_errors = self._parse_response(retry_response, result, trajectory)
                if retry_errors:
                    logger.info(
                        "LLMSkillExtractor: %d candidate(s) still malformed after reflection: %s",
                        len(retry_errors), "; ".join(retry_errors),
                    )
        return candidates

    async def _complete(self, prompt: str) -> "StructuredResponse | None":
        try:
            return await self._model.complete_structured(
                messages=[UserMessage(content=prompt)],
                output_schema=_CANDIDATE_SCHEMA,
                system=_EXTRACTION_SYSTEM_PROMPT,
            )
        except Exception:
            logger.exception("LLMSkillExtractor: extraction call failed")
            return None

    def _parse_response(
        self, response: "StructuredResponse", result: AgentResult, trajectory: dict[str, Any] | None,
    ) -> tuple[list[SkillCandidate], list[str]]:
        """Returns (candidates, errors) — `errors` covers only entries
        skipped OUTRIGHT (not a JSON object, or an unusable name); a field
        we could default our way around (bad confidence, a stray string
        instead of a tags list, ...) still yields a usable candidate and
        is not treated as reflection-worthy."""
        raw_candidates = coerce_list(response.data.get("candidates", [])) if isinstance(response.data, dict) else []
        now = datetime.now(timezone.utc).isoformat()
        candidates: list[SkillCandidate] = []
        errors: list[str] = []
        for idx, raw in enumerate(raw_candidates[: self._max_candidates]):
            candidate = coerce_dict(raw)
            if candidate is None:
                errors.append(f"index {idx}: entry was not a JSON object (got {str(raw)[:120]!r})")
                continue

            name = candidate.get("name", "")
            if not isinstance(name, str) or not _NAME_RE.match(name):
                errors.append(
                    f"index {idx}: 'name' must be lowercase-kebab-case (got {name!r})"
                )
                continue

            tags = candidate.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            elif not isinstance(tags, list):
                tags = []

            try:
                confidence = float(candidate.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5

            try:
                candidates.append(SkillCandidate(
                    candidate_id=str(uuid.uuid4()),
                    name=name,
                    description=str(candidate.get("description", "")),
                    body=str(candidate.get("body", "")),
                    category=coerce_optional_str(candidate.get("category")),
                    subcategory=coerce_optional_str(candidate.get("subcategory")),
                    tags=[str(t) for t in tags],
                    source_session_id=None,
                    source_trajectory_summary=_summarize(result, trajectory),
                    confidence=confidence,
                    created_at=now,
                ))
            except Exception as e:
                errors.append(f"index {idx} ({name!r}): {e}")
        return candidates, errors

    @staticmethod
    def _build_reflection_prompt(original_prompt: str, response: "StructuredResponse", errors: list[str]) -> str:
        error_lines = "\n".join(f"  - {e}" for e in errors)
        return (
            f"{original_prompt}\n\n"
            "---\n"
            "You already answered this once, but your \"candidates\" array had entries that "
            "could not be used:\n"
            f"{error_lines}\n\n"
            f"Your previous response was: {json.dumps(response.data)[:4_000]}\n\n"
            "Return the FULL corrected \"candidates\" array again — every entry must be an "
            "actual JSON object (never a JSON-encoded string), with a valid lowercase-kebab-case "
            "'name'. Keep any entries that were already fine; fix only the flagged ones."
        )

    def _build_prompt(
        self,
        result: AgentResult,
        trajectory: dict[str, Any] | None,
        decision_trace: "list[DecisionTraceEntry] | None",
        existing_catalog: list[SkillMetadata] | None,
    ) -> str:
        parts = [
            f"Goal: {result.goal.description}",
            f"Tool sequence: {' -> '.join(_tool_sequence(result))}",
            f"Output: {str(result.output)[:2_000]}",
        ]
        if trajectory is not None:
            parts.append(f"Trajectory (JSON): {trajectory}")
        if decision_trace:
            reasoning_lines = [
                f"  - {d.tool}: {d.reasoning}" for d in decision_trace if d.reasoning
            ]
            if reasoning_lines:
                parts.append("Stated reasoning per decision:\n" + "\n".join(reasoning_lines))
        if existing_catalog:
            names = ", ".join(f"{m.name} ({m.description})" for m in existing_catalog[:50])
            parts.append(
                f"Existing skill catalog (do NOT propose a near-duplicate of any of these): {names}"
            )
        return "\n\n".join(parts)


def _summarize(result: AgentResult, trajectory: dict[str, Any] | None) -> str:
    if trajectory is not None:
        return f"{len(trajectory.get('steps', []))} steps, final_status={trajectory.get('final_status')}"
    return f"{len(result.turns)} turn(s), goal={result.goal.description!r}"
