# ruff: noqa
"""
Coda REST API Code Generator

Generates CodaDataSource class covering Coda API v1:
- User / Account operations
- Doc CRUD and management
- Table and Row operations
- Column management
- Page operations
- Formula and Control access
- Permission management
- Folder management
- Analytics
- Category listing
- Workspace management
- Pack management

The generated DataSource accepts a CodaClient and uses the client's
base URL (https://coda.io/apis/v1) to construct request URLs.

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional


# ================================================================================
# Coda API Endpoints - organized by resource
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which is https://coda.io/apis/v1)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
# ================================================================================

CODA_API_ENDPOINTS = {
    # ================================================================================
    # USER / ACCOUNT
    # ================================================================================
    "whoami": {
        "method": "GET",
        "path": "/whoami",
        "description": "Get information about the current user",
        "parameters": {},
        "required": [],
    },

    # ================================================================================
    # DOCS
    # ================================================================================
    "list_docs": {
        "method": "GET",
        "path": "/docs",
        "description": "List available Coda docs",
        "parameters": {
            "is_owner": {"type": "Optional[bool]", "location": "query", "description": "Show only docs owned by the user"},
            "query": {"type": "Optional[str]", "location": "query", "description": "Search term to filter docs"},
            "source_doc": {"type": "Optional[str]", "location": "query", "description": "Show only docs copied from the specified source doc"},
            "is_starred": {"type": "Optional[bool]", "location": "query", "description": "Show only starred docs"},
            "in_gallery": {"type": "Optional[bool]", "location": "query", "description": "Show only docs in the gallery"},
            "workspace_id": {"type": "Optional[str]", "location": "query", "description": "Show only docs in the given workspace"},
            "folder_id": {"type": "Optional[str]", "location": "query", "description": "Show only docs in the given folder"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "page_token": {"type": "Optional[str]", "location": "query", "description": "An opaque token for pagination"},
        },
        "required": [],
    },
    "get_doc": {
        "method": "GET",
        "path": "/docs/{doc_id}",
        "description": "Get info about a specific doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
        },
        "required": ["doc_id"],
    },
    "create_doc": {
        "method": "POST",
        "path": "/docs",
        "description": "Create a new Coda doc",
        "parameters": {
            "title": {"type": "Optional[str]", "location": "body", "description": "Title of the new doc"},
            "source_doc": {"type": "Optional[str]", "location": "body", "description": "ID of a doc to copy"},
            "timezone": {"type": "Optional[str]", "location": "body", "description": "Timezone for the doc"},
            "folder_id": {"type": "Optional[str]", "location": "body", "description": "ID of the folder to create the doc in"},
        },
        "required": [],
    },
    "update_doc": {
        "method": "PATCH",
        "path": "/docs/{doc_id}",
        "description": "Update a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "title": {"type": "Optional[str]", "location": "body", "description": "New title for the doc"},
            "icon_name": {"type": "Optional[str]", "location": "body", "description": "New icon for the doc"},
        },
        "required": ["doc_id"],
    },
    "delete_doc": {
        "method": "DELETE",
        "path": "/docs/{doc_id}",
        "description": "Delete a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc to delete"},
        },
        "required": ["doc_id"],
    },

    # ================================================================================
    # FOLDERS
    # ================================================================================
    "list_folders": {
        "method": "GET",
        "path": "/folders",
        "description": "List folders",
        "parameters": {
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "page_token": {"type": "Optional[str]", "location": "query", "description": "An opaque token for pagination"},
        },
        "required": [],
    },
    "get_folder": {
        "method": "GET",
        "path": "/folders/{folder_id}",
        "description": "Get info about a specific folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "path", "description": "The ID of the folder"},
        },
        "required": ["folder_id"],
    },
    "create_folder": {
        "method": "POST",
        "path": "/folders",
        "description": "Create a new folder",
        "parameters": {
            "name": {"type": "str", "location": "body", "description": "Name of the folder"},
            "parent_folder_id": {"type": "Optional[str]", "location": "body", "description": "Parent folder ID"},
        },
        "required": ["name"],
    },
    "update_folder": {
        "method": "PATCH",
        "path": "/folders/{folder_id}",
        "description": "Update a folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "path", "description": "The ID of the folder"},
            "name": {"type": "Optional[str]", "location": "body", "description": "New name for the folder"},
        },
        "required": ["folder_id"],
    },
    "delete_folder": {
        "method": "DELETE",
        "path": "/folders/{folder_id}",
        "description": "Delete a folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "path", "description": "The ID of the folder to delete"},
        },
        "required": ["folder_id"],
    },

    # ================================================================================
    # TABLES
    # ================================================================================
    "list_tables": {
        "method": "GET",
        "path": "/docs/{doc_id}/tables",
        "description": "List tables in a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "page_token": {"type": "Optional[str]", "location": "query", "description": "An opaque token for pagination"},
            "sort_by": {"type": "Optional[str]", "location": "query", "description": "Sort order of the results"},
            "table_types": {"type": "Optional[str]", "location": "query", "description": "Comma-separated list of table types to include"},
        },
        "required": ["doc_id"],
    },
    "get_table": {
        "method": "GET",
        "path": "/docs/{doc_id}/tables/{table_id_or_name}",
        "description": "Get info about a specific table",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "table_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the table"},
        },
        "required": ["doc_id", "table_id_or_name"],
    },

    # ================================================================================
    # ROWS
    # ================================================================================
    "list_rows": {
        "method": "GET",
        "path": "/docs/{doc_id}/tables/{table_id_or_name}/rows",
        "description": "List rows in a table",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "table_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the table"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "page_token": {"type": "Optional[str]", "location": "query", "description": "An opaque token for pagination"},
            "query": {"type": "Optional[str]", "location": "query", "description": "Search query to filter rows"},
            "sort_by": {"type": "Optional[str]", "location": "query", "description": "Sort order of the results"},
            "use_column_names": {"type": "Optional[bool]", "location": "query", "description": "Use column names instead of column IDs in the response"},
            "value_format": {"type": "Optional[str]", "location": "query", "description": "Format of cell values (simple, simpleWithArrays, rich)"},
            "visible_only": {"type": "Optional[bool]", "location": "query", "description": "Show only visible rows"},
        },
        "required": ["doc_id", "table_id_or_name"],
    },
    "get_row": {
        "method": "GET",
        "path": "/docs/{doc_id}/tables/{table_id_or_name}/rows/{row_id_or_name}",
        "description": "Get a specific row in a table",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "table_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the table"},
            "row_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the row"},
            "use_column_names": {"type": "Optional[bool]", "location": "query", "description": "Use column names instead of column IDs"},
            "value_format": {"type": "Optional[str]", "location": "query", "description": "Format of cell values"},
        },
        "required": ["doc_id", "table_id_or_name", "row_id_or_name"],
    },
    "upsert_rows": {
        "method": "POST",
        "path": "/docs/{doc_id}/tables/{table_id_or_name}/rows",
        "description": "Insert or upsert rows in a table",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "table_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the table"},
            "rows": {"type": "list[dict[str, Any]]", "location": "body", "description": "Array of row objects to insert"},
            "key_columns": {"type": "Optional[list[str]]", "location": "body", "description": "Optional column IDs for upsert key matching"},
        },
        "required": ["doc_id", "table_id_or_name", "rows"],
    },
    "update_row": {
        "method": "PUT",
        "path": "/docs/{doc_id}/tables/{table_id_or_name}/rows/{row_id_or_name}",
        "description": "Update a specific row in a table",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "table_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the table"},
            "row_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the row"},
            "row": {"type": "dict[str, Any]", "location": "body", "description": "Row object with cells to update"},
        },
        "required": ["doc_id", "table_id_or_name", "row_id_or_name", "row"],
    },
    "delete_row": {
        "method": "DELETE",
        "path": "/docs/{doc_id}/tables/{table_id_or_name}/rows/{row_id_or_name}",
        "description": "Delete a specific row from a table",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "table_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the table"},
            "row_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the row to delete"},
        },
        "required": ["doc_id", "table_id_or_name", "row_id_or_name"],
    },
    "delete_rows": {
        "method": "DELETE",
        "path": "/docs/{doc_id}/tables/{table_id_or_name}/rows",
        "description": "Delete multiple rows from a table",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "table_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the table"},
            "row_ids": {"type": "list[str]", "location": "body", "description": "Array of row IDs to delete"},
        },
        "required": ["doc_id", "table_id_or_name", "row_ids"],
    },
    "push_button": {
        "method": "POST",
        "path": "/docs/{doc_id}/tables/{table_id_or_name}/rows/{row_id_or_name}/buttons/{column_id_or_name}",
        "description": "Push a button on a row in a table",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "table_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the table"},
            "row_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the row"},
            "column_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the column (button)"},
        },
        "required": ["doc_id", "table_id_or_name", "row_id_or_name", "column_id_or_name"],
    },

    # ================================================================================
    # COLUMNS
    # ================================================================================
    "list_columns": {
        "method": "GET",
        "path": "/docs/{doc_id}/tables/{table_id_or_name}/columns",
        "description": "List columns in a table",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "table_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the table"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "page_token": {"type": "Optional[str]", "location": "query", "description": "An opaque token for pagination"},
            "visible_only": {"type": "Optional[bool]", "location": "query", "description": "Show only visible columns"},
        },
        "required": ["doc_id", "table_id_or_name"],
    },
    "get_column": {
        "method": "GET",
        "path": "/docs/{doc_id}/tables/{table_id_or_name}/columns/{column_id_or_name}",
        "description": "Get info about a specific column",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "table_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the table"},
            "column_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the column"},
        },
        "required": ["doc_id", "table_id_or_name", "column_id_or_name"],
    },

    # ================================================================================
    # PAGES
    # ================================================================================
    "list_pages": {
        "method": "GET",
        "path": "/docs/{doc_id}/pages",
        "description": "List pages in a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "page_token": {"type": "Optional[str]", "location": "query", "description": "An opaque token for pagination"},
        },
        "required": ["doc_id"],
    },
    "get_page": {
        "method": "GET",
        "path": "/docs/{doc_id}/pages/{page_id_or_name}",
        "description": "Get info about a specific page",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "page_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the page"},
        },
        "required": ["doc_id", "page_id_or_name"],
    },
    "create_page": {
        "method": "POST",
        "path": "/docs/{doc_id}/pages",
        "description": "Create a new page in a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "name": {"type": "str", "location": "body", "description": "Name of the new page"},
            "subtitle": {"type": "Optional[str]", "location": "body", "description": "Subtitle for the page"},
            "icon_name": {"type": "Optional[str]", "location": "body", "description": "Name of the icon for the page"},
            "image_url": {"type": "Optional[str]", "location": "body", "description": "URL of the cover image for the page"},
            "parent_page_id": {"type": "Optional[str]", "location": "body", "description": "ID of the parent page"},
            "page_content": {"type": "Optional[dict[str, Any]]", "location": "body", "description": "Content for the page"},
        },
        "required": ["doc_id", "name"],
    },
    "update_page": {
        "method": "PUT",
        "path": "/docs/{doc_id}/pages/{page_id_or_name}",
        "description": "Update a page in a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "page_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the page"},
            "name": {"type": "Optional[str]", "location": "body", "description": "New name for the page"},
            "subtitle": {"type": "Optional[str]", "location": "body", "description": "New subtitle for the page"},
            "icon_name": {"type": "Optional[str]", "location": "body", "description": "Name of the icon for the page"},
            "image_url": {"type": "Optional[str]", "location": "body", "description": "URL of the cover image for the page"},
        },
        "required": ["doc_id", "page_id_or_name"],
    },
    "delete_page": {
        "method": "DELETE",
        "path": "/docs/{doc_id}/pages/{page_id_or_name}",
        "description": "Delete a page from a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "page_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the page"},
        },
        "required": ["doc_id", "page_id_or_name"],
    },
    "list_page_content": {
        "method": "GET",
        "path": "/docs/{doc_id}/pages/{page_id_or_name}/content",
        "description": "List content on a page",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "page_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the page"},
        },
        "required": ["doc_id", "page_id_or_name"],
    },
    "begin_page_content_export": {
        "method": "POST",
        "path": "/docs/{doc_id}/pages/{page_id_or_name}/export",
        "description": "Begin exporting page content",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "page_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the page"},
            "output_format": {"type": "str", "location": "body", "description": "Output format for export (html or markdown)"},
        },
        "required": ["doc_id", "page_id_or_name", "output_format"],
    },
    "get_page_content_export_status": {
        "method": "GET",
        "path": "/docs/{doc_id}/pages/{page_id_or_name}/export/{request_id}",
        "description": "Get the status of a page content export",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "page_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the page"},
            "request_id": {"type": "str", "location": "path", "description": "The ID of the export request"},
        },
        "required": ["doc_id", "page_id_or_name", "request_id"],
    },

    # ================================================================================
    # FORMULAS
    # ================================================================================
    "list_formulas": {
        "method": "GET",
        "path": "/docs/{doc_id}/formulas",
        "description": "List named formulas in a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "page_token": {"type": "Optional[str]", "location": "query", "description": "An opaque token for pagination"},
            "sort_by": {"type": "Optional[str]", "location": "query", "description": "Sort order of the results"},
        },
        "required": ["doc_id"],
    },
    "get_formula": {
        "method": "GET",
        "path": "/docs/{doc_id}/formulas/{formula_id_or_name}",
        "description": "Get info about a specific formula",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "formula_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the formula"},
        },
        "required": ["doc_id", "formula_id_or_name"],
    },

    # ================================================================================
    # CONTROLS
    # ================================================================================
    "list_controls": {
        "method": "GET",
        "path": "/docs/{doc_id}/controls",
        "description": "List controls in a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "page_token": {"type": "Optional[str]", "location": "query", "description": "An opaque token for pagination"},
            "sort_by": {"type": "Optional[str]", "location": "query", "description": "Sort order of the results"},
        },
        "required": ["doc_id"],
    },
    "get_control": {
        "method": "GET",
        "path": "/docs/{doc_id}/controls/{control_id_or_name}",
        "description": "Get info about a specific control",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "control_id_or_name": {"type": "str", "location": "path", "description": "The ID or name of the control"},
        },
        "required": ["doc_id", "control_id_or_name"],
    },

    # ================================================================================
    # PERMISSIONS
    # ================================================================================
    "get_sharing_metadata": {
        "method": "GET",
        "path": "/docs/{doc_id}/acl/metadata",
        "description": "Get sharing metadata for a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
        },
        "required": ["doc_id"],
    },
    "list_permissions": {
        "method": "GET",
        "path": "/docs/{doc_id}/acl/permissions",
        "description": "List permissions for a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "page_token": {"type": "Optional[str]", "location": "query", "description": "An opaque token for pagination"},
        },
        "required": ["doc_id"],
    },
    "add_permission": {
        "method": "POST",
        "path": "/docs/{doc_id}/acl/permissions",
        "description": "Add a permission to a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "access": {"type": "str", "location": "body", "description": "Type of access (readonly, write, comment, none)"},
            "principal": {"type": "dict[str, Any]", "location": "body", "description": "Principal to grant access to (email or type)"},
            "suppress_notification": {"type": "Optional[bool]", "location": "body", "description": "Whether to suppress notification email"},
        },
        "required": ["doc_id", "access", "principal"],
    },
    "delete_permission": {
        "method": "DELETE",
        "path": "/docs/{doc_id}/acl/permissions/{permission_id}",
        "description": "Delete a permission from a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "permission_id": {"type": "str", "location": "path", "description": "The ID of the permission"},
        },
        "required": ["doc_id", "permission_id"],
    },
    "search_principals": {
        "method": "GET",
        "path": "/docs/{doc_id}/acl/principals/search",
        "description": "Search principals for a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "query": {"type": "Optional[str]", "location": "query", "description": "Search query"},
        },
        "required": ["doc_id"],
    },
    "get_acl_settings": {
        "method": "GET",
        "path": "/docs/{doc_id}/acl/settings",
        "description": "Get ACL settings for a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
        },
        "required": ["doc_id"],
    },
    "update_acl_settings": {
        "method": "PATCH",
        "path": "/docs/{doc_id}/acl/settings",
        "description": "Update ACL settings for a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "allow_editors_to_change_permissions": {"type": "Optional[bool]", "location": "body", "description": "Whether editors can change permissions"},
            "allow_copying": {"type": "Optional[bool]", "location": "body", "description": "Whether copying is allowed"},
            "allow_view_all_pages": {"type": "Optional[bool]", "location": "body", "description": "Whether viewing all pages is allowed"},
        },
        "required": ["doc_id"],
    },

    # ================================================================================
    # PUBLISHING
    # ================================================================================
    "publish_doc": {
        "method": "PUT",
        "path": "/docs/{doc_id}/publish",
        "description": "Publish a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "slug": {"type": "Optional[str]", "location": "body", "description": "URL slug for the published doc"},
            "discover_ability": {"type": "Optional[str]", "location": "body", "description": "Discoverability setting"},
            "earn_credit": {"type": "Optional[bool]", "location": "body", "description": "Whether to show Coda credit"},
            "category_names": {"type": "Optional[list[str]]", "location": "body", "description": "Category names for the doc"},
            "mode": {"type": "Optional[str]", "location": "body", "description": "Publishing mode"},
        },
        "required": ["doc_id"],
    },
    "unpublish_doc": {
        "method": "DELETE",
        "path": "/docs/{doc_id}/publish",
        "description": "Unpublish a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
        },
        "required": ["doc_id"],
    },

    # ================================================================================
    # AUTOMATIONS
    # ================================================================================
    "trigger_automation": {
        "method": "POST",
        "path": "/docs/{doc_id}/hooks/automation/{rule_id}",
        "description": "Trigger a webhook automation",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "rule_id": {"type": "str", "location": "path", "description": "The ID of the automation rule"},
            "payload": {"type": "Optional[dict[str, Any]]", "location": "body", "description": "Optional payload for the automation"},
        },
        "required": ["doc_id", "rule_id"],
    },

    # ================================================================================
    # ANALYTICS
    # ================================================================================
    "list_doc_analytics": {
        "method": "GET",
        "path": "/analytics/docs",
        "description": "List doc analytics",
        "parameters": {
            "doc_ids": {"type": "Optional[str]", "location": "query", "description": "Comma-separated list of doc IDs"},
            "workspace_id": {"type": "Optional[str]", "location": "query", "description": "Workspace ID to filter"},
            "query": {"type": "Optional[str]", "location": "query", "description": "Search query"},
            "is_published": {"type": "Optional[bool]", "location": "query", "description": "Filter by publish status"},
            "since_date": {"type": "Optional[str]", "location": "query", "description": "Start date (ISO 8601)"},
            "until_date": {"type": "Optional[str]", "location": "query", "description": "End date (ISO 8601)"},
            "scale": {"type": "Optional[str]", "location": "query", "description": "Time scale (daily or cumulative)"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "page_token": {"type": "Optional[str]", "location": "query", "description": "An opaque token for pagination"},
            "order_by": {"type": "Optional[str]", "location": "query", "description": "Sort order"},
        },
        "required": [],
    },
    "list_page_analytics": {
        "method": "GET",
        "path": "/analytics/docs/{doc_id}/pages",
        "description": "List page analytics for a doc",
        "parameters": {
            "doc_id": {"type": "str", "location": "path", "description": "The ID of the doc"},
            "since_date": {"type": "Optional[str]", "location": "query", "description": "Start date (ISO 8601)"},
            "until_date": {"type": "Optional[str]", "location": "query", "description": "End date (ISO 8601)"},
            "scale": {"type": "Optional[str]", "location": "query", "description": "Time scale (daily or cumulative)"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "page_token": {"type": "Optional[str]", "location": "query", "description": "An opaque token for pagination"},
            "order_by": {"type": "Optional[str]", "location": "query", "description": "Sort order"},
        },
        "required": ["doc_id"],
    },

    # ================================================================================
    # WORKSPACES
    # ================================================================================
    "list_workspace_members": {
        "method": "GET",
        "path": "/workspaces/{workspace_id}/users",
        "description": "List workspace members",
        "parameters": {
            "workspace_id": {"type": "str", "location": "path", "description": "The ID of the workspace"},
            "included_roles": {"type": "Optional[str]", "location": "query", "description": "Filter by roles"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "page_token": {"type": "Optional[str]", "location": "query", "description": "An opaque token for pagination"},
        },
        "required": ["workspace_id"],
    },
    "change_user_role": {
        "method": "POST",
        "path": "/workspaces/{workspace_id}/users/role",
        "description": "Change a user role in a workspace",
        "parameters": {
            "workspace_id": {"type": "str", "location": "path", "description": "The ID of the workspace"},
            "email": {"type": "str", "location": "body", "description": "Email of the user"},
            "new_role": {"type": "str", "location": "body", "description": "New role for the user"},
        },
        "required": ["workspace_id", "email", "new_role"],
    },

    # ================================================================================
    # MISCELLANEOUS
    # ================================================================================
    "resolve_browser_link": {
        "method": "GET",
        "path": "/resolveBrowserLink",
        "description": "Resolve a browser link to a Coda resource",
        "parameters": {
            "link_url": {"type": "str", "location": "query", "description": "The URL to resolve", "api_name": "url"},
            "degrade_gracefully": {"type": "Optional[bool]", "location": "query", "description": "Whether to degrade gracefully on errors"},
        },
        "required": ["link_url"],
    },
    "get_mutation_status": {
        "method": "GET",
        "path": "/mutationStatus/{request_id}",
        "description": "Get the status of a mutation",
        "parameters": {
            "request_id": {"type": "str", "location": "path", "description": "The ID of the mutation request"},
        },
        "required": ["request_id"],
    },

    # ================================================================================
    # CATEGORIES
    # ================================================================================
    "list_categories": {
        "method": "GET",
        "path": "/categories",
        "description": "List available doc categories",
        "parameters": {},
        "required": [],
    },
}


class CodaDataSourceGenerator:
    """Generator for comprehensive Coda REST API datasource class.

    Generates methods for Coda API v1 endpoints.
    The generated DataSource class accepts a CodaClient whose base URL
    setting determines the API endpoint.
    """

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace("-", "_").replace(".", "_").replace("/", "_")
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == "_"):
            sanitized = f"param_{sanitized}"
        return sanitized

    def _build_query_params(self, endpoint_info: Dict) -> List[str]:
        """Build query parameter handling code."""
        lines = ["        query_params: dict[str, Any] = {}"]
        required_set = set(endpoint_info.get("required", []))

        for param_name, param_info in endpoint_info["parameters"].items():
            if param_info["location"] == "query":
                sanitized_name = self._sanitize_parameter_name(param_name)
                api_name = param_info.get("api_name", param_name)
                is_required = param_name in required_set

                if is_required:
                    if "bool" in param_info["type"]:
                        lines.append(
                            f"        query_params['{api_name}'] = str({sanitized_name}).lower()"
                        )
                    else:
                        lines.append(
                            f"        query_params['{api_name}'] = {sanitized_name}"
                        )
                elif "Optional[bool]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}'] = str({sanitized_name}).lower()",
                    ])
                elif "Optional[int]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}'] = str({sanitized_name})",
                    ])
                elif "List[" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}[]'] = {sanitized_name}",
                    ])
                else:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}'] = {sanitized_name}",
                    ])

        return lines

    def _build_path_formatting(self, path: str, endpoint_info: Dict) -> str:
        """Build URL path with parameter substitution."""
        path_params = [
            name
            for name, info in endpoint_info["parameters"].items()
            if info["location"] == "path"
        ]

        if path_params:
            format_dict = ", ".join(
                f"{param}={self._sanitize_parameter_name(param)}"
                for param in path_params
            )
            return f'        url = self.base_url + "{path}".format({format_dict})'
        else:
            return f'        url = self.base_url + "{path}"'

    def _build_request_body(self, endpoint_info: Dict) -> List[str]:
        """Build request body handling."""
        body_params = {
            name: info
            for name, info in endpoint_info["parameters"].items()
            if info["location"] == "body"
        }

        if not body_params:
            return []

        lines = ["        body: dict[str, Any] = {}"]

        for param_name, param_info in body_params.items():
            sanitized_name = self._sanitize_parameter_name(param_name)

            if param_name in endpoint_info["required"]:
                lines.append(f"        body['{param_name}'] = {sanitized_name}")
            else:
                lines.extend([
                    f"        if {sanitized_name} is not None:",
                    f"            body['{param_name}'] = {sanitized_name}",
                ])

        return lines

    @staticmethod
    def _modernize_type(type_str: str) -> str:
        """Convert typing-style annotations to modern Python 3.10+ syntax.

        Optional[str] -> str | None, Dict[str, Any] -> dict[str, Any],
        List[str] -> list[str], etc.
        """
        if type_str.startswith("Optional[") and type_str.endswith("]"):
            inner = type_str[len("Optional["):-1]
            inner = CodaDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = CodaDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                CodaDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = CodaDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                CodaDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"list[{modernized}]"
        if type_str == "List":
            return "list"
        return type_str

    @staticmethod
    def _split_type_args(s: str) -> List[str]:
        """Split type arguments respecting nested brackets."""
        parts = []
        depth = 0
        current = ""
        for ch in s:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            parts.append(current.strip())
        return parts

    def _generate_method_signature(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate method signature with explicit parameters."""
        params = ["self"]
        has_any_bool = False

        # Collect required params, split into non-bool and bool groups
        required_non_bool: List[str] = []
        required_bool: List[str] = []
        for param_name in endpoint_info["required"]:
            if param_name in endpoint_info["parameters"]:
                param_info = endpoint_info["parameters"][param_name]
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                param_str = f"{sanitized_name}: {modern_type}"
                if "bool" in param_info.get("type", ""):
                    required_bool.append(param_str)
                    has_any_bool = True
                else:
                    required_non_bool.append(param_str)

        # Collect optional parameters
        optional_params: List[str] = []
        for param_name, param_info in endpoint_info["parameters"].items():
            if param_name not in endpoint_info["required"]:
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                if "| None" not in modern_type:
                    modern_type = f"{modern_type} | None"
                optional_params.append(f"{sanitized_name}: {modern_type} = None")
                if "bool" in param_info.get("type", ""):
                    has_any_bool = True

        # Build signature: non-bool required first, then * if needed, then bool required + optional
        params.extend(required_non_bool)
        if has_any_bool and (required_bool or optional_params):
            params.append("*")
        params.extend(required_bool)
        params.extend(optional_params)

        signature_params = ",\n        ".join(params)
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> CodaResponse:"

    def _generate_method_docstring(self, endpoint_info: Dict) -> List[str]:
        """Generate method docstring."""
        lines = [f'        """{endpoint_info["description"]}', ""]

        if endpoint_info["parameters"]:
            lines.append("        Args:")
            for param_name, param_info in endpoint_info["parameters"].items():
                sanitized_name = self._sanitize_parameter_name(param_name)
                lines.append(
                    f"            {sanitized_name}: {param_info['description']}"
                )
            lines.append("")

        lines.extend([
            "        Returns:",
            "            CodaResponse with operation result",
            '        """',
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
        has_query = any(
            info["location"] == "query"
            for info in endpoint_info["parameters"].values()
        )
        if has_query:
            query_lines = self._build_query_params(endpoint_info)
            lines.extend(query_lines)
            lines.append("")

        # URL construction
        lines.append(self._build_path_formatting(endpoint_info["path"], endpoint_info))

        # Request body
        body_lines = self._build_request_body(endpoint_info)
        if body_lines:
            lines.append("")
            lines.extend(body_lines)

        # Request construction and execution
        lines.append("")
        lines.append("        try:")
        lines.append("            request = HTTPRequest(")
        lines.append(f'                method="{endpoint_info["method"]}",')
        lines.append("                url=url,")
        lines.append('                headers={"Content-Type": "application/json"},')
        if has_query:
            lines.append("                query=query_params,")
        if body_lines:
            lines.append("                body=body,")
        lines.append("            )")
        lines.extend([
            "            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]",
            "            response_data = response.json() if response.text() else None",
            "            return CodaResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return CodaResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_coda_datasource(self) -> str:
        """Generate the complete Coda datasource class."""

        class_lines = [
            '"""',
            "Coda REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Coda REST API v1 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.coda.coda import CodaClient, CodaResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class CodaDataSource:",
            '    """Coda REST API DataSource',
            "",
            "    Provides async wrapper methods for Coda REST API operations:",
            "    - User / Account information",
            "    - Doc CRUD and management",
            "    - Folder management",
            "    - Table and Row operations",
            "    - Column management",
            "    - Page operations",
            "    - Formula and Control access",
            "    - Permission management",
            "    - Publishing",
            "    - Automations",
            "    - Analytics",
            "    - Workspace management",
            "    - Category listing",
            "",
            "    The base URL is determined by the CodaClient's configured base URL",
            "    (default: https://coda.io/apis/v1).",
            "",
            "    All methods return CodaResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: CodaClient) -> None:",
            '        """Initialize with CodaClient.',
            "",
            "        Args:",
            "            client: CodaClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'CodaDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> CodaClient:",
            '        """Return the underlying CodaClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in CODA_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Coda datasource to a file."""
        if filename is None:
            filename = "coda.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        coda_dir = script_dir.parent / "app" / "sources" / "external" / "coda"
        coda_dir.mkdir(parents=True, exist_ok=True)

        full_path = coda_dir / filename

        class_code = self.generate_coda_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Coda data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by category
        resource_categories = {
            "User/Account": 0,
            "Doc": 0,
            "Folder": 0,
            "Table": 0,
            "Row": 0,
            "Column": 0,
            "Page": 0,
            "Formula": 0,
            "Control": 0,
            "Permission": 0,
            "Publishing": 0,
            "Automation": 0,
            "Analytics": 0,
            "Workspace": 0,
            "Category": 0,
            "Misc": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "whoami" in name:
                resource_categories["User/Account"] += 1
            elif "folder" in name:
                resource_categories["Folder"] += 1
            elif "doc" in name and "analytics" not in name:
                resource_categories["Doc"] += 1
            elif "table" in name:
                resource_categories["Table"] += 1
            elif "row" in name or "push_button" in name:
                resource_categories["Row"] += 1
            elif "column" in name:
                resource_categories["Column"] += 1
            elif "page" in name and "analytics" not in name:
                resource_categories["Page"] += 1
            elif "formula" in name:
                resource_categories["Formula"] += 1
            elif "control" in name:
                resource_categories["Control"] += 1
            elif "permission" in name or "sharing" in name or "acl" in name or "principal" in name:
                resource_categories["Permission"] += 1
            elif "publish" in name:
                resource_categories["Publishing"] += 1
            elif "automation" in name or "trigger" in name:
                resource_categories["Automation"] += 1
            elif "analytics" in name:
                resource_categories["Analytics"] += 1
            elif "workspace" in name or "user_role" in name:
                resource_categories["Workspace"] += 1
            elif "categor" in name:
                resource_categories["Category"] += 1
            else:
                resource_categories["Misc"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for Coda data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Coda REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = CodaDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Coda data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
