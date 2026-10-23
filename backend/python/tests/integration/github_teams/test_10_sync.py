"""First, incremental and full sync; listings that change while they are read.

Every test runs on Neo4j and ArangoDB (NEO4J_IT_URI / ARANGO_IT_URL) against the simulated
GitHub in ``sim/``; see README.md.
"""

import pytest

from tests.integration.github_teams.helpers import (
    fail_from_nth,
    hook_nth,
    issues_listing,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


async def test_incremental_sync_picks_up_new_and_changed_items(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    first = gh.add_issue(repo, "First")
    pr = gh.add_pull(repo, "Feature", changes={"src/f.py": "f"})
    await world.sync()
    gh.edit_issue(first, title="First (renamed)", state="closed", state_reason="completed")
    gh.add_issue(repo, "Second")
    gh.edit_pull(pr, title="Feature v2")
    gh.commit(repo, {"src/app.py": "print('v2')\n", "docs/new.md": "new"})
    await world.sync()
    assert await world.names("TICKET") == ["First (renamed)", "Second"]
    assert await world.names("PULL_REQUEST") == ["Feature v2"]
    diff = await world.diff()
    assert diff.clean(), diff


async def test_issue_updated_while_the_listing_is_read_is_not_skipped(world, org) -> None:
    """GitHub pages the issues listing by offset (``page=N``). Read ``updated asc``, an
    issue edited between two page requests moves to the end, every later issue shifts
    back one place, and the first issue of the next page is never read. Read ``updated
    desc`` (as now), an edit only repeats an issue; this guards either order. (Deletion
    between requests: test_90_hardening.)"""
    gh, repo = world.gh, org["repo"]
    issues = [gh.add_issue(repo, f"Issue {n:03}") for n in range(150)]
    await world.sync()
    for issue in issues:  # 150 changes since the checkpoint, at distinct seconds
        gh.edit_issue(issue, title=issue.title + " v2")
    # Between the first and second listing request, someone edits an issue the first returned.
    hook_nth(world, issues_listing(repo), 2, lambda: gh.edit_issue(issues[0], title="Issue 000 v3"))
    await world.sync()
    await world.sync()
    await world.sync()
    expected = sorted(["Issue 000 v3"] + [i.title for i in issues[1:]])
    assert await world.names("TICKET") == expected


async def test_pull_request_updated_in_the_same_second_as_the_watermark(world, org) -> None:
    """GitHub timestamps have one-second resolution. A PR changed in the same second
    as the newest PR the last sync saw, but after that sync read the listing, carries
    the same ``updated_at`` as the checkpoint."""
    gh, repo = world.gh, org["repo"]
    a = gh.add_pull(repo, "A")
    b = gh.add_pull(repo, "B")
    await world.sync()
    gh.edit_pull(a, title="A v2")
    await world.sync()  # watermark = A's updated_at
    b.title, b.updated_at = "B v2", a.updated_at  # changed later in that same second
    await world.sync()
    await world.sync()
    assert await world.names("PULL_REQUEST") == ["A v2", "B v2"]


async def test_issue_updated_in_the_same_second_as_the_watermark(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    a = gh.add_issue(repo, "A")
    b = gh.add_issue(repo, "B")
    await world.sync()
    gh.edit_issue(a, title="A v2")
    await world.sync()
    b.title, b.updated_at = "B v2", a.updated_at
    await world.sync()
    assert await world.names("TICKET") == ["A v2", "B v2"]


async def test_sync_interrupted_mid_listing_resumes_without_loss(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    for n in range(230):
        gh.add_issue(repo, f"I{n:03}")
    fail_from_nth(world, issues_listing(repo), 3, 500)  # the third listing request onwards fails
    await world.sync(expect_error=True)
    world.gh.faults.clear()
    await world.sync()
    assert len(await world.names("TICKET")) == 230
    diff = await world.diff()
    assert diff.clean(), diff


async def test_full_sync_rebuilds_the_same_state(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "One")
    gh.add_pull(repo, "Two")
    await world.sync()
    before = sorted((await world.live_docs()).keys())
    await world.sync(full=True)
    assert sorted((await world.live_docs()).keys()) == before
    diff = await world.diff()
    assert diff.clean(), diff


async def test_restart_between_syncs_keeps_checkpoints(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "One")
    await world.sync()
    await world.restart()
    start = len(gh.calls)
    await world.sync()
    issue_calls = [c for c in world.api_calls(since=start) if c.path.endswith("/issues")]
    assert issue_calls and all("since" in c.query for c in issue_calls)
