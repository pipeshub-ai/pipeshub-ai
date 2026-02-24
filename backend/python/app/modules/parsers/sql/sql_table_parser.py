"""
SQL Table Parser

Parses JSON stream of SQL Table data (Schema + Rows) into BlocksContainer.
This parser is generic and can be used for any SQL database connector.
"""
import json
from typing import Any, Dict, List, Optional, BinaryIO
import hashlib
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


class SQLTableParser:
    """Parser for SQL tables from JSON stream to BlocksContainer."""

    def __init__(self) -> None:
        self.max_rows_for_llm = 1000

    def parse_stream(self, file_stream: BinaryIO) -> BlocksContainer:
        """
        Parse table data from a JSON stream.
        Expected JSON format:
        {
            "table_name": str,
            "database_name": str,
            "schema_name": str,
            "columns": List[Dict],
            "rows": List[List[Any]] | List[Dict],
            "foreign_keys": List[Dict],
            "primary_keys": List[str],
            ...
        }
        """
        try:
            data = json.load(file_stream)
        except Exception as e:
            logger.error(f"Failed to parse JSON stream: {e}")
            return BlocksContainer(blocks=[], block_groups=[])

        table_name = data.get("table_name", "unknown_table")
        database_name = data.get("database_name", "unknown_db")
        schema_name = data.get("schema_name", "unknown_schema")
        columns = data.get("columns", [])
        rows = data.get("rows", [])
        foreign_keys = data.get("foreign_keys", [])
        primary_keys = data.get("primary_keys", [])

        if not columns:
            logger.warning("No columns provided for table %s", table_name)
            return BlocksContainer(blocks=[], block_groups=[])

        column_names = [col.get("name", f"col_{i}") for i, col in enumerate(columns)]
        
        # Use provided DDL or generate it if missing
        ddl = data.get("ddl")
        if not ddl:
            ddl = self.generate_ddl(table_name, columns, foreign_keys, primary_keys)
            
        schema_row = self._build_schema_row(columns, primary_keys)

        blocks = []
        children = []

        # Handle rows if they are already dicts (from stream_record) or lists
        row_dicts = []
        if rows and isinstance(rows[0], dict):
            row_dicts = rows
        elif rows and isinstance(rows[0], list):
             for row in rows:
                row_dict = {column_names[i]: row[i] if i < len(row) else None for i in range(len(column_names))}
                row_dicts.append(row_dict)

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

        fqn = f"{database_name}.{schema_name}.{table_name}"
        connector_name = data.get("connector_name", "") or ""
        if hasattr(connector_name, "value"):
            connector_name = connector_name.value
        connector_name = (connector_name or "").strip()

        # Generate detailed table summary with column information
        table_summary = self._generate_detailed_table_summary(
            fqn=fqn,
            columns=columns,
            rows=rows,
            primary_keys=primary_keys,
            foreign_keys=foreign_keys,
            connector_name=connector_name,
        )

        # Generate content hash for the schema block group (used for reconciliation)
        schema_hash_content = json.dumps({
            "ddl": ddl,
            "schema_row": schema_row,
            "fqn": fqn,
            "column_headers": column_names,
            "primary_keys": primary_keys,
            "foreign_keys": foreign_keys,
        }, sort_keys=True)
        schema_content_hash = self._calculate_content_hash(schema_hash_content)

        block_group = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            sub_type=GroupSubType.SQL_TABLE,
            name=table_name,
            format=DataFormat.JSON,
            content_hash=schema_content_hash,
            table_metadata=TableMetadata(
                num_of_rows=len(rows),
                num_of_cols=len(columns),
                has_header=True,
                column_names=column_names,
            ),
            data={
                "table_summary": table_summary,
                "column_headers": column_names,
                "schema_row": schema_row,
                "ddl": ddl,
                "foreign_keys": foreign_keys,
                "primary_keys": primary_keys,
                "fqn": fqn,
            },
            children=children,
        )

        return BlocksContainer(blocks=blocks, block_groups=[block_group])

    def _calculate_content_hash(self, content: str) -> str:
        """Calculate SHA256 and MD5 hash of the content, concatenated."""
        sha256_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
        md5_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
        return f"{sha256_hash}:{md5_hash}"
        
    def generate_ddl(
        self,
        table_name: str,
        columns: List[Dict[str, Any]],
        foreign_keys: Optional[List[Dict[str, Any]]] = None,
        primary_keys: Optional[List[str]] = None,
        unique_columns: Optional[List[str]] = None,
    ) -> str:
        """Generate CREATE TABLE DDL statement with full constraint information."""
        foreign_keys = foreign_keys or []
        primary_keys = primary_keys or []
        unique_columns = unique_columns or []
        lines = [f"CREATE TABLE {table_name} ("]

        col_defs = []
        for col in columns:
            name = col.get("name", "unknown")
            dtype = col.get("data_type", "VARCHAR")
            
            # Build full type with precision/scale/length
            full_type = self._build_full_type(col)

            nullable = col.get("nullable", True)
            default = col.get("default")
            is_unique = col.get("is_unique", False) or name in unique_columns

            col_def = f"  {name} {full_type}"
            if not nullable:
                col_def += " NOT NULL"
            if default is not None:
                col_def += f" DEFAULT {default}"
            if is_unique and name not in primary_keys:
                col_def += " UNIQUE"
            col_defs.append(col_def)

        # Add FOREIGN KEY constraints
        for fk in foreign_keys:
            fk_col = fk.get('column_name') or fk.get('column', '')
            fk_ref_schema = fk.get('foreign_table_schema') or fk.get('references_schema', '')
            fk_ref_table = fk.get('foreign_table_name') or fk.get('references_table', '')
            fk_ref_col = fk.get('foreign_column_name') or fk.get('references_column', '')
            fk_def = (
                f"  CONSTRAINT {fk.get('constraint_name', 'fk')} "
                f"FOREIGN KEY ({fk_col}) "
                f"REFERENCES {fk_ref_schema}.{fk_ref_table}({fk_ref_col})"
            )
            col_defs.append(fk_def)

        if primary_keys:
            pk_cols = ", ".join(primary_keys)
            col_defs.append(f"  PRIMARY KEY ({pk_cols})")

        lines.append(",\n".join(col_defs))
        lines.append(");")

        return "\n".join(lines)
    
    def _build_full_type(self, col: Dict[str, Any]) -> str:
        """Build full data type string including length/precision/scale."""
        dtype = col.get("data_type", "VARCHAR")
        max_len = col.get("character_maximum_length")
        precision = col.get("numeric_precision")
        scale = col.get("numeric_scale")
        
        # Check for character types with length
        if max_len is not None:
            char_types = ("character varying", "varchar", "character", "char", "text")
            if dtype.lower() in char_types or any(t in dtype.lower() for t in char_types):
                return f"{dtype}({int(max_len)})"
        
        # Check for numeric types with precision/scale
        if precision is not None:
            numeric_types = ("numeric", "decimal", "number")
            if dtype.lower() in numeric_types or any(t in dtype.lower() for t in numeric_types):
                if scale is not None and scale > 0:
                    return f"{dtype}({int(precision)},{int(scale)})"
                else:
                    return f"{dtype}({int(precision)})"
        
        return dtype

    def _build_schema_row(self, columns: List[Dict[str, Any]], primary_keys: List[str] = None) -> Dict[str, str]:
        """Build schema row with column names mapped to their full data types and constraints.
        
        Format: "full_type [NOT NULL] [DEFAULT value] [PRIMARY KEY] [UNIQUE]"
        """
        schema = {}
        primary_keys = primary_keys or []
        
        for col in columns:
            name = col.get("name", "unknown")
            
            # Build full type with length/precision/scale
            full_type = self._build_full_type(col)
            
            # Build constraints string
            constraints = []
            
            # NOT NULL / NULL
            if not col.get("nullable", True):
                constraints.append("NOT NULL")
            
            # DEFAULT value
            default_val = col.get("default")
            if default_val is not None:
                # Truncate long default values
                default_str = str(default_val)
                if len(default_str) > 50:
                    default_str = default_str[:47] + "..."
                constraints.append(f"DEFAULT {default_str}")
            
            # PRIMARY KEY
            if name in primary_keys:
                constraints.append("PRIMARY KEY")
            
            # UNIQUE (only if not already a primary key)
            if col.get("is_unique", False) and name not in primary_keys:
                constraints.append("UNIQUE")
            
            constraint_str = " ".join(constraints) if constraints else ""
            schema[name] = f"{full_type} {constraint_str}".strip()
        
        return schema

    def _generate_detailed_table_summary(
        self,
        fqn: str,
        columns: List[Dict[str, Any]],
        rows: List[Any],
        primary_keys: Optional[List[str]] = None,
        foreign_keys: Optional[List[Dict[str, Any]]] = None,
        connector_name: str = "",
    ) -> str:
        """
        Generate a detailed table summary including column descriptions.
        This summary is designed to be embedded alongside DDL for better semantic search.
        """
        primary_keys = primary_keys or []
        foreign_keys = foreign_keys or []

        table_type = f"{connector_name} SQL table" if connector_name else "SQL table"
        summary_parts = []
        summary_parts.append(f"{table_type} {fqn} with {len(columns)} columns and {len(rows)} rows.")
        summary_parts.append("")
        summary_parts.append("Columns:")
        
        for col in columns:
            name = col.get("name", "unknown")
            
            # Use the helper to build full type
            full_type = self._build_full_type(col)
            
            # Build constraints description
            constraints = []
            if name in primary_keys:
                constraints.append("PRIMARY KEY")
            if not col.get("nullable", True):
                constraints.append("NOT NULL")
            if col.get("is_unique", False) and name not in primary_keys:
                constraints.append("UNIQUE")
            
            # Add DEFAULT if present (truncated for readability)
            default_val = col.get("default")
            if default_val is not None:
                default_str = str(default_val)
                if len(default_str) > 30:
                    default_str = default_str[:27] + "..."
                constraints.append(f"DEFAULT {default_str}")
            
            # Check if this column is a foreign key (handle both field name formats)
            for fk in foreign_keys:
                fk_col = fk.get("column_name") or fk.get("column", "")
                if fk_col == name:
                    ref_schema = fk.get("foreign_table_schema") or fk.get("references_schema", "")
                    ref_table = fk.get("foreign_table_name") or fk.get("references_table", "")
                    constraints.append(f"FK->{ref_schema}.{ref_table}")
            
            constraint_str = f" [{', '.join(constraints)}]" if constraints else ""
            summary_parts.append(f"  - {name}: {full_type}{constraint_str}")
        
        # Add foreign key relationships summary
        if foreign_keys:
            summary_parts.append("")
            summary_parts.append("Foreign Key Relationships:")
            for fk in foreign_keys:
                col = fk.get("column_name") or fk.get("column", "")
                ref_schema = fk.get("foreign_table_schema") or fk.get("references_schema", "")
                ref_table = fk.get("foreign_table_name") or fk.get("references_table", "")
                ref_col = fk.get("foreign_column_name") or fk.get("references_column", "")
                summary_parts.append(f"  - {col} references {ref_schema}.{ref_table}({ref_col})")
        
        return "\n".join(summary_parts)


