"""QA-7 (Python half): what Python reads of a cross-language enumeration is the generated constant.

Node's half is ``tests/modules/agent-harness/contracts/qa7-hand-copies.test.ts`` and the frontend's
``lib/contracts/__tests__/qa7-hand-copies.test.ts``; all three fail when one language keeps its own copy.
"""

from __future__ import annotations

import json
from typing import get_args

from app.harness.assembly import skill_learning
from app.harness.contracts import state
from app.harness.contracts.generated.pipeshub import enums as ext
from app.harness.contracts.generated.pipeshub.errors import ERROR_CODES
from app.harness.worker.capabilities import PIPESHUB_FEATURES, WORKER_CAPABILITIES
from app.harness.workspace import node_store
from harness_core.contracts.generated import enums as core
from harness_core.contracts.generated.run_spec import ModelPolicy
from harness_core.driver.capabilities import CORE_FEATURES
from harness_core.engines import types as engine_types
from harness_core.journal import types as journal_types
from harness_core.journal.children import child_result_document
from harness_core.policy import ENGINE_PROFILE, policy_registry

from .support import CONTRACTS

PROFILE_EFFORTS = set(core.REASONING_EFFORTS)


def _codes(*groups: str) -> set[str]:
    return {code for code, entry in ERROR_CODES.items() if entry.group in groups}


def test_qa7_1_python_advertises_exactly_the_contract_s_worker_features() -> None:
    assert set(PIPESHUB_FEATURES) <= set(ext.KNOWN_WORKER_FEATURES)
    assert {*CORE_FEATURES, *PIPESHUB_FEATURES} == set(ext.KNOWN_WORKER_FEATURES)
    assert {f.root for f in WORKER_CAPABILITIES.features} == set(ext.KNOWN_WORKER_FEATURES)


def test_qa7_2_the_harness_run_reads_the_run_document_s_vocabulary() -> None:
    assert state.RunStatus is ext.ExecutionStatus
    fields = state.HarnessRun.model_fields
    assert get_args(fields["status"].annotation) == ext.EXECUTION_STATUSES
    outcome = {a for arg in get_args(fields["outcome"].annotation) for a in get_args(arg)}
    assert outcome == set(ext.RUN_OUTCOMES)


def test_qa7_4_a_child_result_and_skill_learning_take_every_completed_outcome() -> None:
    assert skill_learning._SUCCEEDED == frozenset(core.COMPLETED_OUTCOMES)
    for outcome in core.COMPLETED_OUTCOMES:
        assert child_result_document(outcome=outcome, output="done")


def test_qa7_5_engine_efforts_are_the_profile_s() -> None:
    assert engine_types.ReasoningEffort is core.ProfileEffort
    # CPD-3: the engine's effort ranks are the dimension table's, lowest first.
    order = policy_registry(ENGINE_PROFILE).get("reasoningEffort").order
    assert order is not None
    for low, high in zip(core.REASONING_EFFORTS, core.REASONING_EFFORTS[1:], strict=False):
        assert order.leq(low, high) is None, "the schema lists efforts lowest first"
        assert order.leq(high, low) is not None
    assert set(core.REASONING_EFFORTS) == PROFILE_EFFORTS


def test_qa7_7_the_workspace_client_maps_the_catalogue_s_worker_workspace_codes() -> None:
    worker_codes = (_codes("workspace") | {"WORKSPACE_CONFLICT"}) - {"WORKSPACE_WRITER_BUSY", "WORKSPACE_PATH_NOT_FOUND", "WORKSPACE_PATH_EXISTS"}
    assert set(node_store._ERRORS) == worker_codes
    assert node_store._LOST <= set(ERROR_CODES)


def test_qa7_8_payload_store_unavailable_is_a_yield_reason() -> None:
    assert "payload_store_unavailable" in core.YIELD_REASONS
    assert get_args(journal_types.YieldReason) == core.YIELD_REASONS


def test_qa7_42_every_profile_effort_is_one_a_run_may_request() -> None:
    schema = json.loads((CONTRACTS / "core" / "run-spec.schema.json").read_text(encoding="utf-8"))
    inline = schema["$defs"]["ModelPolicy"]["properties"]["reasoningEffort"]["enum"]
    assert tuple(inline) == core.REQUESTED_EFFORTS
    assert {a for arg in get_args(ModelPolicy.model_fields["reasoning_effort"].annotation) for a in get_args(arg)} == set(
        core.REQUESTED_EFFORTS
    )
    assert set(core.REASONING_EFFORTS) <= set(core.REQUESTED_EFFORTS)
