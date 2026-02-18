"""
ClickHouse SDK DataSource - Auto-generated API wrapper

Generated from clickhouse-connect SDK method signatures.
Uses the clickhouse-connect SDK for direct ClickHouse interactions
and HTTP REST calls for ClickHouse Cloud Organization APIs.
All methods have explicit parameter signatures - NO Any type for params, NO **kwargs.
"""

import logging
from typing import Any, Dict, Iterable, Optional, Sequence, Union

from app.sources.client.clickhouse.clickhouse import (
    ClickHouseClient,
    ClickHouseResponse,
)
from app.sources.client.http.http_request import HTTPRequest

CLICKHOUSE_CLOUD_API_BASE_URL = 'https://api.clickhouse.cloud'

logger = logging.getLogger(__name__)


class ClickHouseDataSource:
    """clickhouse-connect SDK DataSource

    Provides wrapper methods for clickhouse-connect SDK operations:
    - Query operations (query, query_df, query_np, query_arrow)
    - Insert operations (insert, insert_df, insert_arrow)
    - Streaming operations (row/column/df/arrow streams)
    - Command execution (DDL/DML via command)
    - Raw data operations (raw_query, raw_insert, raw_stream)
    - Context and utility methods
    - Cloud Organization API operations (list/get/update orgs, activities, BYOC)

    All methods have explicit parameter signatures - NO **kwargs.
    Methods that return structured results return ClickHouseResponse objects.
    Methods that return DataFrames, Arrow tables, or streams return raw SDK results.
    Org API methods are async and use the HTTP client for Cloud Control Plane calls.
    """

    def __init__(self, client: ClickHouseClient, cloud_api_base_url: str = CLICKHOUSE_CLOUD_API_BASE_URL) -> None:
        """Initialize with ClickHouseClient.

        Args:
            client: ClickHouseClient instance with configured authentication
            cloud_api_base_url: Base URL for ClickHouse Cloud API (default: https://api.clickhouse.cloud)
        """
        self._client = client
        self._sdk = client.get_sdk()
        if self._sdk is None:
            raise ValueError('ClickHouse SDK client is not initialized')
        self._http_client = client.get_http_client()
        self._cloud_api_base_url = cloud_api_base_url

    def get_data_source(self) -> 'ClickHouseDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> ClickHouseClient:
        """Return the underlying ClickHouseClient."""
        return self._client

    def query(
        self,
        query: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        query_formats: Optional[Dict[str, str]] = None,
        column_formats: Optional[Dict[str, Union[str, Dict[str, str]]]] = None,
        encoding: Optional[str] = None,
        use_none: Optional[bool] = None,
        column_oriented: Optional[bool] = None,
        use_numpy: Optional[bool] = None,
        max_str_len: Optional[int] = None,
        query_tz: Optional[Union[str, object]] = None,
        column_tzs: Optional[Dict[str, Union[str, object]]] = None,
        utc_tz_aware: Optional[bool] = None,
        external_data: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> ClickHouseResponse:
        """Execute a SELECT or DESCRIBE query and return structured results

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values for parameterized queries
            settings: ClickHouse server settings for this query
            query_formats: Format overrides per ClickHouse type
            column_formats: Format overrides per column name
            encoding: Encoding for string columns
            use_none: Use None for ClickHouse NULL values
            column_oriented: Return results in column-oriented format
            use_numpy: Use numpy arrays for result columns
            max_str_len: Maximum string length for fixed string columns
            query_tz: Timezone for DateTime columns
            column_tzs: Per-column timezone overrides
            utc_tz_aware: Return timezone-aware UTC datetimes
            external_data: External data for the query
            transport_settings: HTTP transport settings

        Returns:
            ClickHouseResponse with operation result
        """
        kwargs: Dict[str, Any] = {'query': query}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if query_formats is not None:
            kwargs['query_formats'] = query_formats
        if column_formats is not None:
            kwargs['column_formats'] = column_formats
        if encoding is not None:
            kwargs['encoding'] = encoding
        if use_none is not None:
            kwargs['use_none'] = use_none
        if column_oriented is not None:
            kwargs['column_oriented'] = column_oriented
        if use_numpy is not None:
            kwargs['use_numpy'] = use_numpy
        if max_str_len is not None:
            kwargs['max_str_len'] = max_str_len
        if query_tz is not None:
            kwargs['query_tz'] = query_tz
        if column_tzs is not None:
            kwargs['column_tzs'] = column_tzs
        if utc_tz_aware is not None:
            kwargs['utc_tz_aware'] = utc_tz_aware
        if external_data is not None:
            kwargs['external_data'] = external_data
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            result = self._sdk.query(**kwargs)
            return ClickHouseResponse(
                success=True,
                data={
                    'result_rows': result.result_rows,
                    'column_names': list(result.column_names),
                    'query_id': result.query_id,
                    'summary': result.summary,
                },
                message='Successfully executed query'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute query')

    def query_df(
        self,
        query: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        query_formats: Optional[Dict[str, str]] = None,
        column_formats: Optional[Dict[str, str]] = None,
        encoding: Optional[str] = None,
        use_none: Optional[bool] = None,
        max_str_len: Optional[int] = None,
        use_na_values: Optional[bool] = None,
        query_tz: Optional[str] = None,
        column_tzs: Optional[Dict[str, Union[str, object]]] = None,
        utc_tz_aware: Optional[bool] = None,
        context: Optional[object] = None,
        external_data: Optional[object] = None,
        use_extended_dtypes: Optional[bool] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> object:
        """Execute a query and return results as a pandas DataFrame

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values
            settings: ClickHouse server settings
            query_formats: Format overrides per ClickHouse type
            column_formats: Format overrides per column name
            encoding: Encoding for string columns
            use_none: Use None for ClickHouse NULL values
            max_str_len: Maximum string length for fixed string columns
            use_na_values: Use pandas NA values for nulls
            query_tz: Timezone for DateTime columns
            column_tzs: Per-column timezone overrides
            utc_tz_aware: Return timezone-aware UTC datetimes
            context: Reusable QueryContext object
            external_data: External data for the query
            use_extended_dtypes: Use pandas extended dtypes
            transport_settings: HTTP transport settings

        Returns:
            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)
        """
        kwargs: Dict[str, Any] = {'query': query}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if query_formats is not None:
            kwargs['query_formats'] = query_formats
        if column_formats is not None:
            kwargs['column_formats'] = column_formats
        if encoding is not None:
            kwargs['encoding'] = encoding
        if use_none is not None:
            kwargs['use_none'] = use_none
        if max_str_len is not None:
            kwargs['max_str_len'] = max_str_len
        if use_na_values is not None:
            kwargs['use_na_values'] = use_na_values
        if query_tz is not None:
            kwargs['query_tz'] = query_tz
        if column_tzs is not None:
            kwargs['column_tzs'] = column_tzs
        if utc_tz_aware is not None:
            kwargs['utc_tz_aware'] = utc_tz_aware
        if context is not None:
            kwargs['context'] = context
        if external_data is not None:
            kwargs['external_data'] = external_data
        if use_extended_dtypes is not None:
            kwargs['use_extended_dtypes'] = use_extended_dtypes
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            return self._sdk.query_df(**kwargs)
        except Exception as e:
            raise RuntimeError(f'Failed to execute query_df: {str(e)}') from e

    def query_np(
        self,
        query: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        query_formats: Optional[Dict[str, str]] = None,
        column_formats: Optional[Dict[str, str]] = None,
        encoding: Optional[str] = None,
        use_none: Optional[bool] = None,
        max_str_len: Optional[int] = None,
        context: Optional[object] = None,
        external_data: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> object:
        """Execute a query and return results as a numpy ndarray

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values
            settings: ClickHouse server settings
            query_formats: Format overrides per ClickHouse type
            column_formats: Format overrides per column name
            encoding: Encoding for string columns
            use_none: Use None for ClickHouse NULL values
            max_str_len: Maximum string length for fixed string columns
            context: Reusable QueryContext object
            external_data: External data for the query
            transport_settings: HTTP transport settings

        Returns:
            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)
        """
        kwargs: Dict[str, Any] = {'query': query}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if query_formats is not None:
            kwargs['query_formats'] = query_formats
        if column_formats is not None:
            kwargs['column_formats'] = column_formats
        if encoding is not None:
            kwargs['encoding'] = encoding
        if use_none is not None:
            kwargs['use_none'] = use_none
        if max_str_len is not None:
            kwargs['max_str_len'] = max_str_len
        if context is not None:
            kwargs['context'] = context
        if external_data is not None:
            kwargs['external_data'] = external_data
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            return self._sdk.query_np(**kwargs)
        except Exception as e:
            raise RuntimeError(f'Failed to execute query_np: {str(e)}') from e

    def query_arrow(
        self,
        query: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        use_strings: Optional[bool] = None,
        external_data: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> object:
        """Execute a query and return results as a PyArrow Table

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values
            settings: ClickHouse server settings
            use_strings: Return ClickHouse String type as Arrow string (vs binary)
            external_data: External data for the query
            transport_settings: HTTP transport settings

        Returns:
            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)
        """
        kwargs: Dict[str, Any] = {'query': query}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if use_strings is not None:
            kwargs['use_strings'] = use_strings
        if external_data is not None:
            kwargs['external_data'] = external_data
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            return self._sdk.query_arrow(**kwargs)
        except Exception as e:
            raise RuntimeError(f'Failed to execute query_arrow: {str(e)}') from e

    def query_df_arrow(
        self,
        query: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        use_strings: Optional[bool] = None,
        external_data: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None,
        dataframe_library: Optional[str] = None
    ) -> object:
        """Execute a query and return results as a DataFrame with PyArrow dtype backend

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values
            settings: ClickHouse server settings
            use_strings: Return ClickHouse String type as Arrow string
            external_data: External data for the query
            transport_settings: HTTP transport settings
            dataframe_library: DataFrame library to use: "pandas" or "polars"

        Returns:
            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)
        """
        kwargs: Dict[str, Any] = {'query': query}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if use_strings is not None:
            kwargs['use_strings'] = use_strings
        if external_data is not None:
            kwargs['external_data'] = external_data
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings
        if dataframe_library is not None:
            kwargs['dataframe_library'] = dataframe_library

        try:
            return self._sdk.query_df_arrow(**kwargs)
        except Exception as e:
            raise RuntimeError(f'Failed to execute query_df_arrow: {str(e)}') from e

    def raw_query(
        self,
        query: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        fmt: Optional[str] = None,
        use_database: Optional[bool] = None,
        external_data: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> ClickHouseResponse:
        """Execute a query and return raw bytes in the specified ClickHouse format

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values
            settings: ClickHouse server settings
            fmt: ClickHouse output format (e.g. TabSeparated, JSON, CSV)
            use_database: Prepend USE database before query
            external_data: External data for the query
            transport_settings: HTTP transport settings

        Returns:
            ClickHouseResponse with operation result
        """
        kwargs: Dict[str, Any] = {'query': query}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if fmt is not None:
            kwargs['fmt'] = fmt
        if use_database is not None:
            kwargs['use_database'] = use_database
        if external_data is not None:
            kwargs['external_data'] = external_data
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            result = self._sdk.raw_query(**kwargs)
            return ClickHouseResponse(
                success=True,
                data={'raw': result, 'size': len(result)},
                message='Successfully executed raw_query'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute raw_query')

    def command(
        self,
        cmd: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        data: Optional[Union[str, bytes]] = None,
        settings: Optional[Dict[str, Any]] = None,
        use_database: Optional[bool] = None,
        external_data: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> ClickHouseResponse:
        """Execute a DDL or DML command (CREATE, DROP, ALTER, SET, etc.) and return the result

        Args:
            cmd: ClickHouse DDL/DML command string
            parameters: Command parameter values
            data: Additional data for the command (e.g. INSERT data)
            settings: ClickHouse server settings
            use_database: Prepend USE database before command
            external_data: External data for the command
            transport_settings: HTTP transport settings

        Returns:
            ClickHouseResponse with operation result
        """
        kwargs: Dict[str, Any] = {'cmd': cmd}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if data is not None:
            kwargs['data'] = data
        if settings is not None:
            kwargs['settings'] = settings
        if use_database is not None:
            kwargs['use_database'] = use_database
        if external_data is not None:
            kwargs['external_data'] = external_data
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            result = self._sdk.command(**kwargs)
            return ClickHouseResponse(
                success=True,
                data={'result': result},
                message='Successfully executed command'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute command')

    def insert(
        self,
        table: str,
        data: Sequence[Sequence[Any]],
        column_names: Optional[Union[str, Iterable[str]]] = None,
        database: Optional[str] = None,
        column_types: Optional[Sequence[Any]] = None,
        column_type_names: Optional[Sequence[str]] = None,
        column_oriented: Optional[bool] = None,
        settings: Optional[Dict[str, Any]] = None,
        context: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> ClickHouseResponse:
        """Insert multiple rows of Python objects into a ClickHouse table

        Args:
            table: Target table name
            data: Row data as list of lists/tuples
            column_names: Column names for the insert (default: * for all columns)
            database: Target database (overrides client default)
            column_types: ClickHouse column type objects
            column_type_names: ClickHouse column type names as strings
            column_oriented: Data is column-oriented (list of columns, not list of rows)
            settings: ClickHouse server settings
            context: Reusable InsertContext object
            transport_settings: HTTP transport settings

        Returns:
            ClickHouseResponse with operation result
        """
        kwargs: Dict[str, Any] = {'table': table, 'data': data}
        if column_names is not None:
            kwargs['column_names'] = column_names
        if database is not None:
            kwargs['database'] = database
        if column_types is not None:
            kwargs['column_types'] = column_types
        if column_type_names is not None:
            kwargs['column_type_names'] = column_type_names
        if column_oriented is not None:
            kwargs['column_oriented'] = column_oriented
        if settings is not None:
            kwargs['settings'] = settings
        if context is not None:
            kwargs['context'] = context
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            summary = self._sdk.insert(**kwargs)
            summary_data = {attr: getattr(summary, attr) for attr in ['written_rows', 'written_bytes', 'query_id', 'summary'] if hasattr(summary, attr)}
            return ClickHouseResponse(
                success=True,
                data=summary_data,
                message='Successfully executed insert'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute insert')

    def insert_df(
        self,
        table: str,
        df: object,
        database: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
        column_names: Optional[Sequence[str]] = None,
        column_types: Optional[Sequence[Any]] = None,
        column_type_names: Optional[Sequence[str]] = None,
        context: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> ClickHouseResponse:
        """Insert a pandas DataFrame into a ClickHouse table

        Args:
            table: Target table name
            df: pandas DataFrame to insert
            database: Target database (overrides client default)
            settings: ClickHouse server settings
            column_names: Column names for the insert
            column_types: ClickHouse column type objects
            column_type_names: ClickHouse column type names as strings
            context: Reusable InsertContext object
            transport_settings: HTTP transport settings

        Returns:
            ClickHouseResponse with operation result
        """
        kwargs: Dict[str, Any] = {'table': table, 'df': df}
        if database is not None:
            kwargs['database'] = database
        if settings is not None:
            kwargs['settings'] = settings
        if column_names is not None:
            kwargs['column_names'] = column_names
        if column_types is not None:
            kwargs['column_types'] = column_types
        if column_type_names is not None:
            kwargs['column_type_names'] = column_type_names
        if context is not None:
            kwargs['context'] = context
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            summary = self._sdk.insert_df(**kwargs)
            summary_data = {attr: getattr(summary, attr) for attr in ['written_rows', 'written_bytes', 'query_id', 'summary'] if hasattr(summary, attr)}
            return ClickHouseResponse(
                success=True,
                data=summary_data,
                message='Successfully executed insert_df'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute insert_df')

    def insert_arrow(
        self,
        table: str,
        arrow_table: object,
        database: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> ClickHouseResponse:
        """Insert a PyArrow Table into a ClickHouse table using Arrow format

        Args:
            table: Target table name
            arrow_table: PyArrow Table to insert
            database: Target database (overrides client default)
            settings: ClickHouse server settings
            transport_settings: HTTP transport settings

        Returns:
            ClickHouseResponse with operation result
        """
        kwargs: Dict[str, Any] = {'table': table, 'arrow_table': arrow_table}
        if database is not None:
            kwargs['database'] = database
        if settings is not None:
            kwargs['settings'] = settings
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            summary = self._sdk.insert_arrow(**kwargs)
            summary_data = {attr: getattr(summary, attr) for attr in ['written_rows', 'written_bytes', 'query_id', 'summary'] if hasattr(summary, attr)}
            return ClickHouseResponse(
                success=True,
                data=summary_data,
                message='Successfully executed insert_arrow'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute insert_arrow')

    def insert_df_arrow(
        self,
        table: str,
        df: object,
        database: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> ClickHouseResponse:
        """Insert a pandas/polars DataFrame using the Arrow format for better type support

        Args:
            table: Target table name
            df: pandas or polars DataFrame to insert
            database: Target database (overrides client default)
            settings: ClickHouse server settings
            transport_settings: HTTP transport settings

        Returns:
            ClickHouseResponse with operation result
        """
        kwargs: Dict[str, Any] = {'table': table, 'df': df}
        if database is not None:
            kwargs['database'] = database
        if settings is not None:
            kwargs['settings'] = settings
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            summary = self._sdk.insert_df_arrow(**kwargs)
            summary_data = {attr: getattr(summary, attr) for attr in ['written_rows', 'written_bytes', 'query_id', 'summary'] if hasattr(summary, attr)}
            return ClickHouseResponse(
                success=True,
                data=summary_data,
                message='Successfully executed insert_df_arrow'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute insert_df_arrow')

    def raw_insert(
        self,
        table: str,
        column_names: Optional[Sequence[str]] = None,
        insert_block: Optional[Union[str, bytes, object]] = None,
        settings: Optional[Dict[str, Any]] = None,
        fmt: Optional[str] = None,
        compression: Optional[str] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> ClickHouseResponse:
        """Insert pre-formatted raw data (CSV, TSV, JSON, etc.) into a ClickHouse table

        Args:
            table: Target table name
            column_names: Column names for the insert
            insert_block: Raw data block to insert (str, bytes, generator, or BinaryIO)
            settings: ClickHouse server settings
            fmt: ClickHouse input format (e.g. TabSeparated, CSV, JSONEachRow)
            compression: Compression codec: lz4, zstd, brotli, or gzip
            transport_settings: HTTP transport settings

        Returns:
            ClickHouseResponse with operation result
        """
        kwargs: Dict[str, Any] = {'table': table}
        if column_names is not None:
            kwargs['column_names'] = column_names
        if insert_block is not None:
            kwargs['insert_block'] = insert_block
        if settings is not None:
            kwargs['settings'] = settings
        if fmt is not None:
            kwargs['fmt'] = fmt
        if compression is not None:
            kwargs['compression'] = compression
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            summary = self._sdk.raw_insert(**kwargs)
            summary_data = {attr: getattr(summary, attr) for attr in ['written_rows', 'written_bytes', 'query_id', 'summary'] if hasattr(summary, attr)}
            return ClickHouseResponse(
                success=True,
                data=summary_data,
                message='Successfully executed raw_insert'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute raw_insert')

    def query_column_block_stream(
        self,
        query: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        query_formats: Optional[Dict[str, str]] = None,
        column_formats: Optional[Dict[str, Union[str, Dict[str, str]]]] = None,
        encoding: Optional[str] = None,
        use_none: Optional[bool] = None,
        context: Optional[object] = None,
        query_tz: Optional[Union[str, object]] = None,
        column_tzs: Optional[Dict[str, Union[str, object]]] = None,
        utc_tz_aware: Optional[bool] = None,
        external_data: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> object:
        """Execute a query and stream results as column-oriented blocks for memory-efficient processing

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values
            settings: ClickHouse server settings
            query_formats: Format overrides per ClickHouse type
            column_formats: Format overrides per column name
            encoding: Encoding for string columns
            use_none: Use None for ClickHouse NULL values
            context: Reusable QueryContext object
            query_tz: Timezone for DateTime columns
            column_tzs: Per-column timezone overrides
            utc_tz_aware: Return timezone-aware UTC datetimes
            external_data: External data for the query
            transport_settings: HTTP transport settings

        Returns:
            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)
        """
        kwargs: Dict[str, Any] = {'query': query}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if query_formats is not None:
            kwargs['query_formats'] = query_formats
        if column_formats is not None:
            kwargs['column_formats'] = column_formats
        if encoding is not None:
            kwargs['encoding'] = encoding
        if use_none is not None:
            kwargs['use_none'] = use_none
        if context is not None:
            kwargs['context'] = context
        if query_tz is not None:
            kwargs['query_tz'] = query_tz
        if column_tzs is not None:
            kwargs['column_tzs'] = column_tzs
        if utc_tz_aware is not None:
            kwargs['utc_tz_aware'] = utc_tz_aware
        if external_data is not None:
            kwargs['external_data'] = external_data
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            return self._sdk.query_column_block_stream(**kwargs)
        except Exception as e:
            raise RuntimeError(f'Failed to execute query_column_block_stream: {str(e)}') from e

    def query_row_block_stream(
        self,
        query: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        query_formats: Optional[Dict[str, str]] = None,
        column_formats: Optional[Dict[str, Union[str, Dict[str, str]]]] = None,
        encoding: Optional[str] = None,
        use_none: Optional[bool] = None,
        context: Optional[object] = None,
        query_tz: Optional[Union[str, object]] = None,
        column_tzs: Optional[Dict[str, Union[str, object]]] = None,
        utc_tz_aware: Optional[bool] = None,
        external_data: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> object:
        """Execute a query and stream results as row-oriented blocks for memory-efficient processing

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values
            settings: ClickHouse server settings
            query_formats: Format overrides per ClickHouse type
            column_formats: Format overrides per column name
            encoding: Encoding for string columns
            use_none: Use None for ClickHouse NULL values
            context: Reusable QueryContext object
            query_tz: Timezone for DateTime columns
            column_tzs: Per-column timezone overrides
            utc_tz_aware: Return timezone-aware UTC datetimes
            external_data: External data for the query
            transport_settings: HTTP transport settings

        Returns:
            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)
        """
        kwargs: Dict[str, Any] = {'query': query}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if query_formats is not None:
            kwargs['query_formats'] = query_formats
        if column_formats is not None:
            kwargs['column_formats'] = column_formats
        if encoding is not None:
            kwargs['encoding'] = encoding
        if use_none is not None:
            kwargs['use_none'] = use_none
        if context is not None:
            kwargs['context'] = context
        if query_tz is not None:
            kwargs['query_tz'] = query_tz
        if column_tzs is not None:
            kwargs['column_tzs'] = column_tzs
        if utc_tz_aware is not None:
            kwargs['utc_tz_aware'] = utc_tz_aware
        if external_data is not None:
            kwargs['external_data'] = external_data
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            return self._sdk.query_row_block_stream(**kwargs)
        except Exception as e:
            raise RuntimeError(f'Failed to execute query_row_block_stream: {str(e)}') from e

    def query_rows_stream(
        self,
        query: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        query_formats: Optional[Dict[str, str]] = None,
        column_formats: Optional[Dict[str, Union[str, Dict[str, str]]]] = None,
        encoding: Optional[str] = None,
        use_none: Optional[bool] = None,
        context: Optional[object] = None,
        query_tz: Optional[Union[str, object]] = None,
        column_tzs: Optional[Dict[str, Union[str, object]]] = None,
        utc_tz_aware: Optional[bool] = None,
        external_data: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> object:
        """Execute a query and stream results as individual rows for memory-efficient processing

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values
            settings: ClickHouse server settings
            query_formats: Format overrides per ClickHouse type
            column_formats: Format overrides per column name
            encoding: Encoding for string columns
            use_none: Use None for ClickHouse NULL values
            context: Reusable QueryContext object
            query_tz: Timezone for DateTime columns
            column_tzs: Per-column timezone overrides
            utc_tz_aware: Return timezone-aware UTC datetimes
            external_data: External data for the query
            transport_settings: HTTP transport settings

        Returns:
            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)
        """
        kwargs: Dict[str, Any] = {'query': query}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if query_formats is not None:
            kwargs['query_formats'] = query_formats
        if column_formats is not None:
            kwargs['column_formats'] = column_formats
        if encoding is not None:
            kwargs['encoding'] = encoding
        if use_none is not None:
            kwargs['use_none'] = use_none
        if context is not None:
            kwargs['context'] = context
        if query_tz is not None:
            kwargs['query_tz'] = query_tz
        if column_tzs is not None:
            kwargs['column_tzs'] = column_tzs
        if utc_tz_aware is not None:
            kwargs['utc_tz_aware'] = utc_tz_aware
        if external_data is not None:
            kwargs['external_data'] = external_data
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            return self._sdk.query_rows_stream(**kwargs)
        except Exception as e:
            raise RuntimeError(f'Failed to execute query_rows_stream: {str(e)}') from e

    def query_df_stream(
        self,
        query: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        query_formats: Optional[Dict[str, str]] = None,
        column_formats: Optional[Dict[str, str]] = None,
        encoding: Optional[str] = None,
        use_none: Optional[bool] = None,
        max_str_len: Optional[int] = None,
        use_na_values: Optional[bool] = None,
        query_tz: Optional[str] = None,
        column_tzs: Optional[Dict[str, Union[str, object]]] = None,
        utc_tz_aware: Optional[bool] = None,
        context: Optional[object] = None,
        external_data: Optional[object] = None,
        use_extended_dtypes: Optional[bool] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> object:
        """Execute a query and stream results as pandas DataFrames per block

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values
            settings: ClickHouse server settings
            query_formats: Format overrides per ClickHouse type
            column_formats: Format overrides per column name
            encoding: Encoding for string columns
            use_none: Use None for ClickHouse NULL values
            max_str_len: Maximum string length for fixed string columns
            use_na_values: Use pandas NA values for nulls
            query_tz: Timezone for DateTime columns
            column_tzs: Per-column timezone overrides
            utc_tz_aware: Return timezone-aware UTC datetimes
            context: Reusable QueryContext object
            external_data: External data for the query
            use_extended_dtypes: Use pandas extended dtypes
            transport_settings: HTTP transport settings

        Returns:
            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)
        """
        kwargs: Dict[str, Any] = {'query': query}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if query_formats is not None:
            kwargs['query_formats'] = query_formats
        if column_formats is not None:
            kwargs['column_formats'] = column_formats
        if encoding is not None:
            kwargs['encoding'] = encoding
        if use_none is not None:
            kwargs['use_none'] = use_none
        if max_str_len is not None:
            kwargs['max_str_len'] = max_str_len
        if use_na_values is not None:
            kwargs['use_na_values'] = use_na_values
        if query_tz is not None:
            kwargs['query_tz'] = query_tz
        if column_tzs is not None:
            kwargs['column_tzs'] = column_tzs
        if utc_tz_aware is not None:
            kwargs['utc_tz_aware'] = utc_tz_aware
        if context is not None:
            kwargs['context'] = context
        if external_data is not None:
            kwargs['external_data'] = external_data
        if use_extended_dtypes is not None:
            kwargs['use_extended_dtypes'] = use_extended_dtypes
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            return self._sdk.query_df_stream(**kwargs)
        except Exception as e:
            raise RuntimeError(f'Failed to execute query_df_stream: {str(e)}') from e

    def query_np_stream(
        self,
        query: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        query_formats: Optional[Dict[str, str]] = None,
        column_formats: Optional[Dict[str, str]] = None,
        encoding: Optional[str] = None,
        use_none: Optional[bool] = None,
        max_str_len: Optional[int] = None,
        context: Optional[object] = None,
        external_data: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> object:
        """Execute a query and stream results as numpy arrays per block

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values
            settings: ClickHouse server settings
            query_formats: Format overrides per ClickHouse type
            column_formats: Format overrides per column name
            encoding: Encoding for string columns
            use_none: Use None for ClickHouse NULL values
            max_str_len: Maximum string length for fixed string columns
            context: Reusable QueryContext object
            external_data: External data for the query
            transport_settings: HTTP transport settings

        Returns:
            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)
        """
        kwargs: Dict[str, Any] = {'query': query}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if query_formats is not None:
            kwargs['query_formats'] = query_formats
        if column_formats is not None:
            kwargs['column_formats'] = column_formats
        if encoding is not None:
            kwargs['encoding'] = encoding
        if use_none is not None:
            kwargs['use_none'] = use_none
        if max_str_len is not None:
            kwargs['max_str_len'] = max_str_len
        if context is not None:
            kwargs['context'] = context
        if external_data is not None:
            kwargs['external_data'] = external_data
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            return self._sdk.query_np_stream(**kwargs)
        except Exception as e:
            raise RuntimeError(f'Failed to execute query_np_stream: {str(e)}') from e

    def query_arrow_stream(
        self,
        query: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        use_strings: Optional[bool] = None,
        external_data: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> object:
        """Execute a query and stream results as PyArrow Tables per block

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values
            settings: ClickHouse server settings
            use_strings: Return ClickHouse String type as Arrow string
            external_data: External data for the query
            transport_settings: HTTP transport settings

        Returns:
            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)
        """
        kwargs: Dict[str, Any] = {'query': query}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if use_strings is not None:
            kwargs['use_strings'] = use_strings
        if external_data is not None:
            kwargs['external_data'] = external_data
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            return self._sdk.query_arrow_stream(**kwargs)
        except Exception as e:
            raise RuntimeError(f'Failed to execute query_arrow_stream: {str(e)}') from e

    def query_df_arrow_stream(
        self,
        query: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        use_strings: Optional[bool] = None,
        external_data: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None,
        dataframe_library: Optional[str] = None
    ) -> object:
        """Execute a query and stream results as Arrow-backed DataFrames per block

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values
            settings: ClickHouse server settings
            use_strings: Return ClickHouse String type as Arrow string
            external_data: External data for the query
            transport_settings: HTTP transport settings
            dataframe_library: DataFrame library to use: "pandas" or "polars"

        Returns:
            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)
        """
        kwargs: Dict[str, Any] = {'query': query}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if use_strings is not None:
            kwargs['use_strings'] = use_strings
        if external_data is not None:
            kwargs['external_data'] = external_data
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings
        if dataframe_library is not None:
            kwargs['dataframe_library'] = dataframe_library

        try:
            return self._sdk.query_df_arrow_stream(**kwargs)
        except Exception as e:
            raise RuntimeError(f'Failed to execute query_df_arrow_stream: {str(e)}') from e

    def raw_stream(
        self,
        query: str,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        fmt: Optional[str] = None,
        use_database: Optional[bool] = None,
        external_data: Optional[object] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> object:
        """Execute a query and return a raw IO stream of bytes in the specified ClickHouse format

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values
            settings: ClickHouse server settings
            fmt: ClickHouse output format (e.g. TabSeparated, JSON, CSV)
            use_database: Prepend USE database before query
            external_data: External data for the query
            transport_settings: HTTP transport settings

        Returns:
            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)
        """
        kwargs: Dict[str, Any] = {'query': query}
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if fmt is not None:
            kwargs['fmt'] = fmt
        if use_database is not None:
            kwargs['use_database'] = use_database
        if external_data is not None:
            kwargs['external_data'] = external_data
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            return self._sdk.raw_stream(**kwargs)
        except Exception as e:
            raise RuntimeError(f'Failed to execute raw_stream: {str(e)}') from e

    def create_query_context(
        self,
        query: Optional[str] = None,
        parameters: Optional[Union[Sequence, Dict[str, Any]]] = None,
        settings: Optional[Dict[str, Any]] = None,
        query_formats: Optional[Dict[str, str]] = None,
        column_formats: Optional[Dict[str, Union[str, Dict[str, str]]]] = None,
        encoding: Optional[str] = None,
        use_none: Optional[bool] = None,
        column_oriented: Optional[bool] = None,
        use_numpy: Optional[bool] = None,
        max_str_len: Optional[int] = None,
        context: Optional[object] = None,
        query_tz: Optional[Union[str, object]] = None,
        column_tzs: Optional[Dict[str, Union[str, object]]] = None,
        utc_tz_aware: Optional[bool] = None,
        use_na_values: Optional[bool] = None,
        streaming: Optional[bool] = None,
        as_pandas: Optional[bool] = None,
        external_data: Optional[object] = None,
        use_extended_dtypes: Optional[bool] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> object:
        """Build a reusable QueryContext for repeated queries with the same configuration

        Args:
            query: ClickHouse SQL query string
            parameters: Query parameter values
            settings: ClickHouse server settings
            query_formats: Format overrides per ClickHouse type
            column_formats: Format overrides per column name
            encoding: Encoding for string columns
            use_none: Use None for ClickHouse NULL values
            column_oriented: Return results in column-oriented format
            use_numpy: Use numpy arrays for result columns
            max_str_len: Maximum string length for fixed string columns
            context: Existing QueryContext to copy/modify
            query_tz: Timezone for DateTime columns
            column_tzs: Per-column timezone overrides
            utc_tz_aware: Return timezone-aware UTC datetimes
            use_na_values: Use pandas NA values for nulls
            streaming: Configure context for streaming queries
            as_pandas: Configure context for pandas DataFrame output
            external_data: External data for the query
            use_extended_dtypes: Use pandas extended dtypes
            transport_settings: HTTP transport settings

        Returns:
            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)
        """
        kwargs: Dict[str, Any] = {}
        if query is not None:
            kwargs['query'] = query
        if parameters is not None:
            kwargs['parameters'] = parameters
        if settings is not None:
            kwargs['settings'] = settings
        if query_formats is not None:
            kwargs['query_formats'] = query_formats
        if column_formats is not None:
            kwargs['column_formats'] = column_formats
        if encoding is not None:
            kwargs['encoding'] = encoding
        if use_none is not None:
            kwargs['use_none'] = use_none
        if column_oriented is not None:
            kwargs['column_oriented'] = column_oriented
        if use_numpy is not None:
            kwargs['use_numpy'] = use_numpy
        if max_str_len is not None:
            kwargs['max_str_len'] = max_str_len
        if context is not None:
            kwargs['context'] = context
        if query_tz is not None:
            kwargs['query_tz'] = query_tz
        if column_tzs is not None:
            kwargs['column_tzs'] = column_tzs
        if utc_tz_aware is not None:
            kwargs['utc_tz_aware'] = utc_tz_aware
        if use_na_values is not None:
            kwargs['use_na_values'] = use_na_values
        if streaming is not None:
            kwargs['streaming'] = streaming
        if as_pandas is not None:
            kwargs['as_pandas'] = as_pandas
        if external_data is not None:
            kwargs['external_data'] = external_data
        if use_extended_dtypes is not None:
            kwargs['use_extended_dtypes'] = use_extended_dtypes
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            return self._sdk.create_query_context(**kwargs)
        except Exception as e:
            raise RuntimeError(f'Failed to execute create_query_context: {str(e)}') from e

    def create_insert_context(
        self,
        table: str,
        column_names: Optional[Union[str, Sequence[str]]] = None,
        database: Optional[str] = None,
        column_types: Optional[Sequence[Any]] = None,
        column_type_names: Optional[Sequence[str]] = None,
        column_oriented: Optional[bool] = None,
        settings: Optional[Dict[str, Any]] = None,
        data: Optional[Sequence[Sequence[Any]]] = None,
        transport_settings: Optional[Dict[str, str]] = None
    ) -> object:
        """Build a reusable InsertContext for repeated inserts to the same table

        Args:
            table: Target table name
            column_names: Column names for the insert
            database: Target database (overrides client default)
            column_types: ClickHouse column type objects
            column_type_names: ClickHouse column type names as strings
            column_oriented: Data is column-oriented
            settings: ClickHouse server settings
            data: Initial data for the insert context
            transport_settings: HTTP transport settings

        Returns:
            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)
        """
        kwargs: Dict[str, Any] = {'table': table}
        if column_names is not None:
            kwargs['column_names'] = column_names
        if database is not None:
            kwargs['database'] = database
        if column_types is not None:
            kwargs['column_types'] = column_types
        if column_type_names is not None:
            kwargs['column_type_names'] = column_type_names
        if column_oriented is not None:
            kwargs['column_oriented'] = column_oriented
        if settings is not None:
            kwargs['settings'] = settings
        if data is not None:
            kwargs['data'] = data
        if transport_settings is not None:
            kwargs['transport_settings'] = transport_settings

        try:
            return self._sdk.create_insert_context(**kwargs)
        except Exception as e:
            raise RuntimeError(f'Failed to execute create_insert_context: {str(e)}') from e

    def data_insert(
        self,
        context: object
    ) -> ClickHouseResponse:
        """Execute an insert using a pre-built InsertContext

        Args:
            context: InsertContext with table, columns, and data configured

        Returns:
            ClickHouseResponse with operation result
        """
        kwargs: Dict[str, Any] = {'context': context}

        try:
            summary = self._sdk.data_insert(**kwargs)
            summary_data = {attr: getattr(summary, attr) for attr in ['written_rows', 'written_bytes', 'query_id', 'summary'] if hasattr(summary, attr)}
            return ClickHouseResponse(
                success=True,
                data=summary_data,
                message='Successfully executed data_insert'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute data_insert')

    def ping(
        self
    ) -> ClickHouseResponse:
        """Validate the ClickHouse connection is alive

        Returns:
            ClickHouseResponse with operation result
        """
        try:
            self._sdk.ping()
            return ClickHouseResponse(
                success=True,
                message='Connection is alive'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Connection ping failed')

    def min_version(
        self,
        version_str: str
    ) -> ClickHouseResponse:
        """Check if the connected ClickHouse server meets a minimum version requirement

        Args:
            version_str: Minimum version string to check (e.g. "22.3")

        Returns:
            ClickHouseResponse with operation result
        """
        kwargs: Dict[str, Any] = {'version_str': version_str}

        try:
            result = self._sdk.min_version(**kwargs)
            return ClickHouseResponse(
                success=True,
                data={'result': result},
                message='Successfully executed min_version'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute min_version')

    def close(
        self
    ) -> ClickHouseResponse:
        """Close the ClickHouse client connection and release resources

        Returns:
            ClickHouseResponse with operation result
        """
        try:
            self._sdk.close()
            return ClickHouseResponse(
                success=True,
                message='Successfully executed close'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute close')

    # ================================================================================
    # CLOUD ORGANIZATION API METHODS (async, HTTP-based)
    # ================================================================================

    async def list_organizations(
        self
    ) -> ClickHouseResponse:
        """List organizations associated with the API key

        Returns:
            ClickHouseResponse with operation result
        """
        query_params = {}

        url = f"{self._cloud_api_base_url}/v1/organizations"

        request = HTTPRequest(
            url=url,
            method='GET',
            query=query_params,
        )

        try:
            response = await self._http_client.execute(request)
            response.raise_for_status()
            data = response.json()
            return ClickHouseResponse(
                success=True,
                data=data.get('result', data),
                message='Successfully called list_organizations'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to call list_organizations')

    async def get_organization(
        self,
        organization_id: str
    ) -> ClickHouseResponse:
        """Get organization details by ID

        Args:
            organization_id: Organization ID

        Returns:
            ClickHouseResponse with operation result
        """
        path_params = {'organizationId': organization_id}
        query_params = {}

        url = self._cloud_api_base_url + '/v1/organizations/{organizationId}'
        url = url.format(**path_params)

        request = HTTPRequest(
            url=url,
            method='GET',
            query=query_params,
        )

        try:
            response = await self._http_client.execute(request)
            response.raise_for_status()
            data = response.json()
            return ClickHouseResponse(
                success=True,
                data=data.get('result', data),
                message='Successfully called get_organization'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to call get_organization')

    async def update_organization(
        self,
        organization_id: str,
        name: Optional[str] = None,
        private_endpoints: Optional[object] = None,
        core_dumps: Optional[object] = None
    ) -> ClickHouseResponse:
        """Update organization name, private endpoints, or core dumps settings

        Args:
            organization_id: Organization ID
            name: New organization name
            private_endpoints: Private endpoints configuration
            core_dumps: Core dumps configuration

        Returns:
            ClickHouseResponse with operation result
        """
        path_params = {'organizationId': organization_id}
        query_params = {}
        body = {}
        if name is not None:
            body['name'] = name
        if private_endpoints is not None:
            body['private_endpoints'] = private_endpoints
        if core_dumps is not None:
            body['core_dumps'] = core_dumps

        url = self._cloud_api_base_url + '/v1/organizations/{organizationId}'
        url = url.format(**path_params)

        request = HTTPRequest(
            url=url,
            method='PATCH',
            query=query_params,
            body=body if body else None,
        )

        try:
            response = await self._http_client.execute(request)
            response.raise_for_status()
            data = response.json()
            return ClickHouseResponse(
                success=True,
                data=data.get('result', data),
                message='Successfully called update_organization'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to call update_organization')

    async def list_organization_activities(
        self,
        organization_id: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None
    ) -> ClickHouseResponse:
        """List activities for an organization with optional date filters

        Args:
            organization_id: Organization ID
            from_date: Start date filter (ISO 8601 format)
            to_date: End date filter (ISO 8601 format)

        Returns:
            ClickHouseResponse with operation result
        """
        path_params = {'organizationId': organization_id}
        query_params = {}
        if from_date is not None:
            query_params['from'] = from_date
        if to_date is not None:
            query_params['to'] = to_date

        url = self._cloud_api_base_url + '/v1/organizations/{organizationId}/activities'
        url = url.format(**path_params)

        request = HTTPRequest(
            url=url,
            method='GET',
            query=query_params,
        )

        try:
            response = await self._http_client.execute(request)
            response.raise_for_status()
            data = response.json()
            return ClickHouseResponse(
                success=True,
                data=data.get('result', data),
                message='Successfully called list_organization_activities'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to call list_organization_activities')

    async def get_organization_activity(
        self,
        organization_id: str,
        activity_id: str
    ) -> ClickHouseResponse:
        """Get a single organization activity by ID

        Args:
            organization_id: Organization ID
            activity_id: Activity ID

        Returns:
            ClickHouseResponse with operation result
        """
        path_params = {'organizationId': organization_id, 'activityId': activity_id}
        query_params = {}

        url = self._cloud_api_base_url + '/v1/organizations/{organizationId}/activities/{activityId}'
        url = url.format(**path_params)

        request = HTTPRequest(
            url=url,
            method='GET',
            query=query_params,
        )

        try:
            response = await self._http_client.execute(request)
            response.raise_for_status()
            data = response.json()
            return ClickHouseResponse(
                success=True,
                data=data.get('result', data),
                message='Successfully called get_organization_activity'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to call get_organization_activity')

    async def get_private_endpoint_config(
        self,
        organization_id: str
    ) -> ClickHouseResponse:
        """Get private endpoint configuration for an organization (deprecated)

        .. deprecated:: This endpoint is deprecated.

        Args:
            organization_id: Organization ID

        Returns:
            ClickHouseResponse with operation result
        """
        path_params = {'organizationId': organization_id}
        query_params = {}

        url = self._cloud_api_base_url + '/v1/organizations/{organizationId}/privateEndpointConfig'
        url = url.format(**path_params)

        request = HTTPRequest(
            url=url,
            method='GET',
            query=query_params,
        )

        try:
            response = await self._http_client.execute(request)
            response.raise_for_status()
            data = response.json()
            return ClickHouseResponse(
                success=True,
                data=data.get('result', data),
                message='Successfully called get_private_endpoint_config'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to call get_private_endpoint_config')

    async def create_byoc_infrastructure(
        self,
        organization_id: str,
        cloud_provider: Optional[str] = None,
        region: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None
    ) -> ClickHouseResponse:
        """Create a BYOC (Bring Your Own Cloud) infrastructure for an organization

        Args:
            organization_id: Organization ID
            cloud_provider: Cloud provider (e.g. aws, gcp, azure)
            region: Cloud region for the infrastructure
            config: BYOC infrastructure configuration

        Returns:
            ClickHouseResponse with operation result
        """
        path_params = {'organizationId': organization_id}
        query_params = {}
        body = {}
        if cloud_provider is not None:
            body['cloud_provider'] = cloud_provider
        if region is not None:
            body['region'] = region
        if config is not None:
            body['config'] = config

        url = self._cloud_api_base_url + '/v1/organizations/{organizationId}/byocInfrastructure'
        url = url.format(**path_params)

        request = HTTPRequest(
            url=url,
            method='POST',
            query=query_params,
            body=body if body else None,
        )

        try:
            response = await self._http_client.execute(request)
            response.raise_for_status()
            data = response.json()
            return ClickHouseResponse(
                success=True,
                data=data.get('result', data),
                message='Successfully called create_byoc_infrastructure'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to call create_byoc_infrastructure')

    async def delete_byoc_infrastructure(
        self,
        organization_id: str,
        byoc_infrastructure_id: str
    ) -> ClickHouseResponse:
        """Delete a BYOC infrastructure from an organization

        Args:
            organization_id: Organization ID
            byoc_infrastructure_id: BYOC infrastructure ID

        Returns:
            ClickHouseResponse with operation result
        """
        path_params = {'organizationId': organization_id, 'byocInfrastructureId': byoc_infrastructure_id}
        query_params = {}

        url = self._cloud_api_base_url + '/v1/organizations/{organizationId}/byocInfrastructure/{byocInfrastructureId}'
        url = url.format(**path_params)

        request = HTTPRequest(
            url=url,
            method='DELETE',
            query=query_params,
        )

        try:
            response = await self._http_client.execute(request)
            response.raise_for_status()
            data = response.json()
            return ClickHouseResponse(
                success=True,
                data=data.get('result', data),
                message='Successfully called delete_byoc_infrastructure'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to call delete_byoc_infrastructure')

    async def update_byoc_infrastructure(
        self,
        organization_id: str,
        byoc_infrastructure_id: str,
        config: Optional[Dict[str, Any]] = None
    ) -> ClickHouseResponse:
        """Update a BYOC infrastructure configuration

        Args:
            organization_id: Organization ID
            byoc_infrastructure_id: BYOC infrastructure ID
            config: Updated BYOC infrastructure configuration

        Returns:
            ClickHouseResponse with operation result
        """
        path_params = {'organizationId': organization_id, 'byocInfrastructureId': byoc_infrastructure_id}
        query_params = {}
        body = {}
        if config is not None:
            body['config'] = config

        url = self._cloud_api_base_url + '/v1/organizations/{organizationId}/byocInfrastructure/{byocInfrastructureId}'
        url = url.format(**path_params)

        request = HTTPRequest(
            url=url,
            method='PATCH',
            query=query_params,
            body=body if body else None,
        )

        try:
            response = await self._http_client.execute(request)
            response.raise_for_status()
            data = response.json()
            return ClickHouseResponse(
                success=True,
                data=data.get('result', data),
                message='Successfully called update_byoc_infrastructure'
            )
        except Exception as e:
            return ClickHouseResponse(success=False, error=str(e), message='Failed to call update_byoc_infrastructure')
