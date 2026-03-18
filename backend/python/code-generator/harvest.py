# ruff: noqa
"""
Harvest REST API Code Generator

Generates HarvestDataSource class covering Harvest API v2:
- Users and user management
- Time entries CRUD
- Projects and clients
- Tasks, invoices, expenses
- Company info, roles
- Project assignments

The generated DataSource accepts a HarvestClient and uses its
configured base URL. Methods are generated for all API endpoints.

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# Harvest API Endpoints
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which is https://api.harvestapp.com/v2)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
# ================================================================================

HARVEST_API_ENDPOINTS = {
    # ================================================================================
    # USERS
    # ================================================================================
    "get_current_user": {
        "method": "GET",
        "path": "/users/me",
        "description": "Get the currently authenticated user",
        "parameters": {},
        "required": [],
    },
    "list_users": {
        "method": "GET",
        "path": "/users",
        "description": "List all users",
        "parameters": {
            "is_active": {"type": "Optional[bool]", "location": "query", "description": "Filter by active status"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of records per page"},
            "updated_since": {"type": "Optional[str]", "location": "query", "description": "Only return users updated since this datetime (ISO 8601)"},
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

    # ================================================================================
    # TIME ENTRIES
    # ================================================================================
    "list_time_entries": {
        "method": "GET",
        "path": "/time_entries",
        "description": "List all time entries",
        "parameters": {
            "user_id": {"type": "Optional[str]", "location": "query", "description": "Filter by user ID"},
            "client_id_": {"type": "Optional[str]", "location": "query", "description": "Filter by client ID"},
            "project_id": {"type": "Optional[str]", "location": "query", "description": "Filter by project ID"},
            "is_billed": {"type": "Optional[bool]", "location": "query", "description": "Filter by billed status"},
            "is_running": {"type": "Optional[bool]", "location": "query", "description": "Filter by running status"},
            "updated_since": {"type": "Optional[str]", "location": "query", "description": "Only return time entries updated since this datetime (ISO 8601)"},
            "from_": {"type": "Optional[str]", "location": "query", "description": "Start date for filtering (YYYY-MM-DD)"},
            "to_": {"type": "Optional[str]", "location": "query", "description": "End date for filtering (YYYY-MM-DD)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of records per page"},
        },
        "required": [],
    },
    "get_time_entry": {
        "method": "GET",
        "path": "/time_entries/{time_entry_id}",
        "description": "Get a specific time entry by ID",
        "parameters": {
            "time_entry_id": {"type": "str", "location": "path", "description": "The time entry ID"},
        },
        "required": ["time_entry_id"],
    },
    "create_time_entry": {
        "method": "POST",
        "path": "/time_entries",
        "description": "Create a new time entry",
        "parameters": {
            "body": {"type": "dict[str, Any]", "location": "body", "description": "Time entry data (project_id, task_id, spent_date, etc.)"},
        },
        "required": ["body"],
    },
    "update_time_entry": {
        "method": "PATCH",
        "path": "/time_entries/{time_entry_id}",
        "description": "Update an existing time entry",
        "parameters": {
            "time_entry_id": {"type": "str", "location": "path", "description": "The time entry ID"},
            "body": {"type": "dict[str, Any]", "location": "body", "description": "Time entry fields to update"},
        },
        "required": ["time_entry_id", "body"],
    },
    "delete_time_entry": {
        "method": "DELETE",
        "path": "/time_entries/{time_entry_id}",
        "description": "Delete a time entry",
        "parameters": {
            "time_entry_id": {"type": "str", "location": "path", "description": "The time entry ID"},
        },
        "required": ["time_entry_id"],
    },

    # ================================================================================
    # PROJECTS
    # ================================================================================
    "list_projects": {
        "method": "GET",
        "path": "/projects",
        "description": "List all projects",
        "parameters": {
            "is_active": {"type": "Optional[bool]", "location": "query", "description": "Filter by active status"},
            "client_id_": {"type": "Optional[str]", "location": "query", "description": "Filter by client ID"},
            "updated_since": {"type": "Optional[str]", "location": "query", "description": "Only return projects updated since this datetime (ISO 8601)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of records per page"},
        },
        "required": [],
    },
    "get_project": {
        "method": "GET",
        "path": "/projects/{project_id}",
        "description": "Get a specific project by ID",
        "parameters": {
            "project_id": {"type": "str", "location": "path", "description": "The project ID"},
        },
        "required": ["project_id"],
    },

    # ================================================================================
    # CLIENTS
    # ================================================================================
    "list_clients": {
        "method": "GET",
        "path": "/clients",
        "description": "List all clients",
        "parameters": {
            "is_active": {"type": "Optional[bool]", "location": "query", "description": "Filter by active status"},
            "updated_since": {"type": "Optional[str]", "location": "query", "description": "Only return clients updated since this datetime (ISO 8601)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of records per page"},
        },
        "required": [],
    },
    "get_client_by_id": {
        "method": "GET",
        "path": "/clients/{client_id_param}",
        "description": "Get a specific client by ID",
        "parameters": {
            "client_id_param": {"type": "str", "location": "path", "description": "The client ID"},
        },
        "required": ["client_id_param"],
    },

    # ================================================================================
    # TASKS
    # ================================================================================
    "list_tasks": {
        "method": "GET",
        "path": "/tasks",
        "description": "List all tasks",
        "parameters": {
            "is_active": {"type": "Optional[bool]", "location": "query", "description": "Filter by active status"},
            "updated_since": {"type": "Optional[str]", "location": "query", "description": "Only return tasks updated since this datetime (ISO 8601)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of records per page"},
        },
        "required": [],
    },
    "get_task": {
        "method": "GET",
        "path": "/tasks/{task_id}",
        "description": "Get a specific task by ID",
        "parameters": {
            "task_id": {"type": "str", "location": "path", "description": "The task ID"},
        },
        "required": ["task_id"],
    },

    # ================================================================================
    # INVOICES
    # ================================================================================
    "list_invoices": {
        "method": "GET",
        "path": "/invoices",
        "description": "List all invoices",
        "parameters": {
            "client_id_": {"type": "Optional[str]", "location": "query", "description": "Filter by client ID"},
            "project_id": {"type": "Optional[str]", "location": "query", "description": "Filter by project ID"},
            "updated_since": {"type": "Optional[str]", "location": "query", "description": "Only return invoices updated since this datetime (ISO 8601)"},
            "from_": {"type": "Optional[str]", "location": "query", "description": "Start date for filtering (YYYY-MM-DD)"},
            "to_": {"type": "Optional[str]", "location": "query", "description": "End date for filtering (YYYY-MM-DD)"},
            "state": {"type": "Optional[str]", "location": "query", "description": "Filter by invoice state (draft, open, paid, closed)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of records per page"},
        },
        "required": [],
    },
    "get_invoice": {
        "method": "GET",
        "path": "/invoices/{invoice_id}",
        "description": "Get a specific invoice by ID",
        "parameters": {
            "invoice_id": {"type": "str", "location": "path", "description": "The invoice ID"},
        },
        "required": ["invoice_id"],
    },

    # ================================================================================
    # EXPENSES
    # ================================================================================
    "list_expenses": {
        "method": "GET",
        "path": "/expenses",
        "description": "List all expenses",
        "parameters": {
            "user_id": {"type": "Optional[str]", "location": "query", "description": "Filter by user ID"},
            "client_id_": {"type": "Optional[str]", "location": "query", "description": "Filter by client ID"},
            "project_id": {"type": "Optional[str]", "location": "query", "description": "Filter by project ID"},
            "is_billed": {"type": "Optional[bool]", "location": "query", "description": "Filter by billed status"},
            "updated_since": {"type": "Optional[str]", "location": "query", "description": "Only return expenses updated since this datetime (ISO 8601)"},
            "from_": {"type": "Optional[str]", "location": "query", "description": "Start date for filtering (YYYY-MM-DD)"},
            "to_": {"type": "Optional[str]", "location": "query", "description": "End date for filtering (YYYY-MM-DD)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of records per page"},
        },
        "required": [],
    },
    "get_expense": {
        "method": "GET",
        "path": "/expenses/{expense_id}",
        "description": "Get a specific expense by ID",
        "parameters": {
            "expense_id": {"type": "str", "location": "path", "description": "The expense ID"},
        },
        "required": ["expense_id"],
    },

    # ================================================================================
    # COMPANY
    # ================================================================================
    "get_company": {
        "method": "GET",
        "path": "/company",
        "description": "Get the company information for the authenticated user's account",
        "parameters": {},
        "required": [],
    },

    # ================================================================================
    # ROLES
    # ================================================================================
    "list_roles": {
        "method": "GET",
        "path": "/roles",
        "description": "List all roles",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of records per page"},
        },
        "required": [],
    },

    # ================================================================================
    # PROJECT ASSIGNMENTS
    # ================================================================================
    "list_project_assignments": {
        "method": "GET",
        "path": "/project_assignments",
        "description": "List project assignments for the currently authenticated user",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of records per page"},
            "updated_since": {"type": "Optional[str]", "location": "query", "description": "Only return assignments updated since this datetime (ISO 8601)"},
        },
        "required": [],
    },
    "list_user_project_assignments": {
        "method": "GET",
        "path": "/users/{user_id}/project_assignments",
        "description": "List project assignments for a specific user",
        "parameters": {
            "user_id": {"type": "str", "location": "path", "description": "The user ID"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of records per page"},
            "updated_since": {"type": "Optional[str]", "location": "query", "description": "Only return assignments updated since this datetime (ISO 8601)"},
        },
        "required": ["user_id"],
    },
}


class HarvestDataSourceGenerator:
    """Generator for comprehensive Harvest REST API datasource class.

    Generates methods for Harvest API v2 endpoints.
    The generated DataSource class accepts a HarvestClient whose
    base URL is configured at initialization.
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
                # Map Python parameter names with trailing underscores to API names
                api_name = param_name
                if api_name == "client_id_":
                    api_name = "client_id"
                elif api_name == "from_":
                    api_name = "from"
                elif api_name == "to_":
                    api_name = "to"

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

        # For Harvest, we pass the body dict directly
        if "body" in body_params:
            return []  # body is passed directly in the request

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
            inner = HarvestDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = HarvestDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                HarvestDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = HarvestDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                HarvestDataSourceGenerator._modernize_type(p.strip()) for p in parts
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
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> HarvestResponse:"

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
            "            HarvestResponse with operation result",
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
        has_body_param = "body" in endpoint_info["parameters"] and endpoint_info["parameters"]["body"]["location"] == "body"
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
        if has_body_param:
            lines.append("                body=body,")
        elif body_lines:
            lines.append("                body=body,")
        lines.append("            )")
        lines.extend([
            "            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]",
            "            response_data = response.json() if response.text() else None",
            "            return HarvestResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return HarvestResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_harvest_datasource(self) -> str:
        """Generate the complete Harvest datasource class."""

        class_lines = [
            '"""',
            "Harvest REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Harvest REST API v2 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.harvest.harvest import HarvestClient, HarvestResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class HarvestDataSource:",
            '    """Harvest REST API DataSource',
            "",
            "    Provides async wrapper methods for Harvest REST API operations:",
            "    - Users and user management",
            "    - Time entries CRUD",
            "    - Projects and clients",
            "    - Tasks, invoices, expenses",
            "    - Company info, roles",
            "    - Project assignments",
            "",
            "    All requests require a Harvest-Account-Id header, which is set",
            "    by the HarvestClient during initialization.",
            "",
            "    All methods return HarvestResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: HarvestClient) -> None:",
            '        """Initialize with HarvestClient.',
            "",
            "        Args:",
            "            client: HarvestClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'HarvestDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> HarvestClient:",
            '        """Return the underlying HarvestClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in HARVEST_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Harvest datasource to a file."""
        if filename is None:
            filename = "harvest.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        harvest_dir = script_dir.parent / "app" / "sources" / "external" / "harvest"
        harvest_dir.mkdir(parents=True, exist_ok=True)

        full_path = harvest_dir / filename

        class_code = self.generate_harvest_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Harvest data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by resource category
        resource_categories = {
            "Users": 0,
            "Time Entries": 0,
            "Projects": 0,
            "Clients": 0,
            "Tasks": 0,
            "Invoices": 0,
            "Expenses": 0,
            "Company": 0,
            "Roles": 0,
            "Project Assignments": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "user" in name and "assignment" not in name:
                resource_categories["Users"] += 1
            elif "time_entr" in name:
                resource_categories["Time Entries"] += 1
            elif "project" in name and "assignment" not in name:
                resource_categories["Projects"] += 1
            elif "client" in name:
                resource_categories["Clients"] += 1
            elif "task" in name:
                resource_categories["Tasks"] += 1
            elif "invoice" in name:
                resource_categories["Invoices"] += 1
            elif "expense" in name:
                resource_categories["Expenses"] += 1
            elif "company" in name:
                resource_categories["Company"] += 1
            elif "role" in name:
                resource_categories["Roles"] += 1
            elif "assignment" in name:
                resource_categories["Project Assignments"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for Harvest data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Harvest REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = HarvestDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Harvest data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
