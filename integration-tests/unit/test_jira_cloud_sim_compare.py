"""The jira_cloud_sim suite's comparison: what a sync must hold, and which Jira rule explains each difference.

The oracle is a small hand-written one in the shape of ``GET /_sim/jira/expected?ids=true`` and
``GET /_sim/jira/access?email=..&ids=true``: two projects (7 and 8); in project 7 a ticket with a file and two comments
(one restricted), a ticket with a sub-task and a ticket under a security level; in project 8 one ticket; five people: one
who reads everything, one who reads project 7 but not the secured ticket, one who reads project 8 only, a deactivated one
and an unlicensed one. Negative controls: a planted over-share, a missing grant, a missing, a stale, a duplicate and a
misfiled record each fail the comparison.
"""

from __future__ import annotations

import pytest
from connectors.jira.cloud_sim_world import (
    Scope,
    _date_skip,
    _project_scope,
    compare,
    date_filter,
    fault_answers,
    merge_filters,
    project_filter,
    unmodeled_requests,
    wanted_records,
)

ALL, P7, P8, GONE, NOLIC = "all@x.test", "p7@x.test", "p8@x.test", "gone@x.test", "nolic@x.test"

EXPECTED = {
    "ticket_index": {
        "101": {"key": "AAA-1", "project_id": 7, "files": [{"id": "900"}, {"id": "902", "clash": True}],
                "comments": [{"id": "50", "restricted": False}, {"id": "51", "restricted": True}],
                "created_ms": 1000, "updated_ms": 5000},
        "102": {"key": "AAA-2", "project_id": 7, "files": [], "comments": [], "created_ms": 2000, "updated_ms": 2000},
        "103": {"key": "AAA-3", "project_id": 7, "parent_id": 102, "files": [], "comments": [], "created_ms": 3000, "updated_ms": 3000},
        "104": {"key": "AAA-4", "project_id": 7, "files": [{"id": "901"}], "comments": [], "level": 3,
                "created_ms": 4000, "updated_ms": 4000},
        "201": {"key": "BBB-1", "project_id": 8, "files": [], "comments": [], "created_ms": 5000, "updated_ms": 9000},
    },
}
RESTRICTED_TO = {"51": {ALL}}


def _acc(ids: list[int], *, active: bool = True, licensed: bool = True) -> dict:
    return {"active": active, "licensed": licensed, "jira_ids": ids}


ACCESS = {
    ALL: _acc([101, 102, 103, 104, 201]),
    P7: _acc([101, 102, 103]),
    P8: _acc([201]),
    GONE: _acc([101, 102, 103, 104, 201], active=False),
    NOLIC: _acc([101, 102, 103, 104, 201], licensed=False),
}


def _wanted(scope: Scope | None = None, **kw):
    return wanted_records(EXPECTED, ACCESS, scope or Scope(), RESTRICTED_TO, **kw)


def _records(wanted) -> list[dict]:
    return [{"id": f"r{ext}", "externalRecordId": ext, "externalGroupId": w.group, "recordName": ext}
            for ext, w in wanted.items() if not w.clash]


def _faithful(wanted) -> dict[str, set[str]]:
    return {email: {f"r{ext}" for ext, w in wanted.items() if email in w.readers and not w.clash} for email in ACCESS}


def _types(problems: list[dict]) -> dict[str, set[tuple[str, str]]]:
    out: dict[str, set[tuple[str, str]]] = {}
    for p in problems:
        out.setdefault(p["type"], set()).add((p.get("record", ""), p.get("user", "")))
    return out


def test_the_scope_is_every_ticket_file_and_comment_filed_under_their_project() -> None:
    wanted = _wanted()
    assert set(wanted) == {"101", "102", "103", "104", "201", "attachment_900", "attachment_902", "attachment_901",
                           "comment_51"}
    assert wanted["attachment_900"].group == "7" and wanted["201"].group == "8"


def test_a_restricted_comment_is_read_only_by_those_jira_shows_it_to() -> None:
    wanted = _wanted()
    assert "comment_50" not in wanted  # a public comment is in its ticket's content
    assert wanted["comment_51"].readers == {ALL}


def test_only_active_licensed_people_who_see_the_ticket_read_it() -> None:
    wanted = _wanted()
    assert wanted["101"].readers == {ALL, P7} and wanted["201"].readers == {ALL, P8}
    assert GONE not in wanted["101"].readers and NOLIC not in wanted["101"].readers


def test_a_faithful_sync_has_no_difference() -> None:
    wanted = _wanted()
    assert compare(wanted, _records(wanted), _faithful(wanted), ACCESS) == []


def test_without_comment_records_they_are_neither_wanted_nor_stale() -> None:
    wanted = _wanted(comments=False)
    assert not any(k.startswith("comment_") for k in wanted)
    records = _records(wanted) + [{"id": "rc", "externalRecordId": "comment_50", "externalGroupId": "7"}]
    assert compare(wanted, records, _faithful(wanted), ACCESS, comments=False) == []


def test_a_planted_over_share_is_a_leak_with_the_rule_that_explains_it() -> None:
    wanted = _wanted()
    seen = _faithful(wanted)
    seen[P7] |= {"r104"}          # a person outside the level reads the secured ticket
    seen[GONE] |= {"r101"}        # a deactivated person reads
    seen[NOLIC] |= {"r101"}       # a person with no product access reads
    seen[P7] |= {"rcomment_51"}   # a restricted comment reaches a reader it is hidden from
    got = _types(compare(wanted, _records(wanted), seen, ACCESS))
    assert got["LEAK:other"] == {("104", P7)}
    assert got["LEAK:inactive-user"] == {("101", GONE)} and got["LEAK:no-product-access"] == {("101", NOLIC)}
    assert got["LEAK:comment"] == {("comment_51", P7)}


def test_a_missing_grant_is_denied() -> None:
    wanted = _wanted()
    seen = _faithful(wanted)
    seen[P8].discard("r201")
    seen[ALL].discard("r104")
    seen[ALL].discard("rcomment_51")
    got = _types(compare(wanted, _records(wanted), seen, ACCESS))
    assert got["DENIED:other"] == {("201", P8)}
    assert got["DENIED:leveled-issue"] == {("104", ALL)}
    assert got["DENIED:comment"] == {("comment_51", ALL)}


def test_a_missing_stale_duplicate_and_misfiled_record_each_fail() -> None:
    wanted = _wanted()
    records = [r for r in _records(wanted) if r["externalRecordId"] != "102"]
    records.append({"id": "rx", "externalRecordId": "999", "externalGroupId": "7", "recordName": "gone"})
    records.append({"id": "rdup", "externalRecordId": "103", "externalGroupId": "7"})
    next(r for r in records if r["externalRecordId"] == "201")["externalGroupId"] = "7"
    got = _types(compare(wanted, records, _faithful(wanted), ACCESS))
    assert {"MISSING", "STALE", "DUPLICATE", "PARENT"} <= set(got)
    assert got["MISSING"] == {("102", "")} and got["STALE"] == {("999", "")}


def test_a_file_that_shares_an_inline_images_name_may_be_missing_and_nothing_else_may() -> None:
    wanted = _wanted()
    records = [r for r in _records(wanted)]
    assert not any(r["externalRecordId"] == "attachment_902" for r in records)
    assert compare(wanted, records, _faithful(wanted), ACCESS) == []
    records = [r for r in records if r["externalRecordId"] != "attachment_900"]
    assert {p["record"] for p in compare(wanted, records, _faithful(wanted), ACCESS) if p["type"] == "MISSING"} == {"attachment_900"}


def test_the_project_filter_and_the_date_filters_narrow_the_scope() -> None:
    f = merge_filters(project_filter("in", ["AAA"]), date_filter("modified", "is_after", start=3500))
    scope = Scope()
    scope.projects_in, scope.projects_out = _project_scope(f)
    scope.skip = _date_skip(EXPECTED, f)
    wanted = _wanted(scope)
    assert set(wanted) == {"101", "104", "attachment_900", "attachment_902", "attachment_901", "comment_51"}
    scope.projects_in, scope.projects_out = _project_scope(project_filter("not_in", ["AAA"]))
    assert set(_wanted(scope)) == {"201"}


@pytest.mark.parametrize("flt", [None, {}, {"sync": {"values": {}}}])
def test_no_filter_is_no_scope(flt) -> None:
    assert _project_scope(flt) == (None, None) and _date_skip(EXPECTED, flt) == set()


def _calls(**by_status):
    return {"GET unmatched": {"by_status": by_status}, "GET /rest/api/3/myself": {"by_status": {"404": 9}}}


@pytest.mark.parametrize("status", ["404", "405"])
def test_a_request_no_route_answers_is_unmodelled(status) -> None:
    assert unmodeled_requests(_calls(**{status: 1}), fault_answers([])) == ["GET unmatched"]


def test_requests_answered_before_routing_by_a_fault_or_for_no_credentials_are_not_unmodelled() -> None:
    log = [{"effect": "status", "method": "GET", "status": 404}] * 2 + [{"effect": "truncate", "method": "GET", "status": 404}]
    assert unmodeled_requests(_calls(**{"401": 3, "403": 1, "429": 7, "500": 2, "502": 219}), fault_answers([])) == []
    assert unmodeled_requests(_calls(**{"404": 2}), fault_answers(log)) == []


def test_an_unmodelled_request_is_found_among_the_requests_a_404_fault_answered() -> None:
    log = [{"effect": "status", "method": "GET", "status": 404}] * 2
    assert unmodeled_requests(_calls(**{"404": 3}), fault_answers(log)) == ["GET unmatched"]
    other = [{"effect": "status", "method": "POST", "status": 404}] * 5  # another method's faults excuse nothing
    assert unmodeled_requests(_calls(**{"404": 1}), fault_answers(other)) == ["GET unmatched"]


def test_a_method_a_known_path_does_not_have_is_unmodelled_and_its_404s_are_not() -> None:
    calls = {"DELETE /rest/api/3/myself": {"by_status": {"405": 1}}, "GET /rest/api/3/issue/{ref}": {"by_status": {"404": 4}}}
    assert unmodeled_requests(calls, fault_answers([])) == ["DELETE /rest/api/3/myself"]
