# ruff: noqa
"""
Snowflake REST API Code Generator

Generates comprehensive SnowflakeDataSource class covering Snowflake REST API v2:
- Database, Schema, Table, View operations
- Warehouse management
- User and Role management
- Stage, Task, Stream, Pipe operations
- And more...

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# Define all Snowflake REST API v2 endpoints with their parameters
SNOWFLAKE_API_ENDPOINTS = {
    # ================================================================================
    # DATABASE OPERATIONS
    # ================================================================================
    'list_databases': {
        'method': 'GET',
        'path': '/databases',
        'description': 'List all accessible databases',
        'parameters': {
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern (case-insensitive)'},
            'starts_with': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name prefix (case-sensitive)'},
            'show_limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Maximum number of rows to return'},
            'from_name': {'type': 'Optional[str]', 'location': 'query', 'description': 'Fetch rows after this name (pagination)'},
            'history': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Include dropped databases'}
        },
        'required': []
    },
    'create_database': {
        'method': 'POST',
        'path': '/databases',
        'description': 'Create a new database',
        'parameters': {
            'name': {'type': 'str', 'location': 'body', 'description': 'Database name'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode: errorIfExists, orReplace, ifNotExists'},
            'kind': {'type': 'Optional[str]', 'location': 'body', 'description': 'Database type: PERMANENT, TRANSIENT'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Database comment'},
            'data_retention_time_in_days': {'type': 'Optional[int]', 'location': 'body', 'description': 'Time Travel retention period in days'},
            'default_ddl_collation': {'type': 'Optional[str]', 'location': 'body', 'description': 'Default collation for DDL statements'},
            'max_data_extension_time_in_days': {'type': 'Optional[int]', 'location': 'body', 'description': 'Maximum data extension time'}
        },
        'required': ['name']
    },
    'get_database': {
        'method': 'GET',
        'path': '/databases/{name}',
        'description': 'Get a specific database by name',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Database name'}
        },
        'required': ['name']
    },
    'delete_database': {
        'method': 'DELETE',
        'path': '/databases/{name}',
        'description': 'Drop a database',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if database exists'},
            'restrict': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Restrict drop if database has dependent objects'}
        },
        'required': ['name']
    },
    'undrop_database': {
        'method': 'POST',
        'path': '/databases/{name}:undrop',
        'description': 'Restore a dropped database',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Database name to restore'}
        },
        'required': ['name']
    },

    # ================================================================================
    # SCHEMA OPERATIONS
    # ================================================================================
    'list_schemas': {
        'method': 'GET',
        'path': '/databases/{database}/schemas',
        'description': 'List all schemas in a database',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'},
            'starts_with': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name prefix'},
            'show_limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Maximum rows to return'},
            'from_name': {'type': 'Optional[str]', 'location': 'query', 'description': 'Fetch rows after this name'},
            'history': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Include dropped schemas'}
        },
        'required': ['database']
    },
    'create_schema': {
        'method': 'POST',
        'path': '/databases/{database}/schemas',
        'description': 'Create a new schema in a database',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'name': {'type': 'str', 'location': 'body', 'description': 'Schema name'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode: errorIfExists, orReplace, ifNotExists'},
            'kind': {'type': 'Optional[str]', 'location': 'body', 'description': 'Schema type: PERMANENT, TRANSIENT'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Schema comment'},
            'managed_access': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Enable managed access'},
            'data_retention_time_in_days': {'type': 'Optional[int]', 'location': 'body', 'description': 'Data retention period'}
        },
        'required': ['database', 'name']
    },
    'get_schema': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{name}',
        'description': 'Get a specific schema',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Schema name'}
        },
        'required': ['database', 'name']
    },
    'delete_schema': {
        'method': 'DELETE',
        'path': '/databases/{database}/schemas/{name}',
        'description': 'Drop a schema',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'},
            'restrict': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Restrict if has dependents'}
        },
        'required': ['database', 'name']
    },
    'undrop_schema': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{name}:undrop',
        'description': 'Restore a dropped schema',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Schema name'}
        },
        'required': ['database', 'name']
    },

    # ================================================================================
    # TABLE OPERATIONS
    # ================================================================================
    'list_tables': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/tables',
        'description': 'List all tables in a schema',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'},
            'starts_with': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name prefix'},
            'show_limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Maximum rows to return'},
            'from_name': {'type': 'Optional[str]', 'location': 'query', 'description': 'Fetch rows after this name'},
            'history': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Include dropped tables'}
        },
        'required': ['database', 'schema']
    },
    'create_table': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{schema}/tables',
        'description': 'Create a new table',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'body', 'description': 'Table name'},
            'columns': {'type': 'List[Dict[str, str]]', 'location': 'body', 'description': 'Column definitions with name, datatype, nullable, etc.'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'kind': {'type': 'Optional[str]', 'location': 'body', 'description': 'Table type: PERMANENT, TRANSIENT, TEMPORARY'},
            'cluster_by': {'type': 'Optional[List[str]]', 'location': 'body', 'description': 'Clustering keys'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Table comment'},
            'data_retention_time_in_days': {'type': 'Optional[int]', 'location': 'body', 'description': 'Data retention period'}
        },
        'required': ['database', 'schema', 'name', 'columns']
    },
    'get_table': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/tables/{name}',
        'description': 'Get a specific table',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Table name'}
        },
        'required': ['database', 'schema', 'name']
    },
    'delete_table': {
        'method': 'DELETE',
        'path': '/databases/{database}/schemas/{schema}/tables/{name}',
        'description': 'Drop a table',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Table name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['database', 'schema', 'name']
    },
    'undrop_table': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{schema}/tables/{name}:undrop',
        'description': 'Restore a dropped table',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Table name'}
        },
        'required': ['database', 'schema', 'name']
    },

    # ================================================================================
    # VIEW OPERATIONS
    # ================================================================================
    'list_views': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/views',
        'description': 'List all views in a schema',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'},
            'starts_with': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name prefix'},
            'show_limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Maximum rows to return'}
        },
        'required': ['database', 'schema']
    },
    'create_view': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{schema}/views',
        'description': 'Create a new view',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'body', 'description': 'View name'},
            'text': {'type': 'str', 'location': 'body', 'description': 'View SQL definition (SELECT statement)'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'is_secure': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Create as secure view'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'View comment'},
            'columns': {'type': 'Optional[List[Dict[str, str]]]', 'location': 'body', 'description': 'Column definitions'}
        },
        'required': ['database', 'schema', 'name', 'text']
    },
    'get_view': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/views/{name}',
        'description': 'Get a specific view',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'View name'}
        },
        'required': ['database', 'schema', 'name']
    },
    'delete_view': {
        'method': 'DELETE',
        'path': '/databases/{database}/schemas/{schema}/views/{name}',
        'description': 'Drop a view',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'View name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['database', 'schema', 'name']
    },

    # ================================================================================
    # WAREHOUSE OPERATIONS
    # ================================================================================
    'list_warehouses': {
        'method': 'GET',
        'path': '/warehouses',
        'description': 'List all warehouses',
        'parameters': {
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'}
        },
        'required': []
    },
    'create_warehouse': {
        'method': 'POST',
        'path': '/warehouses',
        'description': 'Create a new warehouse',
        'parameters': {
            'name': {'type': 'str', 'location': 'body', 'description': 'Warehouse name'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'warehouse_size': {'type': 'Optional[str]', 'location': 'body', 'description': 'Size: XSMALL, SMALL, MEDIUM, LARGE, XLARGE, etc.'},
            'warehouse_type': {'type': 'Optional[str]', 'location': 'body', 'description': 'Type: STANDARD, SNOWPARK-OPTIMIZED'},
            'auto_suspend': {'type': 'Optional[int]', 'location': 'body', 'description': 'Auto-suspend timeout in seconds'},
            'auto_resume': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Enable auto-resume'},
            'initially_suspended': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Create in suspended state'},
            'min_cluster_count': {'type': 'Optional[int]', 'location': 'body', 'description': 'Minimum cluster count for multi-cluster'},
            'max_cluster_count': {'type': 'Optional[int]', 'location': 'body', 'description': 'Maximum cluster count for multi-cluster'},
            'scaling_policy': {'type': 'Optional[str]', 'location': 'body', 'description': 'Scaling policy: STANDARD, ECONOMY'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Warehouse comment'},
            'enable_query_acceleration': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Enable query acceleration'},
            'query_acceleration_max_scale_factor': {'type': 'Optional[int]', 'location': 'body', 'description': 'Max scale factor for query acceleration'}
        },
        'required': ['name']
    },
    'get_warehouse': {
        'method': 'GET',
        'path': '/warehouses/{name}',
        'description': 'Get a specific warehouse',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Warehouse name'}
        },
        'required': ['name']
    },
    'delete_warehouse': {
        'method': 'DELETE',
        'path': '/warehouses/{name}',
        'description': 'Drop a warehouse',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Warehouse name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['name']
    },
    'resume_warehouse': {
        'method': 'POST',
        'path': '/warehouses/{name}:resume',
        'description': 'Resume a suspended warehouse',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Warehouse name'},
            'if_suspended': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only resume if currently suspended'}
        },
        'required': ['name']
    },
    'suspend_warehouse': {
        'method': 'POST',
        'path': '/warehouses/{name}:suspend',
        'description': 'Suspend a running warehouse',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Warehouse name'},
            'if_running': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only suspend if currently running'}
        },
        'required': ['name']
    },
    'abort_warehouse_queries': {
        'method': 'POST',
        'path': '/warehouses/{name}:abort',
        'description': 'Abort all running queries on a warehouse',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Warehouse name'}
        },
        'required': ['name']
    },

    # ================================================================================
    # USER OPERATIONS
    # ================================================================================
    'list_users': {
        'method': 'GET',
        'path': '/users',
        'description': 'List all users',
        'parameters': {
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'},
            'starts_with': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name prefix'},
            'show_limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Maximum rows to return'},
            'from_name': {'type': 'Optional[str]', 'location': 'query', 'description': 'Fetch rows after this name'}
        },
        'required': []
    },
    'create_user': {
        'method': 'POST',
        'path': '/users',
        'description': 'Create a new user',
        'parameters': {
            'name': {'type': 'str', 'location': 'body', 'description': 'User name'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'password': {'type': 'Optional[str]', 'location': 'body', 'description': 'User password'},
            'login_name': {'type': 'Optional[str]', 'location': 'body', 'description': 'Login name'},
            'display_name': {'type': 'Optional[str]', 'location': 'body', 'description': 'Display name'},
            'first_name': {'type': 'Optional[str]', 'location': 'body', 'description': 'First name'},
            'last_name': {'type': 'Optional[str]', 'location': 'body', 'description': 'Last name'},
            'email': {'type': 'Optional[str]', 'location': 'body', 'description': 'Email address'},
            'default_role': {'type': 'Optional[str]', 'location': 'body', 'description': 'Default role'},
            'default_warehouse': {'type': 'Optional[str]', 'location': 'body', 'description': 'Default warehouse'},
            'default_namespace': {'type': 'Optional[str]', 'location': 'body', 'description': 'Default namespace (database.schema)'},
            'must_change_password': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Force password change on first login'},
            'disabled': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Disable user account'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'User comment'}
        },
        'required': ['name']
    },
    'get_user': {
        'method': 'GET',
        'path': '/users/{name}',
        'description': 'Get a specific user',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'User name'}
        },
        'required': ['name']
    },
    'delete_user': {
        'method': 'DELETE',
        'path': '/users/{name}',
        'description': 'Drop a user',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'User name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['name']
    },

    # ================================================================================
    # ROLE OPERATIONS
    # ================================================================================
    'list_roles': {
        'method': 'GET',
        'path': '/roles',
        'description': 'List all roles',
        'parameters': {
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'},
            'starts_with': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name prefix'},
            'show_limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Maximum rows to return'},
            'from_name': {'type': 'Optional[str]', 'location': 'query', 'description': 'Fetch rows after this name'}
        },
        'required': []
    },
    'create_role': {
        'method': 'POST',
        'path': '/roles',
        'description': 'Create a new role',
        'parameters': {
            'name': {'type': 'str', 'location': 'body', 'description': 'Role name'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Role comment'}
        },
        'required': ['name']
    },
    'get_role': {
        'method': 'GET',
        'path': '/roles/{name}',
        'description': 'Get a specific role',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Role name'}
        },
        'required': ['name']
    },
    'delete_role': {
        'method': 'DELETE',
        'path': '/roles/{name}',
        'description': 'Drop a role',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Role name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['name']
    },

    # ================================================================================
    # TASK OPERATIONS
    # ================================================================================
    'list_tasks': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/tasks',
        'description': 'List all tasks in a schema',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'},
            'starts_with': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name prefix'},
            'root_only': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only return root tasks'},
            'show_limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Maximum rows to return'}
        },
        'required': ['database', 'schema']
    },
    'create_task': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{schema}/tasks',
        'description': 'Create a new task',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'body', 'description': 'Task name'},
            'definition': {'type': 'str', 'location': 'body', 'description': 'SQL statement to execute'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'warehouse': {'type': 'Optional[str]', 'location': 'body', 'description': 'Warehouse to use'},
            'schedule': {'type': 'Optional[str]', 'location': 'body', 'description': 'CRON or interval schedule'},
            'predecessors': {'type': 'Optional[List[str]]', 'location': 'body', 'description': 'Predecessor task names'},
            'condition': {'type': 'Optional[str]', 'location': 'body', 'description': 'WHEN condition'},
            'allow_overlapping_execution': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Allow concurrent executions'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Task comment'}
        },
        'required': ['database', 'schema', 'name', 'definition']
    },
    'get_task': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/tasks/{name}',
        'description': 'Get a specific task',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Task name'}
        },
        'required': ['database', 'schema', 'name']
    },
    'delete_task': {
        'method': 'DELETE',
        'path': '/databases/{database}/schemas/{schema}/tasks/{name}',
        'description': 'Drop a task',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Task name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['database', 'schema', 'name']
    },
    'execute_task': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{schema}/tasks/{name}:execute',
        'description': 'Execute a task immediately',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Task name'},
            'retry_last': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Retry the last failed run'}
        },
        'required': ['database', 'schema', 'name']
    },
    'resume_task': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{schema}/tasks/{name}:resume',
        'description': 'Resume a suspended task',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Task name'}
        },
        'required': ['database', 'schema', 'name']
    },
    'suspend_task': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{schema}/tasks/{name}:suspend',
        'description': 'Suspend a running task',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Task name'}
        },
        'required': ['database', 'schema', 'name']
    },

    # ================================================================================
    # STREAM OPERATIONS
    # ================================================================================
    'list_streams': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/streams',
        'description': 'List all streams in a schema',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'},
            'starts_with': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name prefix'},
            'show_limit': {'type': 'Optional[int]', 'location': 'query', 'description': 'Maximum rows to return'}
        },
        'required': ['database', 'schema']
    },
    'create_stream': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{schema}/streams',
        'description': 'Create a new stream',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'body', 'description': 'Stream name'},
            'source_type': {'type': 'str', 'location': 'body', 'description': 'Source type: table, external_table, stage, view'},
            'source_name': {'type': 'str', 'location': 'body', 'description': 'Fully qualified source object name'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'append_only': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Track only inserts'},
            'show_initial_rows': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Include existing rows'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Stream comment'}
        },
        'required': ['database', 'schema', 'name', 'source_type', 'source_name']
    },
    'get_stream': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/streams/{name}',
        'description': 'Get a specific stream',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Stream name'}
        },
        'required': ['database', 'schema', 'name']
    },
    'delete_stream': {
        'method': 'DELETE',
        'path': '/databases/{database}/schemas/{schema}/streams/{name}',
        'description': 'Drop a stream',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Stream name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['database', 'schema', 'name']
    },

    # ================================================================================
    # STAGE OPERATIONS
    # ================================================================================
    'list_stages': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/stages',
        'description': 'List all stages in a schema',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'}
        },
        'required': ['database', 'schema']
    },
    'create_stage': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{schema}/stages',
        'description': 'Create a new stage',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'body', 'description': 'Stage name'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'kind': {'type': 'Optional[str]', 'location': 'body', 'description': 'Stage type: INTERNAL, EXTERNAL'},
            'url': {'type': 'Optional[str]', 'location': 'body', 'description': 'External stage URL (s3://, azure://, gcs://)'},
            'storage_integration': {'type': 'Optional[str]', 'location': 'body', 'description': 'Storage integration name'},
            'credentials': {'type': 'Optional[Dict[str, str]]', 'location': 'body', 'description': 'Stage credentials'},
            'encryption': {'type': 'Optional[Dict[str, str]]', 'location': 'body', 'description': 'Encryption settings'},
            'directory_table': {'type': 'Optional[Dict[str, bool]]', 'location': 'body', 'description': 'Directory table settings'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Stage comment'}
        },
        'required': ['database', 'schema', 'name']
    },
    'get_stage': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/stages/{name}',
        'description': 'Get a specific stage',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Stage name'}
        },
        'required': ['database', 'schema', 'name']
    },
    'delete_stage': {
        'method': 'DELETE',
        'path': '/databases/{database}/schemas/{schema}/stages/{name}',
        'description': 'Drop a stage',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Stage name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['database', 'schema', 'name']
    },

    # ================================================================================
    # PIPE OPERATIONS
    # ================================================================================
    'list_pipes': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/pipes',
        'description': 'List all pipes in a schema',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'}
        },
        'required': ['database', 'schema']
    },
    'create_pipe': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{schema}/pipes',
        'description': 'Create a new pipe for continuous data loading',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'body', 'description': 'Pipe name'},
            'copy_statement': {'type': 'str', 'location': 'body', 'description': 'COPY INTO statement'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'auto_ingest': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Enable auto-ingest'},
            'aws_sns_topic': {'type': 'Optional[str]', 'location': 'body', 'description': 'AWS SNS topic ARN for notifications'},
            'integration': {'type': 'Optional[str]', 'location': 'body', 'description': 'Notification integration name'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Pipe comment'}
        },
        'required': ['database', 'schema', 'name', 'copy_statement']
    },
    'get_pipe': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/pipes/{name}',
        'description': 'Get a specific pipe',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Pipe name'}
        },
        'required': ['database', 'schema', 'name']
    },
    'delete_pipe': {
        'method': 'DELETE',
        'path': '/databases/{database}/schemas/{schema}/pipes/{name}',
        'description': 'Drop a pipe',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Pipe name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['database', 'schema', 'name']
    },

    # ================================================================================
    # ALERT OPERATIONS
    # ================================================================================
    'list_alerts': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/alerts',
        'description': 'List all alerts in a schema',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'}
        },
        'required': ['database', 'schema']
    },
    'create_alert': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{schema}/alerts',
        'description': 'Create a new alert',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'body', 'description': 'Alert name'},
            'warehouse': {'type': 'str', 'location': 'body', 'description': 'Warehouse to execute the alert'},
            'schedule': {'type': 'str', 'location': 'body', 'description': 'CRON or interval schedule'},
            'condition': {'type': 'str', 'location': 'body', 'description': 'SQL condition that triggers the alert'},
            'action': {'type': 'str', 'location': 'body', 'description': 'SQL action to execute when triggered'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Alert comment'}
        },
        'required': ['database', 'schema', 'name', 'warehouse', 'schedule', 'condition', 'action']
    },
    'get_alert': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/alerts/{name}',
        'description': 'Get a specific alert',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Alert name'}
        },
        'required': ['database', 'schema', 'name']
    },
    'delete_alert': {
        'method': 'DELETE',
        'path': '/databases/{database}/schemas/{schema}/alerts/{name}',
        'description': 'Drop an alert',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Alert name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['database', 'schema', 'name']
    },

    # ================================================================================
    # NETWORK POLICY OPERATIONS
    # ================================================================================
    'list_network_policies': {
        'method': 'GET',
        'path': '/network-policies',
        'description': 'List all network policies',
        'parameters': {
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'}
        },
        'required': []
    },
    'create_network_policy': {
        'method': 'POST',
        'path': '/network-policies',
        'description': 'Create a new network policy',
        'parameters': {
            'name': {'type': 'str', 'location': 'body', 'description': 'Network policy name'},
            'allowed_ip_list': {'type': 'List[str]', 'location': 'body', 'description': 'List of allowed IP addresses or CIDR ranges'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'blocked_ip_list': {'type': 'Optional[List[str]]', 'location': 'body', 'description': 'List of blocked IP addresses'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Policy comment'}
        },
        'required': ['name', 'allowed_ip_list']
    },
    'get_network_policy': {
        'method': 'GET',
        'path': '/network-policies/{name}',
        'description': 'Get a specific network policy',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Network policy name'}
        },
        'required': ['name']
    },
    'delete_network_policy': {
        'method': 'DELETE',
        'path': '/network-policies/{name}',
        'description': 'Drop a network policy',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Network policy name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['name']
    },

    # ================================================================================
    # FUNCTION OPERATIONS
    # ================================================================================
    'list_functions': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/functions',
        'description': 'List all user-defined functions in a schema',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'}
        },
        'required': ['database', 'schema']
    },
    'create_function': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{schema}/functions',
        'description': 'Create a new user-defined function',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'body', 'description': 'Function name'},
            'arguments': {'type': 'List[Dict[str, str]]', 'location': 'body', 'description': 'Function arguments with name and type'},
            'return_type': {'type': 'str', 'location': 'body', 'description': 'Return data type'},
            'language': {'type': 'str', 'location': 'body', 'description': 'Language: SQL, JAVASCRIPT, PYTHON, JAVA, SCALA'},
            'body': {'type': 'str', 'location': 'body', 'description': 'Function body/definition'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'is_secure': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Create as secure function'},
            'runtime_version': {'type': 'Optional[str]', 'location': 'body', 'description': 'Runtime version for non-SQL'},
            'packages': {'type': 'Optional[List[str]]', 'location': 'body', 'description': 'Package dependencies'},
            'handler': {'type': 'Optional[str]', 'location': 'body', 'description': 'Handler function name'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Function comment'}
        },
        'required': ['database', 'schema', 'name', 'arguments', 'return_type', 'language', 'body']
    },
    'delete_function': {
        'method': 'DELETE',
        'path': '/databases/{database}/schemas/{schema}/functions/{name_with_args}',
        'description': 'Drop a user-defined function',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name_with_args': {'type': 'str', 'location': 'path', 'description': 'Function name with argument types (e.g., my_func(int,string))'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['database', 'schema', 'name_with_args']
    },

    # ================================================================================
    # PROCEDURE OPERATIONS
    # ================================================================================
    'list_procedures': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/procedures',
        'description': 'List all stored procedures in a schema',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'}
        },
        'required': ['database', 'schema']
    },
    'create_procedure': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{schema}/procedures',
        'description': 'Create a new stored procedure',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'body', 'description': 'Procedure name'},
            'arguments': {'type': 'List[Dict[str, str]]', 'location': 'body', 'description': 'Procedure arguments'},
            'return_type': {'type': 'str', 'location': 'body', 'description': 'Return data type'},
            'language': {'type': 'str', 'location': 'body', 'description': 'Language: SQL, JAVASCRIPT, PYTHON, JAVA, SCALA'},
            'body': {'type': 'str', 'location': 'body', 'description': 'Procedure body'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'execute_as': {'type': 'Optional[str]', 'location': 'body', 'description': 'Execute as: CALLER, OWNER'},
            'runtime_version': {'type': 'Optional[str]', 'location': 'body', 'description': 'Runtime version'},
            'packages': {'type': 'Optional[List[str]]', 'location': 'body', 'description': 'Package dependencies'},
            'handler': {'type': 'Optional[str]', 'location': 'body', 'description': 'Handler function name'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Procedure comment'}
        },
        'required': ['database', 'schema', 'name', 'arguments', 'return_type', 'language', 'body']
    },
    'delete_procedure': {
        'method': 'DELETE',
        'path': '/databases/{database}/schemas/{schema}/procedures/{name_with_args}',
        'description': 'Drop a stored procedure',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name_with_args': {'type': 'str', 'location': 'path', 'description': 'Procedure name with argument types'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['database', 'schema', 'name_with_args']
    },

    # ================================================================================
    # COMPUTE POOL OPERATIONS (Snowpark Container Services)
    # ================================================================================
    'list_compute_pools': {
        'method': 'GET',
        'path': '/compute-pools',
        'description': 'List all compute pools',
        'parameters': {
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'}
        },
        'required': []
    },
    'create_compute_pool': {
        'method': 'POST',
        'path': '/compute-pools',
        'description': 'Create a new compute pool for container services',
        'parameters': {
            'name': {'type': 'str', 'location': 'body', 'description': 'Compute pool name'},
            'min_nodes': {'type': 'int', 'location': 'body', 'description': 'Minimum number of nodes'},
            'max_nodes': {'type': 'int', 'location': 'body', 'description': 'Maximum number of nodes'},
            'instance_family': {'type': 'str', 'location': 'body', 'description': 'Instance family (e.g., CPU_X64_XS)'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'auto_resume': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Enable auto-resume'},
            'initially_suspended': {'type': 'Optional[bool]', 'location': 'body', 'description': 'Create in suspended state'},
            'auto_suspend_secs': {'type': 'Optional[int]', 'location': 'body', 'description': 'Auto-suspend timeout in seconds'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Compute pool comment'}
        },
        'required': ['name', 'min_nodes', 'max_nodes', 'instance_family']
    },
    'get_compute_pool': {
        'method': 'GET',
        'path': '/compute-pools/{name}',
        'description': 'Get a specific compute pool',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Compute pool name'}
        },
        'required': ['name']
    },
    'delete_compute_pool': {
        'method': 'DELETE',
        'path': '/compute-pools/{name}',
        'description': 'Drop a compute pool',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Compute pool name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['name']
    },
    'resume_compute_pool': {
        'method': 'POST',
        'path': '/compute-pools/{name}:resume',
        'description': 'Resume a suspended compute pool',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Compute pool name'}
        },
        'required': ['name']
    },
    'suspend_compute_pool': {
        'method': 'POST',
        'path': '/compute-pools/{name}:suspend',
        'description': 'Suspend a compute pool',
        'parameters': {
            'name': {'type': 'str', 'location': 'path', 'description': 'Compute pool name'}
        },
        'required': ['name']
    },

    # ================================================================================
    # NOTEBOOK OPERATIONS
    # ================================================================================
    'list_notebooks': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/notebooks',
        'description': 'List all notebooks in a schema',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'like': {'type': 'Optional[str]', 'location': 'query', 'description': 'Filter by name pattern'}
        },
        'required': ['database', 'schema']
    },
    'create_notebook': {
        'method': 'POST',
        'path': '/databases/{database}/schemas/{schema}/notebooks',
        'description': 'Create a new notebook',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'body', 'description': 'Notebook name'},
            'create_mode': {'type': 'Optional[str]', 'location': 'query', 'description': 'Creation mode'},
            'comment': {'type': 'Optional[str]', 'location': 'body', 'description': 'Notebook comment'}
        },
        'required': ['database', 'schema', 'name']
    },
    'get_notebook': {
        'method': 'GET',
        'path': '/databases/{database}/schemas/{schema}/notebooks/{name}',
        'description': 'Get a specific notebook',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Notebook name'}
        },
        'required': ['database', 'schema', 'name']
    },
    'delete_notebook': {
        'method': 'DELETE',
        'path': '/databases/{database}/schemas/{schema}/notebooks/{name}',
        'description': 'Drop a notebook',
        'parameters': {
            'database': {'type': 'str', 'location': 'path', 'description': 'Database name'},
            'schema': {'type': 'str', 'location': 'path', 'description': 'Schema name'},
            'name': {'type': 'str', 'location': 'path', 'description': 'Notebook name'},
            'if_exists': {'type': 'Optional[bool]', 'location': 'query', 'description': 'Only drop if exists'}
        },
        'required': ['database', 'schema', 'name']
    },
}


class SnowflakeDataSourceGenerator:
    """Generator for comprehensive Snowflake REST API datasource class."""

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace('-', '_').replace('.', '_').replace('/', '_')
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == '_'):
            sanitized = f"param_{sanitized}"
        return sanitized

    def _get_api_param_name(self, param_name: str) -> str:
        """Convert parameter name to Snowflake API format (camelCase)."""
        param_mapping = {
            'show_limit': 'showLimit',
            'from_name': 'fromName',
            'starts_with': 'startsWith',
            'if_exists': 'ifExists',
            'create_mode': 'createMode',
            'copy_grants': 'copyGrants',
            'data_retention_time_in_days': 'dataRetentionTimeInDays',
            'max_data_extension_time_in_days': 'maxDataExtensionTimeInDays',
            'default_ddl_collation': 'defaultDdlCollation',
            'managed_access': 'managedAccess',
            'cluster_by': 'clusterBy',
            'is_secure': 'isSecure',
            'warehouse_size': 'warehouseSize',
            'warehouse_type': 'warehouseType',
            'auto_suspend': 'autoSuspend',
            'auto_resume': 'autoResume',
            'initially_suspended': 'initiallySuspended',
            'min_cluster_count': 'minClusterCount',
            'max_cluster_count': 'maxClusterCount',
            'scaling_policy': 'scalingPolicy',
            'enable_query_acceleration': 'enableQueryAcceleration',
            'query_acceleration_max_scale_factor': 'queryAccelerationMaxScaleFactor',
            'if_suspended': 'ifSuspended',
            'if_running': 'ifRunning',
            'login_name': 'loginName',
            'display_name': 'displayName',
            'first_name': 'firstName',
            'last_name': 'lastName',
            'default_role': 'defaultRole',
            'default_warehouse': 'defaultWarehouse',
            'default_namespace': 'defaultNamespace',
            'must_change_password': 'mustChangePassword',
            'root_only': 'rootOnly',
            'allow_overlapping_execution': 'allowOverlappingExecution',
            'retry_last': 'retryLast',
            'source_type': 'sourceType',
            'source_name': 'sourceName',
            'append_only': 'appendOnly',
            'show_initial_rows': 'showInitialRows',
            'storage_integration': 'storageIntegration',
            'directory_table': 'directoryTable',
            'copy_statement': 'copyStatement',
            'auto_ingest': 'autoIngest',
            'aws_sns_topic': 'awsSnsTopic',
            'allowed_ip_list': 'allowedIpList',
            'blocked_ip_list': 'blockedIpList',
            'return_type': 'returnType',
            'runtime_version': 'runtimeVersion',
            'execute_as': 'executeAs',
            'name_with_args': 'nameWithArgs',
            'min_nodes': 'minNodes',
            'max_nodes': 'maxNodes',
            'instance_family': 'instanceFamily',
            'auto_suspend_secs': 'autoSuspendSecs',
        }
        return param_mapping.get(param_name, param_name)

    def _build_query_params(self, endpoint_info: Dict) -> List[str]:
        """Build query parameter handling code."""
        lines = ["        query_params = []"]

        for param_name, param_info in endpoint_info['parameters'].items():
            if param_info['location'] == 'query':
                sanitized_name = self._sanitize_parameter_name(param_name)
                api_param_name = self._get_api_param_name(param_name)

                if 'Optional[bool]' in param_info['type']:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params.append(('{api_param_name}', 'true' if {sanitized_name} else 'false'))"
                    ])
                elif 'Optional[int]' in param_info['type']:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params.append(('{api_param_name}', str({sanitized_name})))"
                    ])
                else:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params.append(('{api_param_name}', {sanitized_name}))"
                    ])

        return lines

    def _build_path_formatting(self, path: str, endpoint_info: Dict) -> str:
        """Build URL path with parameter substitution."""
        path_params = [name for name, info in endpoint_info['parameters'].items()
                      if info['location'] == 'path']

        if path_params:
            format_dict = ", ".join(f"{param}={self._sanitize_parameter_name(param)}"
                                  for param in path_params)
            return f'        url = self.base_url + "{path}".format({format_dict})'
        else:
            return f'        url = self.base_url + "{path}"'

    def _build_request_body(self, endpoint_info: Dict, method_name: str = '') -> List[str]:
        """Build request body handling."""
        body_params = {name: info for name, info in endpoint_info['parameters'].items()
                      if info['location'] == 'body'}

        if not body_params:
            return []

        lines = ["        body = {}"]

        for param_name, param_info in body_params.items():
            sanitized_name = self._sanitize_parameter_name(param_name)
            api_param_name = self._get_api_param_name(param_name)

            if param_name in endpoint_info['required']:
                lines.append(f"        body['{api_param_name}'] = {sanitized_name}")
            else:
                lines.extend([
                    f"        if {sanitized_name} is not None:",
                    f"            body['{api_param_name}'] = {sanitized_name}"
                ])

        return lines

    def _generate_method_signature(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate method signature with explicit parameters."""
        params = ["self"]

        # Add required parameters first
        for param_name in endpoint_info['required']:
            if param_name in endpoint_info['parameters']:
                param_info = endpoint_info['parameters'][param_name]
                sanitized_name = self._sanitize_parameter_name(param_name)
                params.append(f"{sanitized_name}: {param_info['type']}")

        # Add optional parameters
        for param_name, param_info in endpoint_info['parameters'].items():
            if param_name not in endpoint_info['required']:
                sanitized_name = self._sanitize_parameter_name(param_name)
                if param_info['type'].startswith('Optional['):
                    params.append(f"{sanitized_name}: {param_info['type']} = None")
                else:
                    inner_type = param_info['type']
                    params.append(f"{sanitized_name}: Optional[{inner_type}] = None")

        signature_params = ",\n        ".join(params)
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> SnowflakeResponse:"

    def _generate_method_docstring(self, endpoint_info: Dict) -> List[str]:
        """Generate method docstring."""
        lines = [f'        """{endpoint_info["description"]}', ""]

        if endpoint_info['parameters']:
            lines.append("        Args:")
            for param_name, param_info in endpoint_info['parameters'].items():
                sanitized_name = self._sanitize_parameter_name(param_name)
                lines.append(f"            {sanitized_name}: {param_info['description']}")
            lines.append("")

        lines.extend([
            "        Returns:",
            "            SnowflakeResponse with operation result",
            '        """'
        ])

        return lines

    def _generate_method(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate a complete method for an API endpoint."""
        lines = []

        # Method signature
        lines.append(self._generate_method_signature(method_name, endpoint_info))

        # Docstring
        lines.extend(self._generate_method_docstring(endpoint_info))

        # Query parameters
        query_lines = self._build_query_params(endpoint_info)
        if len(query_lines) > 1:
            lines.extend(query_lines)
            lines.append("")

        # URL construction
        lines.append(self._build_path_formatting(endpoint_info['path'], endpoint_info))

        # Add query string if there are query parameters
        if len(query_lines) > 1:
            lines.extend([
                "        if query_params:",
                "            query_string = urlencode(query_params)",
                '            url += f"?{query_string}"'
            ])

        # Request body
        body_lines = self._build_request_body(endpoint_info, method_name)
        if body_lines:
            lines.append("")
            lines.extend(body_lines)

        # Headers
        lines.append("")
        lines.append("        headers = self.http.headers.copy()")
        if endpoint_info['method'] in ['POST', 'PATCH', 'PUT']:
            lines.append('        headers["Content-Type"] = "application/json"')

        # Request construction
        lines.append("")
        lines.append("        request = HTTPRequest(")
        lines.append(f'            method="{endpoint_info["method"]}",')
        lines.append("            url=url,")
        if body_lines:
            lines.append("            headers=headers,")
            lines.append("            body=body")
        else:
            lines.append("            headers=headers")
        lines.append("        )")

        # Request execution
        lines.extend([
            "",
            "        try:",
            "            response = await self.http.execute(request)",
            "            response_data = response.json() if response.text() else None",
            "            return SnowflakeResponse(",
            "                success=response.status < 400,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < 400 else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return SnowflakeResponse(success=False, error=str(e), message="Failed to execute {method_name}")'
        ])

        self.generated_methods.append({
            'name': method_name,
            'endpoint': endpoint_info['path'],
            'method': endpoint_info['method'],
            'description': endpoint_info['description']
        })

        return "\n".join(lines)

    def generate_snowflake_datasource(self) -> str:
        """Generate the complete Snowflake datasource class."""

        class_lines = [
            '"""',
            'Snowflake REST API DataSource - Auto-generated API wrapper',
            '',
            'Generated from Snowflake REST API v2 documentation.',
            'Uses HTTP client for direct REST API interactions.',
            'All methods have explicit parameter signatures - NO Any type, NO **kwargs.',
            '"""',
            '',
            'import logging',
            'from typing import Dict, List, Optional',
            'from urllib.parse import urlencode',
            '',
            'from app.sources.client.http.http_request import HTTPRequest',
            'from app.sources.client.snowflake.snowflake import SnowflakeClient, SnowflakeResponse',
            '',
            'logger = logging.getLogger(__name__)',
            '',
            '',
            'class SnowflakeDataSource:',
            '    """Snowflake REST API v2 DataSource',
            '    ',
            '    Provides async wrapper methods for Snowflake REST API v2 operations:',
            '    - Database, Schema, Table, View operations',
            '    - Warehouse management',
            '    - User and Role management',
            '    - Stage, Task, Stream, Pipe operations',
            '    - Alert, Network Policy, Function, Procedure operations',
            '    - Compute Pool, Notebook operations',
            '    ',
            '    All methods have explicit parameter signatures - NO Any type, NO **kwargs.',
            '    All methods return SnowflakeResponse objects.',
            '    """',
            '',
            '    def __init__(self, client: SnowflakeClient) -> None:',
            '        """Initialize with SnowflakeClient.',
            '        ',
            '        Args:',
            '            client: SnowflakeClient instance with configured authentication',
            '        """',
            '        self._client = client',
            '        self.http = client.get_client()',
            '        if self.http is None:',
            "            raise ValueError('HTTP client is not initialized')",
            '        try:',
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            '        except AttributeError as exc:',
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            '',
            "    def get_data_source(self) -> 'SnowflakeDataSource':",
            '        """Return the data source instance."""',
            '        return self',
            '',
            '    def get_client(self) -> SnowflakeClient:',
            '        """Return the underlying SnowflakeClient."""',
            '        return self._client',
            '',
        ]

        # Generate all API methods
        for method_name, endpoint_info in SNOWFLAKE_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Snowflake datasource to a file."""
        if filename is None:
            filename = "snowflake.py"

        # Output to app/sources/external/snowflake/
        script_dir = Path(__file__).parent if __file__ else Path('.')
        snowflake_dir = script_dir.parent / 'app' / 'sources' / 'external' / 'snowflake'
        snowflake_dir.mkdir(parents=True, exist_ok=True)

        full_path = snowflake_dir / filename

        class_code = self.generate_snowflake_datasource()

        full_path.write_text(class_code, encoding='utf-8')

        print(f"Generated Snowflake data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by category
        categories = {
            'Database': 0,
            'Schema': 0,
            'Table': 0,
            'View': 0,
            'Warehouse': 0,
            'User': 0,
            'Role': 0,
            'Task': 0,
            'Stream': 0,
            'Stage': 0,
            'Pipe': 0,
            'Alert': 0,
            'Network Policy': 0,
            'Function': 0,
            'Procedure': 0,
            'Compute Pool': 0,
            'Notebook': 0,
        }

        for method in self.generated_methods:
            name = method['name']
            if 'database' in name and 'schema' not in name:
                categories['Database'] += 1
            elif 'schema' in name:
                categories['Schema'] += 1
            elif 'table' in name:
                categories['Table'] += 1
            elif 'view' in name:
                categories['View'] += 1
            elif 'warehouse' in name:
                categories['Warehouse'] += 1
            elif 'user' in name:
                categories['User'] += 1
            elif 'role' in name:
                categories['Role'] += 1
            elif 'task' in name:
                categories['Task'] += 1
            elif 'stream' in name:
                categories['Stream'] += 1
            elif 'stage' in name:
                categories['Stage'] += 1
            elif 'pipe' in name:
                categories['Pipe'] += 1
            elif 'alert' in name:
                categories['Alert'] += 1
            elif 'network' in name:
                categories['Network Policy'] += 1
            elif 'function' in name:
                categories['Function'] += 1
            elif 'procedure' in name:
                categories['Procedure'] += 1
            elif 'compute' in name:
                categories['Compute Pool'] += 1
            elif 'notebook' in name:
                categories['Notebook'] += 1

        print(f"\nMethods by Category:")
        for category, count in categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for Snowflake data source generator."""
    import argparse

    parser = argparse.ArgumentParser(description='Generate Snowflake REST API data source')
    parser.add_argument('--filename', '-f', help='Output filename (optional)')

    args = parser.parse_args()

    try:
        generator = SnowflakeDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Snowflake data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
