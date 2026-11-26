"""C4.10 (WP-68): the run-scoped rows of checklist §4.2 take effect in the worker.

``toolResults.maxChars`` lowers the inline cap on one tool result, the loop's and an engine's (C410-T1..T4);
``toolResults.format`` is a knowledge search's default ``response_format`` inside the formats
``toolResults.formats`` allows (C410-F1..F4); ``interactions.conversationGrants`` off means nothing is offered,
held or inherited for the conversation (C410-G1..G4); ``sandbox.*`` lowers an engine template's resources, never
raises them (C410-S1..S3). A pinned run reads them from its pin; a run without one keeps the release's.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from app.agents.actions.knowledge_graph.ops.search_budget import (
    RESPONSE_FORMAT_POLICY,
    run_response_format,
)
from app.control_plane.contracts.generated.resolved_run_config import ResolvedRunConfig
from app.control_plane.runtime.deployment import DeploymentLayer
from app.harness.labels import inherited_grants
from app.harness.run_policy.pinned import PinnedRunPolicy
from app.harness.run_policy.resolved import RELEASE_TUNING, RunTuning, bind_run_tuning
from harness_core.context.results import (
    RESULT_CAP_CHARS,
    RESULT_CAP_STATE,
    loop_result_cap,
)
from harness_core.contracts import validate_contract
from harness_core.effects.flow import (
    AskDestination,
    ConversationGrants,
    GrantingLedger,
    explained_ask,
)
from harness_core.engines.controller import (
    EngineControllerConfig,
    _SavingEndpoint,
    engine_sandbox_spec,
)
from harness_core.gateway.result_cap import (
    ENGINE_RESULT_CAP_CHARS,
    bound_engine_result,
    endpoint_result_cap,
    run_result_cap,
)
from harness_core.gateway.types import McpResult
from harness_core.logs import HarnessLog
from harness_core.policy import DimensionPolicyResolver, ScopedLayer
from harness_core.sandbox.types import (
    ImageResources,
    MountSpec,
    ResourceCaps,
    Resources,
    RuntimeMode,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from app.harness.assembly.context import RunContext
    from harness_core.effects.types import ToolEffect
    from harness_core.journal.types import JournalEvent


def _pin(layers: list[ScopedLayer]) -> ResolvedRunConfig:
    from app.control_plane.runtime.pinning import release_layer

    resolved = DimensionPolicyResolver().resolve([release_layer(), *layers])
    assert resolved.config is not None, resolved.refusal
    stored: dict[str, JsonValue] = {
        "schemaVersion": 1,
        "headRevision": 1,
        "routing": {"path": "durable", "reason": None},
        "scope": {"agentKey": "agent-1", "projectId": None},
        "effectiveSet": {"items": [], "digest": "sha256:" + "0" * 64},
        "effectiveConfig": resolved.config,
        "llm": None,
        "deploymentDigest": "sha256:" + "0" * 64,
        "digest": "sha256:" + "0" * 64,
    }
    return validate_contract(ResolvedRunConfig, stored)


def _tuning(*layers: ScopedLayer) -> RunTuning:
    run = cast("RunContext", SimpleNamespace(org_id="o1", agent=None))
    policy = PinnedRunPolicy(run, _pin(list(layers)), deployment=DeploymentLayer(), live=None, log=HarnessLog.named("t"))
    return policy.tuning()


def test_c410_release_tuning_changes_nothing() -> None:
    assert _tuning() == RELEASE_TUNING
    assert RELEASE_TUNING.conversation_grants is True
    assert RELEASE_TUNING.result_cap_chars is None


# --- toolResults.maxChars ---------------------------------------------------------------------------------------


def test_c410_t1_the_pin_carries_the_narrowest_cap() -> None:
    tuning = _tuning(
        ScopedLayer("org", {"toolResults": {"maxChars": 20_000}}),
        ScopedLayer("agent", {"toolResults": {"maxChars": 8_000}}),
    )
    assert tuning.result_cap_chars == 8_000


def test_c410_t2_an_engines_cap_is_lowered_never_raised() -> None:
    assert run_result_cap(None) == ENGINE_RESULT_CAP_CHARS
    assert run_result_cap(8_000) == 8_000
    assert run_result_cap(500_000) == ENGINE_RESULT_CAP_CHARS
    endpoint = _SavingEndpoint(cast("object", None), max_result_chars=run_result_cap(8_000))  # type: ignore[arg-type]
    assert endpoint_result_cap(endpoint) == 8_000
    assert endpoint_result_cap(object()) == ENGINE_RESULT_CAP_CHARS


async def test_c410_t3_the_gateway_cuts_a_result_at_the_runs_cap() -> None:
    endpoint = _SavingEndpoint(cast("object", None), max_result_chars=run_result_cap(5_000))  # type: ignore[arg-type]
    result = McpResult(text="x" * 10_000, is_error=False, disposition="executed", retryable=False)
    bounded = await bound_engine_result(
        endpoint, result, tool="jira__search", arguments={}, max_chars=endpoint_result_cap(endpoint)
    )
    assert len(bounded.text) < 10_000
    untouched = await bound_engine_result(object(), result, tool="jira__search", arguments={})
    assert untouched.text == result.text


def test_c410_t4_the_loops_cap_reads_the_runs_tool_state() -> None:
    state: dict[str, object] = {}
    assert loop_result_cap(state.get(RESULT_CAP_STATE)) == RESULT_CAP_CHARS
    bind_run_tuning(state, RunTuning(result_cap_chars=12_000))
    assert loop_result_cap(state.get(RESULT_CAP_STATE)) == 12_000
    bind_run_tuning(state, RunTuning(result_cap_chars=900_000))
    assert loop_result_cap(state.get(RESULT_CAP_STATE)) == RESULT_CAP_CHARS


# --- toolResults.format -----------------------------------------------------------------------------------------


def test_c410_f1_the_agents_default_applies_when_the_model_names_none() -> None:
    tuning = _tuning(ScopedLayer("agent", {"toolResults": {"format": "detailed"}}))
    assert tuning.response_format == "detailed"
    state: dict[str, object] = {}
    bind_run_tuning(state, tuning)
    assert run_response_format(state, None) == "detailed"
    assert run_response_format(state, "concise") == "concise"


def test_c410_f2_a_format_the_org_does_not_allow_falls_back_to_one_it_does() -> None:
    tuning = _tuning(ScopedLayer("org", {"toolResults": {"formats": ["concise"]}}))
    assert tuning.response_formats == ("concise",)
    state: dict[str, object] = {}
    bind_run_tuning(state, tuning)
    assert run_response_format(state, "detailed") == "concise"
    # Unset is the search's own default, concise.
    assert run_response_format(state, None) is None


def test_c410_f3_only_detailed_allowed_makes_it_the_default() -> None:
    state: dict[str, object] = {RESPONSE_FORMAT_POLICY: (None, ("detailed",))}
    assert run_response_format(state, None) == "detailed"


def test_c410_f4_no_policy_keeps_todays_behaviour() -> None:
    assert run_response_format({}, None) is None
    assert run_response_format({}, "detailed") == "detailed"


def test_c410_f5_an_agent_default_outside_the_orgs_formats_is_refused() -> None:
    refused = DimensionPolicyResolver().resolve(
        [
            ScopedLayer("org", {"toolResults": {"formats": ["concise"]}}),
            ScopedLayer("agent", {"toolResults": {"format": "detailed"}}),
        ]
    ).refusal
    assert refused is not None


# --- interactions.conversationGrants ----------------------------------------------------------------------------


def _event(seq: int, type_: str, writer: str, data: dict[str, JsonValue]) -> JournalEvent:
    return cast("JournalEvent", SimpleNamespace(seq=seq, type=type_, writer=writer, data=data, run_id="r1"))


def _granted_stream() -> list[JournalEvent]:
    return [
        _event(
            1,
            "interaction.explained",
            "worker",
            {
                "interactionId": "ix1",
                "toolId": "slack.send",
                "conversationGrant": True,
                "destination": {"label": "#general", "key": "k1"},
            },
        ),
        _event(2, "interaction.resolved", "node", {"interactionId": "ix1", "decision": "approved"}),
        _event(3, "interaction.granted", "node", {"interactionId": "ix1", "scope": "conversation"}),
    ]


def test_c410_g1_the_pin_turns_conversation_grants_off() -> None:
    assert _tuning(ScopedLayer("org", {"interactions": {"conversationGrants": False}})).conversation_grants is False
    assert _tuning(ScopedLayer("agent", {"interactions": {"conversationGrants": True}})).conversation_grants is True


def test_c410_g2_off_nothing_is_held_or_inherited() -> None:
    on = ConversationGrants([("jira.create", "k0")])
    on.apply(_granted_stream())
    assert on.allows("slack.send", "k1") and on.allows("jira.create", "k0")
    off = ConversationGrants([("jira.create", "k0")], enabled=False)
    off.apply(_granted_stream())
    assert not off.allows("slack.send", "k1")
    assert not off.allows("jira.create", "k0")
    assert off.held() == frozenset()


def test_c410_g3_off_the_card_never_offers_the_conversation() -> None:

    effect = cast("ToolEffect", SimpleNamespace(tool_id="slack.send"))
    where = AskDestination("#general", "k1")
    inner = SimpleNamespace(source_records=lambda _run: frozenset())
    on = GrantingLedger(inner, ConversationGrants())  # type: ignore[arg-type]
    off = GrantingLedger(inner, ConversationGrants(enabled=False))  # type: ignore[arg-type]
    assert explained_ask("flow", effect, on, "r1", where, grantable=True)["conversationGrant"] is True
    assert explained_ask("flow", effect, off, "r1", where, grantable=True)["conversationGrant"] is False


def test_c410_g4_a_child_inherits_no_grant_when_its_run_has_them_off() -> None:
    spec = SimpleNamespace(
        extensions={"x.pipeshub": {"inheritedGrants": [{"toolId": "slack.send", "key": "k1"}]}}, principal_ref="p_u1"
    )
    assert inherited_grants(spec).held() != frozenset()  # type: ignore[arg-type]
    assert inherited_grants(spec, enabled=False).held() == frozenset()  # type: ignore[arg-type]


# --- sandbox.* --------------------------------------------------------------------------------------------------


def test_c410_s1_caps_lower_the_template_and_never_raise_it() -> None:
    template = Resources(cpus=2.0, memory_mb=2048, pids=256, scratch_mb=512)
    assert ResourceCaps().apply(template) == template
    capped = ResourceCaps(cpus=1.0, memory_mb=4096, pids=128, scratch_mb=None).apply(template)
    assert capped == Resources(cpus=1.0, memory_mb=2048, pids=128, scratch_mb=512)


def test_c410_s2_the_pin_carries_the_narrowest_resources() -> None:
    tuning = _tuning(
        ScopedLayer("org", {"sandbox": {"cpus": 2, "memoryMb": 2048}}),
        ScopedLayer("agent", {"sandbox": {"cpus": 1, "pids": 128}}),
    )
    assert tuning.resources == ResourceCaps(cpus=1.0, memory_mb=2048, pids=128, scratch_mb=None)


def test_c410_s3_an_engine_sandbox_is_acquired_with_the_runs_caps() -> None:
    config = EngineControllerConfig(images={}, image_resources={"img": ImageResources(cpus=2.0, min_memory_mb=3072)})
    spec = {
        "image": "img",
        "mode": RuntimeMode.GVISOR,
        "workspace": MountSpec("/workspace"),
        "labels": {},
        "placeholders": ("model-placeholder-0123", "tools-placeholder-0123"),
        "tools_upstream": "http://tool-gateway:8098/m",
    }
    plain = engine_sandbox_spec(config, **spec)  # type: ignore[arg-type]
    assert (plain.resources.cpus, plain.resources.memory_mb) == (2.0, 3072)
    capped = engine_sandbox_spec(config, **spec, resource_caps=ResourceCaps(cpus=0.5, memory_mb=512))  # type: ignore[arg-type]
    assert capped.resources == replace(plain.resources, cpus=0.5, memory_mb=512)


@pytest.mark.parametrize("value", [None, 0, -1, True, "4"])
def test_c410_s4_an_unusable_pinned_value_keeps_the_template(value: object) -> None:
    from app.harness.run_policy.resolved import resolved_tuning

    tuning = resolved_tuning({"sandbox.pids": {"value": value, "setBy": "org", "ceilings": []}})  # type: ignore[dict-item]
    assert tuning.resources.pids is None
