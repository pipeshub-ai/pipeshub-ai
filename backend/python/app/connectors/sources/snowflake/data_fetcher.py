"""Read a Snowflake account's catalog into plain dataclasses.

Every read is one SQL statement through ``SnowflakeStatementRunner``, which
polls slow statements and reads every result partition. Names taken from the
catalog are sent quoted (they are stored case-exact) and schema names are
compared as literals.

Reads raise ``SnowflakeFetchError`` when Snowflake refuses them: an empty list
on failure would read as an empty schema, and a sync that removes what a
listing no longer shows would then remove a schema's tables.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.connectors.core.sql.key_flags import (
    KeyDefinition,
    key_flags,
    primary_key_columns,
)
from app.connectors.core.sql.qualified_names import join_qualified_name
from app.sources.external.snowflake.identifiers import (
    qualified_identifier,
    quote_identifier,
    quote_literal,
    stage_reference,
)
from app.sources.external.snowflake.profiling import top_value_pairs
from app.sources.external.snowflake.statements import (
    SnowflakeFetchError,
    SnowflakeStatementRunner,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ForeignKey",
    "SnowflakeDataFetcher",
    "SnowflakeDatabase",
    "SnowflakeFetchError",
    "SnowflakeFile",
    "SnowflakeHierarchy",
    "SnowflakeSchema",
    "SnowflakeStage",
    "SnowflakeTable",
    "SnowflakeView",
]

# Snowflake's own shared database (ACCOUNT_USAGE and friends), databases owned
# by native apps, and each user's private worksheet database hold nothing a
# user asks business questions about.
_SKIPPED_DATABASES = frozenset({"SNOWFLAKE"})
_SKIPPED_DATABASE_KINDS = frozenset({"APPLICATION", "APPLICATION PACKAGE", "PERSONAL DATABASE"})
# Column added to a row sample to order it by, and removed again.
_ROW_HASH = "_pipeshub_row_hash"
_VALUE_COUNTS_TIMEOUT_S = 60
# Snowflake's error code for DIRECTORY() on a stage created without a directory table.
_DIRECTORY_NOT_ENABLED = "393901"
# Tables with rows and columns to read. Views come from INFORMATION_SCHEMA.VIEWS;
# temporary tables die with their session; event tables hold telemetry.
_TABLE_TYPES = "('BASE TABLE', 'EXTERNAL TABLE')"


@dataclass
class SnowflakeDatabase:
    name: str
    owner: str | None = None
    comment: str | None = None
    created_at: str | None = None


@dataclass
class SnowflakeSchema:
    name: str
    database_name: str
    owner: str | None = None
    comment: str | None = None
    created_at: str | None = None


@dataclass
class ForeignKey:
    constraint_name: str
    source_database: str
    source_schema: str
    source_table: str
    source_column: str
    target_database: str
    target_schema: str
    target_table: str
    target_column: str

    @property
    def target_fqn(self) -> str:
        return join_qualified_name((self.target_database, self.target_schema, self.target_table))


class SnowflakeColumnInfo(BaseModel):
    """A column as INFORMATION_SCHEMA describes it, with its key flags.

    ``ordinal_position`` is the catalog's, counted from 1.
    """
    name: str
    ordinal_position: int | None = None
    data_type: str | None = None
    character_maximum_length: int | None = None
    numeric_precision: int | None = None
    numeric_scale: int | None = None
    nullable: bool = True
    default: str | None = None
    comment: str | None = None
    is_primary_key: bool = False
    is_unique: bool = False
    is_part_of_composite_primary_key: bool = False
    is_part_of_composite_unique: bool = False


@dataclass
class SnowflakeTable:
    name: str
    database_name: str
    schema_name: str
    row_count: int | None = None
    bytes: int | None = None
    owner: str | None = None
    comment: str | None = None
    table_type: str | None = None
    created_at: str | None = None
    last_altered: str | None = None
    last_ddl: str | None = None
    columns: list[SnowflakeColumnInfo] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    primary_keys: list[str] = field(default_factory=list)
    # Whether the columns and the keys were read. A table missing either keeps
    # its stored column records untouched.
    columns_read: bool = False
    keys_read: bool = False
    foreign_keys_read: bool = False

    @property
    def fully_read(self) -> bool:
        """Columns, keys and foreign keys were all read: the table can be written and diffed."""
        return self.columns_read and self.keys_read and self.foreign_keys_read

    @property
    def fqn(self) -> str:
        return join_qualified_name((self.database_name, self.schema_name, self.name))


@dataclass
class SnowflakeView:
    name: str
    database_name: str
    schema_name: str
    owner: str | None = None
    comment: str | None = None
    is_secure: bool = False
    created_at: str | None = None
    last_altered: str | None = None
    definition: str | None = None
    source_tables: list[str] = field(default_factory=list)

    @property
    def fqn(self) -> str:
        return join_qualified_name((self.database_name, self.schema_name, self.name))


@dataclass
class SnowflakeStage:
    name: str
    database_name: str
    schema_name: str
    stage_type: str = "INTERNAL"
    url: str | None = None
    owner: str | None = None
    comment: str | None = None
    created_at: str | None = None

    @property
    def fqn(self) -> str:
        return join_qualified_name((self.database_name, self.schema_name, self.name))


@dataclass
class SnowflakeFile:
    relative_path: str
    stage_name: str
    database_name: str
    schema_name: str
    size: int = 0
    last_modified: str | None = None
    md5: str | None = None
    file_url: str | None = None

    @property
    def is_folder(self) -> bool:
        return self.relative_path.endswith('/')

    @property
    def file_name(self) -> str:
        return self.relative_path.rstrip('/').split('/')[-1]


@dataclass
class SnowflakeHierarchy:
    databases: list[SnowflakeDatabase] = field(default_factory=list)
    # Keyed by database name.
    schemas: dict[str, list[SnowflakeSchema]] = field(default_factory=dict)
    # Keyed by schema FQN (database.schema).
    tables: dict[str, list[SnowflakeTable]] = field(default_factory=dict)
    views: dict[str, list[SnowflakeView]] = field(default_factory=dict)
    stages: dict[str, list[SnowflakeStage]] = field(default_factory=dict)
    # Keyed by stage FQN (database.schema.stage).
    files: dict[str, list[SnowflakeFile]] = field(default_factory=dict)
    # Reads that failed; what was read is kept.
    errors: int = 0
    # What could not be listed: nothing under these may be taken for gone.
    unreadable_databases: set[str] = field(default_factory=set)
    # By schema key (database.schema) and stage FQN.
    unreadable_schemas: set[str] = field(default_factory=set)
    unreadable_stages: set[str] = field(default_factory=set)

    def summary(self) -> dict[str, int]:
        return {
            "databases": len(self.databases),
            "schemas": sum(len(s) for s in self.schemas.values()),
            "tables": sum(len(t) for t in self.tables.values()),
            "views": sum(len(v) for v in self.views.values()),
            "stages": sum(len(s) for s in self.stages.values()),
            "files": sum(len(f) for f in self.files.values()),
            "errors": self.errors,
        }


def _full_type(row: dict[str, Any]) -> str:
    """``NUMBER(38,0)`` rather than ``NUMBER``: precision and scale tell an integer
    key from a measure."""
    data_type = row.get("DATA_TYPE") or ""
    precision, scale = _int_or_none(row.get("NUMERIC_PRECISION")), _int_or_none(row.get("NUMERIC_SCALE"))
    if data_type.upper() in ("NUMBER", "DECIMAL", "NUMERIC") and precision is not None:
        return f"{data_type}({precision},{scale or 0})"
    return data_type


def apply_keys(table: SnowflakeTable, keys: list[KeyDefinition]) -> None:
    """Set the table's column key flags and ordered primary key from its key definitions."""
    flags = key_flags((c.name for c in table.columns), keys)
    for column in table.columns:
        column_flags = flags.get(column.name)
        if column_flags is not None:
            column.is_primary_key = column_flags.is_primary_key
            column.is_unique = column_flags.is_unique
            column.is_part_of_composite_primary_key = column_flags.is_part_of_composite_primary_key
            column.is_part_of_composite_unique = column_flags.is_part_of_composite_unique
    table.primary_keys = primary_key_columns(keys)
    table.keys_read = True


def schema_key(database: str, schema: str) -> str:
    return join_qualified_name((database, schema))


def _yes(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.upper() in ("YES", "TRUE", "Y"))


def _int_or_none(value: Any) -> int | None:  # noqa: ANN401 - a catalog value of any type
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class SnowflakeDataFetcher:
    def __init__(self, runner: SnowflakeStatementRunner) -> None:
        self.runner = runner

    async def fetch_all(
        self,
        include_database: Callable[[str], bool] | None = None,
        include_schema: Callable[[str], bool] | None = None,
        *,
        include_files: bool = True,
        include_relationships: bool = True,
        include_objects: bool = True,
    ) -> SnowflakeHierarchy:
        """Read the catalog: databases, schemas, and (``include_objects``) their tables,
        views and stages.

        ``include_database`` is asked about each database name and
        ``include_schema`` about each schema's ``database.schema`` key.

        A schema, stage or relationship read that fails is logged, counted in
        ``errors`` and skipped; the database listing failing raises.
        """
        hierarchy = SnowflakeHierarchy()
        databases = await self.list_databases()
        if include_database:
            databases = [d for d in databases if include_database(d.name)]
        hierarchy.databases = databases

        for db in databases:
            try:
                schemas = await self.list_schemas(db.name)
            except SnowflakeFetchError as e:
                hierarchy.errors += 1
                hierarchy.unreadable_databases.add(db.name)
                logger.error("Could not list the schemas of %s: %s", db.name, e)
                continue
            if include_schema:
                schemas = [s for s in schemas if include_schema(schema_key(db.name, s.name))]
            hierarchy.schemas[db.name] = schemas
            if not include_objects:
                continue
            for schema in schemas:
                await self._fetch_schema_objects(
                    hierarchy, db.name, schema.name,
                    include_files=include_files, include_relationships=include_relationships,
                )

        logger.info("Fetch complete: %s", hierarchy.summary())
        return hierarchy

    async def _fetch_schema_objects(
        self,
        hierarchy: SnowflakeHierarchy,
        database: str,
        schema: str,
        *,
        include_files: bool,
        include_relationships: bool,
    ) -> None:
        key = schema_key(database, schema)
        try:
            tables = await self.list_tables(database, schema)
            views = await self.list_views(database, schema)
            stages = await self.list_stages(database, schema)
        except SnowflakeFetchError as e:
            hierarchy.errors += 1
            hierarchy.unreadable_schemas.add(key)
            logger.error("Could not read the objects of %s: %s", key, e)
            return
        hierarchy.tables[key] = tables
        hierarchy.views[key] = views
        hierarchy.stages[key] = stages

        if include_files:
            for stage in stages:
                try:
                    hierarchy.files[stage.fqn] = await self.list_stage_files(database, schema, stage.name)
                except SnowflakeFetchError as e:
                    if e.code == _DIRECTORY_NOT_ENABLED:
                        # Its files are listed nowhere: none to sync, and none skipped.
                        hierarchy.files[stage.fqn] = []
                        logger.info("Stage %s has no directory table; its files are not synced", stage.fqn)
                        continue
                    # The schema's tables and views are still worth syncing.
                    hierarchy.errors += 1
                    hierarchy.unreadable_stages.add(stage.fqn)
                    logger.warning("Could not list the files of stage %s: %s", stage.fqn, e)

        if include_relationships and tables:
            hierarchy.errors += await self.attach_relationships(database, schema, tables)

    async def attach_relationships(self, database: str, schema: str, tables: list[SnowflakeTable]) -> int:
        """Fill in each table's columns, keys and foreign keys; returns the reads that failed.

        The three are read separately, so one refused read leaves the others:
        a table whose columns or keys could not be read says so in
        ``columns_read`` / ``keys_read``.
        """
        failures = 0
        key = schema_key(database, schema)
        try:
            columns = await self.columns_by_table(database, schema)
            for table in tables:
                if table.name in columns:
                    table.columns = columns[table.name]
                    table.columns_read = True
        except SnowflakeFetchError as e:
            failures += 1
            logger.error("Could not read the columns of %s: %s", key, e)
        try:
            keys = await self.key_definitions(database, schema)
            for table in tables:
                apply_keys(table, keys.get(table.name, []))
        except SnowflakeFetchError as e:
            failures += 1
            logger.error("Could not read the keys of %s: %s", key, e)
        try:
            foreign_keys = await self.foreign_keys(database, schema)
            for table in tables:
                table.foreign_keys = [fk for fk in foreign_keys if fk.source_table == table.name]
                table.foreign_keys_read = True
        except SnowflakeFetchError as e:
            failures += 1
            logger.error("Could not read the foreign keys of %s: %s", key, e)
        return failures

    async def list_databases(self) -> list[SnowflakeDatabase]:
        result = await self.runner.run("SHOW DATABASES")
        databases = []
        for row in result.as_dicts():
            name = row.get("name") or ""
            if not name or name in _SKIPPED_DATABASES:
                continue
            if (row.get("kind") or "").upper() in _SKIPPED_DATABASE_KINDS:
                continue
            databases.append(SnowflakeDatabase(
                name=name,
                owner=row.get("owner"),
                comment=row.get("comment") or None,
                created_at=row.get("created_on"),
            ))
        return databases

    async def list_schemas(self, database: str) -> list[SnowflakeSchema]:
        result = await self.runner.run(
            "SELECT SCHEMA_NAME, SCHEMA_OWNER, CREATED, COMMENT "
            f"FROM {quote_identifier(database)}.INFORMATION_SCHEMA.SCHEMATA "
            "WHERE SCHEMA_NAME <> 'INFORMATION_SCHEMA' ORDER BY SCHEMA_NAME",
            database=database,
        )
        return [
            SnowflakeSchema(
                name=row["SCHEMA_NAME"],
                database_name=database,
                owner=row.get("SCHEMA_OWNER"),
                comment=row.get("COMMENT"),
                created_at=row.get("CREATED"),
            )
            for row in result.as_dicts()
        ]

    async def list_tables(self, database: str, schema: str) -> list[SnowflakeTable]:
        result = await self.runner.run(
            "SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT, BYTES, TABLE_OWNER, COMMENT, "
            "CREATED, LAST_ALTERED, LAST_DDL "
            f"FROM {quote_identifier(database)}.INFORMATION_SCHEMA.TABLES "
            f"WHERE TABLE_SCHEMA = {quote_literal(schema)} AND TABLE_TYPE IN {_TABLE_TYPES} "
            "ORDER BY TABLE_NAME",
            database=database,
        )
        return [
            SnowflakeTable(
                name=row["TABLE_NAME"],
                database_name=database,
                schema_name=schema,
                row_count=_int_or_none(row.get("ROW_COUNT")),
                bytes=_int_or_none(row.get("BYTES")),
                owner=row.get("TABLE_OWNER"),
                comment=row.get("COMMENT"),
                table_type=row.get("TABLE_TYPE"),
                created_at=row.get("CREATED"),
                last_altered=row.get("LAST_ALTERED"),
                last_ddl=row.get("LAST_DDL"),
            )
            for row in result.as_dicts()
        ]

    async def list_views(self, database: str, schema: str, name: str | None = None) -> list[SnowflakeView]:
        only_view = f" AND TABLE_NAME = {quote_literal(name)}" if name is not None else ""
        result = await self.runner.run(
            "SELECT TABLE_NAME, VIEW_DEFINITION, IS_SECURE, TABLE_OWNER, COMMENT, CREATED, LAST_ALTERED "
            f"FROM {quote_identifier(database)}.INFORMATION_SCHEMA.VIEWS "
            f"WHERE TABLE_SCHEMA = {quote_literal(schema)}{only_view} ORDER BY TABLE_NAME",
            database=database,
        )
        return [
            SnowflakeView(
                name=row["TABLE_NAME"],
                database_name=database,
                schema_name=schema,
                owner=row.get("TABLE_OWNER"),
                comment=row.get("COMMENT"),
                is_secure=_yes(row.get("IS_SECURE")),
                created_at=row.get("CREATED"),
                last_altered=row.get("LAST_ALTERED"),
                # Null for a secure view the role does not own.
                definition=row.get("VIEW_DEFINITION") or None,
            )
            for row in result.as_dicts()
        ]

    async def list_stages(self, database: str, schema: str) -> list[SnowflakeStage]:
        result = await self.runner.run(
            "SELECT STAGE_NAME, STAGE_TYPE, STAGE_URL, STAGE_OWNER, COMMENT, CREATED "
            f"FROM {quote_identifier(database)}.INFORMATION_SCHEMA.STAGES "
            f"WHERE STAGE_SCHEMA = {quote_literal(schema)} ORDER BY STAGE_NAME",
            database=database,
        )
        return [
            SnowflakeStage(
                name=row["STAGE_NAME"],
                database_name=database,
                schema_name=schema,
                stage_type="EXTERNAL" if "EXTERNAL" in (row.get("STAGE_TYPE") or "").upper() else "INTERNAL",
                url=row.get("STAGE_URL") or None,
                owner=row.get("STAGE_OWNER"),
                comment=row.get("COMMENT"),
                created_at=row.get("CREATED"),
            )
            for row in result.as_dicts()
        ]

    async def list_stage_files(self, database: str, schema: str, stage: str) -> list[SnowflakeFile]:
        result = await self.runner.run(
            "SELECT RELATIVE_PATH, SIZE, LAST_MODIFIED, MD5, FILE_URL "
            f"FROM DIRECTORY({stage_reference(database, schema, stage)})",
            database=database,
        )
        return [
            SnowflakeFile(
                relative_path=row["RELATIVE_PATH"],
                stage_name=stage,
                database_name=database,
                schema_name=schema,
                size=_int_or_none(row.get("SIZE")) or 0,
                last_modified=row.get("LAST_MODIFIED"),
                md5=row.get("MD5"),
                file_url=row.get("FILE_URL"),
            )
            for row in result.as_dicts()
            if row.get("RELATIVE_PATH")
        ]

    async def object_names(self, database: str, kind: str) -> list[str]:
        """FQNs of every table, view or stage in a database, in one query.

        For filter dropdowns, which would otherwise read each schema in turn.
        """
        sources = {
            "tables": ("TABLE_SCHEMA", "TABLE_NAME", "TABLES", f" AND TABLE_TYPE IN {_TABLE_TYPES}"),
            "views": ("TABLE_SCHEMA", "TABLE_NAME", "VIEWS", ""),
            "stages": ("STAGE_SCHEMA", "STAGE_NAME", "STAGES", ""),
        }
        schema_column, name_column, view, condition = sources[kind]
        result = await self.runner.run(
            f"SELECT {schema_column}, {name_column} "
            f"FROM {quote_identifier(database)}.INFORMATION_SCHEMA.{view} "
            f"WHERE {schema_column} <> 'INFORMATION_SCHEMA'{condition} "
            f"ORDER BY {schema_column}, {name_column}",
            database=database,
        )
        return [
            join_qualified_name((database, row[schema_column], row[name_column]))
            for row in result.as_dicts()
        ]

    async def columns_by_table(
        self, database: str, schema: str, table: str | None = None,
    ) -> dict[str, list[SnowflakeColumnInfo]]:
        """Column definitions of every table and view in a schema (or of one), in ordinal order."""
        only_table = f" AND TABLE_NAME = {quote_literal(table)}" if table is not None else ""
        result = await self.runner.run(
            "SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, "
            "NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE, COLUMN_DEFAULT, COMMENT "
            f"FROM {quote_identifier(database)}.INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = {quote_literal(schema)}{only_table} ORDER BY TABLE_NAME, ORDINAL_POSITION",
            database=database,
        )
        columns: dict[str, list[SnowflakeColumnInfo]] = {}
        for row in result.as_dicts():
            columns.setdefault(row["TABLE_NAME"], []).append(SnowflakeColumnInfo(
                name=row.get("COLUMN_NAME") or "",
                ordinal_position=_int_or_none(row.get("ORDINAL_POSITION")),
                data_type=_full_type(row),
                character_maximum_length=_int_or_none(row.get("CHARACTER_MAXIMUM_LENGTH")),
                numeric_precision=_int_or_none(row.get("NUMERIC_PRECISION")),
                numeric_scale=_int_or_none(row.get("NUMERIC_SCALE")),
                nullable=_yes(row.get("IS_NULLABLE", "YES")),
                default=row.get("COLUMN_DEFAULT"),
                comment=row.get("COMMENT"),
            ))
        return columns

    async def key_definitions(
        self, database: str, schema: str, table: str | None = None,
    ) -> dict[str, list[KeyDefinition]]:
        """Primary and unique keys per table of a schema (or of one table), columns in key order.

        Snowflake records but does not enforce these constraints. Each SHOW
        lists one row per key column; rows are grouped by constraint.
        """
        keys: dict[str, list[KeyDefinition]] = {}
        for kind, command in (("primary", "SHOW PRIMARY KEYS"), ("unique", "SHOW UNIQUE KEYS")):
            result = await self.runner.run(
                f"{command} IN {self._show_scope(database, schema, table)}", database=database,
            )
            grouped: dict[tuple[str, str], list[tuple[int, str]]] = {}
            for row in result.as_dicts():
                table_name, column = row.get("table_name"), row.get("column_name")
                sequence = _int_or_none(row.get("key_sequence"))
                if not table_name or not column:
                    continue
                if sequence is None:
                    raise SnowflakeFetchError(f"{command} gave a key column without its position")
                grouped.setdefault((table_name, row.get("constraint_name") or ""), []).append((sequence, column))
            for (table_name, _), members in sorted(grouped.items()):
                keys.setdefault(table_name, []).append(
                    KeyDefinition(kind=kind, columns=tuple(column for _, column in sorted(members)))
                )
        return keys

    async def foreign_keys(
        self, database: str, schema: str, table: str | None = None,
    ) -> list[ForeignKey]:
        """Foreign keys declared on a schema's tables, or one table's (informational in Snowflake)."""
        result = await self.runner.run(
            f"SHOW IMPORTED KEYS IN {self._show_scope(database, schema, table)}",
            database=database,
        )
        keys = []
        for row in result.as_dicts():
            fk_table, pk_table = row.get("fk_table_name"), row.get("pk_table_name")
            if not fk_table or not pk_table:
                continue
            keys.append(ForeignKey(
                constraint_name=row.get("fk_name") or "",
                source_database=row.get("fk_database_name") or database,
                source_schema=row.get("fk_schema_name") or schema,
                source_table=fk_table,
                source_column=row.get("fk_column_name") or "",
                target_database=row.get("pk_database_name") or database,
                target_schema=row.get("pk_schema_name") or schema,
                target_table=pk_table,
                target_column=row.get("pk_column_name") or "",
            ))
        return keys

    @staticmethod
    def _show_scope(database: str, schema: str, table: str | None) -> str:
        if table is None:
            return f"SCHEMA {qualified_identifier(database, schema)}"
        return f"TABLE {qualified_identifier(database, schema, table)}"

    async def get_view(self, database: str, schema: str, name: str) -> SnowflakeView | None:
        views = await self.list_views(database, schema, name=name)
        return views[0] if views else None

    async def presigned_url(
        self, database: str, schema: str, stage: str, relative_path: str, expiration_s: int = 3600,
    ) -> str | None:
        result = await self.runner.run(
            f"SELECT GET_PRESIGNED_URL({stage_reference(database, schema, stage)}, "
            f"{quote_literal(relative_path)}, {int(expiration_s)}) AS URL",
            database=database,
        )
        rows = result.as_dicts()
        url = rows[0].get("URL") if rows else None
        return url if isinstance(url, str) and url else None

    async def column_value_counts(
        self, database: str, schema: str, table: str, column: str, *, limit: int, approximate: bool,
    ) -> list[dict[str, Any]]:
        """A column's ``limit`` most common values as ``{"value", "count"}``.

        Exact counts group the whole column, NULL included, values decoded by
        type and as text; ``approximate`` uses APPROX_TOP_K, for tables too
        large to group.
        """
        reference = qualified_identifier(database, schema, table)
        name = quote_identifier(column)
        if approximate:
            result = await self.runner.run(
                f'SELECT APPROX_TOP_K({name}, {int(limit)}, {int(limit) * 10}) AS "top" FROM {reference}',
                database=database, timeout_s=_VALUE_COUNTS_TIMEOUT_S,
            )
            rows = result.as_dicts()
            # NULL, if APPROX_TOP_K counts it at all, is left out (the exact
            # path keeps it); values come as JSON, not decoded by column type.
            raw = rows[0].get("top") if rows else None
            return [{"value": value, "count": count} for value, count in top_value_pairs(raw)]
        result = await self.runner.run(
            f'SELECT {name} AS "value", COUNT(*) AS "count" FROM {reference} '
            f"GROUP BY 1 ORDER BY 2 DESC, 1 NULLS LAST LIMIT {int(limit)}",
            database=database, timeout_s=_VALUE_COUNTS_TIMEOUT_S,
        )
        return [
            {"value": None if row["value"] is None else str(row["value"]), "count": row["count"]}
            for row in result.as_dicts()
        ]

    async def sample_rows(
        self,
        database: str,
        schema: str,
        table: str,
        *,
        limit: int,
        order_by: list[str] | None,
        max_bytes: int,
    ) -> tuple[list[str], list[list[Any]]]:
        """Up to ``limit`` rows and their column names, at most ``max_bytes`` of them as JSON.

        ``order_by`` columns, or ``[]`` to order by a hash of each row, make the
        sample the same on every read of unchanged data; ``None`` reads rows in
        whatever order Snowflake returns them, which needs no sort.
        """
        reference = qualified_identifier(database, schema, table)
        if order_by is None:
            statement = f"SELECT * FROM {reference} LIMIT {int(limit)}"
        elif order_by:
            ordering = ", ".join(quote_identifier(c) for c in order_by)
            statement = f"SELECT * FROM {reference} ORDER BY {ordering} LIMIT {int(limit)}"
        else:
            statement = (
                f'SELECT *, HASH(*) AS "{_ROW_HASH}" FROM {reference} '
                f'ORDER BY "{_ROW_HASH}" LIMIT {int(limit)}'
            )
        result = await self.runner.run(statement, database=database, max_rows=limit, max_bytes=max_bytes)
        names = result.column_names
        rows = result.rows
        if order_by == []:
            names, rows = names[:-1], [row[:-1] for row in rows]
        kept: list[list[Any]] = []
        size = 0
        for row in rows:
            size += len(json.dumps(row, default=str))
            if size > max_bytes:
                break
            kept.append(row)
        return names, kept

    async def get_ddl(self, kind: str, database: str, schema: str, name: str) -> str | None:
        """``GET_DDL`` for a table or view; None when Snowflake returns no text."""
        object_name = quote_literal(qualified_identifier(database, schema, name))
        result = await self.runner.run(
            f"SELECT GET_DDL({quote_literal(kind)}, {object_name}) AS DDL",
            database=database,
        )
        rows = result.as_dicts()
        return (rows[0].get("DDL") or None) if rows else None

    async def get_table_ddl(self, database: str, schema: str, table: str) -> str | None:
        return await self.get_ddl("TABLE", database, schema, table)
