# ruff: noqa
"""
Egnyte REST API Code Generator

Generates EgnyteDataSource class covering Egnyte Public API v1:
- File system operations (metadata, content, folders)
- Links management
- User and group management
- Audit operations (files, logins, permissions)

The generated DataSource accepts an EgnyteClient and uses the client's
configured domain to construct the base URL.

All methods have explicit parameter signatures with no **kwargs usage.

Usage:
    python code-generator/egnyte.py
    python code-generator/egnyte.py --filename egnyte.py
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# Egnyte API Endpoints
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which already includes /pubapi/v1)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
# ================================================================================

EGNYTE_API_ENDPOINTS = {
    # ================================================================================
    # FILE SYSTEM - METADATA
    # ================================================================================
    "get_file_or_folder_metadata": {
        "method": "GET",
        "path": "/fs/{path}",
        "description": "Get file or folder metadata at the given path",
        "parameters": {
            "path": {"type": "str", "location": "path", "description": "File or folder path (e.g. 'Shared/Documents')"},
            "list_content": {"type": "Optional[bool]", "location": "query", "description": "If true and path is a folder, list contents"},
            "allowed_link_types": {"type": "Optional[bool]", "location": "query", "description": "Include allowed link types info"},
            "count": {"type": "Optional[int]", "location": "query", "description": "Number of items to return (for folder listing)"},
            "offset": {"type": "Optional[int]", "location": "query", "description": "Offset for pagination (for folder listing)"},
            "sort_by": {"type": "Optional[str]", "location": "query", "description": "Sort field (name, last_modified, size)"},
            "sort_direction": {"type": "Optional[str]", "location": "query", "description": "Sort direction (asc, desc)"},
        },
        "required": ["path"],
    },
    "create_folder": {
        "method": "POST",
        "path": "/fs/{path}",
        "description": "Create a folder at the given path",
        "parameters": {
            "path": {"type": "str", "location": "path", "description": "Folder path to create"},
            "action": {"type": "str", "location": "body", "description": "Action type (must be 'add_folder')"},
        },
        "required": ["path", "action"],
    },
    "delete_file_or_folder": {
        "method": "DELETE",
        "path": "/fs/{path}",
        "description": "Delete a file or folder at the given path",
        "parameters": {
            "path": {"type": "str", "location": "path", "description": "File or folder path to delete"},
        },
        "required": ["path"],
    },
    "move_file_or_folder": {
        "method": "POST",
        "path": "/fs/{path}",
        "description": "Move or copy a file or folder",
        "parameters": {
            "path": {"type": "str", "location": "path", "description": "Source file or folder path"},
            "action": {"type": "str", "location": "body", "description": "Action type ('move' or 'copy')"},
            "destination": {"type": "str", "location": "body", "description": "Destination path"},
        },
        "required": ["path", "action", "destination"],
    },

    # ================================================================================
    # FILE SYSTEM - CONTENT
    # ================================================================================
    "download_file": {
        "method": "GET",
        "path": "/fs-content/{path}",
        "description": "Download file content at the given path",
        "parameters": {
            "path": {"type": "str", "location": "path", "description": "File path to download"},
            "entry_id": {"type": "Optional[str]", "location": "query", "description": "Specific version entry ID"},
        },
        "required": ["path"],
    },
    "upload_file": {
        "method": "POST",
        "path": "/fs-content/{path}",
        "description": "Upload file content to the given path",
        "parameters": {
            "path": {"type": "str", "location": "path", "description": "File path for upload"},
        },
        "required": ["path"],
    },

    # ================================================================================
    # LINKS
    # ================================================================================
    "list_links": {
        "method": "GET",
        "path": "/links",
        "description": "List shared links",
        "parameters": {
            "path": {"type": "Optional[str]", "location": "query", "description": "Filter by path"},
            "type_": {"type": "Optional[str]", "location": "query", "description": "Link type (file or folder)"},
            "accessibility": {"type": "Optional[str]", "location": "query", "description": "Accessibility (anyone, password, domain, recipients)"},
            "count": {"type": "Optional[int]", "location": "query", "description": "Number of links to return"},
            "offset": {"type": "Optional[int]", "location": "query", "description": "Offset for pagination"},
        },
        "required": [],
    },
    "create_link": {
        "method": "POST",
        "path": "/links",
        "description": "Create a shared link",
        "parameters": {
            "path": {"type": "str", "location": "body", "description": "Path to the file or folder"},
            "type_": {"type": "str", "location": "body", "description": "Link type (file or folder)"},
            "accessibility": {"type": "str", "location": "body", "description": "Accessibility (anyone, password, domain, recipients)"},
            "send_email": {"type": "Optional[bool]", "location": "body", "description": "Send email notification"},
            "recipients": {"type": "Optional[list[str]]", "location": "body", "description": "List of recipient email addresses"},
            "message": {"type": "Optional[str]", "location": "body", "description": "Email message body"},
            "copy_me": {"type": "Optional[bool]", "location": "body", "description": "Send copy to creator"},
            "notify": {"type": "Optional[bool]", "location": "body", "description": "Notify on access"},
            "link_to_current": {"type": "Optional[bool]", "location": "body", "description": "Link to current version only"},
            "expiry_date": {"type": "Optional[str]", "location": "body", "description": "Expiry date (YYYY-MM-DD)"},
            "expiry_clicks": {"type": "Optional[int]", "location": "body", "description": "Number of clicks before expiry"},
            "add_file_name": {"type": "Optional[bool]", "location": "body", "description": "Add file name to link"},
        },
        "required": ["path", "type_", "accessibility"],
    },
    "get_link": {
        "method": "GET",
        "path": "/links/{link_id}",
        "description": "Get a specific shared link",
        "parameters": {
            "link_id": {"type": "str", "location": "path", "description": "The link ID"},
        },
        "required": ["link_id"],
    },
    "delete_link": {
        "method": "DELETE",
        "path": "/links/{link_id}",
        "description": "Delete a shared link",
        "parameters": {
            "link_id": {"type": "str", "location": "path", "description": "The link ID"},
        },
        "required": ["link_id"],
    },

    # ================================================================================
    # USER INFO
    # ================================================================================
    "get_current_user": {
        "method": "GET",
        "path": "/userinfo",
        "description": "Get current authenticated user info",
        "parameters": {},
        "required": [],
    },

    # ================================================================================
    # USERS
    # ================================================================================
    "list_users": {
        "method": "GET",
        "path": "/users",
        "description": "List users in the domain",
        "parameters": {
            "startIndex": {"type": "Optional[int]", "location": "query", "description": "Start index for pagination (1-based)"},
            "count": {"type": "Optional[int]", "location": "query", "description": "Number of users to return (max 100)"},
        },
        "required": [],
    },
    "get_user": {
        "method": "GET",
        "path": "/users/{user_id}",
        "description": "Get a specific user by ID",
        "parameters": {
            "user_id": {"type": "str", "location": "path", "description": "The user ID"},
        },
        "required": ["user_id"],
    },
    "create_user": {
        "method": "POST",
        "path": "/users",
        "description": "Create a new user",
        "parameters": {
            "userName": {"type": "str", "location": "body", "description": "Username (email)"},
            "externalId": {"type": "str", "location": "body", "description": "External ID"},
            "email": {"type": "str", "location": "body", "description": "User email address"},
            "name": {"type": "dict[str, str]", "location": "body", "description": "User name object with familyName and givenName"},
            "active": {"type": "Optional[bool]", "location": "body", "description": "Whether user is active"},
            "sendInvite": {"type": "Optional[bool]", "location": "body", "description": "Send invite email"},
            "authType": {"type": "Optional[str]", "location": "body", "description": "Authentication type"},
            "userType": {"type": "Optional[str]", "location": "body", "description": "User type (power, standard, etc.)"},
            "role": {"type": "Optional[str]", "location": "body", "description": "User role"},
        },
        "required": ["userName", "externalId", "email", "name"],
    },
    "update_user": {
        "method": "PATCH",
        "path": "/users/{user_id}",
        "description": "Update an existing user",
        "parameters": {
            "user_id": {"type": "str", "location": "path", "description": "The user ID"},
            "userName": {"type": "Optional[str]", "location": "body", "description": "Username (email)"},
            "email": {"type": "Optional[str]", "location": "body", "description": "User email address"},
            "name": {"type": "Optional[dict[str, str]]", "location": "body", "description": "User name object"},
            "active": {"type": "Optional[bool]", "location": "body", "description": "Whether user is active"},
            "userType": {"type": "Optional[str]", "location": "body", "description": "User type"},
            "role": {"type": "Optional[str]", "location": "body", "description": "User role"},
        },
        "required": ["user_id"],
    },
    "delete_user": {
        "method": "DELETE",
        "path": "/users/{user_id}",
        "description": "Delete a user",
        "parameters": {
            "user_id": {"type": "str", "location": "path", "description": "The user ID"},
        },
        "required": ["user_id"],
    },

    # ================================================================================
    # GROUPS
    # ================================================================================
    "list_groups": {
        "method": "GET",
        "path": "/groups",
        "description": "List all groups",
        "parameters": {},
        "required": [],
    },
    "get_group": {
        "method": "GET",
        "path": "/groups/{group_id}",
        "description": "Get a specific group by ID",
        "parameters": {
            "group_id": {"type": "str", "location": "path", "description": "The group ID"},
        },
        "required": ["group_id"],
    },
    "create_group": {
        "method": "POST",
        "path": "/groups",
        "description": "Create a new group",
        "parameters": {
            "displayName": {"type": "str", "location": "body", "description": "Group display name"},
            "members": {"type": "Optional[list[dict[str, str]]]", "location": "body", "description": "List of member objects with 'value' (user ID)"},
        },
        "required": ["displayName"],
    },
    "update_group": {
        "method": "PATCH",
        "path": "/groups/{group_id}",
        "description": "Update a group",
        "parameters": {
            "group_id": {"type": "str", "location": "path", "description": "The group ID"},
            "displayName": {"type": "Optional[str]", "location": "body", "description": "Group display name"},
            "members": {"type": "Optional[list[dict[str, str]]]", "location": "body", "description": "List of member objects"},
        },
        "required": ["group_id"],
    },
    "delete_group": {
        "method": "DELETE",
        "path": "/groups/{group_id}",
        "description": "Delete a group",
        "parameters": {
            "group_id": {"type": "str", "location": "path", "description": "The group ID"},
        },
        "required": ["group_id"],
    },

    # ================================================================================
    # AUDIT
    # ================================================================================
    "audit_files": {
        "method": "GET",
        "path": "/audit/files",
        "description": "Audit file activity (access, uploads, downloads, etc.)",
        "parameters": {
            "startdate": {"type": "str", "location": "query", "description": "Start date (YYYY-MM-DD)"},
            "enddate": {"type": "str", "location": "query", "description": "End date (YYYY-MM-DD)"},
            "count": {"type": "Optional[int]", "location": "query", "description": "Number of records to return"},
            "offset": {"type": "Optional[int]", "location": "query", "description": "Offset for pagination"},
            "folder": {"type": "Optional[str]", "location": "query", "description": "Filter by folder path"},
            "file": {"type": "Optional[str]", "location": "query", "description": "Filter by file path"},
            "users": {"type": "Optional[str]", "location": "query", "description": "Filter by username"},
            "transaction_type": {"type": "Optional[str]", "location": "query", "description": "Transaction type filter"},
        },
        "required": ["startdate", "enddate"],
    },
    "audit_logins": {
        "method": "GET",
        "path": "/audit/logins",
        "description": "Audit login activity",
        "parameters": {
            "startdate": {"type": "str", "location": "query", "description": "Start date (YYYY-MM-DD)"},
            "enddate": {"type": "str", "location": "query", "description": "End date (YYYY-MM-DD)"},
            "count": {"type": "Optional[int]", "location": "query", "description": "Number of records to return"},
            "offset": {"type": "Optional[int]", "location": "query", "description": "Offset for pagination"},
            "users": {"type": "Optional[str]", "location": "query", "description": "Filter by username"},
            "events": {"type": "Optional[str]", "location": "query", "description": "Filter by event type"},
            "access_points": {"type": "Optional[str]", "location": "query", "description": "Filter by access point"},
        },
        "required": ["startdate", "enddate"],
    },
    "audit_permissions": {
        "method": "GET",
        "path": "/audit/permissions",
        "description": "Audit permissions changes",
        "parameters": {
            "startdate": {"type": "str", "location": "query", "description": "Start date (YYYY-MM-DD)"},
            "enddate": {"type": "str", "location": "query", "description": "End date (YYYY-MM-DD)"},
            "count": {"type": "Optional[int]", "location": "query", "description": "Number of records to return"},
            "offset": {"type": "Optional[int]", "location": "query", "description": "Offset for pagination"},
            "folder": {"type": "Optional[str]", "location": "query", "description": "Filter by folder path"},
            "users": {"type": "Optional[str]", "location": "query", "description": "Filter by username"},
        },
        "required": ["startdate", "enddate"],
    },

    # ================================================================================
    # SEARCH
    # ================================================================================
    "search": {
        "method": "GET",
        "path": "/search",
        "description": "Search for files and folders",
        "parameters": {
            "query": {"type": "str", "location": "query", "description": "Search query string"},
            "offset": {"type": "Optional[int]", "location": "query", "description": "Offset for pagination"},
            "count": {"type": "Optional[int]", "location": "query", "description": "Number of results to return"},
            "folder": {"type": "Optional[str]", "location": "query", "description": "Restrict search to folder path"},
            "modified_before": {"type": "Optional[str]", "location": "query", "description": "Filter modified before (ISO 8601)"},
            "modified_after": {"type": "Optional[str]", "location": "query", "description": "Filter modified after (ISO 8601)"},
            "type_": {"type": "Optional[str]", "location": "query", "description": "Filter by type (file, folder)"},
        },
        "required": ["query"],
    },

    # ================================================================================
    # PERMISSIONS
    # ================================================================================
    "get_folder_permissions": {
        "method": "GET",
        "path": "/perms/{path}",
        "description": "Get permissions for a folder",
        "parameters": {
            "path": {"type": "str", "location": "path", "description": "Folder path"},
        },
        "required": ["path"],
    },
    "set_folder_permissions": {
        "method": "POST",
        "path": "/perms/{path}",
        "description": "Set permissions for a folder",
        "parameters": {
            "path": {"type": "str", "location": "path", "description": "Folder path"},
            "userPerms": {"type": "Optional[dict[str, str]]", "location": "body", "description": "User permissions mapping"},
            "groupPerms": {"type": "Optional[dict[str, str]]", "location": "body", "description": "Group permissions mapping"},
            "inheritsPermissions": {"type": "Optional[bool]", "location": "body", "description": "Whether folder inherits parent permissions"},
        },
        "required": ["path"],
    },
}


class EgnyteDataSourceGenerator:
    """Generator for comprehensive Egnyte REST API datasource class."""

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace("-", "_").replace(".", "_").replace("/", "_")
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == "_"):
            sanitized = f"param_{sanitized}"
        # Handle trailing underscore for reserved words
        if sanitized == "type_":
            return "type_"
        return sanitized

    def _build_query_params(self, endpoint_info: Dict) -> List[str]:
        """Build query parameter handling code."""
        lines = ["        query_params: dict[str, Any] = {}"]

        # Map for parameters that use different API names
        api_name_map = {"type_": "type"}

        for param_name, param_info in endpoint_info["parameters"].items():
            if param_info["location"] == "query":
                sanitized_name = self._sanitize_parameter_name(param_name)
                api_name = api_name_map.get(param_name, param_name)

                if "Optional[bool]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}'] = str({sanitized_name}).lower()",
                    ])
                elif "Optional[int]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}'] = str({sanitized_name})",
                    ])
                elif param_name in endpoint_info["required"]:
                    lines.append(f"        query_params['{api_name}'] = {sanitized_name}")
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

        # Map for parameters that use different API names
        api_name_map = {"type_": "type"}

        lines = ["        body: dict[str, Any] = {}"]

        for param_name, param_info in body_params.items():
            sanitized_name = self._sanitize_parameter_name(param_name)
            api_name = api_name_map.get(param_name, param_name)

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
        """Convert typing-style annotations to modern Python 3.10+ syntax."""
        if type_str.startswith("Optional[") and type_str.endswith("]"):
            inner = type_str[len("Optional["):-1]
            inner = EgnyteDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        return type_str

    def _generate_method_signature(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate method signature with explicit parameters."""
        params = ["self"]

        # Collect required params
        required_non_bool: List[str] = []
        for param_name in endpoint_info["required"]:
            if param_name in endpoint_info["parameters"]:
                param_info = endpoint_info["parameters"][param_name]
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                required_non_bool.append(f"{sanitized_name}: {modern_type}")

        # Collect optional parameters
        optional_params: List[str] = []
        for param_name, param_info in endpoint_info["parameters"].items():
            if param_name not in endpoint_info["required"]:
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                if "| None" not in modern_type:
                    modern_type = f"{modern_type} | None"
                optional_params.append(f"{sanitized_name}: {modern_type} = None")

        params.extend(required_non_bool)
        if optional_params:
            params.append("*")
        params.extend(optional_params)

        signature_params = ",\n        ".join(params)
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> EgnyteResponse:"

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
            "            EgnyteResponse with operation result",
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
            "            return EgnyteResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return EgnyteResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_egnyte_datasource(self) -> str:
        """Generate the complete Egnyte datasource class."""

        class_lines = [
            '"""',
            "Egnyte REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Egnyte Public API v1 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.egnyte.egnyte import EgnyteClient, EgnyteResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class EgnyteDataSource:",
            '    """Egnyte REST API DataSource',
            "",
            "    Provides async wrapper methods for Egnyte Public API v1 operations:",
            "    - File system operations (metadata, content, folders)",
            "    - Links management",
            "    - User and group management",
            "    - Audit operations (files, logins, permissions)",
            "    - Search",
            "    - Permissions management",
            "",
            "    The base URL is determined by the EgnyteClient's configured domain.",
            "",
            "    All methods return EgnyteResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: EgnyteClient) -> None:",
            '        """Initialize with EgnyteClient.',
            "",
            "        Args:",
            "            client: EgnyteClient instance with configured authentication and domain",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'EgnyteDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> EgnyteClient:",
            '        """Return the underlying EgnyteClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in EGNYTE_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Egnyte datasource to a file."""
        if filename is None:
            filename = "egnyte.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        egnyte_dir = script_dir.parent / "app" / "sources" / "external" / "egnyte"
        egnyte_dir.mkdir(parents=True, exist_ok=True)

        full_path = egnyte_dir / filename

        class_code = self.generate_egnyte_datasource()
        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Egnyte data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by resource
        resource_categories = {
            "File System": 0,
            "Links": 0,
            "Users": 0,
            "Groups": 0,
            "Audit": 0,
            "Search": 0,
            "Permissions": 0,
            "User Info": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "file" in name or "folder" in name or "download" in name or "upload" in name:
                resource_categories["File System"] += 1
            elif "link" in name:
                resource_categories["Links"] += 1
            elif "user" in name and "current" not in name:
                resource_categories["Users"] += 1
            elif "group" in name:
                resource_categories["Groups"] += 1
            elif "audit" in name:
                resource_categories["Audit"] += 1
            elif "search" in name:
                resource_categories["Search"] += 1
            elif "perm" in name:
                resource_categories["Permissions"] += 1
            elif "current" in name:
                resource_categories["User Info"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for Egnyte data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Egnyte REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = EgnyteDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Egnyte data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
