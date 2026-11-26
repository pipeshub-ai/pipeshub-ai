"""GA-15 (G-27, WP-73): a run's four model roles reach their own models through the model gateway, billed per role.

A run calls models in four roles: primary (its turns), fast (an engine's background and small calls), subagent (an
engine's own helper) and compaction (summarising the conversation). The org's ``models.roles`` maps the reserved keys
fast, subagent and compaction to routes; an engine names only the role's neutral id (``engine-model-fast`` …) and the
gateway serves that role's route, or the primary one when the role is unmapped. Any other id is refused. Every charge
names its role, so cost is reported per role. The fold is the table Node runs too (``model-roles.json``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from app.harness.access.budget import NodeBudgetLedger
from app.harness.access.grants import parse_grant
from harness_core.access.model_roles import (
    ENGINE_ROLES,
    ROLE_MODEL_IDS,
    engine_role_routes,
)
from harness_core.access.types import (
    ENGINE_MODEL_ID,
    AccessRouteRef,
    AccountKind,
    AccountRef,
    PinnedRoute,
    neutral_models,
)
from harness_core.budget.metered import charged_as, current_charge_role
from harness_core.budget.types import Amount, RunAccountRef
from harness_core.context.compaction import ModelCompaction
from harness_core.context.ports import CompactionRequest
from harness_core.context.types import SeqSpan
from harness_core.kernel.core.messages import AssistantMessage, TextPart, UserMessage
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.refusals import HarnessRefusal
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    body_of,
    reply_events,
)
from tests.unit.harness_core.gateway.test_ma1_gateway_child_model import _launch
from tests.unit.harness_core.gateway.test_model_gateway import spent
from tests.unit.harness_core.gateway.test_responses_gateway import WIRES

if TYPE_CHECKING:
    from harness_core.gateway.binding import RunAttemptBinding

    from harness_core.budget.types import Reservation

VECTORS = Path(__file__).resolve().parents[6] / "contracts/harness/vectors"
TABLE: dict[str, Any] = json.loads((VECTORS / "model-roles.json").read_text(encoding="utf-8"))

# gpt-6-sol: 2.00 in, 10.00 out per 1M tokens. claude-haiku-4-5: 1.00 in, 5.00 out.
PRIMARY = PinnedRoute("openai", "gpt-6-sol", config_key="mk-a", config_model="gpt-6-sol")
FAST = PinnedRoute("anthropic", "claude-haiku-4-5", config_model="claude-haiku-4-5", by_name=True)
USAGE = TokenUsage(input_tokens=1_000, output_tokens=100)
PRIMARY_CHARGE = Amount(3_000, 1_100)
FAST_CHARGE = Amount(1_500, 1_100)


def _rig(roles: dict[str, PinnedRoute] | None = None) -> tuple[GatewayRig, RecordedTransport, RecordedTransport, RunAttemptBinding]:
    rig = GatewayRig()
    to_primary = RecordedTransport([reply_events("primary", usage=USAGE)] * 4, family="openai")
    to_fast = RecordedTransport([reply_events("fast", usage=USAGE)] * 4, family="anthropic")
    binding = rig.pin("run-1", routes=(PRIMARY,), transport=to_primary, roles=roles, role_transport=to_fast)
    return rig, to_primary, to_fast, binding


def _account() -> RunAccountRef:
    return RunAccountRef("org-1", "run-1")


@pytest.mark.parametrize("case", TABLE["cases"], ids=[c["id"] for c in TABLE["cases"]])
def test_mrole_1_the_engine_role_fold_is_the_shared_table(case: dict[str, Any]) -> None:
    folded = engine_role_routes(case["roles"], primary_account=case["primaryAccount"])

    got = {role: (f"{folded[role][0]}:{folded[role][1]}" if role in folded else "primary") for role in ENGINE_ROLES}
    assert got == case["expect"], case["behaviour"]


def test_mrole_1_the_neutral_ids_are_the_shared_tables() -> None:
    assert dict(ROLE_MODEL_IDS) == TABLE["neutralIds"]
    assert ROLE_MODEL_IDS["primary"] == ENGINE_MODEL_ID


def test_mrole_2_a_grant_names_one_neutral_id_per_role_unmapped_roles_on_the_primary_route() -> None:
    granted = {m.model_id: (m.role, m.route) for m in neutral_models((PRIMARY,), {"fast": FAST})}

    assert granted == {
        "engine-model": ("primary", PRIMARY),
        "engine-model-fast": ("fast", FAST),
        "engine-model-subagent": ("subagent", PRIMARY),
        "engine-model-compaction": ("compaction", PRIMARY),
    }


def test_mrole_2_a_primary_route_on_a_model_account_serves_every_role() -> None:
    own = PinnedRoute("openai", "gpt-6-sol", account=AccountRef(AccountKind.USER_API_KEY, "users/u1/openai"))

    granted = {m.model_id: m.route for m in neutral_models((own,), {"fast": FAST})}

    assert set(granted.values()) == {own}
    assert set(granted) == set(ROLE_MODEL_IDS.values())


def test_mrole_3_node_grants_role_routes_matched_by_name() -> None:
    document = {
        "runId": "run-1",
        "routes": [{"provider": "openai", "model": "gpt-6-sol", "modelKey": "mk-a", "modelName": "gpt-6-sol"}],
        "roleRoutes": {
            "fast": {"provider": "anthropic", "model": "claude-haiku-4-5"},
            "researcher": {"provider": "openai", "model": "gpt-x"},
        },
        "budgets": {"maxCostUsd": 1.0, "maxTokens": 1000},
    }

    grant = parse_grant(document, tenant="org-1")

    fast = grant.model("engine-model-fast")
    assert fast is not None and fast.role == "fast"
    assert fast.route == PinnedRoute("anthropic", "claude-haiku-4-5", config_model="claude-haiku-4-5", by_name=True)
    assert grant.model("engine-model-subagent").route == grant.routes[0]  # type: ignore[union-attr]
    assert grant.model("researcher") is None
    # The token's routes digest covers the pinned policy only: the role routes are the same pin's.
    assert [r.route_id for r in grant.routes] == ["openai:gpt-6-sol"]


@pytest.mark.parametrize("wire", sorted(WIRES))
async def test_ga15_the_fast_model_reaches_its_route_billed_at_its_price_as_fast(wire: str) -> None:
    rig, to_primary, to_fast, binding = _rig({"fast": FAST})
    codec, body = WIRES[wire]

    _ = await rig.gateway.serve(codec, rig.token(binding), {}, body_of(body(model="engine-model-fast")))
    _ = await rig.gateway.serve(codec, rig.token(binding), {}, body_of(body()))

    assert (len(to_fast.calls), len(to_primary.calls)) == (1, 1)
    assert await spent(rig) == Amount(FAST_CHARGE.usd_micros + PRIMARY_CHARGE.usd_micros, 2 * 1_100)
    assert rig.ledger.role_spend(_account()) == {"fast": FAST_CHARGE, "primary": PRIMARY_CHARGE}


@pytest.mark.parametrize("wire", sorted(WIRES))
async def test_ga15_an_unmapped_role_calls_the_primary_route_and_is_still_billed_as_its_role(wire: str) -> None:
    rig, to_primary, to_fast, binding = _rig(None)
    codec, body = WIRES[wire]

    _ = await rig.gateway.serve(codec, rig.token(binding), {}, body_of(body(model="engine-model-subagent")))

    assert (len(to_primary.calls), len(to_fast.calls)) == (1, 0)
    assert rig.ledger.role_spend(_account()) == {"subagent": PRIMARY_CHARGE}


@pytest.mark.parametrize("wire", sorted(WIRES))
@pytest.mark.parametrize("named", ["claude-haiku-4-5", "anthropic:claude-haiku-4-5", "engine-model-researcher", "engine-model-fast "])
async def test_ga15_an_unmapped_model_is_refused(wire: str, named: str) -> None:
    rig, to_primary, to_fast, binding = _rig({"fast": FAST})
    codec, body = WIRES[wire]

    with pytest.raises(HarnessRefusal):
        _ = await rig.gateway.serve(codec, rig.token(binding), {}, body_of(body(model=named)))

    assert to_primary.calls == [] and to_fast.calls == []
    assert await spent(rig) == Amount(0, 0)
    assert rig.ledger.role_spend(_account()) == {}


async def test_ga15_the_gateway_lists_the_four_neutral_ids() -> None:
    rig, _, _, binding = _rig({"fast": FAST})

    listed = await rig.gateway.models(rig.token(binding), {})

    assert sorted(m["id"] for m in listed["data"]) == sorted(ROLE_MODEL_IDS.values())


async def test_mrole_4_the_worker_and_gateway_send_the_role_with_each_charge() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        zero = {"usdMicros": 0, "tokens": 0}
        reservation = {"key": "call-1", "state": "committed", "expiresAt": 0, "requested": zero, "granted": zero}
        remaining = {"available": {"usdMicros": None, "tokens": None}, "committed": zero, "reserved": zero}
        return httpx.Response(
            200, json={"reservation": reservation, "charged": zero, "crossed": False, "remaining": remaining}
        )

    ledger = NodeBudgetLedger(base_url="http://node", http_client=httpx.AsyncClient(base_url="http://node", transport=httpx.MockTransport(handler)))
    reservation = _reservation()
    _ = await ledger.commit(reservation, Amount(1, 1), credential="token", model_role="compaction")
    _ = await ledger.commit(reservation, Amount(1, 1), credential="token")

    assert [body.get("modelRole") for body in seen] == ["compaction", None]


def _reservation() -> Reservation:
    from datetime import UTC, datetime

    from harness_core.budget.types import Reservation

    return Reservation(_account(), "call-1", Amount(10, 10), Amount(10, 10), datetime.now(UTC))


def test_mrole_5_engines_name_their_roles_neutral_ids() -> None:
    route = AccessRouteRef("openai:gpt-6-sol", "sha256:" + "d" * 64)

    claude = _launch("claude_code", route).env
    assert (claude["ANTHROPIC_MODEL"], claude["ANTHROPIC_SMALL_FAST_MODEL"], claude["CLAUDE_CODE_SUBAGENT_MODEL"]) == (
        "engine-model",
        "engine-model-fast",
        "engine-model-subagent",
    )
    assert claude["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "engine-model-fast"
    assert {claude[f"ANTHROPIC_DEFAULT_{tier}_MODEL"] for tier in ("SONNET", "OPUS", "FABLE")} == {"engine-model"}

    from harness_engines.opencode import launch as opencode

    config = json.loads(_launch("opencode", route).env["OPENCODE_CONFIG_CONTENT"])
    assert config["model"] == f"{opencode.PROVIDER}/engine-model"
    assert config["small_model"] == f"{opencode.PROVIDER}/engine-model-fast"
    assert sorted(config["provider"][opencode.PROVIDER]["models"]) == sorted(
        ["engine-model", "engine-model-fast", "engine-model-subagent"]
    )


def test_mrole_5_an_opencode_helper_calls_the_subagent_role() -> None:
    from harness_core.access.types import ModelApi
    from harness_core.engines.types import (
        EngineMode,
        EngineProfile,
        EngineVersion,
        GatewayEndpoints,
        SubAgentPolicy,
    )
    from harness_engines.opencode import launch as opencode

    endpoints = GatewayEndpoints(
        model_base_url="http://model-gateway:8097/v1",
        model_api=ModelApi.CHAT_COMPLETIONS,
        tools_mcp_url="http://tool-gateway:8097/mcp",
        token_placeholder="pxh-model-placeholder-0123",
        tools_token_placeholder="pxh-tools-placeholder-4567",
    )
    profile = EngineProfile(
        engine=EngineVersion("opencode", "1.0.0", "sha256:" + "d" * 64),
        mode=EngineMode.BUILD,
        access=AccessRouteRef("openai:gpt-6-sol", "sha256:" + "d" * 64),
        sub_agents=SubAgentPolicy(enabled=True, max_depth=1, max_concurrent=2),
    )
    config = json.loads(opencode.render_launch(profile, endpoints, "/skills").env["OPENCODE_CONFIG_CONTENT"])

    assert config["agent"][opencode.SUBAGENT]["model"] == f"{opencode.PROVIDER}/engine-model-subagent"
    assert "model" not in config["agent"][opencode.AGENT]


class _Summariser:
    def __init__(self) -> None:
        self.roles: list[str | None] = []

    async def complete(self, messages: list[Any], *args: object, **kwargs: object) -> ModelResponse:
        self.roles.append(current_charge_role())
        return ModelResponse(
            message=AssistantMessage(content=[TextPart(text="summary")]),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            stop_reason=StopReason.END_TURN,
            model="m",
        )


async def test_mrole_6_a_compaction_call_is_charged_as_compaction() -> None:
    model = _Summariser()

    _ = await ModelCompaction(model).compact(  # type: ignore[arg-type]
        CompactionRequest(span=SeqSpan(1, 2), messages=(UserMessage(content="hello"),), max_chars=200, input_chars=2_000)
    )

    assert model.roles == ["compaction"]
    assert current_charge_role() is None
    with charged_as("fast"):
        assert current_charge_role() == "fast"
    assert current_charge_role() is None


async def test_r16_role_2_a_reconciled_estimate_counts_under_the_role_its_commit_named() -> None:
    """GA-15 x WP-66: the provider's usage replaces a cut fast call's estimate in the fast role, not in primary."""
    from harness_core.budget.memory import InMemoryBudgetLedger
    from harness_core.budget.types import Cap, Hold
    from harness_core.testing.clock import ManualClock

    ledger = InMemoryBudgetLedger(ManualClock())
    account = RunAccountRef("org-r16", "run-cut")
    await ledger.open_root(account, Cap(usd_micros=5_000_000))
    held = await ledger.reserve(account, "k1", Amount(100_000, 100), minimum=Amount(1, 0), ttl_s=900)
    await ledger.commit(held, Amount(20_000, 100), hold=Hold(Amount(50_000, 100), 600), model_role="fast")
    await ledger.reconcile(held, Amount(60_000, 100))
    assert ledger.role_spend(account) == {"fast": Amount(60_000, 100)}
