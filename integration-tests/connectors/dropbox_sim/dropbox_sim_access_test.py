# pyright: ignore-file

"""Dropbox team: groups and people, against the Dropbox simulator (design: DESIGN.md here).

The tenant is ``access_scenario``: alice (admin), bob, carol, dan, erin; Engineering (bob, carol) and Finance (dan);
the Projects and Finance team folders; Design, carol's shared folder, shared with bob and with gus, who is outside
the team but has a PipesHub account under the same email.

Every access check asks the simulator who Dropbox lets read each file and compares that, person by person, with
what PipesHub lets each of them open (``assert_access_matches_source``).

One connector, in order:
  DBX-G-01  first sync: both groups are in the graph with their members; access matches Dropbox for everyone,
            including the outside collaborator, and the team admin opens no member's home
  DBX-G-02  a member joins a group and another leaves it: access to the group's team folder follows
  DBX-G-03  the outside collaborator is removed from the shared folder and loses it
  DBX-G-04  a new member joins the team and a group: their own files and the group's are theirs at once
  DBX-G-05  a group is deleted: its members lose what only the group gave them, and the group leaves the graph
Team member root layout (each member's root holds their home and the team folders; how the live team that
reported the next case is laid out), a second connector, in order:
  DBX-R-01  first sync: access matches Dropbox
  DBX-R-02  a folder in the team folder stops inheriting and is shared with a group: its files stay, the group
            and the member who restricted it open them, nobody else finds the folder or its files
  DBX-R-03  that folder is deleted: Dropbox keeps it in the shared folder list and its namespace still lists its
            files, yet its files and record group leave PipesHub; the team folder's own files stay
"""

from __future__ import annotations

from typing import Any

import pytest

from connectors.dropbox_sim.dropbox_it import (
    assert_access_matches_source,
    pipeshub_people,
    assert_no_simulator_problems,
    assert_succeeded,
    sync,
)
from connectors.dropbox_sim.dropbox_sim import DOMAIN, access_scenario, team_member_root_scenario
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import async_poll_until  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.dropbox_sim,
    pytest.mark.asyncio(loop_scope="session"),
]

FILES = ["alice-notes", "bob-todo", "erin-cv", "p-spec", "f-budget", "d-logo"]


async def _group(graph: GraphProviderProtocol, cid: str, group_id: str, what: str, *, present: bool = True,
                 name: str | None = None) -> Any:
    found: dict[str, Any] = {}

    async def check() -> bool | None:
        group = await graph.get_user_group_by_external_id(cid, group_id)
        found["group"] = group
        if not present:
            return group is None or None
        return (group is not None and (name is None or group.name == name)) or None

    await async_poll_until(check, timeout=180, interval=3, description=what)
    return found.get("group")


async def _membership(graph: GraphProviderProtocol, cid: str, group_id: str, email: str, member: bool, what: str) -> None:
    async def check() -> bool | None:
        return (await graph.group_has_member(cid, group_id, email)) is member or None

    await async_poll_until(check, timeout=180, interval=3, description=what)


class TestDropboxTeamAccess:
    TENANT = staticmethod(access_scenario)

    @pytest.mark.order(0)
    async def test_dbx_g_01_groups_and_access_after_first_sync(self, dropbox_team: dict[str, Any],
                                                               graph_provider: GraphProviderProtocol,
                                                               pipeshub_client: PipeshubClient) -> None:
        sim, cid, users = dropbox_team["sim"], dropbox_team["connector_id"], dropbox_team["users"]
        assert "dbx-it-gus@partner." + DOMAIN in sim.readers("d-logo"), "the tenant shares Design with gus"
        assert users["alice"]["email"] not in sim.readers("bob-todo"), "a team admin reads no member's home"
        eng, fin = sim.key_id("group:eng"), sim.key_id("group:fin")
        await _group(graph_provider, cid, eng, "Engineering in the graph", name="Engineering")
        await _group(graph_provider, cid, fin, "Finance in the graph", name="Finance")
        assert await graph_provider.count_group_members(cid, eng) == 2, "Engineering has bob and carol"
        for who in ("bob", "carol"):
            assert await graph_provider.group_has_member(cid, eng, users[who]["email"]), f"{who} is in Engineering"
        assert await graph_provider.group_has_member(cid, fin, users["dan"]["email"]), "dan is in Finance"
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, FILES, "after first sync")
        assert_succeeded(pipeshub_client, cid, "first sync")
        assert_no_simulator_problems(sim)

    @pytest.mark.order(1)
    async def test_dbx_g_02_membership_change(self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol,
                                              pipeshub_client: PipeshubClient) -> None:
        sim, cid, users = dropbox_team["sim"], dropbox_team["connector_id"], dropbox_team["users"]
        eng = sim.key_id("group:eng")
        sim.ops({"op": "group_add_member", "group": "eng", "member": "dan"},
                {"op": "group_remove_member", "group": "eng", "member": "carol"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        await _membership(graph_provider, cid, eng, users["dan"]["email"], True, "dan joined Engineering")
        await _membership(graph_provider, cid, eng, users["carol"]["email"], False, "carol left Engineering")
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, ["p-spec", "f-budget"],
                                           "after the membership change")

    @pytest.mark.order(2)
    async def test_dbx_g_03_collaborator_removed(self, dropbox_team: dict[str, Any],
                                                 graph_provider: GraphProviderProtocol,
                                                 pipeshub_client: PipeshubClient) -> None:
        sim = dropbox_team["sim"]
        sim.ops({"op": "folder_remove_member", "folder": "design", "principal": "ext:gus"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, ["d-logo"],
                                           "after gus was removed from Design")

    @pytest.mark.order(3)
    async def test_dbx_g_04_new_member_joins(self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol,
                                             pipeshub_client: PipeshubClient, users_client: Any) -> None:
        sim = dropbox_team["sim"]
        email = f"dbx-it-hana@{DOMAIN}"
        dropbox_team["users"].update(await pipeshub_people(users_client, graph_provider, {"hana": email}))
        sim.ops({"op": "add_member", "key": "hana", "name": "Hana Ho", "email": email},
                {"op": "group_add_member", "group": "eng", "member": "hana"},
                {"op": "add_file", "in": "home:hana", "key": "hana-file", "name": "hana.txt"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team,
                                           ["hana-file", "p-spec", "bob-todo"], "after hana joined")

    @pytest.mark.order(4)
    async def test_dbx_g_05_group_deleted(self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol,
                                          pipeshub_client: PipeshubClient) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        sim.ops({"op": "delete_group", "group": "eng"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        await _group(graph_provider, cid, sim.key_id("group:eng"), "Engineering gone from the graph", present=False)
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, FILES,
                                           "after Engineering was deleted")
        assert_succeeded(pipeshub_client, cid, "the sync after the group delete")


PLANS = ["p-plans-a", "p-plans-b", "p-spec", "f-budget", "d-logo"]


class TestDropboxTeamMemberRoot:
    TENANT = staticmethod(team_member_root_scenario)
    BEHAVIOURS = {"team_member_root": True}

    @pytest.mark.order(5)
    async def test_dbx_r_01_first_sync(self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol,
                                       pipeshub_client: PipeshubClient) -> None:
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, PLANS, "after first sync")
        assert_succeeded(pipeshub_client, dropbox_team["connector_id"], "first sync")

    @pytest.mark.order(6)
    async def test_dbx_r_02_folder_stops_inheriting_then_shared_with_group(
        self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol, pipeshub_client: PipeshubClient,
    ) -> None:
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        sim.ops({"op": "restrict_folder", "item": "p-plans", "by": "alice"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        sim.ops({"op": "folder_add_member", "folder": "p-plans-ns", "principal": "group:fin", "access": "editor"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        assert sim.readers("p-plans-a") == {dropbox_team["users"][k]["email"] for k in ("alice", "dan")}
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, PLANS,
                                           "after Plans stopped inheriting and was shared with Finance")
        folder = await graph_provider.get_record_by_external_id(cid, sim.key_id("nsroot:p-plans-ns"))
        assert folder is None or folder.is_deleted, "Plans is a record group now, not a folder in Projects"
        group = await graph_provider.get_record_group_by_external_id(cid, sim.key_id("ns:p-plans-ns"))
        assert group is not None, "Plans has a record group of its own"
        assert_succeeded(pipeshub_client, cid, "the sync after the share")
        assert_no_simulator_problems(sim)

    @pytest.mark.order(7)
    async def test_dbx_r_03_folder_deleted_inside_the_team_folder(
        self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol, pipeshub_client: PipeshubClient,
    ) -> None:
        """Live, 2026-10-29: a deleted 'Korean files' inside the team folder stayed in All Records."""
        sim, cid = dropbox_team["sim"], dropbox_team["connector_id"]
        sim.ops({"op": "delete_shared_folder", "folder": "p-plans-ns"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        assert sim.readers("p-plans-a") == set()
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, PLANS,
                                           "after Plans was deleted from Projects")
        group = await graph_provider.get_record_group_by_external_id(cid, sim.key_id("ns:p-plans-ns"))
        assert group is None or getattr(group, "is_deleted", False), "the deleted folder's record group is gone"
        assert_succeeded(pipeshub_client, cid, "the sync after the delete")
        assert_no_simulator_problems(sim)
