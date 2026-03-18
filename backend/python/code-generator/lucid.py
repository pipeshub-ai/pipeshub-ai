# ruff: noqa
"""
Lucid REST API Code Generator

Generates LucidDataSource class covering Lucid API v1:
- User profile operations
- Document management (list, get, create, delete)
- Folder management (list, get, create, folder documents)
- Page listing
- User listing
- Data source operations

The generated DataSource accepts a LucidClient and uses the client's
configured base URL. Methods are generated for all API endpoints.

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# Lucid API Endpoints - organized by resource
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which is https://api.lucid.co/v1)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
#   version: API version tag
# ================================================================================

LUCID_API_ENDPOINTS = {
    # ================================================================================
    # USERS
    # ================================================================================
    "get_current_user": {
        "method": "GET",
        "path": "/users/me",
        "description": "Get the current authenticated user details",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "list_users": {
        "method": "GET",
        "path": "/users",
        "description": "List users in the account",
        "parameters": {
            "pageSize": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Cursor for pagination"},
        },
        "required": [],
        "version": "v1",
    },

    # ================================================================================
    # DOCUMENTS
    # ================================================================================
    "list_documents": {
        "method": "GET",
        "path": "/documents",
        "description": "List all documents accessible to the authenticated user",
        "parameters": {
            "pageSize": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Cursor for pagination"},
            "product": {"type": "Optional[str]", "location": "query", "description": "Filter by product (e.g., lucidchart, lucidspark)"},
        },
        "required": [],
        "version": "v1",
    },
    "get_document": {
        "method": "GET",
        "path": "/documents/{documentId}",
        "description": "Get a specific document by ID",
        "parameters": {
            "documentId": {"type": "str", "location": "path", "description": "The document ID"},
        },
        "required": ["documentId"],
        "version": "v1",
    },
    "create_document": {
        "method": "POST",
        "path": "/documents",
        "description": "Create a new document",
        "parameters": {
            "title": {"type": "Optional[str]", "location": "body", "description": "The title of the document"},
            "product": {"type": "Optional[str]", "location": "body", "description": "The product type (e.g., lucidchart, lucidspark)"},
            "folderId": {"type": "Optional[str]", "location": "body", "description": "The folder ID to create the document in"},
        },
        "required": [],
        "version": "v1",
    },
    "delete_document": {
        "method": "DELETE",
        "path": "/documents/{documentId}",
        "description": "Delete a document by ID",
        "parameters": {
            "documentId": {"type": "str", "location": "path", "description": "The document ID to delete"},
        },
        "required": ["documentId"],
        "version": "v1",
    },

    # ================================================================================
    # FOLDERS
    # ================================================================================
    "list_folders": {
        "method": "GET",
        "path": "/folders",
        "description": "List all folders accessible to the authenticated user",
        "parameters": {
            "pageSize": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Cursor for pagination"},
        },
        "required": [],
        "version": "v1",
    },
    "get_folder": {
        "method": "GET",
        "path": "/folders/{folderId}",
        "description": "Get a specific folder by ID",
        "parameters": {
            "folderId": {"type": "str", "location": "path", "description": "The folder ID"},
        },
        "required": ["folderId"],
        "version": "v1",
    },
    "list_folder_documents": {
        "method": "GET",
        "path": "/folders/{folderId}/documents",
        "description": "List documents in a specific folder",
        "parameters": {
            "folderId": {"type": "str", "location": "path", "description": "The folder ID"},
            "pageSize": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Cursor for pagination"},
        },
        "required": ["folderId"],
        "version": "v1",
    },
    "create_folder": {
        "method": "POST",
        "path": "/folders",
        "description": "Create a new folder",
        "parameters": {
            "name": {"type": "str", "location": "body", "description": "The name of the folder"},
            "parentFolderId": {"type": "Optional[str]", "location": "body", "description": "The parent folder ID"},
        },
        "required": ["name"],
        "version": "v1",
    },

    # ================================================================================
    # PAGES
    # ================================================================================
    "list_pages": {
        "method": "GET",
        "path": "/pages/{documentId}",
        "description": "List all pages in a document",
        "parameters": {
            "documentId": {"type": "str", "location": "path", "description": "The document ID"},
        },
        "required": ["documentId"],
        "version": "v1",
    },

    # ================================================================================
    # DATA SOURCES
    # ================================================================================
    "list_data_sources": {
        "method": "GET",
        "path": "/data-sources",
        "description": "List all data sources",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "get_data_source_by_id": {
        "method": "GET",
        "path": "/data-sources/{dataSourceId}",
        "description": "Get a specific data source by ID",
        "parameters": {
            "dataSourceId": {"type": "str", "location": "path", "description": "The data source ID"},
        },
        "required": ["dataSourceId"],
        "version": "v1",
    },
}


class LucidDataSourceGenerator:
    """Generator for comprehensive Lucid REST API datasource class.

    Generates methods for Lucid API v1 endpoints.
    The generated DataSource class accepts a LucidClient whose base URL
    is https://api.lucid.co/v1.
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
                elif "List[" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{param_name}[]'] = {sanitized_name}",
                    ])
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
            inner = LucidDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = LucidDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                LucidDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = LucidDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                LucidDataSourceGenerator._modernize_type(p.strip()) for p in parts
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
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> LucidResponse:"

    def _generate_method_docstring(self, endpoint_info: Dict) -> List[str]:
        """Generate method docstring."""
        version = endpoint_info.get("version", "v1")
        lines = [f'        """{endpoint_info["description"]} (API {version})', ""]

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
            "            LucidResponse with operation result",
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
            "            return LucidResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return LucidResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
            "version": endpoint_info.get("version", "v1"),
        })

        return "\n".join(lines)

    def generate_lucid_datasource(self) -> str:
        """Generate the complete Lucid datasource class."""

        class_lines = [
            '"""',
            "Lucid REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Lucid REST API v1 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.lucid.lucid import LucidClient, LucidResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class LucidDataSource:",
            '    """Lucid REST API DataSource',
            "",
            "    Provides async wrapper methods for Lucid REST API operations:",
            "    - User profile management",
            "    - Document CRUD operations",
            "    - Folder management",
            "    - Page listing",
            "    - Data source operations",
            "",
            "    The base URL is https://api.lucid.co/v1.",
            "",
            "    All methods return LucidResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: LucidClient) -> None:",
            '        """Initialize with LucidClient.',
            "",
            "        Args:",
            "            client: LucidClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'LucidDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> LucidClient:",
            '        """Return the underlying LucidClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in LUCID_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Lucid datasource to a file."""
        if filename is None:
            filename = "lucid.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        lucid_dir = script_dir.parent / "app" / "sources" / "external" / "lucid"
        lucid_dir.mkdir(parents=True, exist_ok=True)

        full_path = lucid_dir / filename

        class_code = self.generate_lucid_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Lucid data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by category
        resource_categories = {
            "User": 0,
            "Document": 0,
            "Folder": 0,
            "Page": 0,
            "Data Source": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "user" in name:
                resource_categories["User"] += 1
            elif "document" in name:
                resource_categories["Document"] += 1
            elif "folder" in name:
                resource_categories["Folder"] += 1
            elif "page" in name:
                resource_categories["Page"] += 1
            elif "data_source" in name:
                resource_categories["Data Source"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for Lucid data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Lucid REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = LucidDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Lucid data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
