"""A new Entra account that takes a deleted account's address, on both graphs: the real processor and the real access check.

Microsoft: "The new account is assigned a new ID value even though the UPN is the same", and the SharePoint and
OneDrive sites keep only the old ID, so the new account is denied what the old one could open
(https://learn.microsoft.com/en-us/troubleshoot/sharepoint/sharing-and-permissions/fix-site-user-id-mismatch).
PipesHub keeps one user per address; these check that the new account reads nothing the old one was granted, that
nobody else loses anything, and that a restored account (the same object id) keeps its access.
"""

import pytest

from app.config.constants.arangodb import Connectors
from app.models.entities import AppUser

from ..write_path_support import ORG
from . import test_onedrive_inheritance_write_path as onedrive
from .test_onedrive_inheritance_write_path import Drive

drive = onedrive.drive      # the fixture: Ana's OneDrive, shared with Ben and Cal

pytestmark = pytest.mark.integration


def _listing(drive: Drive, ben_id: str) -> list[AppUser]:
    ids = {"ana": "u-ana", "ben": ben_id, "cal": "u-cal", "dee": "u-dee"}
    return [AppUser(app_name=Connectors.ONEDRIVE, connector_id=drive.connector_id, source_user_id=ids[person],
                    email=email, full_name=person, is_active=True) for person, email in drive.emails.items()]


async def _everyone(drive: Drive) -> dict[str, set[str]]:
    return {name: await drive.readers(name) for name in ("root", "Plans", "plan.pdf", "budget.pdf")}


async def test_a_new_account_with_a_deleted_accounts_address_reads_nothing_the_old_one_was_granted(drive: Drive) -> None:
    await drive.write()
    accounts = drive.processor.source_accounts(drive.connector_id)
    before = await _everyone(drive)
    assert "ben" in before["Plans"] and "ben" in before["plan.pdf"]

    await drive.processor.on_new_app_users(_listing(drive, "u-ben-new"))  # Ben's account was deleted and a new one took his address

    after = await _everyone(drive)
    assert after == {name: readers - {"ben"} for name, readers in before.items()}
    assert not accounts.may_grant(drive.emails["ben"], "u-ben") and accounts.may_grant(drive.emails["ben"], "u-ben-new")


async def test_a_restored_account_keeps_what_it_could_read(drive: Drive) -> None:
    await drive.write()
    before = await _everyone(drive)
    ben = [link for link in await drive.processor.get_app_user_links(drive.connector_id) if link.source_user_id == "u-ben"]
    await drive.processor.withdraw_app_user_access(drive.connector_id, ben)  # deleted: withdrawn on the next sync
    assert all("ben" not in readers for readers in (await _everyone(drive)).values())

    await drive.processor.on_new_app_users(_listing(drive, "u-ben"))  # restored: the same object id

    assert await _everyone(drive) == before


async def test_an_address_taken_after_the_old_account_was_withdrawn_reads_nothing_of_it(drive: Drive) -> None:
    await drive.write()
    before = await _everyone(drive)
    ben = [link for link in await drive.processor.get_app_user_links(drive.connector_id) if link.source_user_id == "u-ben"]
    await drive.processor.withdraw_app_user_access(drive.connector_id, ben)

    await drive.processor.on_new_app_users(_listing(drive, "u-ben-new"))  # a later run: the new account appears

    assert await _everyone(drive) == {name: readers - {"ben"} for name, readers in before.items()}


async def test_the_old_accounts_role_memberships_go_with_its_grants(drive: Drive) -> None:
    """A role membership written for the old account is its access too (remove_user_access_to_connector on each store)."""
    from app.config.constants.arangodb import CollectionNames
    from app.models.entities import AppRole

    await drive.write()
    role = AppRole(app_name=Connectors.ONEDRIVE, connector_id=drive.connector_id, source_role_id="r-1", name="Reviewers", org_id=ORG)
    await drive.processor.on_new_app_roles([(role, [_listing(drive, "u-ben")[1]])])

    async def ben_roles() -> set[str]:
        edges = await drive.backend.provider.get_edges_from_node(
            f"{CollectionNames.USERS.value}/{drive.users['ben']}", CollectionNames.PERMISSION.value)
        return {e.get("_to") or e.get("to_id") for e in edges if CollectionNames.ROLES.value in str(e.get("_to") or e.get("to_collection"))}

    assert await ben_roles()
    await drive.processor.on_new_app_users(_listing(drive, "u-ben-new"))
    assert await ben_roles() == set()
