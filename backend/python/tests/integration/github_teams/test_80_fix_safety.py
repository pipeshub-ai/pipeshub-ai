"""Removal acts only on a definite answer from GitHub, and data written by older
versions of the connector ends up consistent. Runs with the trash off and on.

Every test runs on Neo4j and ArangoDB (NEO4J_IT_URI / ARANGO_IT_URL) against the simulated
GitHub in ``sim/``; see README.md.
"""

import contextlib

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from tests.integration.github_teams.helpers import fail_from_nth, on
from tests.integration.github_teams.sim.model import Fault

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
BOTH_TRASH_MODES = True


async def _forget_issue_total(world, repo) -> None:
    """Make the work-items checkpoint look like one the old code wrote."""
    sp = world.connector.record_sync_point
    key = generate_record_sync_point_key(Connectors.GITHUB_TEAMS.value, f"{repo.id}-work-items", "")
    old = await sp.read_sync_point(key) or {}
    await sp.delete_sync_point(key)
    await sp.create_sync_point(key, {"last_sync_time": old.get("last_sync_time")})


async def test_upgrade_removes_issues_the_old_code_left_behind(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    keep = gh.add_issue(repo, "Keep")
    gone = gh.add_issue(repo, "Deleted before the upgrade")
    await world.sync()
    await _forget_issue_total(world, repo)
    gh.delete_issue(gone)
    await world.sync()
    assert await world.names("TICKET") == [keep.title]


async def test_upgrade_rekeys_attachments_written_by_url(world, org) -> None:
    """The old code keyed an attachment by its URL alone. After the upgrade the record
    belongs to its issue and the URL-keyed one is gone."""
    gh, repo = world.gh, org["repo"]
    url = gh.upload(repo, "spec.pdf", b"%PDF spec")
    issue = gh.add_issue(repo, "Spec", body=f"[spec.pdf]({url})")
    await world.sync()
    ticket = await world.record(f"{repo.id}/issues/{issue.number}")
    legacy = world.connector.comments._attachment_file_update(
        {"type": "pdf", "href": url, "filename": "spec.pdf"}, ticket, None, ticket.id).record
    legacy.external_record_id = url
    for key in list((await world.by_type("FILE")).keys()):
        if key.startswith("https://"):
            await world.processor.on_records_deleted_cascade([(await world.record(key)).id], world.connector_id)
    await world.processor.on_new_records([(legacy, [])])
    assert url in await world.by_type("FILE")
    gh.edit_issue(issue, title="Spec v2")
    await world.sync()
    files = [k for k in (await world.by_type("FILE")) if k.startswith("https://")]
    assert files == [f"{url}#{repo.id}/issues/{issue.number}"]
    diff = await world.diff()
    assert diff.clean(), diff


async def test_sweep_failing_halfway_removes_nothing(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    issues = [gh.add_issue(repo, f"I{n:03}") for n in range(150)]
    await world.sync()
    gh.delete_issue(issues[0])
    # The issue-number sweep needs two GraphQL pages; the second one keeps failing.
    # GraphQL requests this sync: the issue total, sweep page 1, sweep page 2 (fails, as
    # does every retry and everything after it).
    fail_from_nth(world, "api.github.com/graphql", 3, 502, method="POST")
    await world.sync(expect_error=True)
    assert len(await world.names("TICKET")) == 150
    gh.faults.clear()
    await world.sync()
    assert len(await world.names("TICKET")) == 149


async def test_issue_missing_from_graphql_but_present_over_rest_is_kept(world, org) -> None:
    """If the two APIs disagree, the direct GET decides, and it says the issue exists."""
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "One")
    hidden = gh.add_issue(repo, "Two")
    await world.sync()
    from tests.integration.github_teams.sim import gql
    original = gql._repository

    def lagging(ws: object, viewer: object, owner: str, name: str) -> dict:
        out = original(ws, viewer, owner, name)
        inner = out["issues"]

        def issues(**kw: object) -> dict:
            page = inner(**kw)
            page["nodes"] = [n for n in page["nodes"] if n["number"] != hidden.number]
            page["totalCount"] -= 1
            return page
        out["issues"] = issues
        return out

    gql._repository = lagging
    try:
        await world.sync()
    finally:
        gql._repository = original
    assert await world.names("TICKET") == ["One", "Two"]


async def test_issue_moved_between_two_synced_repositories(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    other = gh.add_repo(org["org"], "gadgets")
    other.collaborators[org["olga"].id] = "read"
    issue = gh.add_issue(repo, "Wrong repo")
    world.configure([repo, other])  # a legacy multi-repository selection
    await world.sync()
    gh.transfer_issue(issue, other)
    await world.sync()
    tickets = await world.by_type("TICKET")
    assert sorted(tickets) == [f"{other.id}/issues/1"]
    diff = await world.diff()
    assert diff.clean(), diff


async def test_sub_issue_survives_its_parent_being_deleted(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    parent = gh.add_issue(repo, "Epic")
    child = gh.add_issue(repo, "Task")
    child.parent = parent
    await world.sync()
    gh.delete_issue(parent)
    gh.edit_issue(child, title="Task (orphaned)")
    await world.sync()
    assert await world.names("TICKET") == ["Task (orphaned)"]


async def test_attachment_shared_by_two_issues_survives_one_being_deleted(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    url = gh.upload(repo, "plan.pdf", b"%PDF plan")
    first = gh.add_issue(repo, "First", body=f"[plan.pdf]({url})")
    gh.add_issue(repo, "Second", body=f"Same file: [plan.pdf]({url})")
    await world.sync()
    gh.delete_issue(first)
    await world.sync()
    files = [d for k, d in (await world.by_type("FILE")).items() if k.startswith("https://")]
    assert [d["recordName"] for d in files] == ["plan.pdf"]
    diff = await world.diff()
    assert diff.clean(), diff


async def test_deleted_repository_restored_on_github_comes_back_whole(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    gh.add_pull(repo, "PR")
    await world.sync()
    repo.deleted = True
    # Other tests cover what this sync does with a deleted repository; only what happens
    # after the restore is under test.
    with contextlib.suppress(Exception):
        await world.sync()
    repo.deleted = False  # GitHub lets an owner restore a deleted repository
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_repository_transferred_to_another_org_is_kept(world, org) -> None:
    """The filter still names the old full name; GitHub redirects it. Nothing is lost."""
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    await world.sync()
    before = sorted((await world.live_docs()).keys())
    new_org = gh.add_org("acme-labs", owners=[org["admin"]], members=[org["alice"]], base_permission=None)
    gh.transfer_repo(repo, new_org)
    await world.sync()
    assert sorted((await world.live_docs()).keys()) == before


async def test_issue_listing_answering_empty_removes_nothing(world, org) -> None:
    """A 200 with no items (not an error) must not read as 'every issue is gone':
    removal is decided by the issue numbers GitHub lists, then confirmed one by one."""
    gh, repo = world.gh, org["repo"]
    for n in range(3):
        gh.add_issue(repo, f"I{n}")
    await world.sync()
    gh.issue_numbers_lag = True
    await _forget_issue_total(world, repo)  # so the number sweep runs
    await world.sync()
    assert len(await world.names("TICKET")) == 3


async def test_issue_count_that_could_not_be_read_holds_the_checkpoint_back(world, org) -> None:
    """The removal check compares GitHub's issue count with the last one plus the issues
    created since the checkpoint. If the count cannot be read, the checkpoint must not
    move: an issue created then and one deleted later would cancel out, and the deleted
    issue would stay in PipesHub."""
    gh, repo = world.gh, org["repo"]
    old = [gh.add_issue(repo, f"I{n}") for n in range(3)]
    await world.sync()
    gh.add_issue(repo, "New")
    graphql_down = Fault(on("api.github.com/graphql", method="POST"), kind="status", status=502, times=None)
    gh.faults.append(graphql_down)
    await world.sync(expect_error=True)
    gh.faults.remove(graphql_down)
    gh.delete_issue(old[0])
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_window_that_moved_past_the_checkpoint_still_checks_removals(world, org) -> None:
    """A relative window ("last 30 days") moves forward on its own, with no full sync.
    Once it passes the checkpoint, issues created in between are neither listed nor
    counted, so the issue-count shortcut cannot be trusted: one such issue and one
    deletion would cancel out, and the deleted issue would stay in PipesHub."""
    gh, repo = world.gh, org["repo"]
    old = [gh.add_issue(repo, f"I{n}") for n in range(3)]
    await world.sync()
    gh.tick(60)
    gh.add_issue(repo, "Quiet")  # created after the checkpoint, then never touched
    gh.tick(86_400)
    # What a relative window does by itself: its start passes the checkpoint.
    world.config["filters"]["sync"]["values"]["modified"] = {
        "operator": "is_after", "type": "datetime", "value": {"start": gh.clock.now() * 1000, "end": None}}
    gh.delete_issue(old[0])
    await world.sync()
    assert "I0" not in await world.names("TICKET")


async def test_repository_shifted_out_of_an_org_listing_is_not_removed(world, org) -> None:
    """A legacy configuration selects the whole organization. Its repository listing is
    paged by offset: a repository deleted while the listing is read shifts a live one
    out of view. Only GitHub's direct answer may remove a repository."""
    gh = world.gh
    from tests.integration.github_teams.helpers import hook_nth
    repos = [gh.add_repo(org["org"], f"svc-{n:03}", files={"README.md": "x"}) for n in range(101)]
    # Listing order is creation order: widgets, svc-000 ... svc-100. Page 2 starts at
    # offset 100, so once widgets is gone svc-099 slides onto page 1 and is never read.
    gh.add_issue(repos[99], "Issue in the last repository")
    world.configure_orgs(["acme"])
    await world.sync(index=False)
    assert "Issue in the last repository" in await world.names("TICKET")
    first = next(r for r in gh.repos.values() if r.name == "widgets")
    # Between page 1 and page 2 of the org's repository listing, the first one is deleted.
    hook_nth(world, "api.github.com/orgs/acme/repos", 2, lambda: setattr(first, "deleted", True))
    world.scope.discard(first.id)
    # The listing still returned the deleted repository, so syncing it fails: reported.
    await world.sync(index=False, expect_error=True)
    assert "Issue in the last repository" in await world.names("TICKET")
    await world.sync(index=False)  # next sync: everything settles
    assert "Issue in the last repository" in await world.names("TICKET")
    diff = await world.diff(check_missing=False)
    assert not diff.stale, diff
