# ruff: noqa
"""
Pipedrive REST API Code Generator

Generates PipedriveDataSource class covering Pipedrive API v1:
- Users management
- Deals CRUD and management
- Persons (contacts) CRUD
- Organizations CRUD
- Activities management
- Pipelines and Stages
- Products management
- Notes CRUD
- Leads management
- Custom fields (Deal, Person, Organization)

The generated DataSource accepts a PipedriveClient and uses the client's
configured base URL. Methods are generated for all API v1 endpoints.

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# Pipedrive API Endpoints - organized by resource
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which already includes /v1)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
# ================================================================================

PIPEDRIVE_API_ENDPOINTS = {
    # ================================================================================
    # USERS
    # ================================================================================
    "list_users": {
        "method": "GET",
        "path": "/users",
        "description": "List all users in the company",
        "parameters": {},
        "required": [],
    },
    "get_user": {
        "method": "GET",
        "path": "/users/{id}",
        "description": "Get details of a specific user",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The user ID"},
        },
        "required": ["id"],
    },
    "get_current_user": {
        "method": "GET",
        "path": "/users/me",
        "description": "Get the current authenticated user",
        "parameters": {},
        "required": [],
    },

    # ================================================================================
    # DEALS
    # ================================================================================
    "list_deals": {
        "method": "GET",
        "path": "/deals",
        "description": "List all deals",
        "parameters": {
            "status": {"type": "Optional[str]", "location": "query", "description": "Filter by deal status (open, won, lost, deleted, all_not_deleted)"},
            "start": {"type": "Optional[int]", "location": "query", "description": "Pagination start (default 0)"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Items shown per page (default 100)"},
            "sort": {"type": "Optional[str]", "location": "query", "description": "Field name and sorting mode (e.g. 'title ASC')"},
            "filter_id": {"type": "Optional[int]", "location": "query", "description": "ID of the filter to use"},
        },
        "required": [],
    },
    "get_deal": {
        "method": "GET",
        "path": "/deals/{id}",
        "description": "Get details of a specific deal",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The deal ID"},
        },
        "required": ["id"],
    },
    "create_deal": {
        "method": "POST",
        "path": "/deals",
        "description": "Create a new deal",
        "parameters": {
            "title": {"type": "str", "location": "body", "description": "The title of the deal"},
            "value": {"type": "Optional[str]", "location": "body", "description": "Value of the deal"},
            "currency": {"type": "Optional[str]", "location": "body", "description": "Currency of the deal (3-letter code)"},
            "user_id": {"type": "Optional[int]", "location": "body", "description": "ID of the user who owns the deal"},
            "person_id": {"type": "Optional[int]", "location": "body", "description": "ID of a person linked to the deal"},
            "org_id": {"type": "Optional[int]", "location": "body", "description": "ID of an organization linked to the deal"},
            "pipeline_id": {"type": "Optional[int]", "location": "body", "description": "ID of the pipeline this deal will be placed in"},
            "stage_id": {"type": "Optional[int]", "location": "body", "description": "ID of the stage this deal will be placed in"},
            "status": {"type": "Optional[str]", "location": "body", "description": "Status of the deal (open, won, lost, deleted)"},
            "expected_close_date": {"type": "Optional[str]", "location": "body", "description": "Expected close date (YYYY-MM-DD)"},
            "probability": {"type": "Optional[int]", "location": "body", "description": "Deal success probability percentage"},
        },
        "required": ["title"],
    },
    "update_deal": {
        "method": "PUT",
        "path": "/deals/{id}",
        "description": "Update a deal",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The deal ID"},
            "title": {"type": "Optional[str]", "location": "body", "description": "The title of the deal"},
            "value": {"type": "Optional[str]", "location": "body", "description": "Value of the deal"},
            "currency": {"type": "Optional[str]", "location": "body", "description": "Currency of the deal (3-letter code)"},
            "user_id": {"type": "Optional[int]", "location": "body", "description": "ID of the user who owns the deal"},
            "person_id": {"type": "Optional[int]", "location": "body", "description": "ID of a person linked to the deal"},
            "org_id": {"type": "Optional[int]", "location": "body", "description": "ID of an organization linked to the deal"},
            "pipeline_id": {"type": "Optional[int]", "location": "body", "description": "ID of the pipeline"},
            "stage_id": {"type": "Optional[int]", "location": "body", "description": "ID of the stage"},
            "status": {"type": "Optional[str]", "location": "body", "description": "Status of the deal (open, won, lost, deleted)"},
            "expected_close_date": {"type": "Optional[str]", "location": "body", "description": "Expected close date (YYYY-MM-DD)"},
            "probability": {"type": "Optional[int]", "location": "body", "description": "Deal success probability percentage"},
        },
        "required": ["id"],
    },
    "delete_deal": {
        "method": "DELETE",
        "path": "/deals/{id}",
        "description": "Delete a deal",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The deal ID"},
        },
        "required": ["id"],
    },

    # ================================================================================
    # PERSONS (CONTACTS)
    # ================================================================================
    "list_persons": {
        "method": "GET",
        "path": "/persons",
        "description": "List all persons (contacts)",
        "parameters": {
            "start": {"type": "Optional[int]", "location": "query", "description": "Pagination start (default 0)"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Items shown per page (default 100)"},
            "sort": {"type": "Optional[str]", "location": "query", "description": "Field name and sorting mode"},
            "filter_id": {"type": "Optional[int]", "location": "query", "description": "ID of the filter to use"},
        },
        "required": [],
    },
    "get_person": {
        "method": "GET",
        "path": "/persons/{id}",
        "description": "Get details of a specific person",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The person ID"},
        },
        "required": ["id"],
    },
    "create_person": {
        "method": "POST",
        "path": "/persons",
        "description": "Create a new person (contact)",
        "parameters": {
            "name": {"type": "str", "location": "body", "description": "The name of the person"},
            "owner_id": {"type": "Optional[int]", "location": "body", "description": "ID of the user who owns the person"},
            "org_id": {"type": "Optional[int]", "location": "body", "description": "ID of the organization this person belongs to"},
            "email": {"type": "Optional[str]", "location": "body", "description": "Email address of the person"},
            "phone": {"type": "Optional[str]", "location": "body", "description": "Phone number of the person"},
        },
        "required": ["name"],
    },
    "update_person": {
        "method": "PUT",
        "path": "/persons/{id}",
        "description": "Update a person",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The person ID"},
            "name": {"type": "Optional[str]", "location": "body", "description": "The name of the person"},
            "owner_id": {"type": "Optional[int]", "location": "body", "description": "ID of the user who owns the person"},
            "org_id": {"type": "Optional[int]", "location": "body", "description": "ID of the organization"},
            "email": {"type": "Optional[str]", "location": "body", "description": "Email address of the person"},
            "phone": {"type": "Optional[str]", "location": "body", "description": "Phone number of the person"},
        },
        "required": ["id"],
    },

    # ================================================================================
    # ORGANIZATIONS
    # ================================================================================
    "list_organizations": {
        "method": "GET",
        "path": "/organizations",
        "description": "List all organizations",
        "parameters": {
            "start": {"type": "Optional[int]", "location": "query", "description": "Pagination start (default 0)"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Items shown per page (default 100)"},
            "sort": {"type": "Optional[str]", "location": "query", "description": "Field name and sorting mode"},
            "filter_id": {"type": "Optional[int]", "location": "query", "description": "ID of the filter to use"},
        },
        "required": [],
    },
    "get_organization": {
        "method": "GET",
        "path": "/organizations/{id}",
        "description": "Get details of a specific organization",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The organization ID"},
        },
        "required": ["id"],
    },
    "create_organization": {
        "method": "POST",
        "path": "/organizations",
        "description": "Create a new organization",
        "parameters": {
            "name": {"type": "str", "location": "body", "description": "The name of the organization"},
            "owner_id": {"type": "Optional[int]", "location": "body", "description": "ID of the user who owns the organization"},
        },
        "required": ["name"],
    },

    # ================================================================================
    # ACTIVITIES
    # ================================================================================
    "list_activities": {
        "method": "GET",
        "path": "/activities",
        "description": "List all activities",
        "parameters": {
            "start": {"type": "Optional[int]", "location": "query", "description": "Pagination start (default 0)"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Items shown per page (default 100)"},
            "type": {"type": "Optional[str]", "location": "query", "description": "Type of activity (e.g. call, meeting, task, deadline, email)"},
            "done": {"type": "Optional[int]", "location": "query", "description": "Filter by done status (0 = not done, 1 = done)"},
            "user_id": {"type": "Optional[int]", "location": "query", "description": "Filter by user ID"},
            "start_date": {"type": "Optional[str]", "location": "query", "description": "Start date filter (YYYY-MM-DD)"},
            "end_date": {"type": "Optional[str]", "location": "query", "description": "End date filter (YYYY-MM-DD)"},
        },
        "required": [],
    },
    "get_activity": {
        "method": "GET",
        "path": "/activities/{id}",
        "description": "Get details of a specific activity",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The activity ID"},
        },
        "required": ["id"],
    },
    "create_activity": {
        "method": "POST",
        "path": "/activities",
        "description": "Create a new activity",
        "parameters": {
            "subject": {"type": "str", "location": "body", "description": "Subject of the activity"},
            "type": {"type": "str", "location": "body", "description": "Type of the activity (e.g. call, meeting, task)"},
            "done": {"type": "Optional[int]", "location": "body", "description": "Whether the activity is done (0 or 1)"},
            "due_date": {"type": "Optional[str]", "location": "body", "description": "Due date of the activity (YYYY-MM-DD)"},
            "due_time": {"type": "Optional[str]", "location": "body", "description": "Due time of the activity (HH:MM)"},
            "duration": {"type": "Optional[str]", "location": "body", "description": "Duration of the activity (HH:MM)"},
            "deal_id": {"type": "Optional[int]", "location": "body", "description": "ID of the deal this activity is linked to"},
            "person_id": {"type": "Optional[int]", "location": "body", "description": "ID of the person this activity is linked to"},
            "org_id": {"type": "Optional[int]", "location": "body", "description": "ID of the organization this activity is linked to"},
            "user_id": {"type": "Optional[int]", "location": "body", "description": "ID of the user who owns the activity"},
            "note": {"type": "Optional[str]", "location": "body", "description": "Note of the activity (HTML format)"},
        },
        "required": ["subject", "type"],
    },

    # ================================================================================
    # PIPELINES
    # ================================================================================
    "list_pipelines": {
        "method": "GET",
        "path": "/pipelines",
        "description": "List all pipelines",
        "parameters": {},
        "required": [],
    },
    "get_pipeline": {
        "method": "GET",
        "path": "/pipelines/{id}",
        "description": "Get details of a specific pipeline",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The pipeline ID"},
        },
        "required": ["id"],
    },

    # ================================================================================
    # STAGES
    # ================================================================================
    "list_stages": {
        "method": "GET",
        "path": "/stages",
        "description": "List all stages",
        "parameters": {
            "pipeline_id": {"type": "Optional[int]", "location": "query", "description": "Filter stages by pipeline ID"},
        },
        "required": [],
    },
    "get_stage": {
        "method": "GET",
        "path": "/stages/{id}",
        "description": "Get details of a specific stage",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The stage ID"},
        },
        "required": ["id"],
    },

    # ================================================================================
    # PRODUCTS
    # ================================================================================
    "list_products": {
        "method": "GET",
        "path": "/products",
        "description": "List all products",
        "parameters": {
            "start": {"type": "Optional[int]", "location": "query", "description": "Pagination start (default 0)"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Items shown per page (default 100)"},
        },
        "required": [],
    },
    "get_product": {
        "method": "GET",
        "path": "/products/{id}",
        "description": "Get details of a specific product",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The product ID"},
        },
        "required": ["id"],
    },

    # ================================================================================
    # NOTES
    # ================================================================================
    "list_notes": {
        "method": "GET",
        "path": "/notes",
        "description": "List all notes",
        "parameters": {
            "deal_id": {"type": "Optional[int]", "location": "query", "description": "Filter notes by deal ID"},
            "person_id": {"type": "Optional[int]", "location": "query", "description": "Filter notes by person ID"},
            "org_id": {"type": "Optional[int]", "location": "query", "description": "Filter notes by organization ID"},
            "start": {"type": "Optional[int]", "location": "query", "description": "Pagination start (default 0)"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Items shown per page (default 100)"},
        },
        "required": [],
    },
    "get_note": {
        "method": "GET",
        "path": "/notes/{id}",
        "description": "Get details of a specific note",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The note ID"},
        },
        "required": ["id"],
    },
    "create_note": {
        "method": "POST",
        "path": "/notes",
        "description": "Create a new note",
        "parameters": {
            "content": {"type": "str", "location": "body", "description": "Content of the note (HTML format)"},
            "deal_id": {"type": "Optional[int]", "location": "body", "description": "ID of the deal this note is attached to"},
            "person_id": {"type": "Optional[int]", "location": "body", "description": "ID of the person this note is attached to"},
            "org_id": {"type": "Optional[int]", "location": "body", "description": "ID of the organization this note is attached to"},
        },
        "required": ["content"],
    },

    # ================================================================================
    # LEADS
    # ================================================================================
    "list_leads": {
        "method": "GET",
        "path": "/leads",
        "description": "List all leads",
        "parameters": {
            "limit": {"type": "Optional[int]", "location": "query", "description": "Items shown per page (default 100)"},
            "start": {"type": "Optional[int]", "location": "query", "description": "Pagination start (default 0)"},
            "sort": {"type": "Optional[str]", "location": "query", "description": "Field name and sorting mode"},
            "filter_id": {"type": "Optional[int]", "location": "query", "description": "ID of the filter to use"},
        },
        "required": [],
    },
    "get_lead": {
        "method": "GET",
        "path": "/leads/{id}",
        "description": "Get details of a specific lead",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The lead ID"},
        },
        "required": ["id"],
    },

    # ================================================================================
    # CUSTOM FIELDS
    # ================================================================================
    "list_deal_fields": {
        "method": "GET",
        "path": "/dealFields",
        "description": "List all deal fields (including custom fields)",
        "parameters": {
            "start": {"type": "Optional[int]", "location": "query", "description": "Pagination start (default 0)"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Items shown per page (default 100)"},
        },
        "required": [],
    },
    "list_person_fields": {
        "method": "GET",
        "path": "/personFields",
        "description": "List all person fields (including custom fields)",
        "parameters": {
            "start": {"type": "Optional[int]", "location": "query", "description": "Pagination start (default 0)"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Items shown per page (default 100)"},
        },
        "required": [],
    },
    "list_organization_fields": {
        "method": "GET",
        "path": "/organizationFields",
        "description": "List all organization fields (including custom fields)",
        "parameters": {
            "start": {"type": "Optional[int]", "location": "query", "description": "Pagination start (default 0)"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Items shown per page (default 100)"},
        },
        "required": [],
    },
}


# ================================================================================
# Code Generator
# ================================================================================


class PipedriveDataSourceGenerator:
    """Generator for comprehensive Pipedrive REST API datasource class.

    Generates methods for Pipedrive API v1 endpoints.
    The generated DataSource class accepts a PipedriveClient whose base URL
    setting determines the API endpoint.
    """

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace("-", "_").replace(".", "_").replace("/", "_")
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == "_"):
            sanitized = f"param_{sanitized}"
        # Avoid shadowing builtins
        if sanitized in ("type", "id", "format", "input", "list", "dict", "set",
                         "map", "filter", "hash", "range", "open", "print", "next"):
            sanitized = f"{sanitized}_"
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
            inner = PipedriveDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = PipedriveDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                PipedriveDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = PipedriveDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                PipedriveDataSourceGenerator._modernize_type(p.strip()) for p in parts
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
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> PipedriveResponse:"

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
            "            PipedriveResponse with operation result",
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
            "            return PipedriveResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return PipedriveResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_pipedrive_datasource(self) -> str:
        """Generate the complete Pipedrive datasource class."""

        class_lines = [
            '"""',
            "Pipedrive REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Pipedrive REST API v1 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.http.http_request import HTTPRequest",
            "from app.sources.client.pipedrive.pipedrive import PipedriveClient, PipedriveResponse",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class PipedriveDataSource:",
            '    """Pipedrive REST API DataSource',
            "",
            "    Provides async wrapper methods for Pipedrive REST API operations:",
            "    - Users management",
            "    - Deals CRUD and management",
            "    - Persons (contacts) CRUD",
            "    - Organizations CRUD",
            "    - Activities management",
            "    - Pipelines and Stages",
            "    - Products management",
            "    - Notes CRUD",
            "    - Leads management",
            "    - Custom fields (Deal, Person, Organization)",
            "",
            "    The base URL is determined by the PipedriveClient's configured base URL.",
            "",
            "    All methods return PipedriveResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: PipedriveClient) -> None:",
            '        """Initialize with PipedriveClient.',
            "",
            "        Args:",
            "            client: PipedriveClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'PipedriveDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> PipedriveClient:",
            '        """Return the underlying PipedriveClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in PIPEDRIVE_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Pipedrive datasource to a file."""
        if filename is None:
            filename = "pipedrive.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        pipedrive_dir = script_dir.parent / "app" / "sources" / "external" / "pipedrive"
        pipedrive_dir.mkdir(parents=True, exist_ok=True)

        full_path = pipedrive_dir / filename

        class_code = self.generate_pipedrive_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Pipedrive data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by category
        resource_categories = {
            "Users": 0,
            "Deals": 0,
            "Persons": 0,
            "Organizations": 0,
            "Activities": 0,
            "Pipelines": 0,
            "Stages": 0,
            "Products": 0,
            "Notes": 0,
            "Leads": 0,
            "Custom Fields": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "user" in name:
                resource_categories["Users"] += 1
            elif "deal" in name and "field" not in name:
                resource_categories["Deals"] += 1
            elif "person" in name and "field" not in name:
                resource_categories["Persons"] += 1
            elif "organization" in name and "field" not in name:
                resource_categories["Organizations"] += 1
            elif "activit" in name:
                resource_categories["Activities"] += 1
            elif "pipeline" in name:
                resource_categories["Pipelines"] += 1
            elif "stage" in name:
                resource_categories["Stages"] += 1
            elif "product" in name:
                resource_categories["Products"] += 1
            elif "note" in name:
                resource_categories["Notes"] += 1
            elif "lead" in name:
                resource_categories["Leads"] += 1
            elif "field" in name:
                resource_categories["Custom Fields"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for Pipedrive data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Pipedrive REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = PipedriveDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Pipedrive data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
