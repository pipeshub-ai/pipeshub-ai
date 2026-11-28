"""After a source account's address passes to a new account, the grants written later, on both graphs.

The real processor and the real access check. Once the processor has seen the address change hands (a new source id
behind a stored address), a grant naming the address reaches the PipesHub user only when it names the account that
holds the address now: a re-read listing the deleted account, or naming no account at all, writes nothing for it,
while the new account's own grants and group memberships are written. Nobody else's access changes.
"""

import os

import pytest

from app.config.constants.arangodb import Connectors
from app.models.entities import AppUser, AppUserGroup
from app.models.permission import EntityType, Permission, PermissionType

from ..write_path_support import _suffix
from . import test_onedrive_inheritance_write_path as onedrive
from .test_onedrive_inheritance_write_path import Drive

drive = onedrive.drive      # the fixture: Ana's OneDrive, shared with Ben and Cal; Ben is the account u-ben

pytestmark = pytest.mark.integration
# With the accounts kept as nodes, an address listed for a new account while the old one is still stored links neither until the old one
# is withdrawn (test_source_account_writers.py); these cases read the registry's immediate hand-over.
registry_only = pytest.mark.skipif(os.environ.get("PERM_SOURCE_ACCOUNTS") == "1", reason="the registry's hand-over; accounts are covered by test_source_account_writers")


def _grant(email: str, source_id: str | None, role: PermissionType = PermissionType.READ) -> Permission:
    return Permission(email=email, external_id=source_id, type=role, entity_type=EntityType.USER)


def _member(drive: Drive, person: str, source_id: str) -> AppUser:
    return AppUser(app_name=Connectors.ONEDRIVE, connector_id=drive.connector_id, source_user_id=source_id,
                   email=drive.emails[person], full_name=person, is_active=True)


async def _ben_reused(drive: Drive) -> None:
    """Ben's account was deleted and a new one (u-ben-new) took his address."""
    await drive.processor.on_new_app_users([
        _member(drive, person, "u-ben-new" if person == "ben" else f"u-{person}") for person in drive.emails
    ])


@registry_only
async def test_a_reread_writes_the_grant_only_for_the_account_that_holds_the_address(drive: Drive) -> None:
    await drive.write()
    await _ben_reused(drive)
    owner = _grant(drive.emails["ana"], "u-ana", PermissionType.OWNER)
    plans = drive.records["Plans"]

    await drive.processor.on_updated_record_permissions(plans, [owner, _grant(drive.emails["ben"], "u-ben")])
    assert await drive.readers("Plans") == {"ana"}, "a grant to the deleted account"

    await drive.processor.on_updated_record_permissions(plans, [owner, _grant(drive.emails["ben"], None)])
    assert await drive.readers("Plans") == {"ana"}, "a grant naming only the address"

    await drive.processor.on_updated_record_permissions(plans, [owner, _grant(drive.emails["ben"], "u-ben-new")])
    assert await drive.readers("Plans") == {"ana", "ben"}, "the new account's own grant"
    assert await drive.readers("budget.pdf") == {"ana", "cal"}


@registry_only
async def test_a_group_lists_the_new_account_and_not_the_deleted_one(drive: Drive) -> None:
    await drive.write()
    group = AppUserGroup(source_user_group_id=f"g:{_suffix()}", app_name=Connectors.ONEDRIVE,
                         connector_id=drive.connector_id, name="Planners")
    await drive.processor.on_new_user_groups([(group, [_member(drive, "ben", "u-ben"), _member(drive, "dee", "u-dee")])])
    budget = drive.records["budget.pdf"]
    owner = _grant(drive.emails["ana"], "u-ana", PermissionType.OWNER)
    to_group = Permission(external_id=group.source_user_group_id, type=PermissionType.READ, entity_type=EntityType.GROUP)
    await drive.processor.on_updated_record_permissions(budget, [owner, to_group])
    assert await drive.readers("budget.pdf") == {"ana", "ben", "dee"}

    await _ben_reused(drive)
    assert await drive.readers("budget.pdf") == {"ana", "dee"}, "the old account's membership went with its grants"

    await drive.processor.on_new_user_groups([(group, [_member(drive, "ben", "u-ben"), _member(drive, "dee", "u-dee")])])
    assert await drive.readers("budget.pdf") == {"ana", "dee"}, "a group still listing the deleted account"

    await drive.processor.on_new_user_groups([(group, [_member(drive, "ben", "u-ben-new"), _member(drive, "dee", "u-dee")])])
    assert await drive.readers("budget.pdf") == {"ana", "ben", "dee"}, "the new account as a member"


async def test_a_link_already_rewritten_to_the_new_account_still_takes_the_old_accounts_grants(drive: Drive) -> None:
    """The link was moved to the new account by a write that skipped the check (an earlier release linked by address
    alone): the id this connector last linked the address to tells the change of hands."""
    await drive.write()
    async with drive.processor.data_store_provider.transaction() as tx:
        await tx.batch_upsert_app_users([_member(drive, "ben", "u-ben-new")], drive.processor.org_id)
    assert "ben" in await drive.readers("Plans")

    await _ben_reused(drive)

    assert "ben" not in await drive.readers("Plans") and "ben" not in await drive.readers("plan.pdf")
    assert await drive.readers("budget.pdf") == {"ana", "cal"}
