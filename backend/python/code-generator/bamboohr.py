# ruff: noqa
"""
BambooHR REST API Code Generator

Generates BambooHRDataSource class covering BambooHR API v1:
- Employee directory and management
- Employee files
- Metadata (fields, tables, lists, users)
- Custom reports
- Time off requests and policies
- Changed employees tracking
- Applicant tracking (applications, job summaries)

The generated DataSource accepts a BambooHRClient and uses the client's
configured base URL. Methods are generated for all API endpoints.

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# BambooHR API Endpoints - organized by resource
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which already includes /api/gateway.php/{domain}/v1)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
#   version: Which API version this endpoint belongs to
# ================================================================================

BAMBOOHR_API_ENDPOINTS = {
    # ================================================================================
    # EMPLOYEES
    # ================================================================================
    "get_employee_directory": {
        "method": "GET",
        "path": "/employees/directory",
        "description": "Get employee directory listing all active employees",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "get_employee": {
        "method": "GET",
        "path": "/employees/{employee_id}",
        "description": "Get a single employee by ID",
        "parameters": {
            "employee_id": {"type": "str", "location": "path", "description": "The employee ID"},
            "fields": {"type": "Optional[str]", "location": "query", "description": "Comma-separated list of fields to return"},
        },
        "required": ["employee_id"],
        "version": "v1",
    },
    "add_employee": {
        "method": "POST",
        "path": "/employees/",
        "description": "Add a new employee",
        "parameters": {
            "employee_data": {"type": "dict[str, Any]", "location": "body", "description": "Employee data fields (firstName, lastName, etc.)"},
        },
        "required": ["employee_data"],
        "version": "v1",
    },
    "update_employee": {
        "method": "PUT",
        "path": "/employees/{employee_id}",
        "description": "Update an existing employee",
        "parameters": {
            "employee_id": {"type": "str", "location": "path", "description": "The employee ID"},
            "employee_data": {"type": "dict[str, Any]", "location": "body", "description": "Employee data fields to update"},
        },
        "required": ["employee_id", "employee_data"],
        "version": "v1",
    },
    "get_changed_employees": {
        "method": "GET",
        "path": "/employees/changed",
        "description": "Get employees that have changed since a given date",
        "parameters": {
            "since": {"type": "str", "location": "query", "description": "ISO 8601 date string (e.g., 2024-01-01T00:00:00Z)"},
            "change_type": {"type": "Optional[str]", "location": "query", "description": "Type of changes to return (e.g., 'inserted', 'updated', 'deleted')", "api_name": "type"},
        },
        "required": ["since"],
        "version": "v1",
    },

    # ================================================================================
    # EMPLOYEE FILES
    # ================================================================================
    "list_employee_files": {
        "method": "GET",
        "path": "/employees/{employee_id}/files/view/",
        "description": "List all files for an employee",
        "parameters": {
            "employee_id": {"type": "str", "location": "path", "description": "The employee ID"},
        },
        "required": ["employee_id"],
        "version": "v1",
    },

    # ================================================================================
    # METADATA
    # ================================================================================
    "get_metadata_fields": {
        "method": "GET",
        "path": "/meta/fields/",
        "description": "Get list of all metadata fields",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "get_metadata_tables": {
        "method": "GET",
        "path": "/meta/tables/",
        "description": "Get list of all metadata tables",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "get_metadata_lists": {
        "method": "GET",
        "path": "/meta/lists/",
        "description": "Get list of all metadata lists (dropdown options)",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
    "get_metadata_users": {
        "method": "GET",
        "path": "/meta/users/",
        "description": "Get list of all users with access to BambooHR",
        "parameters": {},
        "required": [],
        "version": "v1",
    },

    # ================================================================================
    # REPORTS
    # ================================================================================
    "run_custom_report": {
        "method": "POST",
        "path": "/reports/custom",
        "description": "Run a custom report with specified fields and filters",
        "parameters": {
            "output_format": {"type": "Optional[str]", "location": "query", "description": "Output format (e.g., 'JSON', 'CSV', 'XLS', 'XML', 'PDF')", "api_name": "format"},
            "report_data": {"type": "dict[str, Any]", "location": "body", "description": "Report configuration (fields, filters, title, etc.)"},
        },
        "required": ["report_data"],
        "version": "v1",
    },
    "get_company_report": {
        "method": "GET",
        "path": "/reports/{report_id}",
        "description": "Get a saved company report by ID",
        "parameters": {
            "report_id": {"type": "str", "location": "path", "description": "The report ID"},
            "output_format": {"type": "Optional[str]", "location": "query", "description": "Output format (e.g., 'JSON', 'CSV', 'XLS', 'XML', 'PDF')", "api_name": "format"},
            "fd": {"type": "Optional[str]", "location": "query", "description": "Set to 'yes' to include field data in the response"},
        },
        "required": ["report_id"],
        "version": "v1",
    },

    # ================================================================================
    # TIME OFF
    # ================================================================================
    "get_time_off_requests": {
        "method": "GET",
        "path": "/time_off/requests/",
        "description": "Get time off requests within a date range",
        "parameters": {
            "start": {"type": "Optional[str]", "location": "query", "description": "Start date (YYYY-MM-DD)"},
            "end": {"type": "Optional[str]", "location": "query", "description": "End date (YYYY-MM-DD)"},
            "status": {"type": "Optional[str]", "location": "query", "description": "Filter by status (approved, denied, superceded, requested, canceled)"},
            "action": {"type": "Optional[str]", "location": "query", "description": "Filter by action (view, approve)"},
            "employeeId": {"type": "Optional[str]", "location": "query", "description": "Filter by employee ID"},
            "time_off_type": {"type": "Optional[str]", "location": "query", "description": "Filter by time off type ID", "api_name": "type"},
        },
        "required": [],
        "version": "v1",
    },
    "get_time_off_policies": {
        "method": "GET",
        "path": "/time_off/policies/",
        "description": "Get list of time off policies",
        "parameters": {},
        "required": [],
        "version": "v1",
    },

    # ================================================================================
    # APPLICANT TRACKING
    # ================================================================================
    "list_applications": {
        "method": "GET",
        "path": "/applicant_tracking/applications",
        "description": "List applicant tracking applications",
        "parameters": {
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number for pagination"},
            "jobId": {"type": "Optional[str]", "location": "query", "description": "Filter by job ID"},
            "applicationStatusId": {"type": "Optional[str]", "location": "query", "description": "Filter by application status ID"},
            "applicationStatus": {"type": "Optional[str]", "location": "query", "description": "Filter by application status name"},
            "jobStatusGroups": {"type": "Optional[str]", "location": "query", "description": "Filter by job status groups (e.g., 'Active', 'Inactive')"},
            "newSince": {"type": "Optional[str]", "location": "query", "description": "Filter applications created since this date (ISO 8601)"},
            "sortBy": {"type": "Optional[str]", "location": "query", "description": "Sort field (e.g., 'created_date', 'first_name', 'last_name')"},
            "sortOrder": {"type": "Optional[str]", "location": "query", "description": "Sort order ('ASC' or 'DESC')"},
        },
        "required": [],
        "version": "v1",
    },
    "get_application": {
        "method": "GET",
        "path": "/applicant_tracking/applications/{application_id}",
        "description": "Get a specific applicant tracking application",
        "parameters": {
            "application_id": {"type": "str", "location": "path", "description": "The application ID"},
        },
        "required": ["application_id"],
        "version": "v1",
    },
    "get_job_summaries": {
        "method": "GET",
        "path": "/applicant_tracking/job_summaries",
        "description": "Get job summaries for applicant tracking",
        "parameters": {},
        "required": [],
        "version": "v1",
    },
}


class BambooHRDataSourceGenerator:
    """Generator for comprehensive BambooHR REST API datasource class.

    Generates methods for BambooHR API v1 endpoints.
    The generated DataSource class accepts a BambooHRClient whose base URL
    is determined by the company domain.
    """

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace("-", "_").replace(".", "_").replace("/", "_")
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == "_"):
            sanitized = f"param_{sanitized}"
        return sanitized

    @staticmethod
    def _modernize_type(type_str: str) -> str:
        """Convert typing-style annotations to modern Python 3.10+ syntax.

        Optional[str] -> str | None, Dict[str, Any] -> dict[str, Any],
        List[str] -> list[str], etc.
        """
        if type_str.startswith("Optional[") and type_str.endswith("]"):
            inner = type_str[len("Optional["):-1]
            inner = BambooHRDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = BambooHRDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                BambooHRDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = BambooHRDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                BambooHRDataSourceGenerator._modernize_type(p.strip()) for p in parts
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
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> BambooHRResponse:"

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
            "            BambooHRResponse with operation result",
            '        """',
        ])

        return lines

    def _build_query_params(self, endpoint_info: Dict) -> List[str]:
        """Build query parameter handling code."""
        lines = ["        query_params: dict[str, Any] = {}"]
        required = endpoint_info.get("required", [])

        for param_name, param_info in endpoint_info["parameters"].items():
            if param_info["location"] == "query":
                sanitized_name = self._sanitize_parameter_name(param_name)
                # Use api_name if provided, otherwise use the parameter name
                api_name = param_info.get("api_name", param_name)
                is_required = param_name in required

                if is_required:
                    # Required params are always present, no None check needed
                    if "bool" in param_info["type"]:
                        lines.append(
                            f"        query_params['{api_name}'] = str({sanitized_name}).lower()"
                        )
                    elif "int" in param_info["type"]:
                        lines.append(
                            f"        query_params['{api_name}'] = str({sanitized_name})"
                        )
                    else:
                        lines.append(
                            f"        query_params['{api_name}'] = {sanitized_name}"
                        )
                elif "Optional[bool]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}'] = str({sanitized_name}).lower()",
                    ])
                elif "Optional[int]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}'] = str({sanitized_name})",
                    ])
                elif "List[" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}[]'] = {sanitized_name}",
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

        lines = ["        body: dict[str, Any] = {}"]

        for param_name, param_info in body_params.items():
            sanitized_name = self._sanitize_parameter_name(param_name)

            if param_name in endpoint_info["required"]:
                # For dict body params that represent the entire body, use update
                if "dict[" in self._modernize_type(param_info["type"]):
                    lines.append(f"        body.update({sanitized_name})")
                else:
                    lines.append(f"        body['{param_name}'] = {sanitized_name}")
            else:
                lines.extend([
                    f"        if {sanitized_name} is not None:",
                    f"            body['{param_name}'] = {sanitized_name}",
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
        lines.append('                headers={"Accept": "application/json"},')
        if has_query:
            lines.append("                query=query_params,")
        if body_lines:
            lines.append("                body=body,")
        lines.append("            )")
        lines.extend([
            "            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]",
            "            response_data = response.json() if response.text() else None",
            "            return BambooHRResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return BambooHRResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
            "version": endpoint_info.get("version", "v1"),
        })

        return "\n".join(lines)

    def generate_bamboohr_datasource(self) -> str:
        """Generate the complete BambooHR datasource class."""

        class_lines = [
            '"""',
            "BambooHR REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from BambooHR REST API v1 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.bamboohr.bamboohr import BambooHRClient, BambooHRResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class BambooHRDataSource:",
            '    """BambooHR REST API DataSource',
            "",
            "    Provides async wrapper methods for BambooHR REST API operations:",
            "    - Employee directory and management",
            "    - Employee files",
            "    - Metadata (fields, tables, lists, users)",
            "    - Custom reports and company reports",
            "    - Time off requests and policies",
            "    - Changed employees tracking",
            "    - Applicant tracking (applications, job summaries)",
            "",
            "    The base URL is determined by the BambooHRClient's configured company domain.",
            "",
            "    All methods return BambooHRResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: BambooHRClient) -> None:",
            '        """Initialize with BambooHRClient.',
            "",
            "        Args:",
            "            client: BambooHRClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'BambooHRDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> BambooHRClient:",
            '        """Return the underlying BambooHRClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in BAMBOOHR_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the BambooHR datasource to a file."""
        if filename is None:
            filename = "bamboohr.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        bamboohr_dir = script_dir.parent / "app" / "sources" / "external" / "bamboohr"
        bamboohr_dir.mkdir(parents=True, exist_ok=True)

        full_path = bamboohr_dir / filename

        class_code = self.generate_bamboohr_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated BambooHR data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by category
        categories = {}
        for method in self.generated_methods:
            version = method["version"]
            key = f"API {version}"
            categories[key] = categories.get(key, 0) + 1

        print(f"\nMethods by API version:")
        for category, count in sorted(categories.items()):
            print(f"  - {category}: {count}")

        # Print resource summary
        resource_categories = {
            "Employee": 0,
            "Employee Files": 0,
            "Metadata": 0,
            "Reports": 0,
            "Time Off": 0,
            "Applicant Tracking": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "employee" in name and "file" not in name and "changed" not in name:
                resource_categories["Employee"] += 1
            elif "file" in name:
                resource_categories["Employee Files"] += 1
            elif "metadata" in name or "meta" in name:
                resource_categories["Metadata"] += 1
            elif "report" in name:
                resource_categories["Reports"] += 1
            elif "time_off" in name:
                resource_categories["Time Off"] += 1
            elif "application" in name or "job" in name:
                resource_categories["Applicant Tracking"] += 1
            elif "changed" in name:
                resource_categories["Employee"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for BambooHR data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate BambooHR REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = BambooHRDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate BambooHR data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
