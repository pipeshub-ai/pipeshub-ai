# ruff: noqa
"""
InVision REST API Code Generator

Generates InVisionDataSource class covering InVision API v2:
- Project management (list, get, create)
- Screen operations (list, get)
- Comment operations
- Team and member management
- Space operations
- User profile

The generated DataSource accepts an InVisionClient and uses the client's
configured base URL. Methods are generated for all API endpoints.

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# InVision API Endpoints - organized by resource
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which is https://api.invisionapp.com/v2)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
#   version: API version tag
# ================================================================================

INVISION_API_ENDPOINTS = {
    # ================================================================================
    # USERS
    # ================================================================================
    "get_current_user": {
        "method": "GET",
        "path": "/users/me",
        "description": "Get the current authenticated user details",
        "parameters": {},
        "required": [],
        "version": "v2",
    },

    # ================================================================================
    # PROJECTS
    # ================================================================================
    "list_projects": {
        "method": "GET",
        "path": "/projects",
        "description": "List all projects accessible to the authenticated user",
        "parameters": {
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "offset": {"type": "Optional[int]", "location": "query", "description": "Number of results to skip for pagination"},
            "sortBy": {"type": "Optional[str]", "location": "query", "description": "Field to sort results by"},
            "archived": {"type": "Optional[bool]", "location": "query", "description": "Filter by archived status"},
        },
        "required": [],
        "version": "v2",
    },
    "get_project": {
        "method": "GET",
        "path": "/projects/{projectId}",
        "description": "Get a specific project by ID",
        "parameters": {
            "projectId": {"type": "str", "location": "path", "description": "The project ID"},
        },
        "required": ["projectId"],
        "version": "v2",
    },
    "create_project": {
        "method": "POST",
        "path": "/projects",
        "description": "Create a new project",
        "parameters": {
            "name": {"type": "str", "location": "body", "description": "The name of the project"},
            "project_type": {"type": "Optional[str]", "location": "body", "description": "The type of the project"},
            "description": {"type": "Optional[str]", "location": "body", "description": "The project description"},
        },
        "required": ["name"],
        "version": "v2",
    },

    # ================================================================================
    # SCREENS
    # ================================================================================
    "list_project_screens": {
        "method": "GET",
        "path": "/projects/{projectId}/screens",
        "description": "List all screens in a project",
        "parameters": {
            "projectId": {"type": "str", "location": "path", "description": "The project ID"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "offset": {"type": "Optional[int]", "location": "query", "description": "Number of results to skip for pagination"},
            "sortBy": {"type": "Optional[str]", "location": "query", "description": "Field to sort results by"},
        },
        "required": ["projectId"],
        "version": "v2",
    },
    "get_screen": {
        "method": "GET",
        "path": "/screens/{screenId}",
        "description": "Get a specific screen by ID",
        "parameters": {
            "screenId": {"type": "str", "location": "path", "description": "The screen ID"},
        },
        "required": ["screenId"],
        "version": "v2",
    },

    # ================================================================================
    # COMMENTS
    # ================================================================================
    "list_project_comments": {
        "method": "GET",
        "path": "/projects/{projectId}/comments",
        "description": "List all comments in a project",
        "parameters": {
            "projectId": {"type": "str", "location": "path", "description": "The project ID"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "offset": {"type": "Optional[int]", "location": "query", "description": "Number of results to skip for pagination"},
        },
        "required": ["projectId"],
        "version": "v2",
    },

    # ================================================================================
    # TEAMS
    # ================================================================================
    "list_teams": {
        "method": "GET",
        "path": "/teams",
        "description": "List all teams",
        "parameters": {},
        "required": [],
        "version": "v2",
    },
    "get_team": {
        "method": "GET",
        "path": "/teams/{teamId}",
        "description": "Get a specific team by ID",
        "parameters": {
            "teamId": {"type": "str", "location": "path", "description": "The team ID"},
        },
        "required": ["teamId"],
        "version": "v2",
    },
    "list_team_members": {
        "method": "GET",
        "path": "/teams/{teamId}/members",
        "description": "List all members of a team",
        "parameters": {
            "teamId": {"type": "str", "location": "path", "description": "The team ID"},
        },
        "required": ["teamId"],
        "version": "v2",
    },

    # ================================================================================
    # SPACES
    # ================================================================================
    "list_spaces": {
        "method": "GET",
        "path": "/spaces",
        "description": "List all spaces",
        "parameters": {
            "limit": {"type": "Optional[int]", "location": "query", "description": "Maximum number of results to return"},
            "offset": {"type": "Optional[int]", "location": "query", "description": "Number of results to skip for pagination"},
        },
        "required": [],
        "version": "v2",
    },
    "get_space": {
        "method": "GET",
        "path": "/spaces/{spaceId}",
        "description": "Get a specific space by ID",
        "parameters": {
            "spaceId": {"type": "str", "location": "path", "description": "The space ID"},
        },
        "required": ["spaceId"],
        "version": "v2",
    },
}


class InVisionDataSourceGenerator:
    """Generator for comprehensive InVision REST API datasource class.

    Generates methods for InVision API v2 endpoints.
    The generated DataSource class accepts an InVisionClient whose base URL
    is https://api.invisionapp.com/v2.
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
            inner = InVisionDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = InVisionDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                InVisionDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = InVisionDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                InVisionDataSourceGenerator._modernize_type(p.strip()) for p in parts
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
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> InVisionResponse:"

    def _generate_method_docstring(self, endpoint_info: Dict) -> List[str]:
        """Generate method docstring."""
        version = endpoint_info.get("version", "v2")
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
            "            InVisionResponse with operation result",
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
            "            return InVisionResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return InVisionResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
            "version": endpoint_info.get("version", "v2"),
        })

        return "\n".join(lines)

    def generate_invision_datasource(self) -> str:
        """Generate the complete InVision datasource class."""

        class_lines = [
            '"""',
            "InVision REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from InVision REST API v2 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.invision.invision import InVisionClient, InVisionResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class InVisionDataSource:",
            '    """InVision REST API DataSource',
            "",
            "    Provides async wrapper methods for InVision REST API operations:",
            "    - User profile",
            "    - Project management (list, get, create)",
            "    - Screen operations (list, get)",
            "    - Comment management",
            "    - Team and member management",
            "    - Space operations",
            "",
            "    The base URL is https://api.invisionapp.com/v2.",
            "",
            "    All methods return InVisionResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: InVisionClient) -> None:",
            '        """Initialize with InVisionClient.',
            "",
            "        Args:",
            "            client: InVisionClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'InVisionDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> InVisionClient:",
            '        """Return the underlying InVisionClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in INVISION_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the InVision datasource to a file."""
        if filename is None:
            filename = "invision.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        invision_dir = script_dir.parent / "app" / "sources" / "external" / "invision"
        invision_dir.mkdir(parents=True, exist_ok=True)

        full_path = invision_dir / filename

        class_code = self.generate_invision_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated InVision data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by category
        resource_categories = {
            "User": 0,
            "Project": 0,
            "Screen": 0,
            "Comment": 0,
            "Team": 0,
            "Space": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "user" in name:
                resource_categories["User"] += 1
            elif "project" in name and "screen" not in name and "comment" not in name:
                resource_categories["Project"] += 1
            elif "screen" in name:
                resource_categories["Screen"] += 1
            elif "comment" in name:
                resource_categories["Comment"] += 1
            elif "team" in name or "member" in name:
                resource_categories["Team"] += 1
            elif "space" in name:
                resource_categories["Space"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for InVision data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate InVision REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = InVisionDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate InVision data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
