"""CPD6-14, CPD6-15, CPD10-8: the deployment's flow rule and the Labs stages read the same in Node and Python.

Node pins a run from its own reading of the three flow settings and of the Labs document; the worker's readers
must give the same answer for the same input, or the pin would differ from what a run without one reads.
``contracts/harness/vectors/flow-settings.json`` and ``labs-stages.json`` hold both (Node:
``deployment-policy.test.ts``, ``labs-stages.test.ts``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from app.agents.mcp import service as mcp_service
from app.control_plane.runtime.deployment import deployment_flow_rule
from app.services.featureflag import platform_settings
from app.utils.env_flags import HARNESS_FLOW_GUARD, InvalidEnvFlag
from app.utils.env_utils import HIGH_ACCURACY_MODE_ENV_VAR
from harness_core.effects.flow import FlowRule
from harness_core.logs import HarnessLog
from tests.support.config_service import FakeConfigurationService

VECTORS = Path(__file__).resolve().parents[5] / "contracts/harness/vectors"
FLOW = json.loads((VECTORS / "flow-settings.json").read_text(encoding="utf-8"))["cases"]
LABS = json.loads((VECTORS / "labs-stages.json").read_text(encoding="utf-8"))
SETTINGS = ("HARNESS_FLOW_GUARD", "HARNESS_FLOW_GUARD_LEVEL", "HARNESS_FLOW_GUARD_ABOVE")
READERS = {
    "ENABLE_SKILLS": platform_settings.is_skills_enabled,
    "ENABLE_USER_CONTEXT": platform_settings.is_user_context_enabled,
    "ENABLE_KNOWLEDGE_PUBLICATIONS": platform_settings.is_knowledge_publications_enabled,
    "ENABLE_ACTIONS": platform_settings.is_actions_enabled,
    "ENABLE_MCP": mcp_service.is_mcp_enabled,
    "ENABLE_NAMED_ENTITY_EXTRACTION": platform_settings.is_named_entity_extraction_enabled,
}


@pytest.mark.parametrize("case", FLOW, ids=[c["id"] for c in FLOW])
def test_cpd6_14_the_flow_settings_give_the_vectors_rule(
    case: dict[str, Any], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    for name, value in case["env"].items():
        monkeypatch.setenv(name, value)

    if case.get("refusesToStart"):
        with pytest.raises(InvalidEnvFlag) as refused:
            _ = deployment_flow_rule(HarnessLog.named("tests.cpd6.flow"))
        assert refused.value.name == HARNESS_FLOW_GUARD
        return
    with caplog.at_level(logging.WARNING):
        rule = deployment_flow_rule(HarnessLog.named("tests.cpd6.flow"))

    assert rule == (None if case["rule"] is None else FlowRule(case["rule"]["level"], case["rule"]["above"]))
    invalid = [name for name in ("level", "above") if f"setting=HARNESS_FLOW_GUARD_{name.upper()}" in caplog.text]
    assert invalid == case["invalid"]


def test_cpd6_15_the_vector_names_every_stage_the_worker_reads_from_a_pin() -> None:
    assert set(LABS["stages"]) == set(READERS)


@pytest.mark.parametrize("case", LABS["cases"], ids=[c["id"] for c in LABS["cases"]])
async def test_cpd6_15_the_labs_document_gives_the_vectors_stages(
    case: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # HIGH_ACCURACY_MODE turns named-entity extraction on without reading the document the vector is about.
    monkeypatch.delenv(HIGH_ACCURACY_MODE_ENV_VAR, raising=False)
    config = FakeConfigurationService()
    config.put(platform_settings.PLATFORM_SETTINGS_KEY, case["settings"])

    assert {flag: await reader(config) for flag, reader in READERS.items()} == case["worker"]  # type: ignore[arg-type]


async def test_cpd10_8_a_pinned_stage_answers_every_reader_whatever_the_document_says() -> None:
    config = FakeConfigurationService()
    config.put(platform_settings.PLATFORM_SETTINGS_KEY, {"featureFlags": {"ENABLE_MCP": False, "ENABLE_ACTIONS": True}})
    platform_settings.bind_pinned_stages({"ENABLE_MCP": True, "ENABLE_ACTIONS": False})
    try:
        assert await mcp_service.is_mcp_enabled(config) is True  # type: ignore[arg-type]
        assert await platform_settings.is_actions_enabled(config) is False  # type: ignore[arg-type]
    finally:
        platform_settings.bind_pinned_stages(None)
    assert await mcp_service.is_mcp_enabled(config) is False  # type: ignore[arg-type]
