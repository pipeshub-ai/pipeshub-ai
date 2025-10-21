
"""
Figma API Code Generator

Generates a typed Figma API client based on the official Figma REST API specification.
"""

import argparse
import json
import keyword
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Dict,
    Generic,
    Optional,
    TypeVar,
)

_PY_RESERVED = set(keyword.kwlist) | {
    "from",
    "global",
    "async",
    "await",
    "None",
    "self",
    "cls",
}

T = TypeVar("T")


T = TypeVar('T')

@dataclass
class FigmaResponse(Generic[T]):
    """Standardized response wrapper for Figma API responses.

    Attributes:
        success: Whether the request was successful
        data: Response data if successful
        error: Error message if request failed
        status_code: HTTP status code of the response
        message: Optional additional message
    """

    success: bool = field(default_factory=bool)
    data: Optional[T] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary."""
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "status_code": self.status_code,
            "message": self.message,
        }

    def to_json(self) -> str:
        """Convert response to JSON string."""
        return json.dumps(self.to_dict(), default=str)


def _sanitize_name(name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if name[0].isdigit():
        name = f"_{name}"
    if name in _PY_RESERVED:
        return f"{name}_"
    return name


def _get_python_type(api_type: str) -> str:
    """Convert API type to Python type annotation."""
    type_map = {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "object": "Dict[str, Any]",
        "array": "List[Any]",
    }
    return type_map.get(api_type.lower(), "Any")


def _get_return_type(method: str) -> str:
    """Determine the return type based on the method name and path."""
    if "files" in method:
        if "images" in method:
            return "Dict[str, str]"  # Map of node IDs to image URLs
        return "Dict[str, Any]"  # File data
    elif "components" in method:
        return "List[Dict[str, Any]]"  # List of components
    elif "styles" in method:
        return "List[Dict[str, Any]]"  # List of styles
    return "Dict[str, Any]"  # Default to dict


# Figma API endpoints
FIGMA_ENDPOINTS = {
    "files": [
        {
            "name": "get_file",
            "method": "GET",
            "path": "/v1/files/{file_key}",
            "description": "Get a Figma file by its key",
            "parameters": [
                {
                    "name": "file_key",
                    "type": "string",
                    "required": True,
                    "location": "path",
                },
                {
                    "name": "version",
                    "type": "string",
                    "required": False,
                    "location": "query",
                },
                {
                    "name": "depth",
                    "type": "int",
                    "required": False,
                    "location": "query",
                },
                {
                    "name": "geometry",
                    "type": "string",
                    "required": False,
                    "location": "query",
                },
            ],
        },
        {
            "name": "get_file_nodes",
            "method": "GET",
            "path": "/v1/files/{file_key}/nodes",
            "description": "Get specific nodes from a Figma file",
            "parameters": [
                {
                    "name": "file_key",
                    "type": "string",
                    "required": True,
                    "location": "path",
                },
                {
                    "name": "ids",
                    "type": "list[str]",
                    "required": True,
                    "location": "query",
                },
                {
                    "name": "depth",
                    "type": "int",
                    "required": False,
                    "location": "query",
                },
                {
                    "name": "geometry",
                    "type": "string",
                    "required": False,
                    "location": "query",
                },
            ],
        },
        {
            "name": "get_file_images",
            "method": "GET",
            "path": "/v1/images/{file_key}",
            "description": "Get image URLs for nodes in a file",
            "parameters": [
                {
                    "name": "file_key",
                    "type": "string",
                    "required": True,
                    "location": "path",
                },
                {
                    "name": "ids",
                    "type": "list[str]",
                    "required": True,
                    "location": "query",
                },
                {
                    "name": "scale",
                    "type": "float",
                    "required": False,
                    "location": "query",
                },
                {
                    "name": "format",
                    "type": "str",
                    "required": False,
                    "location": "query",
                    "default": "png",
                },
            ],
        },
    ],
    "components": [
        {
            "name": "get_file_components",
            "method": "GET",
            "path": "/v1/files/{file_key}/components",
            "description": "Get components from a file",
            "parameters": [
                {
                    "name": "file_key",
                    "type": "string",
                    "required": True,
                    "location": "path",
                }
            ],
        },
        {
            "name": "get_team_components",
            "method": "GET",
            "path": "/v1/teams/{team_id}/components",
            "description": "Get all published components from a team",
            "parameters": [
                {
                    "name": "team_id",
                    "type": "string",
                    "required": True,
                    "location": "path",
                },
                {
                    "name": "page_size",
                    "type": "int",
                    "required": False,
                    "location": "query",
                    "default": 100,
                },
                {
                    "name": "after",
                    "type": "int",
                    "required": False,
                    "location": "query",
                },
            ],
        },
    ],
    "styles": [
        {
            "name": "get_file_styles",
            "method": "GET",
            "path": "/v1/files/{file_key}/styles",
            "description": "Get styles from a file",
            "parameters": [
                {
                    "name": "file_key",
                    "type": "string",
                    "required": True,
                    "location": "path",
                }
            ],
        },
        {
            "name": "get_team_styles",
            "method": "GET",
            "path": "/v1/teams/{team_id}/styles",
            "description": "Get all published styles from a team",
            "parameters": [
                {
                    "name": "team_id",
                    "type": "string",
                    "required": True,
                    "location": "path",
                },
                {
                    "name": "page_size",
                    "type": "int",
                    "required": False,
                    "location": "query",
                    "default": 100,
                },
                {
                    "name": "after",
                    "type": "int",
                    "required": False,
                    "location": "query",
                },
            ],
        },
    ],
}


def generate_method_signature(endpoint: Dict[str, Any]) -> str:
    """Generate a method signature from an endpoint definition."""
    params = ["self"]

    for param in endpoint.get("parameters", []):
        if param.get("required", True):
            param_name = _sanitize_name(param["name"])
            param_type = _get_python_type(param["type"])
            params.append(f"{param_name}: {param_type}")

    for param in endpoint.get("parameters", []):
        if not param.get("required", False):
            param_name = _sanitize_name(param["name"])
            param_type = _get_python_type(param["type"])
            default = param.get("default", "None")
            if isinstance(default, str) and default != "None":
                default = f'"{default}"'
            params.append(f"{param_name}: Optional[{param_type}] = {default}")

    return_type = _get_return_type(endpoint["name"])

    return f"async def {endpoint['name']}({', '.join(params)}) -> FigmaResponse[{return_type}]:"


def generate_method_docstring(endpoint: Dict[str, Any]) -> str:
    """Generate a docstring for a method."""
    doc = [f'    """{endpoint["description"]}.']

    if "parameters" in endpoint:
        doc.append("")
        doc.append("    Args:")
        for param in endpoint["parameters"]:
            param_name = _sanitize_name(param["name"])
            param_type = _get_python_type(param["type"])
            required = param.get("required", False)
            doc.append(
                f"        {param_name} ({param_type}): {param.get('description', '')}"
                + (" (required)" if required else " (optional)")
            )

    doc.append('    """')
    return "\n".join(f"    {line}" for line in doc)


def generate_method_body(endpoint: Dict[str, Any]) -> str:
    """Generate the method body for an endpoint."""
    path_params = [
        p for p in endpoint.get("parameters", []) if p.get("in") == "path"
    ]
    query_params = [
        p for p in endpoint.get("parameters", []) if p.get("in") == "query"
    ]

    lines = []

    # Handle path parameters
    path_vars = {p["name"]: _sanitize_name(p["name"]) for p in path_params}
    path = endpoint["path"].format(**path_vars)

    # Build query parameters
    if query_params:
        lines.append("        _query: Dict[str, Any] = {}")
        for param in query_params:
            param_name = _sanitize_name(param["name"])
            lines.append(f'        if {param_name} is not None:')
            lines.append(f'            _query["{param["name"]}"] = {param_name}')

    # Build headers
    lines.append("        _headers: Dict[str, Any] = dict(headers or {})")

    # Build path parameters
    if path_params:
        lines.append(f'        _path = {{{{"{path_params[0]["name"]}": {_sanitize_name(path_params[0]["name"])}}}}}')
    else:
        lines.append('        _path = {}')

    # Make the request
    lines.append(f'        url = f"{path}" if path.startswith("http") else f"{{self.base_url}}{path}"')
    lines.append("        ")
    lines.append("        req = HTTPRequest(")
    lines.append(f'            method="{endpoint["method"]}",')
    lines.append("            url=url,")
    lines.append("            headers=as_str_dict(_headers),")

    if path_params:
        lines.append("            path_params=as_str_dict(_path),")

    if query_params:
        lines.append("            query_params=as_str_dict(_query),")

    lines.append("            body=None,")
    lines.append("        )")
    lines.append("        ")
    lines.append("        resp = await self._client.execute(req)")
    lines.append(f'        return self._handle_response(resp, "{endpoint["operationId"]}")' if "operationId" in endpoint else
                '        return self._handle_response(resp, "unnamed_endpoint")')

    return "\n".join(lines)



def generate_figma_client() -> str:
    """Generate the complete Figma API client."""
    imports = [
        "from __future__ import annotations",
        "import logging",
        "from typing import Any, Dict, List, Optional, TypeVar, Generic, Type, cast, Union",
        "from dataclasses import dataclass, field",
        "from app.sources.client.http.http_request import HTTPRequest",
        "from app.sources.client.http.http_response import HTTPResponse",
        "from app.sources.client.utils.typing import as_str_dict",
        "from app.sources.client.http.http_client import HTTPClient",
        "import json",
        "",
        "T = TypeVar('T')",
        "",
        "@dataclass",
        "class FigmaResponse(Generic[T]):",
        '    """Standardized response wrapper for Figma API responses."""',
        "    success: bool",
        "    data: Optional[T] = None",
        "    error: Optional[str] = None",
        "    status_code: Optional[int] = None",
        "",
        "class FigmaClient:",
        '    """A typed client for the Figma API."""',
        "    ",
        '    def __init__(self, client: Any) -> None:',
        '        """Initialize the Figma client.',
        "        ",
        "        Args:",
        "            client: An instance of the HTTP client to use for requests",
        '        """',
        "        self.client = client",
        "        self.logger = logging.getLogger(__name__)",
        "",
        "    def _handle_response(",
        "        self, response: HTTPResponse, method_name: str",
        "    ) -> FigmaResponse[Any]:",
        '        """Handle API response and convert to FigmaResponse.',
        "        ",
        "        Args:",
        "            response: Raw HTTP response from the client",
        "            method_name: Name of the calling method for logging",
        "        ",
        "        Returns:",
        "            Standardized FigmaResponse",
        '        """',
        "        try:",
        "            if response.status_code >= 400:",
        "                error_msg = response.error or f'Request failed with status {response.status_code}'",
        "                self.logger.error(f'{method_name} failed: {error_msg}')",
        "                return FigmaResponse(",
        "                    success=False,",
        "                    error=error_msg,",
        "                    status_code=response.status_code",
        "                )",
        "            ",
        "            return FigmaResponse(",
        "                success=True,",
        "                data=response.data,",
        "                status_code=response.status_code",
        "            )",
        "        ",
        "        except Exception as e:",
        "            self.logger.error(f'Error in {method_name}: {str(e)}', exc_info=True)",
        "            return FigmaResponse(",
        "                success=False,",
        "                error=f'Unexpected error: {str(e)}',",
        "                status_code=response.status_code if hasattr(response, 'status_code') else 500",
        "            )",
        "",
        "    async def close(self) -> None:",
        '        """Close the HTTP client and release resources."""',
        "        if hasattr(self, 'client') and self.client:",
        "            await self.client.close()",
        "",
        "    async def __aenter__(self):",
        "        return self",
        "",
        "    async def __aexit__(self, exc_type, exc_val, exc_tb):",
        "        await self.close()",
        "",
        "    async def _make_request(",
        "        self,",
        "        method: str,",
        "        path: str,",
        "        params: Optional[Dict[str, Any]] = None,",
        "        data: Optional[Dict[str, Any]] = None,",
        "        headers: Optional[Dict[str, str]] = None,",
        "        max_retries: int = 3,",
        "        retry_delay: float = 1.0",
        "    ) -> HTTPResponse:",
        '        """Make an HTTP request to the Figma API with retry logic.',
        "        ",
        "        Args:",
        "            method: HTTP method (GET, POST, etc.)",
        "            path: API endpoint path",
        "            params: Query parameters",
        "            data: Request body",
        "            headers: HTTP headers",
        "            max_retries: Maximum number of retry attempts",
        "            retry_delay: Delay between retries in seconds",
        "        ",
        "        Returns:",
        "            HTTPResponse: The API response",
        "        ",
        "        Raises:",
        "            ValueError: If the request is invalid",
        "            RuntimeError: If maximum retries exceeded",
        '        """',
        "        if not hasattr(self, 'access_token') or not self.access_token:",
        '            raise ValueError("Missing Figma access token")',
        "        ",
        '        url = f"{self.base_url}{path}"',
        "        headers = headers or {}",
        '        headers["X-Figma-Token"] = self.access_token',
        "        ",
        "        # Convert params to strings and remove None values",
        "        params = {k: str(v) for k, v in (params or {}).items() if v is not None}",
        "        ",
        "        last_error = None",
        "        ",
        "        for attempt in range(max_retries + 1):",
        "            try:",
        "                request = HTTPRequest(",
        "                    method=method,",
        "                    url=url,",
        "                    params=params,",
        "                    json=data,",
        "                    headers=headers",
        "                )",
        '                self.logger.debug("Making %s request to %s (attempt %d/%d)"',
        "                               method, url, attempt + 1, max_retries + 1)",
        "                ",
        "                response = await self.http_client.send(request)",
        "                ",
        "                # Handle rate limiting (429 status code)",
        "                if response.status_code == 429:",
        "                    retry_after = float(response.headers.get('Retry-After', retry_delay))",
        "                    if attempt < max_retries:",
        "                        self.logger.warning(",
        '                            "Rate limited. Retrying after %.1f seconds...",',
        "                            retry_after",
        "                        )",
        "                        await asyncio.sleep(retry_after)",
        "                        continue",
        "                ",
        "                return response",
        "            except Exception as e:",
        "                last_error = e",
        "                if attempt < max_retries:",
        "                    await asyncio.sleep(retry_delay * (2 ** attempt))  # Exponential backoff",
        "        ",
        "        raise RuntimeError(",
        '            f"Failed after {max_retries + 1} attempts. Last error: {{str(last_error)}}"',
        "        ) from last_error",
        "",
        "    # ... rest of the client implementation ...",
    ]

    for category, endpoints in FIGMA_ENDPOINTS.items():
        imports.append(f"\n    # {category.upper()} methods")
        for endpoint in endpoints:
            imports.extend(
                [
                    "",
                    "    " + generate_method_signature(endpoint),
                    generate_method_docstring(endpoint),
                    generate_method_body(endpoint),
                    "",
                ]
            )

    return "\n".join(imports)


def write_to_file(content: str, path: Path) -> None:
    """Write generated code to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Generated {path}")


def main() -> None:
    """Main entry point for the code generator."""
    parser = argparse.ArgumentParser(description="Generate Figma API client")
    parser.add_argument(
        "--output",
        type=Path,
        default="generated/figma_client.py",
        help="Output file path (default: generated/figma_client.py)",
    )
    args = parser.parse_args()

    client_code = generate_figma_client()
    write_to_file(client_code, args.output)


if __name__ == "__main__":
    main()
