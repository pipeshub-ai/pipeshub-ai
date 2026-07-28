"""
Sufficiency judge for record escalation.

IFetchJudge: the Protocol that tests stub out.
LLMFetchJudge: the production implementation — one metadata-only structured
    call per request, via run_structured_single_shot, asking the model whether
    answering correctly REQUIRES fetching any of the candidate records in full.

Key design constraint: the judge is handed the draft answer (not just the
query), which turns this into a sufficiency judgment — a relevance-only judge
would say "fetch" whenever a partial-but-relevant record exists, even when the
draft already contains a complete answer.

Any exception, timeout, or unparseable output returns FetchVerdict(needed=(),
judged=False), failing open toward *not* fetching.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent.single_shot_runner import (
    StructuredSingleShotError,
    build_task_complete_runtime,
    run_structured_single_shot,
)
from app.agent_loop_lib.agent.spec import ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.modules.agents.record_escalation.models import FetchPlan, FetchVerdict
from app.modules.agents.record_escalation.renderer import render_candidate_table

if TYPE_CHECKING:
    from typing import runtime_checkable

    from app.agent_loop_lib.transport.registry import TransportRegistry

try:
    from typing import Protocol, runtime_checkable
except ImportError:
    from typing_extensions import Protocol, runtime_checkable  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class IFetchJudge(Protocol):
    """Decides whether the draft answer requires fetching any candidate records."""

    async def judge(
        self,
        *,
        query: str,
        draft_answer: str,
        plan: FetchPlan,
    ) -> FetchVerdict: ...


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM = """\
You are a sufficiency judge deciding whether a draft answer requires fetching \
additional full-document content.

You are given:
1. The user's query.
2. The draft answer that the agent has produced from partial retrieved blocks.
3. A list of records that are only partially retrieved and could be fetched in full.

Your ONLY job: determine whether correctly answering the query requires reading \
any of those records in full — content that is NOT present in the draft answer.

Rules:
- Return {"needed": []} (empty list) when the draft answer already addresses \
the query correctly and completely. This is the most common case.
- Return {"needed": [{"record_id": "...", "reason": "..."}]} ONLY for records \
where the missing content is genuinely required to answer the query correctly.
- Do NOT recommend fetching records that are merely topically related — only \
records whose missing sections would materially change the answer.
- Be conservative: unnecessary fetches waste resources.

Return ONLY a JSON object with a "needed" key containing an array (possibly empty).\
"""

_OUTPUT_SCHEMA_HINT = (
    '\n\nYour JSON output must be exactly:\n'
    '{"needed": [{"record_id": "<id>", "reason": "<why missing content is needed>"}]}\n'
    'or {"needed": []} if the draft answer is already sufficient.'
)


# ---------------------------------------------------------------------------
# Production implementation
# ---------------------------------------------------------------------------


class LLMFetchJudge:
    """
    One metadata-only structured call on the request's own model.

    Constructed inside _build_hooks from the TransportRegistry and model_name
    — not from AgentContext, which does not carry the transport registry.

    The call uses build_task_complete_runtime + run_structured_single_shot,
    following the same pattern as intent.py:344-357 in this codebase.
    """

    def __init__(
        self,
        transport_registry: "TransportRegistry",
        model_name: str,
        logger_: logging.Logger | None = None,
    ) -> None:
        self._transport_registry = transport_registry
        self._model_name = model_name
        self._logger = logger_ or logger

    async def judge(
        self,
        *,
        query: str,
        draft_answer: str,
        plan: FetchPlan,
    ) -> FetchVerdict:
        """
        Ask the model whether the draft answer requires any of the candidate
        records in full. Returns FetchVerdict(needed=(), judged=False) on any
        failure — the gate must treat that as silence.
        """
        if not plan.has_candidates:
            return FetchVerdict(needed=(), judged=True)

        candidate_text = render_candidate_table(plan)
        goal_text = (
            f"Query: {query}\n\n"
            f"Draft answer:\n{draft_answer}\n\n"
            f"Candidate records (partially retrieved):\n{candidate_text}"
        )

        try:
            runtime = build_task_complete_runtime(self._transport_registry)
            result = await run_structured_single_shot(
                name="fetch-sufficiency-judge",
                system_prompt=_JUDGE_SYSTEM,
                goal=Goal(description=goal_text),
                runtime=runtime,
                model_spec=ModelSpec(provider="langchain", model=self._model_name),
                output_schema_hint=_OUTPUT_SCHEMA_HINT,
            )
            return _parse_verdict(result)
        except StructuredSingleShotError as exc:
            self._logger.warning("FetchJudge: single-shot failed — treating as silence: %s", exc)
            return FetchVerdict(needed=(), judged=False)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("FetchJudge: unexpected error — treating as silence: %s", exc)
            return FetchVerdict(needed=(), judged=False)


def _parse_verdict(raw: dict[str, Any]) -> FetchVerdict:
    """
    Parse the structured output into a FetchVerdict.

    Returns judged=False on any parse error so the gate stays silent.
    """
    try:
        needed_raw = raw.get("needed")
        if not isinstance(needed_raw, list):
            logger.warning("FetchJudge: 'needed' is not a list in %r", raw)
            return FetchVerdict(needed=(), judged=False)

        needed: list[tuple[str, str]] = []
        for item in needed_raw:
            if not isinstance(item, dict):
                continue
            rid = item.get("record_id", "")
            reason = item.get("reason", "")
            if rid:
                needed.append((str(rid), str(reason)))

        return FetchVerdict(needed=tuple(needed), judged=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("FetchJudge: error parsing verdict %r: %s", raw, exc)
        return FetchVerdict(needed=(), judged=False)
