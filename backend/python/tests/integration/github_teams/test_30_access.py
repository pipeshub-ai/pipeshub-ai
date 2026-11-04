"""For every PipesHub user and record, PipesHub's answer must equal GitHub's.

Every test runs on Neo4j and ArangoDB (NEO4J_IT_URI / ARANGO_IT_URL) against the simulated
GitHub in ``sim/``; see README.md.
"""

import pytest

from tests.integration.github_teams.helpers import fail

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


async def test_standard_org_access_matches_github(world, org) -> None:
    """Owner, a member via a team (write), a member with no access (base permission
    none), an outside collaborator (read), and a PipesHub user with no GitHub account."""
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    gh.add_pull(repo, "PR")
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    assert await world.visible_to("bob@acme.com") == set()
    assert await world.visible_to("carol@acme.com") == set()


async def test_removed_collaborator_loses_access(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    await world.sync()
    del repo.collaborators[org["olga"].id]
    org["core"].members.discard(org["alice"].id)
    await world.sync()
    assert await world.visible_to("olga@contractor.io") == set()
    assert await world.visible_to("alice@acme.com") == set()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_child_team_and_base_permission(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    dave = gh.add_user("dave", "dave@acme.com")
    org["org"].members[dave.id] = "member"
    gh.add_team(org["org"], "core-mobile", [dave], parent=org["core"])  # inherits core's access
    await world.add_pipeshub_user("dave@acme.com")
    gh.add_issue(repo, "Issue")
    await world.sync()
    assert (await world.diff()).clean()
    org["org"].base_permission = "read"  # now bob reads every repository
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    assert await world.visible_to("bob@acme.com")


async def test_visibility_changes_follow_github(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    await world.sync()
    repo.visibility = "public"
    await world.sync()
    assert (await world.diff()).clean()
    assert await world.visible_to("carol@acme.com")
    repo.visibility = "private"
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    assert await world.visible_to("carol@acme.com") == set()


async def test_internal_repository_excludes_outside_collaborators_without_a_role(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    repo.visibility = "internal"
    del repo.collaborators[org["olga"].id]
    gh.add_issue(repo, "Issue")
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    assert await world.visible_to("bob@acme.com")  # every member reads internal repos
    assert await world.visible_to("olga@contractor.io") == set()


async def test_collaborator_listing_failure_keeps_previous_grants(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    await world.sync()
    fail(world, f"api.github.com/repos/{repo.full_name}/collaborators", 502)
    await world.sync(expect_error=True)
    diff = await world.diff()
    assert diff.clean(), diff


async def test_attachment_linked_from_two_repositories_follows_each_parent(world, org) -> None:
    """A legacy config syncs two repositories (written before the repository field
    became single-select). The same upload is linked from an issue in a private
    repository only the team can read and from an issue in one everyone in the org can
    read. A user of either repository must get exactly what GitHub shows them."""
    gh, private = world.gh, org["repo"]
    open_repo = gh.add_repo(org["org"], "handbook")
    org["org"].base_permission = None
    open_repo.collaborators[org["bob"].id] = "read"
    url = gh.upload(private, "roadmap.pdf", b"%PDF secret roadmap")
    gh.add_issue(private, "Roadmap", body=f"[roadmap.pdf]({url})")
    world.configure([private, open_repo])
    await world.sync()
    gh.add_issue(open_repo, "Links", body=f"Copied: [roadmap.pdf]({url})")
    await world.sync()
    gh.edit_issue(private.issues[1], title="Roadmap (edited)")
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
