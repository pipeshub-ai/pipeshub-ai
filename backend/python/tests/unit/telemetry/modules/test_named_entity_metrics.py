"""Unit tests for app.telemetry.modules.named_entity_metrics."""

import re

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import (
    ExtractionStats,
    Mention,
    NamedEntity,
    NamedEntityExtraction,
)
from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules.named_entity_metrics import record_run, record_step_down

_ALLOWED_LABELS = {"kind", "strategy", "termination_reason", "outcome", "le"}
_SURFACE = "Priya Shah"


def _extraction() -> NamedEntityExtraction:
    entity = NamedEntity(
        kind=EntityKind.PERSON,
        display_name=_SURFACE,
        norm_key="priya shah",
        mentions=[Mention(block_index=0, char_start=0, char_end=10, surface=_SURFACE, grounding="fuzzy", extractor="agent")],
    )
    return NamedEntityExtraction(
        strategy="agent",
        termination_reason="budget_turns",
        status="PARTIAL",
        entities=[entity],
        stats=ExtractionStats(turns=3, tool_calls=5, tokens_in=1200, tokens_out=90, rejected=2),
    )


def _series() -> list[str]:
    return [line for line in METRICS_BACKEND.serialize().splitlines() if line.startswith("pipeshub_named_entit")]


def test_run_emits_runs_entities_grounding_and_agent_histograms():
    record_run(_extraction(), outcome="written")
    record_step_down("tool")
    text = "\n".join(_series())
    assert 'pipeshub_named_entity_runs_total{outcome="written",strategy="agent",termination_reason="budget_turns"}' in text
    assert 'pipeshub_named_entities_total{kind="person",outcome="accepted"}' in text
    assert 'pipeshub_named_entity_grounding_total{outcome="fuzzy"}' in text
    assert 'pipeshub_named_entity_schema_step_downs_total{outcome="tool"}' in text
    assert "pipeshub_named_entity_agent_turns_count" in text
    assert 'pipeshub_named_entity_tokens_sum{outcome="input",strategy="agent"}' in text


def _value(prefix: str) -> float:
    return sum(float(line.rsplit(" ", 1)[1]) for line in _series() if line.startswith(prefix))


def test_a_failed_write_counts_the_run_but_no_persisted_entities():
    accepted = 'pipeshub_named_entities_total{kind="person",outcome="accepted"}'
    grounded = "pipeshub_named_entity_grounding_total{"
    failed_runs = 'pipeshub_named_entity_runs_total{outcome="failed"'
    before = (_value(accepted), _value(grounded), _value(failed_runs))
    record_run(_extraction(), outcome="failed")
    assert (_value(accepted), _value(grounded)) == before[:2]
    assert _value(failed_runs) == before[2] + 1


def test_labels_are_low_cardinality_and_carry_no_surface_text():
    record_run(_extraction(), outcome="written")
    for line in _series():
        assert _SURFACE not in line
        for label in re.findall(r'(\w+)="', line):
            assert label in _ALLOWED_LABELS, line
