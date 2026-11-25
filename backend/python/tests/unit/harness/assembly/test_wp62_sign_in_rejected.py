"""WP62-TS: a toolset whose vendor rejects the saved sign-in is withdrawn like one never connected.

A Codex child was granted 28 GitHub tools whose saved credential
GitHub answered "Bad credentials"; six calls went to GitHub and failed the same way. Over ``RuntimeAssembler``
with the assembly test world (a run granted a Jira toolset).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.agents.actions.util.sign_in_rejected import (
    note_sign_in_rejected,
    watching_sign_in,
)
from app.harness.credentials.read_through import record_digest
from app.harness.credentials.sign_in_rejection import (
    withdraw_rejected_toolset_credential,
)
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext

from . import world as w
from .test_assembler import JIRA_SEARCH, _build

if TYPE_CHECKING:
    from collections.abc import Iterator

_CTX = ToolContext("run-1", w.ORG, "t1.0", "att-1", "k")
_JIRA_KEY = f"/services/toolsets/{w.JIRA}/{w.ACTOR}"
_VENDOR_SAID = "Jira did not accept the saved sign-in. Jira said: Bad credentials. Reconnect Jira under Your actions."


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with w.deployment():
        yield


class _Vendor:
    """Jira's answers: rejects the sign-in while ``rejecting``; counts the calls that reached it."""

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


async def _jira(runtime: Any, call_id: str) -> Any:  # noqa: ANN401
    return await runtime.tools.execute(ToolCall(id=call_id, name="jira__search_issues", arguments={"jql": call_id}), _CTX)


async def test_wp62_ts_1_a_rejected_sign_in_withdraws_the_stored_record(vendor: _Vendor) -> None:
    services = w.Services()
    runtime = await _build(services, tool_ids=[JIRA_SEARCH])
    first = await _jira(runtime, "c1")
    assert first.status == "error" and "did not accept the saved sign-in" in str(first.content), "the vendor's words"
    stored = await services.config.get_config(_JIRA_KEY)
    assert stored["isAuthenticated"] is False and stored["deauthReason"] == "sign_in_rejected"
    assert stored["auth"] == {"token": "secret-jira-token"}, "only withdrawn: the record is kept for the person"


async def test_wp62_ts_2_later_calls_of_the_run_are_refused_without_asking_the_vendor(vendor: _Vendor) -> None:
    services = w.Services()
    runtime = await _build(services, tool_ids=[JIRA_SEARCH])
    _ = await _jira(runtime, "c1")
    again = await _jira(runtime, "c2")
    assert vendor.calls == 1, "a known-bad credential is not sent to the vendor again"
    assert again.status == "error" and again.error_code == "auth"
    assert "sign_in_rejected" in str(again.content) and "/workspace/actions" in str(again.content)
    assert "secret-jira-token" not in str(again.content)


async def test_wp62_ts_3_the_next_run_treats_it_as_not_connected(
    vendor: _Vendor, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.harness.assembly import assembler

    services = w.Services()
    _ = await _jira(await _build(services, tool_ids=[JIRA_SEARCH]), "c1")
    loaded: list[Any] = []
    original = assembler.load_run_tools

    async def spy(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        tools = await original(*args, **kwargs)
        loaded.append(tools)
        return tools

    monkeypatch.setattr(assembler, "load_run_tools", spy)
    calls_before = vendor.calls
    nxt = await _build(services, tool_ids=[JIRA_SEARCH])
    assert loaded[0].catalog.needs_authentication() == {"jira__search_issues"}, "listed with a connect prompt"
    refused = await _jira(nxt, "c2")
    assert refused.status == "error" and refused.error_code == "auth" and "/workspace/actions" in str(refused.content)
    assert vendor.calls == calls_before


async def test_wp62_ts_4_reconnecting_clears_it(vendor: _Vendor) -> None:
    services = w.Services()
    _ = await _jira(await _build(services, tool_ids=[JIRA_SEARCH]), "c1")
    # The person reconnects: the OAuth callback (or the saved token) writes a new authenticated record.
    services.config.put(_JIRA_KEY, {"isAuthenticated": True, "auth": {"token": "new-token"}})
    vendor.rejecting = False
    fresh = await _build(services, tool_ids=[JIRA_SEARCH])
    assert (await _jira(fresh, "c2")).status == "ok"


async def test_wp62_ts_5_a_record_changed_since_the_client_was_built_is_not_withdrawn(vendor: _Vendor) -> None:
    services = w.Services()
    vendor.rejecting = False
    runtime = await _build(services, tool_ids=[JIRA_SEARCH])
    assert (await _jira(runtime, "c1")).status == "ok"
    # Reconnected (or refreshed) while the run held its client: the rejection is about the old credential.
    services.config.put(_JIRA_KEY, {"isAuthenticated": True, "auth": {"token": "new-token"}})
    vendor.rejecting = True
    _ = await _jira(runtime, "c2")
    stored = await services.config.get_config(_JIRA_KEY)
    assert stored["isAuthenticated"] is True and "deauthReason" not in stored
    vendor.rejecting = False
    assert (await _jira(runtime, "c3")).status == "ok", "the next call builds a client from the current record"


async def test_wp62_ts_6_an_expired_token_the_refresher_can_renew_is_not_withdrawn() -> None:
    from tests.support.config_service import FakeConfigurationService

    record = {
        "isAuthenticated": True,
        "credentials": {"access_token": "a", "refresh_token": "r", "expires_in": 60, "created_at": "2026-01-01T00:00:00"},
    }
    config = FakeConfigurationService({_JIRA_KEY: record})
    withdrawn = await withdraw_rejected_toolset_credential(
        config, org_id=w.ORG, instance_id=w.JIRA, owner_id=w.ACTOR, used_digest=record_digest(record),
        clock_ms=lambda: 1_900_000_000_000,
    )
    assert withdrawn is False
    assert (await config.get_config(_JIRA_KEY))["isAuthenticated"] is True
    # Without a refresh token nothing renews it: the person must reconnect.
    plain = {"isAuthenticated": True, "credentials": {"access_token": "a"}}
    config.put(_JIRA_KEY, plain)
    assert await withdraw_rejected_toolset_credential(
        config, org_id=w.ORG, instance_id=w.JIRA, owner_id=w.ACTOR, used_digest=record_digest(plain)
    )


def test_wp62_ts_7_github_notes_a_401_and_nothing_else() -> None:
    from types import SimpleNamespace

    from app.agents.actions.github.github import _github_error_message

    def response(status: int) -> Any:  # noqa: ANN401
        return SimpleNamespace(status_code=status, api_message="Bad credentials", is_rate_limited=False, retry_after_seconds=None)

    with watching_sign_in() as rejected:
        _ = _github_error_message(response(401), "list the issues")
    assert rejected
    for status in (403, 404, 500):
        with watching_sign_in() as other:
            _ = _github_error_message(response(status), "list the issues")
        assert not other, status


def test_wp62_ts_8_outside_a_watched_call_a_note_does_nothing() -> None:
    note_sign_in_rejected()
    with watching_sign_in() as rejected:
        pass
    assert not rejected
