"""The Branch sync filter: syncing a branch other than the default one, switching
between branches, a branch that does not exist, and the branch picker. Runs with the
trash off and on.

Every test runs on Neo4j and ArangoDB (NEO4J_IT_URI / ARANGO_IT_URL) against the simulated
GitHub in ``sim/``; see README.md.
"""

import pytest

from tests.integration.github_teams.sim.model import Commit, Repo, World

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
BOTH_TRASH_MODES = True


def _branch_from_default(gh: World, repo: Repo, branch: str, changes: dict[str, str | None]) -> Commit:
    """A branch cut from the default branch, then committed to."""
    repo.branches[branch] = repo.branches[repo.default_branch]
    return gh.commit(repo, changes, f"work on {branch}", branch=branch)


async def test_chosen_branch_is_what_gets_indexed(world, org) -> None:
    """Records, streamed content and links all come from the chosen branch, not from
    the default one."""
    gh, repo = world.gh, org["repo"]
    head = _branch_from_default(gh, repo, "release", {
        "src/app.py": "print('release')\n", "src/release_only.py": "x = 1\n", "README.md": None})
    gh.commit(repo, {"src/main_only.py": "y = 2\n"})
    world.set_branch("release")
    await world.sync()

    diff = await world.diff()
    assert diff.clean(), diff
    files = await world.by_type("CODE_FILE")
    assert set(files) == {f"/{repo.id}/blob/src/app.py", f"/{repo.id}/blob/src/release_only.py"}
    assert await world.stream(f"/{repo.id}/blob/src/app.py") == b"print('release')\n"
    app = files[f"/{repo.id}/blob/src/app.py"]
    assert app["webUrl"] == f"https://github.com/{repo.full_name}/blob/{head.sha}/src/app.py"
    folder = (await world.by_type("FILE"))[f"/{repo.id}/tree/src"]
    assert folder["webUrl"] == f"https://github.com/{repo.full_name}/tree/release/src"


async def test_switching_branch_and_back_updates_records_in_place(world, org) -> None:
    """Record ids do not name the branch, so a file on both branches keeps its record
    across a switch; a file only on the branch left behind is removed."""
    gh, repo = world.gh, org["repo"]
    _branch_from_default(gh, repo, "release", {"src/app.py": "print('release')\n", "README.md": None})
    await world.sync()
    app_id = (await world.record(f"/{repo.id}/blob/src/app.py")).id

    world.set_branch("release")
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    assert f"/{repo.id}/blob/README.md" not in await world.live_docs()
    assert (await world.record(f"/{repo.id}/blob/src/app.py")).id == app_id
    assert await world.stream(f"/{repo.id}/blob/src/app.py") == b"print('release')\n"

    world.set_branch(None)
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    assert (await world.record(f"/{repo.id}/blob/src/app.py")).id == app_id
    assert await world.stream(f"/{repo.id}/blob/src/app.py") == b"print('hi')\n"


async def test_branch_that_does_not_exist_fails_the_sync_and_keeps_the_records(world, org) -> None:
    """A deleted or mistyped branch must not fall back to the default branch (its files
    would replace the chosen branch's without the admin asking) nor empty the index."""
    gh, repo = world.gh, org["repo"]
    _branch_from_default(gh, repo, "release", {"src/release_only.py": "x = 1\n"})
    world.set_branch("release")
    await world.sync()
    before = set(await world.by_type("CODE_FILE"))

    del repo.branches["release"]
    gh.commit(repo, {"src/main_only.py": "y = 2\n"})
    exc = await world.sync(expect_error=True)

    assert "GitHub Teams sync did not complete" in str(exc)
    assert "does not exist" in world.logs.text()
    assert set(await world.by_type("CODE_FILE")) == before


async def test_incremental_sync_follows_the_chosen_branch_only(world, org) -> None:
    """After the first sync, a commit on the chosen branch is picked up and one on the
    default branch is not."""
    gh, repo = world.gh, org["repo"]
    _branch_from_default(gh, repo, "release", {"src/release_only.py": "x = 1\n"})
    world.set_branch("release")
    await world.sync()

    gh.commit(repo, {"src/later.py": "z = 3\n", "src/app.py": None}, branch="release")
    gh.commit(repo, {"src/main_only.py": "y = 2\n"})
    await world.sync()

    diff = await world.diff()
    assert diff.clean(), diff
    assert set(await world.by_type("CODE_FILE")) == {
        f"/{repo.id}/blob/README.md", f"/{repo.id}/blob/src/release_only.py", f"/{repo.id}/blob/src/later.py"}


async def test_file_dates_come_from_the_chosen_branch_history(world, org) -> None:
    """A file changed on the default branch after the branch was cut keeps the
    branch's dates: the default branch's newer commit is not part of what is indexed."""
    gh, repo = world.gh, org["repo"]
    created = repo.head()
    branched = _branch_from_default(gh, repo, "release", {"src/release_only.py": "x = 1\n"})
    gh.commit(repo, {"src/app.py": "print('main moved on')\n"})
    world.set_branch("release")
    await world.sync()

    app = (await world.by_type("CODE_FILE"))[f"/{repo.id}/blob/src/app.py"]
    assert app["sourceLastModifiedTimestamp"] == created.date * 1000
    release_only = (await world.by_type("CODE_FILE"))[f"/{repo.id}/blob/src/release_only.py"]
    assert release_only["sourceCreatedAtTimestamp"] == branched.date * 1000


async def test_branch_picker_lists_the_repository_branches_default_first(world, org) -> None:
    """The picker follows the repository chosen in the form, puts the default branch
    first, searches anywhere in a name ignoring case (as GitHub does), and pages."""
    repo = org["repo"]
    for name in ("alpha", "feature/login", "feature/logout", "release/2.x"):
        repo.branches[name] = repo.branches[repo.default_branch]
    await world.start()
    connector = world.connector

    response = await connector.get_filter_options("branch", page=1, limit=20)
    assert response.success and response.options == []
    assert "Select a repository" in (response.message or "")

    connector._request_filter_context_group_paths = [repo.full_name]
    response = await connector.get_filter_options("branch", page=1, limit=20)
    assert [o.id for o in response.options] == ["main", "alpha", "feature/login", "feature/logout", "release/2.x"]
    assert response.options[0].label == "main (default)"

    response = await connector.get_filter_options("branch", page=1, limit=20, search="LOG")
    assert [o.id for o in response.options] == ["feature/login", "feature/logout"]

    seen: list[str] = []
    cursor, page = None, 1
    while True:
        response = await connector.get_filter_options("branch", page=page, limit=2, cursor=cursor)
        seen += [o.id for o in response.options]
        if not response.has_more:
            break
        cursor, page = response.cursor, page + 1
    assert seen[0] == "main"
    assert sorted(seen) == sorted(["main", "alpha", "feature/login", "feature/logout", "release/2.x"])
