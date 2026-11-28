"""The linear_sim suite's comparison: what a sync must hold, and which difference each fault of PipesHub is called.

The oracle here is a small hand-written one in the shape of the simulator's ``GET /_sim/linear/expected``: a public team
whose members read it, a private team, an issue in each, a comment-less project over both with a link, a trashed issue, an
archived issue, and a file linked from the public issue and from the project.
"""

from __future__ import annotations

import copy

from connectors.linear.sim_world import Scope, compare, file_record_id, synced_team_keys, wanted_records

A, B, C = "a@x.example", "b@x.example", "c@x.example"
PUB, PRIV = "team-pub", "team-priv"
FILE_URL = "https://uploads.linear.app/org/f1/spec.pdf"


def _issue(key: str, team: str, readers: list[str], **kw) -> dict:
    return {"id": f"id-{key}", "key": key, "identifier": key.upper(), "team": team, "trashed": False, "archived": False,
            "created": 1000, "readers": readers, "visible_to_viewer": True, **kw}


ORACLE = {
    "teams": [
        {"id": PUB, "key": "PUB", "archived": False, "visible_to_viewer": True, "readers": [A, B, C], "parent": None},
        {"id": PRIV, "key": "PRIV", "archived": False, "visible_to_viewer": True, "readers": [A], "parent": None}],
    "issues": [_issue("p-1", "PUB", [A, B, C]), _issue("s-1", "PRIV", [A]),
               _issue("p-trash", "PUB", [], trashed=True), _issue("p-arch", "PUB", [A, B, C], archived=True)],
    "projects": [{"id": "id-pr", "key": "pr", "name": "Plan", "teams": ["PUB", "PRIV"], "trashed": False,
                  "visible_to_viewer": True}],
    "attachments": [{"id": "id-at", "key": "at", "owner_kind": "issue", "owner": "p-1", "trashed": False,
                     "readers": [A, B, C]}],
    "documents": [], "comments": [],
    "links": [{"id": "id-ln", "key": "ln", "owner_kind": "project", "owner": "pr", "trashed": False}],
    "uploads": [{"key": "f1", "url": FILE_URL, "linked_from": [
        {"kind": "issue", "key": "p-1", "where": "description", "image": False},
        {"kind": "project", "key": "pr", "where": "content", "image": False},
        {"kind": "issue", "key": "p-1", "where": "description", "image": True}]}],
}
PEOPLE = [A, B, C, "outsider@x.example"]
FILE_ISSUE = file_record_id(FILE_URL, "id-p-1")
FILE_PROJECT = file_record_id(FILE_URL, "id-pr")


def _records(scope: Scope, *, drop: tuple[str, ...] = ()) -> list[dict]:
    return [{"id": f"r-{ext}", "externalRecordId": ext, "externalGroupId": None, "recordName": w["name"],
             "recordType": w["kind"]}
            for ext, w in wanted_records(ORACLE, scope).items() if w["in_scope"] and ext not in drop]


def _faithful(scope: Scope) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {p: set() for p in PEOPLE}
    for ext, w in wanted_records(ORACLE, scope).items():
        if w["in_scope"]:
            for email in w["readers"]:
                out.setdefault(email, set()).add(f"r-{ext}")
    return out


def _run(scope: Scope = Scope(), *, records=None, groups=None, readable=None) -> list[dict]:
    records = _records(scope) if records is None else records
    groups = set(synced_team_keys_ids(scope)) if groups is None else groups
    return compare(ORACLE, scope, records, groups, _faithful(scope) if readable is None else readable)


def synced_team_keys_ids(scope: Scope) -> list[str]:
    keys = synced_team_keys(ORACLE, scope)
    return [t["id"] for t in ORACLE["teams"] if t["key"] in keys]


def test_what_a_sync_holds() -> None:
    wanted = wanted_records(ORACLE, Scope())
    in_scope = {e for e, w in wanted.items() if w["in_scope"]}
    assert in_scope == {"id-p-1", "id-s-1", "id-p-arch", "id-at", "id-pr", "id-ln", FILE_ISSUE, FILE_PROJECT}, in_scope
    assert not wanted["id-p-trash"]["in_scope"], "a trashed issue is in scope"
    assert not wanted["id-p-arch"]["required"], "an archived issue is required"
    assert wanted["id-pr"]["readers"] == {A, B, C}, "a project is read by the readers of every synced team"
    assert FILE_URL + "#parent=id-p-1" in wanted and not any(k.endswith("#parent=id-p-1") and k != FILE_ISSUE for k in wanted)


def test_a_faithful_sync_has_no_difference() -> None:
    assert _run() == []


def test_a_team_filter_narrows_the_scope() -> None:
    scope = Scope(teams=("in", ["PUB"]))
    wanted = wanted_records(ORACLE, scope)
    assert not wanted["id-s-1"]["in_scope"] and wanted["id-pr"]["readers"] == {A, B, C}
    assert _run(scope) == []
    assert _run(Scope(teams=("not_in", ["PUB"]))) == []


def test_control_a_planted_over_share_is_a_leak() -> None:
    readable = _faithful(Scope())
    readable[B].add("r-id-s-1")
    assert {p["type"] for p in _run(readable=readable)} == {"LEAK"}


def test_control_a_missing_grant_is_denied() -> None:
    readable = _faithful(Scope())
    readable[A].discard("r-id-p-1")
    assert {p["type"] for p in _run(readable=readable)} == {"DENIED"}


def test_control_a_missing_record_is_missing() -> None:
    problems = _run(records=_records(Scope(), drop=("id-p-1",)))
    assert "MISSING" in {p["type"] for p in problems}


def test_control_a_record_of_a_trashed_issue_is_stale() -> None:
    records = _records(Scope()) + [{"id": "r-trash", "externalRecordId": "id-p-trash", "externalGroupId": None,
                                    "recordName": "P-TRASH", "recordType": "TICKET"}]
    readable = _faithful(Scope())
    readable[A].add("r-trash")
    assert {p["type"] for p in _run(records=records, readable=readable)} == {"STALE"}


def test_control_a_record_of_something_gone_is_stale() -> None:
    records = _records(Scope()) + [{"id": "r-gone", "externalRecordId": "id-nothing", "externalGroupId": None,
                                    "recordName": "gone", "recordType": "TICKET"}]
    readable = _faithful(Scope())
    readable[C].add("r-gone")
    assert {p["type"] for p in _run(records=records, readable=readable)} == {"STALE"}


def test_control_two_records_for_one_thing_is_a_duplicate() -> None:
    records = _records(Scope())
    records.append({**records[0], "id": "r-second"})
    assert "DUPLICATE" in {p["type"] for p in _run(records=records)}


def test_control_a_missing_or_extra_team_is_a_team_difference() -> None:
    assert {p["type"] for p in _run(groups={PUB})} == {"TEAM"}
    extra = _run(groups={PUB, PRIV, "team-ghost"})
    assert [p for p in extra if p["type"] == "TEAM" and p["extra"] == ["team-ghost"]]


def test_a_team_the_filter_left_out_may_stay_stored() -> None:
    scope = Scope(teams=("in", ["PUB"]))
    assert _run(scope, groups={PUB, PRIV}) == [], "the group of a team a filter leaves out was called a difference"


def test_a_file_linked_as_an_image_is_not_a_record() -> None:
    assert not [e for e in wanted_records(ORACLE, Scope()) if e.startswith(FILE_URL) and e.count("#parent=") != 1]
    assert len([e for e in wanted_records(ORACLE, Scope()) if e.startswith(FILE_URL)]) == 2, "one file record per place"


def test_the_created_filter_keeps_out_what_is_older() -> None:
    scope = Scope(created_after=2000)
    assert not any(w["in_scope"] and w["kind"] == "TICKET" for w in wanted_records(ORACLE, scope).values())
    oracle = copy.deepcopy(ORACLE)
    oracle["issues"][0]["created"] = 3000
    assert wanted_records(oracle, scope)["id-p-1"]["in_scope"]
