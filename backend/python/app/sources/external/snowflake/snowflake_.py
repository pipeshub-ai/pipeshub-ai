"""
Snowflake REST API DataSource - Auto-generated API wrapper

Generated from Snowflake REST API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures - NO Any type, NO **kwargs.
"""

import logging
import traceback
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.snowflake.snowflake import SnowflakeClient, SnowflakeResponse

logger = logging.getLogger(__name__)

SUCCESS_STATUS_CODE = 200
ERROR_STATUS_CODE = 400

class SnowflakeDataSource:
    """Snowflake REST API v2 DataSource
    Provides async wrapper methods for Snowflake REST API v2 operations:
    - Database, Schema, Table, View operations
    - Warehouse management
    - User and Role management
    - Stage, Task, Stream, Pipe operations
    - Alert, Network Policy, Function, Procedure operations
    - Compute Pool, Notebook operations
    All methods have explicit parameter signatures - NO Any type, NO **kwargs.
    All methods return SnowflakeResponse objects.
    """

    def __init__(self, client: SnowflakeClient) -> None:
        """Initialize with SnowflakeClient.
        Args:
            client: SnowflakeClient instance with configured authentication
        """
        logger.debug(f"🔧 [SnowflakeDataSource] __init__ called")
        self._client = client
        self.http = client.get_client()
        logger.debug(f"🔧 [SnowflakeDataSource] http client type: {type(self.http).__name__}")
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
            logger.debug(f"🔧 [SnowflakeDataSource] base_url set to: '{self.base_url}'")
            logger.info(f"🔧 [SnowflakeDataSource] Initialized successfully with base_url: '{self.base_url}'")
        except AttributeError as exc:
            logger.error(f"🔧 [SnowflakeDataSource] Failed to get base_url: {exc}")
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'SnowflakeDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> SnowflakeClient:
        """Return the underlying SnowflakeClient."""
        return self._client

    async def list_databases(
        self,
        like: Optional[str] = None,
        starts_with: Optional[str] = None,
        show_limit: Optional[int] = None,
        from_name: Optional[str] = None,
        history: Optional[bool] = None
    ) -> SnowflakeResponse:
        """List all accessible databases

        Args:
            like: Filter by name pattern (case-insensitive)
            starts_with: Filter by name prefix (case-sensitive)
            show_limit: Maximum number of rows to return
            from_name: Fetch rows after this name (pagination)
            history: Include dropped databases

        Returns:
            SnowflakeResponse with operation result
        """
        logger.debug(f"🔧 [SnowflakeDataSource] list_databases called")
        logger.debug(f"🔧 [SnowflakeDataSource] Parameters: like={like}, starts_with={starts_with}, show_limit={show_limit}, from_name={from_name}, history={history}")
        logger.debug(f"🔧 [SnowflakeDataSource] base_url: '{self.base_url}'")
        
        query_params = []
        if like is not None:
            query_params.append(('like', like))
        if starts_with is not None:
            query_params.append(('startsWith', starts_with))
        if show_limit is not None:
            query_params.append(('showLimit', str(show_limit)))
        if from_name is not None:
            query_params.append(('fromName', from_name))
        if history is not None:
            query_params.append(('history', 'true' if history else 'false'))

        url = self.base_url + "/databases"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        logger.debug(f"🔧 [SnowflakeDataSource] Final URL: '{url}'")
        
        headers = self.http.headers.copy()
        logger.debug(f"🔧 [SnowflakeDataSource] Request headers (excluding auth): { {k: v for k, v in headers.items() if 'authorization' not in k.lower()} }")

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            logger.debug(f"🔧 [SnowflakeDataSource] Executing HTTP request...")
            response = await self.http.execute(request)
            logger.debug(f"🔧 [SnowflakeDataSource] Response status: {response.status}")
            logger.debug(f"🔧 [SnowflakeDataSource] Response text (first 500 chars): {response.text()[:500] if response.text() else 'Empty'}")
            
            response_data = response.json() if response.text() else None
            success = response.status < ERROR_STATUS_CODE
            
            logger.debug(f"🔧 [SnowflakeDataSource] list_databases success: {success}")
            if not success:
                logger.error(f"🔧 [SnowflakeDataSource] list_databases FAILED with status {response.status}")
                logger.error(f"🔧 [SnowflakeDataSource] Response data: {response_data}")
            
            return SnowflakeResponse(
                success=success,
                data=response_data,
                message="Successfully executed list_databases" if success else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.error(f"🔧 [SnowflakeDataSource] list_databases exception: {str(e)}")
            logger.error(f"🔧 [SnowflakeDataSource] Traceback: {traceback.format_exc()}")
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_databases")

    async def create_database(
        self,
        name: str,
        create_mode: Optional[str] = None,
        kind: Optional[str] = None,
        comment: Optional[str] = None,
        data_retention_time_in_days: Optional[int] = None,
        default_ddl_collation: Optional[str] = None,
        max_data_extension_time_in_days: Optional[int] = None
    ) -> SnowflakeResponse:
        """Create a new database

        Args:
            name: Database name
            create_mode: Creation mode: errorIfExists, orReplace, ifNotExists
            kind: Database type: PERMANENT, TRANSIENT
            comment: Database comment
            data_retention_time_in_days: Time Travel retention period in days
            default_ddl_collation: Default collation for DDL statements
            max_data_extension_time_in_days: Maximum data extension time

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/databases"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        if kind is not None:
            body['kind'] = kind
        if comment is not None:
            body['comment'] = comment
        if data_retention_time_in_days is not None:
            body['dataRetentionTimeInDays'] = data_retention_time_in_days
        if default_ddl_collation is not None:
            body['defaultDdlCollation'] = default_ddl_collation
        if max_data_extension_time_in_days is not None:
            body['maxDataExtensionTimeInDays'] = max_data_extension_time_in_days

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_database" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_database")

    async def get_database(
        self,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific database by name

        Args:
            name: Database name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{name}".format(name=name)
        # print(url)
        headers = self.http.headers.copy()
        # print(headers)
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        # print(request)
        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_database" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_database")

    async def delete_database(
        self,
        name: str,
        if_exists: Optional[bool] = None,
        restrict: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a database

        Args:
            name: Database name
            if_exists: Only drop if database exists
            restrict: Restrict drop if database has dependent objects

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))
        if restrict is not None:
            query_params.append(('restrict', 'true' if restrict else 'false'))

        url = self.base_url + "/databases/{name}".format(name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_database" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_database")

    async def undrop_database(
        self,
        name: str
    ) -> SnowflakeResponse:
        """Restore a dropped database

        Args:
            name: Database name to restore

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{name}:undrop".format(name=name)

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed undrop_database" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute undrop_database")

    async def list_schemas(
        self,
        database: str,
        like: Optional[str] = None,
        starts_with: Optional[str] = None,
        show_limit: Optional[int] = None,
        from_name: Optional[str] = None,
        history: Optional[bool] = None
    ) -> SnowflakeResponse:
        """List all schemas in a database

        Args:
            database: Database name
            like: Filter by name pattern
            starts_with: Filter by name prefix
            show_limit: Maximum rows to return
            from_name: Fetch rows after this name
            history: Include dropped schemas

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))
        if starts_with is not None:
            query_params.append(('startsWith', starts_with))
        if show_limit is not None:
            query_params.append(('showLimit', str(show_limit)))
        if from_name is not None:
            query_params.append(('fromName', from_name))
        if history is not None:
            query_params.append(('history', 'true' if history else 'false'))

        url = self.base_url + "/databases/{database}/schemas".format(database=database)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_schemas" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_schemas")

    async def create_schema(
        self,
        database: str,
        name: str,
        create_mode: Optional[str] = None,
        kind: Optional[str] = None,
        comment: Optional[str] = None,
        managed_access: Optional[bool] = None,
        data_retention_time_in_days: Optional[int] = None
    ) -> SnowflakeResponse:
        """Create a new schema in a database

        Args:
            database: Database name
            name: Schema name
            create_mode: Creation mode: errorIfExists, orReplace, ifNotExists
            kind: Schema type: PERMANENT, TRANSIENT
            comment: Schema comment
            managed_access: Enable managed access
            data_retention_time_in_days: Data retention period

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/databases/{database}/schemas".format(database=database)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        if kind is not None:
            body['kind'] = kind
        if comment is not None:
            body['comment'] = comment
        if managed_access is not None:
            body['managedAccess'] = managed_access
        if data_retention_time_in_days is not None:
            body['dataRetentionTimeInDays'] = data_retention_time_in_days

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_schema" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_schema")

    async def get_schema(
        self,
        database: str,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific schema

        Args:
            database: Database name
            name: Schema name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{database}/schemas/{name}".format(database=database, name=name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_schema" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_schema")

    async def delete_schema(
        self,
        database: str,
        name: str,
        if_exists: Optional[bool] = None,
        restrict: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a schema

        Args:
            database: Database name
            name: Schema name
            if_exists: Only drop if exists
            restrict: Restrict if has dependents

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))
        if restrict is not None:
            query_params.append(('restrict', 'true' if restrict else 'false'))

        url = self.base_url + "/databases/{database}/schemas/{name}".format(database=database, name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_schema" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_schema")

    async def undrop_schema(
        self,
        database: str,
        name: str
    ) -> SnowflakeResponse:
        """Restore a dropped schema

        Args:
            database: Database name
            name: Schema name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{database}/schemas/{name}:undrop".format(database=database, name=name)

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed undrop_schema" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute undrop_schema")

    async def list_tables(
        self,
        database: str,
        schema: str,
        like: Optional[str] = None,
        starts_with: Optional[str] = None,
        show_limit: Optional[int] = None,
        from_name: Optional[str] = None,
        history: Optional[bool] = None
    ) -> SnowflakeResponse:
        """List all tables in a schema

        Args:
            database: Database name
            schema: Schema name
            like: Filter by name pattern
            starts_with: Filter by name prefix
            show_limit: Maximum rows to return
            from_name: Fetch rows after this name
            history: Include dropped tables

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))
        if starts_with is not None:
            query_params.append(('startsWith', starts_with))
        if show_limit is not None:
            query_params.append(('showLimit', str(show_limit)))
        if from_name is not None:
            query_params.append(('fromName', from_name))
        if history is not None:
            query_params.append(('history', 'true' if history else 'false'))

        url = self.base_url + "/databases/{database}/schemas/{schema}/tables".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_tables" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_tables")

    async def create_table(
        self,
        database: str,
        schema: str,
        name: str,
        columns: List[Dict[str, str]],
        create_mode: Optional[str] = None,
        kind: Optional[str] = None,
        cluster_by: Optional[List[str]] = None,
        comment: Optional[str] = None,
        data_retention_time_in_days: Optional[int] = None
    ) -> SnowflakeResponse:
        """Create a new table

        Args:
            database: Database name
            schema: Schema name
            name: Table name
            columns: Column definitions with name, datatype, nullable, etc.
            create_mode: Creation mode
            kind: Table type: PERMANENT, TRANSIENT, TEMPORARY
            cluster_by: Clustering keys
            comment: Table comment
            data_retention_time_in_days: Data retention period

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/databases/{database}/schemas/{schema}/tables".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        body['columns'] = columns
        if kind is not None:
            body['kind'] = kind
        if cluster_by is not None:
            body['clusterBy'] = cluster_by
        if comment is not None:
            body['comment'] = comment
        if data_retention_time_in_days is not None:
            body['dataRetentionTimeInDays'] = data_retention_time_in_days

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_table" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_table")

    async def get_table(
        self,
        database: str,
        schema: str,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific table

        Args:
            database: Database name
            schema: Schema name
            name: Table name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{database}/schemas/{schema}/tables/{name}".format(database=database, schema=schema, name=name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_table" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_table")

    async def delete_table(
        self,
        database: str,
        schema: str,
        name: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a table

        Args:
            database: Database name
            schema: Schema name
            name: Table name
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/databases/{database}/schemas/{schema}/tables/{name}".format(database=database, schema=schema, name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_table" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_table")

    async def undrop_table(
        self,
        database: str,
        schema: str,
        name: str
    ) -> SnowflakeResponse:
        """Restore a dropped table

        Args:
            database: Database name
            schema: Schema name
            name: Table name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{database}/schemas/{schema}/tables/{name}:undrop".format(database=database, schema=schema, name=name)

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed undrop_table" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute undrop_table")

    async def list_views(
        self,
        database: str,
        schema: str,
        like: Optional[str] = None,
        starts_with: Optional[str] = None,
        show_limit: Optional[int] = None
    ) -> SnowflakeResponse:
        """List all views in a schema

        Args:
            database: Database name
            schema: Schema name
            like: Filter by name pattern
            starts_with: Filter by name prefix
            show_limit: Maximum rows to return

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))
        if starts_with is not None:
            query_params.append(('startsWith', starts_with))
        if show_limit is not None:
            query_params.append(('showLimit', str(show_limit)))

        url = self.base_url + "/databases/{database}/schemas/{schema}/views".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_views" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_views")

    async def create_view(
        self,
        database: str,
        schema: str,
        name: str,
        text: str,
        create_mode: Optional[str] = None,
        is_secure: Optional[bool] = None,
        comment: Optional[str] = None,
        columns: Optional[List[Dict[str, str]]] = None
    ) -> SnowflakeResponse:
        """Create a new view

        Args:
            database: Database name
            schema: Schema name
            name: View name
            text: View SQL definition (SELECT statement)
            create_mode: Creation mode
            is_secure: Create as secure view
            comment: View comment
            columns: Column definitions

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/databases/{database}/schemas/{schema}/views".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        body['text'] = text
        if is_secure is not None:
            body['isSecure'] = is_secure
        if comment is not None:
            body['comment'] = comment
        if columns is not None:
            body['columns'] = columns

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_view" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_view")

    async def get_view(
        self,
        database: str,
        schema: str,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific view

        Args:
            database: Database name
            schema: Schema name
            name: View name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{database}/schemas/{schema}/views/{name}".format(database=database, schema=schema, name=name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_view" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_view")

    async def delete_view(
        self,
        database: str,
        schema: str,
        name: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a view

        Args:
            database: Database name
            schema: Schema name
            name: View name
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/databases/{database}/schemas/{schema}/views/{name}".format(database=database, schema=schema, name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_view" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_view")

    async def list_warehouses(
        self,
        like: Optional[str] = None
    ) -> SnowflakeResponse:
        """List all warehouses

        Args:
            like: Filter by name pattern

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))

        url = self.base_url + "/warehouses"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_warehouses" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_warehouses")

    async def create_warehouse(
        self,
        name: str,
        create_mode: Optional[str] = None,
        warehouse_size: Optional[str] = None,
        warehouse_type: Optional[str] = None,
        auto_suspend: Optional[int] = None,
        auto_resume: Optional[bool] = None,
        initially_suspended: Optional[bool] = None,
        min_cluster_count: Optional[int] = None,
        max_cluster_count: Optional[int] = None,
        scaling_policy: Optional[str] = None,
        comment: Optional[str] = None,
        enable_query_acceleration: Optional[bool] = None,
        query_acceleration_max_scale_factor: Optional[int] = None
    ) -> SnowflakeResponse:
        """Create a new warehouse

        Args:
            name: Warehouse name
            create_mode: Creation mode
            warehouse_size: Size: XSMALL, SMALL, MEDIUM, LARGE, XLARGE, etc.
            warehouse_type: Type: STANDARD, SNOWPARK-OPTIMIZED
            auto_suspend: Auto-suspend timeout in seconds
            auto_resume: Enable auto-resume
            initially_suspended: Create in suspended state
            min_cluster_count: Minimum cluster count for multi-cluster
            max_cluster_count: Maximum cluster count for multi-cluster
            scaling_policy: Scaling policy: STANDARD, ECONOMY
            comment: Warehouse comment
            enable_query_acceleration: Enable query acceleration
            query_acceleration_max_scale_factor: Max scale factor for query acceleration

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/warehouses"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        if warehouse_size is not None:
            body['warehouseSize'] = warehouse_size
        if warehouse_type is not None:
            body['warehouseType'] = warehouse_type
        if auto_suspend is not None:
            body['autoSuspend'] = auto_suspend
        if auto_resume is not None:
            body['autoResume'] = auto_resume
        if initially_suspended is not None:
            body['initiallySuspended'] = initially_suspended
        if min_cluster_count is not None:
            body['minClusterCount'] = min_cluster_count
        if max_cluster_count is not None:
            body['maxClusterCount'] = max_cluster_count
        if scaling_policy is not None:
            body['scalingPolicy'] = scaling_policy
        if comment is not None:
            body['comment'] = comment
        if enable_query_acceleration is not None:
            body['enableQueryAcceleration'] = enable_query_acceleration
        if query_acceleration_max_scale_factor is not None:
            body['queryAccelerationMaxScaleFactor'] = query_acceleration_max_scale_factor

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_warehouse" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_warehouse")

    async def get_warehouse(
        self,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific warehouse

        Args:
            name: Warehouse name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/warehouses/{name}".format(name=name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_warehouse" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_warehouse")

    async def delete_warehouse(
        self,
        name: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a warehouse

        Args:
            name: Warehouse name
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/warehouses/{name}".format(name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_warehouse" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_warehouse")

    async def resume_warehouse(
        self,
        name: str,
        if_suspended: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Resume a suspended warehouse

        Args:
            name: Warehouse name
            if_suspended: Only resume if currently suspended

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_suspended is not None:
            query_params.append(('ifSuspended', 'true' if if_suspended else 'false'))

        url = self.base_url + "/warehouses/{name}:resume".format(name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed resume_warehouse" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute resume_warehouse")

    async def suspend_warehouse(
        self,
        name: str,
        if_running: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Suspend a running warehouse

        Args:
            name: Warehouse name
            if_running: Only suspend if currently running

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_running is not None:
            query_params.append(('ifRunning', 'true' if if_running else 'false'))

        url = self.base_url + "/warehouses/{name}:suspend".format(name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed suspend_warehouse" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute suspend_warehouse")

    async def abort_warehouse_queries(
        self,
        name: str
    ) -> SnowflakeResponse:
        """Abort all running queries on a warehouse

        Args:
            name: Warehouse name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/warehouses/{name}:abort".format(name=name)

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed abort_warehouse_queries" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute abort_warehouse_queries")

    async def list_users(
        self,
        like: Optional[str] = None,
        starts_with: Optional[str] = None,
        show_limit: Optional[int] = None,
        from_name: Optional[str] = None
    ) -> SnowflakeResponse:
        """List all users

        Args:
            like: Filter by name pattern
            starts_with: Filter by name prefix
            show_limit: Maximum rows to return
            from_name: Fetch rows after this name

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))
        if starts_with is not None:
            query_params.append(('startsWith', starts_with))
        if show_limit is not None:
            query_params.append(('showLimit', str(show_limit)))
        if from_name is not None:
            query_params.append(('fromName', from_name))

        url = self.base_url + "/users"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_users" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def create_user(
        self,
        name: str,
        create_mode: Optional[str] = None,
        password: Optional[str] = None,
        login_name: Optional[str] = None,
        display_name: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        email: Optional[str] = None,
        default_role: Optional[str] = None,
        default_warehouse: Optional[str] = None,
        default_namespace: Optional[str] = None,
        must_change_password: Optional[bool] = None,
        disabled: Optional[bool] = None,
        comment: Optional[str] = None
    ) -> SnowflakeResponse:
        """Create a new user

        Args:
            name: User name
            create_mode: Creation mode
            password: User password
            login_name: Login name
            display_name: Display name
            first_name: First name
            last_name: Last name
            email: Email address
            default_role: Default role
            default_warehouse: Default warehouse
            default_namespace: Default namespace (database.schema)
            must_change_password: Force password change on first login
            disabled: Disable user account
            comment: User comment

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/users"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        if password is not None:
            body['password'] = password
        if login_name is not None:
            body['loginName'] = login_name
        if display_name is not None:
            body['displayName'] = display_name
        if first_name is not None:
            body['firstName'] = first_name
        if last_name is not None:
            body['lastName'] = last_name
        if email is not None:
            body['email'] = email
        if default_role is not None:
            body['defaultRole'] = default_role
        if default_warehouse is not None:
            body['defaultWarehouse'] = default_warehouse
        if default_namespace is not None:
            body['defaultNamespace'] = default_namespace
        if must_change_password is not None:
            body['mustChangePassword'] = must_change_password
        if disabled is not None:
            body['disabled'] = disabled
        if comment is not None:
            body['comment'] = comment

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_user" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_user")

    async def get_user(
        self,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific user

        Args:
            name: User name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/users/{name}".format(name=name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_user" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_user")

    async def delete_user(
        self,
        name: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a user

        Args:
            name: User name
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/users/{name}".format(name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_user" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_user")

    async def list_roles(
        self,
        like: Optional[str] = None,
        starts_with: Optional[str] = None,
        show_limit: Optional[int] = None,
        from_name: Optional[str] = None
    ) -> SnowflakeResponse:
        """List all roles

        Args:
            like: Filter by name pattern
            starts_with: Filter by name prefix
            show_limit: Maximum rows to return
            from_name: Fetch rows after this name

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))
        if starts_with is not None:
            query_params.append(('startsWith', starts_with))
        if show_limit is not None:
            query_params.append(('showLimit', str(show_limit)))
        if from_name is not None:
            query_params.append(('fromName', from_name))

        url = self.base_url + "/roles"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_roles" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_roles")

    async def create_role(
        self,
        name: str,
        create_mode: Optional[str] = None,
        comment: Optional[str] = None
    ) -> SnowflakeResponse:
        """Create a new role

        Args:
            name: Role name
            create_mode: Creation mode
            comment: Role comment

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/roles"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        if comment is not None:
            body['comment'] = comment

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_role" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_role")

    async def get_role(
        self,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific role

        Args:
            name: Role name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/roles/{name}".format(name=name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_role" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_role")

    async def delete_role(
        self,
        name: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a role

        Args:
            name: Role name
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/roles/{name}".format(name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_role" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_role")

    async def list_tasks(
        self,
        database: str,
        schema: str,
        like: Optional[str] = None,
        starts_with: Optional[str] = None,
        root_only: Optional[bool] = None,
        show_limit: Optional[int] = None
    ) -> SnowflakeResponse:
        """List all tasks in a schema

        Args:
            database: Database name
            schema: Schema name
            like: Filter by name pattern
            starts_with: Filter by name prefix
            root_only: Only return root tasks
            show_limit: Maximum rows to return

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))
        if starts_with is not None:
            query_params.append(('startsWith', starts_with))
        if root_only is not None:
            query_params.append(('rootOnly', 'true' if root_only else 'false'))
        if show_limit is not None:
            query_params.append(('showLimit', str(show_limit)))

        url = self.base_url + "/databases/{database}/schemas/{schema}/tasks".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_tasks" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_tasks")

    async def create_task(
        self,
        database: str,
        schema: str,
        name: str,
        definition: str,
        create_mode: Optional[str] = None,
        warehouse: Optional[str] = None,
        schedule: Optional[str] = None,
        predecessors: Optional[List[str]] = None,
        condition: Optional[str] = None,
        allow_overlapping_execution: Optional[bool] = None,
        comment: Optional[str] = None
    ) -> SnowflakeResponse:
        """Create a new task

        Args:
            database: Database name
            schema: Schema name
            name: Task name
            definition: SQL statement to execute
            create_mode: Creation mode
            warehouse: Warehouse to use
            schedule: CRON or interval schedule
            predecessors: Predecessor task names
            condition: WHEN condition
            allow_overlapping_execution: Allow concurrent executions
            comment: Task comment

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/databases/{database}/schemas/{schema}/tasks".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        body['definition'] = definition
        if warehouse is not None:
            body['warehouse'] = warehouse
        if schedule is not None:
            body['schedule'] = schedule
        if predecessors is not None:
            body['predecessors'] = predecessors
        if condition is not None:
            body['condition'] = condition
        if allow_overlapping_execution is not None:
            body['allowOverlappingExecution'] = allow_overlapping_execution
        if comment is not None:
            body['comment'] = comment

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_task" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_task")

    async def get_task(
        self,
        database: str,
        schema: str,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific task

        Args:
            database: Database name
            schema: Schema name
            name: Task name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{database}/schemas/{schema}/tasks/{name}".format(database=database, schema=schema, name=name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_task" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_task")

    async def delete_task(
        self,
        database: str,
        schema: str,
        name: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a task

        Args:
            database: Database name
            schema: Schema name
            name: Task name
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/databases/{database}/schemas/{schema}/tasks/{name}".format(database=database, schema=schema, name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_task" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_task")

    async def execute_task(
        self,
        database: str,
        schema: str,
        name: str,
        retry_last: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Execute a task immediately

        Args:
            database: Database name
            schema: Schema name
            name: Task name
            retry_last: Retry the last failed run

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if retry_last is not None:
            query_params.append(('retryLast', 'true' if retry_last else 'false'))

        url = self.base_url + "/databases/{database}/schemas/{schema}/tasks/{name}:execute".format(database=database, schema=schema, name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed execute_task" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute execute_task")

    async def resume_task(
        self,
        database: str,
        schema: str,
        name: str
    ) -> SnowflakeResponse:
        """Resume a suspended task

        Args:
            database: Database name
            schema: Schema name
            name: Task name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{database}/schemas/{schema}/tasks/{name}:resume".format(database=database, schema=schema, name=name)

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed resume_task" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute resume_task")

    async def suspend_task(
        self,
        database: str,
        schema: str,
        name: str
    ) -> SnowflakeResponse:
        """Suspend a running task

        Args:
            database: Database name
            schema: Schema name
            name: Task name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{database}/schemas/{schema}/tasks/{name}:suspend".format(database=database, schema=schema, name=name)

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed suspend_task" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute suspend_task")

    async def list_streams(
        self,
        database: str,
        schema: str,
        like: Optional[str] = None,
        starts_with: Optional[str] = None,
        show_limit: Optional[int] = None
    ) -> SnowflakeResponse:
        """List all streams in a schema

        Args:
            database: Database name
            schema: Schema name
            like: Filter by name pattern
            starts_with: Filter by name prefix
            show_limit: Maximum rows to return

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))
        if starts_with is not None:
            query_params.append(('startsWith', starts_with))
        if show_limit is not None:
            query_params.append(('showLimit', str(show_limit)))

        url = self.base_url + "/databases/{database}/schemas/{schema}/streams".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_streams" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_streams")

    async def create_stream(
        self,
        database: str,
        schema: str,
        name: str,
        source_type: str,
        source_name: str,
        create_mode: Optional[str] = None,
        append_only: Optional[bool] = None,
        show_initial_rows: Optional[bool] = None,
        comment: Optional[str] = None
    ) -> SnowflakeResponse:
        """Create a new stream

        Args:
            database: Database name
            schema: Schema name
            name: Stream name
            source_type: Source type: table, external_table, stage, view
            source_name: Fully qualified source object name
            create_mode: Creation mode
            append_only: Track only inserts
            show_initial_rows: Include existing rows
            comment: Stream comment

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/databases/{database}/schemas/{schema}/streams".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        body['sourceType'] = source_type
        body['sourceName'] = source_name
        if append_only is not None:
            body['appendOnly'] = append_only
        if show_initial_rows is not None:
            body['showInitialRows'] = show_initial_rows
        if comment is not None:
            body['comment'] = comment

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_stream" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_stream")

    async def get_stream(
        self,
        database: str,
        schema: str,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific stream

        Args:
            database: Database name
            schema: Schema name
            name: Stream name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{database}/schemas/{schema}/streams/{name}".format(database=database, schema=schema, name=name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_stream" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_stream")

    async def delete_stream(
        self,
        database: str,
        schema: str,
        name: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a stream

        Args:
            database: Database name
            schema: Schema name
            name: Stream name
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/databases/{database}/schemas/{schema}/streams/{name}".format(database=database, schema=schema, name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_stream" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_stream")

    async def list_stages(
        self,
        database: str,
        schema: str,
        like: Optional[str] = None
    ) -> SnowflakeResponse:
        """List all stages in a schema

        Args:
            database: Database name
            schema: Schema name
            like: Filter by name pattern

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))

        url = self.base_url + "/databases/{database}/schemas/{schema}/stages".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_stages" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_stages")

    async def create_stage(
        self,
        database: str,
        schema: str,
        name: str,
        create_mode: Optional[str] = None,
        kind: Optional[str] = None,
        url: Optional[str] = None,
        storage_integration: Optional[str] = None,
        credentials: Optional[Dict[str, str]] = None,
        encryption: Optional[Dict[str, str]] = None,
        directory_table: Optional[Dict[str, bool]] = None,
        comment: Optional[str] = None
    ) -> SnowflakeResponse:
        """Create a new stage

        Args:
            database: Database name
            schema: Schema name
            name: Stage name
            create_mode: Creation mode
            kind: Stage type: INTERNAL, EXTERNAL
            url: External stage URL (s3://, azure://, gcs://)
            storage_integration: Storage integration name
            credentials: Stage credentials
            encryption: Encryption settings
            directory_table: Directory table settings
            comment: Stage comment

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/databases/{database}/schemas/{schema}/stages".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        if kind is not None:
            body['kind'] = kind
        if url is not None:
            body['url'] = url
        if storage_integration is not None:
            body['storageIntegration'] = storage_integration
        if credentials is not None:
            body['credentials'] = credentials
        if encryption is not None:
            body['encryption'] = encryption
        if directory_table is not None:
            body['directoryTable'] = directory_table
        if comment is not None:
            body['comment'] = comment

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_stage" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_stage")

    async def get_stage(
        self,
        database: str,
        schema: str,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific stage

        Args:
            database: Database name
            schema: Schema name
            name: Stage name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{database}/schemas/{schema}/stages/{name}".format(database=database, schema=schema, name=name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_stage" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_stage")

    async def delete_stage(
        self,
        database: str,
        schema: str,
        name: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a stage

        Args:
            database: Database name
            schema: Schema name
            name: Stage name
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/databases/{database}/schemas/{schema}/stages/{name}".format(database=database, schema=schema, name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_stage" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_stage")

    async def list_pipes(
        self,
        database: str,
        schema: str,
        like: Optional[str] = None
    ) -> SnowflakeResponse:
        """List all pipes in a schema

        Args:
            database: Database name
            schema: Schema name
            like: Filter by name pattern

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))

        url = self.base_url + "/databases/{database}/schemas/{schema}/pipes".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_pipes" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_pipes")

    async def create_pipe(
        self,
        database: str,
        schema: str,
        name: str,
        copy_statement: str,
        create_mode: Optional[str] = None,
        auto_ingest: Optional[bool] = None,
        aws_sns_topic: Optional[str] = None,
        integration: Optional[str] = None,
        comment: Optional[str] = None
    ) -> SnowflakeResponse:
        """Create a new pipe for continuous data loading

        Args:
            database: Database name
            schema: Schema name
            name: Pipe name
            copy_statement: COPY INTO statement
            create_mode: Creation mode
            auto_ingest: Enable auto-ingest
            aws_sns_topic: AWS SNS topic ARN for notifications
            integration: Notification integration name
            comment: Pipe comment

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/databases/{database}/schemas/{schema}/pipes".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        body['copyStatement'] = copy_statement
        if auto_ingest is not None:
            body['autoIngest'] = auto_ingest
        if aws_sns_topic is not None:
            body['awsSnsTopic'] = aws_sns_topic
        if integration is not None:
            body['integration'] = integration
        if comment is not None:
            body['comment'] = comment

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_pipe" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_pipe")

    async def get_pipe(
        self,
        database: str,
        schema: str,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific pipe

        Args:
            database: Database name
            schema: Schema name
            name: Pipe name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{database}/schemas/{schema}/pipes/{name}".format(database=database, schema=schema, name=name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_pipe" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_pipe")

    async def delete_pipe(
        self,
        database: str,
        schema: str,
        name: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a pipe

        Args:
            database: Database name
            schema: Schema name
            name: Pipe name
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/databases/{database}/schemas/{schema}/pipes/{name}".format(database=database, schema=schema, name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_pipe" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_pipe")

    async def list_alerts(
        self,
        database: str,
        schema: str,
        like: Optional[str] = None
    ) -> SnowflakeResponse:
        """List all alerts in a schema

        Args:
            database: Database name
            schema: Schema name
            like: Filter by name pattern

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))

        url = self.base_url + "/databases/{database}/schemas/{schema}/alerts".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_alerts" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_alerts")

    async def create_alert(
        self,
        database: str,
        schema: str,
        name: str,
        warehouse: str,
        schedule: str,
        condition: str,
        action: str,
        create_mode: Optional[str] = None,
        comment: Optional[str] = None
    ) -> SnowflakeResponse:
        """Create a new alert

        Args:
            database: Database name
            schema: Schema name
            name: Alert name
            warehouse: Warehouse to execute the alert
            schedule: CRON or interval schedule
            condition: SQL condition that triggers the alert
            action: SQL action to execute when triggered
            create_mode: Creation mode
            comment: Alert comment

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/databases/{database}/schemas/{schema}/alerts".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        body['warehouse'] = warehouse
        body['schedule'] = schedule
        body['condition'] = condition
        body['action'] = action
        if comment is not None:
            body['comment'] = comment

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_alert" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_alert")

    async def get_alert(
        self,
        database: str,
        schema: str,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific alert

        Args:
            database: Database name
            schema: Schema name
            name: Alert name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{database}/schemas/{schema}/alerts/{name}".format(database=database, schema=schema, name=name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_alert" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_alert")

    async def delete_alert(
        self,
        database: str,
        schema: str,
        name: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop an alert

        Args:
            database: Database name
            schema: Schema name
            name: Alert name
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/databases/{database}/schemas/{schema}/alerts/{name}".format(database=database, schema=schema, name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_alert" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_alert")

    async def list_network_policies(
        self,
        like: Optional[str] = None
    ) -> SnowflakeResponse:
        """List all network policies

        Args:
            like: Filter by name pattern

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))

        url = self.base_url + "/network-policies"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_network_policies" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_network_policies")

    async def create_network_policy(
        self,
        name: str,
        allowed_ip_list: List[str],
        create_mode: Optional[str] = None,
        blocked_ip_list: Optional[List[str]] = None,
        comment: Optional[str] = None
    ) -> SnowflakeResponse:
        """Create a new network policy

        Args:
            name: Network policy name
            allowed_ip_list: List of allowed IP addresses or CIDR ranges
            create_mode: Creation mode
            blocked_ip_list: List of blocked IP addresses
            comment: Policy comment

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/network-policies"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        body['allowedIpList'] = allowed_ip_list
        if blocked_ip_list is not None:
            body['blockedIpList'] = blocked_ip_list
        if comment is not None:
            body['comment'] = comment

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_network_policy" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_network_policy")

    async def get_network_policy(
        self,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific network policy

        Args:
            name: Network policy name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/network-policies/{name}".format(name=name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_network_policy" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_network_policy")

    async def delete_network_policy(
        self,
        name: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a network policy

        Args:
            name: Network policy name
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/network-policies/{name}".format(name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_network_policy" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_network_policy")

    async def list_functions(
        self,
        database: str,
        schema: str,
        like: Optional[str] = None
    ) -> SnowflakeResponse:
        """List all user-defined functions in a schema

        Args:
            database: Database name
            schema: Schema name
            like: Filter by name pattern

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))

        url = self.base_url + "/databases/{database}/schemas/{schema}/functions".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_functions" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_functions")

    async def create_function(
        self,
        database: str,
        schema: str,
        name: str,
        arguments: List[Dict[str, str]],
        return_type: str,
        language: str,
        body: str,
        create_mode: Optional[str] = None,
        is_secure: Optional[bool] = None,
        runtime_version: Optional[str] = None,
        packages: Optional[List[str]] = None,
        handler: Optional[str] = None,
        comment: Optional[str] = None
    ) -> SnowflakeResponse:
        """Create a new user-defined function

        Args:
            database: Database name
            schema: Schema name
            name: Function name
            arguments: Function arguments with name and type
            return_type: Return data type
            language: Language: SQL, JAVASCRIPT, PYTHON, JAVA, SCALA
            body: Function body/definition
            create_mode: Creation mode
            is_secure: Create as secure function
            runtime_version: Runtime version for non-SQL
            packages: Package dependencies
            handler: Handler function name
            comment: Function comment

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/databases/{database}/schemas/{schema}/functions".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        body['arguments'] = arguments
        body['returnType'] = return_type
        body['language'] = language
        body['body'] = body
        if is_secure is not None:
            body['isSecure'] = is_secure
        if runtime_version is not None:
            body['runtimeVersion'] = runtime_version
        if packages is not None:
            body['packages'] = packages
        if handler is not None:
            body['handler'] = handler
        if comment is not None:
            body['comment'] = comment

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_function" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_function")

    async def delete_function(
        self,
        database: str,
        schema: str,
        name_with_args: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a user-defined function

        Args:
            database: Database name
            schema: Schema name
            name_with_args: Function name with argument types (e.g., my_func(int,string))
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/databases/{database}/schemas/{schema}/functions/{name_with_args}".format(database=database, schema=schema, name_with_args=name_with_args)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_function" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_function")

    async def list_procedures(
        self,
        database: str,
        schema: str,
        like: Optional[str] = None
    ) -> SnowflakeResponse:
        """List all stored procedures in a schema

        Args:
            database: Database name
            schema: Schema name
            like: Filter by name pattern

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))

        url = self.base_url + "/databases/{database}/schemas/{schema}/procedures".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_procedures" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_procedures")

    async def create_procedure(
        self,
        database: str,
        schema: str,
        name: str,
        arguments: List[Dict[str, str]],
        return_type: str,
        language: str,
        body: str,
        create_mode: Optional[str] = None,
        execute_as: Optional[str] = None,
        runtime_version: Optional[str] = None,
        packages: Optional[List[str]] = None,
        handler: Optional[str] = None,
        comment: Optional[str] = None
    ) -> SnowflakeResponse:
        """Create a new stored procedure

        Args:
            database: Database name
            schema: Schema name
            name: Procedure name
            arguments: Procedure arguments
            return_type: Return data type
            language: Language: SQL, JAVASCRIPT, PYTHON, JAVA, SCALA
            body: Procedure body
            create_mode: Creation mode
            execute_as: Execute as: CALLER, OWNER
            runtime_version: Runtime version
            packages: Package dependencies
            handler: Handler function name
            comment: Procedure comment

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/databases/{database}/schemas/{schema}/procedures".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        body['arguments'] = arguments
        body['returnType'] = return_type
        body['language'] = language
        body['body'] = body
        if execute_as is not None:
            body['executeAs'] = execute_as
        if runtime_version is not None:
            body['runtimeVersion'] = runtime_version
        if packages is not None:
            body['packages'] = packages
        if handler is not None:
            body['handler'] = handler
        if comment is not None:
            body['comment'] = comment

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_procedure" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_procedure")

    async def delete_procedure(
        self,
        database: str,
        schema: str,
        name_with_args: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a stored procedure

        Args:
            database: Database name
            schema: Schema name
            name_with_args: Procedure name with argument types
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/databases/{database}/schemas/{schema}/procedures/{name_with_args}".format(database=database, schema=schema, name_with_args=name_with_args)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_procedure" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_procedure")

    async def list_compute_pools(
        self,
        like: Optional[str] = None
    ) -> SnowflakeResponse:
        """List all compute pools

        Args:
            like: Filter by name pattern

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))

        url = self.base_url + "/compute-pools"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_compute_pools" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_compute_pools")

    async def create_compute_pool(
        self,
        name: str,
        min_nodes: int,
        max_nodes: int,
        instance_family: str,
        create_mode: Optional[str] = None,
        auto_resume: Optional[bool] = None,
        initially_suspended: Optional[bool] = None,
        auto_suspend_secs: Optional[int] = None,
        comment: Optional[str] = None
    ) -> SnowflakeResponse:
        """Create a new compute pool for container services

        Args:
            name: Compute pool name
            min_nodes: Minimum number of nodes
            max_nodes: Maximum number of nodes
            instance_family: Instance family (e.g., CPU_X64_XS)
            create_mode: Creation mode
            auto_resume: Enable auto-resume
            initially_suspended: Create in suspended state
            auto_suspend_secs: Auto-suspend timeout in seconds
            comment: Compute pool comment

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/compute-pools"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        body['minNodes'] = min_nodes
        body['maxNodes'] = max_nodes
        body['instanceFamily'] = instance_family
        if auto_resume is not None:
            body['autoResume'] = auto_resume
        if initially_suspended is not None:
            body['initiallySuspended'] = initially_suspended
        if auto_suspend_secs is not None:
            body['autoSuspendSecs'] = auto_suspend_secs
        if comment is not None:
            body['comment'] = comment

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_compute_pool" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_compute_pool")

    async def get_compute_pool(
        self,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific compute pool

        Args:
            name: Compute pool name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/compute-pools/{name}".format(name=name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_compute_pool" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_compute_pool")

    async def delete_compute_pool(
        self,
        name: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a compute pool

        Args:
            name: Compute pool name
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/compute-pools/{name}".format(name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_compute_pool" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_compute_pool")

    async def resume_compute_pool(
        self,
        name: str
    ) -> SnowflakeResponse:
        """Resume a suspended compute pool

        Args:
            name: Compute pool name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/compute-pools/{name}:resume".format(name=name)

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed resume_compute_pool" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute resume_compute_pool")

    async def suspend_compute_pool(
        self,
        name: str
    ) -> SnowflakeResponse:
        """Suspend a compute pool

        Args:
            name: Compute pool name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/compute-pools/{name}:suspend".format(name=name)

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed suspend_compute_pool" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute suspend_compute_pool")

    async def list_notebooks(
        self,
        database: str,
        schema: str,
        like: Optional[str] = None
    ) -> SnowflakeResponse:
        """List all notebooks in a schema

        Args:
            database: Database name
            schema: Schema name
            like: Filter by name pattern

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))

        url = self.base_url + "/databases/{database}/schemas/{schema}/notebooks".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed list_notebooks" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute list_notebooks")

    async def create_notebook(
        self,
        database: str,
        schema: str,
        name: str,
        create_mode: Optional[str] = None,
        comment: Optional[str] = None
    ) -> SnowflakeResponse:
        """Create a new notebook

        Args:
            database: Database name
            schema: Schema name
            name: Notebook name
            create_mode: Creation mode
            comment: Notebook comment

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if create_mode is not None:
            query_params.append(('createMode', create_mode))

        url = self.base_url + "/databases/{database}/schemas/{schema}/notebooks".format(database=database, schema=schema)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['name'] = name
        if comment is not None:
            body['comment'] = comment

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed create_notebook" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute create_notebook")

    async def get_notebook(
        self,
        database: str,
        schema: str,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific notebook

        Args:
            database: Database name
            schema: Schema name
            name: Notebook name

        Returns:
            SnowflakeResponse with operation result
        """
        url = self.base_url + "/databases/{database}/schemas/{schema}/notebooks/{name}".format(database=database, schema=schema, name=name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed get_notebook" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute get_notebook")

    async def delete_notebook(
        self,
        database: str,
        schema: str,
        name: str,
        if_exists: Optional[bool] = None
    ) -> SnowflakeResponse:
        """Drop a notebook

        Args:
            database: Database name
            schema: Schema name
            name: Notebook name
            if_exists: Only drop if exists

        Returns:
            SnowflakeResponse with operation result
        """
        query_params = []
        if if_exists is not None:
            query_params.append(('ifExists', 'true' if if_exists else 'false'))

        url = self.base_url + "/databases/{database}/schemas/{schema}/notebooks/{name}".format(database=database, schema=schema, name=name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed delete_notebook" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute delete_notebook")

    # ========================================================================
    # SQL Statements API - For executing custom SQL queries
    # ========================================================================

    async def execute_sql(
        self,
        statement: str,
        database: Optional[str] = None,
        schema: Optional[str] = None,
        warehouse: Optional[str] = None,
        role: Optional[str] = None,
        timeout: Optional[int] = None,
        async_exec: Optional[bool] = None,
        parameters: Optional[Dict[str, str]] = None
    ) -> SnowflakeResponse:
        """Execute a SQL statement via the Snowflake SQL API

        This is used for queries like:
        - SELECT * FROM DIRECTORY(@stage) - List files in a stage
        - SHOW GRANTS ON <object_type> <object_name> - Get permissions
        - DESCRIBE TABLE <table> - Get column details
        - SELECT GET_DDL('VIEW', '<view>') - Get view definition

        Args:
            statement: SQL statement to execute
            database: Database context for the statement
            schema: Schema context for the statement
            warehouse: Warehouse to use for execution
            role: Role to use for execution
            timeout: Query timeout in seconds
            async_exec: If True, execute asynchronously and return statement handle
            parameters: Bind parameters for the statement

        Returns:
            SnowflakeResponse with query results or statement handle
        """
        query_params = []
        if async_exec is not None:
            query_params.append(('async', 'true' if async_exec else 'false'))

        url = self.base_url + "/statements"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body: Dict = {
            "statement": statement,
        }
        if database is not None:
            body["database"] = database
        if schema is not None:
            body["schema"] = schema
        if warehouse is not None:
            body["warehouse"] = warehouse
        if role is not None:
            body["role"] = role
        if timeout is not None:
            body["timeout"] = timeout
        if parameters is not None:
            body["parameters"] = parameters

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=body
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully executed SQL statement" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute SQL statement")

    async def get_statement_status(
        self,
        statement_handle: str,
        partition: Optional[int] = None
    ) -> SnowflakeResponse:
        """Get the status and results of a previously submitted SQL statement

        Use this to check on async statements or retrieve additional result partitions.

        Args:
            statement_handle: The statement handle returned from execute_sql
            partition: Partition index for paginated results (0-based)

        Returns:
            SnowflakeResponse with statement status and/or results
        """
        query_params = []
        if partition is not None:
            query_params.append(('partition', str(partition)))

        url = self.base_url + f"/statements/{statement_handle}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully retrieved statement status" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to get statement status")

    async def cancel_statement(
        self,
        statement_handle: str
    ) -> SnowflakeResponse:
        """Cancel a running SQL statement

        Args:
            statement_handle: The statement handle to cancel

        Returns:
            SnowflakeResponse with cancellation result
        """
        url = self.base_url + f"/statements/{statement_handle}/cancel"

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully cancelled statement" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to cancel statement")

    # ========================================================================
    # Grants API - For permission management
    # ========================================================================

    async def list_grants_to_role(
        self,
        role_name: str,
        show_limit: Optional[int] = None
    ) -> SnowflakeResponse:
        """List all privileges granted to a role

        Equivalent to: SHOW GRANTS TO ROLE <role_name>
        Returns the privileges that the role has on various objects.

        Args:
            role_name: Name of the role
            show_limit: Maximum number of grants to return

        Returns:
            SnowflakeResponse with list of grants (privilege, granted_on, name, etc.)
        """
        query_params = []
        if show_limit is not None:
            query_params.append(('showLimit', str(show_limit)))

        url = self.base_url + f"/grants/role/{role_name}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully retrieved grants to role" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to list grants to role")

    async def list_grants_to_user(
        self,
        user_name: str,
        show_limit: Optional[int] = None
    ) -> SnowflakeResponse:
        """List all roles and privileges granted to a user

        Equivalent to: SHOW GRANTS TO USER <user_name>

        Args:
            user_name: Name of the user
            show_limit: Maximum number of grants to return

        Returns:
            SnowflakeResponse with list of grants
        """
        query_params = []
        if show_limit is not None:
            query_params.append(('showLimit', str(show_limit)))

        url = self.base_url + f"/grants/user/{user_name}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully retrieved grants to user" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to list grants to user")

    async def list_grants_of_role(
        self,
        role_name: str
    ) -> SnowflakeResponse:
        """List all users and roles that have been granted this role

        Equivalent to: SHOW GRANTS OF ROLE <role_name>
        Returns the grantees (users/roles) that have this role.

        Args:
            role_name: Name of the role

        Returns:
            SnowflakeResponse with list of grantees (grantee_name, granted_to: ROLE/USER)
        """
        # This endpoint may not exist in REST API, use SQL statement instead
        statement = f"SHOW GRANTS OF ROLE {role_name}"
        return await self.execute_sql(statement=statement)

    async def list_grants_on_object(
        self,
        object_type: str,
        object_name: str
    ) -> SnowflakeResponse:
        """List all grants on a specific object

        Equivalent to: SHOW GRANTS ON <object_type> <object_name>
        Returns all privileges granted on the object.

        Args:
            object_type: Type of object (DATABASE, SCHEMA, TABLE, VIEW, STAGE, etc.)
            object_name: Fully qualified name of the object

        Returns:
            SnowflakeResponse with list of grants
        """
        # Use SQL statement as there's no direct REST endpoint for this
        statement = f"SHOW GRANTS ON {object_type} {object_name}"
        return await self.execute_sql(statement=statement)

    # ========================================================================
    # Database Roles API
    # ========================================================================

    async def list_database_roles(
        self,
        database: str,
        like: Optional[str] = None,
        starts_with: Optional[str] = None,
        show_limit: Optional[int] = None,
        from_name: Optional[str] = None
    ) -> SnowflakeResponse:
        """List all database roles in a database

        Args:
            database: Database name
            like: Filter by name pattern
            starts_with: Filter by name prefix
            show_limit: Maximum rows to return
            from_name: Fetch rows after this name (pagination)

        Returns:
            SnowflakeResponse with list of database roles
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))
        if starts_with is not None:
            query_params.append(('startsWith', starts_with))
        if show_limit is not None:
            query_params.append(('showLimit', str(show_limit)))
        if from_name is not None:
            query_params.append(('fromName', from_name))

        url = self.base_url + f"/databases/{database}/database-roles"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully listed database roles" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to list database roles")

    async def get_database_role(
        self,
        database: str,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific database role

        Args:
            database: Database name
            name: Database role name

        Returns:
            SnowflakeResponse with database role details
        """
        url = self.base_url + f"/databases/{database}/database-roles/{name}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully retrieved database role" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to get database role")

    # ========================================================================
    # Files API - For downloading files from stages
    # ========================================================================

    async def download_file(
        self,
        file_url: str
    ) -> SnowflakeResponse:
        """Download a file from a Snowflake stage using the Files API

        Use this to download files for indexing in the vector database.
        The file_url should be obtained from a directory table query
        (FILE_URL column).

        REST Endpoint: GET /api/files/{encoded_file_url}

        Args:
            file_url: The file_url from directory table query
                     Format: https://{account}.snowflakecomputing.com/api/files/{db}/{schema}/{stage}/{path}

        Returns:
            SnowflakeResponse with file content in raw_content field (bytes)
        """
        # Extract the path portion if it's a full URL
        if file_url.startswith('http'):
            import re
            match = re.search(r'/api/files/(.+)', file_url)
            if match:
                file_path = match.group(1)
            else:
                file_path = file_url
        else:
            file_path = file_url

        url = self.base_url.replace('/api/v2', '') + f"/api/files/{file_path}"

        headers = self.http.headers.copy()
        headers["Accept"] = "*/*"  # Accept any content type for file download

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            if response.status < ERROR_STATUS_CODE:
                # Get raw bytes from response
                content = response.content if hasattr(response, 'content') else None
                if content is None and hasattr(response, 'text'):
                    # Fallback to text if no content attribute
                    text = response.text()
                    content = text.encode('utf-8') if isinstance(text, str) else text
                
                return SnowflakeResponse(
                    success=True,
                    raw_content=content,
                    message=f"Successfully downloaded file ({len(content) if content else 0} bytes)"
                )
            else:
                return SnowflakeResponse(
                    success=False,
                    data=None,
                    message=f"Failed to download file with status {response.status}"
                )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to download file")

    async def download_file_from_presigned_url(
        self,
        presigned_url: str
    ) -> SnowflakeResponse:
        """Download a file using a pre-signed URL (direct S3 access)

        This is the simplest way to download files from Snowflake stages.
        Pre-signed URLs are obtained from generate_presigned_url() and provide
        direct access to the underlying cloud storage without authentication.

        Args:
            presigned_url: Pre-signed URL from GET_PRESIGNED_URL function
                          (direct S3/Azure/GCS URL with auth in query string)

        Returns:
            SnowflakeResponse with file content in raw_content field (bytes)
        """
        import aiohttp
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(presigned_url) as response:
                    if response.status < 400:
                        content = await response.read()
                        return SnowflakeResponse(
                            success=True,
                            raw_content=content,
                            message=f"Successfully downloaded file ({len(content)} bytes)"
                        )
                    else:
                        return SnowflakeResponse(
                            success=False,
                            message=f"Failed to download file with status {response.status}"
                        )
        except ImportError:
            return SnowflakeResponse(
                success=False,
                error="aiohttp not installed",
                message="Install aiohttp: pip install aiohttp"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to download file")

    async def generate_presigned_url(
        self,
        database: str,
        schema: str,
        stage: str,
        file_path: str,
        expiration_seconds: int = 3600,
        warehouse: Optional[str] = None
    ) -> SnowflakeResponse:
        """Generate a pre-signed URL for file download

        Pre-signed URLs can be shared and accessed by anyone with the URL.
        Uses SQL: SELECT GET_PRESIGNED_URL(@stage, 'path', expiration_seconds)

        Args:
            database: Database name
            schema: Schema name
            stage: Stage name
            file_path: Relative path of the file within the stage
            expiration_seconds: URL expiration time in seconds (default: 1 hour)
            warehouse: Warehouse to use for SQL execution (required)

        Returns:
            SnowflakeResponse with presigned_url in data field
        """
        # Escape single quotes in file path
        safe_file_path = file_path.replace("'", "''")
        statement = f"SELECT GET_PRESIGNED_URL(@{database}.{schema}.{stage}, '{safe_file_path}', {expiration_seconds}) AS presigned_url"
        return await self.execute_sql(statement=statement, database=database, schema=schema, warehouse=warehouse)

    async def list_stage_files(
        self,
        database: str,
        schema: str,
        stage: str,
        pattern: Optional[str] = None,
        warehouse: Optional[str] = None
    ) -> SnowflakeResponse:
        """List all files in a stage using Directory Table

        This queries the directory table of the stage to get file metadata
        including paths, sizes, timestamps, and download URLs.

        Args:
            database: Database name
            schema: Schema name
            stage: Stage name
            pattern: Optional pattern to filter files (e.g., '%.pdf')
            warehouse: Warehouse to use for SQL execution (required for queries)

        Returns:
            SnowflakeResponse with list of files containing:
            - RELATIVE_PATH: File path within stage
            - LAST_MODIFIED: Last modification timestamp
            - MD5: File MD5 hash
            - ETAG: Entity tag
            - FILE_URL: Permanent file URL
            - SCOPED_FILE_URL: Temporary scoped URL for download
        """
        statement = f"SELECT * FROM DIRECTORY(@{database}.{schema}.{stage})"
        if pattern:
            safe_pattern = pattern.replace("'", "''")
            statement += f" WHERE RELATIVE_PATH LIKE '{safe_pattern}'"

        return await self.execute_sql(statement=statement, database=database, schema=schema, warehouse=warehouse)

    # ========================================================================
    # Dynamic Tables API
    # ========================================================================

    async def list_dynamic_tables(
        self,
        database: str,
        schema: str,
        like: Optional[str] = None,
        starts_with: Optional[str] = None,
        show_limit: Optional[int] = None
    ) -> SnowflakeResponse:
        """List all dynamic tables in a schema

        Args:
            database: Database name
            schema: Schema name
            like: Filter by name pattern
            starts_with: Filter by name prefix
            show_limit: Maximum rows to return

        Returns:
            SnowflakeResponse with list of dynamic tables
        """
        query_params = []
        if like is not None:
            query_params.append(('like', like))
        if starts_with is not None:
            query_params.append(('startsWith', starts_with))
        if show_limit is not None:
            query_params.append(('showLimit', str(show_limit)))

        url = self.base_url + f"/databases/{database}/schemas/{schema}/dynamic-tables"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully listed dynamic tables" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to list dynamic tables")

    async def get_dynamic_table(
        self,
        database: str,
        schema: str,
        name: str
    ) -> SnowflakeResponse:
        """Get a specific dynamic table

        Args:
            database: Database name
            schema: Schema name
            name: Dynamic table name

        Returns:
            SnowflakeResponse with dynamic table details including target_lag and refresh info
        """
        url = self.base_url + f"/databases/{database}/schemas/{schema}/dynamic-tables/{name}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json() if response.text() else None
            return SnowflakeResponse(
                success=response.status < ERROR_STATUS_CODE,
                data=response_data,
                message="Successfully retrieved dynamic table" if response.status < ERROR_STATUS_CODE else f"Failed with status {response.status}"
            )
        except Exception as e:
            return SnowflakeResponse(success=False, error=str(e), message="Failed to get dynamic table")

    async def get_stage_file_stream(
        self,
        database: str,
        schema: str,
        stage: str,
        relative_path: str
    ) -> Any:
        """Get a stream of a file from a stage.
        
        Args:
            database: Database name
            schema: Schema name
            stage: Stage name
            relative_path: Path to file in stage
            
        Returns:
            Stream/Response object
        """
        # Constructing a request specific for file retrieval
        # Note: This path structure is hypothetical for the REST API wrapper pattern
        url = self.base_url + f"/databases/{database}/schemas/{schema}/stages/{stage}/files/{relative_path}"
        
        headers = self.http.headers.copy()
        
        # Ensure we accept binary stream
        headers["Accept"] = "*/*"
        
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        
        # We return the raw response context manager to allow streaming
        return await self.http.execute(request)

