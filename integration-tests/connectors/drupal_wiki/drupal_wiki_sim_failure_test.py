# pyright: ignore-file

"""
Drupal Wiki on the simulator – a sync that cannot read is not a success, and removes nothing
============================================================================================

A sync whose reads were refused or failed must end with lastSyncOutcome "failed" (or, if the connector read everything
in the end, match the wiki), never "succeeded" with something missing, and must leave behind what it could not confirm:
every record stays and nobody reads what the wiki does not let them read. Once the wiki answers again, the next sync
must match it. Every injected fault must fire, or the test proves nothing.

The endpoints the sync calls (the keys of the simulator's ``/_sim/calls``; GraphQL is one path, told apart by order: the
member queries of the private spaces come first, then one page tree per space):

  user, group, group/{id}, space, page, attachment (the listing of one page), graphql members, graphql tree

  TC-DWSIM-FAIL-000  The sync's endpoints are exactly the ones the matrix below covers (compared with ``/_sim/calls``)
  TC-DWSIM-FAIL-001  Every endpoint x {401, 403, 429, 503, dropped connection, truncated body, a body of another shape},
                     failing for the whole run, at its first request: not succeeded, nothing lost, nothing over-shared,
                     and the next clean sync matches the wiki
  TC-DWSIM-FAIL-002  The same in the middle of a listing (the wiki lists three rows a page, the first window is read)
                     for user, group, space and page listings
  TC-DWSIM-FAIL-003  A fault that passes (a few failures, then answers): the sync either read everything and matches the
                     wiki, or did not succeed and removed nothing
  TC-DWSIM-FAIL-004  A full sync that fails after some pages were read removes nothing and over-shares nothing
  TC-DWSIM-FAIL-006  An attachment listed without an id [#5799: fails the run; today it is stored as attachment:None]
  TC-DWSIM-FAIL-005  A listing that ends early: ``last`` missing on a page that is not the last, and a listing that
                     never says ``last`` (ends on an empty page)
"""

import re

import pytest
from connectors.drupal_wiki.sim_instances import ALICE, LEADERSHIP_SPACE
from connectors.drupal_wiki.sim_world import DrupalWorld, succeeded

API = "/api/rest/scope/api"
ENDPOINTS = {
    "user": rf"^{API}/user$",
    "group": rf"^{API}/group$",
    "group_detail": rf"^{API}/group/\d+$",
    "space": rf"^{API}/space$",
    "page": rf"^{API}/page$",
    "attachment": rf"^{API}/attachment$",
    "graphql_members": r"^/graphql$",
    "graphql_tree": r"^/graphql$",
}
# What the sync calls, as the simulator names it (``/_sim/calls``). Listing the pages' bodies, downloads and the
# stream probe is the content suite's.
SYNC_CALLS = {
    "GET /api/rest/scope/api/user", "GET /api/rest/scope/api/group", "GET /api/rest/scope/api/group/{group_id}",
    "GET /api/rest/scope/api/space", "GET /api/rest/scope/api/page", "GET /api/rest/scope/api/attachment",
    "POST /graphql",
}
# More failures than the connector's retries for every request a run makes.
KEEPS_FAILING = 500
PRIVATE_SPACES = 2  # Seed: People Ops and Leadership. The first GraphQL calls read their members.
ATTEMPTS = 4        # The connector tries a request up to four times (three retries).

KINDS = {
    "401": {"effect": {"type": "status", "code": 401}},
    "403": {"effect": {"type": "status", "code": 403}},
    "429": {"effect": {"type": "status", "code": 429}},
    "503": {"effect": {"type": "status", "code": 503}},
    "reset": {"effect": {"type": "reset"}},
    "truncated": {"effect": {"type": "truncate", "bytes": 1}},
    "shape": {"effect": {"type": "shape", "ops": []}},   # filled in per endpoint (SHAPES)
}
# A body of another shape: the field the connector reads holds something else.
SHAPES = {
    "user": [{"path": "content", "value": "oops"}], "group": [{"path": "content", "value": "oops"}],
    "group_detail": [{"path": "members", "value": "oops"}], "space": [{"path": "content", "value": "oops"}],
    "page": [{"path": "content", "value": "oops"}], "attachment": [{"path": "[*].id", "delete": True}],
    "graphql_members": [{"path": "data.spaceRoleUserMembers", "value": "oops"}],
    "graphql_tree": [{"path": "data.spaceContentStructureFlatTree", "value": "oops"}],
}


def rule(endpoint: str, kind: str, *, after_n: int | None = None, first_n: int | None = None,
         max_times: int = KEEPS_FAILING) -> dict:
    trigger: dict = {"max_times": max_times}
    if endpoint == "graphql_members":
        trigger["first_n"] = PRIVATE_SPACES * ATTEMPTS
        trigger.pop("max_times")
    elif endpoint == "graphql_tree":
        trigger["after_n"] = PRIVATE_SPACES
    if after_n is not None:
        trigger["after_n"] = after_n
    if first_n is not None:
        trigger["first_n"] = first_n
        trigger.pop("max_times", None)
    effect = KINDS[kind] if kind != "shape" else {"effect": {"type": "shape", "ops": SHAPES[endpoint]}}
    return {"match": {"path_regex": ENDPOINTS[endpoint]}, **effect, "trigger": trigger}


async def _change_the_wiki(w: DrupalWorld, tag: str) -> str:
    """A page and a role added, which an unread run must not make anyone hold, and an edit of a page with attachments."""
    title = f"Filed while {tag}"
    w.mutate(("page_create", {"space": 1, "title": title}),
             ("member", {"space": LEADERSHIP_SPACE, "principal_type": "user", "principal_id": ALICE}),
             ("page_edit", {"id": 105, "body_extra": f"<p>{tag}</p>"}))
    return title


@pytest.mark.integration
@pytest.mark.drupal_wiki_sim
@pytest.mark.asyncio(loop_scope="session")
class TestDrupalWikiSimFailure:

    async def test_tc_dwsim_fail_000_endpoint_inventory(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        called = set(w.sim.calls()["endpoints"])
        sync_only = {c for c in called if not re.search(r"/(download|page/\{page_id\}|attachment/\{attachment_id\})",
                                                        c)}
        assert sync_only == SYNC_CALLS, (
            f"the sync calls {sorted(sync_only - SYNC_CALLS)} that the fault matrix does not cover, and the matrix "
            f"names {sorted(SYNC_CALLS - sync_only)} it never calls")
        assert not w.sim.call("GET", "/_sim/drupalwiki/gaps")

    @pytest.mark.parametrize("endpoint", list(ENDPOINTS))
    async def test_tc_dwsim_fail_001_every_kind_of_failure_at_the_first_request(
            self, drupal_world: DrupalWorld, endpoint: str) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")
        before = await w.holdings()
        for kind in KINDS:
            if (endpoint, kind) == ("attachment", "shape"):
                continue    # TC-DWSIM-FAIL-006 below: the connector stores what the wiki sent
            await _change_the_wiki(w, f"{endpoint} answered {kind}")
            w.faults(rule(endpoint, kind))
            outcome = await w.sync()
            w.clear_faults()
            label = f"{endpoint} {kind}"
            assert not succeeded(outcome), f"a sync whose {label} read failed for the whole run reported success"
            before = await w.assert_kept_not_overshared(before, f"after a sync whose {label} failed")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the wiki answered again")

    @pytest.mark.parametrize("endpoint", ["user", "group", "space", "page"])
    async def test_tc_dwsim_fail_002_failure_in_the_middle_of_a_listing(
            self, drupal_world: DrupalWorld, endpoint: str) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"max_page_size": 3}))
        await w.assert_matches_source("after the first sync")
        before = await w.holdings()
        for kind in ("503", "429", "reset", "truncated"):
            await _change_the_wiki(w, f"{endpoint} listing broke with {kind}")
            w.faults(rule(endpoint, kind, after_n=1))
            outcome = await w.sync()
            w.clear_faults()
            assert not succeeded(outcome), f"a sync whose {endpoint} listing failed ({kind}) after its first window "\
                                           "reported success"
            before = await w.assert_kept_not_overshared(before, f"after a sync whose {endpoint} listing failed ({kind}) midway")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the wiki answered again")

    @pytest.mark.parametrize("endpoint", ["user", "page", "attachment", "graphql_members"])
    async def test_tc_dwsim_fail_003_a_fault_that_passes(self, drupal_world: DrupalWorld, endpoint: str) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        before = await w.holdings()
        await _change_the_wiki(w, f"{endpoint} failed twice")
        w.faults(rule(endpoint, "503", max_times=2, first_n=2 if endpoint == "graphql_members" else None))
        outcome = await w.sync()
        w.clear_faults()
        if succeeded(outcome):
            await w.assert_matches_source(f"after a sync that reported success despite two failures of {endpoint}")
            return
        await w.assert_kept_not_overshared(before, f"after a sync that did not succeed ({endpoint} failed twice)")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the wiki answered again")

    async def test_tc_dwsim_fail_004_full_sync_fails_after_some_pages(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"max_page_size": 3}))
        before = await w.holdings()
        w.mutate(("page_delete", {"id": 109}), ("page_delete", {"id": 303}))
        w.faults(rule("page", "503", after_n=2))
        outcome = await w.sync(full=True)
        w.clear_faults()
        assert not succeeded(outcome), "a full sync whose page listing failed after two windows reported success"
        await w.assert_kept_not_overshared(before, "after a full sync that failed midway")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the clean full sync")

    @pytest.mark.xfail(strict=True, reason="an attachment listed without an id is stored as attachment:None and the run "
                                           "succeeds; open pull request #5799 fails the run instead")
    async def test_tc_dwsim_fail_006_an_attachment_listed_without_an_id(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        before = await w.holdings()
        await _change_the_wiki(w, "attachments were listed without ids")
        w.faults(rule("attachment", "shape"))
        outcome = await w.sync()
        w.clear_faults()
        assert not succeeded(outcome), "a sync that listed attachments without ids reported success"
        await w.assert_kept_not_overshared(before, "after attachments were listed without ids")

    async def test_tc_dwsim_fail_005_a_listing_that_ends_early_or_never(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(knobs={"max_page_size": 3}))
        await w.assert_matches_source("after the first sync")
        before = await w.holdings()
        title = await _change_the_wiki(w, "the user listing lost its last marker")
        w.faults({"match": {"path_regex": ENDPOINTS["space"]},
                  "effect": {"type": "shape", "ops": [{"path": "last", "delete": True}]}})
        outcome = await w.sync()
        w.clear_faults()
        if succeeded(outcome):
            await w.assert_matches_source(f"after the space listing lost `last` ({title})")
        else:
            await w.assert_kept_not_overshared(before, "after a listing that lost `last`")
        w.faults({"match": {"path_regex": ENDPOINTS["page"]},
                  "effect": {"type": "shape", "ops": [{"path": "last", "value": False}]}})
        outcome = await w.sync()
        w.clear_faults()
        assert succeeded(outcome), "a page listing that never says `last` (it ends on an empty page) failed the sync"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the wiki answered again")
