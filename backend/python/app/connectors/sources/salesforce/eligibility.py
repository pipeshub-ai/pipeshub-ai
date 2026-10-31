"""Which Salesforce users may read what PipesHub holds for them, beyond record sharing.

- Frozen users (``UserLogin.IsFrozen``) keep ``IsActive = true`` but cannot sign in.
- Product2 is not shared record by record: a user reads products when a permission set
  (a profile's included) grants Read on Product2, or View All Data. UserRecordAccess only
  covers objects on the Sharing Settings page, so it is not asked about products.

A query that fails returns None: the caller keeps what it stored, it does not guess.
"""

from __future__ import annotations

from logging import Logger
from typing import Any, AsyncIterator, Callable, Optional

RowPages = Callable[[str], AsyncIterator[list[dict[str, Any]]]]

FROZEN_USERS_SOQL = "SELECT UserId FROM UserLogin WHERE IsFrozen = true"
INACTIVE_USERS_SOQL = "SELECT Id FROM User WHERE IsActive = false"
PRODUCT_READERS_SOQL = (
    "SELECT AssigneeId FROM PermissionSetAssignment WHERE PermissionSetId IN "
    "(SELECT ParentId FROM ObjectPermissions WHERE SobjectType = 'Product2' AND PermissionsRead = true)"
)
VIEW_ALL_DATA_SOQL = (
    "SELECT AssigneeId FROM PermissionSetAssignment WHERE PermissionSet.PermissionsViewAllData = true"
)


class SalesforceEligibility:
    def __init__(self, rows: RowPages, logger: Logger,
                 on_unreadable: Optional[Callable[[str], None]] = None) -> None:
        self._rows = rows
        self._logger = logger
        self._on_unreadable = on_unreadable  # told what could not be read

    async def inactive_user_ids(self) -> Optional[set[str]]:
        return await self._ids(INACTIVE_USERS_SOQL, "Id", "inactive users")

    async def frozen_user_ids(self) -> Optional[set[str]]:
        return await self._ids(FROZEN_USERS_SOQL, "UserId", "frozen users")

    async def product_reader_ids(self) -> Optional[set[str]]:
        readers = await self._ids(PRODUCT_READERS_SOQL, "AssigneeId", "product readers")
        everything = await self._ids(VIEW_ALL_DATA_SOQL, "AssigneeId", "View All Data users")
        if readers is None or everything is None:
            return None
        return readers | everything

    async def _ids(self, soql: str, field: str, what: str) -> Optional[set[str]]:
        out: set[str] = set()
        try:
            async for page in self._rows(soql):
                out.update(str(r[field]) for r in page if r.get(field))
        except Exception as e:  # noqa: BLE001 - any failure means "unknown"
            self._logger.warning("Could not read the Salesforce %s: %s", what, e)
            if self._on_unreadable is not None:
                self._on_unreadable(what)
            return None
        return out
