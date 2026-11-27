"""The jira_dc_sim suite's comparison: what a sync must hold, and which Jira rule explains each difference.

The oracle is a small hand-written one in the shape of the simulator's ``GET /_sim/jiradc/expected?ids=true`` and
``GET /_sim/jiradc/access?email=..&ids=true``: two projects (7 and 8); in project 7 a ticket with an attachment, a ticket
with a sub-task and a ticket under a security level; in project 8 one ticket; five people: one who reads everything, one
who reads project 7 but not the secured ticket, one who reads project 8 only, a deactivated one and an unlicensed one.
"""

from __future__ import annotations

import pytest
from connectors.jira_data_center.sim_world import (
    KNOWN_GAPS,
    Scope,
    _date_skip,
    _project_scope,
    compare,
    date_filter,
    merge_filters,
    project_filter,
    wanted_records,
)

ALL, P7, P8, GONE, NOLIC = "all@x.test", "p7@x.test", "p8@x.test", "gone@x.test", "nolic@x.test"

EXPECTED = {
    "ticket_index": {
        "101": {"key": "AAA-1", "project_id": 7, "parent_id": None, "files": ["900"]},
        "102": {"key": "AAA-2", "project_id": 7, "parent_id": None, "files": []},
        "103": {"key": "AAA-3", "project_id": 7, "parent_id": 102, "files": []},
        "104": {"key": "AAA-4", "project_id": 7, "parent_id": None, "files": ["901"], "level": 3},   # secured
        "201": {"key": "BBB-1", "project_id": 8, "parent_id": None, "files": []},
    },
}


def _acc(ids: list[int], *, active: bool = True, licensed: bool = True) -> dict:
    return {"active": active, "licensed": licensed, "jira_ids": ids}


ACCESS = {
    ALL: _acc([101, 102, 103, 104, 201]),
    P7: _acc([101, 102, 103]),
    P8: _acc([201]),
    GONE: _acc([101, 102, 103, 104, 201], active=False),
    NOLIC: _acc([101, 102, 103, 104, 201], licensed=False),
}


def _records(scope: Scope) -> list[dict]:
    return [{"id": f"r{ext}", "externalRecordId": ext, "externalGroupId": want.group, "recordName": ext}
            for ext, want in wanted_records(EXPECTED, ACCESS, scope).items()]


def _faithful(scope: Scope) -> dict[str, set[str]]:
    wanted = wanted_records(EXPECTED, ACCESS, scope)
    return {email: {f"r{ext}" for ext, w in wanted.items() if email in w.readers} for email in ACCESS}


def _types(problems: list[dict]) -> dict[str, set[tuple[str, str]]]:
    out: dict[str, set[tuple[str, str]]] = {}
    for p in problems:
        out.setdefault(p["type"], set()).add((p.get("record", ""), p.get("user", "")))
    return out


def test_the_scope_is_every_ticket_and_attachment_with_the_files_under_their_project() -> None:
    wanted = wanted_records(EXPECTED, ACCESS, Scope())
    assert set(wanted) == {"101", "102", "103", "104", "201", "attachment_900", "attachment_901"}
    assert wanted["attachment_900"].group == "7" and wanted["201"].group == "8"


def test_only_active_licensed_people_who_jira_shows_the_ticket_read_it() -> None:
    wanted = wanted_records(EXPECTED, ACCESS, Scope())
    assert wanted["104"].readers == {ALL}
    assert wanted["attachment_901"].readers == {ALL}
    assert wanted["101"].readers == {ALL, P7}
    assert wanted["201"].readers == {ALL, P8}


def test_a_project_filter_narrows_the_scope_both_ways() -> None:
    assert _project_scope(project_filter("in", ["AAA"])) == ({"AAA"}, None)
    assert _project_scope(project_filter("not_in", ["AAA"])) == (None, {"AAA"})
    assert _project_scope({"sync": {"values": {}}}) == (None, None)
    assert set(wanted_records(EXPECTED, ACCESS, Scope(projects_in={"BBB"}))) == {"201"}
    assert set(wanted_records(EXPECTED, ACCESS, Scope(projects_out={"BBB"}))) == {
        "101", "102", "103", "104", "attachment_900", "attachment_901"}
    assert set(wanted_records(EXPECTED, ACCESS, Scope(skip={"101", "104"}))) == {"102", "103", "201"}


def test_a_leveled_ticket_nobody_reads_is_the_named_leveled_gap_not_a_plain_denial() -> None:
    scope = Scope()
    readable = _faithful(scope)
    readable[ALL] -= {"r104", "rattachment_901"}
    found = _types(compare(EXPECTED, scope, _records(scope), readable, ACCESS))
    assert found == {"DENIED:leveled-issue": {("104", ALL), ("attachment_901", ALL)}}
    assert "DENIED:leveled-issue" in KNOWN_GAPS and KNOWN_GAPS["DENIED:leveled-issue"] == 6114


def test_a_person_a_scheme_names_by_user_who_cannot_read_is_the_named_user_holder_gap_and_only_that_person() -> None:
    expected = {**EXPECTED, "browse_user_holders": {"8": [P8]}}
    scope = Scope()
    readable = _faithful(scope)
    readable[P8].discard("r201")
    readable[ALL].discard("r201")
    found = _types(compare(expected, scope, _records(scope), readable, ACCESS))
    assert found == {"DENIED:user-holder-by-name": {("201", P8)}, "DENIED:other": {("201", ALL)}}
    assert KNOWN_GAPS["DENIED:user-holder-by-name"] == 6114


def test_a_connector_that_follows_jira_differs_in_nothing() -> None:
    scope = Scope()
    assert compare(EXPECTED, scope, _records(scope), _faithful(scope), ACCESS) == []


def test_every_known_gap_names_an_open_pull_request() -> None:
    assert set(KNOWN_GAPS) == {"DENIED:user-holder-by-name", "DENIED:leveled-issue"}
    assert all(isinstance(pr, int) and pr > 0 for pr in KNOWN_GAPS.values())


def test_an_over_share_of_a_secured_ticket_is_a_leak() -> None:
    scope = Scope()
    readable = _faithful(scope)
    readable[P7] |= {"r104", "rattachment_901"}      # the project's access without the security level
    found = _types(compare(EXPECTED, scope, _records(scope), readable, ACCESS))
    assert found == {"LEAK:other": {("104", P7), ("attachment_901", P7)}}


def test_a_missing_grant_is_a_denial() -> None:
    scope = Scope()
    readable = _faithful(scope)
    readable[P8].discard("r201")
    assert _types(compare(EXPECTED, scope, _records(scope), readable, ACCESS)) == {"DENIED:other": {("201", P8)}}


def test_a_deactivated_person_who_still_reads_is_a_leak_and_so_is_an_unlicensed_one() -> None:
    scope = Scope()
    readable = _faithful(scope)
    readable[GONE] = {"r101"}
    readable[NOLIC] = {"r102"}
    assert _types(compare(EXPECTED, scope, _records(scope), readable, ACCESS)) == {
        "LEAK:inactive-user": {("101", GONE)}, "LEAK:no-product-access": {("102", NOLIC)}}


def test_a_person_with_no_jira_account_who_reads_is_a_leak_and_one_who_cannot_is_fine() -> None:
    scope = Scope()
    readable = _faithful(scope)
    readable["stranger@x.test"] = set()
    assert compare(EXPECTED, scope, _records(scope), readable, ACCESS) == []
    readable["stranger@x.test"] = {"r101"}
    assert _types(compare(EXPECTED, scope, _records(scope), readable, ACCESS)) == {
        "LEAK:not-a-jira-user": {("101", "stranger@x.test")}}


def test_records_missing_stale_duplicated_or_misfiled_are_reported() -> None:
    scope = Scope()
    records = [r for r in _records(scope) if r["externalRecordId"] != "attachment_900"]       # missing
    records.append({"id": "rX", "externalRecordId": "999", "externalGroupId": "7", "recordName": "gone"})  # stale
    records.append(dict(records[0], id="r-copy"))                                              # a second record
    records[1] = dict(records[1], externalGroupId="8")                                         # wrong project
    found = _types(compare(EXPECTED, scope, records, _faithful(scope), ACCESS))
    assert set(found) == {"MISSING", "STALE", "DUPLICATE", "PARENT"}
    assert found["MISSING"] == {("attachment_900", "")} and found["STALE"] == {("999", "")}


@pytest.mark.parametrize("who", [ALL, P7, P8])
def test_a_connector_that_grants_nothing_is_a_denial_for_every_reader(who: str) -> None:
    scope = Scope()
    readable = {email: set() for email in ACCESS}
    found = _types(compare(EXPECTED, scope, _records(scope), readable, ACCESS))
    assert any(user == who for pairs in found.values() for _rec, user in pairs)


def test_date_filters_leave_out_tickets_outside_the_window() -> None:
    index = {"ticket_index": {"1": {"created_ms": 100, "updated_ms": 500}, "2": {"created_ms": 200, "updated_ms": 400},
                              "3": {"created_ms": 300, "updated_ms": 300}}}
    assert _date_skip(index, None) == set()
    assert _date_skip(index, date_filter("created", "is_after", 150)) == {"1"}
    assert _date_skip(index, date_filter("created", "is_before", None, 250)) == {"3"}
    assert _date_skip(index, date_filter("modified", "is_between", 350, 450)) == {"1", "3"}
    both = merge_filters(date_filter("created", "is_after", 150), date_filter("modified", "is_after", 350))
    assert _date_skip(index, both) == {"1", "3"} | {"1"}


# ---- the guards of the world (no stack): a fault that never fired, and a request the simulator does not model ----------

import asyncio

from connectors.jira_data_center.sim_world import JiraDcWorld, fault


class _FakeSim:
    def __init__(self, fired: list, unmodeled: list) -> None:
        self.fired, self.unmodeled, self.deleted = fired, unmodeled, 0

    def call(self, method: str, path: str, body: object = None, params: dict | None = None):
        if path == "/_sim/faults/log":
            return self.fired
        if path == "/_sim/faults" and method == "DELETE":
            self.deleted += 1
            return None
        if path == "/_sim/jiradc/unmodeled":
            return {"unmodeled": self.unmodeled}
        return None

    def expected(self) -> dict:
        return EXPECTED

    def users(self) -> list:
        return [{"username": "svc-pipeshub-sync", "email": ALL}]

    def access(self, email: str) -> dict:
        return ACCESS[email]


class _FakeGraph:
    async def fetch_records_by_type(self, connector_id: str, record_type: str, *, scoped: bool = False) -> list:
        return []

    async def filter_accessible_record_ids(self, ids: list, user_id: str, org_id: str) -> list:
        return []


class _FakeClient:
    org_id = "org"


def _world(sim: _FakeSim) -> JiraDcWorld:
    return JiraDcWorld(sim, _FakeClient(), _FakeGraph(), {ALL: "u1"})


def test_a_fault_that_never_fired_fails_its_test() -> None:
    w = _world(_FakeSim(fired=[], unmodeled=[]))
    w._fault_rules = [fault(r"^/rest/api/2/myself$", 503)]
    with pytest.raises(AssertionError, match="no injected fault fired"):
        w.clear_faults()


def test_a_fault_that_fired_and_a_test_with_no_fault_pass_and_the_faults_are_removed() -> None:
    sim = _FakeSim(fired=[{"effect": "status"}], unmodeled=[])
    w = _world(sim)
    w._fault_rules = [fault(r"^/rest/api/2/myself$", 503)]
    w.clear_faults()
    w.clear_faults()
    assert sim.deleted == 2 and w._fault_rules == []


def test_a_request_the_simulator_does_not_model_is_a_difference_that_fails_the_comparison(monkeypatch) -> None:
    from connectors.jira_data_center import sim_world

    monkeypatch.setattr(sim_world, "SETTLE_TIMEOUT_SEC", 0)
    w = _world(_FakeSim(fired=[], unmodeled=[{"method": "GET", "path": "/rest/api/2/dashboard", "hits": 1}]))
    problems = asyncio.run(w.differences())
    assert {"type": "SIM_UNMODELED", "method": "GET", "path": "/rest/api/2/dashboard", "hits": 1} in problems
    with pytest.raises(AssertionError, match="SIM_UNMODELED"):
        asyncio.run(w.assert_matches_source("a sync that called an unmodelled route"))
