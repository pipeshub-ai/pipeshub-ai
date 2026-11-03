"""First, incremental and full sync; checkpoints; pagination; items that change while a
listing is being read; resuming after an interruption."""

import contextlib

from tests.integration.gitlab_simulated.helpers import ends, fail, hook, pid


async def test_incremental_picks_up_new_edited_and_commented_issues(world, org) -> None:
    gl, p = world.gl, org["project"]
    a = gl.add_issue(p, "Alpha", author=org["dev"], description="first body")
    await world.sync()
    b = gl.add_issue(p, "Beta", author=org["dev"])
    a.title = "Alpha renamed"
    gl.touch(a)
    gl.add_note(a, "a brand new comment", author=org["rita"])
    await world.sync()
    assert await world.names("TICKET") == ["Alpha renamed", "Beta"]
    assert "a brand new comment" in await world.stream_text(str(a.id))
    diff = await world.diff()
    assert diff.clean(), diff
    assert b


async def test_unchanged_sync_writes_nothing(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Alpha", author=org["dev"])
    gl.add_mr(p, "MR", author=org["dev"])
    await world.sync()
    world.producer.events.clear()
    await world.sync(index=False)
    updates = [m for m in world.producer.events if m[1].get("eventType") in ("newRecord", "updateRecord")]
    assert not updates, updates


async def test_more_than_one_page_of_everything(world, org) -> None:
    """Issues, notes, merge requests, members and files each over GitLab's 100 per page."""
    gl, p = world.gl, org["project"]
    issues = [gl.add_issue(p, f"Issue {n:03}", author=org["dev"]) for n in range(130)]
    for n in range(120):
        gl.add_note(issues[0], f"comment number {n}", author=org["rita"])
    for n in range(105):
        gl.add_mr(p, f"MR {n:03}", author=org["dev"])
    for n in range(110):
        u = gl.add_user(f"member{n}", world.email(f"member{n}"))
        p.members[u.id] = type(p.members[org["dev"].id])(10)
    gl.commit(p, {f"pkg/m{n:03}.py": f"x = {n}\n" for n in range(240)})
    await world.sync()
    diff = await world.diff(emails=[world.creator_email, org["gus"].email])
    assert diff.clean(), diff
    text = await world.stream_text(str(issues[0].id))
    assert "comment number 0" in text and "comment number 119" in text


async def test_issue_updated_while_the_listing_is_read_is_not_lost(world, org) -> None:
    """An issue edited between two page requests moves to the end of an ``updated asc``
    listing: with offset pages, the issue that slides into the place already read is
    skipped, and the checkpoint moves past it."""
    gl, p = world.gl, org["project"]
    issues = [gl.add_issue(p, f"Issue {n:03}", author=org["dev"]) for n in range(150)]
    await world.sync()
    for i in issues:  # everything changes, so the next sync lists all 150 again
        i.title += " v2"
        gl.touch(i)
    first = issues[0]

    def edit_first() -> None:
        first.title = "Issue 000 v3"
        gl.touch(first)

    hook(world, ends(f"{pid(p)}/issues"), edit_first, skip=1)  # on the second page request
    await world.sync()
    await world.sync()
    names = await world.names("TICKET")
    assert len(names) == 150
    stale = [n for n in names if not (n.endswith(("v2", "v3")))]
    assert not stale, f"{len(stale)} issue(s) kept their old title for good: {stale[:5]}"


async def test_issue_deleted_while_the_listing_is_read_does_not_hide_another(world, org) -> None:
    gl, p = world.gl, org["project"]
    issues = [gl.add_issue(p, f"Issue {n:03}", author=org["dev"]) for n in range(150)]
    await world.sync()
    for i in issues:
        # Seconds apart, as real edits are: the stored checkpoint keeps whole seconds, and
        # edits inside one second would be read again anyway, hiding the skip.
        gl.clock.advance(2)
        i.title += " v2"
        gl.touch(i)

    def delete_one() -> None:
        issues[3].deleted = True

    hook(world, ends(f"{pid(p)}/issues"), delete_one, skip=1)
    # The deleted issue's comments answer 404 mid-sync: that sync may say it is incomplete.
    with contextlib.suppress(Exception):
        await world.sync()
    await world.sync()
    names = await world.names("TICKET")
    old = [n for n in names if not n.endswith("v2") and n != "Issue 003"]
    assert not old, f"{len(old)} issue(s) never got their update: {old[:5]}"


async def test_merge_request_updated_while_the_listing_is_read_is_not_lost(world, org) -> None:
    gl, p = world.gl, org["project"]
    mrs = [gl.add_mr(p, f"MR {n:03}", author=org["dev"]) for n in range(150)]
    await world.sync()
    for m in mrs:
        m.title += " v2"
        gl.touch(m)

    def edit_first() -> None:
        mrs[0].title = "MR 000 v3"
        gl.touch(mrs[0])

    hook(world, ends(f"{pid(p)}/merge_requests"), edit_first, skip=1)
    await world.sync()
    await world.sync()
    names = await world.names("PULL_REQUEST")
    old = [n for n in names if not (n.endswith(("v2", "v3")))]
    assert not old, f"{len(old)} MR(s) kept their old title for good: {old[:5]}"


async def test_two_issues_with_the_same_update_time_across_syncs(world, org) -> None:
    """The checkpoint is an update time; GitLab's ``updated_after`` is inclusive, so an
    issue stamped with exactly that time later is still read."""
    gl, p = world.gl, org["project"]
    a = gl.add_issue(p, "A", author=org["dev"])
    await world.sync()
    b = gl.add_issue(p, "B", author=org["dev"])
    b.updated_at = a.updated_at
    b.created_at = a.created_at
    await world.sync()
    assert await world.names("TICKET") == ["A", "B"]


async def test_store_failure_is_retried_on_the_next_sync(world, org) -> None:
    gl, p = world.gl, org["project"]
    for n in range(12):
        gl.add_issue(p, f"Issue {n:02}", author=org["dev"])
    original = world.processor.on_new_records
    calls = {"n": 0}

    async def flaky(records, *a, **kw) -> object:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("graph store unavailable")
        return await original(records, *a, **kw)

    world.processor.on_new_records = flaky
    with contextlib.suppress(Exception):
        await world.sync()
    world.processor.on_new_records = original
    await world.sync()
    assert len(await world.names("TICKET")) == 12
    diff = await world.diff()
    assert diff.clean(), diff


async def test_interrupted_sync_resumes(world, org) -> None:
    """The sync process dies part-way (the issue listing never finishes); the next sync
    on a new connector instance picks everything up."""
    gl, p = world.gl, org["project"]
    for n in range(40):
        gl.add_issue(p, f"Issue {n:02}", author=org["dev"])
    fail(world, ends(f"{pid(p)}/issues"), status="drop", times=20)
    with contextlib.suppress(Exception):
        await world.sync()
    await world.restart()
    await world.sync()
    assert len(await world.names("TICKET")) == 40


async def test_modified_date_filter_and_changing_it_later(world, org) -> None:
    gl, p = world.gl, org["project"]
    old = gl.add_issue(p, "Old", author=org["dev"])
    gl.clock.advance(86400 * 30)
    cutoff = gl.clock.t
    gl.clock.advance(60)
    new = gl.add_issue(p, "New", author=org["dev"])
    world.set_sync_filter("modified", {"start": int(cutoff * 1000), "end": None}, operator="is_after", type_="datetime")
    await world.sync()
    assert await world.names("TICKET") == ["New"]
    # The admin widens the window: the next sync is a full one and finds the old issue.
    del world.config["filters"]["sync"]["values"]["modified"]
    world.pending_full_sync = True
    await world.sync()
    assert await world.names("TICKET") == ["New", "Old"]
    assert old and new


async def test_code_changes_add_modify_delete_rename(world, org) -> None:
    gl, p = world.gl, org["project"]
    await world.sync()
    gl.commit(p, {"src/new.py": "n = 1\n", "src/app.py": "print('changed')\n", "README.md": None,
                  "src/util/helpers.py": None, "lib/helpers.py": "def h():\n    return 1\n"})
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    body = await world.stream(f"/{p.path_with_namespace}/-/blob/HEAD/src/app.py")
    assert b"changed" in body


async def test_default_branch_switch(world, org) -> None:
    gl, p = world.gl, org["project"]
    await world.sync()
    gl.commit(p, {"only_on_develop.py": "d = 1\n", "README.md": None}, branch="develop")
    p.default_branch = "develop"
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_compare_cut_short_by_gitlab_limits_misses_no_change(world, org) -> None:
    """GitLab's compare stops listing diffs at the instance's ``diff_max_files`` (an
    admin setting, 500 here) and says so with ``compare_timeout: true``. The files past
    the cut must still sync."""
    gl, p = world.gl, org["project"]
    gl.behaviour.compare_max_files = 500
    gl.commit(p, {f"big/f{n:03}.py": "v1\n" for n in range(600)})
    await world.sync()
    gl.commit(p, {f"big/f{n:03}.py": None for n in range(600)} | {"big/keep.py": "k\n"})
    await world.sync()
    diff = await world.diff()
    assert not diff.stale, f"{len(diff.stale)} deleted file(s) still in PipesHub, e.g. {diff.stale[:3]}"


async def test_large_push_that_falls_back_to_a_full_walk_still_removes_deleted_files(world, org) -> None:
    """Over 1000 changed files the connector walks the whole tree instead of the compare.
    A walk only adds: files deleted by that push must still leave PipesHub."""
    gl, p = world.gl, org["project"]
    gl.commit(p, {f"bulk/v{n:04}.py": "v\n" for n in range(1100)})
    await world.sync()
    gl.commit(p, {f"bulk/v{n:04}.py": None for n in range(1100)})
    await world.sync()
    diff = await world.diff()
    assert not diff.stale, f"{len(diff.stale)} deleted file(s) still in PipesHub, e.g. {diff.stale[:3]}"


async def test_history_rewritten_and_old_commit_gone(world, org) -> None:
    """A force push rewrites history and GitLab garbage-collects the old head: the
    compare from the stored commit answers 404. Files the new history dropped must go."""
    gl, p = world.gl, org["project"]
    await world.sync()
    old_head = p.branches["main"]
    gl.commit(p, {"fresh.py": "f\n"}, replace=True, message="rewrite")
    p.commits.pop(old_head)
    await world.sync()
    diff = await world.diff()
    assert not diff.stale, diff
