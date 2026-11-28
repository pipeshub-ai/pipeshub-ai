"""The gitlab_sim suite's comparison: what a sync must hold, and which GitLab rule explains each difference.

The oracle here is a small hand-written one in the shape of the simulator's ``GET /_sim/gitlab/expected``: a private
project with an owner, a developer, a reporter and a guest (and a blocked guest), a public project with one issue, an
issue, a confidential issue, a merge request, a file and a folder, an upload, and a dotfile.
"""

from __future__ import annotations

import copy

import pytest

from connectors.gitlab.sim_world import KNOWN_GAPS, Scope, compare, norm, wanted_records

PRIVATE, PUBLIC = 10, 11
USERS = [{"id": i, "username": n, "email": f"{n}@example.com", "state": s}
         for i, (n, s) in enumerate([("admin", "active"), ("dev", "active"), ("rita", "active"), ("gus", "active"),
                                     ("blocked", "blocked"), ("olga", "active")], start=1)]
EMAILS = [u["email"] for u in USERS] + ["nobody@example.com"]


def rec(rid: str, kind: str, project: int, group: str, readers: list[str], **kw) -> dict:
    return {"id": rid, "kind": kind, "project": project, "group": group, "readers": readers, "anonymous": False, **kw}


ORACLE = {
    "users": USERS,
    "groups": [],
    "projects": [
        {"id": PRIVATE, "path_with_namespace": "acme/widgets", "visibility": "private", "deleted": False,
         "members": {"admin": 50, "dev": 30, "rita": 20, "gus": 10, "blocked": 10}, "inactive_members": ["blocked"]},
        {"id": PUBLIC, "path_with_namespace": "acme/oss", "visibility": "public", "deleted": False,
         "members": {"admin": 50}, "inactive_members": []},
    ],
    "records": [
        rec("101", "issue", PRIVATE, "10-work-items", ["admin", "dev", "rita", "gus"], title="Open"),
        rec("102", "issue", PRIVATE, "10-confidential-work-items", ["admin", "dev", "rita"], title="Secret"),
        rec("103", "merge_request", PRIVATE, "10-merge-requests", ["admin", "dev", "rita"], title="MR"),
        rec("/acme/widgets/-/blob/HEAD/src/app.py", "file", PRIVATE, "10-code-repository", ["admin", "dev", "rita"],
            path="src/app.py"),
        rec("/acme/widgets/-/tree/HEAD/src", "folder", PRIVATE, "10-code-repository", ["admin", "dev", "rita"], path="src"),
        rec("/acme/widgets/-/blob/HEAD/.gitattributes", "file", PRIVATE, "10-code-repository", ["admin", "dev", "rita"],
            path=".gitattributes"),
        rec("/api/v4/projects/10/uploads/abc/shot.png", "upload", PRIVATE, "10-work-items", ["admin", "dev", "rita", "gus"],
            filename="shot.png"),
        rec("/api/v4/projects/10/uploads/def/secret.png", "upload", PRIVATE, "10-work-items", ["admin", "dev", "rita"],
            filename="secret.png", internal_only=True),
        rec("201", "issue", PUBLIC, "11-work-items", ["admin", "dev", "rita", "gus", "olga", "blocked"], title="Public",
            anonymous=True),
    ],
}
SCOPE = Scope(projects=["acme/widgets"])


def live(scope: Scope = SCOPE, **override) -> list[dict]:
    """A faithful PipesHub: a record for everything wanted, filed where GitLab files it."""
    out = []
    for ext, want in wanted_records(ORACLE, scope).items():
        group = override.get(ext, want.group)
        out.append({"id": f"r:{ext}", "externalRecordId": ext, "externalGroupId": group, "recordName": ext})
    return out


def faithful(scope: Scope = SCOPE, *, known: bool = False) -> dict[str, set[str]]:
    """What a connector that follows every GitLab rule grants (``known``: members only, as the connector does)."""
    wanted = wanted_records(ORACLE, scope)
    out: dict[str, set[str]] = {e: set() for e in EMAILS}
    for ext, want in wanted.items():
        for u in USERS:
            if u["username"] in want.readers:
                out[u["email"]].add(f"r:{ext}")
    return out


def types(problems: list[dict]) -> set[str]:
    return {p["type"] for p in problems}


def test_a_faithful_sync_has_no_difference() -> None:
    assert compare(ORACLE, SCOPE, live(), faithful()) == []


def test_what_the_sync_wants_leaves_out_the_dotfile_the_internal_upload_and_the_other_project() -> None:
    wanted = set(wanted_records(ORACLE, SCOPE))
    assert wanted == {"101", "102", "103", "/acme/widgets/-/blob/HEAD/src/app.py", "/acme/widgets/-/tree/HEAD/src",
                      "/api/v4/projects/10/uploads/abc/shot.png"}


def test_an_upload_is_known_by_its_path_whatever_host_is_in_front() -> None:
    assert norm("http://gitlab-sim/api/v4/projects/10/uploads/abc/shot.png") == "/api/v4/projects/10/uploads/abc/shot.png"


def test_planted_over_share_is_caught() -> None:
    seen = faithful()
    seen["gus@example.com"].add("r:/acme/widgets/-/blob/HEAD/src/app.py")      # a guest reads the code
    problems = compare(ORACLE, SCOPE, live(), seen)
    assert types(problems) == {"LEAK:file-below-reporter"} and problems[0]["user"] == "gus@example.com"


def test_a_blocked_member_who_reads_is_a_leak() -> None:
    seen = faithful()
    seen["blocked@example.com"].add("r:101")
    assert types(compare(ORACLE, SCOPE, live(), seen)) == {"LEAK:inactive-user"}


def test_a_stranger_who_reads_is_a_leak() -> None:
    seen = faithful()
    seen["nobody@example.com"].add("r:101")
    assert types(compare(ORACLE, SCOPE, live(), seen)) == {"LEAK:not-a-gitlab-user"}


def test_planted_missing_grant_is_caught() -> None:
    seen = faithful()
    seen["rita@example.com"].discard("r:103")                                    # a reporter loses the merge request
    problems = compare(ORACLE, SCOPE, live(), seen)
    assert types(problems) == {"DENIED:merge_request"} and problems[0]["user"] == "rita@example.com"


def test_missing_record_is_caught() -> None:
    records = [r for r in live() if r["externalRecordId"] != "102"]
    assert types(compare(ORACLE, SCOPE, records, faithful())) == {"MISSING"}


def test_stale_record_is_caught() -> None:
    records = live() + [{"id": "r:gone", "externalRecordId": "999", "externalGroupId": "10-work-items", "recordName": "Gone"}]
    assert types(compare(ORACLE, SCOPE, records, faithful())) == {"STALE"}


def test_a_record_of_another_project_is_stale() -> None:
    records = live() + [{"id": "r:201", "externalRecordId": "201", "externalGroupId": "11-work-items", "recordName": "Public"}]
    assert types(compare(ORACLE, SCOPE, records, faithful())) == {"STALE"}


def test_duplicate_record_is_caught() -> None:
    records = live() + [{"id": "r:101b", "externalRecordId": "101", "externalGroupId": "10-work-items", "recordName": "Open"}]
    assert types(compare(ORACLE, SCOPE, records, faithful())) == {"DUPLICATE"}


def test_a_record_under_the_wrong_group_is_caught() -> None:
    assert types(compare(ORACLE, SCOPE, live(**{"102": "10-work-items"}), faithful())) == {"PARENT"}


def test_an_upload_may_sit_under_any_group_of_its_project_but_not_the_code() -> None:
    up = "/api/v4/projects/10/uploads/abc/shot.png"
    assert compare(ORACLE, SCOPE, live(**{up: "10-merge-requests"}), faithful()) == []
    assert types(compare(ORACLE, SCOPE, live(**{up: "10-code-repository"}), faithful())) == {"PARENT"}
    assert types(compare(ORACLE, SCOPE, live(**{up: "11-work-items"}), faithful())) == {"PARENT"}


def test_project_visibility_not_granted_is_the_known_gap_and_nothing_else() -> None:
    scope = Scope(projects=["acme/oss"])
    seen = faithful(scope)
    for who in ("dev", "rita", "gus", "olga"):         # members only: GitLab lets them read a public project
        seen[f"{who}@example.com"].clear()
    problems = compare(ORACLE, scope, live(scope), seen)
    assert types(problems) == {"DENIED:project-visibility"} <= set(KNOWN_GAPS)
    assert all(p["user"] != "admin@example.com" for p in problems)


def test_a_member_who_loses_a_private_project_is_not_the_known_gap() -> None:
    seen = faithful()
    seen["gus@example.com"].discard("r:101")
    assert types(compare(ORACLE, SCOPE, live(), seen)).isdisjoint(KNOWN_GAPS)


def test_a_stranger_does_not_find_an_anonymous_record_by_design() -> None:
    scope = Scope(projects=["acme/oss"])
    seen = faithful(scope)
    assert "nobody@example.com" in seen and not seen["nobody@example.com"]
    assert types(compare(ORACLE, scope, live(scope), seen)).isdisjoint({"LEAK:not-a-gitlab-user"})


def test_kinds_and_excluded_ids_narrow_what_is_wanted() -> None:
    assert set(wanted_records(ORACLE, Scope(projects=["acme/widgets"], kinds={"issue"}))) == {"101", "102"}
    assert "102" not in wanted_records(ORACLE, Scope(projects=["acme/widgets"], exclude={"102"}))


def test_a_deleted_project_has_no_wanted_record() -> None:
    gone = copy.deepcopy(ORACLE)
    gone["projects"][0]["deleted"] = True
    assert wanted_records(gone, SCOPE) == {}


def test_what_the_sync_account_cannot_read_is_not_wanted() -> None:
    hidden = copy.deepcopy(ORACLE)
    hidden["records"][1]["readers"] = ["dev"]                                    # the sync account (admin) is not a reader
    assert "102" not in wanted_records(hidden, SCOPE)


@pytest.mark.parametrize("who", ["admin", "dev", "rita", "gus"])
def test_every_member_who_may_read_the_work_items_is_expected_to(who: str) -> None:
    seen = faithful()
    seen[f"{who}@example.com"].discard("r:101")
    assert types(compare(ORACLE, SCOPE, live(), seen)) == {"DENIED:issue"}
