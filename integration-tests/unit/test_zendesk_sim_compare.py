"""The zendesk_sim suite's comparison: what a sync must hold, and which Zendesk rule explains each difference.

The oracle here is a small hand-written one in the shape of the simulator's ``GET /_sim/zendesk/expected``: one group
with an agent who reads it, one who does not, and an end user who requested a ticket; a private group; a public ticket
file; an internal comment's file; an embedded image; a deleted ticket; a draft, a segmented and a public article with a
file and an embedded image.
"""

from __future__ import annotations

import pytest
from connectors.zendesk.sim_world import (
    KNOWN_GAPS,
    Scope,
    _group_scope,
    compare,
    date_filter,
    group_filter,
    spec_findings_only,
    wanted_records,
)

ADMIN, AGENT, OTHER, REQ, SUSP = 1, 2, 3, 4, 5
SUPPORT, SECRET = 10, 11


def _user(uid: int, email: str, role: str = "agent", *, active: bool = True, suspended: bool = False) -> dict:
    return {"id": uid, "email": email, "role": role, "active": active, "suspended": suspended}


def _file(fid: int, *, inline: bool = False, deleted: bool = False, kind: str = "application/pdf") -> dict:
    return {"id": fid, "inline": inline, "deleted": deleted, "content_type": kind}


ORACLE = {
    "users": [_user(ADMIN, "admin@x.example", "admin"), _user(AGENT, "agent@x.example"), _user(OTHER, "other@x.example"),
              _user(REQ, "req@x.example", "end-user"), _user(SUSP, "susp@x.example", suspended=True)],
    "groups": [{"id": SUPPORT, "deleted": False}, {"id": SECRET, "deleted": False}],
    "tickets": [
        {"id": 1, "group_id": SUPPORT, "deleted": False, "readers": [ADMIN, AGENT, REQ], "comments": [
            {"id": 100, "public": True, "attachments": [_file(7), _file(8, inline=True, kind="image/png")]},
            {"id": 101, "public": False, "attachments": [_file(9)]},
            {"id": 102, "public": True, "attachments": [_file(6, deleted=True)]}]},
        {"id": 2, "group_id": SECRET, "deleted": False, "readers": [ADMIN], "comments": []},
        {"id": 3, "group_id": None, "deleted": False, "readers": [ADMIN], "comments": []},
        {"id": 4, "group_id": SUPPORT, "deleted": True, "readers": [], "comments": []},
    ],
    "articles": [
        {"id": 50, "section_id": 5, "draft": False, "segmented": False, "public": True,
         "readers": [ADMIN, AGENT, OTHER, REQ, SUSP],
         "attachments": [{"id": 51, "inline": False}, {"id": 52, "inline": True}]},
        {"id": 53, "section_id": 5, "draft": True, "segmented": False, "public": True, "readers": [], "attachments": []},
        {"id": 54, "section_id": 5, "draft": False, "segmented": True, "public": False, "readers": [ADMIN],
         "attachments": []},
    ],
}
EMAILS = {u["id"]: u["email"] for u in ORACLE["users"]}
WORKSPACE = [*EMAILS.values(), "outsider@x.example"]
TICKET_FILE = "ticket_1_comment_100_attachment_7"


def _records(scope: Scope, *, drop: tuple[str, ...] = ()) -> list[dict]:
    return [{"id": f"r{ext}", "externalRecordId": ext, "externalGroupId": want.group, "recordName": ext}
            for ext, want in wanted_records(ORACLE, scope).items() if ext not in drop]


def _faithful(scope: Scope) -> dict[str, set[str]]:
    """What a connector that follows every Zendesk rule grants, a workspace member with no Zendesk account included."""
    out: dict[str, set[str]] = {email: set() for email in WORKSPACE}
    for ext, want in wanted_records(ORACLE, scope).items():
        for uid, email in EMAILS.items():
            may = (uid in want.reader_pool) if want.readers is None else uid in want.readers
            if may:
                out[email].add(f"r{ext}")
        if want.readers is None:
            out["outsider@x.example"].add(f"r{ext}")
    return out


def _types(problems: list[dict]) -> dict[str, set[tuple[str, str]]]:
    out: dict[str, set[tuple[str, str]]] = {}
    for p in problems:
        out.setdefault(p["type"], set()).add((p.get("record", ""), p.get("user", "")))
    return out


def test_the_scope_is_tickets_public_files_and_public_articles() -> None:
    assert set(wanted_records(ORACLE, Scope())) == {"1", TICKET_FILE, "2", "3", "article_50", "article_50_attachment_51"}


def test_a_group_filter_narrows_the_scope_both_ways() -> None:
    assert _group_scope(group_filter("in", [SUPPORT])) == ({str(SUPPORT)}, None)
    assert _group_scope(group_filter("not_in", [SUPPORT])) == (None, {str(SUPPORT)})
    assert _group_scope({"sync": {"values": {}}}) == (None, None)
    assert set(wanted_records(ORACLE, Scope(groups_in={str(SUPPORT)}))) == {
        "1", TICKET_FILE, "article_50", "article_50_attachment_51"}
    assert "3" in wanted_records(ORACLE, Scope(groups_in={"unassigned_tickets"}))
    assert "3" not in wanted_records(ORACLE, Scope(groups_out={"unassigned_tickets"}))
    assert "2" not in wanted_records(ORACLE, Scope(groups_out={str(SECRET)}))


def test_a_date_filter_is_saved_as_the_app_saves_it() -> None:
    assert date_filter("created", 5, None)["sync"]["values"]["created"] == {
        "operator": "is_after", "type": "datetime", "value": {"start": 5, "end": None}}
    assert date_filter("modified", None, 9)["sync"]["values"]["modified"]["operator"] == "is_before"
    assert set(wanted_records(ORACLE, Scope(left_out={"1", "article_50"}))) == {"2", "3"}


def test_a_connector_that_follows_zendesk_differs_in_nothing() -> None:
    scope = Scope()
    assert compare(ORACLE, scope, _records(scope), _faithful(scope)) == []


def test_a_planted_over_share_is_caught_for_each_kind_of_person() -> None:
    scope = Scope()
    for email, record, why in (("other@x.example", "r1", "LEAK:agent"), ("req@x.example", "r2", "LEAK:end-user"),
                               ("susp@x.example", "r2", "LEAK:inactive-user"),
                               ("outsider@x.example", "r1", "LEAK:not-a-zendesk-user"),
                               ("other@x.example", f"r{TICKET_FILE}", "LEAK:agent")):
        readable = _faithful(scope)
        readable[email].add(record)
        found = _types(compare(ORACLE, scope, _records(scope), readable))
        assert found == {why: {(record[1:], email)}}, (email, record, found)


def test_a_missing_grant_is_caught() -> None:
    scope = Scope()
    readable = _faithful(scope)
    readable["agent@x.example"].discard("r1")
    readable["req@x.example"].discard("rarticle_50")
    found = _types(compare(ORACLE, scope, _records(scope), readable))
    assert found == {"DENIED:agent": {("1", "agent@x.example")}, "DENIED:article": {("article_50", "req@x.example")}}


def test_a_public_article_not_found_by_a_member_with_no_account_is_the_known_gap_and_only_that() -> None:
    scope = Scope()
    readable = _faithful(scope)
    readable["outsider@x.example"] = set()
    found = _types(compare(ORACLE, scope, _records(scope), readable))
    assert set(found) == {"DENIED:article-unregistered"} and set(found) <= set(KNOWN_GAPS)


def test_a_missing_stale_duplicate_and_misfiled_record_are_caught() -> None:
    scope = Scope()
    records = _records(scope, drop=("2",))
    records.append({"id": "rgone", "externalRecordId": "4", "externalGroupId": f"group_{SUPPORT}", "recordName": "gone"})
    records.append({"id": "rdraft", "externalRecordId": "article_53", "externalGroupId": "section_5", "recordName": "d"})
    records.append({"id": "rdup", "externalRecordId": "1", "externalGroupId": f"group_{SUPPORT}", "recordName": "1"})
    for r in records:
        if r["externalRecordId"] == "article_50":
            r["externalGroupId"] = "section_9"
    found = _types(compare(ORACLE, scope, records, _faithful(scope)))
    assert found["MISSING"] == {("2", "")}
    assert found["STALE"] == {("4", ""), ("article_53", "")}
    assert found["DUPLICATE"] == {("1", "")}
    assert found["PARENT"] == {("article_50", "")}


def test_an_address_that_passed_to_a_new_account_is_the_new_accounts() -> None:
    oracle = {**ORACLE, "users": [*ORACLE["users"], _user(9, "agent@x.example", "end-user"),
                                  {**_user(2, "agent@x.example"), "active": False}]}
    oracle["users"] = [u for u in oracle["users"] if not (u["id"] == AGENT and u["active"])]
    scope = Scope()
    wanted = wanted_records(oracle, scope)
    readable = {email: set() for email in WORKSPACE}
    for ext, want in wanted.items():
        readable["agent@x.example"].update({f"r{ext}"} if want.readers is None else set())
    records = [{"id": f"r{e}", "externalRecordId": e, "externalGroupId": w.group, "recordName": e}
               for e, w in wanted.items()]
    assert all(p["type"] != "LEAK:agent" for p in compare(oracle, scope, records, readable))


def test_spec_problems_the_guide_lists_are_not_hidden_and_others_are_kept() -> None:
    known = {"op": "GET /api/v2/help_center/articles/{article_id}/comments",
             "problems": ["query: 'sort_order' is not declared"]}
    other = {"op": "GET /api/v2/groups", "problems": ["response.groups[0]: 'x' is not declared"]}
    mixed = {"op": known["op"], "problems": [*known["problems"], "something else"]}
    assert spec_findings_only([known, other, mixed]) == [other, mixed]


@pytest.mark.parametrize("fault_rule", ["no fault fired"])
def test_a_fault_that_never_fired_fails_its_test(fault_rule: str) -> None:
    from connectors.zendesk.sim_world import ZendeskWorld, fault  # noqa: PLC0415

    class Sim:
        def call(self, method: str, path: str, body: object = None):
            return [] if path == "/_sim/faults/log" else None

    world = ZendeskWorld.__new__(ZendeskWorld)
    world.sim = Sim()
    world._fault_rules = [fault(r"^/api/v2/users$", 503)]
    with pytest.raises(AssertionError, match="no injected fault fired"):
        world.clear_faults()
