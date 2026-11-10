"""The zammad_sim suite's comparison: what a sync must hold, and which Zammad rule explains each difference.

The oracle here is a small hand-written one in the shape of the simulator's ``GET /_sim/zammad/expected``: one active
group with a full agent, a read agent, a change-only agent and an agent who reads it through a role; an inactive
group; a customer in a shared organisation and one in the same organisation; a public and an internal article with
attachments; and a published and an archived answer.
"""

from __future__ import annotations

import pytest
from connectors.zammad.sim_world import KNOWN_GAPS, Scope, _group_scope, compare, group_filter, wanted_records

ADMIN, FULL, READ, CHANGE, ROLE, GONE, CAROL, COLE = 2, 3, 4, 5, 6, 7, 10, 11
SUPPORT, RETIRED = 2, 4


def _user(uid: int, email: str, *, groups: dict | None = None, org: int | None = None, active: bool = True) -> dict:
    return {"id": uid, "email": email, "active": active, "roles": [], "organization_id": org, "organization_ids": [],
            "groups": groups or {}}


ORACLE = {
    "users": [
        _user(1, ""),
        _user(ADMIN, "admin@example.com", groups={"2": ["full"], "4": ["full"]}),
        _user(FULL, "ann@example.com", groups={"2": ["full"]}),
        _user(READ, "ben@example.com", groups={"2": ["read"]}),
        _user(CHANGE, "cy@example.com", groups={"2": ["change"]}),
        _user(ROLE, "eva@example.com"),
        _user(GONE, "gus@example.com", groups={"2": ["full"]}, active=False),
        _user(CAROL, "carol@example.com", org=1),
        _user(COLE, "cole@example.com", org=1),
    ],
    "groups": [
        {"id": SUPPORT, "name": "Support", "active": True, "user_ids": [ADMIN, FULL, READ, CHANGE, GONE],
         "read_user_ids": [ADMIN, FULL, READ, ROLE]},
        {"id": RETIRED, "name": "Retired", "active": False, "user_ids": [ADMIN], "read_user_ids": []},
    ],
    "tickets": [
        {"id": 1, "group_id": SUPPORT, "customer_id": CAROL, "organization_id": 1,
         "readers": [ADMIN, FULL, READ, ROLE, CAROL, COLE],
         "articles": [
             {"id": 1, "internal": False, "sender": 2, "attachments": [1],
              "readers": [ADMIN, FULL, READ, ROLE, CAROL, COLE]},
             {"id": 2, "internal": True, "sender": 1, "attachments": [2], "readers": [ADMIN, FULL, READ, ROLE]},
             {"id": 3, "internal": False, "sender": 3, "attachments": [3],
              "readers": [ADMIN, FULL, READ, ROLE, CAROL, COLE]},
         ]},
        {"id": 2, "group_id": RETIRED, "customer_id": CAROL, "organization_id": 1, "readers": [CAROL, COLE],
         "articles": []},
    ],
    "kb": {"active": True, "categories": [{"id": 1, "parent_id": None}],
           "answers": [{"id": 1, "category_id": 1, "state": "published", "attachments": [9], "anonymous": True,
                        "readers": [1, ADMIN, FULL, READ, CHANGE, ROLE, GONE, CAROL, COLE]},
                       {"id": 2, "category_id": 1, "state": "archived", "attachments": [], "anonymous": False,
                        "readers": [ADMIN]}]},
    "tokens": {"pipeshub": {"user_id": ADMIN, "search_ticket_ids": [1, 2], "groups_listed": [SUPPORT, RETIRED],
                            "kb": {"answers": []}},
               "kb-editor": {"user_id": ADMIN, "search_ticket_ids": [1, 2], "groups_listed": [SUPPORT, RETIRED],
                             "kb": {"answers": [1, 2]}},
               "agent-only": {"user_id": FULL, "search_ticket_ids": [1], "groups_listed": [],
                              "kb": {"answers": []}}},
}
EMAILS = {u["id"]: u["email"] for u in ORACLE["users"] if u["email"]}


def _records(scope: Scope) -> list[dict]:
    return [{"id": f"r{ext}", "externalRecordId": ext, "externalGroupId": want.group, "recordName": ext}
            for ext, want in wanted_records(ORACLE, scope).items()]


def _faithful(scope: Scope) -> dict[str, set[str]]:
    """What a connector that follows every Zammad rule grants, customers too."""
    wanted = wanted_records(ORACLE, scope)
    return {email: {f"r{ext}" for ext, want in wanted.items() if uid in want.readers} for uid, email in EMAILS.items()}


def _types(problems: list[dict]) -> dict[str, set[tuple[str, str]]]:
    out: dict[str, set[tuple[str, str]]] = {}
    for p in problems:
        out.setdefault(p["type"], set()).add((p.get("record", ""), p.get("user", "")))
    return out


def test_the_scope_is_the_tokens_search_in_active_listed_groups_without_system_attachments() -> None:
    assert set(wanted_records(ORACLE, Scope())) == {"1", "1_1_1", "1_2_2"}
    assert set(wanted_records(ORACLE, Scope(token="kb-editor"))) == {
        "1", "1_1_1", "1_2_2", "kb_answer_1", "kb_answer_1_attachment_9", "kb_answer_2"}
    # 6.4.1 lists groups to admin.group only: an agent's token can list none, so nothing is in scope.
    assert wanted_records(ORACLE, Scope(token="agent-only")) == {}


def test_a_group_filter_narrows_the_scope_both_ways() -> None:
    assert _group_scope(group_filter("in", [SUPPORT])) == ({SUPPORT}, None)
    assert _group_scope(group_filter("not_in", [SUPPORT])) == (None, {SUPPORT})
    assert _group_scope({"sync": {"values": {}}}) == (None, None)
    assert wanted_records(ORACLE, Scope(groups_out={SUPPORT})) == {}
    assert set(wanted_records(ORACLE, Scope(groups_in={SUPPORT}))) == {"1", "1_1_1", "1_2_2"}


def test_a_connector_that_follows_zammad_differs_in_nothing() -> None:
    for scope in (Scope(), Scope(token="kb-editor")):
        assert compare(ORACLE, scope, _records(scope), _faithful(scope)) == []


def test_customers_left_out_are_the_known_gaps_and_only_those() -> None:
    scope = Scope()
    readable = _faithful(scope)
    readable["carol@example.com"] = set()
    readable["cole@example.com"] = set()
    found = _types(compare(ORACLE, scope, _records(scope), readable))
    assert found == {"DENIED:customer-own-ticket": {("1", "carol@example.com"), ("1_1_1", "carol@example.com")},
                     "DENIED:customer-shared-org": {("1", "cole@example.com"), ("1_1_1", "cole@example.com")}}
    assert set(found) <= KNOWN_GAPS


def test_counting_any_access_level_as_membership_is_a_leak_and_ignoring_roles_a_denial() -> None:
    scope = Scope()
    wanted = wanted_records(ORACLE, scope)
    members = {ADMIN, FULL, READ, CHANGE}            # the old rule: a user_groups row of any level, active users
    readable = {email: ({f"r{ext}" for ext in wanted} if uid in members else set()) for uid, email in EMAILS.items()}
    found = _types(compare(ORACLE, scope, _records(scope), readable))
    assert found["LEAK:group-access-level"] == {(ext, "cy@example.com") for ext in wanted}
    assert found["DENIED:role-group-access"] == {(ext, "eva@example.com") for ext in wanted}
    assert not set(found) - {"LEAK:group-access-level", "DENIED:role-group-access", *KNOWN_GAPS}


def test_an_internal_articles_attachment_given_to_customers_is_a_leak() -> None:
    scope = Scope()
    readable = _faithful(scope)
    readable["carol@example.com"].add("r1_2_2")
    assert _types(compare(ORACLE, scope, _records(scope), readable)) == {
        "LEAK:internal-article": {("1_2_2", "carol@example.com")}}


def test_an_inactive_agent_who_still_reads_is_a_leak() -> None:
    scope = Scope()
    readable = _faithful(scope)
    readable["gus@example.com"] = {"r1"}
    assert _types(compare(ORACLE, scope, _records(scope), readable)) == {"LEAK:inactive-user": {("1", "gus@example.com")}}


@pytest.mark.parametrize(("finds", "state", "who"), [(True, "archived", "ann@example.com"),
                                                      (False, "published", "cole@example.com")])
def test_an_answer_difference_names_its_state(finds: bool, state: str, who: str) -> None:
    scope = Scope(token="kb-editor")
    readable = _faithful(scope)
    ext = "kb_answer_2" if state == "archived" else "kb_answer_1"
    (readable[who].add if finds else readable[who].discard)(f"r{ext}")
    assert _types(compare(ORACLE, scope, _records(scope), readable)) == {
        f"{'LEAK' if finds else 'DENIED'}:kb-{state}": {(ext, who)}}


def test_a_published_answer_is_everyones_even_without_a_zammad_account() -> None:
    scope = Scope(token="kb-editor")
    readable = _faithful(scope)
    readable["newbie@example.com"] = {"rkb_answer_1", "rkb_answer_1_attachment_9"}
    assert compare(ORACLE, scope, _records(scope), readable) == []
    readable["newbie@example.com"].add("rkb_answer_2")
    assert _types(compare(ORACLE, scope, _records(scope), readable)) == {
        "LEAK:kb-archived": {("kb_answer_2", "newbie@example.com")}}


def test_records_missing_stale_duplicated_or_misfiled_are_reported() -> None:
    scope = Scope()
    records = _records(scope)
    records = [r for r in records if r["externalRecordId"] != "1_1_1"]       # missing
    records.append({"id": "r2", "externalRecordId": "2", "externalGroupId": "group_4", "recordName": "retired"})
    records.append(dict(records[0], id="r1-copy"))                          # a second record of ticket 1
    records[1] = dict(records[1], externalGroupId="group_9")                 # filed under another group
    found = _types(compare(ORACLE, scope, records, _faithful(scope)))
    assert set(found) == {"MISSING", "STALE", "DUPLICATE", "PARENT"}
    assert found["MISSING"] == {("1_1_1", "")} and found["STALE"] == {("2", "")}
