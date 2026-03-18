# ruff: noqa
"""
Intercom REST API Code Generator

Generates IntercomDataSource class covering Intercom API:
- Admin operations
- Contact operations (list, get, create, update, search)
- Conversation operations
- Company operations
- Article operations
- Teams, tags, segments, data attributes

The generated DataSource accepts an IntercomClient and uses the client's
base URL to construct API requests.

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# Intercom API Endpoints
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url: https://api.intercom.io)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
# ================================================================================

INTERCOM_API_ENDPOINTS = {
    # ================================================================================
    # ADMINS
    # ================================================================================
    "get_me": {
        "method": "GET",
        "path": "/me",
        "description": "Get the current admin",
        "parameters": {},
        "required": [],
    },
    "list_admins": {
        "method": "GET",
        "path": "/admins",
        "description": "List all admins",
        "parameters": {},
        "required": [],
    },
    "get_admin": {
        "method": "GET",
        "path": "/admins/{id}",
        "description": "Get a specific admin by ID",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "Admin ID"},
        },
        "required": ["id"],
    },

    # ================================================================================
    # CONTACTS
    # ================================================================================
    "list_contacts": {
        "method": "GET",
        "path": "/contacts",
        "description": "List all contacts with optional pagination",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of contacts per page"},
            "starting_after": {"type": "Optional[str]", "location": "query", "description": "Cursor for pagination"},
        },
        "required": [],
    },
    "get_contact": {
        "method": "GET",
        "path": "/contacts/{id}",
        "description": "Get a specific contact by ID",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "Contact ID"},
        },
        "required": ["id"],
    },
    "create_contact": {
        "method": "POST",
        "path": "/contacts",
        "description": "Create a new contact",
        "parameters": {
            "role": {"type": "Optional[str]", "location": "body", "description": "Role: lead or user"},
            "external_id": {"type": "Optional[str]", "location": "body", "description": "External ID for the contact"},
            "email": {"type": "Optional[str]", "location": "body", "description": "Email address"},
            "phone": {"type": "Optional[str]", "location": "body", "description": "Phone number"},
            "name": {"type": "Optional[str]", "location": "body", "description": "Full name"},
            "avatar": {"type": "Optional[str]", "location": "body", "description": "Avatar URL"},
            "signed_up_at": {"type": "Optional[int]", "location": "body", "description": "Signup timestamp (Unix)"},
            "last_seen_at": {"type": "Optional[int]", "location": "body", "description": "Last seen timestamp (Unix)"},
            "owner_id": {"type": "Optional[int]", "location": "body", "description": "Owner admin ID"},
            "unsubscribed_from_emails": {"type": "Optional[bool]", "location": "body", "description": "Unsubscribed from emails"},
            "custom_attributes": {"type": "Optional[Dict[str, Any]]", "location": "body", "description": "Custom attributes"},
        },
        "required": [],
    },
    "update_contact": {
        "method": "PUT",
        "path": "/contacts/{id}",
        "description": "Update an existing contact",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "Contact ID"},
            "role": {"type": "Optional[str]", "location": "body", "description": "Role: lead or user"},
            "external_id": {"type": "Optional[str]", "location": "body", "description": "External ID"},
            "email": {"type": "Optional[str]", "location": "body", "description": "Email address"},
            "phone": {"type": "Optional[str]", "location": "body", "description": "Phone number"},
            "name": {"type": "Optional[str]", "location": "body", "description": "Full name"},
            "avatar": {"type": "Optional[str]", "location": "body", "description": "Avatar URL"},
            "signed_up_at": {"type": "Optional[int]", "location": "body", "description": "Signup timestamp (Unix)"},
            "last_seen_at": {"type": "Optional[int]", "location": "body", "description": "Last seen timestamp (Unix)"},
            "owner_id": {"type": "Optional[int]", "location": "body", "description": "Owner admin ID"},
            "unsubscribed_from_emails": {"type": "Optional[bool]", "location": "body", "description": "Unsubscribed from emails"},
            "custom_attributes": {"type": "Optional[Dict[str, Any]]", "location": "body", "description": "Custom attributes"},
        },
        "required": ["id"],
    },
    "search_contacts": {
        "method": "POST",
        "path": "/contacts/search",
        "description": "Search contacts with query filters",
        "parameters": {
            "query": {"type": "Dict[str, Any]", "location": "body", "description": "Search query object with field, operator, and value"},
            "pagination": {"type": "Optional[Dict[str, Any]]", "location": "body", "description": "Pagination options"},
            "sort": {"type": "Optional[Dict[str, Any]]", "location": "body", "description": "Sort options"},
        },
        "required": ["query"],
    },

    # ================================================================================
    # CONVERSATIONS
    # ================================================================================
    "list_conversations": {
        "method": "GET",
        "path": "/conversations",
        "description": "List all conversations with optional pagination",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of conversations per page"},
            "starting_after": {"type": "Optional[str]", "location": "query", "description": "Cursor for pagination"},
        },
        "required": [],
    },
    "get_conversation": {
        "method": "GET",
        "path": "/conversations/{id}",
        "description": "Get a specific conversation by ID",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "Conversation ID"},
        },
        "required": ["id"],
    },

    # ================================================================================
    # COMPANIES
    # ================================================================================
    "list_companies": {
        "method": "GET",
        "path": "/companies",
        "description": "List all companies",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of companies per page"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number"},
        },
        "required": [],
    },
    "get_company": {
        "method": "GET",
        "path": "/companies/{id}",
        "description": "Get a specific company by ID",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "Company ID"},
        },
        "required": ["id"],
    },
    "create_company": {
        "method": "POST",
        "path": "/companies",
        "description": "Create or update a company",
        "parameters": {
            "company_id": {"type": "Optional[str]", "location": "body", "description": "External company ID"},
            "name": {"type": "Optional[str]", "location": "body", "description": "Company name"},
            "plan": {"type": "Optional[str]", "location": "body", "description": "Plan name"},
            "monthly_spend": {"type": "Optional[float]", "location": "body", "description": "Monthly spend"},
            "size": {"type": "Optional[int]", "location": "body", "description": "Number of employees"},
            "website": {"type": "Optional[str]", "location": "body", "description": "Website URL"},
            "industry": {"type": "Optional[str]", "location": "body", "description": "Industry"},
            "remote_created_at": {"type": "Optional[int]", "location": "body", "description": "Creation timestamp (Unix)"},
            "custom_attributes": {"type": "Optional[Dict[str, Any]]", "location": "body", "description": "Custom attributes"},
        },
        "required": [],
    },

    # ================================================================================
    # ARTICLES
    # ================================================================================
    "list_articles": {
        "method": "GET",
        "path": "/articles",
        "description": "List all articles",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of articles per page"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number"},
        },
        "required": [],
    },
    "get_article": {
        "method": "GET",
        "path": "/articles/{id}",
        "description": "Get a specific article by ID",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "Article ID"},
        },
        "required": ["id"],
    },
    "create_article": {
        "method": "POST",
        "path": "/articles",
        "description": "Create a new article",
        "parameters": {
            "title": {"type": "str", "location": "body", "description": "Article title"},
            "author_id": {"type": "int", "location": "body", "description": "Author admin ID"},
            "description": {"type": "Optional[str]", "location": "body", "description": "Article description"},
            "body": {"type": "Optional[str]", "location": "body", "description": "Article body (HTML)"},
            "state": {"type": "Optional[str]", "location": "body", "description": "State: published or draft"},
            "parent_id": {"type": "Optional[int]", "location": "body", "description": "Parent collection/section ID"},
            "parent_type": {"type": "Optional[str]", "location": "body", "description": "Parent type: collection or section"},
            "translated_content": {"type": "Optional[Dict[str, Any]]", "location": "body", "description": "Translated content by locale"},
        },
        "required": ["title", "author_id"],
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
    },

    # ================================================================================
    # TAGS
    # ================================================================================
    "list_tags": {
        "method": "GET",
        "path": "/tags",
        "description": "List all tags",
        "parameters": {},
        "required": [],
    },

    # ================================================================================
    # SEGMENTS
    # ================================================================================
    "list_segments": {
        "method": "GET",
        "path": "/segments",
        "description": "List all segments",
        "parameters": {},
        "required": [],
    },

    # ================================================================================
    # DATA ATTRIBUTES
    # ================================================================================
    "list_data_attributes": {
        "method": "GET",
        "path": "/data_attributes",
        "description": "List all data attributes",
        "parameters": {},
        "required": [],
    },
}


class IntercomDataSourceGenerator:
    """Generator for Intercom REST API datasource class."""

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace("-", "_").replace(".", "_").replace("/", "_")
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == "_"):
            sanitized = f"param_{sanitized}"
        if sanitized == "type":
            sanitized = "type_"
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
            inner = IntercomDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = IntercomDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                IntercomDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = IntercomDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                IntercomDataSourceGenerator._modernize_type(p.strip()) for p in parts
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
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> IntercomResponse:"

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
            "            IntercomResponse with operation result",
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
        lines.append('                headers={"Content-Type": "application/json", "Accept": "application/json"},')
        if has_query:
            lines.append("                query=query_params,")
        if body_lines:
            lines.append("                body=request_body,")
        lines.append("            )")
        lines.extend([
            "            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]",
            "            response_data = response.json() if response.text() else None",
            "            return IntercomResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return IntercomResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_intercom_datasource(self) -> str:
        """Generate the complete Intercom datasource class."""

        class_lines = [
            "# ruff: noqa: A002, FBT001",
            '"""',
            "Intercom REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Intercom REST API documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.intercom.intercom import IntercomClient, IntercomResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class IntercomDataSource:",
            '    """Intercom REST API DataSource',
            "",
            "    Provides async wrapper methods for Intercom REST API operations:",
            "    - Admin management",
            "    - Contact CRUD and search",
            "    - Conversation management",
            "    - Company management",
            "    - Article management",
            "    - Teams, tags, segments, data attributes",
            "",
            "    All methods return IntercomResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: IntercomClient) -> None:",
            '        """Initialize with IntercomClient.',
            "",
            "        Args:",
            "            client: IntercomClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'IntercomDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> IntercomClient:",
            '        """Return the underlying IntercomClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in INTERCOM_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Intercom datasource to a file."""
        if filename is None:
            filename = "intercom.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        target_dir = script_dir.parent / "app" / "sources" / "external" / "intercom"
        target_dir.mkdir(parents=True, exist_ok=True)

        full_path = target_dir / filename

        class_code = self.generate_intercom_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Intercom data source with {len(self.generated_methods)} methods")
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
    """Main function for Intercom data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Intercom REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = IntercomDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Intercom data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
