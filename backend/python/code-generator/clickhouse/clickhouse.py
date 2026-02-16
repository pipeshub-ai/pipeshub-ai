# ruff: noqa
"""
ClickHouse SDK Data Source Generator

Generates comprehensive ClickHouseDataSource class wrapping clickhouse-connect SDK:
- Query operations (query, query_df, query_np, query_arrow, raw_query)
- Insert operations (insert, insert_df, insert_arrow, raw_insert)
- Streaming operations (row/column/df/arrow streams)
- Command execution (DDL/DML)
- Context and utility methods

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional


# ================================================================================
# PARAMETER TYPE MAPPINGS
# ================================================================================

PARAMETER_TYPES = {
    # Query parameters
    'query': 'str',
    'cmd': 'str',
    'parameters': 'Union[Sequence, Dict[str, Any]]',
    'settings': 'Dict[str, Any]',
    'query_formats': 'Dict[str, str]',
    'column_formats': 'Dict[str, Union[str, Dict[str, str]]]',
    'encoding': 'str',
    'use_none': 'bool',
    'column_oriented': 'bool',
    'use_numpy': 'bool',
    'max_str_len': 'int',
    'query_tz': 'Union[str, Any]',
    'column_tzs': 'Dict[str, Union[str, Any]]',
    'utc_tz_aware': 'bool',
    'external_data': 'Any',
    'transport_settings': 'Dict[str, str]',
    'use_strings': 'bool',
    'use_na_values': 'bool',
    'use_extended_dtypes': 'bool',
    'fmt': 'str',
    'use_database': 'bool',
    'streaming': 'bool',
    'as_pandas': 'bool',
    'dataframe_library': 'str',

    # Insert parameters
    'table': 'str',
    'data': 'Sequence[Sequence[Any]]',
    'column_names': 'Union[str, Iterable[str]]',
    'database': 'str',
    'column_types': 'Sequence[Any]',
    'column_type_names': 'Sequence[str]',
    'df': 'Any',
    'arrow_table': 'Any',
    'insert_block': 'Union[str, bytes, Any]',
    'compression': 'str',

    # Command parameters
    'data_cmd': 'Union[str, bytes]',

    # Context parameters
    'context': 'Any',

    # Utility parameters
    'version_str': 'str',
    'key': 'str',
    'value': 'Any',
    'access_token': 'str',
}


# ================================================================================
# SDK METHOD DEFINITIONS
# ================================================================================

CLICKHOUSE_SDK_METHODS = {
    # ================================================================================
    # QUERY METHODS
    # ================================================================================
    'query': {
        'description': 'Execute a SELECT or DESCRIBE query and return structured results',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values for parameterized queries'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings for this query'},
            'query_formats': {'type': 'Dict[str, str]', 'description': 'Format overrides per ClickHouse type'},
            'column_formats': {'type': 'Dict[str, Union[str, Dict[str, str]]]', 'description': 'Format overrides per column name'},
            'encoding': {'type': 'str', 'description': 'Encoding for string columns'},
            'use_none': {'type': 'bool', 'description': 'Use None for ClickHouse NULL values'},
            'column_oriented': {'type': 'bool', 'description': 'Return results in column-oriented format'},
            'use_numpy': {'type': 'bool', 'description': 'Use numpy arrays for result columns'},
            'max_str_len': {'type': 'int', 'description': 'Maximum string length for fixed string columns'},
            'query_tz': {'type': 'Union[str, Any]', 'description': 'Timezone for DateTime columns'},
            'column_tzs': {'type': 'Dict[str, Union[str, Any]]', 'description': 'Per-column timezone overrides'},
            'utc_tz_aware': {'type': 'bool', 'description': 'Return timezone-aware UTC datetimes'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['query'],
        'return_handling': 'query_result',
    },

    'query_df': {
        'description': 'Execute a query and return results as a pandas DataFrame',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'query_formats': {'type': 'Dict[str, str]', 'description': 'Format overrides per ClickHouse type'},
            'column_formats': {'type': 'Dict[str, str]', 'description': 'Format overrides per column name'},
            'encoding': {'type': 'str', 'description': 'Encoding for string columns'},
            'use_none': {'type': 'bool', 'description': 'Use None for ClickHouse NULL values'},
            'max_str_len': {'type': 'int', 'description': 'Maximum string length for fixed string columns'},
            'use_na_values': {'type': 'bool', 'description': 'Use pandas NA values for nulls'},
            'query_tz': {'type': 'str', 'description': 'Timezone for DateTime columns'},
            'column_tzs': {'type': 'Dict[str, Union[str, Any]]', 'description': 'Per-column timezone overrides'},
            'utc_tz_aware': {'type': 'bool', 'description': 'Return timezone-aware UTC datetimes'},
            'context': {'type': 'Any', 'description': 'Reusable QueryContext object'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'use_extended_dtypes': {'type': 'bool', 'description': 'Use pandas extended dtypes'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['query'],
        'return_handling': 'raw',
    },

    'query_np': {
        'description': 'Execute a query and return results as a numpy ndarray',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'query_formats': {'type': 'Dict[str, str]', 'description': 'Format overrides per ClickHouse type'},
            'column_formats': {'type': 'Dict[str, str]', 'description': 'Format overrides per column name'},
            'encoding': {'type': 'str', 'description': 'Encoding for string columns'},
            'use_none': {'type': 'bool', 'description': 'Use None for ClickHouse NULL values'},
            'max_str_len': {'type': 'int', 'description': 'Maximum string length for fixed string columns'},
            'context': {'type': 'Any', 'description': 'Reusable QueryContext object'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['query'],
        'return_handling': 'raw',
    },

    'query_arrow': {
        'description': 'Execute a query and return results as a PyArrow Table',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'use_strings': {'type': 'bool', 'description': 'Return ClickHouse String type as Arrow string (vs binary)'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['query'],
        'return_handling': 'raw',
    },

    'query_df_arrow': {
        'description': 'Execute a query and return results as a DataFrame with PyArrow dtype backend',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'use_strings': {'type': 'bool', 'description': 'Return ClickHouse String type as Arrow string'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
            'dataframe_library': {'type': 'str', 'description': 'DataFrame library to use: "pandas" or "polars"'},
        },
        'required': ['query'],
        'return_handling': 'raw',
    },

    'raw_query': {
        'description': 'Execute a query and return raw bytes in the specified ClickHouse format',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'fmt': {'type': 'str', 'description': 'ClickHouse output format (e.g. TabSeparated, JSON, CSV)'},
            'use_database': {'type': 'bool', 'description': 'Prepend USE database before query'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['query'],
        'return_handling': 'raw_bytes',
    },

    'command': {
        'description': 'Execute a DDL or DML command (CREATE, DROP, ALTER, SET, etc.) and return the result',
        'parameters': {
            'cmd': {'type': 'str', 'description': 'ClickHouse DDL/DML command string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Command parameter values'},
            'data': {'type': 'Union[str, bytes]', 'description': 'Additional data for the command (e.g. INSERT data)'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'use_database': {'type': 'bool', 'description': 'Prepend USE database before command'},
            'external_data': {'type': 'Any', 'description': 'External data for the command'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['cmd'],
        'return_handling': 'command_result',
    },

    # ================================================================================
    # INSERT METHODS
    # ================================================================================
    'insert': {
        'description': 'Insert multiple rows of Python objects into a ClickHouse table',
        'parameters': {
            'table': {'type': 'str', 'description': 'Target table name'},
            'data': {'type': 'Sequence[Sequence[Any]]', 'description': 'Row data as list of lists/tuples'},
            'column_names': {'type': 'Union[str, Iterable[str]]', 'description': 'Column names for the insert (default: * for all columns)'},
            'database': {'type': 'str', 'description': 'Target database (overrides client default)'},
            'column_types': {'type': 'Sequence[Any]', 'description': 'ClickHouse column type objects'},
            'column_type_names': {'type': 'Sequence[str]', 'description': 'ClickHouse column type names as strings'},
            'column_oriented': {'type': 'bool', 'description': 'Data is column-oriented (list of columns, not list of rows)'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'context': {'type': 'Any', 'description': 'Reusable InsertContext object'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['table', 'data'],
        'return_handling': 'query_summary',
    },

    'insert_df': {
        'description': 'Insert a pandas DataFrame into a ClickHouse table',
        'parameters': {
            'table': {'type': 'str', 'description': 'Target table name'},
            'df': {'type': 'Any', 'description': 'pandas DataFrame to insert'},
            'database': {'type': 'str', 'description': 'Target database (overrides client default)'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'column_names': {'type': 'Sequence[str]', 'description': 'Column names for the insert'},
            'column_types': {'type': 'Sequence[Any]', 'description': 'ClickHouse column type objects'},
            'column_type_names': {'type': 'Sequence[str]', 'description': 'ClickHouse column type names as strings'},
            'context': {'type': 'Any', 'description': 'Reusable InsertContext object'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['table', 'df'],
        'return_handling': 'query_summary',
    },

    'insert_arrow': {
        'description': 'Insert a PyArrow Table into a ClickHouse table using Arrow format',
        'parameters': {
            'table': {'type': 'str', 'description': 'Target table name'},
            'arrow_table': {'type': 'Any', 'description': 'PyArrow Table to insert'},
            'database': {'type': 'str', 'description': 'Target database (overrides client default)'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['table', 'arrow_table'],
        'return_handling': 'query_summary',
    },

    'insert_df_arrow': {
        'description': 'Insert a pandas/polars DataFrame using the Arrow format for better type support',
        'parameters': {
            'table': {'type': 'str', 'description': 'Target table name'},
            'df': {'type': 'Any', 'description': 'pandas or polars DataFrame to insert'},
            'database': {'type': 'str', 'description': 'Target database (overrides client default)'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['table', 'df'],
        'return_handling': 'query_summary',
    },

    'raw_insert': {
        'description': 'Insert pre-formatted raw data (CSV, TSV, JSON, etc.) into a ClickHouse table',
        'parameters': {
            'table': {'type': 'str', 'description': 'Target table name'},
            'column_names': {'type': 'Sequence[str]', 'description': 'Column names for the insert'},
            'insert_block': {'type': 'Union[str, bytes, Any]', 'description': 'Raw data block to insert (str, bytes, generator, or BinaryIO)'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'fmt': {'type': 'str', 'description': 'ClickHouse input format (e.g. TabSeparated, CSV, JSONEachRow)'},
            'compression': {'type': 'str', 'description': 'Compression codec: lz4, zstd, brotli, or gzip'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['table'],
        'return_handling': 'query_summary',
    },

    # ================================================================================
    # STREAMING METHODS
    # ================================================================================
    'query_column_block_stream': {
        'description': 'Execute a query and stream results as column-oriented blocks for memory-efficient processing',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'query_formats': {'type': 'Dict[str, str]', 'description': 'Format overrides per ClickHouse type'},
            'column_formats': {'type': 'Dict[str, Union[str, Dict[str, str]]]', 'description': 'Format overrides per column name'},
            'encoding': {'type': 'str', 'description': 'Encoding for string columns'},
            'use_none': {'type': 'bool', 'description': 'Use None for ClickHouse NULL values'},
            'context': {'type': 'Any', 'description': 'Reusable QueryContext object'},
            'query_tz': {'type': 'Union[str, Any]', 'description': 'Timezone for DateTime columns'},
            'column_tzs': {'type': 'Dict[str, Union[str, Any]]', 'description': 'Per-column timezone overrides'},
            'utc_tz_aware': {'type': 'bool', 'description': 'Return timezone-aware UTC datetimes'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['query'],
        'return_handling': 'raw',
    },

    'query_row_block_stream': {
        'description': 'Execute a query and stream results as row-oriented blocks for memory-efficient processing',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'query_formats': {'type': 'Dict[str, str]', 'description': 'Format overrides per ClickHouse type'},
            'column_formats': {'type': 'Dict[str, Union[str, Dict[str, str]]]', 'description': 'Format overrides per column name'},
            'encoding': {'type': 'str', 'description': 'Encoding for string columns'},
            'use_none': {'type': 'bool', 'description': 'Use None for ClickHouse NULL values'},
            'context': {'type': 'Any', 'description': 'Reusable QueryContext object'},
            'query_tz': {'type': 'Union[str, Any]', 'description': 'Timezone for DateTime columns'},
            'column_tzs': {'type': 'Dict[str, Union[str, Any]]', 'description': 'Per-column timezone overrides'},
            'utc_tz_aware': {'type': 'bool', 'description': 'Return timezone-aware UTC datetimes'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['query'],
        'return_handling': 'raw',
    },

    'query_rows_stream': {
        'description': 'Execute a query and stream results as individual rows for memory-efficient processing',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'query_formats': {'type': 'Dict[str, str]', 'description': 'Format overrides per ClickHouse type'},
            'column_formats': {'type': 'Dict[str, Union[str, Dict[str, str]]]', 'description': 'Format overrides per column name'},
            'encoding': {'type': 'str', 'description': 'Encoding for string columns'},
            'use_none': {'type': 'bool', 'description': 'Use None for ClickHouse NULL values'},
            'context': {'type': 'Any', 'description': 'Reusable QueryContext object'},
            'query_tz': {'type': 'Union[str, Any]', 'description': 'Timezone for DateTime columns'},
            'column_tzs': {'type': 'Dict[str, Union[str, Any]]', 'description': 'Per-column timezone overrides'},
            'utc_tz_aware': {'type': 'bool', 'description': 'Return timezone-aware UTC datetimes'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['query'],
        'return_handling': 'raw',
    },

    'query_df_stream': {
        'description': 'Execute a query and stream results as pandas DataFrames per block',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'query_formats': {'type': 'Dict[str, str]', 'description': 'Format overrides per ClickHouse type'},
            'column_formats': {'type': 'Dict[str, str]', 'description': 'Format overrides per column name'},
            'encoding': {'type': 'str', 'description': 'Encoding for string columns'},
            'use_none': {'type': 'bool', 'description': 'Use None for ClickHouse NULL values'},
            'max_str_len': {'type': 'int', 'description': 'Maximum string length for fixed string columns'},
            'use_na_values': {'type': 'bool', 'description': 'Use pandas NA values for nulls'},
            'query_tz': {'type': 'str', 'description': 'Timezone for DateTime columns'},
            'column_tzs': {'type': 'Dict[str, Union[str, Any]]', 'description': 'Per-column timezone overrides'},
            'utc_tz_aware': {'type': 'bool', 'description': 'Return timezone-aware UTC datetimes'},
            'context': {'type': 'Any', 'description': 'Reusable QueryContext object'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'use_extended_dtypes': {'type': 'bool', 'description': 'Use pandas extended dtypes'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['query'],
        'return_handling': 'raw',
    },

    'query_np_stream': {
        'description': 'Execute a query and stream results as numpy arrays per block',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'query_formats': {'type': 'Dict[str, str]', 'description': 'Format overrides per ClickHouse type'},
            'column_formats': {'type': 'Dict[str, str]', 'description': 'Format overrides per column name'},
            'encoding': {'type': 'str', 'description': 'Encoding for string columns'},
            'use_none': {'type': 'bool', 'description': 'Use None for ClickHouse NULL values'},
            'max_str_len': {'type': 'int', 'description': 'Maximum string length for fixed string columns'},
            'context': {'type': 'Any', 'description': 'Reusable QueryContext object'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['query'],
        'return_handling': 'raw',
    },

    'query_arrow_stream': {
        'description': 'Execute a query and stream results as PyArrow Tables per block',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'use_strings': {'type': 'bool', 'description': 'Return ClickHouse String type as Arrow string'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['query'],
        'return_handling': 'raw',
    },

    'query_df_arrow_stream': {
        'description': 'Execute a query and stream results as Arrow-backed DataFrames per block',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'use_strings': {'type': 'bool', 'description': 'Return ClickHouse String type as Arrow string'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
            'dataframe_library': {'type': 'str', 'description': 'DataFrame library to use: "pandas" or "polars"'},
        },
        'required': ['query'],
        'return_handling': 'raw',
    },

    'raw_stream': {
        'description': 'Execute a query and return a raw IO stream of bytes in the specified ClickHouse format',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'fmt': {'type': 'str', 'description': 'ClickHouse output format (e.g. TabSeparated, JSON, CSV)'},
            'use_database': {'type': 'bool', 'description': 'Prepend USE database before query'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['query'],
        'return_handling': 'raw',
    },

    # ================================================================================
    # CONTEXT METHODS
    # ================================================================================
    'create_query_context': {
        'description': 'Build a reusable QueryContext for repeated queries with the same configuration',
        'parameters': {
            'query': {'type': 'str', 'description': 'ClickHouse SQL query string'},
            'parameters': {'type': 'Union[Sequence, Dict[str, Any]]', 'description': 'Query parameter values'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'query_formats': {'type': 'Dict[str, str]', 'description': 'Format overrides per ClickHouse type'},
            'column_formats': {'type': 'Dict[str, Union[str, Dict[str, str]]]', 'description': 'Format overrides per column name'},
            'encoding': {'type': 'str', 'description': 'Encoding for string columns'},
            'use_none': {'type': 'bool', 'description': 'Use None for ClickHouse NULL values'},
            'column_oriented': {'type': 'bool', 'description': 'Return results in column-oriented format'},
            'use_numpy': {'type': 'bool', 'description': 'Use numpy arrays for result columns'},
            'max_str_len': {'type': 'int', 'description': 'Maximum string length for fixed string columns'},
            'context': {'type': 'Any', 'description': 'Existing QueryContext to copy/modify'},
            'query_tz': {'type': 'Union[str, Any]', 'description': 'Timezone for DateTime columns'},
            'column_tzs': {'type': 'Dict[str, Union[str, Any]]', 'description': 'Per-column timezone overrides'},
            'utc_tz_aware': {'type': 'bool', 'description': 'Return timezone-aware UTC datetimes'},
            'use_na_values': {'type': 'bool', 'description': 'Use pandas NA values for nulls'},
            'streaming': {'type': 'bool', 'description': 'Configure context for streaming queries'},
            'as_pandas': {'type': 'bool', 'description': 'Configure context for pandas DataFrame output'},
            'external_data': {'type': 'Any', 'description': 'External data for the query'},
            'use_extended_dtypes': {'type': 'bool', 'description': 'Use pandas extended dtypes'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': [],
        'return_handling': 'raw',
    },

    'create_insert_context': {
        'description': 'Build a reusable InsertContext for repeated inserts to the same table',
        'parameters': {
            'table': {'type': 'str', 'description': 'Target table name'},
            'column_names': {'type': 'Union[str, Sequence[str]]', 'description': 'Column names for the insert'},
            'database': {'type': 'str', 'description': 'Target database (overrides client default)'},
            'column_types': {'type': 'Sequence[Any]', 'description': 'ClickHouse column type objects'},
            'column_type_names': {'type': 'Sequence[str]', 'description': 'ClickHouse column type names as strings'},
            'column_oriented': {'type': 'bool', 'description': 'Data is column-oriented'},
            'settings': {'type': 'Dict[str, Any]', 'description': 'ClickHouse server settings'},
            'data': {'type': 'Sequence[Sequence[Any]]', 'description': 'Initial data for the insert context'},
            'transport_settings': {'type': 'Dict[str, str]', 'description': 'HTTP transport settings'},
        },
        'required': ['table'],
        'return_handling': 'raw',
    },

    'data_insert': {
        'description': 'Execute an insert using a pre-built InsertContext',
        'parameters': {
            'context': {'type': 'Any', 'description': 'InsertContext with table, columns, and data configured'},
        },
        'required': ['context'],
        'return_handling': 'query_summary',
    },

    # ================================================================================
    # UTILITY METHODS
    # ================================================================================
    'ping': {
        'description': 'Validate the ClickHouse connection is alive',
        'parameters': {},
        'required': [],
        'return_handling': 'ping',
    },

    'min_version': {
        'description': 'Check if the connected ClickHouse server meets a minimum version requirement',
        'parameters': {
            'version_str': {'type': 'str', 'description': 'Minimum version string to check (e.g. "22.3")'},
        },
        'required': ['version_str'],
        'return_handling': 'bool_result',
    },

    'close': {
        'description': 'Close the ClickHouse client connection and release resources',
        'parameters': {},
        'required': [],
        'return_handling': 'none',
    },
}


# ================================================================================
# GENERATOR CLASS
# ================================================================================

class ClickHouseDataSourceGenerator:
    """Generator for comprehensive ClickHouse SDK datasource class."""

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace('-', '_').replace('.', '_').replace('/', '_')
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == '_'):
            sanitized = f"param_{sanitized}"
        return sanitized

    def _generate_method_signature(self, method_name: str, method_info: Dict) -> str:
        """Generate method signature with explicit parameters."""
        params = ["self"]

        # Required parameters first
        for param_name in method_info['required']:
            if param_name in method_info['parameters']:
                param_info = method_info['parameters'][param_name]
                sanitized = self._sanitize_parameter_name(param_name)
                params.append(f"{sanitized}: {param_info['type']}")

        # Optional parameters
        for param_name, param_info in method_info['parameters'].items():
            if param_name not in method_info['required']:
                sanitized = self._sanitize_parameter_name(param_name)
                ptype = param_info['type']
                if not ptype.startswith('Optional['):
                    ptype = f"Optional[{ptype}]"
                params.append(f"{sanitized}: {ptype} = None")

        signature_params = ",\n        ".join(params)

        return_handling = method_info.get('return_handling', 'raw')
        if return_handling == 'raw':
            return_type = 'Any'
        else:
            return_type = 'ClickHouseResponse'

        return f"    def {method_name}(\n        {signature_params}\n    ) -> {return_type}:"

    def _generate_method_docstring(self, method_info: Dict) -> List[str]:
        """Generate method docstring."""
        lines = [f'        """{method_info["description"]}', ""]

        if method_info['parameters']:
            lines.append("        Args:")
            for param_name, param_info in method_info['parameters'].items():
                sanitized = self._sanitize_parameter_name(param_name)
                lines.append(f"            {sanitized}: {param_info['description']}")
            lines.append("")

        return_handling = method_info.get('return_handling', 'raw')
        if return_handling == 'raw':
            lines.extend([
                "        Returns:",
                "            Raw SDK result (DataFrame, ndarray, StreamContext, etc.)",
            ])
        else:
            lines.extend([
                "        Returns:",
                "            ClickHouseResponse with operation result",
            ])

        lines.append('        """')
        return lines

    def _generate_kwargs_block(self, method_info: Dict) -> List[str]:
        """Generate kwargs building code."""
        required = method_info.get('required', [])
        params = method_info.get('parameters', {})

        if not params:
            return ["        kwargs: Dict[str, Any] = {}"]

        lines = []

        # Build required kwargs
        if required:
            req_parts = []
            for p in required:
                sanitized = self._sanitize_parameter_name(p)
                req_parts.append(f"'{p}': {sanitized}")
            lines.append(f"        kwargs: Dict[str, Any] = {{{', '.join(req_parts)}}}")
        else:
            lines.append("        kwargs: Dict[str, Any] = {}")

        # Add optional kwargs
        for param_name in params:
            if param_name not in required:
                sanitized = self._sanitize_parameter_name(param_name)
                lines.append(f"        if {sanitized} is not None:")
                lines.append(f"            kwargs['{param_name}'] = {sanitized}")

        return lines

    def _generate_return_handling(self, method_name: str, return_handling: str) -> List[str]:
        """Generate return handling code based on return type."""
        lines = []

        if return_handling == 'query_result':
            lines.extend([
                "        try:",
                f"            result = self._sdk.{method_name}(**kwargs)",
                "            return ClickHouseResponse(",
                "                success=True,",
                "                data={",
                "                    'result_rows': result.result_rows,",
                "                    'column_names': list(result.column_names),",
                "                    'query_id': result.query_id,",
                "                    'summary': result.summary,",
                "                },",
                f"                message='Successfully executed {method_name}'",
                "            )",
                "        except Exception as e:",
                f"            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute {method_name}')",
            ])

        elif return_handling == 'command_result':
            lines.extend([
                "        try:",
                f"            result = self._sdk.{method_name}(**kwargs)",
                "            return ClickHouseResponse(",
                "                success=True,",
                "                data={'result': result},",
                f"                message='Successfully executed {method_name}'",
                "            )",
                "        except Exception as e:",
                f"            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute {method_name}')",
            ])

        elif return_handling == 'query_summary':
            lines.extend([
                "        try:",
                f"            summary = self._sdk.{method_name}(**kwargs)",
                "            summary_data = {}",
                "            if hasattr(summary, 'written_rows'):",
                "                summary_data['written_rows'] = summary.written_rows",
                "            if hasattr(summary, 'written_bytes'):",
                "                summary_data['written_bytes'] = summary.written_bytes",
                "            if hasattr(summary, 'query_id'):",
                "                summary_data['query_id'] = summary.query_id",
                "            if hasattr(summary, 'summary'):",
                "                summary_data['summary'] = summary.summary",
                "            return ClickHouseResponse(",
                "                success=True,",
                "                data=summary_data,",
                f"                message='Successfully executed {method_name}'",
                "            )",
                "        except Exception as e:",
                f"            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute {method_name}')",
            ])

        elif return_handling == 'raw_bytes':
            lines.extend([
                "        try:",
                f"            result = self._sdk.{method_name}(**kwargs)",
                "            return ClickHouseResponse(",
                "                success=True,",
                "                data={'raw': result, 'size': len(result)},",
                f"                message='Successfully executed {method_name}'",
                "            )",
                "        except Exception as e:",
                f"            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute {method_name}')",
            ])

        elif return_handling == 'bool_result':
            lines.extend([
                "        try:",
                f"            result = self._sdk.{method_name}(**kwargs)",
                "            return ClickHouseResponse(",
                "                success=True,",
                "                data={'result': result},",
                f"                message='Successfully executed {method_name}'",
                "            )",
                "        except Exception as e:",
                f"            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute {method_name}')",
            ])

        elif return_handling == 'ping':
            lines.extend([
                "        try:",
                f"            self._sdk.{method_name}()",
                "            return ClickHouseResponse(",
                "                success=True,",
                "                message='Connection is alive'",
                "            )",
                "        except Exception as e:",
                "            return ClickHouseResponse(success=False, error=str(e), message='Connection ping failed')",
            ])

        elif return_handling == 'none':
            lines.extend([
                "        try:",
                f"            self._sdk.{method_name}()",
                "            return ClickHouseResponse(",
                "                success=True,",
                f"                message='Successfully executed {method_name}'",
                "            )",
                "        except Exception as e:",
                f"            return ClickHouseResponse(success=False, error=str(e), message='Failed to execute {method_name}')",
            ])

        elif return_handling == 'raw':
            lines.extend([
                "        try:",
                f"            return self._sdk.{method_name}(**kwargs)",
                "        except Exception as e:",
                f"            raise RuntimeError(f'Failed to execute {method_name}: {{str(e)}}') from e",
            ])

        return lines

    def _generate_method(self, method_name: str, method_info: Dict) -> str:
        """Generate a complete method."""
        lines = []

        # Signature
        lines.append(self._generate_method_signature(method_name, method_info))

        # Docstring
        lines.extend(self._generate_method_docstring(method_info))

        # Build kwargs
        return_handling = method_info.get('return_handling', 'raw')
        if return_handling not in ('ping', 'none'):
            kwargs_lines = self._generate_kwargs_block(method_info)
            lines.extend(kwargs_lines)
            lines.append("")

        # Return handling
        return_lines = self._generate_return_handling(method_name, return_handling)
        lines.extend(return_lines)

        self.generated_methods.append({
            'name': method_name,
            'description': method_info['description'],
            'return_handling': return_handling,
        })

        return "\n".join(lines)

    def generate_datasource(self) -> str:
        """Generate the complete ClickHouse datasource class."""

        class_lines = [
            '"""',
            'ClickHouse SDK DataSource - Auto-generated API wrapper',
            '',
            'Generated from clickhouse-connect SDK method signatures.',
            'Uses the clickhouse-connect SDK for direct ClickHouse interactions.',
            'All methods have explicit parameter signatures - NO Any type for params, NO **kwargs.',
            '"""',
            '',
            'import logging',
            'from typing import Any, Dict, Iterable, Optional, Sequence, Union',
            '',
            'from app.sources.client.clickhouse.clickhouse import (',
            '    ClickHouseClient,',
            '    ClickHouseResponse,',
            ')',
            '',
            'logger = logging.getLogger(__name__)',
            '',
            '',
            'class ClickHouseDataSource:',
            '    """clickhouse-connect SDK DataSource',
            '',
            '    Provides wrapper methods for clickhouse-connect SDK operations:',
            '    - Query operations (query, query_df, query_np, query_arrow)',
            '    - Insert operations (insert, insert_df, insert_arrow)',
            '    - Streaming operations (row/column/df/arrow streams)',
            '    - Command execution (DDL/DML via command)',
            '    - Raw data operations (raw_query, raw_insert, raw_stream)',
            '    - Context and utility methods',
            '',
            '    All methods have explicit parameter signatures - NO **kwargs.',
            '    Methods that return structured results return ClickHouseResponse objects.',
            '    Methods that return DataFrames, Arrow tables, or streams return raw SDK results.',
            '    """',
            '',
            '    def __init__(self, client: ClickHouseClient) -> None:',
            '        """Initialize with ClickHouseClient.',
            '',
            '        Args:',
            '            client: ClickHouseClient instance with configured authentication',
            '        """',
            '        self._client = client',
            '        self._sdk = client.get_sdk()',
            '        if self._sdk is None:',
            "            raise ValueError('ClickHouse SDK client is not initialized')",
            '',
            "    def get_data_source(self) -> 'ClickHouseDataSource':",
            '        """Return the data source instance."""',
            '        return self',
            '',
            '    def get_client(self) -> ClickHouseClient:',
            '        """Return the underlying ClickHouseClient."""',
            '        return self._client',
            '',
        ]

        # Generate all SDK methods
        for method_name, method_info in CLICKHOUSE_SDK_METHODS.items():
            class_lines.append(self._generate_method(method_name, method_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the ClickHouse datasource to a file."""
        if filename is None:
            filename = "clickhouse.py"

        # Output to app/sources/external/clickhouse/
        script_dir = Path(__file__).parent if __file__ else Path('.')
        clickhouse_dir = script_dir.parent.parent / 'app' / 'sources' / 'external' / 'clickhouse'
        clickhouse_dir.mkdir(parents=True, exist_ok=True)

        full_path = clickhouse_dir / filename

        class_code = self.generate_datasource()

        full_path.write_text(class_code, encoding='utf-8')

        print(f"Generated ClickHouse data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by category
        categories = {
            'Query': 0,
            'Insert': 0,
            'Streaming': 0,
            'Context': 0,
            'Utility': 0,
            'Command': 0,
        }

        for method in self.generated_methods:
            name = method['name']
            if 'stream' in name:
                categories['Streaming'] += 1
            elif name.startswith('query') or name.startswith('raw_query'):
                categories['Query'] += 1
            elif name.startswith('insert') or name.startswith('raw_insert') or name == 'data_insert':
                categories['Insert'] += 1
            elif name.startswith('create_'):
                categories['Context'] += 1
            elif name == 'command':
                categories['Command'] += 1
            else:
                categories['Utility'] += 1

        print(f"\nMethods by Category:")
        for category, count in categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for ClickHouse data source generator."""
    import argparse

    parser = argparse.ArgumentParser(description='Generate ClickHouse SDK data source')
    parser.add_argument('--filename', '-f', help='Output filename (optional)')

    args = parser.parse_args()

    try:
        generator = ClickHouseDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate ClickHouse data source: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
