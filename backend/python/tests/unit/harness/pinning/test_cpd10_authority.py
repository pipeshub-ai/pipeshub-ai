"""CPD-10 R3 on the production assembler: what a migrated org's run can call is what its effective set holds.

The item list alone would not make "nothing offered" true: the catalog the model sees, the toolsets the tool
environment builds and the credentials it resolves all follow the run's grants. Each case admits a run whose
spec still names the Jira tools, with a pin that says who the authority is, and drives it through
``PipesHubRuntimeAssembler``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from app.control_plane.runtime.pinning import assemble
from app.harness.assembly.descriptor_pins import ToolDescriptors
from app.harness.assembly.items.pins import CAPABILITIES_ROW
from app.utils.env_flags import CONTROL_PLANE_CREDENTIALS
from harness_core.contracts import canonical_digest, dump_contract
from harness_core.testing import Reply
from tests.unit.harness.assembly import world as w
from tests.unit.harness.credentials.binding_support import NodeFormatBindings
from tests.unit.harness.credentials.support import DictConfig

from .support import CREATE, JIRA_CREATE, JIRA_SEARCH, SEARCH, LoopRig, call

if TYPE_CHECKING:
    import pytest
    from pydantic import JsonValue

DONE = Reply(text="done")
JIRA_CREDENTIAL = f"/services/toolsets/{w.JIRA}/"


def jira_item(
    rig: LoopRig,
    *tools: str,
    state: str = "ready",
    reason: str | None = None,
    binding: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    descriptors = ToolDescriptors(rig.services.config)
    return {
        "installation": w.JIRA,
        "item": "toolset:pipeshub/jira@1",
        "kind": "toolset",
        "state": state,
        "reason": reason,
        "tools": [
            {"toolId": f"jira@{w.JIRA}/{tool}", "descriptorDigest": cast("str", descriptors.toolset_tool("jira", tool))}
            for tool in tools
        ],
        "binding": binding,
        "provenance": {"grantedBy": f"agent:{w.AGENT}", "visibleVia": "org", "approvedBy": None},
    }


def authority_pin(rig: LoopRig, *items: dict[str, JsonValue], migrated: bool = True) -> dict[str, JsonValue]:
    """The pin admission stores for the run: a migrated org's carries the row, and its effective set."""
    fields = {key: value for key, value in cast("dict[str, Any]", rig.control.pin()).items() if key != "digest"}
    fields["effectiveSet"] = {"items": list(items), "digest": canonical_digest(list(items))}
    if migrated:
        fields["effectiveConfig"]["dimensions"][CAPABILITIES_ROW] = {
            "value": True,
            "setBy": "org",
            "ceilings": [{"layer": "org", "value": True}],
        }
    return cast("dict[str, JsonValue]", dump_contract(assemble(fields)))


def credential_reads(rig: LoopRig) -> list[str]:
    return [key for key in rig.control._config.read if key.startswith(JIRA_CREDENTIAL)]  # pyright: ignore[reportPrivateUsage]


async def test_cpd10_53_a_migrated_org_s_empty_set_leaves_the_model_no_item_to_call_and_no_credential_is_read() -> None:
    rig = LoopRig.scripted(call(SEARCH, jql="open bugs"), call(CREATE, title="x"), DONE)
    _ = rig.admit(pin=authority_pin(rig))

    outcome = await rig.attempt()

    assert outcome.status == "completed"
    assert rig.ran(JIRA_SEARCH) == 0
    assert rig.ran(JIRA_CREATE) == 0
    assert credential_reads(rig) == []


async def test_cpd10_54_an_org_that_is_not_migrated_runs_on_its_spec_s_pins_as_before() -> None:
    rig = LoopRig.scripted(call(SEARCH, jql="open bugs"), call(CREATE, title="x"), DONE)
    _ = rig.admit(pin=authority_pin(rig, migrated=False))

    outcome = await rig.attempt()

    assert outcome.status == "completed"
    assert rig.ran(JIRA_SEARCH) == 1
    assert rig.ran(JIRA_CREATE) == 1
    assert credential_reads(rig) != []


async def test_cpd10_55_only_the_tools_the_set_holds_ready_can_be_called() -> None:
    rig = LoopRig.scripted(call(SEARCH, jql="open bugs"), call(CREATE, title="x"), DONE)
    _ = rig.admit(pin=authority_pin(rig, jira_item(rig, "search_issues")))

    outcome = await rig.attempt()

    assert outcome.status == "completed"
    assert rig.ran(JIRA_SEARCH) == 1
    assert rig.ran(JIRA_CREATE) == 0


async def test_cpd10_50_a_run_whose_spec_grants_an_item_its_set_holds_unavailable_never_starts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = LoopRig.scripted(call(SEARCH, jql="open bugs"), DONE)
    _ = rig.admit(pin=authority_pin(rig, jira_item(rig, state="unavailable", reason="credential_revoked")))
    caplog.set_level(logging.INFO)

    _ = await rig.attempt()

    assert rig.ran(JIRA_SEARCH) == 0
    assert [e.data.get("reason") for e in rig.events() if e.type == "run.yielded"] == ["capability_wait"]
    waits = [r.harness for r in caplog.records if getattr(r, "harness_event", None) == "run.capability_wait"]  # type: ignore[attr-defined]
    assert [wait["missing"] for wait in waits] == [[f"item:{w.JIRA}:credential_revoked"]]
    assert credential_reads(rig) == []


# ---- CPD10-51: the bound item's credential, end to end

BINDING = "bnd_01J9ZQ5Y8K3M4N5P6Q7R8S0001"
BOUND_TOKEN = "xoxe.xoxp-1-" + "PinnedBindingAccess0123456789abcd"
OWNER_PATH_TOKEN = "secret-jira-token"


class Vault:
    """The control plane's credential vault beside the rig's configuration store, as Node's binder writes it."""

    def __init__(self, rig: LoopRig) -> None:
        self._rig = rig
        self._documents = DictConfig()
        self._bindings = NodeFormatBindings(self._documents, w.ORG)

    def _publish(self) -> None:
        for key, value in self._documents.values.items():
            self._rig.services.config.put(key, value)

    def bind(self, token: str = BOUND_TOKEN) -> dict[str, JsonValue]:
        """Binds the actor's Jira credential (or refreshes it); answers the reference a pin holds."""
        version = self._bindings.store(
            BINDING, {"isAuthenticated": True, "auth": {"token": token}}, installation=w.JIRA, owner=f"user:{w.ACTOR}"
        )
        self._publish()
        return {"ref": BINDING, "owner": f"user:{w.ACTOR}", "versionDigest": version}

    def revoke(self) -> None:
        self._bindings.tombstone(BINDING)
        self._publish()


def seen_by_the_client(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The token the Jira client was created with, each time the search tool runs; the tool echoes it."""
    tokens: list[str] = []

    async def search_issues(self: w.FakeJira, jql: str) -> str:
        token = cast("dict[str, Any]", self.client)["config"]["auth"]["token"]
        tokens.append(token)
        return f"upstream rejected {token}"

    w.replace_tool(monkeypatch, w.FakeJira, "search_issues", search_issues)
    return tokens


def closed_handles(caplog: pytest.LogCaptureFixture) -> list[int]:
    return [r.harness["credentialHandles"] for r in caplog.records if getattr(r, "harness_event", None) == "run.closed"]  # type: ignore[attr-defined]


async def test_cpd10_51_with_the_switch_on_the_client_uses_the_pinned_binding_masked_in_its_result_and_revoked_at_teardown(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    caplog.set_level(logging.INFO)
    rig = LoopRig.scripted(call(SEARCH, jql="open bugs"), DONE)
    binding = Vault(rig).bind()
    tokens = seen_by_the_client(monkeypatch)
    _ = rig.admit(pin=authority_pin(rig, jira_item(rig, "search_issues", binding=binding)))

    outcome = await rig.attempt()

    assert outcome.status == "completed"
    assert tokens == [BOUND_TOKEN]
    assert credential_reads(rig) == []
    assert BOUND_TOKEN not in str([e.data for e in rig.events()])
    assert closed_handles(caplog) == [1]


async def test_cpd10_51_with_the_switch_off_the_binding_is_leased_but_the_record_is_the_owner_path_s(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "false")
    caplog.set_level(logging.INFO)
    rig = LoopRig.scripted(call(SEARCH, jql="open bugs"), DONE)
    binding = Vault(rig).bind()
    tokens = seen_by_the_client(monkeypatch)
    _ = rig.admit(pin=authority_pin(rig, jira_item(rig, "search_issues", binding=binding)))

    outcome = await rig.attempt()

    assert outcome.status == "completed"
    assert tokens == [OWNER_PATH_TOKEN]
    # The owner path's handle and the binding's lease.
    assert closed_handles(caplog) == [2]


async def test_cpd10_51_a_refresh_after_admission_is_served_and_a_revoke_refuses_the_run_whatever_the_switch(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    rig = LoopRig.scripted(call(SEARCH, jql="open bugs"), DONE)
    vault = Vault(rig)
    pinned_binding = vault.bind()
    tokens = seen_by_the_client(monkeypatch)
    _ = rig.admit(pin=authority_pin(rig, jira_item(rig, "search_issues", binding=pinned_binding)))
    refreshed = "xoxe.xoxp-1-" + "RefreshedBindingAccess0123456789a"
    _ = vault.bind(refreshed)

    _ = await rig.attempt()

    assert tokens == [refreshed]

    for switch in ("true", "false"):
        monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, switch)
        caplog.clear()
        caplog.set_level(logging.INFO)
        revoked = LoopRig.scripted(call(SEARCH, jql="open bugs"), DONE)
        revoked_vault = Vault(revoked)
        held = revoked_vault.bind()
        _ = revoked.admit(pin=authority_pin(revoked, jira_item(revoked, "search_issues", binding=held)))
        revoked_vault.revoke()

        _ = await revoked.attempt()

        assert revoked.ran(JIRA_SEARCH) == 0
        waits = [r.harness for r in caplog.records if getattr(r, "harness_event", None) == "run.capability_wait"]  # type: ignore[attr-defined]
        assert [wait["changed"] for wait in waits] == [[f"credential:{w.JIRA}:credential_revoked"]]


async def test_cpd10_51_an_item_without_a_binding_keeps_the_owner_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    rig = LoopRig.scripted(call(SEARCH, jql="open bugs"), DONE)
    tokens = seen_by_the_client(monkeypatch)
    _ = rig.admit(pin=authority_pin(rig, jira_item(rig, "search_issues")))

    _ = await rig.attempt()

    assert tokens == [OWNER_PATH_TOKEN]


async def test_cpd10_52_a_run_without_a_pin_resolves_its_credentials_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_PLANE_CREDENTIALS, "true")
    rig = LoopRig.scripted(call(SEARCH, jql="open bugs"), DONE, pins=False)
    _ = Vault(rig)
    tokens = seen_by_the_client(monkeypatch)
    _ = rig.admit()

    outcome = await rig.attempt()

    assert outcome.status == "completed"
    assert tokens == [OWNER_PATH_TOKEN]
