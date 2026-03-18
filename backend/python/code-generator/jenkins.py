# ruff: noqa
"""
Jenkins REST API Code Generator

Generates JenkinsDataSource class covering Jenkins REST API endpoints:
- Instance info and status
- Job management (info, build triggers, enable/disable)
- Build info and console output
- Build queue and nodes/agents
- Plugin management
- User info and views
- CSRF crumb issuer

The generated DataSource accepts a JenkinsClient and uses the client's
configured Jenkins URL as the base URL. All endpoints append /api/json
for JSON responses where applicable.

All methods have explicit parameter signatures with no **kwargs usage.

Usage:
    python code-generator/jenkins.py
    python code-generator/jenkins.py --filename jenkins.py

Output:
    app/sources/external/jenkins/jenkins.py
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# ================================================================================
# Jenkins API Endpoints - organized by resource
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which is the Jenkins instance URL)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#   required: List of required parameter names
# ================================================================================

JENKINS_API_ENDPOINTS = {
    # ================================================================================
    # INSTANCE INFO
    # ================================================================================
    "get_instance_info": {
        "method": "GET",
        "path": "/api/json",
        "description": "Get Jenkins instance information including jobs, views, and system status",
        "parameters": {
            "tree": {"type": "Optional[str]", "location": "query", "description": "Tree filter to limit response fields (e.g. 'jobs[name,url,color]')"},
            "depth": {"type": "Optional[int]", "location": "query", "description": "Depth of nested objects to return"},
        },
        "required": [],
    },

    # ================================================================================
    # CURRENT USER
    # ================================================================================
    "get_current_user": {
        "method": "GET",
        "path": "/me/api/json",
        "description": "Get the currently authenticated user information",
        "parameters": {
            "tree": {"type": "Optional[str]", "location": "query", "description": "Tree filter to limit response fields"},
            "depth": {"type": "Optional[int]", "location": "query", "description": "Depth of nested objects to return"},
        },
        "required": [],
    },

    # ================================================================================
    # USER INFO
    # ================================================================================
    "get_user": {
        "method": "GET",
        "path": "/user/{username}/api/json",
        "description": "Get information about a specific Jenkins user",
        "parameters": {
            "username": {"type": "str", "location": "path", "description": "The Jenkins username"},
            "tree": {"type": "Optional[str]", "location": "query", "description": "Tree filter to limit response fields"},
            "depth": {"type": "Optional[int]", "location": "query", "description": "Depth of nested objects to return"},
        },
        "required": ["username"],
    },

    # ================================================================================
    # JOBS
    # ================================================================================
    "get_job_info": {
        "method": "GET",
        "path": "/job/{job_name}/api/json",
        "description": "Get detailed information about a specific job",
        "parameters": {
            "job_name": {"type": "str", "location": "path", "description": "The job name (URL-encoded if contains slashes for folders)"},
            "tree": {"type": "Optional[str]", "location": "query", "description": "Tree filter to limit response fields"},
            "depth": {"type": "Optional[int]", "location": "query", "description": "Depth of nested objects to return"},
        },
        "required": ["job_name"],
    },
    "trigger_build": {
        "method": "POST",
        "path": "/job/{job_name}/build",
        "description": "Trigger a new build for a job without parameters",
        "parameters": {
            "job_name": {"type": "str", "location": "path", "description": "The job name"},
        },
        "required": ["job_name"],
    },
    "trigger_parameterized_build": {
        "method": "POST",
        "path": "/job/{job_name}/buildWithParameters",
        "description": "Trigger a new build for a parameterized job",
        "parameters": {
            "job_name": {"type": "str", "location": "path", "description": "The job name"},
            "parameters": {"type": "Optional[dict[str, Any]]", "location": "query", "description": "Build parameters as key-value pairs"},
        },
        "required": ["job_name"],
    },
    "disable_job": {
        "method": "POST",
        "path": "/job/{job_name}/disable",
        "description": "Disable a job to prevent new builds from being triggered",
        "parameters": {
            "job_name": {"type": "str", "location": "path", "description": "The job name"},
        },
        "required": ["job_name"],
    },
    "enable_job": {
        "method": "POST",
        "path": "/job/{job_name}/enable",
        "description": "Enable a previously disabled job",
        "parameters": {
            "job_name": {"type": "str", "location": "path", "description": "The job name"},
        },
        "required": ["job_name"],
    },

    # ================================================================================
    # BUILDS
    # ================================================================================
    "get_build_info": {
        "method": "GET",
        "path": "/job/{job_name}/{build_number}/api/json",
        "description": "Get detailed information about a specific build",
        "parameters": {
            "job_name": {"type": "str", "location": "path", "description": "The job name"},
            "build_number": {"type": "str", "location": "path", "description": "The build number"},
            "tree": {"type": "Optional[str]", "location": "query", "description": "Tree filter to limit response fields"},
            "depth": {"type": "Optional[int]", "location": "query", "description": "Depth of nested objects to return"},
        },
        "required": ["job_name", "build_number"],
    },
    "get_last_build": {
        "method": "GET",
        "path": "/job/{job_name}/lastBuild/api/json",
        "description": "Get information about the last build of a job",
        "parameters": {
            "job_name": {"type": "str", "location": "path", "description": "The job name"},
            "tree": {"type": "Optional[str]", "location": "query", "description": "Tree filter to limit response fields"},
            "depth": {"type": "Optional[int]", "location": "query", "description": "Depth of nested objects to return"},
        },
        "required": ["job_name"],
    },
    "get_last_successful_build": {
        "method": "GET",
        "path": "/job/{job_name}/lastSuccessfulBuild/api/json",
        "description": "Get information about the last successful build of a job",
        "parameters": {
            "job_name": {"type": "str", "location": "path", "description": "The job name"},
            "tree": {"type": "Optional[str]", "location": "query", "description": "Tree filter to limit response fields"},
            "depth": {"type": "Optional[int]", "location": "query", "description": "Depth of nested objects to return"},
        },
        "required": ["job_name"],
    },
    "get_build_console_output": {
        "method": "GET",
        "path": "/job/{job_name}/{build_number}/consoleText",
        "description": "Get the console output (build log) for a specific build as plain text",
        "parameters": {
            "job_name": {"type": "str", "location": "path", "description": "The job name"},
            "build_number": {"type": "str", "location": "path", "description": "The build number"},
        },
        "required": ["job_name", "build_number"],
    },

    # ================================================================================
    # BUILD QUEUE
    # ================================================================================
    "get_build_queue": {
        "method": "GET",
        "path": "/queue/api/json",
        "description": "Get the current build queue with all pending build items",
        "parameters": {
            "tree": {"type": "Optional[str]", "location": "query", "description": "Tree filter to limit response fields"},
            "depth": {"type": "Optional[int]", "location": "query", "description": "Depth of nested objects to return"},
        },
        "required": [],
    },

    # ================================================================================
    # NODES / AGENTS
    # ================================================================================
    "get_nodes": {
        "method": "GET",
        "path": "/computer/api/json",
        "description": "Get information about all nodes (build agents) including master",
        "parameters": {
            "tree": {"type": "Optional[str]", "location": "query", "description": "Tree filter to limit response fields"},
            "depth": {"type": "Optional[int]", "location": "query", "description": "Depth of nested objects to return"},
        },
        "required": [],
    },

    # ================================================================================
    # PLUGINS
    # ================================================================================
    "get_plugins": {
        "method": "GET",
        "path": "/pluginManager/api/json",
        "description": "Get information about all installed plugins",
        "parameters": {
            "tree": {"type": "Optional[str]", "location": "query", "description": "Tree filter to limit response fields (e.g. 'plugins[shortName,version,active]')"},
            "depth": {"type": "Optional[int]", "location": "query", "description": "Depth of nested objects to return"},
        },
        "required": [],
    },

    # ================================================================================
    # CSRF CRUMB
    # ================================================================================
    "get_crumb": {
        "method": "GET",
        "path": "/crumbIssuer/api/json",
        "description": "Get a CSRF crumb token required for POST requests on CSRF-protected Jenkins instances",
        "parameters": {},
        "required": [],
    },

    # ================================================================================
    # VIEWS
    # ================================================================================
    "get_view": {
        "method": "GET",
        "path": "/view/{view_name}/api/json",
        "description": "Get information about a specific view including its jobs",
        "parameters": {
            "view_name": {"type": "str", "location": "path", "description": "The view name"},
            "tree": {"type": "Optional[str]", "location": "query", "description": "Tree filter to limit response fields"},
            "depth": {"type": "Optional[int]", "location": "query", "description": "Depth of nested objects to return"},
        },
        "required": ["view_name"],
    },
}


class JenkinsDataSourceGenerator:
    """Generator for comprehensive Jenkins REST API datasource class.

    Generates methods for Jenkins API endpoints.
    The generated DataSource class accepts a JenkinsClient whose
    configured Jenkins URL is used as the base URL.
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
                elif "dict[" in param_info["type"]:
                    # For dict-type query params (like build parameters),
                    # merge them directly into query_params
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params.update({{str(k): str(v) for k, v in {sanitized_name}.items()}})",
                    ])
                elif "List[" in param_info["type"] or "list[" in param_info["type"]:
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
            inner = JenkinsDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = JenkinsDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                JenkinsDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = JenkinsDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                JenkinsDataSourceGenerator._modernize_type(p.strip()) for p in parts
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
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> JenkinsResponse:"

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
            "            JenkinsResponse with operation result",
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

        # Determine if this endpoint returns plain text (consoleText)
        is_text_response = "consoleText" in endpoint_info["path"]

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

        lines.append("            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]")

        if is_text_response:
            # Console output returns plain text, not JSON
            lines.extend([
                '            text_data = response.text() or ""',
                "            return JenkinsResponse(",
                "                success=response.status < HTTP_ERROR_THRESHOLD,",
                '                data={"console_output": text_data},',
                f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
                "            )",
            ])
        else:
            lines.extend([
                "            response_data = response.json() if response.text() else None",
                "            return JenkinsResponse(",
                "                success=response.status < HTTP_ERROR_THRESHOLD,",
                "                data=response_data,",
                f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
                "            )",
            ])

        lines.extend([
            "        except Exception as e:",
            f'            return JenkinsResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_jenkins_datasource(self) -> str:
        """Generate the complete Jenkins datasource class."""

        class_lines = [
            '"""',
            "Jenkins REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Jenkins REST API documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.http.http_request import HTTPRequest",
            "from app.sources.client.jenkins.jenkins import JenkinsClient, JenkinsResponse",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class JenkinsDataSource:",
            '    """Jenkins REST API DataSource',
            "",
            "    Provides async wrapper methods for Jenkins REST API operations:",
            "    - Instance info and system status",
            "    - Job management (info, triggers, enable/disable)",
            "    - Build info and console output",
            "    - Build queue management",
            "    - Node/agent monitoring",
            "    - Plugin management",
            "    - User info and views",
            "    - CSRF crumb retrieval",
            "",
            "    The base URL is the Jenkins instance URL configured in the JenkinsClient.",
            "",
            "    All methods return JenkinsResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: JenkinsClient) -> None:",
            '        """Initialize with JenkinsClient.',
            "",
            "        Args:",
            "            client: JenkinsClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'JenkinsDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> JenkinsClient:",
            '        """Return the underlying JenkinsClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in JENKINS_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Jenkins datasource to a file."""
        if filename is None:
            filename = "jenkins.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        jenkins_dir = script_dir.parent / "app" / "sources" / "external" / "jenkins"
        jenkins_dir.mkdir(parents=True, exist_ok=True)

        full_path = jenkins_dir / filename

        class_code = self.generate_jenkins_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Jenkins data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by category
        resource_categories = {
            "Instance": 0,
            "User": 0,
            "Job": 0,
            "Build": 0,
            "Queue": 0,
            "Node": 0,
            "Plugin": 0,
            "CSRF": 0,
            "View": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "instance" in name:
                resource_categories["Instance"] += 1
            elif "user" in name or "current" in name:
                resource_categories["User"] += 1
            elif "build" in name and "queue" not in name:
                resource_categories["Build"] += 1
            elif "job" in name or "trigger" in name or "disable" in name or "enable" in name:
                resource_categories["Job"] += 1
            elif "queue" in name:
                resource_categories["Queue"] += 1
            elif "node" in name:
                resource_categories["Node"] += 1
            elif "plugin" in name:
                resource_categories["Plugin"] += 1
            elif "crumb" in name:
                resource_categories["CSRF"] += 1
            elif "view" in name:
                resource_categories["View"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for Jenkins data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Jenkins REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = JenkinsDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Jenkins data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
