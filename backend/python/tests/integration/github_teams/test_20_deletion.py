"""Everything that leaves GitHub or the sync's scope must leave PipesHub, and nothing
else may be removed. Runs with the trash (soft delete) off and on.

Every test runs on Neo4j and ArangoDB (NEO4J_IT_URI / ARANGO_IT_URL) against the simulated
GitHub in ``sim/``; see README.md.
"""

import pytest

from tests.integration.github_teams.helpers import fail, issues_listing

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
BOTH_TRASH_MODES = True


async def test_deleted_issue_is_removed(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    keep = gh.add_issue(repo, "Keep")
    gone = gh.add_issue(repo, "Delete me")
    await world.sync()
    gh.delete_issue(gone)
    await world.sync()
    assert await world.names("TICKET") == [keep.title]
    diff = await world.diff()
    assert diff.clean(), diff


async def test_issue_transferred_to_another_repository_leaves_the_old_one(world, org) -> None:
    """Transferred to a repository this connector does not sync (a private one only
    the security team can read): the old copy must not stay readable to the old
    repository's audience."""
    gh, repo = world.gh, org["repo"]
    secret = gh.add_repo(org["org"], "security-private")
    issue = gh.add_issue(repo, "CVE details")
    await world.sync()
    gh.transfer_issue(issue, secret)
    await world.sync()
    assert await world.names("TICKET") == []
    diff = await world.diff()
    assert diff.clean(), diff


async def test_repository_deleted_on_github_is_removed(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    gh.add_pull(repo, "PR")
    await world.sync()
    repo.deleted = True
    await world.sync()
    diff = await world.diff(check_missing=False)
    assert not diff.stale, diff
    assert not (await world.live_docs())


async def test_lost_access_to_the_repository_removes_it(world, org) -> None:
    """The sync account is removed from the organisation: GitHub now answers 404 for
    the repository, so nobody may keep reading it through PipesHub."""
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    await world.sync()
    del org["org"].members[org["admin"].id]
    await world.sync()
    assert not (await world.live_docs())


async def test_selecting_a_different_repository_removes_the_old_one(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    other = gh.add_repo(org["org"], "gadgets", files={"g.py": "g"})
    gh.add_issue(repo, "Old repo issue")
    gh.add_issue(other, "New repo issue")
    await world.sync()
    world.select([other])  # the next sync is a full one
    await world.sync()
    assert await world.names("TICKET") == ["New repo issue"]
    diff = await world.diff()
    assert diff.clean(), diff


async def test_renamed_repository_keeps_everything(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    await world.sync()
    before = sorted((await world.live_docs()).keys())
    gh.rename_repo(repo, "widgets-2")  # the filter still names acme/widgets; GitHub redirects
    await world.sync()
    assert sorted((await world.live_docs()).keys()) == before


async def test_failed_listing_removes_nothing(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    for n in range(5):
        gh.add_issue(repo, f"I{n}")
    await world.sync()
    before = sorted((await world.live_docs()).keys())
    fail(world, issues_listing(repo), 502)
    fail(world, "api.github.com/graphql", 502, method="POST")
    await world.sync(expect_error=True)
    assert sorted((await world.live_docs()).keys()) == before


async def test_repository_unreachable_for_a_moment_removes_nothing(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    await world.sync()
    before = sorted((await world.live_docs()).keys())
    fail(world, f"api.github.com/repos/{repo.full_name}", 503)
    fail(world, f"api.github.com/repositories/{repo.id}", 503)
    await world.sync(expect_error=True)
    assert sorted((await world.live_docs()).keys()) == before


async def test_attachment_removed_from_the_issue_is_removed(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    url = gh.upload(repo, "design.pdf", b"%PDF-1.4 design")
    issue = gh.add_issue(repo, "Spec", body=f"See [design.pdf]({url})")
    await world.sync()
    assert await world.names("FILE") == ["design.pdf", "src"]
    gh.edit_issue(issue, body="Superseded, no attachment any more")
    await world.sync()
    assert await world.names("FILE") == ["src"]


async def test_attachments_of_a_deleted_issue_are_removed(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    url = gh.upload(repo, "dump.log", b"secret log")
    issue = gh.add_issue(repo, "Crash", body=f"[dump.log]({url})")
    await world.sync()
    gh.delete_issue(issue)
    await world.sync()
    assert await world.names("FILE") == ["src"]


async def test_force_push_that_drops_most_files_removes_them(world, org) -> None:
    """A force push to a much smaller history (or a switch of the default branch to a
    small one) is only visible to a full tree walk. A complete, untruncated tree is
    GitHub's definite answer for what exists."""
    gh, repo = world.gh, org["repo"]
    gh.commit(repo, {f"lib/m{n}.py": str(n) for n in range(12)})
    await world.sync()
    gh.commit(repo, {"README.md": "# rewritten\n"}, replace=True, parent=None)  # force push
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_code_file_deleted_in_a_normal_commit_is_removed(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    await world.sync()
    gh.commit(repo, {"src/app.py": None})
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
