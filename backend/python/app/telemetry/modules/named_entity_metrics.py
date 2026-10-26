"""Named-entity extraction metrics.

Labels are limited to kind, strategy, termination_reason and outcome so the
series count stays bounded per org. No surface text, names or values.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from app.modules.named_entities.domain.models import NamedEntityExtraction

ENTITIES = METRICS_BACKEND.counter(
    "pipeshub_named_entities_total",
    "Named entities by kind and outcome",
    ["kind", "outcome"],
)

RUNS = METRICS_BACKEND.counter(
    "pipeshub_named_entity_runs_total",
    "Extraction runs by strategy, termination reason and persist outcome",
    ["strategy", "termination_reason", "outcome"],
)

FALLBACKS = METRICS_BACKEND.counter(
    "pipeshub_named_entity_fallbacks_total",
    "Agent runs that fell back to another strategy",
    ["strategy", "termination_reason"],
)

GROUNDING = METRICS_BACKEND.counter(
    "pipeshub_named_entity_grounding_total",
    "Persisted mentions by grounding status",
    ["outcome"],
)

STEP_DOWNS = METRICS_BACKEND.counter(
    "pipeshub_named_entity_schema_step_downs_total",
    "Structured-output step-downs, by the mode stepped down to",
    ["outcome"],
)

STAGE_SECONDS = METRICS_BACKEND.histogram(
    "pipeshub_named_entity_stage_seconds",
    "Wall-clock time of the named-entity persist stage",
    ["strategy"],
    buckets=(0.05, 0.25, 0.5, 1, 2, 5, 15, 30, 60, 120),
)

EXTRACT_SECONDS = METRICS_BACKEND.histogram(
    "pipeshub_named_entity_extract_seconds",
    "Wall-clock time of extraction, the part that holds the index permit with classify",
    ["strategy"],
    buckets=(0.05, 0.25, 0.5, 1, 2, 5, 15, 30, 60, 120),
)

TURNS = METRICS_BACKEND.histogram(
    "pipeshub_named_entity_agent_turns",
    "Agent turns per record",
    ["strategy"],
    buckets=(0, 1, 2, 3, 4, 6, 8, 12),
)

TOOL_CALLS = METRICS_BACKEND.histogram(
    "pipeshub_named_entity_agent_tool_calls",
    "Agent tool calls per record",
    ["strategy"],
    buckets=(0, 1, 2, 4, 8, 12, 16, 24, 32),
)

TOKENS = METRICS_BACKEND.histogram(
    "pipeshub_named_entity_tokens",
    "Model tokens per record, by direction in the outcome label",
    ["strategy", "outcome"],
    buckets=(0, 500, 1000, 2500, 5000, 10000, 20000, 40000, 60000),
)


def record_run(extraction: NamedEntityExtraction, *, outcome: str) -> None:
    strategy = extraction.strategy or "none"
    RUNS.inc(strategy, extraction.termination_reason or "none", outcome)
    if outcome == "written":
        for entity in extraction.entities:
            ENTITIES.inc(entity.kind.value, "accepted")
            for mention in entity.mentions:
                GROUNDING.inc(mention.grounding or "none")
    stats = extraction.stats
    if stats.dropped_cap:
        ENTITIES.inc("all", "dropped_cap", value=float(stats.dropped_cap))
    if stats.rejected:
        ENTITIES.inc("all", "rejected", value=float(stats.rejected))
    if strategy in {"agent", "single_call"}:
        TURNS.observe(strategy, value=float(stats.turns))
        TOOL_CALLS.observe(strategy, value=float(stats.tool_calls))
        TOKENS.observe(strategy, "input", value=float(stats.tokens_in))
        TOKENS.observe(strategy, "output", value=float(stats.tokens_out))


def record_fallback(to_strategy: str, reason: str) -> None:
    FALLBACKS.inc(to_strategy or "none", reason or "none")


def record_step_down(mode: str) -> None:
    STEP_DOWNS.inc(mode)


def observe_stage(strategy: str, seconds: float) -> None:
    STAGE_SECONDS.observe(strategy or "none", value=seconds)


def observe_extract(strategy: str, seconds: float) -> None:
    EXTRACT_SECONDS.observe(strategy or "none", value=seconds)


SWEEP = METRICS_BACKEND.counter(
    "pipeshub_named_entity_sweep_total",
    "Orphan sweep results: marked, cleared, deleted, relinked, dangling_removed, dangling_failed, values_removed, vector_failed",
    ["outcome"],
)


def record_sweep(outcome: str, count: int = 1) -> None:
    if count:
        SWEEP.inc(outcome, value=float(count))
