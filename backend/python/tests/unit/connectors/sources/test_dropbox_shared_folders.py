"""Dropbox shared folders as one record group each, listed once, granted to their own members."""

from types import SimpleNamespace

from app.connectors.sources.dropbox.shared_folders import (
    folder_grants,
    outside_namespace,
    parents_first,
    plan_shared_folders,
)
from app.models.permission import EntityType, PermissionType


def _tag(value: str) -> SimpleNamespace:
    return SimpleNamespace(_tag=value)


def _entry(folder_id: str, name: str, access: str = "viewer", *, parent: str | None = None,
           inheritance: str = "inherit", team: bool = False) -> SimpleNamespace:
    return SimpleNamespace(shared_folder_id=folder_id, name=name, access_type=_tag(access), parent_shared_folder_id=parent,
                           access_inheritance=_tag(inheritance), is_team_folder=team)


class TestPlan:
    def test_one_folder_per_id_with_every_members_access(self) -> None:
        plan = plan_shared_folders({
            "dbmid:b": [_entry("1", "Korean files", "viewer")],
            "dbmid:a": [_entry("1", "Korean files", "editor")],
        })
        assert list(plan) == ["1"]
        assert plan["1"].access == {"dbmid:b": "viewer", "dbmid:a": "editor"}

    def test_the_owner_lists_the_folder(self) -> None:
        plan = plan_shared_folders({"dbmid:a": [_entry("1", "x", "viewer")], "dbmid:z": [_entry("1", "x", "owner")]})
        assert plan["1"].syncer == "dbmid:z"

    def test_equal_access_picks_the_lowest_member_id_so_the_cursor_stays_with_one_member(self) -> None:
        plan = plan_shared_folders({"dbmid:b": [_entry("1", "x", "editor")], "dbmid:a": [_entry("1", "x", "editor")]})
        assert plan["1"].syncer == "dbmid:a"

    def test_only_a_nested_inheriting_folder_inherits(self) -> None:
        plan = plan_shared_folders({"dbmid:a": [
            _entry("top", "Top"),
            _entry("open", "Open", parent="top"),
            _entry("closed", "Closed", parent="top", inheritance="no_inherit"),
        ]})
        assert (plan["top"].inherits, plan["open"].inherits, plan["closed"].inherits) == (False, True, False)
        assert plan["open"].parent_id == "top"

    def test_a_team_folder_is_marked(self) -> None:
        assert plan_shared_folders({"dbmid:a": [_entry("t", "Team", team=True)]})["t"].is_team_folder

    def test_parents_come_before_their_nested_folders(self) -> None:
        plan = plan_shared_folders({"dbmid:a": [
            _entry("c", "C", parent="b"), _entry("b", "B", parent="a"), _entry("a", "A"),
        ]})
        assert [folder.folder_id for folder in parents_first(plan)] == ["a", "b", "c"]


def _user(email: str, access: str, *, inherited: bool = False) -> SimpleNamespace:
    return SimpleNamespace(user=SimpleNamespace(email=email, account_id=f"dbid:{email}"), access_type=_tag(access), is_inherited=inherited)


class TestFolderGrants:
    def test_direct_users_groups_and_invitees_with_their_roles(self) -> None:
        grants = folder_grants(
            [_user("owner@x.com", "owner"), _user("viewer@x.com", "viewer")],
            [SimpleNamespace(group=SimpleNamespace(group_id="g:1"), access_type=_tag("editor"), is_inherited=False)],
            [SimpleNamespace(invitee=SimpleNamespace(email="guest@y.com"), access_type=_tag("viewer"), is_inherited=False)],
        )
        assert {(g.entity_type, g.email or g.external_id, g.type) for g in grants} == {
            (EntityType.USER, "owner@x.com", PermissionType.OWNER),
            (EntityType.USER, "viewer@x.com", PermissionType.READ),
            (EntityType.GROUP, "g:1", PermissionType.WRITE),
            (EntityType.USER, "guest@y.com", PermissionType.READ),
        }

    def test_inherited_members_stay_on_the_parent_folder(self) -> None:
        grants = folder_grants(
            [_user("from-parent@x.com", "editor", inherited=True)],
            [SimpleNamespace(group=SimpleNamespace(group_id="g:1"), access_type=_tag("viewer"), is_inherited=True)],
            [],
        )
        assert grants == []

    def test_a_user_without_a_usable_email_is_skipped(self) -> None:
        assert folder_grants([_user("", "viewer"), _user("hidden#", "viewer")], [], []) == []


def _file(*, parent: str | None = None, own: str | None = None) -> SimpleNamespace:
    info = None if parent is None and own is None else SimpleNamespace(parent_shared_folder_id=parent, shared_folder_id=own)
    return SimpleNamespace(sharing_info=info)


class TestOutsideNamespace:
    KNOWN = frozenset({"sf-open", "sf-nested"})

    def test_a_home_listing_keeps_the_members_own_files(self) -> None:
        assert not outside_namespace(_file(), None, self.KNOWN)

    def test_a_home_listing_leaves_out_a_mounted_shared_folder_and_its_content(self) -> None:
        assert outside_namespace(_file(own="sf-open"), None, self.KNOWN)
        assert outside_namespace(_file(parent="sf-open"), None, self.KNOWN)

    def test_a_shared_folders_listing_keeps_its_own_items(self) -> None:
        assert not outside_namespace(_file(parent="sf-open"), "sf-open", self.KNOWN)

    def test_a_shared_folders_listing_leaves_out_a_folder_nested_in_it(self) -> None:
        assert outside_namespace(_file(own="sf-nested", parent="sf-open"), "sf-open", self.KNOWN)
        assert outside_namespace(_file(parent="sf-nested"), "sf-open", self.KNOWN)

    def test_a_namespace_that_is_not_a_known_shared_folder_is_kept(self) -> None:
        """A team-space member's home folder carries its own namespace id; it is never in the shared list."""
        assert not outside_namespace(_file(parent="ns-home"), None, self.KNOWN)
