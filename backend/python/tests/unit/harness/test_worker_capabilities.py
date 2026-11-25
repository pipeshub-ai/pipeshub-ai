"""RU-2b: the worker advertises what its build can run, from code constants."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, get_args

from app.harness.worker.capabilities import (
    PIPESHUB_FEATURES,
    WORKER_CAPABILITIES,
    log_capabilities,
)
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver.capabilities import CORE_FEATURES, worker_capabilities
from harness_core.kernel.fold import FOLD_VERSION
from harness_core.logs import HarnessLog
from tests.unit.contracts_harness.support import instance_schemas, validator

if TYPE_CHECKING:
    import pytest


def test_the_advertisement_is_the_builds_own_versions() -> None:
    wire = WORKER_CAPABILITIES.model_dump(mode="json", by_alias=True)
    assert wire == {
        "harnessMajor": 2,
        "supportedSchemaVersions": list(get_args(RunSpec.model_fields["schema_version"].annotation)),
        "foldVersion": FOLD_VERSION,
        "features": sorted({*CORE_FEATURES, *PIPESHUB_FEATURES}),
    }
    assert set(wire["features"]) == {"agent_code", "approval_edits", "assistant_agent", "caller", "chat", "child_asks", "child_models", "child_runs", "code_task_tool", "command_input", "compact_request", "deep_mode", "deliverable_pin", "descriptor_pins", "engine_inputs", "environment_pin", "executor_engine", "governed_memory", "inherited_grants", "inherited_labels", "keyword_search_budget", "knowledge_selection", "mcp_carried_pins", "mcp_first_use", "model_fallback", "output_locale", "region_aware", "release_pin", "resolved_config", "run_gates", "run_surface", "sandbox_command", "session_roles", "steering", "steering_by", "steering_references", "trace_content", "trigger_context", "turn_labels", "vendor_signin", "workspace_forks"}


def test_the_advertisement_is_a_valid_contract_instance() -> None:
    (schema,) = [s for s in instance_schemas() if s.path.name == "worker-capabilities.schema.json"]
    wire = WORKER_CAPABILITIES.model_dump(mode="json", by_alias=True)
    assert list(validator(schema).iter_errors(wire)) == []


def test_the_core_does_not_know_the_apps_shapes() -> None:
    core = worker_capabilities()
    assert [f.root for f in core.features] == ["approval_edits", "child_runs", "steering", "steering_by", "steering_references"]


def test_worker_capabilities_is_logged_once_at_start_without_content(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    log_capabilities(HarnessLog.named("app.harness.worker", workerId="w1", role="dispatched"))
    (record,) = [r for r in caplog.records if getattr(r, "harness_event", None) == "worker.capabilities"]
    assert record.harness == {  # type: ignore[attr-defined]
        "workerId": "w1",
        "role": "dispatched",
        "harnessMajor": 2,
        "supportedSchemaVersions": ["2", "3"],
        "foldVersion": FOLD_VERSION,
        "features": ["agent_code", "approval_edits", "assistant_agent", "caller", "chat", "child_asks", "child_models", "child_runs", "code_task_tool", "command_input", "compact_request", "deep_mode", "deliverable_pin", "descriptor_pins", "engine_inputs", "environment_pin", "executor_engine", "governed_memory", "inherited_grants", "inherited_labels", "keyword_search_budget", "knowledge_selection", "mcp_carried_pins", "mcp_first_use", "model_fallback", "output_locale", "region_aware", "release_pin", "resolved_config", "run_gates", "run_surface", "sandbox_command", "session_roles", "steering", "steering_by", "steering_references", "trace_content", "trigger_context", "turn_labels", "vendor_signin", "workspace_forks"],
    }


def test_cc5_a_worker_without_the_engine_kernel_does_not_advertise_engine_runs() -> None:
    from app.harness.worker.capabilities import advertised

    assert "executor_engine" in {f.root for f in advertised(engines=True).features}
    without = {f.root for f in advertised(engines=False).features}
    assert "executor_engine" not in without
    # trace_content: tracing is off in the unit environment (C411-FU-1).
    assert without == {f.root for f in WORKER_CAPABILITIES.features} - {
        "executor_engine", "engine_inputs", "vendor_signin", "trace_content"
    }


def test_qa6_f4_a_process_without_code_execution_does_not_advertise_agent_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """QA6-F4: with SANDBOX_MODE unset, Node pinned `agent-code` (a live worker advertised it) and every durable agent
    turn failed `isolation_unavailable` at claim. Without the feature Node admits the run without `run_code`."""
    from app.harness.worker.capabilities import advertised, code_execution_configured

    monkeypatch.delenv("SANDBOX_MODE", raising=False)
    assert code_execution_configured() is False
    assert "agent_code" not in {f.root for f in advertised(engines=False, code=code_execution_configured()).features}
    monkeypatch.setenv("SANDBOX_MODE", "")
    assert code_execution_configured() is False
    monkeypatch.setenv("SANDBOX_MODE", "broker")
    assert code_execution_configured() is True
    served = {f.root for f in advertised(engines=True, code=code_execution_configured()).features}
    assert {"agent_code", "executor_engine"} <= served
