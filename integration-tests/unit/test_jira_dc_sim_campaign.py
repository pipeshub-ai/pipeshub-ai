"""The failure campaign's safety assertions, with planted failures (no stack): a lost record, a leaked grant, a grant given
by a run that did not succeed, a fault that never fired each fail the case; a success with the change unread is an
outcome the PARTIAL_SUCCESS list must name, and a listed one that stops fails too."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from connectors.jira_data_center.sim_campaign import Campaign
from connectors.jira_data_center.sim_world import JiraDcWorld, fault

BASE = {"records": {"1", "2"}, "a@x.test": {"1", "2"}, "b@x.test": {"1"}}


class _Sim:
    def __init__(self) -> None:
        self.switched = []

    def expected(self) -> dict:
        return {"ticket_index": {"1": {"key": "K-1"}, "2": {"key": "K-2"}}}

    def vendor_get(self, path: str, **params: Any):
        class R:
            def json(self_inner) -> dict:
                return {"total": 3}
        return R()

    def switches(self, **values: Any) -> dict:
        self.switched.append(values)
        return values

    def call(self, *a: Any, **k: Any) -> None:
        return None


class _World(JiraDcWorld):
    """A world whose stack is a script: ``after`` is what PipesHub holds once the run ends, ``outcome`` how it ended."""

    def __init__(self, after: dict, outcome: str, problems: list, name: str = "Edited 1", fired: bool = True) -> None:
        self.sim = _Sim()
        self.people = {"a@x.test": "u1", "b@x.test": "u2"}
        self.switches_on = {}
        self.script = (after, outcome, problems, name, fired)
        self.state = BASE

    def usernames(self, *, active: bool = True) -> list[str]:
        return ["a", "b"]

    def mutate(self, *ops: dict) -> list:
        return []

    def faults(self, *rules: dict) -> None:
        self._fault_rules = list(rules)

    def clear_faults(self) -> int:
        if not self.script[4]:
            raise AssertionError("no injected fault fired, so the test proves nothing")
        return 1

    async def sync(self, *, full: bool = False) -> str:
        self.state = self.script[0]
        return self.script[1]

    async def holdings(self) -> dict:
        return self.state

    async def differences(self) -> list:
        return self.script[2]

    async def record_for(self, external_id: str) -> dict:
        return {"recordName": f"[K-1] {self.script[3]}"}


def run(world: _World, label: str = "GET /x") -> Campaign:
    c = Campaign(world)
    asyncio.run(c.case(label, "500", fault(r"^/x$", 500)))
    return c


def test_a_run_that_fails_and_changes_nothing_is_fine() -> None:
    c = run(_World(BASE, "failed", []))
    assert c.unread and not c.partial


def test_a_planted_loss_fails_the_case_whatever_the_outcome() -> None:
    lost = {**BASE, "records": {"1"}}
    for outcome in ("failed", "succeeded"):
        with pytest.raises(AssertionError, match="lost records or access"):
            run(_World(lost, outcome, []))
    with pytest.raises(AssertionError, match="lost records or access"):
        run(_World({**BASE, "b@x.test": set()}, "failed", []))


def test_a_planted_leak_in_a_run_that_reported_success_fails_the_case() -> None:
    with pytest.raises(AssertionError, match="reported success but PipesHub differs"):
        run(_World(BASE, "succeeded", [{"type": "LEAK:other", "record": "2", "user": "b@x.test"}]))
    with pytest.raises(AssertionError, match="reported success but PipesHub differs"):
        run(_World(BASE, "succeeded", [{"type": "STALE", "record": "9"}]))


def test_access_given_by_a_run_that_did_not_succeed_fails_the_case() -> None:
    gained = {**BASE, "b@x.test": {"1", "2"}}
    with pytest.raises(AssertionError, match="did not succeed gave access"):
        run(_World(gained, "failed", [{"type": "LEAK:other", "record": "2", "user": "b@x.test"}]))
    with pytest.raises(AssertionError, match="did not succeed gave access"):
        run(_World(BASE, "failed", [{"type": "DUPLICATE", "record": "1"}]))
    # Access Jira itself gives (no leak against its readers) is what a failed run may write.
    assert run(_World(gained, "failed", [])).unread


def test_a_fault_that_never_fired_fails_the_case() -> None:
    with pytest.raises(AssertionError, match="no injected fault fired"):
        run(_World(BASE, "failed", [], fired=False))


def test_a_success_with_the_change_unread_is_an_outcome_the_list_must_name() -> None:
    unread = _World(BASE, "succeeded", [{"type": "DENIED:other", "record": "2", "user": "b@x.test"}])
    c = run(unread)
    assert c.partial == {("GET /x", "500")}
    with pytest.raises(AssertionError, match="not in PARTIAL_SUCCESS"):
        c.assert_outcomes(set(), "x")
    c.assert_outcomes({("GET /x", "500")}, "x")


def test_a_success_whose_edit_was_not_read_is_partial_too() -> None:
    c = run(_World(BASE, "succeeded", [], name="the old name"))
    assert c.partial == {("GET /x", "500")}


def test_a_listed_pair_that_stops_reporting_success_fails_the_outcome() -> None:
    c = run(_World(BASE, "failed", []))
    with pytest.raises(AssertionError, match="no longer doing it"):
        c.assert_outcomes({("GET /x", "500")}, "x")


def test_a_clean_success_is_neither_partial_nor_unread() -> None:
    c = run(_World(BASE, "succeeded", []))
    assert not c.partial and not c.unread and c.before is None


def test_access_lost_in_a_listed_fail_closed_pair_is_excused_but_a_lost_record_never_is() -> None:
    from connectors.jira_data_center.sim_campaign import FAILS_CLOSED

    pair = next(iter(FAILS_CLOSED))
    closed = {**BASE, "a@x.test": set(), "b@x.test": set()}
    w = _World(closed, "succeeded", [])
    c = Campaign(w)

    async def go(world: _World, endpoint: str, kind: str) -> None:
        await Campaign(world).case(endpoint, kind, fault(r"^/x$", 401))

    asyncio.run(go(w, *pair))
    cc = Campaign(_World(closed, "succeeded", []))
    asyncio.run(cc.case(*pair, fault(r"^/x$", 401)))
    assert cc.closed_seen == {pair}
    cc.assert_outcomes(set(), "x")
    quiet = Campaign(_World(BASE, "succeeded", []))
    asyncio.run(quiet.case(*pair, fault(r"^/x$", 401)))
    with pytest.raises(AssertionError, match="no longer take access away"):
        quiet.assert_outcomes(set(), "x")
    with pytest.raises(AssertionError, match="lost records or access"):
        asyncio.run(go(_World(closed, "succeeded", []), "GET /somewhere/else", pair[1]))
    with pytest.raises(AssertionError, match="lost records or access"):
        asyncio.run(go(_World({**closed, "records": {"1"}}, "succeeded", []), *pair))
    assert c
