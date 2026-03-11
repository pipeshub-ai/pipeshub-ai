"""
Snowflake Data Fetcher

A simple service to fetch metadata from Snowflake and cache it locally.
This is used for testing and exploring Snowflake data before building the full connector.

Usage:
    from app.connectors.sources.snowflake.data_fetcher import SnowflakeDataFetcher
    
    fetcher = SnowflakeDataFetcher(data_source, warehouse="MY_WH")
    hierarchy = await fetcher.fetch_all()
    fetcher.save_to_file("snowflake_data.json")
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from app.sources.external.snowflake.snowflake_ import SnowflakeDataSource

logger = logging.getLogger(__name__)

@dataclass
class SnowflakeDatabase:
    name: str
    owner: Optional[str] = None
    comment: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class SnowflakeSchema:
    name: str
    database_name: str
    owner: Optional[str] = None
    comment: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class SnowflakeTable:
    name: str
    database_name: str
    schema_name: str
    row_count: Optional[int] = None
    bytes: Optional[int] = None
    owner: Optional[str] = None
    comment: Optional[str] = None
    table_type: Optional[str] = None
    created_at: Optional[str] = None
    last_altered: Optional[str] = None  # For incremental sync change detection
    columns: List[Dict[str, Any]] = field(default_factory=list)
    foreign_keys: List[Dict[str, Any]] = field(default_factory=list)
    primary_keys: List[str] = field(default_factory=list)
    
    @property
    def fqn(self) -> str:
        return f"{self.database_name}.{self.schema_name}.{self.name}"
    
    @property
    def column_signature(self) -> str:
        """Hash of column names and types for schema change detection."""
        import hashlib
        if not self.columns:
            return ""
        sig = "|".join(
            f"{c.get('name', '')}:{c.get('data_type', '')}" 
            for c in sorted(self.columns, key=lambda x: x.get('name', ''))
        )
        return hashlib.md5(sig.encode()).hexdigest()


@dataclass
class SnowflakeView:
    name: str
    database_name: str
    schema_name: str
    owner: Optional[str] = None
    comment: Optional[str] = None
    is_secure: bool = False
    created_at: Optional[str] = None
    definition: Optional[str] = None
    source_tables: List[str] = field(default_factory=list)
    
    @property
    def fqn(self) -> str:
        return f"{self.database_name}.{self.schema_name}.{self.name}"


@dataclass
class SnowflakeStage:
    name: str
    database_name: str
    schema_name: str
    stage_type: str = "INTERNAL"
    url: Optional[str] = None
    owner: Optional[str] = None
    comment: Optional[str] = None
    created_at: Optional[str] = None
    
    @property
    def fqn(self) -> str:
        return f"{self.database_name}.{self.schema_name}.{self.name}"


@dataclass
class SnowflakeFile:
    relative_path: str
    stage_name: str
    database_name: str
    schema_name: str
    size: int = 0
    last_modified: Optional[str] = None
    md5: Optional[str] = None
    file_url: Optional[str] = None
    
    @property
    def is_folder(self) -> bool:
        return self.relative_path.endswith('/')
    
    @property
    def file_name(self) -> str:
        path = self.relative_path.rstrip('/')
        return path.split('/')[-1] if '/' in path else path
    
    @property
    def parent_folder(self) -> Optional[str]:
        path = self.relative_path.rstrip('/')
        if '/' not in path:
            return None
        return '/'.join(path.split('/')[:-1])


@dataclass
class SnowflakeFolder:
    path: str
    stage_name: str
    database_name: str
    schema_name: str
    parent_path: Optional[str] = None
    
    @property
    def name(self) -> str:
        return self.path.split('/')[-1]


@dataclass
class ForeignKey:
    constraint_name: str
    database_name: str
    source_schema: str
    source_table: str
    source_column: str
    target_schema: str
    target_table: str
    target_column: str
    
    @property
    def source_fqn(self) -> str:
        """Fully qualified name of source table: database.schema.table"""
        return f"{self.database_name}.{self.source_schema}.{self.source_table}"
    
    @property
    def target_fqn(self) -> str:
        """Fully qualified name of target table: database.schema.table"""
        return f"{self.database_name}.{self.target_schema}.{self.target_table}"


@dataclass
class SnowflakeHierarchy:
    fetched_at: str = ""
    databases: List[SnowflakeDatabase] = field(default_factory=list)
    schemas: Dict[str, List[SnowflakeSchema]] = field(default_factory=dict)
    tables: Dict[str, List[SnowflakeTable]] = field(default_factory=dict)
    views: Dict[str, List[SnowflakeView]] = field(default_factory=dict)
    stages: Dict[str, List[SnowflakeStage]] = field(default_factory=dict)
    files: Dict[str, List[SnowflakeFile]] = field(default_factory=dict)
    folders: Dict[str, List[SnowflakeFolder]] = field(default_factory=dict)
    foreign_keys: List[ForeignKey] = field(default_factory=list)
    
    def summary(self) -> Dict[str, int]:
        return {
            "databases": len(self.databases),
            "schemas": sum(len(s) for s in self.schemas.values()),
            "tables": sum(len(t) for t in self.tables.values()),
            "views": sum(len(v) for v in self.views.values()),
            "stages": sum(len(s) for s in self.stages.values()),
            "files": sum(len(f) for f in self.files.values()),
            "folders": sum(len(f) for f in self.folders.values()),
            "foreign_keys": len(self.foreign_keys),
        }
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "fetched_at": self.fetched_at,
            "summary": self.summary(),
            "databases": [asdict(d) for d in self.databases],
            "schemas": {k: [asdict(s) for s in v] for k, v in self.schemas.items()},
            "tables": {k: [asdict(t) for t in v] for k, v in self.tables.items()},
            "views": {k: [asdict(v_) for v_ in v] for k, v in self.views.items()},
            "stages": {k: [asdict(s) for s in v] for k, v in self.stages.items()},
            "files": {k: [asdict(f) for f in v] for k, v in self.files.items()},
            "folders": {k: [asdict(f) for f in v] for k, v in self.folders.items()},
            "foreign_keys": [asdict(fk) for fk in self.foreign_keys],
        }


class SnowflakeDataFetcher:
    
    def __init__(
        self,
        data_source: SnowflakeDataSource,
        warehouse: Optional[str] = None,
    ) -> None:
        self.data_source = data_source
        self.warehouse = warehouse
        self.hierarchy = SnowflakeHierarchy()
        self._schema_cache: Dict[str, List[SnowflakeSchema]] = {}
        self._columns_cache: Dict[str, List[Dict[str, Any]]] = {}
    
    def _clear_cache(self) -> None:
        self._schema_cache.clear()
        self._columns_cache.clear()
    
    async def fetch_all(
        self,
        database_filter: Optional[List[str]] = None,
        schema_filter: Optional[List[str]] = None,
        include_files: bool = True,
        include_relationships: bool = True,
    ) -> SnowflakeHierarchy:
        self._clear_cache()
        self.hierarchy = SnowflakeHierarchy()
        self.hierarchy.fetched_at = datetime.now().isoformat()
        
        logger.info("Fetching databases")
        databases = await self._fetch_databases()
        
        if database_filter:
            databases = [d for d in databases if d.name in database_filter]
        
        self.hierarchy.databases = databases
        logger.info("Found %d databases", len(databases))
        
        for db in databases:
            logger.info("Fetching schemas in %s", db.name)
            schemas = await self._fetch_schemas(db.name)
            
            if schema_filter:
                schemas = [s for s in schemas if f"{db.name}.{s.name}" in schema_filter]
            
            self.hierarchy.schemas[db.name] = schemas
            
            for schema in schemas:
                schema_key = f"{db.name}.{schema.name}"
                logger.info("Fetching objects in %s", schema_key)
                
                tables = await self._fetch_tables(db.name, schema.name)
                self.hierarchy.tables[schema_key] = tables
                
                views = await self._fetch_views(db.name, schema.name)
                self.hierarchy.views[schema_key] = views
                
                stages = await self._fetch_stages(db.name, schema.name)
                self.hierarchy.stages[schema_key] = stages
                
                if include_files:
                    for stage in stages:
                        stage_key = f"{db.name}.{schema.name}.{stage.name}"
                        files = await self._fetch_stage_files(db.name, schema.name, stage.name)
                        self.hierarchy.files[stage_key] = files
                        folders = self._deduce_folders(files, db.name, schema.name, stage.name)
                        self.hierarchy.folders[stage_key] = folders
                
                if include_relationships and tables:
                    all_columns = await self._fetch_all_columns_in_schema(db.name, schema.name)
                    for table in tables:
                        table.columns = all_columns.get(table.name, [])
                    
                    fks = await self._fetch_foreign_keys_in_schema(db.name, schema.name)
                    self.hierarchy.foreign_keys.extend(fks)
                    
                    fk_by_table: Dict[str, List[Dict[str, Any]]] = {}
                    for fk in fks:
                        table_name = fk.source_table
                        if table_name not in fk_by_table:
                            fk_by_table[table_name] = []
                        fk_by_table[table_name].append({
                            "constraint_name": fk.constraint_name,
                            "column": fk.source_column,
                            "references_schema": fk.target_schema,
                            "references_table": fk.target_table,
                            "references_column": fk.target_column,
                        })
                    
                    for table in tables:
                        table.foreign_keys = fk_by_table.get(table.name, [])

                    pks = await self._fetch_primary_keys_in_schema(db.name, schema.name)
                    pk_by_table: Dict[str, List[str]] = {}
                    for pk in pks:
                        if pk["table"] not in pk_by_table:
                            pk_by_table[pk["table"]] = []
                        pk_by_table[pk["table"]].append(pk["column"])
                    
                    for table in tables:
                        table.primary_keys = pk_by_table.get(table.name, [])
        
        logger.info("Fetch complete: %s", self.hierarchy.summary())
        return self.hierarchy
    
    async def _fetch_databases(self) -> List[SnowflakeDatabase]:
        response = await self.data_source.list_databases()
        if not response.success:
            logger.error("Failed to fetch databases: %s", response.error)
            return []
        
        databases = []
        for item in self._extract_items(response.data):
            databases.append(SnowflakeDatabase(
                name=item.get("name", ""),
                owner=item.get("owner"),
                comment=item.get("comment"),
                created_at=item.get("created_on"),
            ))
        return databases
    
    async def _fetch_schemas(self, database: str) -> List[SnowflakeSchema]:
        if database in self._schema_cache:
            return self._schema_cache[database]
        
        response = await self.data_source.list_schemas(database=database)
        if not response.success:
            logger.error("Failed to fetch schemas: %s", response.error)
            return []
        
        schemas = []
        for item in self._extract_items(response.data):
            schemas.append(SnowflakeSchema(
                name=item.get("name", ""),
                database_name=database,
                owner=item.get("owner"),
                comment=item.get("comment"),
                created_at=item.get("created_on"),
            ))
        
        self._schema_cache[database] = schemas
        return schemas
    
    async def _fetch_tables(self, database: str, schema: str) -> List[SnowflakeTable]:
        response = await self.data_source.list_tables(database=database, schema=schema)
        if not response.success:
            logger.error("Failed to fetch tables: %s", response.error)
            return []
        
        tables = []
        for item in self._extract_items(response.data):
            tables.append(SnowflakeTable(
                name=item.get("name", ""),
                database_name=database,
                schema_name=schema,
                row_count=item.get("rows"),
                bytes=item.get("bytes"),
                owner=item.get("owner"),
                comment=item.get("comment"),
                table_type=item.get("kind") or item.get("table_type"),
                created_at=item.get("created_on"),
                # last_altered is typically available from INFORMATION_SCHEMA or SHOW TABLES
                last_altered=item.get("last_altered") or item.get("changed_on"),
            ))
        return tables
    
    async def _fetch_views(self, database: str, schema: str) -> List[SnowflakeView]:
        response = await self.data_source.list_views(database=database, schema=schema)
        if not response.success:
            logger.error("Failed to fetch views: %s", response.error)
            return []
        
        views = []
        for item in self._extract_items(response.data):
            views.append(SnowflakeView(
                name=item.get("name", ""),
                database_name=database,
                schema_name=schema,
                owner=item.get("owner"),
                comment=item.get("comment"),
                is_secure=item.get("is_secure", False),
                created_at=item.get("created_on"),
                definition=item.get("text"), # Start with text if available
            ))
        
        # Optionally fetch full definitions if not present or incomplete
        # Note: listing views usually provides 'text' which is the DDL
        # If deeper definition is needed, one could use GET_DDL
        
        return views
    
    async def _fetch_stages(self, database: str, schema: str) -> List[SnowflakeStage]:
        response = await self.data_source.list_stages(database=database, schema=schema)
        if not response.success:
            logger.error("Failed to fetch stages: %s", response.error)
            return []
        
        stages = []
        for item in self._extract_items(response.data):
            stage_type = "EXTERNAL" if "EXTERNAL" in item.get("type", "").upper() else "INTERNAL"
            stages.append(SnowflakeStage(
                name=item.get("name", ""),
                database_name=database,
                schema_name=schema,
                stage_type=stage_type,
                url=item.get("url"),
                owner=item.get("owner"),
                comment=item.get("comment"),
                created_at=item.get("created_on"),
            ))
        return stages
    
    async def _fetch_stage_files(
        self, database: str, schema: str, stage: str
    ) -> List[SnowflakeFile]:
        stage_fqn = f"{database}.{schema}.{stage}"
        if not self.warehouse:
            logger.warning("Warehouse not set, skipping stage files for %s", stage_fqn)
            return []
        
        logger.debug("Fetching files for stage: %s (warehouse: %s)", stage_fqn, self.warehouse)
        response = await self.data_source.list_stage_files(
            database=database,
            schema=schema,
            stage=stage,
            warehouse=self.warehouse,
        )
        if not response.success:
            logger.error("Failed to fetch stage files for %s: %s", stage_fqn, response.error)
            return []
        
        files = []
        data = response.data.get("data", []) if isinstance(response.data, dict) else []
        logger.debug("Stage %s: received %d file entries from Snowflake", stage_fqn, len(data))
        for item in data:
            if isinstance(item, list) and len(item) >= 1:
                files.append(SnowflakeFile(
                    relative_path=item[0],
                    stage_name=stage,
                    database_name=database,
                    schema_name=schema,
                    size=int(item[1]) if len(item) > 1 and item[1] else 0,
                    last_modified=item[2] if len(item) > 2 else None,
                    md5=item[3] if len(item) > 3 else None,
                    file_url=item[5] if len(item) > 5 else None,
                ))
        return files
    
    def _deduce_folders(
        self,
        files: List[SnowflakeFile],
        database: str,
        schema: str,
        stage: str,
    ) -> List[SnowflakeFolder]:
        folder_paths: Set[str] = set()
        
        for file in files:
            path = file.relative_path.rstrip('/')
            parts = path.split('/')[:-1]
            for i in range(len(parts)):
                folder_paths.add('/'.join(parts[:i+1]))
        
        folders = []
        for path in sorted(folder_paths):
            parent = None
            if '/' in path:
                parent = '/'.join(path.split('/')[:-1])
            folders.append(SnowflakeFolder(
                path=path,
                stage_name=stage,
                database_name=database,
                schema_name=schema,
                parent_path=parent,
            ))
        return folders
    
    async def _fetch_all_columns_in_schema(
        self, database: str, schema: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        cache_key = f"{database}.{schema}"
        if cache_key in self._columns_cache:
            return self._columns_cache[cache_key]
        
        if not self.warehouse:
            return {}
        
        sql = f"""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE, COLUMN_DEFAULT, COMMENT
        FROM {database}.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = '{schema}'
        ORDER BY TABLE_NAME, ORDINAL_POSITION
        """
        
        response = await self.data_source.execute_sql(
            statement=sql,
            database=database,
            warehouse=self.warehouse,
        )
        
        if not response.success:
            logger.warning("Failed to fetch columns: %s", response.error)
            return {}
        
        columns_by_table: Dict[str, List[Dict[str, Any]]] = {}
        for row in self._parse_sql_result(response.data):
            table_name = row.get("TABLE_NAME", "")
            if table_name not in columns_by_table:
                columns_by_table[table_name] = []
            columns_by_table[table_name].append({
                "name": row.get("COLUMN_NAME", ""),
                "data_type": row.get("DATA_TYPE", ""),
                "character_maximum_length": row.get("CHARACTER_MAXIMUM_LENGTH"),
                "numeric_precision": row.get("NUMERIC_PRECISION"),
                "numeric_scale": row.get("NUMERIC_SCALE"),
                "nullable": row.get("IS_NULLABLE", "YES") == "YES",
                "default": row.get("COLUMN_DEFAULT"),
                "comment": row.get("COMMENT"),
            })
        
        self._columns_cache[cache_key] = columns_by_table
        return columns_by_table
    
    async def get_table_ddl(self, database: str, schema: str, table: str) -> Optional[str]:
        """Fetch the DDL for a specific table using GET_DDL function."""
        if not self.warehouse:
            return None
            
        sql = f"SELECT GET_DDL('TABLE', '{database}.{schema}.{table}') as DDL"
        
        response = await self.data_source.execute_sql(
            statement=sql,
            database=database,
            warehouse=self.warehouse,
        )
        
        if response.success and response.data:
            rows = self._parse_sql_result(response.data)
            if rows:
                return rows[0].get("DDL")
        
        logger.warning(f"Failed to fetch DDL for {database}.{schema}.{table}: {response.error}")
        return None
    
    async def _fetch_foreign_keys_in_schema(
        self, database: str, schema: str
    ) -> List[ForeignKey]:
        if not self.warehouse:
            logger.warning("Warehouse not set, skipping foreign keys")
            return []
        
        sql = f"SHOW IMPORTED KEYS IN SCHEMA {database}.{schema}"
        
        response = await self.data_source.execute_sql(
            statement=sql,
            database=database,
            warehouse=self.warehouse,
        )
        
        if not response.success:
            logger.warning("Failed to fetch foreign keys: %s", response.error)
            return []
        
        foreign_keys = []
        rows = self._parse_sql_result(response.data)
        
        for row in rows:
            fk_schema = row.get("fk_schema_name") or row.get("FK_SCHEMA_NAME") or schema
            fk_table = row.get("fk_table_name") or row.get("FK_TABLE_NAME") or ""
            fk_column = row.get("fk_column_name") or row.get("FK_COLUMN_NAME") or ""
            pk_schema = row.get("pk_schema_name") or row.get("PK_SCHEMA_NAME") or schema
            pk_table = row.get("pk_table_name") or row.get("PK_TABLE_NAME") or ""
            pk_column = row.get("pk_column_name") or row.get("PK_COLUMN_NAME") or ""
            constraint = row.get("fk_name") or row.get("FK_NAME") or ""
            
            if fk_table and pk_table:
                foreign_keys.append(ForeignKey(
                    constraint_name=constraint,
                    database_name=database,
                    source_schema=fk_schema,
                    source_table=fk_table,
                    source_column=fk_column,
                    target_schema=pk_schema,
                    target_table=pk_table,
                    target_column=pk_column,
                ))
        
        return foreign_keys
    
    async def _fetch_primary_keys_in_schema(
        self, database: str, schema: str
    ) -> List[Dict[str, str]]:
        if not self.warehouse:
            return []
        
        sql = f"SHOW PRIMARY KEYS IN SCHEMA {database}.{schema}"

        # SHOW PRIMARY KEYS might return columns: 
        # database_name, schema_name, table_name, column_name, key_sequence, constraint_name
        
        response = await self.data_source.execute_sql(
            statement=sql,
            database=database,
            warehouse=self.warehouse,
        )
        
        if not response.success:
            logger.warning("Failed to fetch primary keys: %s", response.error)
            return []
        
        pks = []
        rows = self._parse_sql_result(response.data)
        
        for row in rows:
            table = row.get("table_name") or row.get("TABLE_NAME")
            column = row.get("column_name") or row.get("COLUMN_NAME")
            if table and column:
                pks.append({"table": table, "column": column})
        
        return pks
    
    def _extract_items(self, data: Any) -> List[Dict[str, Any]]:
        if not data:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ["data", "rowset", "items"]:
                if key in data and isinstance(data[key], list):
                    return data[key]
        return []
    
    def _parse_sql_result(self, data: Any) -> List[Dict[str, Any]]:
        if not data or not isinstance(data, dict):
            return []
        
        meta = data.get("resultSetMetaData", {})
        row_type = meta.get("rowType", [])
        columns = [col.get("name", f"col_{i}") for i, col in enumerate(row_type)]
        
        rows = []
        for row in data.get("data", []):
            if isinstance(row, list):
                rows.append(dict(zip(columns, row)))
            elif isinstance(row, dict):
                rows.append(row)
        return rows
    
    def save_to_file(self, filepath: str) -> None:
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.hierarchy.to_dict(), f, indent=2, default=str)
        
        logger.info("Saved to %s", path)
    
    def print_summary(self) -> None:
        print("\n" + "=" * 50)
        print("Snowflake Data Summary")
        print("=" * 50)
        for key, value in self.hierarchy.summary().items():
            print(f"  {key}: {value}")
        print("=" * 50)
