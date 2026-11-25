"""WP-64 B (after WP62-TS): a toolset whose saved sign-in the vendor rejects pauses the running attempt for a connect.

Since WP-62 the rejected credential is withdrawn and every later call of the toolset in the same attempt is refused
with "reconnect, then ask again", so a long task lost its thread to one expired token. Now a later call waits on the
connect card instead (G-12's connect wait, kind ``connect``): the person reconnects, says so, and the attempt that
follows assembles the toolset with the new credential and runs the call once. Over ``RuntimeAssembler`` with the
assembly test world (a run granted a Jira toolset), and the Tool Gateway for the engine path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.agents.actions.util.sign_in_rejected import note_sign_in_rejected
from harness_core.interactions.connect import ConnectNeed
from harness_core.interactions.connect_wait import (
    AUTH_REQUIRED,
    BridgeConnectWait,
    connect_interaction_id,
)
from harness_core.interactions.ports import RunBinding
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    build_endpoint,
    claims_for,
)
from harness_core.testing.engines.gateway import FakeInteractionBridge

from . import world as w
from .test_assembler import JIRA_SEARCH, _build

if TYPE_CHECKING:
    from collections.abc import Iterator

_CTX = ToolContext("run-1", w.ORG, "t1.0", "att-1", "k")
_JIRA_KEY = f"/services/toolsets/{w.JIRA}/{w.ACTOR}"
_VENDOR_SAID = "Jira did not accept the saved sign-in. Jira said: Bad credentials. Reconnect Jira under Your actions."
_SEARCH = ToolCall(id="c2", name="jira__search_issues", arguments={"jql": "c2"})


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with w.deployment():
        yield


class _Vendor:
    def __init__(self) -> None:
        self.rejecting = True
        self.calls = 0


@pytest.fixture
def vendor(monkeypatch: pytest.MonkeyPatch) -> _Vendor:
    held = _Vendor()

    async def search_issues(self: object, jql: str) -> Any:  # noqa: ANN401
        held.calls += 1
        if held.rejecting:
            note_sign_in_rejected()
            return False, _VENDOR_SAID
        return f'{{"issues": ["{jql}"]}}'

    w.replace_tool(monkeypatch, w.FakeJira, "search_issues", search_issues)
    return held


@pytest.fixture
def catalogs(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from app.harness.assembly import assembler

    loaded: list[Any] = []
    original = assembler.load_run_tools

    async def spy(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        tools = await original(*args, **kwargs)
        loaded.append(tools.catalog)
        return tools

    monkeypatch.setattr(assembler, "load_run_tools", spy)
    return loaded


async def test_wp64_tsp_1_after_a_rejected_sign_in_a_later_call_waits_for_a_connect(
    vendor: _Vendor, catalogs: list[Any]
) -> None:
    runtime = await _build(w.Services(), tool_ids=[JIRA_SEARCH])
    gate = catalogs[0].connect_gate()
    assert gate.need(_SEARCH) is None, "connected: nothing to wait on"
    first = await runtime.tools.execute(ToolCall(id="c1", name="jira__search_issues", arguments={"jql": "c1"}), _CTX)
    assert first.status == "error" and "did not accept the saved sign-in" in str(first.content)
    need = gate.need(_SEARCH)
    assert need == ConnectNeed("Jira", "toolset", w.JIRA, "sign_in_rejected"), "the card names the toolset to reconnect"
    assert "/workspace/actions" in gate.declined(_SEARCH), "declined: the next step in words"
    assert vendor.calls == 1


async def test_wp64_tsp_2_a_record_changed_since_the_client_was_built_waits_on_nothing(
    vendor: _Vendor, catalogs: list[Any]
) -> None:
    services = w.Services()
    vendor.rejecting = False
    runtime = await _build(services, tool_ids=[JIRA_SEARCH])
    assert (await runtime.tools.execute(ToolCall(id="c1", name="jira__search_issues", arguments={"jql": "c1"}), _CTX)).status == "ok"
    services.config.put(_JIRA_KEY, {"isAuthenticated": True, "auth": {"token": "new-token"}})
    vendor.rejecting = True
    _ = await runtime.tools.execute(ToolCall(id="c2", name="jira__search_issues", arguments={"jql": "c2"}), _CTX)
    # Not withdrawn (WP62-TS-5): the next call builds a client from the current record, so it does not wait.
    assert catalogs[0].connect_gate().need(_SEARCH) is None


async def test_wp64_tsp_3_reconnected_the_attempt_that_follows_runs_the_call(
    vendor: _Vendor, catalogs: list[Any]
) -> None:
    services = w.Services()
    _ = await (await _build(services, tool_ids=[JIRA_SEARCH])).tools.execute(
        ToolCall(id="c1", name="jira__search_issues", arguments={"jql": "c1"}), _CTX
    )
    services.config.put(_JIRA_KEY, {"isAuthenticated": True, "auth": {"token": "new-token"}})
    vendor.rejecting = False
    resumed = await _build(services, tool_ids=[JIRA_SEARCH])
    assert catalogs[-1].connect_gate().need(_SEARCH) is None
    assert (await resumed.tools.execute(_SEARCH, _CTX)).status == "ok"


class _RejectedGate:
    """The engine's attempt after its toolset's sign-in was rejected: connected at assembly, so not masked."""

    def need(self, call: ToolCall) -> ConnectNeed | None:
        return ConnectNeed("Jira", "toolset", "inst_jira_1") if call.name == "lookup" else None

    def declined(self, call: ToolCall) -> str:
        return "interaction_denied: Jira was not connected, so this call was not made"


async def test_wp64_tsp_4_the_engine_path_defers_a_call_of_a_rejected_toolset_on_a_connect_wait() -> None:
    bridge = FakeInteractionBridge()
    world = build_endpoint(
        needs_authentication=frozenset(),
        connect=BridgeConnectWait(bridge, RunBinding("org-1", "ses-1", "run-1"), _RejectedGate()),
    )
    result = await world.endpoint.call_tool(claims_for(BINDING), "lookup", {"q": "x"})
    ix = connect_interaction_id("run-1", "lookup", {"q": "x"})
    assert (result.disposition, result.reason_code, result.interaction_id) == ("deferred", AUTH_REQUIRED, ix)
    assert world.runs == [], "nothing reached the vendor with the rejected sign-in"
    plain = await world.endpoint.call_tool(claims_for(BINDING), "search", {"q": "x"})
    assert plain.disposition == "executed"


def test_wp64_tsp_5_a_sign_in_that_came_or_went_is_no_capability_change() -> None:
    """A withdrawn GitHub sign-in made every later attempt a capability mismatch, three backoffs and
    ``capability_lost``. A tool moving to or from waiting on a connect keeps the run; any other change still stops it."""
    from harness_core.kernel.durable.capability import NEEDS_CONNECT, capability_changes

    before = {"tool:jira@i/search": "sha256:a", "tool:web@x/fetch": "sha256:b"}
    assert capability_changes(before, {**before, "tool:jira@i/search": NEEDS_CONNECT}) == []
    assert capability_changes({**before, "tool:jira@i/search": NEEDS_CONNECT}, before) == []
    # A run recorded before the mark kept no key for a masked tool.
    assert capability_changes({"tool:web@x/fetch": "sha256:b"}, {**before, "tool:jira@i/search": NEEDS_CONNECT}) == []
    assert capability_changes(before, {**before, "tool:jira@i/search": "sha256:c"}) == ["tool:jira@i/search"]
    assert capability_changes(before, {"tool:web@x/fetch": "sha256:b"}) == ["tool:jira@i/search"]


async def test_wp64_tsp_6_a_withdrawn_sign_in_marks_its_tools_as_waiting_for_a_connect(
    vendor: _Vendor, catalogs: list[Any]
) -> None:
    from harness_core.kernel.durable.capability import NEEDS_CONNECT

    services = w.Services()
    _ = await (await _build(services, tool_ids=[JIRA_SEARCH])).tools.execute(
        ToolCall(id="c1", name="jira__search_issues", arguments={"jql": "c1"}), _CTX
    )
    _ = await _build(services, tool_ids=[JIRA_SEARCH])
    assert catalogs[-1].granted_digests()[f"tool:{JIRA_SEARCH}"] == NEEDS_CONNECT


async def test_wp64_tsp_7_the_assistant_keeps_a_withdrawn_toolset_its_run_holds() -> None:
    """The assistant's toolsets are the actor's authenticated ones, so a withdrawn GitHub fell out of
    them and the pin's grant no longer resolved (``tool_instance_missing``). The harness asks for the withdrawn ones
    too; they come back unauthenticated, with no credential, and today's chat never sees them."""
    from unittest.mock import MagicMock

    from app.api.routes.toolsets import get_authenticated_toolsets

    records = {
        "/services/toolset-instances": [
            {"_id": "gh", "orgId": "o1", "toolsetType": "github"},
            {"_id": "never", "orgId": "o1", "toolsetType": "jira"},
        ],
    }

    class Config:
        async def get_config(self, path: str, default: object = None, **_options: object) -> object:
            if "toolset-instances" in path:
                return records["/services/toolset-instances"]
            if "/gh/" in path or path.endswith("/gh") or "gh" in path.split("/"):
                return {"isAuthenticated": False, "deauthReason": "sign_in_rejected", "deauthAt": 1, "auth": {"token": "t"}}
            return None

    registry = MagicMock()
    registry.get_toolset_metadata.return_value = {"display_name": "GitHub", "tools": [{"name": "list_issues"}]}
    plain, _ = await get_authenticated_toolsets("u1", "o1", Config(), registry)  # type: ignore[arg-type]
    assert plain == [], "today's chat: only authenticated toolsets"
    kept, auth = await get_authenticated_toolsets("u1", "o1", Config(), registry, include_withdrawn=True)  # type: ignore[arg-type]
    assert [t["instanceId"] for t in kept] == ["gh"] and kept[0]["isAuthenticated"] is False
    assert auth == {}, "no credential of a withdrawn sign-in is handed on"


async def test_wp64_tsp_8_my_actions_says_why_a_sign_in_is_gone_and_since_when() -> None:
    """The admin's GitHub action showed as not connected with no reason. The list now says the service
    rejected the saved sign-in and when (the record's ``deauthReason``/``deauthAt``), never the credential."""
    from unittest.mock import AsyncMock, patch

    from app.api.routes.toolsets import get_my_toolsets
    from tests.unit.api.routes.test_toolsets import _make_registry, _make_request

    records = {
        "gh": {"isAuthenticated": False, "deauthReason": "sign_in_rejected", "deauthAt": 1_762_043_359_000, "auth": {"token": "t"}},
        "jira": {"isAuthenticated": True, "auth": {"token": "u"}},
    }
    config = AsyncMock()
    config.get_config = AsyncMock(side_effect=lambda path, default=None: next((v for k, v in records.items() if f"/{k}/" in path), None))
    request = _make_request()
    request.app.state.toolset_registry = _make_registry("github")
    instances = [{"_id": "gh", "orgId": "o1", "toolsetType": "github"}, {"_id": "jira", "orgId": "o1", "toolsetType": "github"}]
    with patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
        result = await get_my_toolsets(
            request, search=None, page=1, limit=20, include_registry=False, toolset_type=None, auth_status=None,
            scope="org", config_service=config,
        )
    rows = {row["instanceId"]: row for row in result["toolsets"]}
    assert rows["gh"]["deauthReason"] == "sign_in_rejected" and rows["gh"]["deauthAt"] == 1_762_043_359_000
    assert "deauthReason" not in rows["jira"]
