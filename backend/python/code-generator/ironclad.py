# ruff: noqa
"""
Ironclad REST API Code Generator

Generates IroncladDataSource class covering Ironclad API v1:
- Workflow operations (list, get, launch, update)
- Workflow approvals
- Records management
- Templates
- Webhooks
- Users and Groups

The generated DataSource accepts an IroncladClient and uses the client's
configured base URL. All methods have explicit parameter signatures with
no **kwargs usage.

API Reference: https://developer.ironcladapp.com/reference
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# Ironclad API Endpoints
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
# ================================================================================

IRONCLAD_API_ENDPOINTS = {
    # ================================================================================
    # WORKFLOWS
    # ================================================================================
    "list_workflows": {
        "method": "GET",
        "path": "/workflows",
        "description": "List workflows with optional filters",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "page_size": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
            "status": {"type": "Optional[str]", "location": "query", "description": "Filter by workflow status"},
            "template_id": {"type": "Optional[str]", "location": "query", "description": "Filter by template ID"},
            "created_after": {"type": "Optional[str]", "location": "query", "description": "Filter workflows created after this ISO 8601 date"},
            "created_before": {"type": "Optional[str]", "location": "query", "description": "Filter workflows created before this ISO 8601 date"},
        },
        "required": [],
    },
    "get_workflow": {
        "method": "GET",
        "path": "/workflows/{workflow_id}",
        "description": "Get a specific workflow by ID",
        "parameters": {
            "workflow_id": {"type": "str", "location": "path", "description": "The workflow ID"},
        },
        "required": ["workflow_id"],
    },
    "launch_workflow": {
        "method": "POST",
        "path": "/workflows",
        "description": "Launch a new workflow",
        "parameters": {
            "template_id": {"type": "str", "location": "body", "description": "The template ID to launch the workflow from"},
            "attributes": {"type": "Optional[Dict[str, Any]]", "location": "body", "description": "Workflow attribute values"},
            "creator": {"type": "Optional[Dict[str, Any]]", "location": "body", "description": "Creator information"},
        },
        "required": ["template_id"],
    },
    "update_workflow": {
        "method": "PATCH",
        "path": "/workflows/{workflow_id}",
        "description": "Update a workflow",
        "parameters": {
            "workflow_id": {"type": "str", "location": "path", "description": "The workflow ID"},
            "attributes": {"type": "Optional[Dict[str, Any]]", "location": "body", "description": "Workflow attribute values to update"},
        },
        "required": ["workflow_id"],
    },

    # ================================================================================
    # WORKFLOW APPROVALS
    # ================================================================================
    "list_workflow_approvals": {
        "method": "GET",
        "path": "/workflows/{workflow_id}/approvals",
        "description": "List approvals for a workflow",
        "parameters": {
            "workflow_id": {"type": "str", "location": "path", "description": "The workflow ID"},
        },
        "required": ["workflow_id"],
    },
    "create_workflow_approval": {
        "method": "POST",
        "path": "/workflows/{workflow_id}/approvals",
        "description": "Create an approval for a workflow",
        "parameters": {
            "workflow_id": {"type": "str", "location": "path", "description": "The workflow ID"},
            "role_id": {"type": "Optional[str]", "location": "body", "description": "The role ID for the approval"},
            "user_id": {"type": "Optional[str]", "location": "body", "description": "The user ID for the approval"},
            "status": {"type": "Optional[str]", "location": "body", "description": "Approval status"},
        },
        "required": ["workflow_id"],
    },

    # ================================================================================
    # RECORDS
    # ================================================================================
    "list_records": {
        "method": "GET",
        "path": "/records",
        "description": "List records with optional filters",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "page_size": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
            "template_id": {"type": "Optional[str]", "location": "query", "description": "Filter by template ID"},
            "filter": {"type": "Optional[str]", "location": "query", "description": "Filter expression"},
        },
        "required": [],
    },
    "get_record": {
        "method": "GET",
        "path": "/records/{record_id}",
        "description": "Get a specific record by ID",
        "parameters": {
            "record_id": {"type": "str", "location": "path", "description": "The record ID"},
        },
        "required": ["record_id"],
    },
    "update_record": {
        "method": "PATCH",
        "path": "/records/{record_id}",
        "description": "Update a record",
        "parameters": {
            "record_id": {"type": "str", "location": "path", "description": "The record ID"},
            "attributes": {"type": "Optional[Dict[str, Any]]", "location": "body", "description": "Record attribute values to update"},
        },
        "required": ["record_id"],
    },

    # ================================================================================
    # TEMPLATES
    # ================================================================================
    "list_templates": {
        "method": "GET",
        "path": "/templates",
        "description": "List all templates",
        "parameters": {},
        "required": [],
    },
    "get_template": {
        "method": "GET",
        "path": "/templates/{template_id}",
        "description": "Get a specific template by ID",
        "parameters": {
            "template_id": {"type": "str", "location": "path", "description": "The template ID"},
        },
        "required": ["template_id"],
    },

    # ================================================================================
    # WEBHOOKS
    # ================================================================================
    "list_webhooks": {
        "method": "GET",
        "path": "/webhooks",
        "description": "List all webhooks",
        "parameters": {},
        "required": [],
    },
    "create_webhook": {
        "method": "POST",
        "path": "/webhooks",
        "description": "Create a webhook",
        "parameters": {
            "target_url": {"type": "str", "location": "body", "description": "The URL to send webhook events to"},
            "events": {"type": "Optional[List[str]]", "location": "body", "description": "List of event types to subscribe to"},
        },
        "required": ["target_url"],
    },
    "delete_webhook": {
        "method": "DELETE",
        "path": "/webhooks/{webhook_id}",
        "description": "Delete a webhook",
        "parameters": {
            "webhook_id": {"type": "str", "location": "path", "description": "The webhook ID"},
        },
        "required": ["webhook_id"],
    },

    # ================================================================================
    # USERS
    # ================================================================================
    "list_users": {
        "method": "GET",
        "path": "/users",
        "description": "List users with optional pagination",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "page_size": {"type": "Optional[int]", "location": "query", "description": "Number of results per page"},
        },
        "required": [],
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
}


# ================================================================================
# Code Generator
# ================================================================================


class IroncladDataSourceGenerator:
    """Generator for comprehensive Ironclad REST API datasource class.

    Generates methods for Ironclad API v1 endpoints.
    The generated DataSource class accepts an IroncladClient whose base URL
    is pre-configured.
    """

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    # Python builtins that must not be used as parameter names
    _PYTHON_BUILTINS = frozenset({
        "filter", "format", "type", "id", "input", "hash", "help", "list",
        "map", "max", "min", "next", "object", "open", "print", "range",
        "set", "slice", "sorted", "sum", "super", "tuple", "vars", "zip",
    })

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace("-", "_").replace(".", "_").replace("/", "_")
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == "_"):
            sanitized = f"param_{sanitized}"
        if sanitized in self._PYTHON_BUILTINS:
            sanitized = f"{sanitized}_value"
        return sanitized

    def _build_query_params(self, endpoint_info: Dict) -> List[str]:
        """Build query parameter handling code."""
        lines = ["        query_params: dict[str, Any] = {}"]
        required = endpoint_info.get("required", [])

        for param_name, param_info in endpoint_info["parameters"].items():
            if param_info["location"] == "query":
                sanitized_name = self._sanitize_parameter_name(param_name)
                is_required = param_name in required

                if is_required:
                    # Required query params: assign directly, no None check
                    lines.append(
                        f"        query_params['{param_name}'] = {sanitized_name}"
                    )
                elif "Optional[bool]" in param_info["type"]:
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
            inner = IroncladDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = IroncladDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                IroncladDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = IroncladDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                IroncladDataSourceGenerator._modernize_type(p.strip()) for p in parts
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
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> IroncladResponse:"

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
            "            IroncladResponse with operation result",
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
            "            return IroncladResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return IroncladResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_ironclad_datasource(self) -> str:
        """Generate the complete Ironclad datasource class."""

        class_lines = [
            '"""',
            "Ironclad REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Ironclad REST API v1 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.ironclad.ironclad import IroncladClient, IroncladResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class IroncladDataSource:",
            '    """Ironclad REST API DataSource',
            "",
            "    Provides async wrapper methods for Ironclad REST API operations:",
            "    - Workflow management (list, get, launch, update)",
            "    - Workflow approvals",
            "    - Records management",
            "    - Templates",
            "    - Webhooks",
            "    - Users and Groups",
            "",
            "    The base URL is determined by the IroncladClient's configuration.",
            "",
            "    All methods return IroncladResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: IroncladClient) -> None:",
            '        """Initialize with IroncladClient.',
            "",
            "        Args:",
            "            client: IroncladClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'IroncladDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> IroncladClient:",
            '        """Return the underlying IroncladClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in IRONCLAD_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Ironclad datasource to a file."""
        if filename is None:
            filename = "ironclad.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        ironclad_dir = script_dir.parent / "app" / "sources" / "external" / "ironclad"
        ironclad_dir.mkdir(parents=True, exist_ok=True)

        full_path = ironclad_dir / filename

        class_code = self.generate_ironclad_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Ironclad data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by category
        resource_categories = {
            "Workflow": 0,
            "Approval": 0,
            "Record": 0,
            "Template": 0,
            "Webhook": 0,
            "User": 0,
            "Group": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "approval" in name:
                resource_categories["Approval"] += 1
            elif "workflow" in name:
                resource_categories["Workflow"] += 1
            elif "record" in name:
                resource_categories["Record"] += 1
            elif "template" in name:
                resource_categories["Template"] += 1
            elif "webhook" in name:
                resource_categories["Webhook"] += 1
            elif "user" in name:
                resource_categories["User"] += 1
            elif "group" in name:
                resource_categories["Group"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for Ironclad data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Ironclad REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = IroncladDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Ironclad data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
