"""The github_teams_sim suite's comparison: what a sync must hold, and who may read it under GitHub's rules.

The oracle is a small hand-written one in the shape of the simulator's ``GET /_sim/github/expected``: one private
repository (owner and a team member read it, a member with no grant does not) with an issue, a pull request, a file and a
folder, a public repository with an issue, an upload on the private issue, and a deleted repository. Each negative control
plants one fault into PipesHub's answer and must be caught.
"""

from __future__ import annotations

import pytest
from connectors.github_teams.sim_world import (
    KNOWN_GAPS,
    Scope,
    Truth,
    call_problems,
    compare,
    spec_findings_only,
)

PRIV, PUB, GONE = 1004, 1005, 1006
CREATOR = "creator@x.example"
EMAILS = {"admin": "admin@x.example", "alice": "alice@x.example", "bob": "bob@x.example", "nogh": "nogh@x.example",
          CREATOR: CREATOR}


def _repo(rid: int, name: str, readers: list[str], public: bool = False, deleted: bool = False) -> dict:
    return {"id": rid, "key": name, "full_name": f"acme/{name}", "readers": readers, "public": public, "deleted": deleted}


ORACLE = {
    "sync_account": "admin",
    "users": [{"login": k, "email": v, "corporate_email": None} for k, v in EMAILS.items() if k in ("admin", "alice", "bob")],
    "repos": [_repo(PRIV, "widgets", ["admin", "alice"]), _repo(PUB, "open", ["admin", "alice", "bob"], public=True),
              _repo(GONE, "gone", ["admin"], deleted=True)],
    "issues": [
        {"repo_id": PRIV, "kind": "issue", "number": 1, "live": True, "public": False, "readers": ["admin", "alice"]},
        {"repo_id": PRIV, "kind": "pull", "number": 2, "live": True, "public": False, "readers": ["admin", "alice"]},
        {"repo_id": PRIV, "kind": "issue", "number": 3, "live": False, "public": False, "readers": ["admin", "alice"]},
        {"repo_id": PUB, "kind": "issue", "number": 1, "live": True, "public": True, "readers": ["admin", "alice", "bob"]},
        {"repo_id": GONE, "kind": "issue", "number": 1, "live": True, "public": False, "readers": ["admin"]},
    ],
    "files": [
        {"repo_id": PRIV, "branch": "main", "default_branch": True, "path": "src/app.py", "public": False, "readers": ["admin", "alice"]},
        {"repo_id": PRIV, "branch": "release", "default_branch": False, "path": "src/rel.py", "public": False, "readers": ["admin", "alice"]},
        {"repo_id": PUB, "branch": "main", "default_branch": True, "path": "README.md", "public": True, "readers": ["admin", "alice", "bob"]},
    ],
    "uploads": [{"url": "https://github.com/user-attachments/assets/a1", "owners": [{"repo_id": PRIV, "kind": "issue", "number": 1}]}],
}
URL = "https://github.com/user-attachments/assets/a1#1004/issues/1"
ALL = {
    "1004/issues/1": "TICKET", "1004/pull/2": "PULL_REQUEST", "/1004/blob/src/app.py": "CODE_FILE", "/1004/tree/src": "FILE",
    URL: "FILE", "1005/issues/1": "TICKET", "/1005/blob/README.md": "CODE_FILE",
}
SCOPE_PRIV = {"acme/widgets"}


def _records(extra: dict | None = None, drop: set | None = None, repos: set | None = None) -> list[dict]:
    def repo_of(ext: str) -> str:
        return "1004" if ext == URL else ext.lstrip("/").split("/")[0]

    keep = {k: v for k, v in ALL.items() if k not in (drop or set()) and (repos is None or repo_of(k) in repos)}
    keep.update(extra or {})
    return [{"id": f"id-{i}", "externalRecordId": ext, "recordType": kind, "recordName": ext} for i, (ext, kind) in enumerate(keep.items())]


def _readable(records: list[dict], who: dict[str, set[str]]) -> dict[str, set[str]]:
    by = {r["externalRecordId"]: r["id"] for r in records}
    return {EMAILS[p]: {by[e] for e in exts if e in by} for p, exts in who.items()}


PRIV_ALL = {"1004/issues/1", "1004/pull/2", "/1004/blob/src/app.py", "/1004/tree/src", URL}
WHO = {"admin": PRIV_ALL, "alice": PRIV_ALL, "bob": set(), "nogh": set(), CREATOR: PRIV_ALL}


def _run(records: list[dict], who: dict[str, set[str]], scope: Scope | None = None) -> list[dict]:
    return compare(ORACLE, scope or Scope(repos=set(SCOPE_PRIV), creator=CREATOR), records, _readable(records, who))


def _priv_records() -> list[dict]:
    return _records(repos={"1004"}) + []


def test_a_correct_answer_has_no_difference() -> None:
    rec = _priv_records()
    assert _run(rec, WHO) == []


def test_wanted_lists_what_a_complete_sync_holds() -> None:
    wanted = Truth(ORACLE, Scope(repos=set(SCOPE_PRIV))).wanted()
    assert set(wanted) == PRIV_ALL, set(wanted) ^ PRIV_ALL


def test_branch_filter_selects_the_branch_files() -> None:
    wanted = Truth(ORACLE, Scope(repos=set(SCOPE_PRIV), branch="release")).wanted()
    assert "/1004/blob/src/rel.py" in wanted and "/1004/blob/src/app.py" not in wanted


def test_planted_over_share_is_caught() -> None:
    rec = _priv_records()
    problems = _run(rec, {**WHO, "bob": PRIV_ALL})
    assert {p["type"] for p in problems} == {"LEAK"} and {p["user"] for p in problems} == {EMAILS["bob"]}


def test_planted_missing_grant_is_caught() -> None:
    rec = _priv_records()
    problems = _run(rec, {**WHO, "alice": set()})
    assert {p["type"] for p in problems} == {"DENIED"} and {p["user"] for p in problems} == {EMAILS["alice"]}


def test_missing_record_is_caught() -> None:
    rec = _records(drop={"1004/pull/2"}, repos={"1004"})
    assert [p for p in _run(rec, {k: v - {"1004/pull/2"} for k, v in WHO.items()}) if p["type"] == "MISSING"] == [
        {"type": "MISSING", "record": "1004/pull/2"}]


def test_stale_record_is_caught() -> None:
    rec = _priv_records() + [{"id": "x", "externalRecordId": "1004/issues/3", "recordType": "TICKET", "recordName": "gone"},
                             {"id": "y", "externalRecordId": "1006/issues/1", "recordType": "TICKET", "recordName": "repo gone"}]
    assert {p["record"] for p in _run(rec, WHO) if p["type"] == "STALE"} == {"1004/issues/3", "1006/issues/1"}


def test_duplicate_record_is_caught() -> None:
    rec = _priv_records() + [{"id": "dup", "externalRecordId": "1004/issues/1", "recordType": "TICKET", "recordName": "dup"}]
    assert [p["type"] for p in _run(rec, WHO) if p["type"] == "DUPLICATE"] == ["DUPLICATE"]


def test_a_repository_outside_the_scope_is_stale() -> None:
    rec = _records(repos={"1004", "1005"})
    assert {p["record"] for p in _run(rec, {**WHO}) if p["type"] == "STALE"} == {"1005/issues/1", "/1005/blob/README.md"}


def test_public_content_without_a_github_account_is_the_known_gap() -> None:
    rec = _records(repos={"1005"})
    scope = Scope(repos={"acme/open"}, creator=CREATOR)
    who = {"admin": {"1005/issues/1", "/1005/blob/README.md"}, "alice": {"1005/issues/1", "/1005/blob/README.md"},
           "bob": {"1005/issues/1", "/1005/blob/README.md"}, CREATOR: {"1005/issues/1", "/1005/blob/README.md"},
           "nogh": {"1005/issues/1", "/1005/blob/README.md"}}
    types = {p["type"] for p in _run(rec, who, scope)}
    assert types == {"LEAK:public-without-account"} and set(types) <= set(KNOWN_GAPS)


def test_personal_connector_is_read_by_the_creator_alone() -> None:
    rec = _priv_records()
    scope = Scope(repos=set(SCOPE_PRIV), creator=CREATOR, personal=True)
    only_creator = {"admin": set(), "alice": set(), "bob": set(), "nogh": set(), CREATOR: PRIV_ALL}
    assert _run(rec, only_creator, scope) == []
    assert {p["type"] for p in _run(rec, WHO, scope)} == {"LEAK"}


def test_a_spec_finding_listed_as_known_is_dropped_others_are_kept() -> None:
    problems = [{"op": "GET /x", "problems": ["'q' is not declared"]}, {"op": "GET /y", "problems": ["bad"]}]
    assert spec_findings_only(problems) == problems  # none is listed as known in this suite


def test_calls_to_other_hosts_and_a_token_at_the_storage_host_are_caught() -> None:
    calls = [{"host": "api.github.com", "path": "/user", "authorization": True},
             {"host": "github.com", "path": "/user-attachments/assets/a", "authorization": True},
             {"host": "evil.example", "path": "/", "authorization": False},
             {"host": "private-user-images.githubusercontent.com", "path": "/k", "authorization": True},
             {"host": "github.com", "path": "/login/oauth/access_token", "authorization": True}]
    kinds = [(p["type"], p["host"]) for p in call_problems(calls)]
    assert kinds == [("SIM_HOST", "evil.example"), ("SIM_TOKEN_LEAK", "private-user-images.githubusercontent.com"),
                     ("SIM_TOKEN_LEAK", "github.com")]


@pytest.mark.parametrize("gap", sorted(KNOWN_GAPS))
def test_every_known_gap_names_an_open_pull_request(gap: str) -> None:
    assert KNOWN_GAPS[gap].startswith("#")
