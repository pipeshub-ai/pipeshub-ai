# ruff: noqa
"""
Aha! REST API Code Generator

Generates AhaDataSource class covering Aha! API v1:
- User profile and management
- Product management
- Feature CRUD operations
- Idea management
- Release management
- Goal operations
- Epic management
- Integration listing

The generated DataSource accepts an AhaClient and uses the client's
configured subdomain-based base URL. Methods are generated for all
API endpoints.

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# Aha! API Endpoints - organized by resource
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which is https://{subdomain}.aha.io/api/v1)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
#   version: API version tag
# ================================================================================

AHA_API_ENDPOINTS = {
    # ================================================================================
    # USERS
    # ================================================================================
    "get_current_user": {
        "method": "GET",
        "path": "/me",
        "description": "Get the current authenticated user details",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "list_users": {
        "method": "GET",
        "path": "/users",
        "description": "List all users in the account",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": [],
        "version": "v1",
    },
    "get_user": {
        "method": "GET",
        "path": "/users/{user_id}",
        "description": "Get a specific user by ID",
        "parameters": {
            "user_id": {"type": "str", "location": "path", "description": "The user ID"},
        },
        "required": ["user_id"],
        "version": "v1",
    },

    # ================================================================================
    # PRODUCTS
    # ================================================================================
    "list_products": {
        "method": "GET",
        "path": "/products",
        "description": "List all products in the account",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": [],
        "version": "v1",
    },
    "get_product": {
        "method": "GET",
        "path": "/products/{product_id}",
        "description": "Get a specific product by ID",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },

    # ================================================================================
    # FEATURES
    # ================================================================================
    "list_product_features": {
        "method": "GET",
        "path": "/products/{product_id}/features",
        "description": "List all features for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
            "q": {"type": "Optional[str]", "location": "query", "description": "Search query string"},
            "assigned_to_user": {"type": "Optional[str]", "location": "query", "description": "Filter by assigned user"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "get_feature": {
        "method": "GET",
        "path": "/features/{feature_id}",
        "description": "Get a specific feature by ID",
        "parameters": {
            "feature_id": {"type": "str", "location": "path", "description": "The feature ID"},
        },
        "required": ["feature_id"],
        "version": "v1",
    },
    "create_feature": {
        "method": "POST",
        "path": "/products/{product_id}/features",
        "description": "Create a new feature in a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
            "name": {"type": "str", "location": "body", "description": "The name of the feature"},
            "description": {"type": "Optional[str]", "location": "body", "description": "The feature description"},
            "workflow_status": {"type": "Optional[str]", "location": "body", "description": "The workflow status"},
            "assigned_to_user": {"type": "Optional[str]", "location": "body", "description": "User to assign the feature to"},
            "due_date": {"type": "Optional[str]", "location": "body", "description": "Due date in YYYY-MM-DD format"},
            "start_date": {"type": "Optional[str]", "location": "body", "description": "Start date in YYYY-MM-DD format"},
            "release": {"type": "Optional[str]", "location": "body", "description": "Release to associate the feature with"},
            "tags": {"type": "Optional[str]", "location": "body", "description": "Comma-separated list of tags"},
        },
        "required": ["product_id", "name"],
        "version": "v1",
    },
    "update_feature": {
        "method": "PUT",
        "path": "/features/{feature_id}",
        "description": "Update an existing feature",
        "parameters": {
            "feature_id": {"type": "str", "location": "path", "description": "The feature ID"},
            "name": {"type": "Optional[str]", "location": "body", "description": "The name of the feature"},
            "description": {"type": "Optional[str]", "location": "body", "description": "The feature description"},
            "workflow_status": {"type": "Optional[str]", "location": "body", "description": "The workflow status"},
            "assigned_to_user": {"type": "Optional[str]", "location": "body", "description": "User to assign the feature to"},
            "due_date": {"type": "Optional[str]", "location": "body", "description": "Due date in YYYY-MM-DD format"},
            "start_date": {"type": "Optional[str]", "location": "body", "description": "Start date in YYYY-MM-DD format"},
            "release": {"type": "Optional[str]", "location": "body", "description": "Release to associate the feature with"},
            "tags": {"type": "Optional[str]", "location": "body", "description": "Comma-separated list of tags"},
        },
        "required": ["feature_id"],
        "version": "v1",
    },

    # ================================================================================
    # IDEAS
    # ================================================================================
    "list_product_ideas": {
        "method": "GET",
        "path": "/products/{product_id}/ideas",
        "description": "List all ideas for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "get_idea": {
        "method": "GET",
        "path": "/ideas/{idea_id}",
        "description": "Get a specific idea by ID",
        "parameters": {
            "idea_id": {"type": "str", "location": "path", "description": "The idea ID"},
        },
        "required": ["idea_id"],
        "version": "v1",
    },

    # ================================================================================
    # RELEASES
    # ================================================================================
    "list_product_releases": {
        "method": "GET",
        "path": "/products/{product_id}/releases",
        "description": "List all releases for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "get_release": {
        "method": "GET",
        "path": "/releases/{release_id}",
        "description": "Get a specific release by ID",
        "parameters": {
            "release_id": {"type": "str", "location": "path", "description": "The release ID"},
        },
        "required": ["release_id"],
        "version": "v1",
    },

    # ================================================================================
    # GOALS
    # ================================================================================
    "list_product_goals": {
        "method": "GET",
        "path": "/products/{product_id}/goals",
        "description": "List all goals for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "get_goal": {
        "method": "GET",
        "path": "/goals/{goal_id}",
        "description": "Get a specific goal by ID",
        "parameters": {
            "goal_id": {"type": "str", "location": "path", "description": "The goal ID"},
        },
        "required": ["goal_id"],
        "version": "v1",
    },

    # ================================================================================
    # EPICS
    # ================================================================================
    "list_product_epics": {
        "method": "GET",
        "path": "/products/{product_id}/epics",
        "description": "List all epics for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
    "get_epic": {
        "method": "GET",
        "path": "/epics/{epic_id}",
        "description": "Get a specific epic by ID",
        "parameters": {
            "epic_id": {"type": "str", "location": "path", "description": "The epic ID"},
        },
        "required": ["epic_id"],
        "version": "v1",
    },

    # ================================================================================
    # INTEGRATIONS
    # ================================================================================
    "list_product_integrations": {
        "method": "GET",
        "path": "/products/{product_id}/integrations",
        "description": "List all integrations for a product",
        "parameters": {
            "product_id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["product_id"],
        "version": "v1",
    },
}


class AhaDataSourceGenerator:
    """Generator for comprehensive Aha! REST API datasource class.

    Generates methods for Aha! API v1 endpoints.
    The generated DataSource class accepts an AhaClient whose base URL
    is https://{subdomain}.aha.io/api/v1.
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
            inner = AhaDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = AhaDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                AhaDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = AhaDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                AhaDataSourceGenerator._modernize_type(p.strip()) for p in parts
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
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> AhaResponse:"

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
            "            AhaResponse with operation result",
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
            "            return AhaResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return AhaResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
            "version": endpoint_info.get("version", "v1"),
        })

        return "\n".join(lines)

    def generate_aha_datasource(self) -> str:
        """Generate the complete Aha! datasource class."""

        class_lines = [
            '"""',
            "Aha! REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Aha! REST API v1 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.aha.aha import AhaClient, AhaResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class AhaDataSource:",
            '    """Aha! REST API DataSource',
            "",
            "    Provides async wrapper methods for Aha! REST API operations:",
            "    - User profile and management",
            "    - Product management",
            "    - Feature CRUD operations",
            "    - Idea management",
            "    - Release management",
            "    - Goal operations",
            "    - Epic management",
            "    - Integration listing",
            "",
            "    The base URL is https://{subdomain}.aha.io/api/v1.",
            "",
            "    All methods return AhaResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: AhaClient) -> None:",
            '        """Initialize with AhaClient.',
            "",
            "        Args:",
            "            client: AhaClient instance with configured authentication and subdomain",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'AhaDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> AhaClient:",
            '        """Return the underlying AhaClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in AHA_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Aha! datasource to a file."""
        if filename is None:
            filename = "aha.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        aha_dir = script_dir.parent / "app" / "sources" / "external" / "aha"
        aha_dir.mkdir(parents=True, exist_ok=True)

        full_path = aha_dir / filename

        class_code = self.generate_aha_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Aha! data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by category
        resource_categories = {
            "User": 0,
            "Product": 0,
            "Feature": 0,
            "Idea": 0,
            "Release": 0,
            "Goal": 0,
            "Epic": 0,
            "Integration": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "user" in name:
                resource_categories["User"] += 1
            elif "product" in name and "feature" not in name and "idea" not in name and "release" not in name and "goal" not in name and "epic" not in name and "integration" not in name:
                resource_categories["Product"] += 1
            elif "feature" in name:
                resource_categories["Feature"] += 1
            elif "idea" in name:
                resource_categories["Idea"] += 1
            elif "release" in name:
                resource_categories["Release"] += 1
            elif "goal" in name:
                resource_categories["Goal"] += 1
            elif "epic" in name:
                resource_categories["Epic"] += 1
            elif "integration" in name:
                resource_categories["Integration"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for Aha! data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Aha! REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = AhaDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Aha! data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
