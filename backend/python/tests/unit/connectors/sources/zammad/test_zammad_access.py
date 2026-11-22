"""Who may read a Zammad group's tickets (``app.connectors.sources.zammad.access``).

Zammad's rule: the agent is active, holds ``ticket.agent`` through an active role,
the group is active, and the agent has ``read`` or ``full`` access to the group,
directly or through an active role. ``change``, ``create`` and ``overview`` do not
let an agent read a ticket.
"""

import pytest

from app.connectors.sources.zammad.access import (
    GroupReaders,
    RoleAccess,
    UserAccess,
    group_levels,
)

AGENT, CUSTOMER = 2, 3
SUPPORT = 1


def role(role_id: int = AGENT, *, active: bool = True, permissions: object = ("ticket.agent",),
         group_ids: object = None) -> dict:
    return {
        "id": role_id, "active": active,
        "permissions": list(permissions) if isinstance(permissions, tuple) else permissions,
        "group_ids": group_ids or {},
    }


def user(*, active: bool = True, role_ids: object = (AGENT,), group_ids: object = None) -> dict:
    return {
        "active": active,
        "role_ids": list(role_ids) if isinstance(role_ids, tuple) else role_ids,
        "group_ids": {} if group_ids is None else group_ids,
    }


def readers(users: dict[int, dict], roles: list[dict], group: int = SUPPORT, *, group_active: bool = True) -> set[int]:
    return GroupReaders(
        {uid: UserAccess.from_user(u) for uid, u in users.items()},
        {r["id"]: RoleAccess.from_role(r) for r in roles},
    ).readers(group, group_active=group_active)


class TestGroupLevels:
    def test_levels_are_keyed_by_group_id_string(self) -> None:
        assert group_levels({1: ["read", "change"], "2": ["full"]}) == {
            "1": frozenset({"read", "change"}), "2": frozenset({"full"}),
        }

    def test_a_single_level_may_come_as_a_string(self) -> None:
        assert group_levels({"1": "full"}) == {"1": frozenset({"full"})}

    @pytest.mark.parametrize("old_shape", [[1, 2], None, "1", 5])
    def test_anything_but_a_map_grants_nothing(self, old_shape: object) -> None:
        assert group_levels(old_shape) == {}

    def test_non_string_levels_are_dropped(self) -> None:
        assert group_levels({"1": ["read", None, 3]}) == {"1": frozenset({"read"})}


class TestAccessLevels:
    @pytest.mark.parametrize("levels", [["read"], ["full"], ["read", "change"], ["overview", "full"]])
    def test_read_or_full_access_makes_a_reader(self, levels: list[str]) -> None:
        assert readers({7: user(group_ids={"1": levels})}, [role()]) == {7}

    @pytest.mark.parametrize("levels", [["change"], ["create"], ["overview"], ["change", "create", "overview"], []])
    def test_change_create_or_overview_alone_is_not_read(self, levels: list[str]) -> None:
        assert readers({7: user(group_ids={"1": levels})}, [role()]) == set()

    def test_access_to_another_group_is_not_access_to_this_one(self) -> None:
        assert readers({7: user(group_ids={"2": ["full"]})}, [role()]) == set()


class TestAccessThroughRoles:
    def test_read_access_through_a_role(self) -> None:
        assert readers({7: user(group_ids={})}, [role(group_ids={"1": ["read"]})]) == {7}

    def test_a_roles_change_access_is_not_read(self) -> None:
        assert readers({7: user()}, [role(group_ids={"1": ["change", "overview"]})]) == set()

    def test_group_access_through_a_role_without_ticket_agent_reads_nothing(self) -> None:
        roles = [role(AGENT), role(CUSTOMER, permissions=("ticket.customer",), group_ids={"1": ["full"]})]
        # The agent permission comes from one role, the group access from another: both count.
        assert readers({7: user(role_ids=(AGENT, CUSTOMER))}, roles) == {7}
        # Without any agent role the access row does not count.
        assert readers({8: user(role_ids=(CUSTOMER,))}, roles) == set()

    def test_access_through_an_inactive_role_does_not_count(self) -> None:
        roles = [role(AGENT), role(CUSTOMER, active=False, group_ids={"1": ["full"]})]
        assert readers({7: user(role_ids=(AGENT, CUSTOMER))}, roles) == set()

    def test_access_through_a_role_the_user_does_not_hold_does_not_count(self) -> None:
        roles = [role(AGENT), role(CUSTOMER, group_ids={"1": ["full"]})]
        assert readers({7: user(role_ids=(AGENT,))}, roles) == set()


class TestTicketAgent:
    def test_a_user_without_ticket_agent_reads_nothing_even_with_full_access(self) -> None:
        roles = [role(CUSTOMER, permissions=("ticket.customer",))]
        assert readers({7: user(role_ids=(CUSTOMER,), group_ids={"1": ["full"]})}, roles) == set()

    def test_the_parent_permission_ticket_grants_ticket_agent(self) -> None:
        roles = [role(permissions=("ticket",))]
        assert readers({7: user(group_ids={"1": ["read"]})}, roles) == {7}

    @pytest.mark.parametrize("permissions", [("ticket.agent.extra",), ("tick",), ("ticket.customer",), ("admin",)])
    def test_other_permissions_do_not_grant_ticket_agent(self, permissions: tuple[str, ...]) -> None:
        assert RoleAccess.from_role(role(permissions=permissions)).agent is False

    def test_ticket_agent_held_only_through_an_inactive_role_does_not_count(self) -> None:
        assert readers({7: user(group_ids={"1": ["full"]})}, [role(active=False)]) == set()

    def test_permissions_missing_from_the_role_grant_nothing(self) -> None:
        """Without ``expand`` Zammad sends ``permission_ids`` only: nothing is assumed from them."""
        unexpanded = {"id": AGENT, "active": True, "permission_ids": [55], "group_ids": {}}
        assert readers({7: user(group_ids={"1": ["full"]})}, [unexpanded]) == set()

    def test_is_agent(self) -> None:
        group_readers = GroupReaders(
            {7: UserAccess.from_user(user()), 8: UserAccess.from_user(user(active=False)),
             9: UserAccess.from_user(user(role_ids=(CUSTOMER,)))},
            {AGENT: RoleAccess.from_role(role()),
             CUSTOMER: RoleAccess.from_role(role(CUSTOMER, permissions=("ticket.customer",)))},
        )
        assert [group_readers.is_agent(uid) for uid in (7, 8, 9, 404)] == [True, False, False, False]


class TestInactive:
    def test_an_inactive_user_reads_nothing(self) -> None:
        assert readers({7: user(active=False, group_ids={"1": ["full"]})}, [role()]) == set()

    def test_nobody_reads_an_inactive_group(self) -> None:
        assert readers({7: user(group_ids={"1": ["full"]})}, [role()], group_active=False) == set()

    def test_a_role_that_could_not_be_read_grants_nothing(self) -> None:
        """A role missing from the listing (the read failed) fails closed."""
        assert readers({7: user(group_ids={"1": ["full"]})}, []) == set()


class TestShapes:
    def test_role_ids_given_as_strings_count(self) -> None:
        assert readers({7: user(role_ids=[str(AGENT)], group_ids={"1": ["read"]})}, [role()]) == {7}

    @pytest.mark.parametrize("bad", [True, "x", None, 2.0])
    def test_role_ids_that_are_not_ids_are_ignored(self, bad: object) -> None:
        assert UserAccess.from_user(user(role_ids=[bad])).role_ids == frozenset()

    def test_a_missing_active_flag_means_active(self) -> None:
        assert UserAccess.from_user({"role_ids": [AGENT]}).active is True
        assert RoleAccess.from_role({"id": AGENT, "permissions": ["ticket.agent"]}).active is True

    def test_group_id_may_be_given_as_int_or_string(self) -> None:
        users = {7: user(group_ids={"1": ["read"]})}
        assert readers(users, [role()], group=1) == readers(users, [role()], group="1") == {7}  # type: ignore[arg-type]

    def test_several_readers_and_one_who_is_not(self) -> None:
        users = {
            7: user(group_ids={"1": ["full"]}),
            8: user(group_ids={"1": ["read"], "2": ["full"]}),
            9: user(group_ids={"1": ["change"]}),
        }
        assert readers(users, [role()]) == {7, 8}
        assert readers(users, [role()], group=2) == {8}
