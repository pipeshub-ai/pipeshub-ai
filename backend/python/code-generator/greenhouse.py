# ruff: noqa
"""
Greenhouse Harvest REST API Code Generator

Generates GreenhouseDataSource class covering Greenhouse Harvest API v1:
- Candidates and Applications
- Jobs and Job Stages
- Offers
- Departments and Offices
- Users
- Scorecards, Scheduled Interviews
- Sources, Rejection Reasons, Custom Fields
- Activity Feed

The generated DataSource accepts a GreenhouseClient and uses the client's
base URL (https://harvest.greenhouse.io/v1). Methods are generated for
all Harvest API endpoints.

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# Greenhouse Harvest API Endpoints
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which already includes /v1)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
# ================================================================================

GREENHOUSE_API_ENDPOINTS = {
    # ================================================================================
    # CANDIDATES
    # ================================================================================
    "list_candidates": {
        "method": "GET",
        "path": "/candidates",
        "description": "List all candidates",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page (max 500)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number to retrieve"},
            "created_after": {"type": "Optional[str]", "location": "query", "description": "Return candidates created after this date (ISO 8601)"},
            "created_before": {"type": "Optional[str]", "location": "query", "description": "Return candidates created before this date (ISO 8601)"},
            "updated_after": {"type": "Optional[str]", "location": "query", "description": "Return candidates updated after this date (ISO 8601)"},
            "updated_before": {"type": "Optional[str]", "location": "query", "description": "Return candidates updated before this date (ISO 8601)"},
            "job_id": {"type": "Optional[str]", "location": "query", "description": "Filter candidates by job ID"},
        },
        "required": [],
    },
    "get_candidate": {
        "method": "GET",
        "path": "/candidates/{candidate_id}",
        "description": "Get a single candidate by ID",
        "parameters": {
            "candidate_id": {"type": "str", "location": "path", "description": "The candidate ID"},
        },
        "required": ["candidate_id"],
    },

    # ================================================================================
    # APPLICATIONS
    # ================================================================================
    "list_applications": {
        "method": "GET",
        "path": "/applications",
        "description": "List all applications",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page (max 500)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number to retrieve"},
            "created_after": {"type": "Optional[str]", "location": "query", "description": "Return applications created after this date (ISO 8601)"},
            "created_before": {"type": "Optional[str]", "location": "query", "description": "Return applications created before this date (ISO 8601)"},
            "last_activity_after": {"type": "Optional[str]", "location": "query", "description": "Return applications with activity after this date (ISO 8601)"},
            "job_id": {"type": "Optional[str]", "location": "query", "description": "Filter applications by job ID"},
            "status": {"type": "Optional[str]", "location": "query", "description": "Filter by application status (active, converted, hired, rejected)"},
        },
        "required": [],
    },
    "get_application": {
        "method": "GET",
        "path": "/applications/{application_id}",
        "description": "Get a single application by ID",
        "parameters": {
            "application_id": {"type": "str", "location": "path", "description": "The application ID"},
        },
        "required": ["application_id"],
    },

    # ================================================================================
    # JOBS
    # ================================================================================
    "list_jobs": {
        "method": "GET",
        "path": "/jobs",
        "description": "List all jobs",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page (max 500)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number to retrieve"},
            "status": {"type": "Optional[str]", "location": "query", "description": "Filter by job status (open, closed, draft)"},
            "department_id": {"type": "Optional[str]", "location": "query", "description": "Filter jobs by department ID"},
            "office_id": {"type": "Optional[str]", "location": "query", "description": "Filter jobs by office ID"},
            "created_after": {"type": "Optional[str]", "location": "query", "description": "Return jobs created after this date (ISO 8601)"},
            "created_before": {"type": "Optional[str]", "location": "query", "description": "Return jobs created before this date (ISO 8601)"},
            "updated_after": {"type": "Optional[str]", "location": "query", "description": "Return jobs updated after this date (ISO 8601)"},
            "updated_before": {"type": "Optional[str]", "location": "query", "description": "Return jobs updated before this date (ISO 8601)"},
        },
        "required": [],
    },
    "get_job": {
        "method": "GET",
        "path": "/jobs/{job_id}",
        "description": "Get a single job by ID",
        "parameters": {
            "job_id": {"type": "str", "location": "path", "description": "The job ID"},
        },
        "required": ["job_id"],
    },

    # ================================================================================
    # JOB STAGES
    # ================================================================================
    "list_job_stages": {
        "method": "GET",
        "path": "/job_stages",
        "description": "List all job stages",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page (max 500)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number to retrieve"},
            "created_after": {"type": "Optional[str]", "location": "query", "description": "Return job stages created after this date (ISO 8601)"},
            "updated_after": {"type": "Optional[str]", "location": "query", "description": "Return job stages updated after this date (ISO 8601)"},
        },
        "required": [],
    },
    "get_job_stage": {
        "method": "GET",
        "path": "/job_stages/{job_stage_id}",
        "description": "Get a single job stage by ID",
        "parameters": {
            "job_stage_id": {"type": "str", "location": "path", "description": "The job stage ID"},
        },
        "required": ["job_stage_id"],
    },

    # ================================================================================
    # OFFERS
    # ================================================================================
    "list_offers": {
        "method": "GET",
        "path": "/offers",
        "description": "List all offers",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page (max 500)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number to retrieve"},
            "created_after": {"type": "Optional[str]", "location": "query", "description": "Return offers created after this date (ISO 8601)"},
            "created_before": {"type": "Optional[str]", "location": "query", "description": "Return offers created before this date (ISO 8601)"},
            "updated_after": {"type": "Optional[str]", "location": "query", "description": "Return offers updated after this date (ISO 8601)"},
            "updated_before": {"type": "Optional[str]", "location": "query", "description": "Return offers updated before this date (ISO 8601)"},
            "status": {"type": "Optional[str]", "location": "query", "description": "Filter by offer status (unresolved, accepted, rejected, deprecated)"},
        },
        "required": [],
    },
    "get_offer": {
        "method": "GET",
        "path": "/offers/{offer_id}",
        "description": "Get a single offer by ID",
        "parameters": {
            "offer_id": {"type": "str", "location": "path", "description": "The offer ID"},
        },
        "required": ["offer_id"],
    },

    # ================================================================================
    # DEPARTMENTS
    # ================================================================================
    "list_departments": {
        "method": "GET",
        "path": "/departments",
        "description": "List all departments",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page (max 500)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number to retrieve"},
        },
        "required": [],
    },
    "get_department": {
        "method": "GET",
        "path": "/departments/{department_id}",
        "description": "Get a single department by ID",
        "parameters": {
            "department_id": {"type": "str", "location": "path", "description": "The department ID"},
        },
        "required": ["department_id"],
    },

    # ================================================================================
    # OFFICES
    # ================================================================================
    "list_offices": {
        "method": "GET",
        "path": "/offices",
        "description": "List all offices",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page (max 500)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number to retrieve"},
        },
        "required": [],
    },
    "get_office": {
        "method": "GET",
        "path": "/offices/{office_id}",
        "description": "Get a single office by ID",
        "parameters": {
            "office_id": {"type": "str", "location": "path", "description": "The office ID"},
        },
        "required": ["office_id"],
    },

    # ================================================================================
    # USERS
    # ================================================================================
    "list_users": {
        "method": "GET",
        "path": "/users",
        "description": "List all users",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page (max 500)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number to retrieve"},
            "created_after": {"type": "Optional[str]", "location": "query", "description": "Return users created after this date (ISO 8601)"},
            "updated_after": {"type": "Optional[str]", "location": "query", "description": "Return users updated after this date (ISO 8601)"},
            "email": {"type": "Optional[str]", "location": "query", "description": "Filter users by email address"},
        },
        "required": [],
    },
    "get_user": {
        "method": "GET",
        "path": "/users/{user_id}",
        "description": "Get a single user by ID",
        "parameters": {
            "user_id": {"type": "str", "location": "path", "description": "The user ID"},
        },
        "required": ["user_id"],
    },

    # ================================================================================
    # SCORECARDS
    # ================================================================================
    "list_scorecards": {
        "method": "GET",
        "path": "/scorecards",
        "description": "List all scorecards",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page (max 500)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number to retrieve"},
            "created_after": {"type": "Optional[str]", "location": "query", "description": "Return scorecards created after this date (ISO 8601)"},
            "updated_after": {"type": "Optional[str]", "location": "query", "description": "Return scorecards updated after this date (ISO 8601)"},
            "application_id": {"type": "Optional[str]", "location": "query", "description": "Filter scorecards by application ID"},
        },
        "required": [],
    },

    # ================================================================================
    # SCHEDULED INTERVIEWS
    # ================================================================================
    "list_scheduled_interviews": {
        "method": "GET",
        "path": "/scheduled_interviews",
        "description": "List all scheduled interviews",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page (max 500)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number to retrieve"},
            "created_after": {"type": "Optional[str]", "location": "query", "description": "Return interviews created after this date (ISO 8601)"},
            "updated_after": {"type": "Optional[str]", "location": "query", "description": "Return interviews updated after this date (ISO 8601)"},
            "starts_after": {"type": "Optional[str]", "location": "query", "description": "Return interviews starting after this date (ISO 8601)"},
            "starts_before": {"type": "Optional[str]", "location": "query", "description": "Return interviews starting before this date (ISO 8601)"},
        },
        "required": [],
    },

    # ================================================================================
    # SOURCES
    # ================================================================================
    "list_sources": {
        "method": "GET",
        "path": "/sources",
        "description": "List all sources",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page (max 500)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number to retrieve"},
        },
        "required": [],
    },

    # ================================================================================
    # REJECTION REASONS
    # ================================================================================
    "list_rejection_reasons": {
        "method": "GET",
        "path": "/rejection_reasons",
        "description": "List all rejection reasons",
        "parameters": {
            "per_page": {"type": "Optional[int]", "location": "query", "description": "Number of results per page (max 500)"},
            "page": {"type": "Optional[int]", "location": "query", "description": "Page number to retrieve"},
        },
        "required": [],
    },

    # ================================================================================
    # CUSTOM FIELDS
    # ================================================================================
    "list_custom_fields": {
        "method": "GET",
        "path": "/custom_fields",
        "description": "List all custom fields",
        "parameters": {
            "field_type": {"type": "Optional[str]", "location": "query", "description": "Filter by field type (candidate, application, offer, job, etc.)"},
        },
        "required": [],
    },

    # ================================================================================
    # ACTIVITY FEED
    # ================================================================================
    "get_activity_feed": {
        "method": "GET",
        "path": "/candidates/{candidate_id}/activity_feed",
        "description": "Get the activity feed for a candidate",
        "parameters": {
            "candidate_id": {"type": "str", "location": "path", "description": "The candidate ID"},
        },
        "required": ["candidate_id"],
    },
}


class GreenhouseDataSourceGenerator:
    """Generator for comprehensive Greenhouse Harvest API datasource class.

    Generates methods for Greenhouse Harvest API v1 endpoints.
    The generated DataSource class accepts a GreenhouseClient whose
    base URL is https://harvest.greenhouse.io/v1.
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
            inner = GreenhouseDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = GreenhouseDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                GreenhouseDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = GreenhouseDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                GreenhouseDataSourceGenerator._modernize_type(p.strip()) for p in parts
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

        # Collect required params
        required_non_bool: List[str] = []
        for param_name in endpoint_info["required"]:
            if param_name in endpoint_info["parameters"]:
                param_info = endpoint_info["parameters"][param_name]
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                required_non_bool.append(f"{sanitized_name}: {modern_type}")

        # Collect optional parameters
        optional_params: List[str] = []
        for param_name, param_info in endpoint_info["parameters"].items():
            if param_name not in endpoint_info["required"]:
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                if "| None" not in modern_type:
                    modern_type = f"{modern_type} | None"
                optional_params.append(f"{sanitized_name}: {modern_type} = None")

        # Build signature: required first, then * separator, then optional
        params.extend(required_non_bool)
        if optional_params:
            params.append("*")
        params.extend(optional_params)

        signature_params = ",\n        ".join(params)
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> GreenhouseResponse:"

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
            "            GreenhouseResponse with operation result",
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
            "            return GreenhouseResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_greenhouse_datasource(self) -> str:
        """Generate the complete Greenhouse datasource class."""

        class_lines = [
            '"""',
            "Greenhouse Harvest REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Greenhouse Harvest API v1 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.greenhouse.greenhouse import GreenhouseClient, GreenhouseResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class GreenhouseDataSource:",
            '    """Greenhouse Harvest REST API DataSource',
            "",
            "    Provides async wrapper methods for Greenhouse Harvest API operations:",
            "    - Candidates and Applications",
            "    - Jobs and Job Stages",
            "    - Offers",
            "    - Departments and Offices",
            "    - Users",
            "    - Scorecards, Scheduled Interviews",
            "    - Sources, Rejection Reasons, Custom Fields",
            "    - Activity Feed",
            "",
            "    The base URL is https://harvest.greenhouse.io/v1.",
            "",
            "    All methods return GreenhouseResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: GreenhouseClient) -> None:",
            '        """Initialize with GreenhouseClient.',
            "",
            "        Args:",
            "            client: GreenhouseClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'GreenhouseDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> GreenhouseClient:",
            '        """Return the underlying GreenhouseClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in GREENHOUSE_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Greenhouse datasource to a file."""
        if filename is None:
            filename = "greenhouse.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        greenhouse_dir = script_dir.parent / "app" / "sources" / "external" / "greenhouse"
        greenhouse_dir.mkdir(parents=True, exist_ok=True)

        full_path = greenhouse_dir / filename

        class_code = self.generate_greenhouse_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Greenhouse data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by resource
        resource_categories = {
            "Candidates": 0,
            "Applications": 0,
            "Jobs": 0,
            "Job Stages": 0,
            "Offers": 0,
            "Departments": 0,
            "Offices": 0,
            "Users": 0,
            "Scorecards": 0,
            "Scheduled Interviews": 0,
            "Sources": 0,
            "Rejection Reasons": 0,
            "Custom Fields": 0,
            "Activity Feed": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "candidate" in name and "activity" not in name:
                resource_categories["Candidates"] += 1
            elif "application" in name:
                resource_categories["Applications"] += 1
            elif "job_stage" in name:
                resource_categories["Job Stages"] += 1
            elif "job" in name:
                resource_categories["Jobs"] += 1
            elif "offer" in name:
                resource_categories["Offers"] += 1
            elif "department" in name:
                resource_categories["Departments"] += 1
            elif "office" in name:
                resource_categories["Offices"] += 1
            elif "user" in name:
                resource_categories["Users"] += 1
            elif "scorecard" in name:
                resource_categories["Scorecards"] += 1
            elif "interview" in name:
                resource_categories["Scheduled Interviews"] += 1
            elif "source" in name:
                resource_categories["Sources"] += 1
            elif "rejection" in name:
                resource_categories["Rejection Reasons"] += 1
            elif "custom_field" in name:
                resource_categories["Custom Fields"] += 1
            elif "activity" in name:
                resource_categories["Activity Feed"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for Greenhouse data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Greenhouse Harvest REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = GreenhouseDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Greenhouse data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
