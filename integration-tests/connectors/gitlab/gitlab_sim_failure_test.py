# pyright: ignore-file

"""
GitLab on the simulator – a sync that cannot read is not a success, and removes nothing
=======================================================================================

For every endpoint the connector calls (the list is asserted against the simulator's call log) there is a transient
case (GitLab answers 429 with Retry-After, or 503 once: the run recovers and equals GitLab) and a lasting one (403, or
503 for ever: nothing stored is lost, nobody gains access, a run that reports success equals GitLab, and the next clean
run equals GitLab). Endpoints whose faults cannot affect each other share one connector. Every fault must fire.
Live twin: none (the live suite cannot make GitLab fail). Harness scenarios: fault_429_issues, fault_502_members,
fault_503_graphql, fault_reset_merge_requests, fault_500_issues_page_2.

  TC-GLSIM-FAIL-001  every endpoint the connector calls is in the fault matrix (the call log of a full run)
  TC-GLSIM-FAIL-002  a 503 once on each endpoint at the same time: the run recovers and equals GitLab
  TC-GLSIM-FAIL-003  a 429 with Retry-After once on each endpoint: the run recovers and equals GitLab
  TC-GLSIM-FAIL-004  a dropped connection once on each listing: the run recovers and equals GitLab
  TC-GLSIM-FAIL-005  lasting 403 on each endpoint in turn (one connector): safety holds; success means equal to GitLab
  (FAIL-006, a lasting 503, is not a case: python-gitlab retries a 5xx ten times with a growing wait, about two minutes
   a request, so the lasting case is the 403 of FAIL-005 and the repeated one is the 429/503 that recovers)
  TC-GLSIM-FAIL-007  the second page of a 110-issue listing fails once and then for good: nothing lost, next run equal
  TC-GLSIM-FAIL-008  GitLab answers 404 for the project (gone, or not visible to the token): nobody gains access, and the
                     next run with the project back equals GitLab
  TC-GLSIM-FAIL-009  a full sync refused the members keeps every grant but the assignees' of confidential issues (fail closed)
"""

import re

import pytest

from connectors.gitlab.sim_instances import base_instance, email_of, issues
from connectors.gitlab.sim_world import (
    KNOWN_GAPS,
    GitLabWorld,
    fault,
    reset_fault,
    succeeded,
)

PID = r"(?:\d+|acme(?:%2F|/)widgets)"
# Name -> path regex of the endpoints, in the order the connector reaches them. Independent of the project id.
ENDPOINTS: dict[str, str] = {
    "user": r"^/api/v4/user$",
    "project": rf"^/api/v4/projects/{PID}$",
    "group": r"^/api/v4/groups/acme$",
    "group_members": r"^/api/v4/groups/acme/members$",
    "project_members": rf"^/api/v4/projects/{PID}/members/all$",
    "issues": rf"^/api/v4/projects/{PID}/issues$",
    "issue": rf"^/api/v4/projects/{PID}/issues/\d+$",
    "issue_notes": rf"^/api/v4/projects/{PID}/issues/\d+/notes$",
    "merge_requests": rf"^/api/v4/projects/{PID}/merge_requests$",
    "merge_request": rf"^/api/v4/projects/{PID}/merge_requests/\d+$",
    "merge_request_notes": rf"^/api/v4/projects/{PID}/merge_requests/\d+/notes$",
    "merge_request_changes": rf"^/api/v4/projects/{PID}/merge_requests/\d+/changes$",
    "merge_request_commits": rf"^/api/v4/projects/{PID}/merge_requests/\d+/commits$",
    "branch": rf"^/api/v4/projects/{PID}/repository/branches/[^/]+$",
    "commits": rf"^/api/v4/projects/{PID}/repository/commits$",
    "files": rf"^/api/v4/projects/{PID}/repository/files/.+$",
    "uploads": rf"^/api/v4/projects/{PID}/uploads/.+$",
    "compare": rf"^/api/v4/projects/{PID}/repository/compare$",
    "graphql": r"^/api/graphql$",
    "tree": rf"^/api/v4/projects/{PID}/repository/tree$",
}
# Read only when the GraphQL walk fails: the REST listing of the tree is its fallback.
FALLBACK_ONLY = ("tree",)
# Read only by a first or a full sync (the whole repository's walk, and the code changes and commits of each merge
# request); every other run compares commits and reads what changed.
FULL_ONLY = ("graphql", "tree", "merge_request_changes", "merge_request_commits")
# What each endpoint's faults can reach: endpoints that can hide behind another (an issue's notes are only read for an
# issue the listing returned) are in the same group, and a group is faulted by itself in the lasting matrix.
GROUPS: list[list[str]] = [
    ["user"], ["project"], ["group", "group_members"], ["project_members"], ["issues", "issue", "issue_notes"],
    ["merge_requests", "merge_request", "merge_request_notes"], ["merge_request_changes", "merge_request_commits"],
    ["branch", "commits", "compare"], ["files"], ["uploads"], ["graphql"], ["graphql", "tree"],
]


# OUTCOME pins: the endpoint groups whose lasting fault still leaves a run recorded as succeeded while PipesHub differs
# from GitLab (the change that was not read is missing). Safety holds for them (nothing lost, nobody gains). Each is a
# known gap, with its cause: GitLab answering 403 or 404 is taken for "refused for good: an answer, not a gap"
# (backend/python/app/connectors/sources/gitlab/connector.py note_failed_read), so the run carries on without it.
SILENT_403: set[str] = {"project", "issues+issue+issue_notes", "merge_requests+merge_request+merge_request_notes"}


def fold(path: str) -> str:
    return re.sub(r"/[0-9]+", "/:id", path)


async def churn(w: GitLabWorld) -> None:
    """Change what every endpoint reads, so a run that cannot read it has something to lose. Each call changes
    everything again (a counter in the content), so two calls in a row are two real changes."""
    w.churned = getattr(w, "churned", 0) + 1
    n = w.churned
    w.ops({"op": "add_upload", "project": "widgets", "key": f"up{n}", "filename": f"shot{n}.txt", "text": f"shot {n}"},
          {"op": "set_issue", "issue": "open", "title": f"Open issue v{n}"},
          {"op": "add_note", "issue": "open", "author": "dev", "body": f"a later comment {n}"},
          {"op": "add_issue", "project": "widgets", "title": f"Brand new {n}", "author": "dev",
           "description": f"[s]({{{{upload:up{n}}}}})"},
          {"op": "set_mr", "mr": "mr1", "title": f"Add feature v{n}"},
          {"op": "add_mr", "project": "widgets", "title": f"Second MR {n}", "author": "dev"},
          {"op": "commit", "project": "widgets", "files": {"src/extra.py": f"y = {n}\n", "README.md": f"# v{n}\n"}})


@pytest.mark.integration
@pytest.mark.gitlab_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGitLabSimFailure:

    async def test_tc_glsim_fail_001_every_endpoint_is_in_the_matrix(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        await churn(w)
        assert succeeded(await w.sync())
        assert succeeded(await w.sync(full=True))
        called = [c["path"] for c in w.sim.calls() if c["status"] < 400 or c["status"] in (301,)]
        unmatched = sorted({fold(p) for p in called if not any(re.search(rx, p) for rx in ENDPOINTS.values())})
        assert not unmatched, f"the connector calls endpoints the fault matrix does not name: {unmatched}"
        unused = sorted(n for n, rx in ENDPOINTS.items()
                        if n not in FALLBACK_ONLY and not any(re.search(rx, p) for p in called))
        assert not unused, f"endpoints of the matrix the connector never called: {unused}"

    async def _recovers(self, w: GitLabWorld, rule_for, label: str, names: list[str] | None = None) -> None:
        """Each endpoint fails once; the run recovers and equals GitLab. The endpoints a full walk reads are faulted in a
        full sync."""
        assert succeeded(await w.start(base_instance()))
        for full in (False, True):
            chosen = [n for n in (names or list(ENDPOINTS)) if (n in FULL_ONLY) == full and n not in FALLBACK_ONLY]
            if not chosen:
                continue
            await churn(w)
            w.faults(*[rule_for(ENDPOINTS[n]) for n in chosen])
            outcome = await w.sync(full=full)
            if "commits" in chosen:
                await w.wait_fired(ENDPOINTS["commits"])  # read by the timestamp task the run starts as it ends
            fired = w.clear_faults(every_rule=True)
            assert succeeded(outcome), f"{label}: the {'full ' if full else ''}run did not recover ({outcome}; {fired} fault(s))"
            await w.assert_matches_source(f"{label}: after the {'full ' if full else ''}run that recovered")

    async def test_tc_glsim_fail_002_one_503_on_every_endpoint(self, gitlab_world: GitLabWorld) -> None:
        await self._recovers(gitlab_world, lambda rx: fault(rx, 503, first_n=1), "503 once on every endpoint")

    async def test_tc_glsim_fail_003_one_429_on_every_endpoint(self, gitlab_world: GitLabWorld) -> None:
        await self._recovers(gitlab_world, lambda rx: fault(rx, 429, first_n=1), "429 once on every endpoint")

    async def test_tc_glsim_fail_004_one_dropped_connection_on_the_listings(self, gitlab_world: GitLabWorld) -> None:
        names = ["project_members", "issues", "merge_requests", "compare", "graphql"]
        await self._recovers(gitlab_world, lambda rx: reset_fault(rx, first_n=1), "a dropped connection", names)

    async def _lasting(self, w: GitLabWorld, group: list[str], code: int) -> tuple[str, list[str]]:
        """One run with a lasting fault on ``group``. Returns (outcome, safety failures). Safety: the fault fired,
        nothing stored is lost, nobody gains access to what was stored already, and the next clean run equals GitLab."""
        unsafe: list[str] = []
        label = f"{code} on {'+'.join(group)}"
        held = await w.holdings()
        await churn(w)
        w.faults(*[fault(ENDPOINTS[n], code) for n in group])
        outcome = await w.sync(full=any(n in FULL_ONLY for n in group))
        try:
            w.clear_faults()
        except AssertionError as exc:
            unsafe.append(f"{label}: {exc}")
        after = await w.holdings()
        new = after["records"] - held["records"]
        lost = {who: sorted(held[who] - after[who]) for who in held if held[who] - after[who]}
        gains = {who: sorted(after[who] - held[who] - new) for who in held
                 if who != "records" and after[who] - held[who] - new}
        if lost:
            unsafe.append(f"{label}: lost {lost}")
        if gains:
            unsafe.append(f"{label}: gained access to what was stored already: {gains}")
        silent = succeeded(outcome) and [p for p in await w.differences() if p["type"] not in KNOWN_GAPS]
        if not succeeded(await w.sync()):
            unsafe.append(f"{label}: the next clean run did not succeed")
        else:
            left = [p for p in await w.differences() if p["type"] not in KNOWN_GAPS]
            if left:
                unsafe.append(f"{label}: after the next clean run PipesHub differs from GitLab: {left[:5]}")
        w.silent[(label, "+".join(group))] = bool(silent)
        return outcome, unsafe

    async def _matrix(self, w: GitLabWorld, groups: list[list[str]], code: int) -> tuple[set[str], list[str]]:
        silent: set[str] = set()
        unsafe: list[str] = []
        for group in groups:
            _, bad = await self._lasting(w, group, code)
            unsafe += bad
            silent |= {name for (_, name), flag in w.silent.items() if flag}
        return silent, unsafe

    async def test_tc_glsim_fail_005_lasting_403_on_each_endpoint(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        silent, unsafe = await self._matrix(w, GROUPS, 403)
        assert not unsafe, f"a lasting 403 lost or gave away something: {unsafe}"
        # OUTCOME, pinned both ways: a run that reports success while PipesHub still differs from GitLab.
        assert silent == SILENT_403, (
            f"runs that reported success without reading everything changed: now {sorted(silent)}, pinned "
            f"{sorted(SILENT_403)}")

    async def test_tc_glsim_fail_007_second_issue_page_fails(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        w.ops(*issues(110))
        w.faults(fault(ENDPOINTS["issues"], 500, after_n=1, max_times=1))
        outcome = await w.sync()
        w.clear_faults(every_rule=True)
        assert succeeded(outcome), f"the second page failed once and the run did not recover: {outcome}"
        await w.assert_matches_source("after the second page failed once")
        # And for good: the second page is refused (403) on the next run, which has 110 more to read.
        mid = await w.holdings()
        w.ops(*issues(110, prefix="Later"))
        w.faults(fault(ENDPOINTS["issues"], 403, after_n=1))
        await w.sync()
        w.clear_faults(every_rule=True)
        await w.assert_holdings_kept(mid, "after a run whose second page was refused", allow_gains=True)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the second page answers")

    async def test_tc_glsim_fail_008_project_gone_for_the_sync_account(self, gitlab_world: GitLabWorld) -> None:
        # GitLab answers 404 for a project that is gone or that the token cannot see: the connector takes the project
        # as removed and withdraws its records. Nobody may gain access, and the next run with the project back is GitLab.
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        held = await w.holdings()
        w.faults(fault(ENDPOINTS["project"], 404))
        await w.sync()
        w.clear_faults(every_rule=True)
        after = await w.holdings()
        gains = {who: sorted(after[who] - held[who]) for who in held if who != "records" and after[who] - held[who]}
        assert not gains, f"somebody gained access when GitLab said the project is gone: {gains}"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the project answers again")

    async def test_tc_glsim_fail_009_full_sync_without_members_keeps_grants(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        held = await w.holdings()
        w.faults(fault(ENDPOINTS["project_members"], 403))
        await w.sync(full=True)
        w.clear_faults(every_rule=True)
        # Every grant stays but one kind: the author's and assignees' access to a confidential issue is only given to
        # people the member list shows still read the project (issues.py _confidential_exception_permissions), so
        # with no list the connector fails closed and gives it to nobody until the next clean run.
        after = await w.holdings()
        lost = {who: sorted(held[who] - after[who]) for who in held if held[who] - after[who]}
        assert lost == {email_of("gus"): [str(w.key("conf"))]}, f"a full sync refused the members lost {lost}"
        assert not {who: after[who] - held[who] for who in held if after[who] - held[who]}, "somebody gained access"
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a clean full sync")
