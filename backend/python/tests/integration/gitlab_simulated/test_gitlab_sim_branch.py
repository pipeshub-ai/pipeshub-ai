"""The branch filter: index a chosen branch instead of the default one.

Record ids keep GitLab's HEAD form whatever branch is synced, so choosing, switching or
clearing a branch updates the same records in place. The harness's truth follows the
chosen branch (``World.code_tree``), so ``diff()`` holds the index to that branch's files.
"""

import pytest

from app.connectors.sources.gitlab.repos import ReposSync

BRANCH = "feature/login"
DEFAULT_APP = b"print('hi')\n"
BRANCH_APP = b"print('login')\n"


def fork(gl, p) -> str:
    """``feature/login`` off main: one file changed, one added, one removed. Its head sha."""
    gl.add_branch(p, BRANCH)
    return gl.commit(p, {"src/app.py": BRANCH_APP, "src/login/form.py": "def form():\n    pass\n",
                         "src/util/helpers.py": None}, branch=BRANCH, message="login").sha


def blob(p, path: str) -> str:
    return f"/{p.path_with_namespace}/-/blob/HEAD/{path}"


def tree(p, path: str) -> str:
    return f"/{p.path_with_namespace}/-/tree/HEAD/{path}"


async def code_ids(world) -> dict[str, str]:
    """External id -> record id of every code file and folder someone in PipesHub can read."""
    reachable = set()
    for email in world.users:
        reachable |= await world.visible_to(email)
    return {k: d["id"] for k, d in (await world.live_docs()).items() if "/-/" in k and k in reachable}


async def _chosen_branch_is_what_gets_indexed(world, org) -> None:
    gl, p = world.gl, org["project"]
    head = fork(gl, p)
    gl.commit(p, {"main_only.py": "x = 1\n"})
    world.set_branch(BRANCH)
    await world.sync()
    # The personal connector shows its records to the person who connected it, no one else.
    diff = await world.diff(emails=[world.creator_email] if world.personal else None)
    assert diff.clean(), diff
    ids = await code_ids(world)
    assert blob(p, "main_only.py") not in ids and blob(p, "src/util/helpers.py") not in ids
    assert blob(p, "src/login/form.py") in ids
    assert all("/-/blob/HEAD/" in k or "/-/tree/HEAD/" in k for k in ids), sorted(ids)
    assert await world.stream(blob(p, "src/app.py")) == BRANCH_APP
    app = await world.typed(blob(p, "src/app.py"))
    assert app.commit_sha == head
    assert app.weburl == f"{gl.base_url}/{p.path_with_namespace}/-/blob/{head}/src/app.py"
    folder = await world.typed(tree(p, "src/login"))
    assert folder.weburl == f"{gl.base_url}/{p.path_with_namespace}/-/tree/{BRANCH}/src/login"


async def test_a_chosen_branch_indexes_that_branch_and_streams_its_content(world, org) -> None:
    """Files only on main stay out, files only on the branch come in, a file both have
    streams the branch's content (the record is pinned to the branch's head commit), and a
    folder links to the branch rather than to a commit."""
    await _chosen_branch_is_what_gets_indexed(world, org)


@pytest.mark.personal
async def test_personal_connector_indexes_the_chosen_branch(world, org) -> None:
    await _chosen_branch_is_what_gets_indexed(world, org)


async def test_switching_branch_syncs_only_what_differs(world, org, monkeypatch) -> None:
    """A switch, either way, is the straight compare between the two heads: no walk, and
    a file the same on both branches is not written again."""
    gl, p = world.gl, org["project"]
    fork(gl, p)
    await world.sync()
    readme = await world.typed(blob(p, "README.md"))
    walks: list[str] = []
    walk = ReposSync._sync_repo_full

    async def counted(self: ReposSync, *args: object) -> bool:
        walks.append(str(args[-1]))
        return await walk(self, *args)

    monkeypatch.setattr(ReposSync, "_sync_repo_full", counted)
    for branch, app in ((BRANCH, BRANCH_APP), (None, DEFAULT_APP)):
        world.set_branch(branch)
        await world.sync()
        diff = await world.diff()
        assert diff.clean(), diff
        assert await world.stream(blob(p, "src/app.py")) == app
    assert walks == []
    after = await world.typed(blob(p, "README.md"))
    assert (after.updated_at, after.version) == (readme.updated_at, readme.version)


async def test_switching_to_a_branch_and_back_updates_records_in_place(world, org) -> None:
    """A file on both branches keeps its record (same id) through each switch, so its
    permissions, citations and indexing state follow it; a file on only one branch leaves
    with that branch. Switching back must stream main's content again: a record still
    pinned to the branch's commit would keep serving the branch."""
    gl, p = world.gl, org["project"]
    fork(gl, p)
    await world.sync()
    before = await code_ids(world)
    assert blob(p, "src/util/helpers.py") in before

    world.set_branch(BRANCH)
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    on_branch = await code_ids(world)
    assert blob(p, "src/util/helpers.py") not in on_branch
    for path in ("README.md", "src/app.py"):
        assert on_branch[blob(p, path)] == before[blob(p, path)], path
    assert await world.stream(blob(p, "src/app.py")) == BRANCH_APP

    world.set_branch(None)
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    after = await code_ids(world)
    assert blob(p, "src/login/form.py") not in after and tree(p, "src/login") not in after
    assert blob(p, "src/util/helpers.py") in after
    for path in ("README.md", "src/app.py"):
        assert after[blob(p, path)] == before[blob(p, path)], path
    assert await world.stream(blob(p, "src/app.py")) == DEFAULT_APP


async def test_a_branch_that_does_not_exist_keeps_the_index(world, org) -> None:
    """A mistyped branch is a failed sync, not an empty branch: the full sync it starts
    must not unlink the files already indexed, nor fall back to main on its own."""
    p = org["project"]
    await world.sync()
    before = await code_ids(world)
    world.set_branch("no-such-branch")
    err = await world.sync(expect_error=True)
    assert "no-such-branch" in str(err) and "does not exist" in str(err), err
    assert await code_ids(world) == before
    assert await world.stream(blob(p, "src/app.py")) == DEFAULT_APP


async def test_a_synced_branch_deleted_on_gitlab_is_not_replaced_by_main(world, org) -> None:
    """The branch goes away after it was synced (merged and deleted, say). Its files stay
    indexed and the sync says why; main's files do not move in behind the admin's back."""
    gl, p = world.gl, org["project"]
    fork(gl, p)
    world.set_branch(BRANCH)
    await world.sync()
    before = await code_ids(world)
    del p.branches[BRANCH]
    err = await world.sync(expect_error=True)
    assert "does not exist" in str(err), err
    assert await code_ids(world) == before
    assert blob(p, "src/util/helpers.py") not in before
    assert await world.stream(blob(p, "src/app.py")) == BRANCH_APP


async def test_incremental_sync_follows_the_chosen_branch_only(world, org) -> None:
    """A push to the chosen branch is picked up through the compare; a push to main is
    not this connector's business while a branch is chosen."""
    gl, p = world.gl, org["project"]
    fork(gl, p)
    world.set_branch(BRANCH)
    await world.sync()
    form_id = (await code_ids(world))[blob(p, "src/login/form.py")]
    head = gl.commit(p, {"src/login/form.py": "def form():\n    return 'v2'\n", "src/login/api.py": "API = 1\n"},
                     branch=BRANCH, message="v2").sha
    gl.commit(p, {"main_only.py": "x = 1\n", "src/app.py": "print('main moved')\n"})
    since = len(gl.calls)
    await world.sync()
    calls = world.api_calls(since=since)
    assert any(c.resource == "/api/v4/projects/:id/repository/compare" for c in calls)
    assert not any(c.path == "/api/graphql" for c in calls), "the incremental sync walked the whole tree"
    diff = await world.diff()
    assert diff.clean(), diff
    ids = await code_ids(world)
    assert blob(p, "main_only.py") not in ids
    assert ids[blob(p, "src/login/form.py")] == form_id
    assert await world.stream(blob(p, "src/login/form.py")) == b"def form():\n    return 'v2'\n"
    assert await world.stream(blob(p, "src/app.py")) == BRANCH_APP
    assert (await world.typed(blob(p, "src/login/api.py"))).commit_sha == head
    assert (await world.checkpoint(f"{p.id}-code-repository")).get("branch") == BRANCH


async def test_branch_picker_lists_default_first_searches_and_pages(world, org) -> None:
    """The picker asks GitLab for one page of the repository in the form
    (``contextGroupPath``), leads page one with the default branch, and leaves it out of
    later pages, so paging through lists every branch exactly once."""
    gl, p = world.gl, org["project"]
    names = ["feature/login", "Feature/Search", "release-1.0", "dev", *(f"topic-{n:02}" for n in range(12))]
    for name in names:
        gl.add_branch(p, name)
    pwn = p.path_with_namespace

    res = await world.filter_options("branch")
    assert res.success and res.options == [], res

    seen: list[str] = []
    page = 1
    while True:
        res = await world.filter_options("branch", context=[pwn], page=page, limit=5)
        assert res.success, res
        if page == 1:
            assert (res.options[0].id, res.options[0].label) == ("main", "main (default)"), res.options
        else:
            assert "main" not in [o.id for o in res.options]
        seen += [o.id for o in res.options]
        if not res.has_more:
            break
        page += 1
        assert page < 10, "the picker never stopped paging"
    assert sorted(seen) == sorted(["main", *names]) and len(seen) == len(set(seen)), seen
    listings = [c for c in gl.calls if c.path.endswith("/repository/branches")]
    assert listings and all(c.query.get("per_page") == "5" for c in listings)

    res = await world.filter_options("branch", context=[pwn], search="FEAT")
    assert [o.id for o in res.options] == ["Feature/Search", "feature/login"], res.options
    res = await world.filter_options("branch", context=[pwn], search="mai")
    assert [(o.id, o.label) for o in res.options] == [("main", "main (default)")], res.options
    # GitLab reads ^, $ and * as a pattern: "^ma" matches main though the text is not in it.
    res = await world.filter_options("branch", context=[pwn], search="^ma")
    assert [(o.id, o.label) for o in res.options] == [("main", "main (default)")], res.options
    res = await world.filter_options("branch", context=[pwn], search="topic-0*")
    assert [o.id for o in res.options] == [f"topic-{n:02}" for n in range(10)], res.options

    p.default_branch = "dev"
    res = await world.filter_options("branch", context=[pwn], limit=5)
    assert (res.options[0].id, res.options[0].label) == ("dev", "dev (default)"), res.options
