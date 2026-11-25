"""C1: every schema validates its examples and rejects its invalid fixtures (JSON Schema + pydantic).

The zod half of C1 is ``tests/modules/agent-harness/contracts/schemas.test.ts``.
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.canonical_json import canonical_json
from harness_core.contracts.generated.event_catalog import EVENT_MODELS, EVENT_WRITERS

from .support import CONTRACTS, all_schema_files, instance_schemas, validator

SCHEMAS = instance_schemas()

# REPORT 6.1.2 event catalog, with its writer column.
EXPECTED_CORE_WRITERS = {
    **{t: {"node"} for t in [
        "run.admitted", "run.claimed", "run.spec_pinned", "run.suspended", "run.resumed", "run.yielded",
        "run.cancel_requested", "run.completed", "run.failed", "run.cancelled", "run.quarantined",
        "input.queued", "interaction.resolved", "child.spawned", "child.completed",
        "session.imported", "session.snapshot", "session.participant_changed", "session.forked", "session.erased",
        # CLEANUP-G2: a shared session moved to an admin when its owner was deleted.
        "session.owner_changed",
        "gateway.refused",
        # OB-03: a destination the egress proxy let a run reach.
        "egress.allowed",
        # C6.12: an unusual egress volume of a run (SEC-EG-14).
        "egress.volume_alert",
        # WP-103: a credential shape a gateway hid from a run's model call.
        "gateway.secret_hidden",
        # UX1: a person's "allow for this conversation".
        "interaction.granted",
        # ADR-GRANT-01 D8 (UI-B1 G5): the owner took it back.
        "interaction.grant_revoked",
        # ADR-GRANT-01 D9 (R38-AUTHZ): a person's decline that holds for their runs in the conversation.
        "interaction.declined",
    ]},
    **{t: {"worker"} for t in [
        "input.consumed", "turn.started", "turn.completed", "model.request", "model.response",
        "message.completed", "reasoning.completed", "tool.intent", "tool.denied",
        "tool.unknown", "tool.reconciled", "interaction.requested", "workspace.committed",
        "context.compacted", "context.cleared", "task.created", "task.updated", "usage.recorded",
        "loop.state_saved",
        # G-05 (GA-04): what the run's verify gate found when it said it was done.
        "verification.recorded",
        # E1: an engine's saved state (ARCHITECTURE §4.8); P5.9b: its sandbox snapshot.
        "engine.checkpointed",
        "engine.snapshotted",
        # UX1: why the worker asks a person.
        "interaction.explained",
    ]},
    "label.declassified": {"node", "worker"},
    # EV-06: Node closes a call a run left open when it ends the run itself.
    "tool.outcome": {"node", "worker"},
    # G-48 (WP-100): the kernel moves a loop run; Node journals the Model Gateway's move of an engine run's call.
    "model.fallback": {"node", "worker"},
}


def test_every_schema_is_valid_2020_12() -> None:
    files = all_schema_files()
    assert len(files) >= 40
    for path in files:
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema", path
        Draft202012Validator.check_schema(schema)


_JS_SAFE = 2**53 - 1


def unbounded_numbers(node: object, at: str = "#") -> list[str]:
    """Pointers to numeric schemas that admit a value JavaScript and canonical JSON cannot carry exactly.

    ``number`` counts too: a whole-number value parses as an ``int`` in Python.
    """
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in unbounded_numbers(v, f"{at}/{i}")]
    if not isinstance(node, dict):
        return []
    found = [p for k, v in node.items() for p in unbounded_numbers(v, f"{at}/{k}")]
    types = node.get("type")
    numeric = [t for t in (types if isinstance(types, list) else [types]) if t in ("integer", "number")]
    if not numeric:
        return found
    if "const" in node or "enum" in node:
        listed = [node["const"]] if "const" in node else node["enum"]
        safe = all(not isinstance(v, int) or abs(v) <= _JS_SAFE for v in listed)
    else:
        low = node.get("minimum", node.get("exclusiveMinimum"))
        high = node.get("maximum", node.get("exclusiveMaximum"))
        safe = low is not None and high is not None and -_JS_SAFE <= low and high <= _JS_SAFE
    return found if safe else [at, *found]


def test_every_number_fits_javascript() -> None:
    # Unbounded numbers made C-4 draw a session.erased count above 2^53 (FLK1); bound them as UInt and Usd are.
    offenders = [f"{path.relative_to(CONTRACTS.parent)} {ptr}" for path in all_schema_files()
                 for ptr in unbounded_numbers(json.loads(path.read_text(encoding="utf-8")))]
    assert offenders == []


@pytest.mark.parametrize(
    ("schema", "flagged"),
    [
        ({"type": "integer", "minimum": 0}, True),
        ({"type": ["integer", "null"], "minimum": 0, "maximum": 2**53}, True),
        ({"type": "object", "additionalProperties": {"type": "integer", "minimum": 0}}, True),
        ({"type": "integer", "enum": [2**53]}, True),
        ({"type": "number", "minimum": 0}, True),
        ({"type": "number", "minimum": 0, "maximum": 1e9}, False),
        ({"type": "integer", "minimum": -_JS_SAFE, "maximum": _JS_SAFE}, False),
        ({"type": "integer", "exclusiveMinimum": -1, "exclusiveMaximum": 10}, False),
        ({"type": "integer", "enum": [2]}, False),
        ({"$ref": "common.schema.json#/$defs/UInt"}, False),
    ],
)
def test_unbounded_numbers_finds_values_beyond_the_safe_range(schema: dict, flagged: bool) -> None:
    assert bool(unbounded_numbers(schema)) is flagged


def test_every_instance_schema_has_examples_and_invalid_fixtures() -> None:
    for schema in SCHEMAS:
        assert schema.examples, schema
        assert schema.invalid, schema


@pytest.mark.parametrize("schema", SCHEMAS, ids=str)
def test_examples_are_valid(schema) -> None:
    v = validator(schema)
    model = schema.model()
    for example in schema.examples:
        errors = [e.message for e in v.iter_errors(example)]
        assert errors == [], errors
        parsed = validate_contract(model, example)
        assert canonical_json(dump_contract(parsed)) == canonical_json(example)


@pytest.mark.parametrize("schema", SCHEMAS, ids=str)
def test_invalid_fixtures_are_rejected(schema) -> None:
    v = validator(schema)
    model = schema.model()
    for case in schema.invalid:
        assert not v.is_valid(case["instance"]), f"JSON Schema accepted: {case['description']}"
        with pytest.raises(ValidationError):
            validate_contract(model, case["instance"])
            pytest.fail(f"pydantic accepted: {case['description']}")


def test_event_catalog_matches_report_writer_column() -> None:
    assert dict(EVENT_WRITERS) == EXPECTED_CORE_WRITERS
    assert set(EVENT_MODELS) == set(EXPECTED_CORE_WRITERS)


def test_writer_annotation_matches_writer_constraint() -> None:
    for schema in SCHEMAS:
        if not schema.is_event:
            continue
        writer = schema.schema["properties"]["writer"]
        allowed = [writer["const"]] if "const" in writer else writer["enum"]
        assert sorted(allowed) == sorted(schema.schema["x-writer"]), schema


def _overlay_writers(group: str) -> dict[str, set[str]]:
    """The event types an edition's overlay adds to ``group``'s catalog (``contracts/harness/ee/<group>/``)."""
    found: dict[str, set[str]] = {}
    for path in sorted((CONTRACTS / "ee" / group).glob("**/*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        if "x-writer" in schema:
            found[schema["properties"]["type"]["const"]] = set(schema["x-writer"])
    return found


def test_ext_event_catalog() -> None:
    from app.harness.contracts.generated.pipeshub.event_catalog import (
        EVENT_WRITERS as EXT_WRITERS,
    )

    assert dict(EXT_WRITERS) == _overlay_writers("ext/pipeshub") | {
        "x.pipeshub.citation.recorded": {"worker"},
        "x.pipeshub.citations.recorded": {"worker"},
        # G-35: a knowledge export's reserve and settlement.
        "x.pipeshub.knowledge.exported": {"worker"},
        # WP-63 part A: a public repository the worker fetched for an engine run.
        "x.pipeshub.repository.fetched": {"worker"},
        # WP-63: a clone refused because its host is not on the org's list (the "Clone not available" card).
        "x.pipeshub.repository.unavailable": {"worker"},
        # WP-96 (ADR-RUN-01 D3): each tool result's provenance, the session's provenance ledger.
        "x.pipeshub.provenance.recorded": {"worker"},
        # One approval of an ask that needs several approvals.
        "x.pipeshub.approval.counted": {"node"},
        "x.pipeshub.artifact.publish_intent": {"worker"},
        "x.pipeshub.artifact.published": {"worker"},
        "x.pipeshub.artifact.presented": {"worker"},
        "x.pipeshub.answer.finalized": {"worker"},
        "x.pipeshub.goal.presented": {"worker"},
        "x.pipeshub.context.compacted": {"worker"},
        "x.pipeshub.message.presented": {"worker"},
        "x.pipeshub.question.asked": {"worker"},
        "x.pipeshub.tool.call_presented": {"worker"},
        "x.pipeshub.tool.result_presented": {"worker"},
        "x.pipeshub.tool.unavailable": {"worker"},
        "x.pipeshub.engine.progress": {"worker"},
        "x.pipeshub.memory.recalled": {"worker"},
        "x.pipeshub.history.labelled": {"node"},
        "x.pipeshub.run.notice": {"node", "worker"},
        "x.pipeshub.run.handoff": {"worker"},
        "x.pipeshub.command.expanded": {"node"},
        "x.pipeshub.command.finished": {"worker"},
        "x.pipeshub.telemetry.recorded": {"worker"},
        # C2.39, C2.40: what an engine sent natively, sealed and never shown.
        "x.pipeshub.native.unknown": {"worker"},
        "x.pipeshub.native.transcript": {"worker"},
        # ADR-RUN-01 D1: the run's outcome ledger facts and its one settled outcome.
        "x.pipeshub.outcome.recorded": {"worker"},
        "x.pipeshub.outcome.settled": {"worker"},
    }


def test_pipeshub_extension_validates_inside_a_core_run_spec() -> None:
    from app.harness.contracts.generated.pipeshub.run_spec import (
        PipeshubRunSpecExtension,
    )
    from harness_core.contracts.generated.run_spec import RunSpec

    core = json.loads((CONTRACTS / "core" / "run-spec.schema.json").read_text(encoding="utf-8"))
    ext = json.loads((CONTRACTS / "ext" / "pipeshub" / "run-spec.schema.json").read_text(encoding="utf-8"))
    spec = {**core["examples"][0], "extensions": {"x.pipeshub": ext["examples"][0]}}
    parsed = validate_contract(RunSpec, spec)
    assert parsed.extensions is not None
    validate_contract(PipeshubRunSpecExtension, parsed.extensions["x.pipeshub"])
