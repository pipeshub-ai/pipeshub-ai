# ruff: noqa
"""
Canva Connect REST API Code Generator

Generates CanvaDataSource class covering Canva Connect API v1:
- User profile
- Designs (list, get, create)
- Folders (list, get, create, items)
- Brand templates (list, get)
- Assets (list, upload)
- Comments (list, create)
- Exports (create, get status)

The generated DataSource accepts a CanvaClient and uses the client's
configured base URL (https://api.canva.com/rest/v1).

All methods have explicit parameter signatures with no **kwargs usage.

API Reference: https://www.canva.dev/docs/connect/
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# Canva Connect API Endpoints
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which is https://api.canva.com/rest/v1)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
# ================================================================================

CANVA_API_ENDPOINTS = {
    # ================================================================================
    # USER / PROFILE
    # ================================================================================
    "get_current_user": {
        "method": "GET",
        "path": "/users/me",
        "description": "Get the profile of the currently authenticated user",
        "parameters": {},
        "required": [],
    },

    # ================================================================================
    # DESIGNS
    # ================================================================================
    "list_designs": {
        "method": "GET",
        "path": "/designs",
        "description": "List designs accessible by the authenticated user",
        "parameters": {
            "ownership": {"type": "Optional[str]", "location": "query", "description": "Filter by ownership (owned, shared, any)"},
            "sort_by": {"type": "Optional[str]", "location": "query", "description": "Sort field (relevance, modified_descending, modified_ascending, title_descending, title_ascending)"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "continuation": {"type": "Optional[str]", "location": "query", "description": "Continuation token for pagination"},
        },
        "required": [],
    },
    "get_design": {
        "method": "GET",
        "path": "/designs/{design_id}",
        "description": "Get metadata for a specific design",
        "parameters": {
            "design_id": {"type": "str", "location": "path", "description": "The design ID"},
        },
        "required": ["design_id"],
    },
    "create_design": {
        "method": "POST",
        "path": "/designs",
        "description": "Create a new Canva design",
        "parameters": {
            "design_type": {"type": "Optional[str]", "location": "body", "description": "Type of design to create"},
            "title": {"type": "Optional[str]", "location": "body", "description": "Title for the new design"},
            "width": {"type": "Optional[int]", "location": "body", "description": "Width of the design in pixels"},
            "height": {"type": "Optional[int]", "location": "body", "description": "Height of the design in pixels"},
            "asset_id": {"type": "Optional[str]", "location": "body", "description": "Asset ID to use as design content"},
        },
        "required": [],
    },

    # ================================================================================
    # FOLDERS
    # ================================================================================
    "list_folders": {
        "method": "GET",
        "path": "/folders",
        "description": "List folders accessible by the authenticated user",
        "parameters": {
            "sort_by": {"type": "Optional[str]", "location": "query", "description": "Sort field (relevance, modified_descending, modified_ascending, title_descending, title_ascending)"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "continuation": {"type": "Optional[str]", "location": "query", "description": "Continuation token for pagination"},
        },
        "required": [],
    },
    "get_folder": {
        "method": "GET",
        "path": "/folders/{folder_id}",
        "description": "Get metadata for a specific folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "path", "description": "The folder ID"},
        },
        "required": ["folder_id"],
    },
    "list_folder_items": {
        "method": "GET",
        "path": "/folders/{folder_id}/items",
        "description": "List items within a specific folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "path", "description": "The folder ID"},
            "item_types": {"type": "Optional[str]", "location": "query", "description": "Filter by item type (design, folder, image)"},
            "sort_by": {"type": "Optional[str]", "location": "query", "description": "Sort field (relevance, modified_descending, modified_ascending, title_descending, title_ascending)"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "continuation": {"type": "Optional[str]", "location": "query", "description": "Continuation token for pagination"},
        },
        "required": ["folder_id"],
    },
    "create_folder": {
        "method": "POST",
        "path": "/folders",
        "description": "Create a new folder",
        "parameters": {
            "name": {"type": "str", "location": "body", "description": "Name of the folder"},
            "parent_folder_id": {"type": "Optional[str]", "location": "body", "description": "ID of the parent folder"},
        },
        "required": ["name"],
    },

    # ================================================================================
    # BRAND TEMPLATES
    # ================================================================================
    "list_brand_templates": {
        "method": "GET",
        "path": "/brand-templates",
        "description": "List brand templates accessible by the authenticated user",
        "parameters": {
            "dataset": {"type": "Optional[str]", "location": "query", "description": "Filter by dataset"},
            "ownership": {"type": "Optional[str]", "location": "query", "description": "Filter by ownership (owned, shared, any)"},
            "sort_by": {"type": "Optional[str]", "location": "query", "description": "Sort field"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "continuation": {"type": "Optional[str]", "location": "query", "description": "Continuation token for pagination"},
        },
        "required": [],
    },
    "get_brand_template": {
        "method": "GET",
        "path": "/brand-templates/{brand_template_id}",
        "description": "Get metadata for a specific brand template",
        "parameters": {
            "brand_template_id": {"type": "str", "location": "path", "description": "The brand template ID"},
        },
        "required": ["brand_template_id"],
    },

    # ================================================================================
    # ASSETS
    # ================================================================================
    "list_assets": {
        "method": "GET",
        "path": "/assets",
        "description": "List assets accessible by the authenticated user",
        "parameters": {
            "sort_by": {"type": "Optional[str]", "location": "query", "description": "Sort field"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "continuation": {"type": "Optional[str]", "location": "query", "description": "Continuation token for pagination"},
        },
        "required": [],
    },
    "upload_asset": {
        "method": "POST",
        "path": "/assets/upload",
        "description": "Upload an asset to Canva (multipart upload)",
        "parameters": {
            "name": {"type": "str", "location": "body", "description": "Name of the asset"},
            "folder_id": {"type": "Optional[str]", "location": "body", "description": "Target folder ID for the asset"},
        },
        "required": ["name"],
    },

    # ================================================================================
    # COMMENTS
    # ================================================================================
    "list_design_comments": {
        "method": "GET",
        "path": "/comments/{design_id}",
        "description": "List comments on a specific design",
        "parameters": {
            "design_id": {"type": "str", "location": "path", "description": "The design ID"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "continuation": {"type": "Optional[str]", "location": "query", "description": "Continuation token for pagination"},
        },
        "required": ["design_id"],
    },
    "create_design_comment": {
        "method": "POST",
        "path": "/comments/{design_id}",
        "description": "Create a comment on a specific design",
        "parameters": {
            "design_id": {"type": "str", "location": "path", "description": "The design ID"},
            "message": {"type": "str", "location": "body", "description": "The comment message text"},
        },
        "required": ["design_id", "message"],
    },

    # ================================================================================
    # EXPORTS
    # ================================================================================
    "create_export": {
        "method": "POST",
        "path": "/exports",
        "description": "Create an export job to export a design",
        "parameters": {
            "design_id": {"type": "str", "location": "body", "description": "The design ID to export"},
            "format": {"type": "Optional[str]", "location": "body", "description": "Export format (pdf, jpg, png, gif, pptx, mp4)"},
            "quality": {"type": "Optional[str]", "location": "body", "description": "Export quality (regular, pro)"},
            "pages": {"type": "Optional[list[int]]", "location": "body", "description": "List of page indices to export"},
            "width": {"type": "Optional[int]", "location": "body", "description": "Target width in pixels"},
            "height": {"type": "Optional[int]", "location": "body", "description": "Target height in pixels"},
        },
        "required": ["design_id"],
    },
    "get_export": {
        "method": "GET",
        "path": "/exports/{export_id}",
        "description": "Get the status and result of an export job",
        "parameters": {
            "export_id": {"type": "str", "location": "path", "description": "The export job ID"},
        },
        "required": ["export_id"],
    },
}


class CanvaDataSourceGenerator:
    """Generator for comprehensive Canva Connect REST API datasource class.

    Generates methods for Canva Connect API v1 endpoints.
    The generated DataSource class accepts a CanvaClient whose base URL
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
        """Convert typing-style annotations to modern Python 3.10+ syntax.

        Optional[str] -> str | None, Dict[str, Any] -> dict[str, Any],
        List[str] -> list[str], etc.
        """
        if type_str.startswith("Optional[") and type_str.endswith("]"):
            inner = type_str[len("Optional["):-1]
            inner = CanvaDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = CanvaDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                CanvaDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = CanvaDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                CanvaDataSourceGenerator._modernize_type(p.strip()) for p in parts
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
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> CanvaResponse:"

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
            "            CanvaResponse with operation result",
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
            "            return CanvaResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return CanvaResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_canva_datasource(self) -> str:
        """Generate the complete Canva datasource class."""

        class_lines = [
            '"""',
            "Canva Connect REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Canva Connect REST API v1 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.canva.canva import CanvaClient, CanvaResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class CanvaDataSource:",
            '    """Canva Connect REST API DataSource',
            "",
            "    Provides async wrapper methods for Canva Connect REST API operations:",
            "    - User profile",
            "    - Designs (list, get, create)",
            "    - Folders (list, get, create, items)",
            "    - Brand templates (list, get)",
            "    - Assets (list, upload)",
            "    - Comments (list, create)",
            "    - Exports (create, get status)",
            "",
            "    The base URL is determined by the CanvaClient's configured base URL.",
            "    All methods return CanvaResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: CanvaClient) -> None:",
            '        """Initialize with CanvaClient.',
            "",
            "        Args:",
            "            client: CanvaClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'CanvaDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> CanvaClient:",
            '        """Return the underlying CanvaClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in CANVA_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Canva datasource to a file."""
        if filename is None:
            filename = "canva.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        canva_dir = script_dir.parent / "app" / "sources" / "external" / "canva"
        canva_dir.mkdir(parents=True, exist_ok=True)

        full_path = canva_dir / filename

        class_code = self.generate_canva_datasource()

        # Strip trailing whitespace from every line
        clean_lines = [line.rstrip() for line in class_code.split("\n")]
        full_path.write_text("\n".join(clean_lines), encoding="utf-8")

        print(f"Generated Canva data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print resource summary
        resource_categories = {
            "User/Profile": 0,
            "Design": 0,
            "Folder": 0,
            "Brand Template": 0,
            "Asset": 0,
            "Comment": 0,
            "Export": 0,
        }

        for method in self.generated_methods:
            endpoint = method["endpoint"]
            if "users" in endpoint:
                resource_categories["User/Profile"] += 1
            elif "designs" in endpoint and "comments" not in endpoint:
                resource_categories["Design"] += 1
            elif "folders" in endpoint:
                resource_categories["Folder"] += 1
            elif "brand-templates" in endpoint:
                resource_categories["Brand Template"] += 1
            elif "assets" in endpoint:
                resource_categories["Asset"] += 1
            elif "comments" in endpoint:
                resource_categories["Comment"] += 1
            elif "exports" in endpoint:
                resource_categories["Export"] += 1

        print(f"\nMethods by resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main() -> int:
    """Main function for Canva data source generator."""
    try:
        generator = CanvaDataSourceGenerator()
        generator.save_to_file()
        return 0
    except Exception as e:
        print(f"Failed to generate Canva data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
