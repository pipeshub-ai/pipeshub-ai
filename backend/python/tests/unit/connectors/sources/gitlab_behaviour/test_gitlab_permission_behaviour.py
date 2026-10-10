"""Who can see what: roles, inherited group access, scoping filters, and failed membership reads."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from gitlab_server_fake import BASE_URL, GUEST, MINIMAL, OWNER, REPORTER
from gitlab_world import ALICE, API, BOB, CAROL, DAVE, WEB, build_acme

if TYPE_CHECKING:
    from gitlab_store_fakes import FakeRecordsDb


def project_audiences(db: FakeRecordsDb, project_id: int) -> dict[str, set[str]]:
    return {
        suffix or "project": db.group_access(f"{project_id}{suffix}")
        for suffix in ("", "-work-items", "-confidential-work-items", "-merge-requests", "-code-repository")
    }


async def test_project_roles_decide_which_parts_of_a_project_each_person_sees(harness, gitlab, db) -> None:
    build_acme(gitlab)

    await harness.sync()

    web = project_audiences(db, WEB)
    # Guests see work items only; Reporter and above see everything.
    assert "bob@example.com" in web["-work-items"]
    for part in ("-confidential-work-items", "-merge-requests", "-code-repository"):
        assert "bob@example.com" not in web[part]
    for part in web.values():
        assert {"alice@example.com", "owner@example.com"} <= part
    # dave has no public email, so his access is parked on a placeholder group for him.
    assert f"group:{DAVE}" in web["-code-repository"]
    # Access granted on a parent group reaches projects in its subgroups; not sideways.
    assert "carol@example.com" in db.group_access(f"{API}-code-repository")
    assert all("carol@example.com" not in part for part in web.values())


async def test_code_files_inherit_their_project_audience(harness, gitlab, db) -> None:
    build_acme(gitlab)

    await harness.sync()

    readme = db.access("/acme/web/-/blob/HEAD/README.md")
    assert "alice@example.com" in readme
    assert "bob@example.com" not in readme


async def test_someone_removed_from_a_group_loses_access_on_the_next_sync(harness, gitlab, db) -> None:
    build_acme(gitlab)
    await harness.sync()
    assert "alice@example.com" in db.group_access(f"{WEB}-code-repository")

    del gitlab.groups["acme"].members[ALICE]
    await harness.sync()

    for part in project_audiences(db, WEB).values():
        assert "alice@example.com" not in part


async def test_a_promoted_guest_gains_code_access_on_the_next_sync(harness, gitlab, db) -> None:
    build_acme(gitlab)
    await harness.sync()
    assert "bob@example.com" not in db.group_access(f"{WEB}-code-repository")

    gitlab.projects[WEB].members[BOB] = REPORTER
    await harness.sync()

    assert "bob@example.com" in db.group_access(f"{WEB}-code-repository")


async def test_a_member_who_later_shares_an_email_is_moved_off_the_placeholder_group(harness, gitlab, db) -> None:
    build_acme(gitlab)
    await harness.sync()
    assert f"group:{DAVE}" in db.group_access(f"{WEB}-code-repository")
    assert str(DAVE) not in db.app_users

    gitlab.users[DAVE].public_email = "dave@example.com"
    await harness.sync()

    assert db.app_users[str(DAVE)].email == "dave@example.com"
    code = db.group_access(f"{WEB}-code-repository")
    assert "dave@example.com" in code
    assert f"group:{DAVE}" not in code


async def test_every_member_becomes_a_user_even_when_one_profile_cannot_be_read(harness, gitlab, db) -> None:
    build_acme(gitlab)
    gitlab.fail("GET", rf"^/api/v4/users/{CAROL}$", 500, times=None)

    await harness.sync()

    assert {u.email for u in db.app_users.values()} >= {"alice@example.com", "bob@example.com", "owner@example.com"}
    # Without the profile there is no public email, so carol is parked on a placeholder group, not dropped.
    assert f"group:{CAROL}" in db.group_access(f"{API}-code-repository")


async def test_an_admin_token_with_no_memberships_still_reaches_every_project(harness, gitlab, db) -> None:
    gitlab.add_user(1, "root", is_admin=True)
    gitlab.add_user(ALICE, "alice", email="alice@example.com")
    gitlab.token_for(1)
    gitlab.add_group("solo")
    gitlab.add_project(21, "solo/one", members={ALICE: REPORTER}, files={"a.py": "x = 1\n"})
    gitlab.add_project(22, "solo/two", files={"b.py": "y = 2\n"})

    await harness.sync()

    assert {"/solo/one/-/blob/HEAD/a.py", "/solo/two/-/blob/HEAD/b.py"} <= set(db.by_type("CODE_FILE"))
    assert "owner@example.com" in db.group_access("22-code-repository")
    assert "alice@example.com" not in db.group_access("22-code-repository")


async def test_project_listing_follows_keyset_pages_to_the_end(harness, gitlab, db) -> None:
    build_acme(gitlab)
    gitlab.max_per_page = 2
    for pid in range(31, 35):
        gitlab.add_project(pid, f"acme/extra-{pid}", files={"x.py": "x\n"})

    await harness.sync()

    code_groups = {g for g in db.record_groups if g.endswith("-code-repository")}
    assert code_groups == {f"{pid}-code-repository" for pid in (WEB, API, 31, 32, 33, 34)}
    keyset = [r.params for r in gitlab.calls("GET", r"^/api/v4/projects$") if r.params.get("pagination") == "keyset"]
    assert [p.get("id_after") for p in keyset[:3]] == [None, str(API), "32"]


async def test_group_filter_limits_the_sync_to_that_group_and_its_subgroups(harness, gitlab, db) -> None:
    build_acme(gitlab)
    harness.set_sync_filter("group_ids", "in", ["acme/platform"])

    await harness.sync()

    assert f"{API}-code-repository" in db.record_groups
    assert f"{WEB}-code-repository" not in db.record_groups
    assert db.record_groups[str(API)].parent_external_group_id == "acme/platform"
    assert "carol@example.com" in db.group_access("acme/platform")
    # Users are discovered only from the filtered scope: bob is a member of acme/web alone.
    assert str(BOB) not in db.app_users


async def test_project_filter_syncs_only_the_named_project(harness, gitlab, db) -> None:
    build_acme(gitlab)
    harness.set_sync_filter("project_ids", "in", ["acme/web"], "select")

    await harness.sync()

    assert f"{WEB}-code-repository" in db.record_groups
    assert f"{API}-code-repository" not in db.record_groups
    assert db.record_groups[str(WEB)].parent_external_group_id == "acme"
    assert db.record_groups[str(WEB)].name == "web"
    assert db.record_groups[str(WEB)].web_url == f"{BASE_URL}/acme/web"


async def test_excluding_a_group_also_excludes_its_subgroup_projects(harness, gitlab, db) -> None:
    build_acme(gitlab)
    harness.set_sync_filter("group_ids", "not_in", ["acme/platform"])

    await harness.sync()

    assert f"{WEB}-code-repository" in db.record_groups
    assert f"{API}-code-repository" not in db.record_groups


async def test_a_filter_naming_a_project_that_is_gone_still_syncs_the_others(harness, gitlab, db) -> None:
    build_acme(gitlab)
    harness.set_sync_filter("project_ids", "in", ["acme/web", "acme/deleted"], "select")

    await harness.sync()

    assert f"{WEB}-code-repository" in db.record_groups
    assert "alice@example.com" in db.group_access(f"{WEB}-code-repository")


async def test_excluding_a_project_by_name_drops_it_and_its_only_members(harness, gitlab, db) -> None:
    build_acme(gitlab)
    harness.set_sync_filter("project_ids", "not_in", ["acme/web"])

    await harness.sync()

    assert f"{WEB}-code-repository" not in db.record_groups
    assert f"{API}-code-repository" in db.record_groups
    assert str(BOB) not in db.app_users
    assert db.app_users[str(CAROL)].email == "carol@example.com"


async def test_when_every_configured_project_fails_and_no_creator_is_known_the_sync_stops(harness, gitlab,
                                                                                         db) -> None:
    build_acme(gitlab)
    db.creators = {}
    harness.set_sync_filter("project_ids", "in", ["acme/web"], "select")
    gitlab.fail("GET", r"^/api/v4/projects/acme/web/members/all$", 403)

    with pytest.raises(RuntimeError, match="every configured group/project failed"):
        await harness.sync()

    assert db.record_groups == {}


async def test_an_auditor_whose_group_listing_comes_back_empty_falls_back_to_memberships(harness, gitlab, db) -> None:
    gitlab.add_user(1, "auditor", is_auditor=True)
    gitlab.add_user(CAROL, "carol", email="carol@example.com")
    gitlab.token_for(1)
    gitlab.add_group("corp", {1: REPORTER})
    gitlab.add_group("corp/infra", {CAROL: OWNER})
    gitlab.add_project(41, "corp/infra/tools", files={"t.py": "t\n"})

    await harness.sync()

    assert db.app_users[str(CAROL)].email == "carol@example.com"
    fallback = [r for r in gitlab.calls("GET", r"^/api/v4/groups$") if r.params.get("min_access_level") == "10"]
    assert fallback, "auditor fallback listing was never tried"
    assert gitlab.calls("GET", r"^/api/v4/groups/\d+/descendant_groups$")


async def test_a_guest_only_member_of_a_private_project_gets_no_code_even_via_the_project_node(harness, gitlab, db) -> None:
    build_acme(gitlab)
    gitlab.projects[WEB].members[BOB] = GUEST

    await harness.sync()

    assert "bob@example.com" not in db.access("/acme/web/-/blob/HEAD/src/app.py")


async def test_a_failed_project_member_read_keeps_the_stored_project_access(harness, gitlab, db) -> None:
    build_acme(gitlab)
    await harness.sync()
    before = project_audiences(db, WEB)
    assert "alice@example.com" in before["-code-repository"]

    gitlab.fail("GET", rf"^/api/v4/projects/{WEB}/members/all$", 503)
    await harness.sync()

    assert project_audiences(db, WEB) == before


async def test_a_failed_group_member_read_keeps_the_stored_group_access(harness, gitlab, db) -> None:
    build_acme(gitlab)
    harness.set_sync_filter("group_ids", "in", ["acme"])
    await harness.sync()
    before = db.group_access("acme")
    assert "alice@example.com" in before

    gitlab.fail("GET", r"^/api/v4/groups/acme/members/all$", 503)
    await harness.sync()

    assert db.group_access("acme") == before


async def test_a_failed_group_lookup_keeps_the_stored_group_access(harness, gitlab, db) -> None:
    build_acme(gitlab)
    harness.set_sync_filter("group_ids", "in", ["acme"])
    await harness.sync()
    before = db.group_access("acme")
    assert "alice@example.com" in before

    gitlab.fail("GET", r"^/api/v4/groups/acme$", 503)
    await harness.sync()

    assert db.group_access("acme") == before


ERIN = 6


@pytest.mark.parametrize(
    ("group_filter", "group_members_status"),
    [
        pytest.param(True, None, id="through-the-group-record-group"),
        pytest.param(False, None, id="unscoped-project-node"),
        pytest.param(True, 403, id="child-project-union"),
    ],
)
async def test_a_minimal_access_group_member_reads_nothing_in_the_groups_projects(
    harness, gitlab, db, group_filter, group_members_status,
) -> None:
    # GitLab: Minimal Access "cannot view project features such as wikis, issues, or the repository".
    build_acme(gitlab)
    gitlab.add_user(ERIN, "erin", email="erin@example.com")
    gitlab.groups["acme"].members[ERIN] = MINIMAL
    gitlab.add_issue(WEB, 1, "Issue 1", "2026-09-01T10:00:00Z")
    if group_filter:
        harness.set_sync_filter("group_ids", "in", ["acme"])
    if group_members_status:
        gitlab.fail("GET", r"^/api/v4/groups/acme/members(/all)?$", group_members_status)

    await harness.sync()

    if group_filter:
        assert "erin@example.com" not in db.group_access("acme")
    for part in project_audiences(db, WEB).values():
        assert "erin@example.com" not in part
    assert "erin@example.com" not in db.access(str(WEB * 1000 + 1))
    assert "bob@example.com" in db.access(str(WEB * 1000 + 1))
