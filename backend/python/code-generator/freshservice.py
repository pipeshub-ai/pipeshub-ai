# ruff: noqa
"""
Freshservice REST API Code Generator

Generates FreshserviceDataSource class covering Freshservice API v2:
- Ticket operations
- Ticket conversations
- Requesters and agents
- Assets
- Problems, changes, releases
- Departments, groups
- Service catalog items

The generated DataSource accepts a FreshserviceClient and uses the client's
base URL to construct API requests.

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# Freshservice API Endpoints
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which already includes /api/v2)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
# ================================================================================

FRESHSERVICE_API_ENDPOINTS = {
    # ================================================================================
    # TICKETS
    # ================================================================================
    "list_tickets": {
        "method": "GET",
        "path": "/tickets",
        "description": "List all tickets with optional filters",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of tickets per page (max 100)"},
            "filter": {"type": "Optional[str]", "location": "query", "description": "Predefined filter name"},
            "order_by": {"type": "Optional[str]", "location": "query", "description": "Field to order by (e.g., created_at, updated_at)"},
            "order_type": {"type": "Optional[str]", "location": "query", "description": "Order direction: asc or desc"},
            "updated_since": {"type": "Optional[str]", "location": "query", "description": "Filter tickets updated since this timestamp (ISO format)"},
            "requester_id": {"type": "Optional[int]", "location": "query", "description": "Filter by requester ID"},
        },
        "required": [],
    },
    "get_ticket": {
        "method": "GET",
        "path": "/tickets/{id}",
        "description": "Get a specific ticket by ID",
        "parameters": {
            "id": {"type": "int", "location": "path", "description": "Ticket ID"},
        },
        "required": ["id"],
    },
    "create_ticket": {
        "method": "POST",
        "path": "/tickets",
        "description": "Create a new ticket",
        "parameters": {
            "subject": {"type": "str", "location": "body", "description": "Subject of the ticket"},
            "description": {"type": "Optional[str]", "location": "body", "description": "HTML content of the ticket"},
            "email": {"type": "Optional[str]", "location": "body", "description": "Email of the requester"},
            "requester_id": {"type": "Optional[int]", "location": "body", "description": "User ID of the requester"},
            "phone": {"type": "Optional[str]", "location": "body", "description": "Phone number of the requester"},
            "priority": {"type": "Optional[int]", "location": "body", "description": "Priority: 1=Low, 2=Medium, 3=High, 4=Urgent"},
            "status": {"type": "Optional[int]", "location": "body", "description": "Status: 2=Open, 3=Pending, 4=Resolved, 5=Closed"},
            "source": {"type": "Optional[int]", "location": "body", "description": "Source of the ticket"},
            "type": {"type": "Optional[str]", "location": "body", "description": "Type of the ticket"},
            "tags": {"type": "Optional[List[str]]", "location": "body", "description": "Tags for the ticket"},
            "cc_emails": {"type": "Optional[List[str]]", "location": "body", "description": "CC email addresses"},
            "custom_fields": {"type": "Optional[Dict[str, Any]]", "location": "body", "description": "Custom field values"},
            "department_id": {"type": "Optional[int]", "location": "body", "description": "Department ID"},
            "group_id": {"type": "Optional[int]", "location": "body", "description": "Group ID"},
            "category": {"type": "Optional[str]", "location": "body", "description": "Category of the ticket"},
            "sub_category": {"type": "Optional[str]", "location": "body", "description": "Sub-category of the ticket"},
            "item_category": {"type": "Optional[str]", "location": "body", "description": "Item category"},
            "responder_id": {"type": "Optional[int]", "location": "body", "description": "Agent ID to assign"},
            "due_by": {"type": "Optional[str]", "location": "body", "description": "Due date (ISO format)"},
            "fr_due_by": {"type": "Optional[str]", "location": "body", "description": "First response due date (ISO format)"},
            "urgency": {"type": "Optional[int]", "location": "body", "description": "Urgency of the ticket"},
            "impact": {"type": "Optional[int]", "location": "body", "description": "Impact of the ticket"},
        },
        "required": ["subject"],
    },
    "update_ticket": {
        "method": "PUT",
        "path": "/tickets/{id}",
        "description": "Update an existing ticket",
        "parameters": {
            "id": {"type": "int", "location": "path", "description": "Ticket ID"},
            "subject": {"type": "Optional[str]", "location": "body", "description": "Subject of the ticket"},
            "description": {"type": "Optional[str]", "location": "body", "description": "HTML content of the ticket"},
            "priority": {"type": "Optional[int]", "location": "body", "description": "Priority: 1=Low, 2=Medium, 3=High, 4=Urgent"},
            "status": {"type": "Optional[int]", "location": "body", "description": "Status: 2=Open, 3=Pending, 4=Resolved, 5=Closed"},
            "type": {"type": "Optional[str]", "location": "body", "description": "Type of the ticket"},
            "tags": {"type": "Optional[List[str]]", "location": "body", "description": "Tags for the ticket"},
            "custom_fields": {"type": "Optional[Dict[str, Any]]", "location": "body", "description": "Custom field values"},
            "department_id": {"type": "Optional[int]", "location": "body", "description": "Department ID"},
            "group_id": {"type": "Optional[int]", "location": "body", "description": "Group ID"},
            "category": {"type": "Optional[str]", "location": "body", "description": "Category"},
            "sub_category": {"type": "Optional[str]", "location": "body", "description": "Sub-category"},
            "item_category": {"type": "Optional[str]", "location": "body", "description": "Item category"},
            "responder_id": {"type": "Optional[int]", "location": "body", "description": "Agent ID to assign"},
            "urgency": {"type": "Optional[int]", "location": "body", "description": "Urgency"},
            "impact": {"type": "Optional[int]", "location": "body", "description": "Impact"},
        },
        "required": ["id"],
    },
    "delete_ticket": {
        "method": "DELETE",
        "path": "/tickets/{id}",
        "description": "Delete a ticket",
        "parameters": {
            "id": {"type": "int", "location": "path", "description": "Ticket ID"},
        },
        "required": ["id"],
    },

    # ================================================================================
    # TICKET CONVERSATIONS
    # ================================================================================
    "list_ticket_conversations": {
        "method": "GET",
        "path": "/tickets/{id}/conversations",
        "description": "List all conversations of a ticket",
        "parameters": {
            "id": {"type": "int", "location": "path", "description": "Ticket ID"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Items per page"},
        },
        "required": ["id"],
    },

    # ================================================================================
    # REQUESTERS
    # ================================================================================
    "list_requesters": {
        "method": "GET",
        "path": "/requesters",
        "description": "List all requesters",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Items per page"},
            "email": {"type": "Optional[str]", "location": "query", "description": "Filter by email"},
            "query": {"type": "Optional[str]", "location": "query", "description": "Search query string"},
        },
        "required": [],
    },
    "get_requester": {
        "method": "GET",
        "path": "/requesters/{id}",
        "description": "Get a specific requester by ID",
        "parameters": {
            "id": {"type": "int", "location": "path", "description": "Requester ID"},
        },
        "required": ["id"],
    },

    # ================================================================================
    # AGENTS
    # ================================================================================
    "list_agents": {
        "method": "GET",
        "path": "/agents",
        "description": "List all agents",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Items per page"},
            "email": {"type": "Optional[str]", "location": "query", "description": "Filter by email"},
            "state": {"type": "Optional[str]", "location": "query", "description": "Filter by agent state (fulltime, occasional)"},
        },
        "required": [],
    },
    "get_agent": {
        "method": "GET",
        "path": "/agents/{id}",
        "description": "Get a specific agent by ID",
        "parameters": {
            "id": {"type": "int", "location": "path", "description": "Agent ID"},
        },
        "required": ["id"],
    },

    # ================================================================================
    # ASSETS
    # ================================================================================
    "list_assets": {
        "method": "GET",
        "path": "/assets",
        "description": "List all assets",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Items per page"},
            "filter": {"type": "Optional[str]", "location": "query", "description": "Filter name"},
        },
        "required": [],
    },
    "get_asset": {
        "method": "GET",
        "path": "/assets/{display_id}",
        "description": "Get a specific asset by display ID",
        "parameters": {
            "display_id": {"type": "int", "location": "path", "description": "Asset display ID"},
        },
        "required": ["display_id"],
    },

    # ================================================================================
    # PROBLEMS
    # ================================================================================
    "list_problems": {
        "method": "GET",
        "path": "/problems",
        "description": "List all problems",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Items per page"},
        },
        "required": [],
    },

    # ================================================================================
    # CHANGES
    # ================================================================================
    "list_changes": {
        "method": "GET",
        "path": "/changes",
        "description": "List all changes",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Items per page"},
        },
        "required": [],
    },

    # ================================================================================
    # RELEASES
    # ================================================================================
    "list_releases": {
        "method": "GET",
        "path": "/releases",
        "description": "List all releases",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Items per page"},
        },
        "required": [],
    },

    # ================================================================================
    # DEPARTMENTS
    # ================================================================================
    "list_departments": {
        "method": "GET",
        "path": "/departments",
        "description": "List all departments",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Items per page"},
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
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Items per page"},
        },
        "required": [],
    },

    # ================================================================================
    # SERVICE CATALOG
    # ================================================================================
    "list_service_catalog_items": {
        "method": "GET",
        "path": "/service_catalog/items",
        "description": "List all service catalog items",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number"},
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Items per page"},
        },
        "required": [],
    },
}


class FreshserviceDataSourceGenerator:
    """Generator for Freshservice REST API datasource class."""

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace("-", "_").replace(".", "_").replace("/", "_")
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == "_"):
            sanitized = f"param_{sanitized}"
        # Handle Python keywords
        if sanitized == "type":
            sanitized = "type_"
        elif sanitized == "filter":
            sanitized = "filter_"
        elif sanitized == "query":
            sanitized = "query_"
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

        lines = ["        request_body: dict[str, Any] = {}"]

        for param_name, param_info in body_params.items():
            sanitized_name = self._sanitize_parameter_name(param_name)

            if param_name in endpoint_info["required"]:
                lines.append(f"        request_body['{param_name}'] = {sanitized_name}")
            else:
                lines.extend([
                    f"        if {sanitized_name} is not None:",
                    f"            request_body['{param_name}'] = {sanitized_name}",
                ])

        return lines

    @staticmethod
    def _modernize_type(type_str: str) -> str:
        """Convert typing-style annotations to modern Python 3.10+ syntax."""
        if type_str.startswith("Optional[") and type_str.endswith("]"):
            inner = type_str[len("Optional["):-1]
            inner = FreshserviceDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = FreshserviceDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                FreshserviceDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = FreshserviceDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                FreshserviceDataSourceGenerator._modernize_type(p.strip()) for p in parts
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

        # Required params
        for param_name in endpoint_info["required"]:
            if param_name in endpoint_info["parameters"]:
                param_info = endpoint_info["parameters"][param_name]
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                params.append(f"{sanitized_name}: {modern_type}")

        # Optional parameters
        for param_name, param_info in endpoint_info["parameters"].items():
            if param_name not in endpoint_info["required"]:
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                if "| None" not in modern_type:
                    modern_type = f"{modern_type} | None"
                params.append(f"{sanitized_name}: {modern_type} = None")

        signature_params = ",\n        ".join(params)
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> FreshserviceResponse:"

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
            "            FreshserviceResponse with operation result",
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
            lines.append("                body=request_body,")
        lines.append("            )")
        lines.extend([
            "            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]",
            "            response_data = response.json() if response.text() else None",
            "            return FreshserviceResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_freshservice_datasource(self) -> str:
        """Generate the complete Freshservice datasource class."""

        class_lines = [
            "# ruff: noqa: A002, FBT001",
            '"""',
            "Freshservice REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Freshservice REST API v2 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.freshservice.freshservice import FreshserviceClient, FreshserviceResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class FreshserviceDataSource:",
            '    """Freshservice REST API DataSource',
            "",
            "    Provides async wrapper methods for Freshservice REST API operations:",
            "    - Ticket CRUD and management",
            "    - Ticket conversations",
            "    - Requesters and agents",
            "    - Assets",
            "    - Problems, changes, releases",
            "    - Departments, groups",
            "    - Service catalog items",
            "",
            "    All methods return FreshserviceResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: FreshserviceClient) -> None:",
            '        """Initialize with FreshserviceClient.',
            "",
            "        Args:",
            "            client: FreshserviceClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'FreshserviceDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> FreshserviceClient:",
            '        """Return the underlying FreshserviceClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in FRESHSERVICE_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Freshservice datasource to a file."""
        if filename is None:
            filename = "freshservice.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        target_dir = script_dir.parent / "app" / "sources" / "external" / "freshservice"
        target_dir.mkdir(parents=True, exist_ok=True)

        full_path = target_dir / filename

        class_code = self.generate_freshservice_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Freshservice data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary
        method_counts: Dict[str, int] = {}
        for method in self.generated_methods:
            http_method = method["method"]
            method_counts[http_method] = method_counts.get(http_method, 0) + 1

        print(f"\nMethods by HTTP verb:")
        for verb, count in sorted(method_counts.items()):
            print(f"  - {verb}: {count}")


def main():
    """Main function for Freshservice data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Freshservice REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = FreshserviceDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Freshservice data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
