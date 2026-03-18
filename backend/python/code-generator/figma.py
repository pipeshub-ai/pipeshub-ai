# ruff: noqa
"""
Figma REST API Code Generator

Generates FigmaDataSource class covering Figma API:
- Files and File Nodes
- File Versions
- Images and Image Fills
- Comments and Comment Reactions
- Users
- Projects and Project Files
- Components and Component Sets
- Styles
- Webhooks (v2)
- Activity Logs
- Payments
- Variables
- Dev Resources
- Library Analytics

The generated DataSource accepts a FigmaClient and uses the client's
base URL (https://api.figma.com) to construct request URLs.
Paths include /v1/ or /v2/ as appropriate.

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional


# ================================================================================
# Figma API Endpoints - organized by resource
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which is https://api.figma.com)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#               Optional: api_name (the actual API parameter name if different from Python name)
#   required: List of required parameter names
# ================================================================================

FIGMA_API_ENDPOINTS = {
    # ================================================================================
    # FILES
    # ================================================================================
    "get_file": {
        "method": "GET",
        "path": "/v1/files/{file_key}",
        "description": "Get file JSON",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key (from the Figma file URL)"},
            "version": {"type": "Optional[str]", "location": "query", "description": "A specific version ID to get"},
            "ids": {"type": "Optional[str]", "location": "query", "description": "Comma-separated list of node IDs to retrieve"},
            "depth": {"type": "Optional[int]", "location": "query", "description": "Positive integer representing how deep into the document tree to traverse"},
            "geometry": {"type": "Optional[str]", "location": "query", "description": "Set to 'paths' to export vector data"},
            "plugin_data": {"type": "Optional[str]", "location": "query", "description": "Comma-separated list of plugin IDs or 'shared' for shared plugin data"},
            "branch_data": {"type": "Optional[bool]", "location": "query", "description": "Returns branch metadata for the requested file"},
        },
        "required": ["file_key"],
    },
    "get_file_nodes": {
        "method": "GET",
        "path": "/v1/files/{file_key}/nodes",
        "description": "Get specific nodes from a file",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "ids": {"type": "str", "location": "query", "description": "Comma-separated list of node IDs to retrieve"},
            "version": {"type": "Optional[str]", "location": "query", "description": "A specific version ID to get"},
            "depth": {"type": "Optional[int]", "location": "query", "description": "Positive integer for document tree depth"},
            "geometry": {"type": "Optional[str]", "location": "query", "description": "Set to 'paths' to export vector data"},
            "plugin_data": {"type": "Optional[str]", "location": "query", "description": "Comma-separated list of plugin IDs or 'shared'"},
        },
        "required": ["file_key", "ids"],
    },
    "get_images": {
        "method": "GET",
        "path": "/v1/images/{file_key}",
        "description": "Render images of file nodes",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "ids": {"type": "str", "location": "query", "description": "Comma-separated list of node IDs to render"},
            "version": {"type": "Optional[str]", "location": "query", "description": "A specific version ID to use"},
            "scale": {"type": "Optional[float]", "location": "query", "description": "Image scale factor (0.01 to 4)"},
            "format": {"type": "Optional[str]", "location": "query", "description": "Image format: jpg, png, svg, or pdf", "api_name": "format"},
            "svg_outline_text": {"type": "Optional[bool]", "location": "query", "description": "Whether text elements are rendered as outlines in SVGs"},
            "svg_include_id": {"type": "Optional[bool]", "location": "query", "description": "Include id attribute for all SVG elements"},
            "svg_include_node_id": {"type": "Optional[bool]", "location": "query", "description": "Include node-id attribute for all SVG elements"},
            "svg_simplify_stroke": {"type": "Optional[bool]", "location": "query", "description": "Simplify inside/outside strokes and use stroke attribute"},
            "contents_only": {"type": "Optional[bool]", "location": "query", "description": "Whether content that overlaps the node should be excluded"},
            "use_absolute_bounds": {"type": "Optional[bool]", "location": "query", "description": "Use full dimensions of the node regardless of cropping"},
        },
        "required": ["file_key", "ids"],
    },
    "get_image_fills": {
        "method": "GET",
        "path": "/v1/files/{file_key}/images",
        "description": "Get image fills",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
        },
        "required": ["file_key"],
    },
    "get_file_meta": {
        "method": "GET",
        "path": "/v1/files/{file_key}/meta",
        "description": "Get file metadata",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
        },
        "required": ["file_key"],
    },

    # ================================================================================
    # FILE VERSIONS
    # ================================================================================
    "get_file_versions": {
        "method": "GET",
        "path": "/v1/files/{file_key}/versions",
        "description": "Get version history",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "page_size": {"type": "Optional[int]", "location": "query", "description": "Number of items per page"},
            "before": {"type": "Optional[int]", "location": "query", "description": "Version ID to get versions before"},
            "after": {"type": "Optional[int]", "location": "query", "description": "Version ID to get versions after"},
        },
        "required": ["file_key"],
    },

    # ================================================================================
    # COMMENTS
    # ================================================================================
    "get_comments": {
        "method": "GET",
        "path": "/v1/files/{file_key}/comments",
        "description": "Get comments in a file",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "as_md": {"type": "Optional[bool]", "location": "query", "description": "Return comments as markdown"},
        },
        "required": ["file_key"],
    },
    "post_comment": {
        "method": "POST",
        "path": "/v1/files/{file_key}/comments",
        "description": "Add a comment to a file",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "message": {"type": "str", "location": "body", "description": "The comment text"},
            "comment_id": {"type": "Optional[str]", "location": "body", "description": "The ID of the comment to reply to"},
            "client_meta": {"type": "Optional[dict[str, Any]]", "location": "body", "description": "Position of the comment (x, y, node_id, node_offset)"},
        },
        "required": ["file_key", "message"],
    },
    "delete_comment": {
        "method": "DELETE",
        "path": "/v1/files/{file_key}/comments/{comment_id}",
        "description": "Delete a comment",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "comment_id": {"type": "str", "location": "path", "description": "The comment ID"},
        },
        "required": ["file_key", "comment_id"],
    },

    # ================================================================================
    # COMMENT REACTIONS
    # ================================================================================
    "get_comment_reactions": {
        "method": "GET",
        "path": "/v1/files/{file_key}/comments/{comment_id}/reactions",
        "description": "Get reactions for a comment",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "comment_id": {"type": "str", "location": "path", "description": "The comment ID"},
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Cursor for pagination"},
        },
        "required": ["file_key", "comment_id"],
    },
    "post_comment_reaction": {
        "method": "POST",
        "path": "/v1/files/{file_key}/comments/{comment_id}/reactions",
        "description": "Add a reaction to a comment",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "comment_id": {"type": "str", "location": "path", "description": "The comment ID"},
            "emoji": {"type": "str", "location": "body", "description": "The emoji to react with"},
        },
        "required": ["file_key", "comment_id", "emoji"],
    },
    "delete_comment_reaction": {
        "method": "DELETE",
        "path": "/v1/files/{file_key}/comments/{comment_id}/reactions",
        "description": "Delete a reaction",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "comment_id": {"type": "str", "location": "path", "description": "The comment ID"},
            "emoji": {"type": "str", "location": "query", "description": "The emoji to remove"},
        },
        "required": ["file_key", "comment_id", "emoji"],
    },

    # ================================================================================
    # USERS
    # ================================================================================
    "get_me": {
        "method": "GET",
        "path": "/v1/me",
        "description": "Get current user",
        "parameters": {},
        "required": [],
    },

    # ================================================================================
    # PROJECTS
    # ================================================================================
    "get_team_projects": {
        "method": "GET",
        "path": "/v1/teams/{team_id}/projects",
        "description": "Get projects in a team",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The team ID"},
        },
        "required": ["team_id"],
    },
    "get_project_files": {
        "method": "GET",
        "path": "/v1/projects/{project_id}/files",
        "description": "Get files in a project",
        "parameters": {
            "project_id": {"type": "str", "location": "path", "description": "The project ID"},
            "branch_data": {"type": "Optional[bool]", "location": "query", "description": "Returns branch metadata for the requested files"},
        },
        "required": ["project_id"],
    },

    # ================================================================================
    # COMPONENTS
    # ================================================================================
    "get_team_components": {
        "method": "GET",
        "path": "/v1/teams/{team_id}/components",
        "description": "Get team components",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The team ID"},
            "page_size": {"type": "Optional[int]", "location": "query", "description": "Number of items per page"},
            "after": {"type": "Optional[int]", "location": "query", "description": "Cursor for pagination (next page)"},
            "before": {"type": "Optional[int]", "location": "query", "description": "Cursor for pagination (previous page)"},
        },
        "required": ["team_id"],
    },
    "get_file_components": {
        "method": "GET",
        "path": "/v1/files/{file_key}/components",
        "description": "Get file components",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
        },
        "required": ["file_key"],
    },
    "get_component": {
        "method": "GET",
        "path": "/v1/components/{key}",
        "description": "Get component by key",
        "parameters": {
            "key": {"type": "str", "location": "path", "description": "The component key"},
        },
        "required": ["key"],
    },

    # ================================================================================
    # COMPONENT SETS
    # ================================================================================
    "get_team_component_sets": {
        "method": "GET",
        "path": "/v1/teams/{team_id}/component_sets",
        "description": "Get team component sets",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The team ID"},
            "page_size": {"type": "Optional[int]", "location": "query", "description": "Number of items per page"},
            "after": {"type": "Optional[int]", "location": "query", "description": "Cursor for pagination (next page)"},
            "before": {"type": "Optional[int]", "location": "query", "description": "Cursor for pagination (previous page)"},
        },
        "required": ["team_id"],
    },
    "get_file_component_sets": {
        "method": "GET",
        "path": "/v1/files/{file_key}/component_sets",
        "description": "Get file component sets",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
        },
        "required": ["file_key"],
    },
    "get_component_set": {
        "method": "GET",
        "path": "/v1/component_sets/{key}",
        "description": "Get component set by key",
        "parameters": {
            "key": {"type": "str", "location": "path", "description": "The component set key"},
        },
        "required": ["key"],
    },

    # ================================================================================
    # STYLES
    # ================================================================================
    "get_team_styles": {
        "method": "GET",
        "path": "/v1/teams/{team_id}/styles",
        "description": "Get team styles",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The team ID"},
            "page_size": {"type": "Optional[int]", "location": "query", "description": "Number of items per page"},
            "after": {"type": "Optional[int]", "location": "query", "description": "Cursor for pagination (next page)"},
            "before": {"type": "Optional[int]", "location": "query", "description": "Cursor for pagination (previous page)"},
        },
        "required": ["team_id"],
    },
    "get_file_styles": {
        "method": "GET",
        "path": "/v1/files/{file_key}/styles",
        "description": "Get file styles",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
        },
        "required": ["file_key"],
    },
    "get_style": {
        "method": "GET",
        "path": "/v1/styles/{key}",
        "description": "Get style by key",
        "parameters": {
            "key": {"type": "str", "location": "path", "description": "The style key"},
        },
        "required": ["key"],
    },

    # ================================================================================
    # WEBHOOKS (v2)
    # ================================================================================
    "get_webhooks": {
        "method": "GET",
        "path": "/v2/webhooks",
        "description": "Get webhooks by context or plan",
        "parameters": {
            "context": {"type": "Optional[str]", "location": "query", "description": "The context type to filter by"},
            "context_id": {"type": "Optional[str]", "location": "query", "description": "The context ID to filter by"},
            "plan_api_id": {"type": "Optional[str]", "location": "query", "description": "The plan API ID to filter by"},
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Cursor for pagination"},
        },
        "required": [],
    },
    "post_webhook": {
        "method": "POST",
        "path": "/v2/webhooks",
        "description": "Create a webhook",
        "parameters": {
            "event_type": {"type": "str", "location": "body", "description": "The event type to subscribe to"},
            "context": {"type": "str", "location": "body", "description": "The context type for the webhook"},
            "context_id": {"type": "str", "location": "body", "description": "The context ID for the webhook"},
            "endpoint": {"type": "str", "location": "body", "description": "The endpoint URL to receive webhook events"},
            "passcode": {"type": "str", "location": "body", "description": "A passcode for webhook verification"},
            "team_id": {"type": "Optional[str]", "location": "body", "description": "The team ID"},
            "status": {"type": "Optional[str]", "location": "body", "description": "The webhook status"},
            "description": {"type": "Optional[str]", "location": "body", "description": "A description for the webhook"},
        },
        "required": ["event_type", "context", "context_id", "endpoint", "passcode"],
    },
    "get_webhook": {
        "method": "GET",
        "path": "/v2/webhooks/{webhook_id}",
        "description": "Get a webhook by ID",
        "parameters": {
            "webhook_id": {"type": "str", "location": "path", "description": "The webhook ID"},
        },
        "required": ["webhook_id"],
    },
    "put_webhook": {
        "method": "PUT",
        "path": "/v2/webhooks/{webhook_id}",
        "description": "Update a webhook",
        "parameters": {
            "webhook_id": {"type": "str", "location": "path", "description": "The webhook ID"},
            "event_type": {"type": "Optional[str]", "location": "body", "description": "The event type to subscribe to"},
            "endpoint": {"type": "Optional[str]", "location": "body", "description": "The endpoint URL to receive webhook events"},
            "passcode": {"type": "Optional[str]", "location": "body", "description": "A passcode for webhook verification"},
            "status": {"type": "Optional[str]", "location": "body", "description": "The webhook status"},
            "description": {"type": "Optional[str]", "location": "body", "description": "A description for the webhook"},
        },
        "required": ["webhook_id"],
    },
    "delete_webhook": {
        "method": "DELETE",
        "path": "/v2/webhooks/{webhook_id}",
        "description": "Delete a webhook",
        "parameters": {
            "webhook_id": {"type": "str", "location": "path", "description": "The webhook ID"},
        },
        "required": ["webhook_id"],
    },
    "get_team_webhooks": {
        "method": "GET",
        "path": "/v2/teams/{team_id}/webhooks",
        "description": "Get team webhooks (deprecated)",
        "parameters": {
            "team_id": {"type": "str", "location": "path", "description": "The team ID"},
        },
        "required": ["team_id"],
    },
    "get_webhook_requests": {
        "method": "GET",
        "path": "/v2/webhooks/{webhook_id}/requests",
        "description": "Get webhook requests",
        "parameters": {
            "webhook_id": {"type": "str", "location": "path", "description": "The webhook ID"},
        },
        "required": ["webhook_id"],
    },

    # ================================================================================
    # ACTIVITY LOGS
    # ================================================================================
    "get_activity_logs": {
        "method": "GET",
        "path": "/v1/activity_logs",
        "description": "Get activity logs",
        "parameters": {
            "events": {"type": "Optional[str]", "location": "query", "description": "Comma-separated list of event types to filter"},
            "start_time": {"type": "Optional[int]", "location": "query", "description": "Start time as Unix timestamp"},
            "end_time": {"type": "Optional[int]", "location": "query", "description": "End time as Unix timestamp"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of events to return"},
            "order": {"type": "Optional[str]", "location": "query", "description": "Sort order: 'asc' or 'desc'"},
        },
        "required": [],
    },

    # ================================================================================
    # PAYMENTS
    # ================================================================================
    "get_payments": {
        "method": "GET",
        "path": "/v1/payments",
        "description": "Get payments",
        "parameters": {
            "plugin_payment_token": {"type": "Optional[str]", "location": "query", "description": "Plugin payment token"},
            "user_id": {"type": "Optional[str]", "location": "query", "description": "User ID to filter by"},
            "community_file_id": {"type": "Optional[str]", "location": "query", "description": "Community file ID to filter by"},
            "plugin_id": {"type": "Optional[str]", "location": "query", "description": "Plugin ID to filter by"},
            "widget_id": {"type": "Optional[str]", "location": "query", "description": "Widget ID to filter by"},
        },
        "required": [],
    },

    # ================================================================================
    # VARIABLES
    # ================================================================================
    "get_local_variables": {
        "method": "GET",
        "path": "/v1/files/{file_key}/variables/local",
        "description": "Get local variables",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
        },
        "required": ["file_key"],
    },
    "get_published_variables": {
        "method": "GET",
        "path": "/v1/files/{file_key}/variables/published",
        "description": "Get published variables",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
        },
        "required": ["file_key"],
    },
    "post_variables": {
        "method": "POST",
        "path": "/v1/files/{file_key}/variables",
        "description": "Create/modify/delete variables",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "variable_collections": {"type": "Optional[list]", "location": "body", "description": "List of variable collections to create/modify/delete", "api_name": "variableCollections"},
            "variable_modes": {"type": "Optional[list]", "location": "body", "description": "List of variable modes to create/modify/delete", "api_name": "variableModes"},
            "variables": {"type": "Optional[list]", "location": "body", "description": "List of variables to create/modify/delete"},
            "variable_mode_values": {"type": "Optional[list]", "location": "body", "description": "List of variable mode values to set", "api_name": "variableModeValues"},
        },
        "required": ["file_key"],
    },

    # ================================================================================
    # DEV RESOURCES
    # ================================================================================
    "get_dev_resources": {
        "method": "GET",
        "path": "/v1/files/{file_key}/dev_resources",
        "description": "Get dev resources",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "node_ids": {"type": "Optional[str]", "location": "query", "description": "Comma-separated list of node IDs to filter by"},
        },
        "required": ["file_key"],
    },
    "post_dev_resources": {
        "method": "POST",
        "path": "/v1/dev_resources",
        "description": "Create dev resources",
        "parameters": {
            "dev_resources": {"type": "list", "location": "body", "description": "List of dev resources to create (each with name, url, file_key, node_id)"},
        },
        "required": ["dev_resources"],
    },
    "put_dev_resources": {
        "method": "PUT",
        "path": "/v1/dev_resources",
        "description": "Update dev resources",
        "parameters": {
            "dev_resources": {"type": "list", "location": "body", "description": "List of dev resources to update (each with id, name, url)"},
        },
        "required": ["dev_resources"],
    },
    "delete_dev_resource": {
        "method": "DELETE",
        "path": "/v1/files/{file_key}/dev_resources/{dev_resource_id}",
        "description": "Delete dev resource",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "dev_resource_id": {"type": "str", "location": "path", "description": "The dev resource ID"},
        },
        "required": ["file_key", "dev_resource_id"],
    },

    # ================================================================================
    # LIBRARY ANALYTICS
    # ================================================================================
    "get_library_analytics_component_actions": {
        "method": "GET",
        "path": "/v1/analytics/libraries/{file_key}/component/actions",
        "description": "Get library analytics for component actions",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "group_by": {"type": "str", "location": "query", "description": "How to group the results"},
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Cursor for pagination"},
            "start_date": {"type": "Optional[str]", "location": "query", "description": "Start date (ISO 8601)"},
            "end_date": {"type": "Optional[str]", "location": "query", "description": "End date (ISO 8601)"},
        },
        "required": ["file_key", "group_by"],
    },
    "get_library_analytics_component_usages": {
        "method": "GET",
        "path": "/v1/analytics/libraries/{file_key}/component/usages",
        "description": "Get library analytics for component usages",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "group_by": {"type": "str", "location": "query", "description": "How to group the results"},
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Cursor for pagination"},
        },
        "required": ["file_key", "group_by"],
    },
    "get_library_analytics_style_actions": {
        "method": "GET",
        "path": "/v1/analytics/libraries/{file_key}/style/actions",
        "description": "Get library analytics for style actions",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "group_by": {"type": "str", "location": "query", "description": "How to group the results"},
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Cursor for pagination"},
            "start_date": {"type": "Optional[str]", "location": "query", "description": "Start date (ISO 8601)"},
            "end_date": {"type": "Optional[str]", "location": "query", "description": "End date (ISO 8601)"},
        },
        "required": ["file_key", "group_by"],
    },
    "get_library_analytics_style_usages": {
        "method": "GET",
        "path": "/v1/analytics/libraries/{file_key}/style/usages",
        "description": "Get library analytics for style usages",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "group_by": {"type": "str", "location": "query", "description": "How to group the results"},
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Cursor for pagination"},
        },
        "required": ["file_key", "group_by"],
    },
    "get_library_analytics_variable_actions": {
        "method": "GET",
        "path": "/v1/analytics/libraries/{file_key}/variable/actions",
        "description": "Get library analytics for variable actions",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "group_by": {"type": "str", "location": "query", "description": "How to group the results"},
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Cursor for pagination"},
            "start_date": {"type": "Optional[str]", "location": "query", "description": "Start date (ISO 8601)"},
            "end_date": {"type": "Optional[str]", "location": "query", "description": "End date (ISO 8601)"},
        },
        "required": ["file_key", "group_by"],
    },
    "get_library_analytics_variable_usages": {
        "method": "GET",
        "path": "/v1/analytics/libraries/{file_key}/variable/usages",
        "description": "Get library analytics for variable usages",
        "parameters": {
            "file_key": {"type": "str", "location": "path", "description": "The file key"},
            "group_by": {"type": "str", "location": "query", "description": "How to group the results"},
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Cursor for pagination"},
        },
        "required": ["file_key", "group_by"],
    },
}


class FigmaDataSourceGenerator:
    """Generator for comprehensive Figma REST API datasource class.

    Generates methods for Figma API v1/v2 endpoints.
    The generated DataSource class accepts a FigmaClient whose base URL
    setting determines the API endpoint (https://api.figma.com).

    All methods have explicit parameter signatures.
    """

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace("-", "_").replace(".", "_").replace("/", "_")
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == "_"):
            sanitized = f"param_{sanitized}"
        # Avoid shadowing Python builtins
        if sanitized == "format":
            sanitized = "image_format"
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
                    elif "int" in param_info["type"] or "float" in param_info["type"]:
                        lines.append(
                            f"        query_params['{api_name}'] = str({sanitized_name})"
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
                elif "Optional[int]" in param_info["type"] or "Optional[float]" in param_info["type"]:
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
            api_name = param_info.get("api_name", param_name)

            if param_name in endpoint_info["required"]:
                lines.append(f"        body['{api_name}'] = {sanitized_name}")
            else:
                lines.extend([
                    f"        if {sanitized_name} is not None:",
                    f"            body['{api_name}'] = {sanitized_name}",
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
            inner = FigmaDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = FigmaDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                FigmaDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = FigmaDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                FigmaDataSourceGenerator._modernize_type(p.strip()) for p in parts
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
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> FigmaResponse:"

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
            "            FigmaResponse with operation result",
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
            "            return FigmaResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return FigmaResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_figma_datasource(self) -> str:
        """Generate the complete Figma datasource class."""

        class_lines = [
            "# ruff: noqa",
            '"""',
            "Figma REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Figma REST API documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.figma.figma import FigmaClient, FigmaResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class FigmaDataSource:",
            '    """Figma REST API DataSource',
            "",
            "    Provides async wrapper methods for Figma REST API operations:",
            "    - Files and File Nodes",
            "    - File Versions",
            "    - Images and Image Fills",
            "    - Comments and Comment Reactions",
            "    - Users",
            "    - Projects and Project Files",
            "    - Components and Component Sets",
            "    - Styles",
            "    - Webhooks (v2)",
            "    - Activity Logs",
            "    - Payments",
            "    - Variables",
            "    - Dev Resources",
            "    - Library Analytics",
            "",
            "    The base URL is determined by the FigmaClient's configured base URL",
            "    (default: https://api.figma.com).",
            "",
            "    All methods return FigmaResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: FigmaClient) -> None:",
            '        """Initialize with FigmaClient.',
            "",
            "        Args:",
            "            client: FigmaClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'FigmaDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> FigmaClient:",
            '        """Return the underlying FigmaClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in FIGMA_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Figma datasource to a file."""
        if filename is None:
            filename = "figma.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        figma_dir = script_dir.parent / "app" / "sources" / "external" / "figma"
        figma_dir.mkdir(parents=True, exist_ok=True)

        full_path = figma_dir / filename

        class_code = self.generate_figma_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Figma data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by category
        resource_categories = {
            "File": 0,
            "File Version": 0,
            "Comment": 0,
            "Comment Reaction": 0,
            "User": 0,
            "Project": 0,
            "Component": 0,
            "Component Set": 0,
            "Style": 0,
            "Webhook": 0,
            "Activity Log": 0,
            "Payment": 0,
            "Variable": 0,
            "Dev Resource": 0,
            "Library Analytics": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "library_analytics" in name:
                resource_categories["Library Analytics"] += 1
            elif "comment_reaction" in name:
                resource_categories["Comment Reaction"] += 1
            elif "comment" in name:
                resource_categories["Comment"] += 1
            elif "component_set" in name:
                resource_categories["Component Set"] += 1
            elif "component" in name:
                resource_categories["Component"] += 1
            elif "version" in name:
                resource_categories["File Version"] += 1
            elif "me" == name or "get_me" == name:
                resource_categories["User"] += 1
            elif "project" in name:
                resource_categories["Project"] += 1
            elif "style" in name:
                resource_categories["Style"] += 1
            elif "webhook" in name:
                resource_categories["Webhook"] += 1
            elif "activity" in name:
                resource_categories["Activity Log"] += 1
            elif "payment" in name:
                resource_categories["Payment"] += 1
            elif "variable" in name:
                resource_categories["Variable"] += 1
            elif "dev_resource" in name:
                resource_categories["Dev Resource"] += 1
            elif "file" in name or "image" in name or "meta" in name:
                resource_categories["File"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for Figma data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Figma REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = FigmaDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Figma data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
