"""The permission hierarchy end to end, the user who connected the connector, issues
turned off, listings that shrink while read, and code removal that fails or follows a
default-branch switch. Runs with the trash off and on.

Every test runs on Neo4j and ArangoDB (NEO4J_IT_URI / ARANGO_IT_URL) against the simulated
GitHub in ``sim/``; see README.md.
"""

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.github_teams.repos import ReposSync
from tests.integration.github_teams.helpers import hook_nth, issues_listing

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
BOTH_TRASH_MODES = True


# --------------------------------------------------------------- listing and code


async def test_issue_deleted_while_the_listing_is_read_does_not_hide_another(world, org) -> None:
    """The issue listing is read newest first by offset (``page=N``). An issue deleted
    after page 1 was read moves every later issue up one place, so the first issue of
    page 2 slides into page 1 and is never read. Its update is older than the
    checkpoint the sync then commits, so no later sync reads it either."""
    gh, repo = world.gh, org["repo"]
    issues = [gh.add_issue(repo, f"Issue {n:03}") for n in range(250)]
    await world.sync()
    for issue in issues:  # 250 changes since the checkpoint, at distinct seconds
        gh.edit_issue(issue, title=issue.title + " v2")
    # Between the first and second listing request, the newest issue (on page 1) is deleted.
    hook_nth(world, issues_listing(repo), 2, lambda: gh.delete_issue(issues[-1]))
    await world.sync()
    await world.sync()
    await world.sync()
    assert await world.names("TICKET") == sorted(i.title for i in issues[:-1])
    diff = await world.diff(check_missing=False)
    assert diff.clean(), diff


async def test_default_branch_switched_to_a_small_branch_removes_the_old_files(world, org) -> None:
    """A default-branch switch re-reads the whole tree. If the new branch holds far fewer
    files, the old ones must go: a complete, untruncated tree is GitHub's definite answer."""
    gh, repo = world.gh, org["repo"]
    gh.commit(repo, {f"lib/m{n}.py": str(n) for n in range(12)})
    await world.sync()
    gh.commit(repo, {"README.md": "# slim\n"}, branch="slim", replace=True, parent=None)
    repo.default_branch = "slim"
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_code_prune_that_fails_is_redone_next_sync(world, org, monkeypatch) -> None:
    """The removal step of a full tree walk fails once (graph store). The sync must not
    report success, and the next sync must remove the files."""
    gh, repo = world.gh, org["repo"]
    gh.commit(repo, {f"lib/m{n}.py": str(n) for n in range(12)})
    await world.sync()
    gh.commit(repo, {"README.md": "# slim\n"}, branch="slim", replace=True, parent=None)
    repo.default_branch = "slim"

    async def no_delta(*_args: object) -> bool:
        return False

    # A branch switch is synced from the delta when it can be; the walk is the fallback.
    monkeypatch.setattr(ReposSync, "_incremental_sync", no_delta)
    original = world.processor.on_records_deleted_cascade
    calls = {"n": 0}

    async def flaky(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("graph store unavailable")
        return await original(*args, **kwargs)

    monkeypatch.setattr(world.processor, "on_records_deleted_cascade", flaky)
    await world.sync(expect_error=True)
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


# --------------------------------------------------------------- permission hierarchy


async def test_grandchild_team_inherits_the_repository_role(world, org) -> None:
    """GitHub: a child team inherits its parent's repository access, at any depth."""
    gh, repo = world.gh, org["repo"]
    eve = gh.add_user("eve", "eve@acme.com")
    org["org"].members[eve.id] = "member"
    mobile = gh.add_team(org["org"], "core-mobile", [], parent=org["core"])
    gh.add_team(org["org"], "core-mobile-ios", [eve], parent=mobile)
    await world.add_pipeshub_user("eve@acme.com")
    gh.add_issue(repo, "Issue")
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    assert await world.visible_to("eve@acme.com")


async def test_team_removed_from_the_repository_loses_access(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    await world.sync()
    assert await world.visible_to("alice@acme.com")
    del org["core"].repo_roles[repo.id]
    await world.sync()
    assert await world.visible_to("alice@acme.com") == set()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_team_moved_out_from_under_its_parent_loses_inherited_access(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    dave = gh.add_user("dave", "dave@acme.com")
    org["org"].members[dave.id] = "member"
    ops = gh.add_team(org["org"], "ops", [dave], parent=org["core"])
    await world.add_pipeshub_user("dave@acme.com")
    gh.add_issue(repo, "Issue")
    await world.sync()
    assert await world.visible_to("dave@acme.com")
    ops.parent = None
    await world.sync()
    assert await world.visible_to("dave@acme.com") == set()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_base_permission_lowered_removes_access(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    org["org"].base_permission = "read"
    gh.add_issue(repo, "Issue")
    await world.sync()
    assert await world.visible_to("bob@acme.com")
    org["org"].base_permission = None
    await world.sync()
    assert await world.visible_to("bob@acme.com") == set()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_member_removed_from_the_org_loses_access(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    await world.sync()
    del org["org"].members[org["alice"].id]  # GitHub drops their team memberships with it
    await world.sync()
    assert await world.visible_to("alice@acme.com") == set()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_owner_demoted_to_member_loses_owner_access(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    oscar = gh.add_user("oscar", "oscar@acme.com")
    org["org"].members[oscar.id] = "admin"
    await world.add_pipeshub_user("oscar@acme.com")
    gh.add_issue(repo, "Issue")
    await world.sync()
    assert await world.visible_to("oscar@acme.com")
    org["org"].members[oscar.id] = "member"  # base permission none, no team
    await world.sync()
    assert await world.visible_to("oscar@acme.com") == set()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_outside_collaborator_who_joins_the_org_keeps_the_direct_grant(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    await world.sync()
    org["org"].members[org["olga"].id] = "member"
    await world.sync()
    assert await world.visible_to("olga@contractor.io")
    diff = await world.diff()
    assert diff.clean(), diff


async def test_access_never_leaks_between_two_repositories(world, org) -> None:
    """The org group carries the union of every repository's grants. Nobody may read a
    repository through it: each repository answers for itself."""
    gh, repo = world.gh, org["repo"]
    payroll = gh.add_repo(org["org"], "payroll", files={"pay.py": "salary = 1\n"})
    finance = gh.add_team(org["org"], "finance", [org["bob"]])
    finance.repo_roles[payroll.id] = "read"
    gh.add_issue(repo, "Widget bug")
    gh.add_issue(payroll, "Salaries 2026")
    gh.add_pull(payroll, "Raise")
    world.configure([repo, payroll])
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    alice_sees = await world.visible_to("alice@acme.com")
    bob_sees = await world.visible_to("bob@acme.com")
    assert alice_sees and bob_sees
    assert not any(e.startswith(f"{payroll.id}/") or f"/{payroll.id}/" in e for e in alice_sees)
    assert not any(e.startswith(f"{repo.id}/") or f"/{repo.id}/" in e for e in bob_sees)
    assert not any(str(payroll.id) in e for e in await world.visible_to("olga@contractor.io"))


async def test_sub_issue_in_a_private_repository_keeps_its_own_access(world, org) -> None:
    """A sub-issue's parent can live in another repository. The sub-issue is readable by
    its own repository's audience, never by its parent's."""
    gh, repo = world.gh, org["repo"]
    payroll = gh.add_repo(org["org"], "payroll")
    finance = gh.add_team(org["org"], "finance", [org["bob"]])
    finance.repo_roles[payroll.id] = "read"
    epic = gh.add_issue(repo, "Epic")
    task = gh.add_issue(payroll, "Pay contractors")
    task.parent = epic
    world.configure([repo, payroll])
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_archived_repository_keeps_its_records_and_access(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    gh.add_pull(repo, "PR")
    await world.sync()
    repo.archived = True
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff
    assert await world.names("TICKET") == ["Issue"]


async def test_member_with_a_private_email_is_matched_through_the_verified_domain(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    org["org"].verified_domain = True
    alice = org["alice"]
    alice.corporate_email, alice.email = alice.email, None
    gh.add_issue(repo, "Issue")
    await world.sync()
    assert await world.visible_to("alice@acme.com")
    diff = await world.diff()
    assert diff.clean(), diff


# --------------------------------------------------------------- the connector's own account


async def test_creator_reads_what_the_connected_account_reads(world, org) -> None:
    """The admin connected GitHub with an account whose email is not their PipesHub one.
    They read what that account reads, through the product's authenticatedAs link."""
    gh, repo = world.gh, org["repo"]
    org["admin"].email = "admin@acme-github.example"
    gh.add_issue(repo, "Issue")
    gh.add_pull(repo, "PR")
    await world.sync()
    assert await world.visible_to(world.creator_email)
    diff = await world.diff()
    assert diff.clean(), diff


async def test_creator_reads_what_the_connected_account_reads_when_its_email_is_private(world, org) -> None:
    """Most GitHub accounts keep their email private, so ``GET /user`` gives none. The
    org's verified domain still names the account's corporate email, and the user sync
    grants the account's access to it; the link must reach it too."""
    gh, repo = world.gh, org["repo"]
    org["org"].verified_domain = True
    admin = org["admin"]
    admin.email, admin.corporate_email = None, "github-admin@acme.com"
    gh.add_issue(repo, "Issue")
    await world.sync()
    assert await world.visible_to(world.creator_email)
    diff = await world.diff()
    assert diff.clean(), diff


async def test_repository_with_issues_turned_off_hides_them_and_still_syncs(world, org) -> None:
    """Turning issues off on GitHub hides every issue (410 on the listing), and turning
    them back on restores them. The sync must not fail on every run meanwhile, and
    PipesHub must follow GitHub both ways."""
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    gh.add_pull(repo, "PR")
    await world.sync()
    repo.has_issues = False
    await world.sync()
    assert await world.names("TICKET") == []
    diff = await world.diff()
    assert diff.clean(), diff
    repo.has_issues = True
    await world.sync()
    assert await world.names("TICKET") == ["Issue"]
    diff = await world.diff()
    assert diff.clean(), diff


async def test_comment_attachment_stays_readable_after_a_full_sync(world, org) -> None:
    """A file linked from a comment becomes a record only when the issue is streamed. A
    full sync drops the connector's links and rebuilds them from what it lists; the
    comment's file must not be left with no parent and no access."""
    gh, repo = world.gh, org["repo"]
    url = gh.upload(repo, "notes.txt", b"meeting notes")
    issue = gh.add_issue(repo, "Planning")
    gh.comment(issue, f"Notes: [notes.txt]({url})", org["alice"])
    await world.sync()
    assert any(k.startswith(url) for k in await world.by_type("FILE"))
    await world.sync(full=True)
    diff = await world.diff()
    assert diff.clean(), diff


async def test_pull_request_comment_attachment_survives_a_full_sync_with_issues_off(world, org) -> None:
    """With issues turned off, the issue sweep stops early. The files linked from pull
    request comments must still be kept by a full sync."""
    gh, repo = world.gh, org["repo"]
    url = gh.upload(repo, "notes.txt", b"review notes")
    pr = gh.add_pull(repo, "Feature")
    gh.comment(pr, f"Notes: [notes.txt]({url})", org["alice"])
    repo.has_issues = False
    await world.sync()
    assert any(k.startswith(url) for k in await world.by_type("FILE"))
    await world.sync(full=True)
    diff = await world.diff()
    assert diff.clean(), diff


async def test_upgrade_removes_url_keyed_attachments_without_waiting_for_the_issue(world, org) -> None:
    """Older versions keyed an attachment by its URL alone. The first sync of this
    version removes those records even for an issue that never changes again, so the
    file is not listed twice (once per key)."""
    from tests.integration.github_teams.test_80_fix_safety import _forget_issue_total

    gh, repo = world.gh, org["repo"]
    url = gh.upload(repo, "spec.pdf", b"%PDF spec")
    notes = gh.upload(repo, "notes.txt", b"meeting notes")
    issue = gh.add_issue(repo, "Spec", body=f"[spec.pdf]({url})")
    gh.comment(issue, f"Notes: [notes.txt]({notes})", org["alice"])
    await world.sync()
    ticket = await world.record(f"{repo.id}/issues/{issue.number}")
    # Older versions never wrote the keyed records: delete them outright (not to the trash).
    for key in [k for k in await world.by_type("FILE") if k.startswith("https://")]:
        await world.graph.delete_nodes_and_edges([(await world.record(key)).id], CollectionNames.RECORDS.value)
    for link, name in ((url, "spec.pdf"), (notes, "notes.txt")):  # as older versions wrote them
        legacy = world.connector.comments._attachment_file_update(
            {"type": name.rsplit(".", 1)[1], "href": link, "filename": name}, ticket, None, ticket.id).record
        legacy.external_record_id = link
        await world.processor.on_new_records([(legacy, [])])
    await _forget_issue_total(world, repo)
    await world.sync()
    files = sorted(k for k in await world.by_type("FILE") if k.startswith("https://"))
    # The comment's file comes back too: the issue is streamed again, without changing.
    assert files == sorted([f"{url}#{repo.id}/issues/{issue.number}", f"{notes}#{repo.id}/issues/{issue.number}"])
    diff = await world.diff()
    assert diff.clean(), diff


async def test_issue_older_than_a_new_window_keeps_its_access(world, org) -> None:
    """The admin narrows the sync to issues updated since a date. An older issue is no
    longer listed, which is not the same as gone: the full sync the filter change starts
    must not leave it unreadable."""
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Old")
    gh.tick(86_400)
    start = gh.clock.now()
    gh.tick(60)
    gh.add_issue(repo, "New")
    await world.sync()
    world.set_window(start)
    await world.sync()
    assert await world.names("TICKET") == ["New", "Old"]
    diff = await world.diff()
    assert diff.clean(), diff


async def test_attachment_linked_again_after_its_removal_comes_back(world, org) -> None:
    """A file unlinked from a comment leaves PipesHub (with the trash on, into the trash).
    Linked again, it must be readable again, not left in the trash."""
    gh, repo = world.gh, org["repo"]
    url = gh.upload(repo, "notes.txt", b"meeting notes")
    issue = gh.add_issue(repo, "Planning")
    comment = gh.comment(issue, f"Notes: [notes.txt]({url})", org["alice"])
    await world.sync()
    gh.edit_comment(issue, comment, "Notes: see the wiki")
    await world.sync()
    assert not any(k.startswith(url) for k in await world.by_type("FILE"))
    gh.edit_comment(issue, comment, f"Notes: [notes.txt]({url})")
    await world.sync()
    assert any(k.startswith(url) for k in await world.by_type("FILE"))
    diff = await world.diff()
    assert diff.clean(), diff
