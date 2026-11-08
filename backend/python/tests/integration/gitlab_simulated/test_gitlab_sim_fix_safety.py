"""Attacks on the fixes: removal must act only on a definite answer from GitLab, and data
written by the old code must end up consistent."""

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from tests.integration.gitlab_simulated.glsim.model import Membership
from tests.integration.gitlab_simulated.helpers import ends, fail, on, pid


async def test_deletion_sweep_failing_halfway_removes_nothing(world, org) -> None:
    gl, p = world.gl, org["project"]
    issues = [gl.add_issue(p, f"Issue {n:03}", author=org["dev"]) for n in range(130)]
    await world.sync()
    issues[0].deleted = True
    # The count shows one fewer; the id sweep's second page then fails for good.
    fail(world, on(f"{pid(p)}/issues", page="2"), status=502, times=100)
    await world.sync(expect_error=True)
    gl.faults.clear()
    names = await world.names("TICKET")
    assert len(names) in (129, 130)  # the deleted one may or may not be gone yet
    await world.sync()
    assert len(await world.names("TICKET")) == 129


async def test_sweep_that_comes_back_empty_removes_only_confirmed_deletions(world, org) -> None:
    gl, p = world.gl, org["project"]
    keep = [gl.add_issue(p, f"Keep {n}", author=org["dev"]) for n in range(3)]
    gone = gl.add_issue(p, "Gone", author=org["dev"])
    await world.sync()
    gone.deleted = True
    # The id sweep answers 200 with nothing: every stored issue becomes a candidate.
    fail(world, lambda m, path, q: m == "GET" and path.endswith(f"{pid(p)}/issues") and q.get("per_page") == "100"
         and "updated_after" not in q, status="empty", times=1)
    await world.sync()
    assert await world.names("TICKET") == sorted(i.title for i in keep)


async def test_candidate_lookup_failing_keeps_the_issue(world, org) -> None:
    gl, p = world.gl, org["project"]
    a = gl.add_issue(p, "A", author=org["dev"])
    b = gl.add_issue(p, "B", author=org["dev"])
    await world.sync()
    b.deleted = True
    fail(world, ends(f"{pid(p)}/issues/{b.iid}"), status=500, times=100)
    await world.sync(expect_error=True)
    assert await world.names("TICKET") == ["A", "B"]
    gl.faults.clear()
    await world.sync()
    assert await world.names("TICKET") == ["A"]
    assert a


async def test_moved_and_deleted_in_the_same_window(world, org) -> None:
    gl, p = world.gl, org["project"]
    other = gl.add_project(org["group"], "elsewhere")
    moved = gl.add_issue(p, "Moved", author=org["dev"])
    gone = gl.add_issue(p, "Gone", author=org["dev"])
    await world.sync()
    copy = gl.add_issue(other, "Moved", author=org["dev"])
    moved.state, moved.moved_to_id = "closed", copy.id
    gl.touch(moved)
    gone.deleted = True
    await world.sync()
    assert await world.names("TICKET") == ["Moved"]


async def test_project_answering_503_or_401_removes_nothing(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    await world.sync()
    fail(world, lambda m, path, q: path.endswith("/api/v4/projects/acme%2Fwidgets"), status=503, times=100)
    await world.sync(expect_error=True)
    gl.faults.clear()
    gl.sync_token.revoked = True
    await world.sync(expect_error=True)
    gl.sync_token.revoked = False
    diff = await world.diff()
    assert diff.clean(), diff


async def test_empty_or_failed_repository_walk_removes_no_file(world, org) -> None:
    gl, p = world.gl, org["project"]
    await world.sync()
    # Force a full walk (an admin full sync), with the tree failing on its second page.
    gl.commit(p, {f"lib/m{n:03}.py": "x\n" for n in range(150)})
    await world.sync()
    fail(world, on("/api/graphql", method="POST"), status=502, times=100, skip=1)
    await world.sync(full=True, expect_error=True)
    gl.faults.clear()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_member_listing_answering_empty_keeps_stored_access(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    await world.sync()
    fail(world, ends(f"{pid(p)}/members/all"), status="empty", times=100)
    await world.sync()
    gl.faults.clear()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_many_issues_changed_in_the_same_millisecond(world, org) -> None:
    gl, p = world.gl, org["project"]
    issues = [gl.add_issue(p, f"Issue {n:03}", author=org["dev"]) for n in range(250)]
    stamp = gl.clock.tick()
    for i in issues:
        i.updated_at = stamp
    await world.sync()
    assert len(await world.names("TICKET")) == 250


async def test_modified_before_filter_is_respected_by_the_new_listing(world, org) -> None:
    gl, p = world.gl, org["project"]
    early = gl.add_issue(p, "Early", author=org["dev"])
    gl.clock.advance(3600)
    cutoff = gl.clock.t
    gl.clock.advance(3600)
    gl.add_issue(p, "Late", author=org["dev"])
    world.set_sync_filter("modified", {"start": None, "end": int(cutoff * 1000)}, operator="is_before", type_="datetime")
    await world.sync()
    assert await world.names("TICKET") == ["Early"]
    assert early


async def test_blocked_author_of_a_confidential_issue_loses_access(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Guest report", author=org["gus"], confidential=True)
    await world.sync()
    org["gus"].state = "blocked"
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_upgrade_code_checkpoint_without_a_path_rewalks_once(world, org) -> None:
    """A checkpoint written by the old code has no project path: one walk, then incremental."""
    gl, p = world.gl, org["project"]
    await world.sync()
    key = generate_record_sync_point_key(Connectors.GITLAB.value, f"{p.id}-code-repository", "")
    await world.connector.record_sync_point.update_sync_point(key, {"last_commit_sha": p.branches["main"]})
    p.old_paths.append(p.path_with_namespace)
    p.path = "renamed-before-upgrade"
    world.select(p)
    world.pending_full_sync = False
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    calls = len(gl.calls)
    await world.sync()
    assert not [c for c in gl.calls[calls:] if c.path == "/api/graphql"], "walked again with nothing changed"


async def test_upgrade_malicious_attachment_record_is_never_fetched(world, org) -> None:
    """A record the old code wrote from a link that leaves /uploads/: streaming refuses it."""
    gl, p = world.gl, org["project"]
    vault = gl.add_project(org["group"], "vault", members={org["maya"]: 40}, files={"s.txt": "PRIVATE\n"})
    issue = gl.add_issue(p, "Issue", author=org["gus"])
    await world.sync()
    from app.models.entities import FileRecord, RecordGroupType, RecordType
    bad = f"{gl.base_url}/api/v4/projects/{p.id}/uploads/0123456789abcdef0123456789abcdef/../../../../projects/{vault.id}/repository/files/s.txt/raw"
    record = FileRecord(org_id=world.org_id, record_name="s.txt", record_type=RecordType.FILE, external_record_id=bad,
                        connector_name=Connectors.GITLAB, connector_id=world.connector_id, weburl=bad,
                        record_group_type=RecordGroupType.PROJECT, parent_external_record_id=str(issue.id),
                        external_record_group_id=f"{p.id}-work-items", is_file=True, extension="txt",
                        version=0, inherit_permissions=True, origin="CONNECTOR")
    await world.processor.on_new_records([(record, [])])
    with pytest.raises(HTTPException):
        await world.stream(bad)
    assert not [c for c in gl.calls if f"/projects/{vault.id}/" in c.path]


async def test_upgrade_leftover_deleted_issue_is_removed(world, org) -> None:
    """Issues deleted while the old code ran stay in PipesHub; the first sync of the new
    code (no deletion-check state yet) sweeps once and removes them."""
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Keep", author=org["dev"])
    gone = gl.add_issue(p, "Deleted under the old code", author=org["dev"])
    await world.sync()
    for suffix in ("-work-items-deletion-check",):
        key = generate_record_sync_point_key(Connectors.GITLAB.value, f"{p.id}{suffix}", "")
        await world.connector.record_sync_point.delete_sync_point(key)
    gone.deleted = True
    await world.sync()
    assert await world.names("TICKET") == ["Keep"]


async def test_unchanged_sync_after_the_fixes_costs_little(world, org) -> None:
    """What the fixes add to a sync where nothing changed: one count each for issues and
    merge requests. No id sweep, no tree walk, no profile lookups."""
    gl, p = world.gl, org["project"]
    for n in range(20):
        gl.add_issue(p, f"I{n}", author=org["dev"])
    await world.sync()
    calls = len(gl.calls)
    await world.sync()
    unchanged = gl.calls[calls:]
    counts = [c for c in unchanged if c.query.get("per_page") == "1"]
    sweeps = [c for c in unchanged if c.query.get("per_page") == "100" and "order_by" not in c.query
              and c.resource.endswith(("/issues", "/merge_requests"))]
    assert len(counts) == 2, counts
    assert not sweeps, sweeps
    assert not [c for c in unchanged if c.path == "/api/graphql" or c.resource == "/api/v4/users/:n"]


@pytest.mark.personal
async def test_personal_connector_removes_deleted_issues_and_shows_only_the_creator(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Keep", author=org["dev"])
    gone = gl.add_issue(p, "Gone", author=org["dev"])
    await world.sync()
    gone.deleted = True
    await world.sync()
    assert await world.names("TICKET") == ["Keep"]
    assert not await world.visible_to(org["dev"].email)
    assert await world.visible_to(world.creator_email)


async def test_project_shared_with_a_group_whose_member_is_awaiting(world, org) -> None:
    gl, p = world.gl, org["project"]
    partners = gl.add_group("partners")
    pia = gl.add_user("pia", world.email("pia", "partner"))
    partners.members[pia.id] = Membership(30, state="awaiting")
    p.shared_with[partners.id] = 30
    await world.add_pipeshub_user(pia.email)
    gl.add_issue(p, "Issue", author=org["dev"])
    await world.sync()
    assert not await world.visible_to(pia.email)
