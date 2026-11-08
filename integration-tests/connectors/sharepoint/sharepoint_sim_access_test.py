# pyright: ignore-file

"""
SharePoint Online on the simulator – who can open what
======================================================

Each test changes who may read something in the simulated tenant, syncs, and checks that every person
opens in PipesHub exactly what the simulator's oracle says they may read (sim_world.py). The finding each
test proves is in brackets; those fail on upstream main and pass with the audit's fixes.

  TC-SPSIM-ACL-001  Removing someone from a shared folder removes their access to the files in it
                    [F-INHERITED-COPIES]
  TC-SPSIM-ACL-002  Removing a site-level grant removes access to everything that inherits it
                    [F-INHERITED-COPIES]
  TC-SPSIM-ACL-003  Tightening a library's permissions reaches its files [F-INHERITED-COPIES]
  TC-SPSIM-ACL-004  A folder moved into a restricted folder takes the restriction [F-INHERITED-COPIES]
  TC-SPSIM-ACL-005  A guest does not get what is shared with "Everyone except external users"
                    [F-GUEST-EVERYONE-EXT]
  TC-SPSIM-ACL-006  A disabled user loses access [F-DISABLED-USER]
  TC-SPSIM-ACL-007  A deleted user loses access [F-DISABLED-USER]
  TC-SPSIM-ACL-008  A user enabled again gets access back
  TC-SPSIM-ACL-009  Leaving a nested group removes the access it gave [F-NESTED-GROUP-STALE]
  TC-SPSIM-ACL-010  A member two groups down gets the access [F-NESTED-GROUP-DEPTH]
  TC-SPSIM-ACL-011  Joining and leaving a group adds and removes access
  TC-SPSIM-ACL-012  A sharing link to people grants access; deleting it takes it away
  TC-SPSIM-ACL-013  Deleting a SharePoint group takes away the access it gave [F-SITE-GROUP-DELETE]
  TC-SPSIM-ACL-014  A non-English owners group gives members no owner access [F-M365-OWNERS-TITLE]
  TC-SPSIM-ACL-015  Removing a user from a folder that already had its own permissions removes their
                    access [F-INHERITED-COPIES]
"""

import logging

import pytest
from connectors.sharepoint.sim_tenants import base_tenant, site
from connectors.sharepoint.sim_world import (
    QUIET,
    SharePointWorld,
)

logger = logging.getLogger("sharepoint-sim-access-test")

# Graph's delta does not return the children of an item whose sharing changed when they inherit from it
# (Microsoft, onedrive-api-docs#1250); the copies the old code kept on each child are what this exposes.
CHILDREN_NOT_REPORTED = {**QUIET, "delta_sharing_reports_descendants": False}


@pytest.mark.integration
@pytest.mark.sharepoint_sim
@pytest.mark.asyncio(loop_scope="session")
class TestSharePointSimAccess:

    async def test_tc_spsim_acl_001_folder_unshare_reaches_children(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=CHILDREN_NOT_REPORTED, enable=False)
        # Shared before the first sync, so that sync reads the files under it while the share exists.
        w.ops({"op": "grant", "target": "hr-policies", "kind": "user", "ref": "frank", "roles": ["Read"]})
        await w.enable()
        await w.assert_matches_source("with the folder shared with frank")
        w.ops({"op": "revoke", "target": "hr-policies", "kind": "user", "ref": "frank"})
        await w.sync()
        await w.assert_matches_source("after frank is removed from the folder")

    async def test_tc_spsim_acl_002_site_level_grant_revoked(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET, enable=False)
        w.ops({"op": "grant", "target": "fin", "kind": "user", "ref": "frank", "roles": ["Read"]})
        await w.enable()
        await w.assert_matches_source("with frank granted on the site")
        w.ops({"op": "revoke", "target": "fin", "kind": "user", "ref": "frank"})
        await w.sync()
        await w.assert_matches_source("after frank's site grant is removed")

    async def test_tc_spsim_acl_003_library_permissions_tightened(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.ops({"op": "revoke", "target": "eng-docs", "kind": "spgroup", "ref": "eng-visitors"})
        await w.sync()
        await w.assert_matches_source("after the library stops letting visitors in")

    async def test_tc_spsim_acl_004_folder_moved_into_restricted_folder(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.ops({"op": "move", "item": "eng-specs", "parent": "eng-restricted"})
        await w.sync()
        await w.assert_matches_source("after the folder moves into the restricted folder")

    async def test_tc_spsim_acl_005_guest_and_everyone_claims(self, sharepoint_world: SharePointWorld) -> None:
        """The Engineering site's visitors are "Everyone except external users"; the Intranet's, "Everyone"."""
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET, guests=True)
        await w.assert_matches_source("after the first sync, with the guest in PipesHub")

    async def test_tc_spsim_acl_006_disabled_user_loses_access(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.ops({"op": "set_user", "user": "carol", "enabled": False})
        await w.sync()
        await w.assert_matches_source("after carol is disabled")

    async def test_tc_spsim_acl_007_deleted_user_loses_access(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.ops({"op": "delete_user", "user": "erin"})
        await w.sync()
        await w.assert_matches_source("after erin is deleted")

    async def test_tc_spsim_acl_008_disabled_user_enabled_again(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.ops({"op": "set_user", "user": "erin", "enabled": False})
        await w.sync()
        w.ops({"op": "set_user", "user": "erin", "enabled": True})
        await w.sync()
        await w.assert_matches_source("after erin is enabled again")

    async def test_tc_spsim_acl_009_nested_group_member_removed(self, sharepoint_world: SharePointWorld) -> None:
        """Bob reaches Engineering through Engineering Leads, which is a member of Engineering."""
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.ops({"op": "remove_member", "group": "eng-leads", "member": "bob"})
        await w.sync()
        await w.assert_matches_source("after bob leaves the nested group")

    async def test_tc_spsim_acl_010_deeply_nested_group_member(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        tenant = base_tenant()
        tenant["groups"].append({"key": "eng-contractors", "name": "Eng contractors", "members": ["user:frank"]})
        next(g for g in tenant["groups"] if g["key"] == "eng-leads")["members"].append("group:eng-contractors")
        await w.start(tenant, behaviours=QUIET)
        await w.assert_matches_source("after the first sync, frank two groups below Engineering")

    async def test_tc_spsim_acl_011_group_membership_added_and_removed(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        w.ops({"op": "add_member", "group": "fin", "member": "frank"})
        await w.sync()
        await w.assert_matches_source("after frank joins Finance")
        w.ops({"op": "remove_member", "group": "fin", "member": "frank"})
        await w.sync()
        await w.assert_matches_source("after frank leaves Finance")

    async def test_tc_spsim_acl_012_sharing_link_to_people(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET)
        link = w.ops({"op": "share_link", "item": "eng-roadmap", "scope": "users", "users": ["frank"]})[0]
        await w.sync()
        await w.assert_matches_source("after the roadmap is shared with frank by link")
        w.ops({"op": "delete_link", "link": link["id"]})
        await w.sync()
        await w.assert_matches_source("after the link is deleted")

    async def test_tc_spsim_acl_013_deleted_site_group_takes_its_access(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        tenant = base_tenant()
        fin = site(tenant, "fin")
        fin["sp_groups"] = [{"key": "fin-auditors", "title": "Auditors", "members": ["user:frank"]}]
        fin["libraries"][0]["tree"].append({"folder": "Audit", "key": "fin-audit", "unique": True,
                                            "grants": [{"principal": "spgroup:fin-auditors", "roles": ["Read"]},
                                                       {"principal": "secgroup:fin", "roles": ["Read"]}],
                                            "children": [{"file": "audit.pdf", "key": "fin-audit-file"}]})
        await w.start(tenant, behaviours=QUIET)
        await w.assert_matches_source("with the Auditors group")
        w.ops({"op": "delete_sp_group", "site": "fin", "sp_group": "fin-auditors"})
        await w.sync()
        await w.assert_matches_source("after the Auditors group is deleted")

    async def test_tc_spsim_acl_014_m365_owners_claim_in_localised_group(self, sharepoint_world: SharePointWorld) -> None:
        w = sharepoint_world
        tenant = base_tenant()
        hr = site(tenant, "hr")
        hr["group_titles"] = {"owners": "Propriétaires de HR", "members": "Membres de HR"}
        hr["libraries"][0]["tree"].append({"folder": "Board", "key": "hr-board", "unique": True,
                                           "grants": [{"principal": "spgroup:hr-owners", "roles": ["Read"]}],
                                           "children": [{"file": "minutes.docx", "key": "hr-minutes"}]})
        await w.start(tenant, behaviours=QUIET)
        await w.assert_matches_source("after the first sync, owners group titled in French")

    async def test_tc_spsim_acl_015_unique_folder_user_revoked(self, sharepoint_world: SharePointWorld) -> None:
        """Salaries already has its own permissions, so frank's grant changes no inheritance."""
        w = sharepoint_world
        await w.start(base_tenant(), behaviours=QUIET, enable=False)
        w.ops({"op": "grant", "target": "hr-salaries", "kind": "user", "ref": "frank", "roles": ["Read"]})
        await w.enable()
        await w.assert_matches_source("with frank granted on the Salaries folder")
        w.ops({"op": "revoke", "target": "hr-salaries", "kind": "user", "ref": "frank"})
        await w.sync()
        await w.assert_matches_source("after frank's grant on the Salaries folder is removed")
