"""Everything that leaves GitLab or the sync's scope must leave PipesHub, and nothing
else may be removed. Run with GL_AUDIT_SOFT_DELETE=1 for the trash."""

import contextlib

import pytest

from tests.integration.gitlab_simulated.helpers import ends, fail, pid


async def test_deleted_issue_is_removed(world, org) -> None:
    gl, p = world.gl, org["project"]
    keep = gl.add_issue(p, "Keep", author=org["dev"])
    gone = gl.add_issue(p, "Delete me", author=org["dev"])
    await world.sync()
    gone.deleted = True
    await world.sync()
    assert await world.names("TICKET") == [keep.title]
    diff = await world.diff()
    assert diff.clean(), diff


async def test_deleted_merge_request_is_removed(world, org) -> None:
    gl, p = world.gl, org["project"]
    keep = gl.add_mr(p, "Keep", author=org["dev"])
    gone = gl.add_mr(p, "Delete me", author=org["dev"])
    await world.sync()
    gone.deleted = True
    await world.sync()
    assert await world.names("PULL_REQUEST") == [keep.title]
    diff = await world.diff()
    assert diff.clean(), diff


async def test_issue_moved_to_another_project_keeps_the_closed_original(world, org) -> None:
    """GitLab keeps the original, closed, with ``moved_to_id``; it is still readable there."""
    gl, p = world.gl, org["project"]
    other = gl.add_project(org["group"], "elsewhere")
    issue = gl.add_issue(p, "Moves", author=org["dev"])
    await world.sync()
    copy = gl.add_issue(other, "Moves", author=org["dev"])
    issue.state, issue.moved_to_id = "closed", copy.id
    gl.touch(issue)
    await world.sync()
    assert await world.names("TICKET") == ["Moves"]
    diff = await world.diff()
    assert diff.clean(), diff


async def test_deleted_project_is_removed(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    gl.add_mr(p, "MR", author=org["dev"])
    await world.sync()
    p.deleted = True
    await world.sync()
    diff = await world.diff(check_missing=False)
    assert not diff.stale, diff


async def test_lost_access_to_the_project_removes_it(world, org) -> None:
    """The sync account is removed from the group: GitLab answers 404 for the project,
    so nobody may keep reading it through PipesHub."""
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    await world.sync()
    del org["group"].members[org["admin"].id]
    await world.sync()
    visible = set()
    for email in world.users:
        visible |= await world.visible_to(email)
    assert not visible, f"still readable after access was lost: {len(visible)} record(s)"


async def test_selecting_a_different_project_removes_the_old_one(world, org) -> None:
    gl, p = world.gl, org["project"]
    other = gl.add_project(org["group"], "gadgets", members={org["dev"]: 30}, files={"g.py": "g\n"})
    gl.add_issue(p, "Old project issue", author=org["dev"])
    await world.sync()
    world.select(other)
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_deleted_file_and_emptied_folder_are_removed(world, org) -> None:
    gl, p = world.gl, org["project"]
    await world.sync()
    gl.commit(p, {"src/util/helpers.py": None})
    await world.sync()
    docs = await world.live_docs()
    assert f"/{p.path_with_namespace}/-/blob/HEAD/src/util/helpers.py" not in docs
    diff = await world.diff()
    assert diff.clean(), diff


@pytest.mark.xfail(strict=True, reason="not fixed (low): the upload stays downloadable on GitLab "
                   "for the project's readers, and removing it safely needs to know whether another issue links it")
async def test_attachment_removed_from_the_issue_is_removed(world, org) -> None:
    gl, p = world.gl, org["project"]
    href = gl.add_upload(p, "spec.pdf", b"%PDF-1.4 secret plan")
    issue = gl.add_issue(p, "With file", author=org["dev"], description=f"see [spec]({href})")
    await world.sync()
    assert any("/uploads/" in k for k in await world.by_type("FILE"))
    issue.description = "the file is gone"
    gl.touch(issue)
    await world.sync()
    diff = await world.diff()
    assert not diff.stale, diff


async def test_attachments_of_a_deleted_issue_are_removed(world, org) -> None:
    gl, p = world.gl, org["project"]
    href = gl.add_upload(p, "notes.txt", b"private notes")
    gone = gl.add_issue(p, "Has file", author=org["dev"], description=f"[n]({href})")
    await world.sync()
    gone.deleted = True
    await world.sync()
    diff = await world.diff()
    assert not diff.stale, diff


async def test_renamed_project_keeps_one_copy_of_each_file(world, org) -> None:
    """Code records are keyed by the project's path. After a rename the next sync must
    neither duplicate the files nor keep deleted ones under the old path."""
    gl, p = world.gl, org["project"]
    await world.sync()
    old = p.path_with_namespace
    p.old_paths.append(old)
    p.path = "widgets-renamed"
    gl.commit(p, {"src/app.py": "print('after rename')\n", "README.md": None})
    await world.sync()
    diff = await world.diff()
    assert not diff.stale, diff
    files = await world.by_type("CODE_FILE")
    by_name: dict[str, int] = {}
    for d in files.values():
        by_name[d["recordName"]] = by_name.get(d["recordName"], 0) + 1
    assert all(n == 1 for n in by_name.values()), by_name


async def test_project_renamed_under_a_dotted_namespace_keeps_syncing(world, org) -> None:
    """gitlab.com answers the old path of a project renamed under a namespace with a dot
    with a redirect that ends in 404 (seen live). The project still exists under its id:
    it must keep syncing, and a member removed meanwhile must lose access."""
    gl, p = world.gl, org["project"]
    org["group"].path = "acme.labs"
    world.configure(p)
    gl.add_issue(p, "Kept issue", author=org["dev"])
    await world.sync()
    p.old_paths.append(p.path_with_namespace)
    p.path = "widgets-renamed"
    del p.members[org["rita"].id]
    gl.commit(p, {"src/app.py": None})
    await world.sync()
    assert await world.names("TICKET") == ["Kept issue"], "the renamed project was removed from PipesHub"
    diff = await world.diff()
    assert diff.clean(), diff


async def test_project_transferred_to_another_group(world, org) -> None:
    """Transferred to a group the old group's members are not in: they lose access."""
    gl, p = world.gl, org["project"]
    secure = gl.add_group("secure", members={org["admin"]: 50})
    gl.add_issue(p, "Issue", author=org["dev"])
    await world.sync()
    p.old_paths.append(p.path_with_namespace)
    p.namespace = secure
    await world.sync()
    diff = await world.diff()
    assert not diff.over and not diff.stale, diff


async def test_exclude_path_added_later_removes_those_files(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.commit(p, {"docs/guide.md": "# guide\n"})
    await world.sync()
    world.set_sync_filter("exclude_paths", ["docs/"])
    await world.sync()
    docs = await world.live_docs()
    reachable = set()
    for email in world.users:
        reachable |= await world.visible_to(email)
    assert f"/{p.path_with_namespace}/-/blob/HEAD/docs/guide.md" not in (set(docs) & reachable)


async def test_a_failed_issue_listing_removes_nothing(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Stays", author=org["dev"])
    gl.add_mr(p, "Stays too", author=org["dev"])
    await world.sync()
    fail(world, ends(f"{pid(p)}/issues"), status=502, times=50)
    fail(world, ends(f"{pid(p)}/merge_requests"), status=502, times=50)
    with contextlib.suppress(Exception):
        await world.sync()
    world.gl.faults.clear()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_a_full_sync_whose_issue_listing_fails_hides_nothing(world, org) -> None:
    """A full sync (after a filter change) unlinks whatever it did not see. A listing
    that failed did not see anything, and must not cost anyone access."""
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Stays", author=org["dev"])
    await world.sync()
    fail(world, ends(f"{pid(p)}/issues"), status=502, times=50)
    with contextlib.suppress(Exception):
        await world.sync(full=True)
    world.gl.faults.clear()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_a_full_sync_with_a_revoked_token_hides_nothing(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Stays", author=org["dev"])
    gl.add_mr(p, "Stays too", author=org["dev"])
    await world.sync()
    gl.sync_token.revoked = True
    with contextlib.suppress(Exception):
        await world.sync(full=True)
    gl.sync_token.revoked = False
    diff = await world.diff()
    assert diff.clean(), diff
