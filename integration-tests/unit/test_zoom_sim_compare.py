"""The zoom_sim suite's comparison: what a sync must hold, and which rule explains each difference.

The oracle is a small hand-written one in the shape of the simulator's ``GET /_sim/zoom/expected``: Ann hosts a
meeting with Bob as alternative host, Cara as participant and Fay as invitee, plus a guest; Dan is inactive and hosts
one; one meeting is too old; one is held by Ann alone. No stack is needed: each test plants a difference in what
PipesHub holds and the comparison must name it, and the clean state must give none.
"""

from __future__ import annotations

import datetime

import pytest

from connectors.zoom.sim_world import KNOWN_GAPS, Scope, compare, wanted_records

ANN, BOB, CARA, FAY, GUEST, DAN = (f"{n}@acme.example" for n in ("ann", "bob", "cara", "fay", "guest", "dan"))
ANN_ID, DAN_ID = "id-ann", "id-dan"
NOW = "2026-11-20T12:00:00Z"
DAY = datetime.date(2026, 11, 20)


def _meeting(key: str, uuid: str, host: str, host_id: str, access: dict, *, days_ago: int = 3, synced: bool = True,
             alt: list | None = None, invitees: list | None = None, participants: list | None = None) -> dict:
    start = DAY - datetime.timedelta(days=days_ago)
    return {"key": key, "uuid": uuid, "host": host, "host_id": host_id, "synced": synced, "access": access,
            "start_time": f"{start.isoformat()}T09:00:00Z", "alt_hosts": alt or [], "invitees": invitees or [],
            "participants": participants or []}


ORACLE = {
    "now": NOW,
    "app_users": [ANN, BOB, CARA, FAY],
    "meetings": [
        _meeting("m1", "uuid-1", ANN, ANN_ID, {ANN: "OWNER", BOB: "READ", CARA: "READ", FAY: "READ", GUEST: "READ"},
                 alt=[BOB], invitees=[FAY], participants=[{"email": CARA}, {"email": GUEST}, {"email": ""}]),
        _meeting("m2", "uuid-2", ANN, ANN_ID, {ANN: "OWNER", BOB: "READ"}, days_ago=179, participants=[{"email": BOB}]),
        _meeting("old", "uuid-old", ANN, ANN_ID, {ANN: "OWNER"}, days_ago=181),
        _meeting("alone", "uuid-alone", ANN, ANN_ID, {ANN: "OWNER"}, synced=False),
        _meeting("dans", "uuid-dan", DAN, DAN_ID, {DAN: "OWNER", ANN: "READ"}, synced=False),
    ],
}
PEOPLE = [ANN, BOB, CARA, FAY, GUEST, DAN]


def _records(*uuids: str, group: str | None = None) -> list[dict]:
    hosts = {m["uuid"]: m["host_id"] for m in ORACLE["meetings"]}
    return [{"id": f"r-{u}", "externalRecordId": u, "externalGroupId": group or hosts[u], "recordName": u}
            for u in uuids]


def _readable(grants: dict[str, set[str]]) -> dict[str, set[str]]:
    return {p: {f"r-{u}" for u in grants.get(p, set())} for p in PEOPLE}


CLEAN_GRANTS = {ANN: {"uuid-1", "uuid-2"}, BOB: {"uuid-1", "uuid-2"}, CARA: {"uuid-1"}, FAY: {"uuid-1"}}


def _run(records: list[dict], grants: dict[str, set[str]], scope: Scope | None = None) -> list[dict]:
    return compare(ORACLE, scope or Scope(), records, _readable(grants))


def _types(problems: list[dict]) -> set[str]:
    return {p["type"] for p in problems}


def test_the_scope_is_what_zoom_lists_for_an_active_host_inside_the_window() -> None:
    assert set(wanted_records(ORACLE, Scope())) == {"uuid-1", "uuid-2"}
    assert set(wanted_records(ORACLE, Scope(window_days=100))) == {"uuid-1"}
    assert set(wanted_records(ORACLE, Scope(created={ANN: DAY - datetime.timedelta(days=10)}))) == {"uuid-1"}
    assert set(wanted_records(ORACLE, Scope(excluded={"m1"}))) == {"uuid-2"}


def test_a_clean_state_has_no_difference() -> None:
    assert _run(_records("uuid-1", "uuid-2"), CLEAN_GRANTS) == []


def test_the_guest_who_is_not_a_user_reads_nothing_by_the_connector_gate_not_as_a_leak() -> None:
    # Zoom's rule names the guest, but a person outside the active users passes no gate: not reading is correct.
    assert _run(_records("uuid-1", "uuid-2"), CLEAN_GRANTS) == []


def test_a_planted_over_share_is_a_leak_with_its_reason() -> None:
    problems = _run(_records("uuid-1", "uuid-2"), {**CLEAN_GRANTS, CARA: {"uuid-1", "uuid-2"}})
    assert [(p["type"], p["user"], p["meeting"]) for p in problems] == [("LEAK:not-named", CARA, "m2")]


def test_a_guest_who_reads_is_a_leak_outside_the_gate() -> None:
    problems = _run(_records("uuid-1", "uuid-2"), {**CLEAN_GRANTS, GUEST: {"uuid-1"}})
    assert _types(problems) == {"LEAK:outside-gate"}


@pytest.mark.parametrize("who, expected_type", [(BOB, "DENIED:alt-host"), (CARA, "DENIED:participant"),
                                                (FAY, "DENIED:invitee"), (ANN, "DENIED:host")])
def test_a_missing_grant_is_denied_with_the_role_zoom_gives(who: str, expected_type: str) -> None:
    grants = {**CLEAN_GRANTS, who: set()}
    problems = _run(_records("uuid-1", "uuid-2"), grants)
    assert expected_type in _types(problems), problems


def test_a_missing_record_is_missing_and_gives_no_reader_difference() -> None:
    problems = _run(_records("uuid-1"), {k: v - {"uuid-2"} for k, v in CLEAN_GRANTS.items()})
    assert problems == [{"type": "MISSING", "record": "uuid-2", "meeting": "m2"}]


def test_a_record_of_something_outside_the_scope_is_stale() -> None:
    for uuid in ("uuid-old", "uuid-alone", "uuid-dan", "uuid-gone"):
        records = _records("uuid-1", "uuid-2") + [{"id": "r-x", "externalRecordId": uuid, "externalGroupId": None,
                                                  "recordName": "x"}]
        assert {p["type"] for p in _run(records, CLEAN_GRANTS)} == {"STALE"}, uuid


def test_a_record_stored_twice_is_a_duplicate() -> None:
    records = _records("uuid-1", "uuid-2") + [{"id": "r-again", "externalRecordId": "uuid-1",
                                              "externalGroupId": ANN_ID, "recordName": "again"}]
    assert "DUPLICATE" in _types(_run(records, CLEAN_GRANTS))


def test_a_record_outside_its_hosts_group_is_a_parent_difference() -> None:
    records = _records("uuid-1", "uuid-2")
    records[0]["externalGroupId"] = None
    problems = _run(records, CLEAN_GRANTS)
    assert problems == [{"type": "PARENT", "record": "uuid-1", "is": None, "should": ANN_ID, "meeting": "m1"}]
    records[0]["externalGroupId"] = DAN_ID
    assert _types(_run(records, CLEAN_GRANTS)) == {"PARENT"}


def test_addresses_compare_without_case() -> None:
    grants = {k.upper(): v for k, v in CLEAN_GRANTS.items()}
    readable = {p: {f"r-{u}" for u in grants.get(p.upper(), set())} for p in PEOPLE}
    assert compare(ORACLE, Scope(), _records("uuid-1", "uuid-2"), readable) == []


def test_known_gaps_are_empty_so_every_difference_fails_a_test() -> None:
    assert KNOWN_GAPS == {}


def test_a_meeting_kept_for_a_host_who_left_is_read_by_the_others_and_not_by_the_host() -> None:
    scope = Scope(kept={"uuid-dan"})
    grants = {**CLEAN_GRANTS, ANN: {"uuid-1", "uuid-2", "uuid-dan"}}
    assert _run(_records("uuid-1", "uuid-2", "uuid-dan"), grants, scope) == []


def test_a_kept_meeting_the_host_still_reads_is_a_leak_and_one_that_went_is_missing() -> None:
    scope = Scope(kept={"uuid-dan"})
    grants = {**CLEAN_GRANTS, ANN: {"uuid-1", "uuid-2", "uuid-dan"}, DAN: {"uuid-dan"}}
    assert [p["type"] for p in _run(_records("uuid-1", "uuid-2", "uuid-dan"), grants, scope)] == ["LEAK:outside-gate"]
    assert [p["type"] for p in _run(_records("uuid-1", "uuid-2"), CLEAN_GRANTS, scope)] == ["MISSING"]


def test_a_kept_meeting_that_somebody_new_reads_is_a_leak() -> None:
    scope = Scope(kept={"uuid-dan"})
    grants = {**CLEAN_GRANTS, ANN: {"uuid-1", "uuid-2", "uuid-dan"}, FAY: {"uuid-1", "uuid-dan"}}
    assert _types(_run(_records("uuid-1", "uuid-2", "uuid-dan"), grants, scope)) == {"LEAK:not-named"}


def test_a_meeting_of_a_host_who_left_is_stale_unless_it_was_noted_as_kept() -> None:
    assert _types(_run(_records("uuid-1", "uuid-2", "uuid-dan"), {**CLEAN_GRANTS, ANN: {"uuid-1", "uuid-2", "uuid-dan"}})) \
        == {"STALE"}
