"""What a Snowflake sync saves for the next one to compare against.

One sync point per database keeps that database's slice small (a large
account has tens of thousands of tables), plus an index naming the account
and the databases. A state saved for another account, or by another version,
is not used: the next sync then sweeps the graph instead.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, ValidationError

from app.connectors.sources.snowflake.catalog_state import (
    SnowflakeTableState,  # noqa: TC001 - a pydantic field type
)

if TYPE_CHECKING:
    from app.connectors.core.base.sync_point.sync_point import SyncPoint

logger = logging.getLogger(__name__)

SYNC_STATE_VERSION = 1
INDEX_KEY = "snowflake_state"


def database_key(database: str) -> str:
    return f"{INDEX_KEY}:{database}"


class DatabaseState(BaseModel):
    # Table FQN -> its state.
    tables: dict[str, SnowflakeTableState] = Field(default_factory=dict)
    # View FQN -> LAST_ALTERED; file id -> MD5 (or last modified).
    views: dict[str, str] = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict)
    # Schema keys (database.schema) and stage FQNs whose groups exist.
    schemas: list[str] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)


def _account(identifier: str | None) -> str:
    return (identifier or "").strip().lower()


class SnowflakeStateStore:
    def __init__(self, sync_point: SyncPoint) -> None:
        self.sync_point = sync_point

    async def load(self, account_identifier: str | None) -> dict[str, DatabaseState] | None:
        """The saved state by database, or None when there is none usable.

        Raises if the store cannot be read: taking that for "no state" would
        run a first sync over a partial view. A slice that is missing or does
        not parse makes the whole state unusable; failing on it would fail
        every later sync too.
        """
        index = await self.sync_point.read_sync_point(INDEX_KEY)
        if (
            not index
            or index.get("state_version") != SYNC_STATE_VERSION
            or index.get("account") != _account(account_identifier)
        ):
            return None
        states: dict[str, DatabaseState] = {}
        for database in index.get("databases") or []:
            raw = await self.sync_point.read_sync_point(database_key(database))
            payload = (raw or {}).get("state")
            try:
                states[database] = DatabaseState.model_validate_json(payload or "")
            except ValidationError:
                logger.warning("The saved sync state of database %s is missing or unreadable; syncing in full", database)
                return None
        return states

    async def save(
        self,
        account_identifier: str | None,
        states: dict[str, DatabaseState],
        *,
        previous: dict[str, DatabaseState] | None = None,
        sync_time: int | None = None,
    ) -> None:
        """Save every database's slice, then the index; drop slices of databases no longer kept."""
        for database, state in states.items():
            await self.sync_point.update_sync_point(database_key(database), {"state": state.model_dump_json()})
        index: dict[str, Any] = {
            "state_version": SYNC_STATE_VERSION,
            "account": _account(account_identifier),
            "databases": sorted(states),
        }
        if sync_time is not None:
            index["last_sync_time"] = sync_time
        await self.sync_point.update_sync_point(INDEX_KEY, index)
        for database in sorted(set(previous or {}) - set(states)):
            await self.sync_point.delete_sync_point(database_key(database))


def state_size(states: dict[str, DatabaseState]) -> int:
    """Bytes the saved state takes, for logging."""
    return sum(len(json.dumps(state.model_dump(), default=str)) for state in states.values())
