"""For every user and record, PipesHub's answer must equal GitLab's."""

import contextlib

from tests.integration.gitlab_simulated.glsim.model import Membership
from tests.integration.gitlab_simulated.helpers import ends, fail, pid


async def test_role_matrix(world, org) -> None:
    """Guest: issues. Planner: also confidential issues. Reporter and up: code and merge
    requests too. Outsider: nothing."""
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Open issue", author=org["dev"])
    gl.add_issue(p, "Secret issue", author=org["dev"], confidential=True)
    gl.add_mr(p, "MR", author=org["dev"])
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_confidential_issue_author_and_assignee_keep_access(world, org) -> None:
    gl, p = world.gl, org["project"]
    guest2 = gl.add_user("gina", world.email("gina"))
    p.members[guest2.id] = Membership(10)
    await world.add_pipeshub_user(guest2.email)
    gl.add_issue(p, "Reported by a guest", author=org["gus"], confidential=True)
    gl.add_issue(p, "Assigned to a guest", author=org["dev"], confidential=True, assignees=[guest2])
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_issue_made_confidential_then_public_again(world, org) -> None:
    gl, p = world.gl, org["project"]
    issue = gl.add_issue(p, "Flip", author=org["dev"])
    await world.sync()
    issue.confidential = True
    gl.touch(issue)
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    issue.confidential = False
    gl.touch(issue)
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_internal_note_is_not_shown_to_guests(world, org) -> None:
    """Internal notes are for Planner and up. A guest who can read the issue must not
    read its internal notes through PipesHub."""
    gl, p = world.gl, org["project"]
    issue = gl.add_issue(p, "Customer report", author=org["dev"], description="public text")
    gl.add_note(issue, "INTERNAL: the customer is Acme Bank, contract 4471", author=org["maya"], internal=True)
    gl.add_note(issue, "public reply", author=org["maya"])
    await world.sync()
    record = str(issue.id)
    assert record in await world.visible_to(org["gus"].email)
    text = await world.stream_text(record)
    assert "public reply" in text
    assert "contract 4471" not in text, "an internal note is indexed into a record guests can read"


async def test_attachment_in_an_internal_note_is_not_shown_to_guests(world, org) -> None:
    gl, p = world.gl, org["project"]
    href = gl.add_upload(p, "contract.pdf", b"%PDF confidential contract")
    issue = gl.add_issue(p, "Report", author=org["dev"])
    gl.add_note(issue, f"internal copy [contract]({href})", author=org["maya"], internal=True)
    await world.sync()
    diff = await world.diff()
    assert not diff.over, diff


async def test_blocked_user_loses_access(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    gl.add_mr(p, "MR", author=org["dev"])
    await world.sync()
    org["dev"].state = "blocked"
    await world.sync()
    assert not await world.visible_to(org["dev"].email), "a blocked GitLab user still reads the project"
    diff = await world.diff()
    assert diff.clean(), diff


async def test_deactivated_and_banned_users_have_no_access(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    org["rita"].state = "deactivated"
    org["gus"].state = "banned"
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_pending_member_awaiting_a_seat_has_no_access(world, org) -> None:
    """A member over the seat limit is ``awaiting`` and cannot open the project."""
    gl, p = world.gl, org["project"]
    newbie = gl.add_user("nora", world.email("nora"))
    p.members[newbie.id] = Membership(30, state="awaiting")
    await world.add_pipeshub_user(newbie.email)
    gl.add_issue(p, "Issue", author=org["dev"])
    gl.add_mr(p, "MR", author=org["dev"])
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_inherited_and_shared_group_access(world, org) -> None:
    """Access through the parent group, and through a group the project is shared with
    (capped at the share's role)."""
    gl, p = world.gl, org["project"]
    partners = gl.add_group("partners")
    pia = gl.add_user("pia", world.email("pia", "partner"))
    partners.members[pia.id] = Membership(40)
    p.shared_with[partners.id] = 20  # shared with Reporter
    sub_owner = gl.add_user("sam", world.email("sam"))
    org["group"].members[sub_owner.id] = Membership(10)
    for u in (pia, sub_owner):
        await world.add_pipeshub_user(u.email)
    gl.add_issue(p, "Issue", author=org["dev"])
    gl.add_issue(p, "Confidential", author=org["dev"], confidential=True)
    gl.add_mr(p, "MR", author=org["dev"])
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_member_removed_and_role_lowered(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    gl.add_mr(p, "MR", author=org["dev"])
    await world.sync()
    del p.members[org["dev"].id]
    p.members[org["rita"].id] = Membership(10)
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_minimal_access_group_member_sees_nothing(world, org) -> None:
    gl, p = world.gl, org["project"]
    mina = gl.add_user("mina", world.email("mina"))
    org["group"].members[mina.id] = Membership(5)
    await world.add_pipeshub_user(mina.email)
    gl.add_issue(p, "Issue", author=org["dev"])
    await world.sync()
    assert not await world.visible_to(mina.email)


async def test_member_without_a_public_email_gets_access_once_it_is_set(world, org) -> None:
    """No public email: the connector cannot match the PipesHub user yet (a pseudo-group
    holds the grant). Once the email is public the grant moves to the user."""
    gl, p = world.gl, org["project"]
    org["dev"].public_email = ""
    gl.add_issue(p, "Issue", author=org["rita"])
    await world.sync()
    assert not await world.visible_to(org["dev"].email)
    org["dev"].public_email = org["dev"].email
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_member_listing_failing_for_a_moment_costs_nobody_access(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    gl.add_mr(p, "MR", author=org["dev"])
    await world.sync()
    fail(world, ends(f"{pid(p)}/members/all"), status=503, times=200)
    with contextlib.suppress(Exception):
        await world.sync()
    gl.faults.clear()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_admin_sync_account_with_no_membership(world, org) -> None:
    """An instance admin syncs a project it is not a member of: it still reads all of it,
    and PipesHub's answer for everyone else stays GitLab's."""
    gl, p = world.gl, org["project"]
    root = gl.add_user("root", world.email("root"), is_admin=True)
    gl.set_sync_account(root)
    world.creator_email = root.email
    await world.add_pipeshub_user(root.email)
    world.creator_id = world.users[root.email]
    gl.add_issue(p, "Issue", author=org["dev"])
    gl.add_issue(p, "Confidential", author=org["dev"], confidential=True)
    gl.add_mr(p, "MR", author=org["dev"])
    world.configure(p)
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_public_project_non_members(world, org) -> None:
    """Documented decision: members only, at their role. GitLab lets anyone read a public
    project's issues, code and merge requests; the connector grants its members (code and
    merge requests from Reporter). PipesHub may show less than GitLab, never more."""
    gl, p = world.gl, org["project"]
    p.visibility = "public"
    gl.add_issue(p, "Public issue", author=org["dev"])
    await world.sync()
    diff = await world.diff()
    assert not diff.over, diff
    assert diff.under, "the scenario no longer shows the members-only gap it documents"


async def test_records_do_not_cross_tenants(world, org) -> None:
    """Two orgs connect the same GitLab project; each sees only its own copy."""
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    await world.sync()
    docs = await world.record_docs()
    assert docs and all(d.get("orgId") in (None, world.org_id) for d in docs)
