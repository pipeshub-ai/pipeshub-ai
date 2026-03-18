# ruff: noqa
"""
Amplitude REST API Code Generator

Generates AmplitudeDataSource class covering Amplitude API v2 and v3:
- Event Segmentation
- User Search and Activity
- User Deletion Management
- Raw Data Export
- Event Upload
- Cohorts
- Charts
- Annotations
- Releases
- Taxonomy (Event Types, User Properties, Event Properties)

The generated DataSource accepts an AmplitudeClient and uses the client's
configured base URLs for v2 and v3 endpoints.

All methods have explicit parameter signatures with no **kwargs usage.

Usage:
    python code-generator/amplitude.py
    python code-generator/amplitude.py --filename amplitude.py

Output:
    app/sources/external/amplitude/amplitude.py
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# Amplitude API Endpoints - organized by resource category
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
#   base: Which API base URL to use ("v2" or "v3")
# ================================================================================

AMPLITUDE_API_ENDPOINTS = {
    # ================================================================================
    # EVENT SEGMENTATION (v2)
    # ================================================================================
    "get_event_segmentation": {
        "method": "GET",
        "path": "/events/segmentation",
        "description": "Get event segmentation data for analytics queries",
        "parameters": {
            "e": {"type": "str", "location": "query", "description": "Event JSON object (required). Defines the event to segment on"},
            "start": {"type": "str", "location": "query", "description": "Start date (required), e.g. '20230101'"},
            "end": {"type": "str", "location": "query", "description": "End date (required), e.g. '20230131'"},
            "m": {"type": "str", "location": "query", "description": "Metric type (e.g. 'uniques', 'totals', 'avg')"},
            "i": {"type": "str", "location": "query", "description": "Interval: '-300000', '3600000', '86400000', or '604800000'"},
            "g": {"type": "str", "location": "query", "description": "Group by property"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Limit the number of group by values returned"},
        },
        "required": ["e", "start", "end"],
        "base": "v2",
    },

    # ================================================================================
    # USER SEARCH (v2)
    # ================================================================================
    "search_user": {
        "method": "GET",
        "path": "/usersearch",
        "description": "Search for a user by email or Amplitude ID",
        "parameters": {
            "user": {"type": "str", "location": "query", "description": "User email address or Amplitude ID (required)"},
        },
        "required": ["user"],
        "base": "v2",
    },

    # ================================================================================
    # USER ACTIVITY (v2)
    # ================================================================================
    "get_user_activity": {
        "method": "GET",
        "path": "/useractivity",
        "description": "Get a user's event activity",
        "parameters": {
            "user": {"type": "str", "location": "query", "description": "Amplitude user ID (required)"},
            "offset": {"type": "Optional[int]", "location": "query", "description": "Offset for pagination"},
            "limit": {"type": "Optional[int]", "location": "query", "description": "Number of events to return (max 1000)"},
        },
        "required": ["user"],
        "base": "v2",
    },

    # ================================================================================
    # USER DELETIONS (v2)
    # ================================================================================
    "get_user_deletion_jobs": {
        "method": "GET",
        "path": "/deletions/users",
        "description": "Get user deletion jobs within a date range",
        "parameters": {
            "start_day": {"type": "Optional[str]", "location": "query", "description": "Start date for deletion jobs (e.g. '2023-01-01')"},
            "end_day": {"type": "Optional[str]", "location": "query", "description": "End date for deletion jobs (e.g. '2023-01-31')"},
        },
        "required": [],
        "base": "v2",
    },
    "create_user_deletion": {
        "method": "POST",
        "path": "/deletions/users",
        "description": "Create a user deletion job to delete user data",
        "parameters": {
            "amplitude_ids": {"type": "list[int] | None", "location": "body", "description": "List of Amplitude user IDs to delete"},
            "user_ids": {"type": "list[str] | None", "location": "body", "description": "List of user IDs to delete"},
            "requester": {"type": "Optional[str]", "location": "body", "description": "Email of the requester"},
        },
        "required": [],
        "base": "v2",
    },

    # ================================================================================
    # RAW DATA EXPORT (v2)
    # ================================================================================
    "export_raw_data": {
        "method": "GET",
        "path": "/export",
        "description": "Export raw event data for a date range (returns zipped JSON)",
        "parameters": {
            "start": {"type": "str", "location": "query", "description": "Start date hour (required), e.g. '20230101T00'"},
            "end": {"type": "str", "location": "query", "description": "End date hour (required), e.g. '20230102T00'"},
        },
        "required": ["start", "end"],
        "base": "v2",
    },

    # ================================================================================
    # EVENT UPLOAD (v2)
    # ================================================================================
    "upload_events": {
        "method": "POST",
        "path": "/events/upload",
        "description": "Upload events to Amplitude (batch upload)",
        "parameters": {
            "api_key": {"type": "str", "location": "body", "description": "Amplitude API key"},
            "events": {"type": "list[dict[str, Any]]", "location": "body", "description": "List of event objects to upload"},
        },
        "required": ["api_key", "events"],
        "base": "v2",
    },

    # ================================================================================
    # COHORTS (v3)
    # ================================================================================
    "list_cohorts": {
        "method": "GET",
        "path": "/cohorts",
        "description": "List all cohorts in the project",
        "parameters": {},
        "required": [],
        "base": "v3",
    },
    "get_cohort": {
        "method": "GET",
        "path": "/cohorts/{cohort_id}",
        "description": "Get details of a specific cohort",
        "parameters": {
            "cohort_id": {"type": "str", "location": "path", "description": "The cohort ID"},
        },
        "required": ["cohort_id"],
        "base": "v3",
    },

    # ================================================================================
    # CHARTS (v3)
    # ================================================================================
    "query_chart": {
        "method": "POST",
        "path": "/charts/{chart_id}/query",
        "description": "Query a saved chart by ID",
        "parameters": {
            "chart_id": {"type": "str", "location": "path", "description": "The chart ID"},
        },
        "required": ["chart_id"],
        "base": "v3",
    },

    # ================================================================================
    # ANNOTATIONS (v2)
    # ================================================================================
    "list_annotations": {
        "method": "GET",
        "path": "/annotations",
        "description": "List all annotations",
        "parameters": {},
        "required": [],
        "base": "v2",
    },
    "create_annotation": {
        "method": "POST",
        "path": "/annotations",
        "description": "Create a new annotation",
        "parameters": {
            "date": {"type": "str", "location": "body", "description": "Date of the annotation (e.g. '2023-01-15')"},
            "label": {"type": "str", "location": "body", "description": "Label/title of the annotation"},
            "details": {"type": "Optional[str]", "location": "body", "description": "Additional details for the annotation"},
        },
        "required": ["date", "label"],
        "base": "v2",
    },

    # ================================================================================
    # RELEASES (v2)
    # ================================================================================
    "list_releases": {
        "method": "GET",
        "path": "/releases",
        "description": "List all releases",
        "parameters": {},
        "required": [],
        "base": "v2",
    },
    "create_release": {
        "method": "POST",
        "path": "/releases",
        "description": "Create a new release",
        "parameters": {
            "version": {"type": "str", "location": "body", "description": "Release version string"},
            "release_start": {"type": "str", "location": "body", "description": "Release start date (e.g. '2023-01-15')"},
            "release_end": {"type": "Optional[str]", "location": "body", "description": "Release end date (e.g. '2023-01-16')"},
            "title": {"type": "Optional[str]", "location": "body", "description": "Title of the release"},
            "description": {"type": "Optional[str]", "location": "body", "description": "Description of the release"},
            "platforms": {"type": "list[str] | None", "location": "body", "description": "List of platforms for this release"},
            "created_by": {"type": "Optional[str]", "location": "body", "description": "Email of the release creator"},
            "chart_id": {"type": "Optional[str]", "location": "body", "description": "Chart ID to associate with the release"},
        },
        "required": ["version", "release_start"],
        "base": "v2",
    },

    # ================================================================================
    # TAXONOMY - EVENT TYPES (v2)
    # ================================================================================
    "list_event_types": {
        "method": "GET",
        "path": "/taxonomy/event-type",
        "description": "List all event types in the project's taxonomy",
        "parameters": {},
        "required": [],
        "base": "v2",
    },
    "get_event_type": {
        "method": "GET",
        "path": "/taxonomy/event-type/{event_type}",
        "description": "Get a specific event type from the taxonomy",
        "parameters": {
            "event_type": {"type": "str", "location": "path", "description": "The event type name"},
        },
        "required": ["event_type"],
        "base": "v2",
    },

    # ================================================================================
    # TAXONOMY - USER PROPERTIES (v2)
    # ================================================================================
    "list_user_properties": {
        "method": "GET",
        "path": "/taxonomy/user-property",
        "description": "List all user properties in the project's taxonomy",
        "parameters": {},
        "required": [],
        "base": "v2",
    },

    # ================================================================================
    # TAXONOMY - EVENT PROPERTIES (v2)
    # ================================================================================
    "list_event_properties": {
        "method": "GET",
        "path": "/taxonomy/event-property",
        "description": "List all event properties in the project's taxonomy",
        "parameters": {},
        "required": [],
        "base": "v2",
    },
}


class AmplitudeDataSourceGenerator:
    """Generator for comprehensive Amplitude REST API datasource class.

    Generates methods for both v2 and v3 Amplitude API endpoints.
    The generated DataSource class accepts an AmplitudeClient whose
    base URLs determine the API endpoints.
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
                    # For required params, always add; for optional, check None
                    if param_name in endpoint_info.get("required", []):
                        lines.append(f"        query_params['{param_name}'] = {sanitized_name}")
                    else:
                        lines.extend([
                            f"        if {sanitized_name} is not None:",
                            f"            query_params['{param_name}'] = {sanitized_name}",
                        ])

        return lines

    def _build_path_formatting(self, path: str, endpoint_info: Dict) -> str:
        """Build URL path with parameter substitution."""
        base = endpoint_info.get("base", "v2")
        base_url_expr = "self.base_url" if base == "v2" else "self.base_url_v3"

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
            return f'        url = {base_url_expr} + "{path}".format({format_dict})'
        else:
            return f'        url = {base_url_expr} + "{path}"'

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
            inner = AmplitudeDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = AmplitudeDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                AmplitudeDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = AmplitudeDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                AmplitudeDataSourceGenerator._modernize_type(p.strip()) for p in parts
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
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> AmplitudeResponse:"

    def _generate_method_docstring(self, endpoint_info: Dict) -> List[str]:
        """Generate method docstring."""
        base = endpoint_info.get("base", "v2")
        lines = [f'        """{endpoint_info["description"]} (API {base})', ""]

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
            "            AmplitudeResponse with operation result",
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
            "            return AmplitudeResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
            "base": endpoint_info.get("base", "v2"),
        })

        return "\n".join(lines)

    def generate_amplitude_datasource(self) -> str:
        """Generate the complete Amplitude datasource class."""

        class_lines = [
            '"""',
            "Amplitude REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Amplitude REST API v2/v3 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.amplitude.amplitude import AmplitudeClient, AmplitudeResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class AmplitudeDataSource:",
            '    """Amplitude REST API DataSource',
            "",
            "    Provides async wrapper methods for Amplitude REST API operations:",
            "    - Event Segmentation queries",
            "    - User Search and Activity",
            "    - User Deletion management",
            "    - Raw Data Export",
            "    - Event Upload",
            "    - Cohort management",
            "    - Chart queries",
            "    - Annotations and Releases",
            "    - Taxonomy (Event Types, User Properties, Event Properties)",
            "",
            "    Uses two base URLs:",
            "    - v2: https://amplitude.com/api/2",
            "    - v3: https://analytics.amplitude.com/api/3",
            "",
            "    All methods return AmplitudeResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: AmplitudeClient) -> None:",
            '        """Initialize with AmplitudeClient.',
            "",
            "        Args:",
            "            client: AmplitudeClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "        try:",
            "            self.base_url_v3 = self.http.get_base_url_v3().rstrip('/')",
            "        except AttributeError:",
            "            self.base_url_v3 = 'https://analytics.amplitude.com/api/3'",
            "",
            "    def get_data_source(self) -> 'AmplitudeDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> AmplitudeClient:",
            '        """Return the underlying AmplitudeClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in AMPLITUDE_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Amplitude datasource to a file."""
        if filename is None:
            filename = "amplitude.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        amplitude_dir = script_dir.parent / "app" / "sources" / "external" / "amplitude"
        amplitude_dir.mkdir(parents=True, exist_ok=True)

        full_path = amplitude_dir / filename

        class_code = self.generate_amplitude_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Amplitude data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by API version
        categories = {}
        for method in self.generated_methods:
            base = method["base"]
            key = f"API {base}"
            categories[key] = categories.get(key, 0) + 1

        print(f"\nMethods by API version:")
        for category, count in sorted(categories.items()):
            print(f"  - {category}: {count}")

        # Print resource summary
        resource_categories = {
            "Event Segmentation": 0,
            "User Search/Activity": 0,
            "User Deletion": 0,
            "Raw Data Export": 0,
            "Event Upload": 0,
            "Cohorts": 0,
            "Charts": 0,
            "Annotations": 0,
            "Releases": 0,
            "Taxonomy": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "segmentation" in name:
                resource_categories["Event Segmentation"] += 1
            elif "search" in name or "activity" in name:
                resource_categories["User Search/Activity"] += 1
            elif "deletion" in name:
                resource_categories["User Deletion"] += 1
            elif "export" in name:
                resource_categories["Raw Data Export"] += 1
            elif "upload" in name:
                resource_categories["Event Upload"] += 1
            elif "cohort" in name:
                resource_categories["Cohorts"] += 1
            elif "chart" in name:
                resource_categories["Charts"] += 1
            elif "annotation" in name:
                resource_categories["Annotations"] += 1
            elif "release" in name:
                resource_categories["Releases"] += 1
            elif "event_type" in name or "user_propert" in name or "event_propert" in name:
                resource_categories["Taxonomy"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for Amplitude data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Amplitude REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = AmplitudeDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Amplitude data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
