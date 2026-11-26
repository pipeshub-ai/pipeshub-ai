"""CPD-10 R3 and assigned item 3: which authority a pinned run's items have, on the worker.

CPD10-53..55 read the shared table ``contracts/control-plane/vectors/effective-set-authority.json`` (Node reads
the same file for the admission side): the pin fragment Node stored for each case, the legacy fields a spec may
still carry, and exactly what the worker offers. CPD10-50 and CPD10-52 are the worker's own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.control_plane.runtime.pinning import (
    PinSnapshot,
    PinSubject,
    assemble,
    pin_layers,
)
from app.control_plane.runtime.types import RunContext as ItemRunContext
from app.control_plane.testing import contracts as broker_contract
from app.harness.assembly.context import RunContext
from app.harness.assembly.items.credentials import (
    PinnedBindingLeases,
    bound_installations,
)
from app.harness.assembly.items.mcp import McpConnector, McpItemRuntime
from app.harness.assembly.items.pins import (
    CAPABILITIES_ROW,
    offer,
    pinned_items,
    set_is_authority,
)
from app.harness.assembly.items.run import (
    PinnedCredentialsUnavailable,
    item_credentials,
)
from app.harness.assembly.items.toolsets import RunToolsetConnector, ToolsetItemRuntime
from app.harness.credentials.bindings import BindingCredentialBroker, BindingDocuments
from app.harness.credentials.service import pipeshub_credential_service
from app.harness.credentials.sources import TOOLSET_KIND
from app.harness.credentials.tool_credentials import ToolCredentials
from app.harness.credentials.vault import VaultSecretStore
from app.utils.env_flags import CONTROL_PLANE_CREDENTIALS
from harness_core.contracts import canonical_digest, dump_contract
from harness_core.credentials.errors import CredentialRefused, CredentialRevoked
from harness_core.credentials.service import HandleCredentialService
from harness_core.credentials.types import MAX_TTL_S, CredentialScope, Secret
from harness_core.driver import CapabilityLost
from harness_core.mcp_hub.manifest import ApprovedManifest
from harness_core.policy import DimensionPolicyResolver
from harness_core.security.secrets import masking
from harness_core.security.token_patterns import redact_tokens
from harness_core.testing.clock import ManualClock
from tests.unit.harness.assembly import world as w
from tests.unit.harness.credentials.binding_support import NodeFormatBindings
from tests.unit.harness.credentials.support import DictConfig
from tests.unit.harness.pinning.support import pinned

if TYPE_CHECKING:
    from pydantic import JsonValue

    from app.control_plane.contracts.generated.effective_set import EffectiveItem
    from app.control_plane.runtime.types import ItemLoad

VECTORS = Path(__file__).resolve().parents[7] / "contracts" / "control-plane" / "vectors" / "effective-set-authority.json"
CASES: list[dict[str, Any]] = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]
CASE = {case["id"]: case for case in CASES}
EMPTY_SET: dict[str, JsonValue] = {"items": [], "digest": canonical_digest([])}


def pin_of(rollout: bool | None, effective_set: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """A pin as admission assembles it: the org layer carries the row for a migrated org, and nothing otherwise."""
    org: dict[str, JsonValue] = {"schemaVersion": 1, "scope": {"kind": "org"}}
    if rollout is not None:
        org["rollout"] = {"CONTROL_PLANE_CAPABILITIES": rollout}
    subject = PinSubject(
        org_id=w.ORG, actor_id=w.ACTOR, agent_key=w.AGENT, chat_mode="quick", surface="web",
        request={"schemaVersion": 1, "scope": {"kind": "request"}},
    )
    resolved = DimensionPolicyResolver().resolve(
        pin_layers(subject, PinSnapshot(head_revision=1, layers={f"org:{w.ORG}": org}, deployment=None))
    )
    assert resolved.config is not None, resolved.refusal
    return cast(
        "dict[str, JsonValue]",
        dump_contract(
            assemble(
                {
                    "schemaVersion": 1,
                    "headRevision": 1,
                    "routing": {"path": "durable", "reason": None},
                    "scope": {"agentKey": w.AGENT, "projectId": None},
                    "effectiveSet": effective_set,
                    "effectiveConfig": cast("JsonValue", resolved.config),
                    "llm": None,
                    "deploymentDigest": canonical_digest({}),
                }
            )
        ),
    )


def run_of(spec: dict[str, Any], pin: dict[str, JsonValue] | None) -> RunContext:
    built = w.pinned_spec(tool_ids=spec["toolIds"], mcp=spec["mcpInstances"], skills=spec["skills"])
    return RunContext.from_claim(w.claim(built if pin is None else pinned(built, pin)))


def vector_run(case: dict[str, Any]) -> RunContext:
    return run_of(case["spec"], pin_of(case["pin"]["rollout"], case["pin"]["effectiveSet"]))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_cpd10_53_54_55_the_worker_offers_exactly_what_the_vector_says(case: dict[str, Any]) -> None:
    run = vector_run(case)
    assert set_is_authority(run) is case["migrated"]
    offered = offer(run)
    held = run if offered is None else offered.narrowed(run)
    expected = case["expected"]
    assert sorted(held.tool_ids) == expected["toolIds"]
    assert sorted(held.mcp_instances) == expected["mcpInstances"]
    assert list(held.skills) == expected["skills"]
    assert sorted(held.skill_digests) == expected["skills"]
    items = pinned_items(held)
    assert [
        {"installation": item.installation.root, "binding": item.binding.ref if item.binding else None} for item in items
    ] == expected["items"]
    assert all(item.state == "ready" for item in items)
    # Narrowing is settled: what the run holds after it is what it is offered again.
    assert pinned_items(run) == items


def test_cpd10_53_a_migrated_org_s_empty_set_offers_no_item_whatever_the_spec_still_names() -> None:
    case = CASE["CPD10-53"]
    run = vector_run(case)
    offered = offer(run)
    assert offered is not None
    assert offered.items == ()
    assert offered.mcp_instances == ()
    assert offered.skills == ()
    assert offered.tool_ids == ("calculator@default/add",)
    assert len(run.tool_ids) > 1
    assert run.skills == ("notes",)


def test_cpd10_54_without_the_row_the_spec_s_pins_are_the_items_exactly_as_before() -> None:
    case = CASE["CPD10-54"]
    for pin in (pin_of(None, EMPTY_SET), pin_of(False, EMPTY_SET), None):
        run = run_of(case["spec"], pin)
        assert set_is_authority(run) is False
        assert offer(run) is None
        assert [item.installation.root for item in pinned_items(run)] == ["skill_notes"]
    absent = pin_of(None, EMPTY_SET)
    assert CAPABILITIES_ROW not in cast("dict[str, Any]", absent["effectiveConfig"])["dimensions"]


def test_cpd10_55_the_ready_item_keeps_its_binding_and_the_set_s_digests_under_the_spec_s_tool_ids() -> None:
    case = CASE["CPD10-55"]
    (jira,) = pinned_items(vector_run(case))
    ready = next(item for item in case["pin"]["effectiveSet"]["items"] if item["state"] == "ready")
    assert jira.binding is not None
    assert dump_contract(jira.binding) == ready["binding"]
    assert {tool.tool_id: tool.descriptor_digest.root for tool in jira.tools} == {
        tool["toolId"]: tool["descriptorDigest"] for tool in ready["tools"]
    }


def test_cpd10_50_a_tool_of_a_ready_item_that_the_set_does_not_hold_is_not_offered() -> None:
    case = CASE["CPD10-55"]
    spec = {**case["spec"], "toolIds": [*case["spec"]["toolIds"], "jira@jira-1/delete_issue"]}
    run = run_of(spec, pin_of(True, case["pin"]["effectiveSet"]))
    offered = offer(run)
    assert offered is not None
    assert "jira@jira-1/delete_issue" not in offered.tool_ids
    assert [tool.tool_id for item in offered.items for tool in item.tools] == sorted(
        t for t in case["expected"]["toolIds"] if t.startswith("jira@")
    )


def test_cpd10_50_an_item_the_spec_grants_that_the_set_holds_unavailable_is_capability_lost_with_the_set_s_reason() -> None:
    case = CASE["CPD10-55"]
    unavailable = next(item for item in case["pin"]["effectiveSet"]["items"] if item["state"] == "unavailable")
    installation = unavailable["installation"]
    spec = {
        **case["spec"],
        "toolIds": [*case["spec"]["toolIds"], f"mcp@{installation}/list_issues"],
        "mcpInstances": [*case["spec"]["mcpInstances"], installation],
    }
    run = run_of(spec, pin_of(True, case["pin"]["effectiveSet"]))
    with pytest.raises(CapabilityLost) as lost:
        _ = offer(run)
    assert lost.value.missing == (f"item:{installation}:credential_missing",)
    with pytest.raises(CapabilityLost):
        _ = pinned_items(run)


def test_cpd10_50_an_unavailable_skill_the_spec_grants_is_lost_too() -> None:
    skill = {
        "installation": "skill_notes", "item": "skill:pipeshub/notes@1.0.0", "kind": "skill", "state": "unavailable",
        "reason": "disabled", "tools": [], "binding": None,
        "provenance": {"grantedBy": "agent:a7", "visibleVia": None, "approvedBy": None},
    }
    run = run_of(CASE["CPD10-53"]["spec"], pin_of(True, {"items": [skill], "digest": canonical_digest([skill])}))
    with pytest.raises(CapabilityLost) as lost:
        _ = offer(run)
    assert lost.value.missing == ("item:skill_notes:disabled",)


def test_cpd10_50_a_server_pin_goes_with_a_tool_the_set_no_longer_holds_and_stays_when_every_tool_is_held() -> None:
    instance = "inst_01J9ZQ5Y8K3M4N5P6Q7R8S0001"
    digest = "sha256:" + "2" * 64

    def server(*tools: str) -> dict[str, JsonValue]:
        return {
            "installation": instance, "item": "mcp:pipeshub/linear@3", "kind": "mcp", "state": "ready", "reason": None,
            "tools": [{"toolId": f"mcp@{instance}/{tool}", "descriptorDigest": digest} for tool in tools],
            "binding": None, "provenance": {"grantedBy": "agent:a7", "visibleVia": "org", "approvedBy": None},
        }

    def run_with(*held: str) -> RunContext:
        item = server(*held)
        run = run_of(
            {"toolIds": [f"mcp@{instance}/a", f"mcp@{instance}/b"], "mcpInstances": [instance], "skills": []},
            pin_of(True, {"items": [item], "digest": canonical_digest([item])}),
        )
        from app.harness.assembly.descriptor_pins import DescriptorPin

        return run.model_copy(
            update={"descriptor_pins": (DescriptorPin(kind="mcp", ref=instance, digest=digest, tools=("a", "b")),)}
        )

    whole = run_with("a", "b")
    assert offer(whole).narrowed(whole).descriptor_pins == whole.descriptor_pins  # type: ignore[union-attr]
    fewer = run_with("a")
    narrowed = offer(fewer).narrowed(fewer)  # type: ignore[union-attr]
    assert narrowed.tool_ids == (f"mcp@{instance}/a",)
    assert narrowed.mcp_instances == (instance,)
    assert narrowed.descriptor_pins == ()


# ---- CPD10-51, CPD10-52: the binding broker in the item runtimes


INSTALLATION = "inst-a"
BOUND_SECRET = "xoxe.xoxp-1-" + "PinnedBindingAccess0123456789abcd"
REFRESHED_SECRET = "xoxe.xoxp-1-" + "RefreshedBindingAccess0123456789a"
OWNER_PATH_SECRET = "xoxe.xoxp-1-" + "OwnerPathAccessToken0123456789abc"
RECORD: dict[str, JsonValue] = {"isAuthenticated": True, "auth": {"token": BOUND_SECRET}}
ITEM_CTX = ItemRunContext(broker_contract.ORG, "run-1", "attempt-1")


class _Descriptors:
    """Serves every tool at its contract digest, as a release toolset and as an approved MCP manifest."""

    def toolset_tool(self, toolset: str, tool: str) -> str | None:
        return broker_contract.tool_digest(tool)

    async def approved_mcp_tools(self, instance_id: str) -> ApprovedManifest | None:
        return ApprovedManifest(tools={"list": broker_contract.tool_digest("list")}, approved_by="u-admin", approved_at=1)


class _Vault:
    """Bindings as Node's binder writes them, and the production broker over them (``item_credentials``'s)."""

    def __init__(self, clock: ManualClock) -> None:
        self.config = DictConfig()
        self._bindings = NodeFormatBindings(self.config, broker_contract.ORG)
        self.broker = BindingCredentialBroker(
            pipeshub_credential_service(self.config, clock=clock),
            BindingDocuments(VaultSecretStore(self.config, clock)),
        )

    def store(self, binding: str, material: JsonValue) -> str:
        """A version of ``binding`` (the first, or a refresh); each binding is its own installation's."""
        installation = INSTALLATION if binding == broker_contract.BINDING else "inst-other"
        return self._bindings.store(binding, material, installation=installation)

    def tombstone(self, binding: str) -> None:
        self._bindings.tombstone(binding)


@dataclass
class _Bound:
    """One bound item of ``kind``, loaded by the production connector from the production broker."""

    kind: str
    clock: ManualClock = field(default_factory=ManualClock)

    def __post_init__(self) -> None:
        self.vault = _Vault(self.clock)
        self.broker = self.vault.broker
        self.leases = PinnedBindingLeases(self.broker)
        descriptors = _Descriptors()
        self.runtime = (
            ToolsetItemRuntime(RunToolsetConnector(descriptors, {}, self.leases))
            if self.kind == "toolset"
            else McpItemRuntime(McpConnector(descriptors, {}, None, self.leases))
        )

    def item(self, version_digest: str, ref: str = broker_contract.BINDING) -> EffectiveItem:
        bound = broker_contract.effective_item(self.kind, INSTALLATION, ["list"])
        return bound.model_copy(update={"binding": broker_contract.pinned_binding(ref, version_digest)})

    async def load(self, version_digest: str, ref: str = broker_contract.BINDING) -> ItemLoad:
        return await self.runtime.load([self.item(version_digest, ref)], ITEM_CTX, self.broker)


BOUND_KINDS = pytest.mark.parametrize("kind", ["toolset", "mcp"])


@BOUND_KINDS
async def test_cpd10_51_a_bound_item_leases_its_pinned_binding_at_load_and_the_lease_is_the_record(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    bound = _Bound(kind)
    pinned_version = bound.vault.store(broker_contract.BINDING, RECORD)

    loaded = await bound.load(pinned_version)

    assert [tool.tool_id for tool in loaded.tools] == [f"{kind}@{INSTALLATION}/list"]
    assert bound.leases.holds(INSTALLATION)
    assert await bound.leases.material(INSTALLATION) == RECORD


@BOUND_KINDS
async def test_cpd10_51_a_revoked_binding_is_refused_at_load_as_a_lost_capability(kind: str) -> None:
    bound = _Bound(kind)
    pinned_version = bound.vault.store(broker_contract.BINDING, RECORD)
    bound.vault.tombstone(broker_contract.BINDING)

    with pytest.raises(CapabilityLost) as lost:
        _ = await bound.load(pinned_version)

    assert lost.value.changed == (f"credential:{INSTALLATION}:credential_revoked",)
    assert lost.value.missing == ()


@BOUND_KINDS
async def test_cpd10_51_a_version_that_is_not_the_pinned_binding_s_is_refused_at_load(kind: str) -> None:
    bound = _Bound(kind)
    _ = bound.vault.store(broker_contract.BINDING, RECORD)
    foreign = bound.vault.store(broker_contract.OTHER_BINDING, {"auth": {"token": "someone-elses"}})

    with pytest.raises(CapabilityLost) as lost:
        _ = await bound.load(foreign)

    assert lost.value.changed == (f"credential:{INSTALLATION}:credential_unavailable",)


@BOUND_KINDS
async def test_cpd10_51_a_refreshed_version_of_the_same_binding_is_accepted_and_served(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    bound = _Bound(kind)
    pinned_version = bound.vault.store(broker_contract.BINDING, RECORD)
    refreshed: dict[str, JsonValue] = {"isAuthenticated": True, "auth": {"token": REFRESHED_SECRET}}
    _ = bound.vault.store(broker_contract.BINDING, refreshed)

    _ = await bound.load(pinned_version)

    assert await bound.leases.material(INSTALLATION) == refreshed


async def test_cpd10_51_a_lease_that_lapsed_is_taken_again_but_never_after_a_revoke(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    bound = _Bound("toolset")
    _ = await bound.load(bound.vault.store(broker_contract.BINDING, RECORD))
    _ = bound.clock.advance(MAX_TTL_S + 1)

    assert await bound.leases.material(INSTALLATION) == RECORD

    bound.vault.tombstone(broker_contract.BINDING)
    _ = bound.clock.advance(MAX_TTL_S + 1)
    with pytest.raises(CredentialRevoked):
        _ = await bound.leases.material(INSTALLATION)


async def test_cpd10_51_an_item_without_a_binding_leases_nothing() -> None:
    bound = _Bound("toolset")
    unbound = broker_contract.effective_item("toolset", INSTALLATION, ["list"])

    loaded = await bound.runtime.load([unbound], ITEM_CTX, bound.broker)

    assert len(loaded.tools) == 1
    assert not bound.leases.holds(INSTALLATION)


class _OwnerPath:
    """The credential service's toolset source: the record at the owner path, counted."""

    def __init__(self) -> None:
        self.reads = 0

    async def read(self, tenant: str, scope: CredentialScope, version: str | None = None) -> Secret | None:
        self.reads += 1
        return Secret("v1", {"isAuthenticated": True, "auth": {"token": OWNER_PATH_SECRET}})

    async def write(self, tenant: str, scope: CredentialScope, material: JsonValue) -> str:
        raise AssertionError("a run never writes a credential")


@dataclass
class _Client:
    """A tool client's view: ``ToolCredentials`` of the attempt, routed through the attempt's pinned leases."""

    bound: _Bound
    owner_path: _OwnerPath = field(default_factory=_OwnerPath)

    def __post_init__(self) -> None:
        service = HandleCredentialService({TOOLSET_KIND: self.owner_path}, self.bound.clock)
        self.credentials = ToolCredentials(
            service, tenant=broker_contract.ORG, run_id="run-1", principal="u3", attempt_id="attempt-1", clock=self.bound.clock
        )
        self.credentials.route(self.bound.leases)

    async def record(self, installation: str) -> JsonValue:
        return await self.credentials.material(CredentialScope(TOOLSET_KIND, f"{installation}/u3"))


@pytest.mark.parametrize(
    ("switch", "installation", "token", "reads_owner_path"),
    [
        ("true", INSTALLATION, BOUND_SECRET, False),
        ("true", "inst-unbound", OWNER_PATH_SECRET, True),
        ("false", INSTALLATION, OWNER_PATH_SECRET, True),
        ("false", "inst-unbound", OWNER_PATH_SECRET, True),
    ],
    ids=["on-bound", "on-unbound", "off-bound", "off-unbound"],
)
async def test_cpd10_51_the_client_s_record_by_the_credentials_switch_and_whether_the_item_is_bound(
    switch: str, installation: str, token: str, reads_owner_path: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, switch)
    bound = _Bound("toolset")
    _ = await bound.load(bound.vault.store(broker_contract.BINDING, RECORD))
    client = _Client(bound)

    record = await client.record(installation)

    assert cast("dict[str, Any]", record)["auth"]["token"] == token
    assert (client.owner_path.reads > 0) is reads_owner_path


async def test_cpd10_51_the_leased_record_is_masked_at_the_effect_boundary_until_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    bound = _Bound("toolset")
    _ = await bound.load(bound.vault.store(broker_contract.BINDING, RECORD))
    client = _Client(bound)
    echoed = f"upstream said: bad token {BOUND_SECRET}"
    assert BOUND_SECRET in client.credentials.resolved_secrets().mask(echoed)

    _ = await client.record(INSTALLATION)

    assert BOUND_SECRET not in client.credentials.resolved_secrets().mask(echoed)
    with masking(client.credentials):
        assert BOUND_SECRET not in redact_tokens(echoed)


async def test_cpd10_51_teardown_revokes_every_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    bound = _Bound("toolset")
    _ = await bound.load(bound.vault.store(broker_contract.BINDING, RECORD))
    client = _Client(bound)
    _ = await client.record(INSTALLATION)

    assert await client.credentials.close() == 1

    with pytest.raises(CredentialRefused):
        _ = await client.record(INSTALLATION)
    assert not client.credentials.resolved_secrets()


def test_cpd10_51_a_pinned_run_s_runtimes_get_the_binding_broker_and_cpd10_52_a_run_without_a_pin_does_not() -> None:
    services = w.Services()
    spec = CASE["CPD10-54"]["spec"]

    assert isinstance(item_credentials(run_of(spec, pin_of(None, EMPTY_SET)), services), BindingCredentialBroker)
    assert isinstance(item_credentials(run_of(spec, pin_of(True, EMPTY_SET)), services), BindingCredentialBroker)
    assert isinstance(item_credentials(run_of(spec, None), services), PinnedCredentialsUnavailable)


async def test_cpd10_52_a_run_without_a_pin_is_refused_every_lease_and_holds_none() -> None:
    broker = PinnedCredentialsUnavailable()
    leases = PinnedBindingLeases(broker)
    runtime = ToolsetItemRuntime(RunToolsetConnector(_Descriptors(), {}, leases))
    bound = broker_contract.effective_item("toolset", INSTALLATION, ["list"]).model_copy(
        update={"binding": broker_contract.pinned_binding()}
    )

    with pytest.raises(CapabilityLost) as lost:
        _ = await runtime.load([bound], ITEM_CTX, broker)

    assert lost.value.changed == (f"credential:{INSTALLATION}:credential_unavailable",)
    assert await leases.close() == 0


@pytest.mark.parametrize(("switch", "expected"), [("true", {"jira-1"}), ("false", set())])
def test_cpd10_51_an_installation_s_old_record_is_skipped_only_while_its_binding_is_the_record(
    switch: str, expected: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, switch)
    case = CASE["CPD10-55"]
    run = vector_run(case)
    held = offer(run).narrowed(run)  # type: ignore[union-attr]

    assert bound_installations(pinned_items(held)) == expected
    # An unavailable item's binding, were one named, is never the record: only ready items count.
    assert bound_installations(tuple(run.resolved.effective_set.items)) == expected  # type: ignore[union-attr]
