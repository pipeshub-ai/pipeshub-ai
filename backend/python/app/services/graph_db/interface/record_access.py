"""The graph's record-access surface: every method that decides whether a user may read a record.

``IGraphDBProvider`` implements it on each backend; tests use one in-memory fake of it
(``tests/support/record_access.py``) held to the same contract as Neo4j and ArangoDB. A method added here must be
added to every provider and to the fake, or the contract run fails (H5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.services.graph_db.interface.graph_db_provider import AccessCheck, RowScope


class RecordAccessReader(Protocol):
    async def check_record_access_with_details(
        self, user_id: str, org_id: str, record_id: str
    ) -> dict[str, Any] | None:
        """The record's details when the user may read it in the org, else ``None``."""
        ...

    async def check_access(
        self,
        user_key: str,
        org_id: str,
        *,
        node_ids: Iterable[str] = (),
        virtual_record_ids: Iterable[str] = (),
        indexed_only: bool = False,
        connector_ids: frozenset[str] | None = None,
        scopes: Iterable[RowScope] = (),
        access: dict[str, Any] | None = None,
        transaction: str | None = None,
    ) -> AccessCheck:
        """The one batch access check (main #4255): every permission decision, the harness's raw reads too."""
        ...

    async def filter_accessible_record_ids(
        self, record_ids: list[str], user_id: str, org_id: str, *, transaction: str | None = None
    ) -> set[str]:
        """The ids among ``record_ids`` search's gates admit (stubs excluded): never more than the single check."""
        ...


RECORD_ACCESS_METHODS: tuple[str, ...] = tuple(
    sorted(name for name, value in vars(RecordAccessReader).items() if not name.startswith("_") and callable(value))
)

__all__ = ["RECORD_ACCESS_METHODS", "RecordAccessReader"]
