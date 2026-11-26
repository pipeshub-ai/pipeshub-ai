# pyright: ignore-file

"""
GitHub Teams on the simulator: failures of the source
=====================================================

For every endpoint the connector calls: a transient failure (one 503, one dropped connection, a 429 with Retry-After) is
recovered from, and a lasting one (503, 403) fails the run without losing a record or giving anybody access. After each,
the next clean run equals the source. A fault that never fired fails its test. A lasting fault costs the client's retries
(three tries with a backoff), so each is bounded to one run.

Endpoints that cannot affect each other share a connector: the lasting cases run in six groups (people, repository,
issues, pull requests, code, GraphQL); within a group the endpoints are faulted together, in one run.

  TC-GHSIM-FAIL-001  The routes a sync calls are exactly the list the fault cases cover [I, O]
  TC-GHSIM-FAIL-002  One 503 on each of the endpoints of a first sync: recovered, equals the source [I]
  TC-GHSIM-FAIL-003  One dropped connection on the listings and the repository: recovered [I]
  TC-GHSIM-FAIL-004  A 429 with Retry-After on the issue reads: waited out, equals the source [I]
  TC-GHSIM-FAIL-005  Lasting 503 per group of endpoints: the run does not report success unless everything was read,
                     nothing is lost, nobody gains, and the next clean run equals the source [I]
  TC-GHSIM-FAIL-006  A lasting 403 on the collaborators listing: nobody gains, nothing is lost [I, E]
  TC-GHSIM-FAIL-007  The repository unreachable for a moment (503 on both its addresses): nothing removed [I, F]
  TC-GHSIM-FAIL-008  A spent hourly pool fails the run and loses nothing; after the window the run equals the source [I]
  TC-GHSIM-FAIL-009  A truncated body on the issues listing: the run is not a success, nothing is lost [I]
  TC-GHSIM-FAIL-010  A listing failing from its second request on (150 issues): checkpoint withheld, next run completes [H, I]
"""

import re

import pytest
from connectors.github_teams.sim_instances import acme, issues, rich
from connectors.github_teams.sim_world import (
    CALLED,
    GithubWorld,
    drop,
    fault,
    succeeded,
    throttle,
)

NUMBER = re.compile(r"/(\d+)(?=/|$)")
SHA = re.compile(r"/[0-9a-f]{40}(?=/|$)")


def normalised(routes: set[str]) -> set[str]:
    """Routes with ids, numbers and hashes replaced by their kind."""
    out = set()
    for r in routes:
        r = SHA.sub("/{sha}", r)
        r = re.sub(r"/users/[^/]+$", "/users/{login}", r)
        out.add(NUMBER.sub("/{n}", r))
    return out


# What a first sync of ``rich()`` over one repository calls.
EXPECTED_ROUTES = {
    "GET /user", "GET /users/{login}", "GET /orgs/acme/members", "GET /orgs/acme/outside_collaborators",
    "GET /repos/acme/widgets", "GET /repositories/{n}", "GET /repos/acme/widgets/collaborators",
    "GET /repos/acme/widgets/branches/main", "GET /repos/acme/widgets/contents/.gitattributes",
    "GET /repos/acme/widgets/issues", "GET /repos/acme/widgets/issues/{n}", "GET /repos/acme/widgets/issues/{n}/comments",
    "GET /repos/acme/widgets/issues/{n}/dependencies/blocking",
    "GET /repos/acme/widgets/pulls", "GET /repos/acme/widgets/pulls/{n}", "GET /repos/acme/widgets/pulls/{n}/comments",
    "GET /repos/acme/widgets/pulls/{n}/commits", "GET /repos/acme/widgets/pulls/{n}/files",
    "GET /repos/acme/widgets/pulls/{n}/reviews",
    "GET /repos/acme/widgets/git/trees/{sha}", "GET /repos/acme/widgets/git/blobs/{sha}", "POST /graphql",
}

# Faults for the first sync (each fires once) and the groups of lasting faults: name -> (rules, a change that makes the
# next run read the endpoints, is the run allowed to report success while a rule fires).
ONE_SHOT = [r"^/user$", r"^/orgs/acme/members$", r"^/orgs/acme/outside_collaborators$", r"^/repos/acme/widgets$",
            r"/collaborators$", r"/issues$", r"/issues/1$", r"/issues/1/comments$", r"/issues/5/dependencies/blocking$",
            r"/pulls$", r"/pulls/6/(files|reviews)$", r"/git/trees/", r"/git/blobs/"]

GROUPS: dict[str, tuple[list[dict], list[dict]]] = {
    "people": ([fault(r"^/user$"), fault(r"^/orgs/acme/members$"), fault(r"^/orgs/acme/outside_collaborators$")],
               [{"op": "add_issue", "repo": "widgets", "title": "People change"}]),
    "repository": ([fault(r"^/repos/acme/widgets$"), fault(r"^/repositories/\d+$"), fault(r"/branches/main$")],
                   [{"op": "add_issue", "repo": "widgets", "title": "Repository change"}]),
    "issues": ([fault(r"/issues$"), fault(r"/issues/\d+/comments$"), fault(r"/dependencies/blocking$")],
               [{"op": "add_issue", "repo": "widgets", "title": "Issue change", "key": "chg",
                 "comments": [{"user": "bob", "body": "late"}]},
                {"op": "add_issue", "repo": "widgets", "title": "Blocks chg", "blocking": ["chg"]}]),
    "pulls": ([fault(r"/pulls$"), fault(r"/pulls/\d+/(files|reviews|comments|commits)$")],
              [{"op": "edit_pull", "pull": "fix", "title": "Fix login v2"},
               {"op": "add_review", "pull": "fix", "user": "alice", "state": "APPROVED", "body": "ok"}]),
    "code": ([fault(r"/git/trees/"), fault(r"/git/blobs/"), fault(r"/compare/"), fault(r"/commits$"), fault(r"/contents/")],
             [{"op": "commit", "repo": "widgets", "changes": {"src/new_file.py": "new = 1\n", "docs/x.md": "x\n"}}]),
    "graphql": ([fault(r"^/graphql$", method="POST")],
                [{"op": "add_issue", "repo": "widgets", "title": "Graph change"},
                 {"op": "commit", "repo": "widgets", "changes": {"src/g.py": "g = 1\n"}}]),
}


@pytest.mark.integration
@pytest.mark.github_teams_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGithubSimFailure:

    async def test_tc_ghsim_fail_001_the_routes_a_sync_calls(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        called = normalised(w.sim.routes())
        CALLED.update(called)
        # a person's profile is read only when none is stored from the last day, so /users/{login} may be absent
        expected = EXPECTED_ROUTES - {"GET /users/{login}"}
        assert expected <= called <= EXPECTED_ROUTES, (
            f"the fault cases cover {sorted(expected - called)} which a sync no longer calls, and miss "
            f"{sorted(called - EXPECTED_ROUTES)}")

    async def test_tc_ghsim_fail_002_one_503_everywhere(self, github_world: GithubWorld) -> None:
        w = github_world
        await w.start(acme(), ops=rich(), enable=False)
        w.faults(*[fault(p, 503, max_times=1) for p in ONE_SHOT], fault(r"^/graphql$", 503, method="POST", max_times=1))
        assert succeeded(await w.enable()), "a single 503 per endpoint was not recovered from"
        log = w.sim.call("GET", "/_sim/faults/log")
        fired = w.clear_faults()
        silent = sorted(set(range(len(ONE_SHOT) + 1)) - {e["rule"] for e in log})
        assert not silent, f"faults that never fired (rules {silent} of {ONE_SHOT} + graphql): the endpoint was not called"
        assert fired == len(ONE_SHOT) + 1
        await w.assert_matches_source("after one 503 on every endpoint")

    async def test_tc_ghsim_fail_003_one_dropped_connection(self, github_world: GithubWorld) -> None:
        w = github_world
        await w.start(acme(), ops=rich(), enable=False)
        w.faults(*[drop(p, max_times=1) for p in (r"/issues$", r"/pulls$", r"^/repos/acme/widgets$", r"/collaborators$", r"/git/trees/")])
        assert succeeded(await w.enable()), "a dropped connection was not recovered from"
        assert w.clear_faults() == 5
        await w.assert_matches_source("after dropped connections")

    async def test_tc_ghsim_fail_004_throttled_with_retry_after(self, github_world: GithubWorld) -> None:
        w = github_world
        await w.start(acme(), ops=rich(), enable=False)
        # a bucket of one request: the second and later reads of each kind are answered 429 with Retry-After 1
        w.faults(throttle(r"/issues/\d+$"), throttle(r"/issues/\d+/comments$"))
        assert succeeded(await w.enable()), "a 429 with Retry-After was not waited out"
        assert w.clear_faults() >= 2
        await w.assert_matches_source("after throttling")

    async def test_tc_ghsim_fail_005_lasting_failures_per_group(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        failures = []
        for name, (rules, change) in GROUPS.items():
            w.ops(*change)
            before = await w.holdings()
            w.faults(*rules)
            outcome = await w.sync()
            fired = w.clear_faults()
            try:
                if succeeded(outcome):
                    # reported success: everything must have been read
                    await w.assert_matches_source(f"{name}: a run that reported success")
                else:
                    await w.assert_holdings_kept(before, f"{name}: after the failed run", new_records_ok=True)
            except AssertionError as exc:
                failures.append(f"{name} ({fired} faults fired, run {outcome}): {exc}")
                continue
            assert succeeded(await w.sync()), f"{name}: the next clean run failed"
            try:
                await w.assert_matches_source(f"{name}: the next clean run")
            except AssertionError as exc:
                failures.append(f"{name} (clean run after): {exc}")
        assert not failures, "\n".join(failures)

    async def test_tc_ghsim_fail_006_collaborators_forbidden(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        before = await w.holdings()
        w.ops({"op": "add_issue", "repo": "widgets", "title": "During the 403"})
        w.faults(fault(r"/collaborators$", 403, message="Must have push access to view repository collaborators."))
        outcome = await w.sync()
        w.clear_faults()
        await w.assert_holdings_kept(before, f"after a 403 on the collaborators listing (run {outcome})", new_records_ok=True)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the collaborators came back")

    async def test_tc_ghsim_fail_007_repository_unreachable_for_a_moment(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        before = await w.holdings()
        w.faults(fault(r"^/repos/acme/widgets$"), fault(rf"^/repositories/{w.repo_id()}$"))
        outcome = await w.sync()
        w.clear_faults()
        assert not succeeded(outcome), "a run that could not look the repository up reported success"
        await w.assert_holdings_kept(before, "after the repository was unreachable")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the repository came back")

    async def test_tc_ghsim_fail_008_hourly_pool_spent(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        before = await w.holdings()
        w.ops({"op": "add_issue", "repo": "widgets", "title": "During"}, {"op": "exhaust", "resource": "core", "seconds": 1800})
        assert not succeeded(await w.sync()), "a spent pool did not fail the run"
        await w.assert_holdings_kept(before, "after the pool was spent")
        w.ops({"op": "advance", "seconds": 2000})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the pool refilled")

    async def test_tc_ghsim_fail_009_truncated_issue_listing(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich()))
        before = await w.holdings()
        w.ops({"op": "add_issue", "repo": "widgets", "title": "Cut off"})
        rule = {"match": {"path_regex": r"/issues$", "method": "GET"}, "effect": {"type": "truncate", "bytes": 40}}
        w.faults(rule)
        outcome = await w.sync()
        w.clear_faults()
        assert not succeeded(outcome), "a run that read a cut-off listing reported success"
        await w.assert_holdings_kept(before, "after a truncated listing", new_records_ok=True)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the listing came whole")

    async def test_tc_ghsim_fail_010_listing_fails_from_its_second_request(self, github_world: GithubWorld) -> None:
        w = github_world
        await w.start(acme(), ops=issues(150), enable=False)
        w.faults(fault(r"/issues$", 503, after_n=1))
        assert not succeeded(await w.enable())
        w.clear_faults()
        assert succeeded(await w.sync())
        assert len([r for r in await w.records() if r["recordType"] == "TICKET"]) == 150
        await w.assert_matches_source("after the listing resumed")
