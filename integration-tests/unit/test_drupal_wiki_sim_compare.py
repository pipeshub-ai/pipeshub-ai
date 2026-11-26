"""The drupal_wiki_sim suite's comparison: what a sync must hold, and which Drupal Wiki rule explains each difference.

The oracle here is a small hand-written one in the shape of the simulator's ``GET /_sim/drupalwiki/expected``: an
open space and a private one, a page tree with an attachment, a page shared into the private space, an enabled
reader of each space, a disabled user, a user with no email, and an attachment of a type a filter can leave out.
"""

from __future__ import annotations

import pytest
from connectors.drupal_wiki.sim_world import (
    KNOWN_GAPS,
    SEED_SPECIAL_SPACES,
    Gap,
    Scope,
    apply_known,
    gap,
    compare,
    extension_filter,
    merge_filters,
    scope_of,
    space_filter,
    wanted_records,
)

ADMIN, ANN, BEN, GONE, NOMAIL = 1, 2, 3, 4, 5
OPEN, PRIVATE = "10", "20"

ORACLE = {
    "users": [{"id": ADMIN, "email": "admin@example.com", "status": "ENABLED"},
              {"id": ANN, "email": "ann@example.com", "status": "ENABLED"},
              {"id": BEN, "email": "ben@example.com", "status": "ENABLED"},
              {"id": GONE, "email": "gone@example.com", "status": "DISABLED"},
              {"id": NOMAIL, "email": None, "status": "ENABLED"}],
    "spaces": {OPEN: {"name": "Open", "type": "BASIC", "accessStatus": "AUTHENTICATED",
                      "readers": [ADMIN, ANN, BEN, NOMAIL]},
               PRIVATE: {"name": "Private", "type": "BASIC", "accessStatus": "PRIVATE", "readers": [ADMIN, BEN]}},
    "pages": {
        "1": {"space": 10, "parent": None, "title": "Root", "readers": [ADMIN, ANN, BEN, NOMAIL],
              "shared_into": [20]},
        "2": {"space": 10, "parent": 1, "title": "Child", "readers": [ADMIN, ANN, BEN, NOMAIL], "shared_into": []},
        "3": {"space": 20, "parent": None, "title": "Secret", "readers": [ADMIN, BEN], "shared_into": []},
    },
    "attachments": {"7": {"page": 2, "fileName": "notes.md", "size": 5},
                    "8": {"page": 3, "fileName": "plan.PDF", "size": 5}},
}
EMAILS = {u["id"]: u["email"] for u in ORACLE["users"] if u["email"]}


def _records(scope: Scope) -> list[dict]:
    """What a connector that files everything where the wiki does stores."""
    return [{"id": f"r{ext}", "externalRecordId": ext, "externalGroupId": want.group, "parent": want.parent,
             "recordName": ext} for ext, want in wanted_records(ORACLE, scope).items()]


def _faithful(scope: Scope) -> dict[str, set[str]]:
    wanted = wanted_records(ORACLE, scope)
    return {email: {f"r{ext}" for ext, want in wanted.items() if uid in want.readers} for uid, email in EMAILS.items()}


def _types(problems: list[dict]) -> dict[str, set[tuple[str, str]]]:
    out: dict[str, set[tuple[str, str]]] = {}
    for p in problems:
        out.setdefault(p["type"], set()).add((p.get("record", p.get("name", "")), p.get("user", "")))
    return out


def test_the_scope_is_the_oracles_pages_and_their_attachments() -> None:
    assert set(wanted_records(ORACLE, Scope())) == {"page:1", "page:2", "page:3", "attachment:7", "attachment:8"}
    want = wanted_records(ORACLE, Scope())
    assert want["attachment:8"].parent == "page:3" and want["attachment:8"].group == PRIVATE
    assert want["page:2"].parent == "page:1" and want["page:1"].parent is None


def test_the_attachment_type_filter_leaves_attachments_out_both_ways() -> None:
    only_md = scope_of(extension_filter("in", ["md"]))
    assert set(wanted_records(ORACLE, only_md)) == {"page:1", "page:2", "page:3", "attachment:7"}
    no_pdf = scope_of(extension_filter("not_in", [".pdf"]))
    assert set(wanted_records(ORACLE, no_pdf)) == {"page:1", "page:2", "page:3", "attachment:7"}


def test_filters_give_the_simulators_include_and_exclude() -> None:
    assert scope_of(space_filter("in", [10, 20])).include == [10, 20]
    assert scope_of(space_filter("not_in", [20])).exclude == [20]
    merged = merge_filters(space_filter("in", [10]), extension_filter("in", ["md"]))
    assert scope_of(merged).include == [10] and scope_of(merged).extensions_in == {"md"}


def test_a_connector_that_follows_the_wiki_differs_in_nothing() -> None:
    scope = Scope()
    assert compare(ORACLE, scope, _records(scope), _faithful(scope), ["Open", "Private"]) == []


def test_a_planted_over_share_is_a_leak_that_names_its_rule() -> None:
    scope = Scope()
    readable = _faithful(scope)
    readable["ann@example.com"].add("rpage:3")           # an open-space reader reads the private space
    readable["gone@example.com"] = {"rpage:1"}          # a disabled user reads an open page
    readable["outsider@example.com"] = {"rpage:1"}      # someone with no wiki account
    found = _types(compare(ORACLE, scope, _records(scope), readable))
    assert found == {"LEAK:other": {("page:3", "ann@example.com")},
                     "LEAK:disabled-user": {("page:1", "gone@example.com")},
                     "LEAK:no-wiki-account": {("page:1", "outsider@example.com")}}


def test_a_planted_missing_grant_is_a_denial_that_names_its_rule() -> None:
    scope = Scope()
    readable = _faithful(scope)
    readable["ben@example.com"].discard("rpage:3")      # a member of the private space is refused
    readable["ann@example.com"].discard("rpage:2")      # a reader of an open space is refused
    found = _types(compare(ORACLE, scope, _records(scope), readable))
    assert found == {"DENIED:private-space": {("page:3", "ben@example.com")},
                     "DENIED:open-space": {("page:2", "ann@example.com")}}


def test_a_reader_who_only_a_share_gives_a_read_and_is_refused_is_a_shared_page_denial() -> None:
    scope = Scope()
    # Ben reads page 1 only because it is shared into Private, where he is a member; Ann reads it as an open reader.
    oracle = {**ORACLE, "pages": {**ORACLE["pages"], "1": {**ORACLE["pages"]["1"], "readers": [ADMIN, BEN]}}}
    oracle["spaces"] = {**ORACLE["spaces"], OPEN: {**ORACLE["spaces"][OPEN], "readers": [ADMIN, ANN]}}
    readable = {"ann@example.com": set(), "ben@example.com": {"rpage:2"}, "admin@example.com": set()}
    found = _types(compare(oracle, scope, _records(scope), readable))
    assert ("page:1", "ben@example.com") in found["DENIED:shared-page"]


def test_a_disabled_user_who_should_read_is_a_denial_and_the_rest_are_not_confused() -> None:
    scope = Scope()
    oracle = {**ORACLE, "users": [dict(u, status="ENABLED") if u["id"] == GONE else u for u in ORACLE["users"]]}
    oracle["pages"] = {**ORACLE["pages"], "1": {**ORACLE["pages"]["1"], "readers": [ADMIN, ANN, BEN, GONE]}}
    readable = _faithful(scope)
    assert "DENIED" in "".join(_types(compare(oracle, scope, _records(scope), readable)))


def test_records_missing_stale_duplicated_or_misfiled_are_reported() -> None:
    scope = Scope()
    records = [r for r in _records(scope) if r["externalRecordId"] != "attachment:7"]              # missing
    records.append({"id": "r9", "externalRecordId": "page:9", "externalGroupId": "10", "recordName": "gone page"})
    records.append(dict(records[0], id="r1-copy"))                                                  # a second page 1
    records[1] = dict(records[1], externalGroupId="30")                                             # another space
    records[2] = dict(records[2], parent="page:1")                                                  # another parent
    found = _types(compare(ORACLE, scope, records, _faithful(scope)))
    assert set(found) == {"MISSING", "STALE", "DUPLICATE", "PARENT"}
    assert found["MISSING"] == {("attachment:7", "")} and found["STALE"] == {("page:9", "")}
    assert len(found["PARENT"]) == 2


def test_someone_reading_a_record_of_something_gone_is_a_leak() -> None:
    scope = Scope()
    records = [*_records(scope), {"id": "r9", "externalRecordId": "page:9", "externalGroupId": "10", "parent": None,
                                  "recordName": "deleted page"}]
    readable = _faithful(scope)
    assert _types(compare(ORACLE, scope, records, readable)) == {"STALE": {("page:9", "")}}
    readable["ann@example.com"].add("r9")
    assert _types(compare(ORACLE, scope, records, readable)) == {
        "STALE": {("page:9", "")}, "LEAK:stale-record": {("page:9", "ann@example.com")}}


def test_a_record_a_full_sync_unlinked_is_a_removed_record() -> None:
    scope = Scope()
    gone = {"id": "r9", "externalRecordId": "page:9", "externalGroupId": "10", "parent": None, "recordName": "gone",
            "linked": False}
    readable = _faithful(scope)
    assert compare(ORACLE, scope, [*_records(scope), gone], readable) == []
    assert _types(compare(ORACLE, scope, [*_records(scope), dict(gone, linked=True)], readable)) == {
        "STALE": {("page:9", "")}}


def test_the_parent_is_not_asked_of_pages_when_the_tree_cannot_be_read() -> None:
    scope = Scope(parents=False)
    records = [dict(r, parent=None) if r["externalRecordId"] == "page:2" else r for r in _records(scope)]
    assert compare(ORACLE, scope, records, _faithful(scope)) == []
    records = [dict(r, parent="page:1") if r["externalRecordId"] == "attachment:7" else r for r in records]
    assert _types(compare(ORACLE, scope, records, _faithful(scope))) == {"PARENT": {("attachment:7", "")}}


@pytest.mark.parametrize(("names", "kind", "name"), [(["Open"], "MISSING-GROUP", "Private"),
                                                      (["Open", "Private", "Trash"], "STALE-GROUP", "Trash")])
def test_spaces_missing_or_extra_as_record_groups_are_reported(names: list[str], kind: str, name: str) -> None:
    scope = Scope()
    found = compare(ORACLE, scope, _records(scope), _faithful(scope), names)
    assert found == [{"type": kind, "name": name}]


# ---- known gaps are narrowed to the exact case they stand for --------------------------------------------------


def _over_share(scope: Scope, *, user: str, ext: str) -> list[dict]:
    readable = _faithful(scope)
    readable[user].add(f"r{ext}")
    return compare(ORACLE, scope, _records(scope), readable)


def test_a_known_leak_covers_its_person_and_its_spaces_and_nothing_else_of_that_type() -> None:
    scope = Scope()
    # ann reading the private page 3 is a leak of the same type as the gap, but not the person or space it names
    problems = _over_share(scope, user="ann@example.com", ext="page:3")
    problems[0]["type"] = "LEAK:disabled-user"
    known = {"LEAK:disabled-user": gap("#1", users=["gone@example.com"], spaces=[10])}
    failing, unused = apply_known(problems, known)
    assert failing == problems and unused == ["LEAK:disabled-user"]
    # the case it stands for: the disabled user reading an open-space page
    readable = _faithful(scope)
    readable["gone@example.com"] = {"rpage:1"}
    problems = compare(ORACLE, scope, _records(scope), readable)
    assert apply_known(problems, known) == ([], [])
    # the same person reading a page of the private space is not covered
    readable["gone@example.com"] = {"rpage:1", "rpage:3"}
    failing, _ = apply_known(compare(ORACLE, scope, _records(scope), readable), known)
    assert [(p["type"], p["record"]) for p in failing] == [("LEAK:disabled-user", "page:3")]


def test_a_known_stale_record_covers_that_record_and_another_stale_record_fails() -> None:
    scope = Scope()
    records = [*_records(scope),
               {"id": "r8", "externalRecordId": "page:8", "externalGroupId": "10", "parent": None, "recordName": "a"},
               {"id": "r9", "externalRecordId": "page:9", "externalGroupId": "10", "parent": None, "recordName": "b"}]
    problems = compare(ORACLE, scope, records, _faithful(scope))
    failing, unused = apply_known(problems, {"STALE": gap("#1", records=["page:8"])})
    assert [p["record"] for p in failing] == ["page:9"] and unused == []


def test_a_gap_that_covers_nothing_is_reported_as_fixed() -> None:
    scope = Scope()
    problems = compare(ORACLE, scope, _records(scope), _faithful(scope))
    assert apply_known(problems, {"MISSING": gap("#1", records=["page:8"])}) == ([], ["MISSING"])


def test_a_gap_must_name_a_pull_request_and_a_case() -> None:
    with pytest.raises(AssertionError):
        Gap("not-a-pr", records=frozenset({"page:1"}))
    with pytest.raises(AssertionError):
        Gap("#1")


def test_the_global_known_gap_covers_only_the_seed_wikis_special_spaces() -> None:
    scope = Scope()
    names = ["Open", "Private", "Trash", "A new space nobody listed"]
    problems = compare(ORACLE, scope, _records(scope), _faithful(scope), names)
    failing, _ = apply_known(problems, KNOWN_GAPS)
    assert failing == [{"type": "STALE-GROUP", "name": "A new space nobody listed"}]
    assert "Trash" in SEED_SPECIAL_SPACES


def test_groups_a_full_sync_unlinked_are_removed_groups_and_the_seed_special_spaces_stay_stale() -> None:
    scope = Scope()
    names = ["Open", "Private", "Company Handbook", "Trash"]
    found = compare(ORACLE, scope, _records(scope), _faithful(scope), names, unlinked_groups=1)
    assert found == [{"type": "STALE-GROUP", "name": "Trash"}]      # the unlinked one is the deleted space's
    assert compare(ORACLE, scope, _records(scope), _faithful(scope), names, unlinked_groups=2) == []
