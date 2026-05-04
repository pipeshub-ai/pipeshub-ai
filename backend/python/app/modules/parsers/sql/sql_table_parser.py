"""
SQL Table Parser

Parses JSON stream of SQL Table data (Schema + Rows) into BlocksContainer.
This parser is generic and can be used for any SQL database connector.
"""
import json
from typing import Any, BinaryIO, Dict, List, Optional
import hashlib
from pydantic import BaseModel, ConfigDict

from app.models.blocks import (
    Block,
    BlockContainerIndex,
    BlockGroup,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupSubType,
    GroupType,
    TableMetadata,
)
from app.utils.indexing_helpers import generate_simple_row_text
from app.utils.logger import create_logger

logger = create_logger("sql_table_parser")


# ---------------------------------------------------------------------------
# Pydantic models for the JSON input received from connectors.
# Both PostgreSQL and MariaDB ColumnInfo / ForeignKeyInfo share these fields
# after .model_dump().  extra='allow' keeps any DB-specific extras intact.
# ---------------------------------------------------------------------------

class SQLColumnInfo(BaseModel):
    """Column metadata compatible with both PostgreSQL and MariaDB ColumnInfo."""
    model_config = ConfigDict(extra="allow")
    name: str = "unknown"
    data_type: str = "VARCHAR"
    character_maximum_length: Optional[int] = None
    numeric_precision: Optional[int] = None
    numeric_scale: Optional[int] = None
    nullable: bool = True
    default: Optional[str] = None
    is_unique: bool = False


class SQLForeignKeyInfo(BaseModel):
    """Foreign key metadata handling both PostgreSQL and MariaDB field names."""
    model_config = ConfigDict(extra="allow")
    constraint_name: str = "fk"
    column_name: str = ""
    # PostgreSQL
    foreign_table_schema: Optional[str] = None
    # MariaDB
    foreign_database: Optional[str] = None
    foreign_table_name: str = ""
    foreign_column_name: str = ""

    @property
    def reference_namespace(self) -> str:
        return self.foreign_table_schema or self.foreign_database or ""

    @property
    def reference_target(self) -> str:
        ns = self.reference_namespace
        return f"{ns}.{self.foreign_table_name}" if ns else self.foreign_table_name


class SQLTableInput(BaseModel):
    """Top-level input data for SQL table parsing, as received from connectors."""
    model_config = ConfigDict(extra="allow")
    table_name: str = "unknown_table"
    database_name: str = "unknown_db"
    schema_name: Optional[str] = None
    columns: List[SQLColumnInfo] = []
    rows: List[Any] = []
    foreign_keys: List[SQLForeignKeyInfo] = []
    primary_keys: List[str] = []
    ddl: Optional[str] = None
    connector_name: str = ""


class SQLTableParser:
    """Parser for SQL tables from JSON stream to BlocksContainer."""

    def __init__(self) -> None:
        pass

    def parse_stream(self, file_stream: BinaryIO) -> BlocksContainer:
        """
        Parse table data from a JSON stream.
        Expected JSON format matches SQLTableInput fields.
        """
        try:
            raw = json.load(file_stream)
            data = SQLTableInput.model_validate(raw)
        except Exception as e:
            logger.error(f"Failed to parse JSON stream: {e}")
            return BlocksContainer(blocks=[], block_groups=[])

        if not data.columns:
            logger.warning("No columns provided for table %s", data.table_name)
            return BlocksContainer(blocks=[], block_groups=[])

        column_names = [col.name for col in data.columns]

        ddl = data.ddl or self._generate_ddl(
            data.table_name, data.columns, data.foreign_keys, data.primary_keys
        )
        schema_row = self._build_schema_row(data.columns, data.primary_keys)

        # Normalise rows to list-of-dicts
        row_dicts: List[Dict[str, Any]] = []
        if data.rows:
            first = data.rows[0]
            if isinstance(first, dict):
                row_dicts = data.rows
            elif isinstance(first, list):
                for row in data.rows:
                    row_dicts.append(
                        {column_names[i]: row[i] if i < len(row) else None for i in range(len(column_names))}
                    )

        blocks: List[Block] = []
        children: List[BlockContainerIndex] = []

        for idx, row_dict in enumerate(row_dicts):
            row_text = generate_simple_row_text(row_dict)
            content_hash = self._calculate_content_hash(row_text)
            blocks.append(
                Block(
                    index=idx,
                    type=BlockType.TABLE_ROW,
                    format=DataFormat.JSON,
                    content_hash=content_hash,
                    data={
                        "row_natural_language_text": row_text,
                        "row": json.dumps(row_dict),
                    },
                    parent_index=0,
                )
            )
            children.append(BlockContainerIndex(block_index=idx))

        fqn = self._build_fqn(data.database_name, data.schema_name, data.table_name)
        connector_name = data.connector_name.strip()

        table_summary = self._generate_detailed_table_summary(
            fqn=fqn,
            columns=data.columns,
            row_count=len(data.rows),
            primary_keys=data.primary_keys,
            foreign_keys=data.foreign_keys,
            connector_name=connector_name,
        )

        # exclude_none keeps dumps identical to the raw connector dicts,
        # avoiding spurious hash changes from Pydantic-added None defaults.
        fk_dicts = [fk.model_dump(exclude_none=True) for fk in data.foreign_keys]

        # Intentional schema-only hash: excludes table_summary (which includes
        # row count) so reconciliation only detects actual schema changes.
        schema_hash_content = json.dumps({
            "ddl": ddl,
            "schema_row": schema_row,
            "fqn": fqn,
            "column_headers": column_names,
            "primary_keys": data.primary_keys,
            "foreign_keys": fk_dicts,
        }, sort_keys=True)
        schema_content_hash = self._calculate_content_hash(schema_hash_content)

        block_group = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            sub_type=GroupSubType.SQL_TABLE,
            name=data.table_name,
            format=DataFormat.JSON,
            content_hash=schema_content_hash,
            table_metadata=TableMetadata(
                num_of_rows=len(data.rows),
                num_of_cols=len(data.columns),
                has_header=True,
                column_names=column_names,
            ),
            data={
                "table_summary": table_summary,
                "column_headers": column_names,
                "schema_row": schema_row,
                "ddl": ddl,
                "foreign_keys": fk_dicts,
                "primary_keys": data.primary_keys,
                "fqn": fqn,
            },
            children=children,
        )

        return BlocksContainer(blocks=blocks, block_groups=[block_group])

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_fqn(database_name: str, schema_name: Optional[str], table_name: str) -> str:
        if schema_name:
            return f"{database_name}.{schema_name}.{table_name}"
        return f"{database_name}.{table_name}"

    @staticmethod
    def _calculate_content_hash(content: str) -> str:
        sha256_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        md5_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        return f"{sha256_hash}:{md5_hash}"

    @staticmethod
    def _build_full_type(col: SQLColumnInfo) -> str:
        """Build full data type string including length/precision/scale."""
        dtype = col.data_type

        if col.character_maximum_length is not None:
            char_types = ("character varying", "varchar", "character", "char", "text")
            if dtype.lower() in char_types or any(t in dtype.lower() for t in char_types):
                return f"{dtype}({int(col.character_maximum_length)})"

        if col.numeric_precision is not None:
            numeric_types = ("numeric", "decimal", "number")
            if dtype.lower() in numeric_types or any(t in dtype.lower() for t in numeric_types):
                if col.numeric_scale is not None and col.numeric_scale > 0:
                    return f"{dtype}({int(col.numeric_precision)},{int(col.numeric_scale)})"
                return f"{dtype}({int(col.numeric_precision)})"

        return dtype

    def _generate_ddl(
        self,
        table_name: str,
        columns: List[SQLColumnInfo],
        foreign_keys: List[SQLForeignKeyInfo],
        primary_keys: List[str],
        unique_columns: Optional[List[str]] = None,
    ) -> str:
        """Generate CREATE TABLE DDL statement with full constraint information."""
        unique_columns = unique_columns or []
        lines = [f"CREATE TABLE {table_name} ("]

        col_defs: List[str] = []
        for col in columns:
            full_type = self._build_full_type(col)

            col_def = f"  {col.name} {full_type}"
            if not col.nullable:
                col_def += " NOT NULL"
            if col.default is not None:
                col_def += f" DEFAULT {col.default}"
            if (col.is_unique or col.name in unique_columns) and col.name not in primary_keys:
                col_def += " UNIQUE"
            col_defs.append(col_def)

        for fk in foreign_keys:
            fk_def = (
                f"  CONSTRAINT {fk.constraint_name} "
                f"FOREIGN KEY ({fk.column_name}) "
                f"REFERENCES {fk.reference_target}({fk.foreign_column_name})"
            )
            col_defs.append(fk_def)

        if primary_keys:
            pk_cols = ", ".join(primary_keys)
            col_defs.append(f"  PRIMARY KEY ({pk_cols})")

        lines.append(",\n".join(col_defs))
        lines.append(");")

        return "\n".join(lines)

    def _build_schema_row(self, columns: List[SQLColumnInfo], primary_keys: List[str]) -> Dict[str, str]:
        """Build schema row with column names mapped to their full data types and constraints.

        Format: "full_type [NOT NULL] [DEFAULT value] [PRIMARY KEY] [UNIQUE]"
        """
        schema: Dict[str, str] = {}

        for col in columns:
            full_type = self._build_full_type(col)

            constraints: List[str] = []
            if not col.nullable:
                constraints.append("NOT NULL")
            if col.default is not None:
                default_str = str(col.default)
                if len(default_str) > 50:
                    default_str = default_str[:47] + "..."
                constraints.append(f"DEFAULT {default_str}")
            if col.name in primary_keys:
                constraints.append("PRIMARY KEY")
            if col.is_unique and col.name not in primary_keys:
                constraints.append("UNIQUE")

            constraint_str = " ".join(constraints) if constraints else ""
            schema[col.name] = f"{full_type} {constraint_str}".strip()

        return schema

    def _generate_detailed_table_summary(
        self,
        fqn: str,
        columns: List[SQLColumnInfo],
        row_count: int,
        primary_keys: List[str],
        foreign_keys: List[SQLForeignKeyInfo],
        connector_name: str = "",
    ) -> str:
        """Generate a detailed table summary including column descriptions."""
        table_type = f"{connector_name} SQL table" if connector_name else "SQL table"
        summary_parts: List[str] = []
        summary_parts.append(f"{table_type} {fqn} with {len(columns)} columns and {row_count} rows.")
        summary_parts.append("")
        summary_parts.append("Columns:")

        for col in columns:
            full_type = self._build_full_type(col)

            constraints: List[str] = []
            if col.name in primary_keys:
                constraints.append("PRIMARY KEY")
            if not col.nullable:
                constraints.append("NOT NULL")
            if col.is_unique and col.name not in primary_keys:
                constraints.append("UNIQUE")
            if col.default is not None:
                default_str = str(col.default)
                if len(default_str) > 30:
                    default_str = default_str[:27] + "..."
                constraints.append(f"DEFAULT {default_str}")

            for fk in foreign_keys:
                if fk.column_name == col.name:
                    constraints.append(f"FK->{fk.reference_target}")

            constraint_str = f" [{', '.join(constraints)}]" if constraints else ""
            summary_parts.append(f"  - {col.name}: {full_type}{constraint_str}")

        if foreign_keys:
            summary_parts.append("")
            summary_parts.append("Foreign Key Relationships:")
            for fk in foreign_keys:
                summary_parts.append(
                    f"  - {fk.column_name} references {fk.reference_target}({fk.foreign_column_name})"
                )

        return "\n".join(summary_parts)
