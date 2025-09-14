# ruff: noqa

# NOTE — Development-only generator (Notion)

import json
import keyword
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# Notion API endpoints based on their official documentation
# https://developers.notion.com/reference/intro
NOTION_ENDPOINTS = {
    # Authentication endpoints
    "authentication": [
        {
            "name": "retrieve_bot_user",
            "method": "GET", 
            "path": "/users/me",
            "description": "Retrieve your token's bot user",
            "parameters": [],
            "request_body": None
        }
    ],
    
    # Block endpoints
    "blocks": [
        {
            "name": "retrieve_block",
            "method": "GET",
            "path": "/blocks/{block_id}",
            "description": "Retrieve a Block object using the ID specified",
            "parameters": [
                {"name": "block_id", "type": "str", "required": True, "location": "path", "description": "Block ID to retrieve"}
            ],
            "request_body": None
        },
        {
            "name": "update_block",
            "method": "PATCH",
            "path": "/blocks/{block_id}",
            "description": "Update the content for the specified block_id based on the block type",
            "parameters": [
                {"name": "block_id", "type": "str", "required": True, "location": "path", "description": "Block ID to update"}
            ],
            "request_body": {"description": "Block object with updated properties"}
        },
        {
            "name": "delete_block",
            "method": "DELETE",
            "path": "/blocks/{block_id}",
            "description": "Set a Block object, including page blocks, to archived: true",
            "parameters": [
                {"name": "block_id", "type": "str", "required": True, "location": "path", "description": "Block ID to delete"}
            ],
            "request_body": None
        },
        {
            "name": "retrieve_block_children",
            "method": "GET",
            "path": "/blocks/{block_id}/children",
            "description": "Return a paginated array of child block objects contained in the block",
            "parameters": [
                {"name": "block_id", "type": "str", "required": True, "location": "path", "description": "Block ID to get children from"},
                {"name": "start_cursor", "type": "str", "required": False, "location": "query", "description": "Pagination cursor"},
                {"name": "page_size", "type": "int", "required": False, "location": "query", "description": "Number of items to return (max 100)"}
            ],
            "request_body": None
        },
        {
            "name": "append_block_children",
            "method": "PATCH",
            "path": "/blocks/{block_id}/children",
            "description": "Create and append new children blocks to the parent block_id specified",
            "parameters": [
                {"name": "block_id", "type": "str", "required": True, "location": "path", "description": "Block ID to append children to"}
            ],
            "request_body": {"description": "Object containing children array of block objects"}
        }
    ],
    
    # Page endpoints
    "pages": [
        {
            "name": "create_page",
            "method": "POST",
            "path": "/pages",
            "description": "Create a new page in the specified database or as a child of an existing page",
            "parameters": [],
            "request_body": {"description": "Page object with parent, properties, and optionally children"}
        },
        {
            "name": "retrieve_page",
            "method": "GET",
            "path": "/pages/{page_id}",
            "description": "Retrieve a Page object using the ID specified",
            "parameters": [
                {"name": "page_id", "type": "str", "required": True, "location": "path", "description": "Page ID to retrieve"},
                {"name": "filter_properties", "type": "List[str]", "required": False, "location": "query", "description": "List of property IDs to filter"}
            ],
            "request_body": None
        },
        {
            "name": "update_page_properties",
            "method": "PATCH",
            "path": "/pages/{page_id}",
            "description": "Update the properties of a page in a database",
            "parameters": [
                {"name": "page_id", "type": "str", "required": True, "location": "path", "description": "Page ID to update"}
            ],
            "request_body": {"description": "Object containing properties to update"}
        },
        {
            "name": "retrieve_page_property_item",
            "method": "GET",
            "path": "/pages/{page_id}/properties/{property_id}",
            "description": "Retrieve a property_item object for a given page_id and property_id",
            "parameters": [
                {"name": "page_id", "type": "str", "required": True, "location": "path", "description": "Page ID"},
                {"name": "property_id", "type": "str", "required": True, "location": "path", "description": "Property ID"},
                {"name": "start_cursor", "type": "str", "required": False, "location": "query", "description": "Pagination cursor"},
                {"name": "page_size", "type": "int", "required": False, "location": "query", "description": "Number of items to return (max 100)"}
            ],
            "request_body": None
        }
    ],
    
    # Database endpoints  
    "databases": [
        {
            "name": "create_database",
            "method": "POST",
            "path": "/databases",
            "description": "Create a database as a subpage in the specified parent page",
            "parameters": [],
            "request_body": {"description": "Database object with parent, title, and properties"}
        },
        {
            "name": "query_database",
            "method": "POST",
            "path": "/databases/{database_id}/query",
            "description": "Get a list of Pages contained in the database, filtered and ordered according to the filter conditions and sort criteria provided in the request",
            "parameters": [
                {"name": "database_id", "type": "str", "required": True, "location": "path", "description": "Database ID to query"}
            ],
            "request_body": {"description": "Query object with filter, sorts, start_cursor, and page_size"}
        },
        {
            "name": "retrieve_database",
            "method": "GET",
            "path": "/databases/{database_id}",
            "description": "Retrieve a Database object using the ID specified",
            "parameters": [
                {"name": "database_id", "type": "str", "required": True, "location": "path", "description": "Database ID to retrieve"}
            ],
            "request_body": None
        },
        {
            "name": "update_database",
            "method": "PATCH",
            "path": "/databases/{database_id}",
            "description": "Update an existing database as specified by the parameters",
            "parameters": [
                {"name": "database_id", "type": "str", "required": True, "location": "path", "description": "Database ID to update"}
            ],
            "request_body": {"description": "Database object with properties to update"}
        }
    ],

    # Data Sources endpoints (COMPLETE)
    "data_sources": [
        {
            "name": "create_data_source",
            "method": "POST",
            "path": "/databases/{database_id}/data_sources",
            "description": "Create a new data source for a database",
            "parameters": [
                {"name": "database_id", "type": "str", "required": True, "location": "path", "description": "Database ID to create data source for"}
            ],
            "request_body": {"description": "Data source configuration object"}
        },
        {
            "name": "update_data_source",
            "method": "PATCH",
            "path": "/databases/{database_id}/data_sources/{data_source_id}",
            "description": "Update an existing data source",
            "parameters": [
                {"name": "database_id", "type": "str", "required": True, "location": "path", "description": "Database ID containing the data source"},
                {"name": "data_source_id", "type": "str", "required": True, "location": "path", "description": "Data source ID to update"}
            ],
            "request_body": {"description": "Updated data source configuration"}
        },
        {
            "name": "retrieve_data_source",
            "method": "GET",
            "path": "/databases/{database_id}/data_sources/{data_source_id}",
            "description": "Retrieve a specific data source by ID",
            "parameters": [
                {"name": "database_id", "type": "str", "required": True, "location": "path", "description": "Database ID containing the data source"},
                {"name": "data_source_id", "type": "str", "required": True, "location": "path", "description": "Data source ID to retrieve"}
            ],
            "request_body": None
        },
        {
            "name": "query_data_source",
            "method": "POST",
            "path": "/databases/{database_id}/data_sources/{data_source_id}/query",
            "description": "Query a data source connected to a database",
            "parameters": [
                {"name": "database_id", "type": "str", "required": True, "location": "path", "description": "Database ID containing the data source"},
                {"name": "data_source_id", "type": "str", "required": True, "location": "path", "description": "Data source ID to query"}
            ],
            "request_body": {"description": "Query parameters for the data source"}
        }
    ],
    
    # Comment endpoints (EXPANDED)
    "comments": [
        {
            "name": "create_comment",
            "method": "POST",
            "path": "/comments",
            "description": "Create a comment in a page or existing discussion thread",
            "parameters": [],
            "request_body": {"description": "Comment object with parent and rich_text"}
        },
        {
            "name": "retrieve_comments",
            "method": "GET",
            "path": "/comments",
            "description": "Retrieve a list of un-resolved Comment objects from a page or block",
            "parameters": [
                {"name": "block_id", "type": "str", "required": True, "location": "query", "description": "Block or page ID to get comments for"},
                {"name": "start_cursor", "type": "str", "required": False, "location": "query", "description": "Pagination cursor"},
                {"name": "page_size", "type": "int", "required": False, "location": "query", "description": "Number of items to return (max 100)"}
            ],
            "request_body": None
        },
        {
            "name": "retrieve_comment",
            "method": "GET",
            "path": "/comments/{comment_id}",
            "description": "Retrieve a specific comment by its ID",
            "parameters": [
                {"name": "comment_id", "type": "str", "required": True, "location": "path", "description": "Comment ID to retrieve"}
            ],
            "request_body": None
        }
    ],

    # File Upload endpoints (COMPLETE)
    "file_uploads": [
        {
            "name": "create_file_upload",
            "method": "POST",
            "path": "/files",
            "description": "Create a new file upload request",
            "parameters": [],
            "request_body": {"description": "File upload request with name, file details, and parent information"}
        },
        {
            "name": "send_file_upload",
            "method": "POST",
            "path": "/files/{file_id}/upload",
            "description": "Send file data to complete the upload",
            "parameters": [
                {"name": "file_id", "type": "str", "required": True, "location": "path", "description": "File ID from upload session"}
            ],
            "request_body": {"description": "File binary data and metadata"}
        },
        {
            "name": "complete_file_upload",
            "method": "POST",
            "path": "/files/{file_id}/complete",
            "description": "Complete a file upload process",
            "parameters": [
                {"name": "file_id", "type": "str", "required": True, "location": "path", "description": "File ID to complete"}
            ],
            "request_body": {"description": "Upload completion confirmation"}
        },
        {
            "name": "retrieve_file_upload",
            "method": "GET",
            "path": "/files/{file_id}",
            "description": "Retrieve information about a file upload",
            "parameters": [
                {"name": "file_id", "type": "str", "required": True, "location": "path", "description": "File ID to retrieve"}
            ],
            "request_body": None
        },
        {
            "name": "list_file_uploads",
            "method": "GET",
            "path": "/files",
            "description": "List all file uploads for the workspace",
            "parameters": [
                {"name": "start_cursor", "type": "str", "required": False, "location": "query", "description": "Pagination cursor"},
                {"name": "page_size", "type": "int", "required": False, "location": "query", "description": "Number of items to return (max 100)"}
            ],
            "request_body": None
        }
    ],
    
    # Search endpoints
    "search": [
        {
            "name": "search",
            "method": "POST",
            "path": "/search",
            "description": "Search all original pages, databases, and child pages/databases that are shared with the integration",
            "parameters": [],
            "request_body": {"description": "Search object with query, sort, filter, start_cursor, and page_size"}
        },
        {
            "name": "search_by_title",
            "method": "POST",
            "path": "/search",
            "description": "Search pages and databases by title",
            "parameters": [],
            "request_body": {"description": "Search object with title query and optional filters"}
        }
    ],
    
    # Users endpoints
    "users": [
        {
            "name": "list_users",
            "method": "GET",
            "path": "/users",
            "description": "Return a paginated list of Users for the workspace",
            "parameters": [
                {"name": "start_cursor", "type": "str", "required": False, "location": "query", "description": "Pagination cursor"},
                {"name": "page_size", "type": "int", "required": False, "location": "query", "description": "Number of items to return (max 100)"}
            ],
            "request_body": None
        },
        {
            "name": "retrieve_user",
            "method": "GET",
            "path": "/users/{user_id}",
            "description": "Retrieve a User using the ID specified",
            "parameters": [
                {"name": "user_id", "type": "str", "required": True, "location": "path", "description": "User ID to retrieve"}
            ],
            "request_body": None
        },
        {
            "name": "retrieve_bot_user_info",
            "method": "GET",
            "path": "/users/me",
            "description": "Retrieve your token's bot user information",
            "parameters": [],
            "request_body": None
        }
    ],

    # SCIM API v2.0 endpoints (ENTERPRISE)
    "scim": [
        # Service Provider Configuration
        {
            "name": "get_scim_service_provider_config",
            "method": "GET",
            "path": "/scim/v2/ServiceProviderConfig",
            "description": "Get SCIM service provider configuration",
            "parameters": [],
            "request_body": None
        },
        {
            "name": "get_scim_resource_types",
            "method": "GET",
            "path": "/scim/v2/ResourceTypes",
            "description": "Get supported SCIM resource types",
            "parameters": [],
            "request_body": None
        },
        # SCIM Users
        {
            "name": "list_scim_users",
            "method": "GET",
            "path": "/scim/v2/Users",
            "description": "Retrieve a paginated list of workspace members via SCIM",
            "parameters": [
                {"name": "startIndex", "type": "int", "required": False, "location": "query", "description": "1-indexed start position"},
                {"name": "count", "type": "int", "required": False, "location": "query", "description": "Number of results (max 100)"},
                {"name": "filter", "type": "str", "required": False, "location": "query", "description": "Filter expression"}
            ],
            "request_body": None
        },
        {
            "name": "retrieve_scim_user",
            "method": "GET",
            "path": "/scim/v2/Users/{user_id}",
            "description": "Retrieve a specific workspace member by SCIM user ID",
            "parameters": [
                {"name": "user_id", "type": "str", "required": True, "location": "path", "description": "SCIM User ID"}
            ],
            "request_body": None
        },
        {
            "name": "create_scim_user",
            "method": "POST",
            "path": "/scim/v2/Users",
            "description": "Create a new user via SCIM provisioning",
            "parameters": [],
            "request_body": {"description": "SCIM user object with required attributes"}
        },
        {
            "name": "update_scim_user",
            "method": "PUT",
            "path": "/scim/v2/Users/{user_id}",
            "description": "Update a user via SCIM (full replacement)",
            "parameters": [
                {"name": "user_id", "type": "str", "required": True, "location": "path", "description": "SCIM User ID"}
            ],
            "request_body": {"description": "Complete SCIM user object"}
        },
        {
            "name": "patch_scim_user",
            "method": "PATCH",
            "path": "/scim/v2/Users/{user_id}",
            "description": "Partially update a user via SCIM operations",
            "parameters": [
                {"name": "user_id", "type": "str", "required": True, "location": "path", "description": "SCIM User ID"}
            ],
            "request_body": {"description": "SCIM patch operations"}
        },
        {
            "name": "delete_scim_user",
            "method": "DELETE",
            "path": "/scim/v2/Users/{user_id}",
            "description": "Remove a user from workspace via SCIM",
            "parameters": [
                {"name": "user_id", "type": "str", "required": True, "location": "path", "description": "SCIM User ID"}
            ],
            "request_body": None
        },
        # SCIM Groups
        {
            "name": "list_scim_groups",
            "method": "GET",
            "path": "/scim/v2/Groups",
            "description": "Retrieve a paginated list of groups via SCIM",
            "parameters": [
                {"name": "startIndex", "type": "int", "required": False, "location": "query", "description": "1-indexed start position"},
                {"name": "count", "type": "int", "required": False, "location": "query", "description": "Number of results (max 100)"},
                {"name": "filter", "type": "str", "required": False, "location": "query", "description": "Filter expression"}
            ],
            "request_body": None
        },
        {
            "name": "retrieve_scim_group",
            "method": "GET",
            "path": "/scim/v2/Groups/{group_id}",
            "description": "Retrieve a specific group by SCIM group ID",
            "parameters": [
                {"name": "group_id", "type": "str", "required": True, "location": "path", "description": "SCIM Group ID"}
            ],
            "request_body": None
        },
        {
            "name": "create_scim_group",
            "method": "POST",
            "path": "/scim/v2/Groups",
            "description": "Create a new group via SCIM provisioning",
            "parameters": [],
            "request_body": {"description": "SCIM group object with members"}
        },
        {
            "name": "update_scim_group",
            "method": "PUT",
            "path": "/scim/v2/Groups/{group_id}",
            "description": "Update a group via SCIM (full replacement)",
            "parameters": [
                {"name": "group_id", "type": "str", "required": True, "location": "path", "description": "SCIM Group ID"}
            ],
            "request_body": {"description": "Complete SCIM group object"}
        },
        {
            "name": "patch_scim_group",
            "method": "PATCH",
            "path": "/scim/v2/Groups/{group_id}",
            "description": "Partially update a group via SCIM operations",
            "parameters": [
                {"name": "group_id", "type": "str", "required": True, "location": "path", "description": "SCIM Group ID"}
            ],
            "request_body": {"description": "SCIM patch operations"}
        },
        {
            "name": "delete_scim_group",
            "method": "DELETE",
            "path": "/scim/v2/Groups/{group_id}",
            "description": "Remove a group via SCIM",
            "parameters": [
                {"name": "group_id", "type": "str", "required": True, "location": "path", "description": "SCIM Group ID"}
            ],
            "request_body": None
        }
    ],

    # Organization Management (ENTERPRISE)
    "organization": [
        {
            "name": "get_organization_settings",
            "method": "GET",
            "path": "/organizations/{organization_id}",
            "description": "Get organization-level settings and configuration",
            "parameters": [
                {"name": "organization_id", "type": "str", "required": True, "location": "path", "description": "Organization ID"}
            ],
            "request_body": None
        },
        {
            "name": "update_organization_settings",
            "method": "PATCH",
            "path": "/organizations/{organization_id}",
            "description": "Update organization-level settings",
            "parameters": [
                {"name": "organization_id", "type": "str", "required": True, "location": "path", "description": "Organization ID"}
            ],
            "request_body": {"description": "Organization settings to update"}
        },
        {
            "name": "list_organization_workspaces",
            "method": "GET",
            "path": "/organizations/{organization_id}/workspaces",
            "description": "List all workspaces in the organization",
            "parameters": [
                {"name": "organization_id", "type": "str", "required": True, "location": "path", "description": "Organization ID"}
            ],
            "request_body": None
        },
        {
            "name": "claim_workspace",
            "method": "POST",
            "path": "/organizations/{organization_id}/workspaces/claim",
            "description": "Claim ownership of eligible workspaces",
            "parameters": [
                {"name": "organization_id", "type": "str", "required": True, "location": "path", "description": "Organization ID"}
            ],
            "request_body": {"description": "Workspace claim configuration"}
        }
    ],

    # Workspace Administration (ENTERPRISE)
    "workspace_admin": [
        {
            "name": "get_workspace_settings",
            "method": "GET",
            "path": "/workspaces/{workspace_id}/admin/settings",
            "description": "Get workspace administration settings",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": None
        },
        {
            "name": "update_workspace_settings",
            "method": "PATCH",
            "path": "/workspaces/{workspace_id}/admin/settings",
            "description": "Update workspace administration settings",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": {"description": "Workspace settings to update"}
        },
        {
            "name": "list_workspace_members_admin",
            "method": "GET",
            "path": "/workspaces/{workspace_id}/admin/members",
            "description": "List all workspace members (admin view)",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"},
                {"name": "start_cursor", "type": "str", "required": False, "location": "query", "description": "Pagination cursor"},
                {"name": "page_size", "type": "int", "required": False, "location": "query", "description": "Number of items to return"}
            ],
            "request_body": None
        },
        {
            "name": "update_member_permissions",
            "method": "PATCH",
            "path": "/workspaces/{workspace_id}/admin/members/{user_id}",
            "description": "Update member permissions and roles",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"},
                {"name": "user_id", "type": "str", "required": True, "location": "path", "description": "User ID"}
            ],
            "request_body": {"description": "Member permission updates"}
        },
        {
            "name": "remove_workspace_member",
            "method": "DELETE",
            "path": "/workspaces/{workspace_id}/admin/members/{user_id}",
            "description": "Remove member from workspace",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"},
                {"name": "user_id", "type": "str", "required": True, "location": "path", "description": "User ID"}
            ],
            "request_body": None
        },
        {
            "name": "transfer_member_content",
            "method": "POST",
            "path": "/workspaces/{workspace_id}/admin/members/{user_id}/transfer",
            "description": "Transfer member's private pages to another user",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"},
                {"name": "user_id", "type": "str", "required": True, "location": "path", "description": "User ID"}
            ],
            "request_body": {"description": "Content transfer configuration"}
        },
        {
            "name": "get_recently_left_members",
            "method": "GET",
            "path": "/workspaces/{workspace_id}/admin/members/recently-left",
            "description": "Get list of recently left members",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": None
        }
    ],

    # Integration Management (ENTERPRISE) 
    "integrations": [
        {
            "name": "list_workspace_integrations",
            "method": "GET",
            "path": "/workspaces/{workspace_id}/integrations",
            "description": "List all integrations in workspace",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": None
        },
        {
            "name": "get_integration_details",
            "method": "GET",
            "path": "/workspaces/{workspace_id}/integrations/{integration_id}",
            "description": "Get details of a specific integration",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"},
                {"name": "integration_id", "type": "str", "required": True, "location": "path", "description": "Integration ID"}
            ],
            "request_body": None
        },
        {
            "name": "update_integration_settings",
            "method": "PATCH",
            "path": "/workspaces/{workspace_id}/integrations/{integration_id}",
            "description": "Update integration settings and permissions",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"},
                {"name": "integration_id", "type": "str", "required": True, "location": "path", "description": "Integration ID"}
            ],
            "request_body": {"description": "Integration settings to update"}
        },
        {
            "name": "remove_integration",
            "method": "DELETE",
            "path": "/workspaces/{workspace_id}/integrations/{integration_id}",
            "description": "Remove integration from workspace",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"},
                {"name": "integration_id", "type": "str", "required": True, "location": "path", "description": "Integration ID"}
            ],
            "request_body": None
        },
        {
            "name": "get_integration_restrictions",
            "method": "GET",
            "path": "/workspaces/{workspace_id}/integration-restrictions",
            "description": "Get workspace integration restrictions",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": None
        },
        {
            "name": "update_integration_restrictions",
            "method": "PATCH",
            "path": "/workspaces/{workspace_id}/integration-restrictions",
            "description": "Update workspace integration restrictions",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": {"description": "Integration restriction policies"}
        }
    ],

    # Security & Compliance (ENTERPRISE)
    "security": [
        {
            "name": "get_security_settings",
            "method": "GET",
            "path": "/workspaces/{workspace_id}/security",
            "description": "Get workspace security settings",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": None
        },
        {
            "name": "update_security_settings",
            "method": "PATCH",
            "path": "/workspaces/{workspace_id}/security",
            "description": "Update workspace security settings",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": {"description": "Security settings to update"}
        },
        {
            "name": "get_saml_sso_config",
            "method": "GET",
            "path": "/workspaces/{workspace_id}/security/saml",
            "description": "Get SAML SSO configuration",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": None
        },
        {
            "name": "update_saml_sso_config",
            "method": "PATCH",
            "path": "/workspaces/{workspace_id}/security/saml",
            "description": "Update SAML SSO configuration",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": {"description": "SAML SSO configuration"}
        },
        {
            "name": "verify_domain",
            "method": "POST",
            "path": "/workspaces/{workspace_id}/security/domains/verify",
            "description": "Verify domain ownership for enterprise features",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": {"description": "Domain verification data"}
        },
        {
            "name": "list_verified_domains",
            "method": "GET",
            "path": "/workspaces/{workspace_id}/security/domains",
            "description": "List verified domains for workspace",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": None
        }
    ],

    # Content Management (ENTERPRISE)
    "content_management": [
        {
            "name": "search_workspace_content",
            "method": "POST",
            "path": "/workspaces/{workspace_id}/admin/content/search",
            "description": "Enterprise content search across workspace",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": {"description": "Advanced search parameters for content discovery"}
        },
        {
            "name": "get_content_permissions",
            "method": "GET",
            "path": "/workspaces/{workspace_id}/admin/content/{page_id}/permissions",
            "description": "Get detailed permissions for a page or database",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"},
                {"name": "page_id", "type": "str", "required": True, "location": "path", "description": "Page or database ID"}
            ],
            "request_body": None
        },
        {
            "name": "update_content_permissions",
            "method": "PATCH",
            "path": "/workspaces/{workspace_id}/admin/content/{page_id}/permissions",
            "description": "Update permissions for a page or database",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"},
                {"name": "page_id", "type": "str", "required": True, "location": "path", "description": "Page or database ID"}
            ],
            "request_body": {"description": "Permission updates"}
        },
        {
            "name": "get_sharing_settings",
            "method": "GET",
            "path": "/workspaces/{workspace_id}/admin/sharing-settings",
            "description": "Get workspace sharing and collaboration settings",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": None
        },
        {
            "name": "update_sharing_settings",
            "method": "PATCH",
            "path": "/workspaces/{workspace_id}/admin/sharing-settings",
            "description": "Update workspace sharing and collaboration settings",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": {"description": "Sharing settings to update"}
        },
        {
            "name": "bulk_content_operations",
            "method": "POST",
            "path": "/workspaces/{workspace_id}/admin/content/bulk",
            "description": "Perform bulk operations on content",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": {"description": "Bulk operation parameters"}
        },
        {
            "name": "get_trash_settings",
            "method": "GET",
            "path": "/workspaces/{workspace_id}/admin/trash",
            "description": "Get trash retention and deletion settings",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": None
        },
        {
            "name": "update_trash_settings",
            "method": "PATCH",
            "path": "/workspaces/{workspace_id}/admin/trash",
            "description": "Update trash retention and deletion settings",
            "parameters": [
                {"name": "workspace_id", "type": "str", "required": True, "location": "path", "description": "Workspace ID"}
            ],
            "request_body": {"description": "Trash retention settings"}
        }
    ]
}

# Python reserved keywords and naming helpers
_PY_RESERVED = set(keyword.kwlist) | {"from", "global", "async", "await", "None", "self", "cls"}

def _sanitize_name(name: str) -> str:
    """Sanitize parameter/method names to be valid Python identifiers."""
    # Convert to snake_case
    name = re.sub(r'([A-Z]+)', r'_\1', name).lower()
    name = name.replace('-', '_').replace(' ', '_')
    name = re.sub(r'_+', '_', name).strip('_')
    
    # Ensure valid identifier
    if not name.isidentifier() or name in _PY_RESERVED:
        name = f"{name}_"
    return name

def _get_python_type(param_type: str) -> str:
    """Convert parameter type to Python type annotation."""
    type_mapping = {
        "str": "str",
        "string": "str", 
        "int": "int",
        "integer": "int",
        "bool": "bool",
        "boolean": "bool",
        "float": "float",
        "number": "float",
        "List[str]": "List[str]",
        "Dict[str, Any]": "Dict[str, Any]",
        "object": "Dict[str, Any]"
    }
    return type_mapping.get(param_type, "str")

def _generate_method_signature(endpoint: Dict[str, Any]) -> Tuple[str, List[str], List[str]]:
    """Generate method signature components."""
    method_name = _sanitize_name(endpoint["name"])
    
    required_params = ["self"]
    optional_params = []
    
    # Process parameters
    for param in endpoint.get("parameters", []):
        param_name = _sanitize_name(param["name"])
        param_type = _get_python_type(param["type"])
        
        if param.get("required", False):
            required_params.append(f"{param_name}: {param_type}")
        else:
            optional_params.append(f"{param_name}: Optional[{param_type}] = None")
    
    # Add request body parameter if needed
    if endpoint.get("request_body"):
        optional_params.append("request_body: Optional[Dict[str, Any]] = None")
    
    # Always add **kwargs for flexibility
    optional_params.append("**kwargs")
    
    return method_name, required_params, optional_params

def _generate_method_docstring(endpoint: Dict[str, Any]) -> str:
    """Generate method docstring."""
    description = endpoint.get("description", "Notion API method")
    method = endpoint.get("method", "GET")
    path = endpoint.get("path", "")
    
    doc_lines = [
        f'        """{description}',
        "",
        f"        HTTP {method} {path}",
        ""
    ]
    
    parameters = endpoint.get("parameters", [])
    if parameters or endpoint.get("request_body"):
        doc_lines.append("        Args:")
        
        for param in parameters:
            param_name = _sanitize_name(param["name"])
            param_type = _get_python_type(param["type"])
            required_text = "required" if param.get("required", False) else "optional"
            param_desc = param.get("description", "")
            doc_lines.append(f"            {param_name} ({param_type}, {required_text}): {param_desc}")
        
        if endpoint.get("request_body"):
            body_desc = endpoint["request_body"].get("description", "Request body data")
            doc_lines.append(f"            request_body (Dict[str, Any], optional): {body_desc}")
        
        doc_lines.append("")
    
    doc_lines.extend([
        "        Returns:",
        "            NotionResponse: Standardized response wrapper with success/data/error",
        '        """'
    ])
    
    return "\n".join(doc_lines)

def _generate_method_body(endpoint: Dict[str, Any]) -> str:
    """Generate method implementation."""
    method = endpoint.get("method", "GET").upper()
    path = endpoint.get("path", "")
    
    # Build parameter handling
    param_lines = ["        params: Dict[str, Any] = {}"]
    path_params = []
    
    for param in endpoint.get("parameters", []):
        param_name = _sanitize_name(param["name"])
        location = param.get("location", "query")
        
        if location == "path":
            path_params.append(param_name)
        elif location == "query":
            param_lines.extend([
                f"        if {param_name} is not None:",
                f"            params['{param['name']}'] = {param_name}"
            ])
    
    # Add kwargs handling
    param_lines.extend([
        "        if kwargs:",
        "            params.update(kwargs)"
    ])
    
    # Build path formatting - FIXED
    if path_params:
        path_format_dict = ", ".join(f"{param}={param}" for param in path_params)
        url_line = f'        url = self.base_url + "{path}".format({path_format_dict})'
    else:
        url_line = f'        url = self.base_url + "{path}"'
    
    # Build request
    request_lines = [
        "        request = HTTPRequest(",
        f'            method="{method}",',
        "            url=url,",
        "            headers=self.http.headers,",
        "            query_params={k: str(v) for k, v in params.items() if v is not None},"
    ]
    
    if endpoint.get("request_body"):
        request_lines.append("            body=request_body,")
    
    request_lines.append("        )")
    
    # Execute request and handle response
    execute_lines = [
        "        try:",
        "            response = await self.http.execute(request)",
        "            return NotionResponse(success=True, data=response)",
        "        except Exception as e:",
        "            return NotionResponse(success=False, error=str(e))"
    ]
    
    return "\n".join(param_lines + [url_line] + request_lines + execute_lines)

def generate_notion_client() -> str:
    """Generate the complete Notion API client."""
    
    # Class header
    class_lines = [
        "from typing import Dict, List, Optional, Any",
        "from app.sources.client.http.http_request import HTTPRequest",
        "from app.sources.client.notion.notion import NotionClient, NotionResponse",
        "",
        "",
        "class NotionDataSource:",
        '    """Auto-generated Notion API client wrapper.',
        "    ",
        "    Provides async methods for all Notion API endpoints:",
        "    - Authentication (retrieve bot user)",
        "    - Blocks (retrieve, update, delete, children, append)",
        "    - Pages (create, retrieve, update properties, property items)",
        "    - Databases (create, query, retrieve, update)",
        "    - Data Sources (create, update, retrieve, query)",
        "    - Comments (create, retrieve all, retrieve by ID)",
        "    - File Uploads (create, send, complete, retrieve, list)",
        "    - Search (general search, search by title)",
        "    - Users (list, retrieve, bot user info)",
        "    - SCIM v2.0 (complete enterprise user/group provisioning)",
        "    - Organization Management (enterprise organization controls)",
        "    - Workspace Administration (advanced admin features)",
        "    - Integration Management (control workspace integrations)",
        "    - Security & Compliance (SAML SSO, domain verification)",
        "    - Content Management (enterprise content discovery & permissions)",
        "    ",
        "    All methods return NotionResponse objects with standardized success/data/error format.",
        '    """',
        "",
        "    def __init__(self, client: NotionClient) -> None:",
        '        """Initialize with NotionClient."""',
        "        self.http = client.get_client()",
        "        if self.http is None:",
        "            raise ValueError('HTTP client is not initialized')",
        "        self.base_url = self.http.get_base_url()",
        "",
        "    def get_data_source(self) -> 'NotionDataSource':",
        "        return self",
        ""
    ]
    
    # Generate methods for each endpoint category
    method_count = 0
    for category, endpoints in NOTION_ENDPOINTS.items():
        class_lines.append(f"    # {category.replace('_', ' ').title()} methods")
        class_lines.append("")
        
        for endpoint in endpoints:
            method_name, required_params, optional_params = _generate_method_signature(endpoint)
            
            # Method signature
            all_params = required_params + optional_params
            if len(all_params) <= 4:
                signature = f"    async def {method_name}({', '.join(all_params)}) -> NotionResponse:"
            else:
                params_formatted = ',\n        '.join(all_params)
                signature = f"    async def {method_name}(\n        {params_formatted}\n    ) -> NotionResponse:"
            
            class_lines.append(signature)
            
            # Docstring
            docstring = _generate_method_docstring(endpoint)
            class_lines.append(docstring)
            
            # Method body
            method_body = _generate_method_body(endpoint)
            class_lines.append(method_body)
            class_lines.append("")
            
            method_count += 1
    
    return "\n".join(class_lines)

def generate_notion_api_client(output_dir: Optional[Path] = None) -> str:
    """Generate Notion API client and save to file."""
    if output_dir is None:
        output_dir = Path(__file__).parent
    
    notion_dir = output_dir / "notion"
    notion_dir.mkdir(exist_ok=True)
    
    client_file = notion_dir / "notion_client.py"
    
    print("🚀 Generating complete Notion API client...")
    client_code = generate_notion_client()
    
    client_file.write_text(client_code, encoding="utf-8")
    
    method_count = sum(len(endpoints) for endpoints in NOTION_ENDPOINTS.values())
    print(f"✅ Generated Notion API client with {method_count} methods")
    print(f"📁 Saved to: {client_file}")
    
    # Print detailed summary
    print(f"\n📊 Complete API Coverage Summary:")
    print(f"   - Total methods: {method_count}")
    for category, endpoints in NOTION_ENDPOINTS.items():
        print(f"   - {category.replace('_', ' ').title()}: {len(endpoints)} methods")
        for endpoint in endpoints:
            print(f"     * {endpoint['method']} {endpoint['path']} -> {endpoint['name']}")
    
    return str(client_file)

def main() -> None:
    """Main function for CLI usage."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate complete Notion API client from endpoint definitions")
    parser.add_argument("--output", "-o", help="Output directory (default: current directory)")
    
    args = parser.parse_args()
    
    output_dir = Path(args.output) if args.output else None
    
    try:
        generate_notion_api_client(output_dir)
        print("\n🎉 Complete Notion API client generation completed!")
        print("📋 ALL APIs have been included:")
        print("   ✅ Standard APIs:")
        print("      • Authentication, Blocks, Pages, Databases, Comments, Search, Users")
        print("      • Data Sources (create, update, retrieve, query)")
        print("      • File Uploads (create, send, complete, retrieve, list)")
        print("   ✅ Enterprise & Business APIs:")
        print("      • SCIM v2.0 API (complete user/group provisioning)")
        print("      • Organization Management (enterprise controls)")
        print("      • Workspace Administration (advanced admin features)")
        print("      • Integration Management (workspace integration controls)")
        print("      • Security & Compliance (SAML SSO, domain verification)")
        print("      • Content Management (enterprise content discovery & permissions)")
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()