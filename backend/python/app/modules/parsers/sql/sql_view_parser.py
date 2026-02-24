"""
SQL View Parser

Parses JSON stream of SQL View definition into BlocksContainer.
This parser is generic and can be used for any SQL database connector.
"""
import json
from typing import Any, Dict, List, Optional, BinaryIO

from app.models.blocks import (
    BlockGroup,
    BlocksContainer,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.utils.logger import create_logger

logger = create_logger("sql_view_parser")


class SQLViewParser:
    """Parser for SQL Views from JSON stream to BlocksContainer."""

    def __init__(self) -> None:
        pass

    def parse_stream(self, file_stream: BinaryIO) -> BlocksContainer:
        """
        Parse view data from a JSON stream.
        Expected JSON format:
        {
            "view_name": str,
            "database_name": str,
            "schema_name": str,
            "definition": str,
            "source_tables": List[str],
            ...
        }
        """
        try:
            data = json.load(file_stream)
        except Exception as e:
            logger.error(f"Failed to parse JSON stream: {e}")
            return BlocksContainer(blocks=[], block_groups=[])

        view_name = data.get("view_name", "unknown_view")
        database_name = data.get("database_name", "unknown_db")
        schema_name = data.get("schema_name", "unknown_schema")
        definition = data.get("definition", "")
        source_tables = data.get("source_tables", [])
        source_table_ddls = data.get("source_table_ddls", {})
        source_tables_summary = data.get("source_tables_summary", "")
        is_secure = data.get("is_secure", False)
        comment = data.get("comment")

        fqn = f"{database_name}.{schema_name}.{view_name}"
        
        # Create the main BlockGroup for the View (contains all needed data)
        block_group = BlockGroup(
            index=0,
            type=GroupType.VIEW,
            sub_type=GroupSubType.SQL_VIEW,
            name=view_name,
            format=DataFormat.JSON,
            data={
                "fqn": fqn,
                "database": database_name,
                "schema": schema_name,
                "is_secure": is_secure,
                "comment": comment,
                "definition": definition,
                "source_tables": source_tables,
                "source_table_ddls": source_table_ddls,
                "source_tables_summary": source_tables_summary,
                "type": "VIEW"
            },
            children=[],
        )

        return BlocksContainer(blocks=[], block_groups=[block_group])


