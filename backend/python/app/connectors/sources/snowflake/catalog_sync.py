"""What a Snowflake sync found, and what it may take for gone.

Pure decisions over the catalog read and the saved state; the connector does
the reads and writes. Nothing under a database, schema or stage that could not
be listed is ever taken for gone, and a table whose columns, keys or
foreign keys could not be read keeps its saved state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.connectors.core.sql.qualified_names import leading_parts, split_qualified_name
from app.connectors.sources.snowflake.catalog_state import SnowflakeTableState
from app.connectors.sources.snowflake.data_fetcher import SnowflakeHierarchy, schema_key
from app.connectors.sources.snowflake.sync_state import DatabaseState

Test = Callable[[str], bool] | None


def _passes(test: Test, name: str) -> bool:
    return test is None or test(name)


def file_id(stage_fqn: str, relative_path: str) -> str:
    return f"{stage_fqn}/{relative_path}"


@dataclass(frozen=True)
class Filters:
    """The sync filters as predicates; None lets everything through."""
    tables: Test = None
    views: Test = None
    stages: Test = None
    files: Test = None


@dataclass
class Scope:
    """What could not be listed this sync."""
    databases: set[str] = field(default_factory=set)
    schemas: set[str] = field(default_factory=set)
    stages: set[str] = field(default_factory=set)

    @classmethod
    def of(cls, hierarchy: SnowflakeHierarchy) -> Scope:
        return cls(
            databases=set(hierarchy.unreadable_databases),
            schemas=set(hierarchy.unreadable_schemas),
            stages=set(hierarchy.unreadable_stages),
        )

    def schema_unreadable(self, database: str, schema: str) -> bool:
        return database in self.databases or schema_key(database, schema) in self.schemas

    def object_unreadable(self, fqn: str) -> bool:
        """For a table or view FQN (database.schema.name)."""
        try:
            database, schema, _ = split_qualified_name(fqn, 3)
        except ValueError:
            return False
        return self.schema_unreadable(database, schema)

    def stage_unreadable(self, stage_fqn: str) -> bool:
        try:
            database, schema, _ = split_qualified_name(stage_fqn, 3)
        except ValueError:
            return False
        return stage_fqn in self.stages or self.schema_unreadable(database, schema)

    def file_unreadable(self, file_fqn: str) -> bool:
        # Stage names may contain "/", so a file id is matched against stages, never split at one.
        if any(in_stage(file_fqn, stage) for stage in self.stages):
            return True
        try:
            (database, schema), _ = leading_parts(file_fqn, 2)
        except ValueError:
            return False
        return self.schema_unreadable(database, schema)


def in_stage(file_fqn: str, stage_fqn: str) -> bool:
    return file_fqn.startswith(f"{stage_fqn}/")


@dataclass
class Snapshot:
    """What this sync listed, after the filters."""
    databases: set[str] = field(default_factory=set)
    schemas: set[str] = field(default_factory=set)
    # Tables whose columns, keys and foreign keys were all read.
    tables: dict[str, SnowflakeTableState] = field(default_factory=dict)
    # Listed, but some of that could not be read.
    unread_tables: set[str] = field(default_factory=set)
    # Their CREATED: a stored table renamed into one of them is not gone.
    unread_identities: set[str] = field(default_factory=set)
    views: dict[str, str] = field(default_factory=dict)
    stages: set[str] = field(default_factory=set)
    files: dict[str, str] = field(default_factory=dict)


def take_snapshot(hierarchy: SnowflakeHierarchy, filters: Filters, *, row_limit: int) -> Snapshot:
    snapshot = Snapshot(databases={db.name for db in hierarchy.databases})
    created = {
        table.fqn: table.created_at
        for tables in hierarchy.tables.values() for table in tables if table.created_at
    }
    for db in hierarchy.databases:
        for schema in hierarchy.schemas.get(db.name, []):
            snapshot.schemas.add(schema_key(db.name, schema.name))
    for tables in hierarchy.tables.values():
        for table in tables:
            if not _passes(filters.tables, table.fqn):
                continue
            if table.fully_read:
                snapshot.tables[table.fqn] = SnowflakeTableState.of(
                    table, row_limit=row_limit, identity_of=created.get,
                )
            else:
                snapshot.unread_tables.add(table.fqn)
                if table.created_at:
                    snapshot.unread_identities.add(table.created_at)
    for views in hierarchy.views.values():
        for view in views:
            if _passes(filters.views, view.fqn):
                snapshot.views[view.fqn] = view.last_altered or ""
    for stages in hierarchy.stages.values():
        for stage in stages:
            if not _passes(filters.stages, stage.fqn):
                continue
            snapshot.stages.add(stage.fqn)
            for f in hierarchy.files.get(stage.fqn, []):
                fid = file_id(stage.fqn, f.relative_path)
                if not f.is_folder and _passes(filters.files, fid):
                    snapshot.files[fid] = f.md5 or f.last_modified or ""
    return snapshot


def stored_tables(stored: dict[str, DatabaseState]) -> dict[str, SnowflakeTableState]:
    return {fqn: state for db in stored.values() for fqn, state in db.tables.items()}


def unreadable_tables(stored: dict[str, SnowflakeTableState], snapshot: Snapshot, scope: Scope) -> set[str]:
    """Stored tables this sync could not see clearly: neither gone nor changed."""
    return {
        fqn for fqn, state in stored.items()
        if fqn in snapshot.unread_tables
        or (state.created and state.created in snapshot.unread_identities)
        or scope.schema_unreadable(state.database, state.schema_name)
    }


@dataclass
class Gone:
    """Stored objects this sync no longer lists, under scopes it could list."""
    views: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
    schemas: list[str] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)


def gone_since(stored: dict[str, DatabaseState], snapshot: Snapshot, scope: Scope) -> Gone:
    gone = Gone()
    for database, state in stored.items():
        if database not in snapshot.databases:
            gone.databases.append(database)
        gone.views += [v for v in state.views if v not in snapshot.views and not scope.object_unreadable(v)]
        gone.files += [f for f in state.files if f not in snapshot.files and not scope.file_unreadable(f)]
        gone.stages += [s for s in state.stages if s not in snapshot.stages and not scope.stage_unreadable(s)]
        gone.schemas += [
            key for key in state.schemas
            if key not in snapshot.schemas and database not in scope.databases
        ]
    for name in ("views", "files", "stages", "schemas", "databases"):
        setattr(gone, name, sorted(getattr(gone, name)))
    return gone


def next_state(
    snapshot: Snapshot,
    stored: dict[str, DatabaseState],
    *,
    tables: dict[str, SnowflakeTableState],
    kept_views: set[str],
    kept_files: set[str],
    kept_stages: set[str],
    kept_schemas: set[str],
    kept_databases: set[str] = frozenset(),
) -> dict[str, DatabaseState]:
    """The state to save, by database.

    ``tables`` is every table state to keep (written ones as read now, the
    rest as stored). The ``kept_*`` sets are stored objects that were not
    listed but must stay: under an unreadable scope, or their removal failed.
    A database whose group could not be removed is kept, so it is tried again.
    """
    stored_views = {v: t for db in stored.values() for v, t in db.views.items()}
    stored_files = {f: t for db in stored.values() for f, t in db.files.items()}
    states: dict[str, DatabaseState] = {}

    def slice_of(database: str) -> DatabaseState:
        return states.setdefault(database, DatabaseState())

    for fqn, state in tables.items():
        slice_of(state.database).tables[fqn] = state
    for fqn, last_altered in snapshot.views.items():
        slice_of(database_of(fqn)).views[fqn] = last_altered
    for fqn in kept_views:
        slice_of(database_of(fqn)).views.setdefault(fqn, stored_views.get(fqn, ""))
    for fid, tag in snapshot.files.items():
        slice_of(database_of(fid)).files[fid] = tag
    for fid in kept_files:
        slice_of(database_of(fid)).files.setdefault(fid, stored_files.get(fid, ""))
    for stage in snapshot.stages | kept_stages:
        slice_of(database_of(stage)).stages.append(stage)
    for key in snapshot.schemas | kept_schemas:
        slice_of(database_of(key)).schemas.append(key)
    for database in snapshot.databases | kept_databases:
        slice_of(database)
    for state in states.values():
        state.stages.sort()
        state.schemas.sort()
    return states


def database_of(fqn: str) -> str:
    """The database part of a schema key, table, view, stage or file id."""
    return leading_parts(fqn, 1)[0][0]
