# ruff: noqa
"""
Quip REST API Code Generator

Generates QuipDataSource class covering Quip Automation API:
- Users (current user, get user)
- Threads (documents) - get, create, edit, search, recent
- Messages (thread comments)
- Folders - get, create

The generated DataSource accepts a QuipClient and uses the client's
configured base URL.

All methods have explicit parameter signatures with no **kwargs usage.

Usage:
    python code-generator/quip.py
    python code-generator/quip.py --filename quip.py
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# Quip API Endpoints
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which is https://platform.quip.com/1)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
# ================================================================================

QUIP_API_ENDPOINTS = {
    # ================================================================================
    # USERS
    # ================================================================================
    "get_current_user": {
        "method": "GET",
        "path": "/users/current",
        "description": "Get the authenticated user's information",
        "parameters": {},
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
    "get_users": {
        "method": "GET",
        "path": "/users/{user_ids}",
        "description": "Get multiple users by IDs (comma-separated)",
        "parameters": {
            "user_ids": {"type": "str", "location": "path", "description": "Comma-separated user IDs"},
        },
        "required": ["user_ids"],
    },
    "get_contacts": {
        "method": "GET",
        "path": "/users/contacts",
        "description": "Get the authenticated user's contacts",
        "parameters": {},
        "required": [],
    },

    # ================================================================================
    # THREADS (Documents)
    # ================================================================================
    "get_thread": {
        "method": "GET",
        "path": "/threads/{thread_id}",
        "description": "Get a specific thread (document) by ID",
        "parameters": {
            "thread_id": {"type": "str", "location": "path", "description": "The thread ID"},
        },
        "required": ["thread_id"],
    },
    "get_threads": {
        "method": "GET",
        "path": "/threads/{thread_ids}",
        "description": "Get multiple threads by IDs (comma-separated)",
        "parameters": {
            "thread_ids": {"type": "str", "location": "path", "description": "Comma-separated thread IDs"},
        },
        "required": ["thread_ids"],
    },
    "get_recent_threads": {
        "method": "GET",
        "path": "/threads/recent",
        "description": "Get recently accessed threads for the authenticated user",
        "parameters": {
            "count": {"type": "Optional[int]", "location": "query", "description": "Number of threads to return"},
            "max_updated_usec": {"type": "Optional[int]", "location": "query", "description": "Max updated time in microseconds (for pagination)"},
        },
        "required": [],
    },
    "search_threads": {
        "method": "GET",
        "path": "/threads/search",
        "description": "Search for threads (documents)",
        "parameters": {
            "query": {"type": "str", "location": "query", "description": "Search query string"},
            "count": {"type": "Optional[int]", "location": "query", "description": "Number of results to return"},
            "only_match_titles": {"type": "Optional[bool]", "location": "query", "description": "Only match thread titles"},
        },
        "required": ["query"],
    },
    "create_document": {
        "method": "POST",
        "path": "/threads/new-document",
        "description": "Create a new document thread",
        "parameters": {
            "content": {"type": "str", "location": "body", "description": "HTML content of the document"},
            "title": {"type": "Optional[str]", "location": "body", "description": "Document title"},
            "format": {"type": "Optional[str]", "location": "body", "description": "Content format ('html' or 'markdown')"},
            "member_ids": {"type": "Optional[list[str]]", "location": "body", "description": "List of member IDs to add"},
            "type": {"type": "Optional[str]", "location": "body", "description": "Thread type (document, spreadsheet)"},
        },
        "required": ["content"],
    },
    "edit_document": {
        "method": "POST",
        "path": "/threads/edit-document",
        "description": "Edit an existing document thread",
        "parameters": {
            "thread_id": {"type": "str", "location": "body", "description": "The thread ID to edit"},
            "content": {"type": "Optional[str]", "location": "body", "description": "New HTML content"},
            "format": {"type": "Optional[str]", "location": "body", "description": "Content format ('html' or 'markdown')"},
            "location": {"type": "Optional[int]", "location": "body", "description": "Insert location (0=beginning, 1=end, 2=after_section, 3=before_section, 4=replace_section, 5=delete_section)"},
            "section_id": {"type": "Optional[str]", "location": "body", "description": "Section ID for location-based edits"},
        },
        "required": ["thread_id"],
    },
    "add_thread_members": {
        "method": "POST",
        "path": "/threads/add-members",
        "description": "Add members to a thread",
        "parameters": {
            "thread_id": {"type": "str", "location": "body", "description": "The thread ID"},
            "member_ids": {"type": "list[str]", "location": "body", "description": "List of user IDs to add as members"},
        },
        "required": ["thread_id", "member_ids"],
    },
    "remove_thread_members": {
        "method": "POST",
        "path": "/threads/remove-members",
        "description": "Remove members from a thread",
        "parameters": {
            "thread_id": {"type": "str", "location": "body", "description": "The thread ID"},
            "member_ids": {"type": "list[str]", "location": "body", "description": "List of user IDs to remove"},
        },
        "required": ["thread_id", "member_ids"],
    },
    "move_thread": {
        "method": "POST",
        "path": "/threads/move",
        "description": "Move a thread to a different folder",
        "parameters": {
            "thread_id": {"type": "str", "location": "body", "description": "The thread ID to move"},
            "folder_id": {"type": "str", "location": "body", "description": "Destination folder ID"},
        },
        "required": ["thread_id", "folder_id"],
    },
    "delete_thread": {
        "method": "POST",
        "path": "/threads/delete",
        "description": "Delete (trash) a thread",
        "parameters": {
            "thread_id": {"type": "str", "location": "body", "description": "The thread ID to delete"},
        },
        "required": ["thread_id"],
    },

    # ================================================================================
    # MESSAGES (Thread Comments)
    # ================================================================================
    "get_thread_messages": {
        "method": "GET",
        "path": "/messages/{thread_id}",
        "description": "Get messages (comments) for a thread",
        "parameters": {
            "thread_id": {"type": "str", "location": "path", "description": "The thread ID"},
            "count": {"type": "Optional[int]", "location": "query", "description": "Number of messages to return"},
            "max_created_usec": {"type": "Optional[int]", "location": "query", "description": "Max created time in microseconds (for pagination)"},
        },
        "required": ["thread_id"],
    },
    "create_message": {
        "method": "POST",
        "path": "/messages/new",
        "description": "Create a new message (comment) on a thread",
        "parameters": {
            "thread_id": {"type": "str", "location": "body", "description": "The thread ID to comment on"},
            "content": {"type": "str", "location": "body", "description": "Message content (can contain HTML)"},
            "frame": {"type": "Optional[str]", "location": "body", "description": "Frame type (bubble, card, line)"},
            "section_id": {"type": "Optional[str]", "location": "body", "description": "Section ID to attach comment to"},
            "annotation_id": {"type": "Optional[str]", "location": "body", "description": "Annotation ID for inline comments"},
        },
        "required": ["thread_id", "content"],
    },

    # ================================================================================
    # FOLDERS
    # ================================================================================
    "get_folder": {
        "method": "GET",
        "path": "/folders/{folder_id}",
        "description": "Get a specific folder by ID",
        "parameters": {
            "folder_id": {"type": "str", "location": "path", "description": "The folder ID"},
        },
        "required": ["folder_id"],
    },
    "get_folders": {
        "method": "GET",
        "path": "/folders/{folder_ids}",
        "description": "Get multiple folders by IDs (comma-separated)",
        "parameters": {
            "folder_ids": {"type": "str", "location": "path", "description": "Comma-separated folder IDs"},
        },
        "required": ["folder_ids"],
    },
    "create_folder": {
        "method": "POST",
        "path": "/folders/new",
        "description": "Create a new folder",
        "parameters": {
            "title": {"type": "str", "location": "body", "description": "Folder title"},
            "parent_id": {"type": "Optional[str]", "location": "body", "description": "Parent folder ID"},
            "color": {"type": "Optional[str]", "location": "body", "description": "Folder color (manila, red, orange, green, blue)"},
            "member_ids": {"type": "Optional[list[str]]", "location": "body", "description": "List of member IDs to add"},
        },
        "required": ["title"],
    },
    "update_folder": {
        "method": "POST",
        "path": "/folders/update",
        "description": "Update a folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "body", "description": "The folder ID to update"},
            "title": {"type": "Optional[str]", "location": "body", "description": "New folder title"},
            "color": {"type": "Optional[str]", "location": "body", "description": "New folder color"},
        },
        "required": ["folder_id"],
    },
    "add_folder_members": {
        "method": "POST",
        "path": "/folders/add-members",
        "description": "Add members to a folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "body", "description": "The folder ID"},
            "member_ids": {"type": "list[str]", "location": "body", "description": "List of user IDs to add"},
        },
        "required": ["folder_id", "member_ids"],
    },
    "remove_folder_members": {
        "method": "POST",
        "path": "/folders/remove-members",
        "description": "Remove members from a folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "body", "description": "The folder ID"},
            "member_ids": {"type": "list[str]", "location": "body", "description": "List of user IDs to remove"},
        },
        "required": ["folder_id", "member_ids"],
    },
    "delete_folder": {
        "method": "POST",
        "path": "/folders/delete",
        "description": "Delete (trash) a folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "body", "description": "The folder ID to delete"},
        },
        "required": ["folder_id"],
    },
}


class QuipDataSourceGenerator:
    """Generator for comprehensive Quip REST API datasource class."""

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

        for param_name, param_info in endpoint_info["parameters"].items():
            if param_info["location"] == "query":
                sanitized_name = self._sanitize_parameter_name(param_name)

                if "Optional[bool]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{param_name}'] = str({sanitized_name}).lower()",
                    ])
                elif "Optional[int]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{param_name}'] = str({sanitized_name})",
                    ])
                elif param_name in endpoint_info["required"]:
                    lines.append(f"        query_params['{param_name}'] = {sanitized_name}")
                else:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{param_name}'] = {sanitized_name}",
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
        """Convert typing-style annotations to modern Python 3.10+ syntax."""
        if type_str.startswith("Optional[") and type_str.endswith("]"):
            inner = type_str[len("Optional["):-1]
            inner = QuipDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        return type_str

    def _generate_method_signature(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate method signature with explicit parameters."""
        params = ["self"]

        # Collect required params
        required_params: List[str] = []
        for param_name in endpoint_info["required"]:
            if param_name in endpoint_info["parameters"]:
                param_info = endpoint_info["parameters"][param_name]
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                required_params.append(f"{sanitized_name}: {modern_type}")

        # Collect optional parameters
        optional_params: List[str] = []
        for param_name, param_info in endpoint_info["parameters"].items():
            if param_name not in endpoint_info["required"]:
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                if "| None" not in modern_type:
                    modern_type = f"{modern_type} | None"
                optional_params.append(f"{sanitized_name}: {modern_type} = None")

        params.extend(required_params)
        if optional_params:
            params.append("*")
        params.extend(optional_params)

        signature_params = ",\n        ".join(params)
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> QuipResponse:"

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
            "            QuipResponse with operation result",
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
            "            return QuipResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return QuipResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_quip_datasource(self) -> str:
        """Generate the complete Quip datasource class."""

        class_lines = [
            '"""',
            "Quip REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Quip Automation API documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.http.http_request import HTTPRequest",
            "from app.sources.client.quip.quip import QuipClient, QuipResponse",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class QuipDataSource:",
            '    """Quip REST API DataSource',
            "",
            "    Provides async wrapper methods for Quip Automation API operations:",
            "    - Users (current user, get user, contacts)",
            "    - Threads/Documents (get, create, edit, search, recent)",
            "    - Messages/Comments (get, create)",
            "    - Folders (get, create, update, members)",
            "",
            "    The base URL is https://platform.quip.com/1.",
            "",
            "    All methods return QuipResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: QuipClient) -> None:",
            '        """Initialize with QuipClient.',
            "",
            "        Args:",
            "            client: QuipClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'QuipDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> QuipClient:",
            '        """Return the underlying QuipClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in QUIP_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Quip datasource to a file."""
        if filename is None:
            filename = "quip.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        quip_dir = script_dir.parent / "app" / "sources" / "external" / "quip"
        quip_dir.mkdir(parents=True, exist_ok=True)

        full_path = quip_dir / filename

        class_code = self.generate_quip_datasource()
        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Quip data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by resource
        resource_categories = {
            "Users": 0,
            "Threads/Documents": 0,
            "Messages": 0,
            "Folders": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "user" in name or "contact" in name:
                resource_categories["Users"] += 1
            elif "thread" in name or "document" in name:
                resource_categories["Threads/Documents"] += 1
            elif "message" in name:
                resource_categories["Messages"] += 1
            elif "folder" in name:
                resource_categories["Folders"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for Quip data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Quip REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = QuipDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Quip data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
