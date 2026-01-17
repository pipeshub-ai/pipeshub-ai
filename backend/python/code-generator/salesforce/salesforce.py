# ruff: noqa
"""
Salesforce API Code Generator

Generates a comprehensive SalesforceDataSource class from Postman collection JSON files.
This generator parses multiple Salesforce API collections including:
- Platform APIs (Core CRM, SOQL, SOSL, Composite, Bulk API)
- Commerce B2B/D2C APIs
- Commerce B2C APIs
- Marketing Cloud APIs
- Data 360 APIs (formerly Data Cloud)
- CRM Analytics Connect API
- Messaging for In-App and Web (MIAW) API

Usage:
    python crm.py
    python crm.py --out custom_output.py
"""

import json
import re
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field

# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
# Look for Postman collections in the postman folder (same directory as crm.py)
COLLECTION_DIR = SCRIPT_DIR / "postman"
OUTPUT_DIR = SCRIPT_DIR / "salesforce_generated"

# Postman collection files to process
POSTMAN_COLLECTIONS = [
    "Salesforce Commerce B2B-D2C.postman_collection.json",
    "Salesforce Platform APIs.postman_collection.json",
    "Salesforce Messaging for In-App and Web (MIAW) API.postman_collection.json",
    "Salesforce Marketing Cloud APIs.postman_collection.json",
    "Salesforce Data 360 APIs.postman_collection.json",
    "Salesforce CRM Analytics Connect API.postman_collection.json",
    "Salesforce Commerce B2C.postman_collection.json",
]

# Python reserved keywords to avoid
PYTHON_KEYWORDS = {
    "from", "in", "class", "global", "nonlocal", "for", "while", "if",
    "else", "elif", "try", "except", "finally", "def", "return", "import",
    "as", "with", "raise", "yield", "lambda", "pass", "break", "continue",
    "assert", "del", "not", "or", "and", "is", "async", "await", "type",
    "True", "False", "None", "id", "list", "dict", "set", "str", "int",
    "float", "bool", "bytes", "object", "filter", "map", "format"
}


# ------------------------------------------------------------
# DATA CLASSES
# ------------------------------------------------------------

@dataclass
class ParameterInfo:
    """Metadata for an API parameter."""
    name: str
    location: str  # 'path', 'query', 'header', 'body'
    required: bool
    description: str
    param_type: str = 'str'
    default_value: Optional[str] = None


@dataclass
class EndpointInfo:
    """Metadata for an API endpoint."""
    name: str
    method: str
    path: str
    description: str
    category: str = ""
    parameters: List[ParameterInfo] = field(default_factory=list)
    has_request_body: bool = False
    body_content_type: str = "application/json"


# ------------------------------------------------------------
# UTILITIES
# ------------------------------------------------------------

def sanitize_param_name(name: str) -> str:
    """Convert parameter name to safe Python identifier."""
    if not name:
        return "param"

    # Replace invalid characters with underscores
    s = re.sub(r"[^0-9a-zA-Z_]", "_", name)

    # Ensure doesn't start with digit
    if s and s[0].isdigit():
        s = "_" + s

    # Remove consecutive underscores
    s = re.sub(r"_+", "_", s).strip("_")

    # Handle Python keywords
    if s.lower() in PYTHON_KEYWORDS:
        s = s + "_param"

    return s.lower() or "param"


def to_snake_case(name: str) -> str:
    """Convert name to snake_case for method names."""
    if not name:
        return "method"

    # Handle camelCase and PascalCase
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)

    # Replace special characters
    s = s.replace("-", "_").replace(".", "_").replace(" ", "_")
    s = re.sub(r"[^0-9a-zA-Z_]", "_", s)
    s = re.sub(r"_+", "_", s).lower().strip("_")

    # Ensure doesn't start with digit
    if s and s[0].isdigit():
        s = "_" + s

    # Handle Python keywords
    if s in PYTHON_KEYWORDS:
        s = s + "_op"

    return s or "method"


def extract_path_params(path: str) -> List[str]:
    """Extract path parameter names from URL path."""
    # Match {{variable}} and :variable and {variable} patterns
    params = []

    # Postman style {{variable}}
    params.extend(re.findall(r"\{\{([^}]+)\}\}", path))

    # Express style :variable
    params.extend(re.findall(r":([a-zA-Z_][a-zA-Z0-9_]*)", path))

    # OpenAPI style {variable}
    params.extend(re.findall(r"\{([^}]+)\}", path))

    return list(set(params))


def normalize_path(path: str) -> str:
    """Normalize URL path for code generation."""
    # Convert {{variable}} to {variable}
    path = re.sub(r"\{\{([^}]+)\}\}", r"{\1}", path)

    # Convert :variable to {variable}
    path = re.sub(r":([a-zA-Z_][a-zA-Z0-9_]*)", r"{\1}", path)

    # Strip whitespace and newlines
    path = path.strip()

    return path


def normalize_path_params_in_path(path: str) -> str:
    """
    Convert all path parameters in the path to their sanitized (lowercase) form.
    E.g., {RECORD_ID} -> {record_id}, {userId} -> {userid}
    """
    def replace_param(match):
        param_name = match.group(1)
        safe_name = sanitize_param_name(param_name)
        return f"{{{safe_name}}}"

    # Replace {ANYTHING} with {sanitized_name}
    return re.sub(r"\{([^}]+)\}", replace_param, path)


def clean_description(desc: str) -> str:
    """Clean description text for docstrings."""
    if not desc:
        return ""

    # Remove markdown links
    desc = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", desc)

    # Remove HTML tags
    desc = re.sub(r"<[^>]+>", "", desc)

    # Normalize whitespace
    desc = " ".join(desc.split())

    # Truncate if too long
    if len(desc) > 200:
        desc = desc[:197] + "..."

    return desc


# ------------------------------------------------------------
# POSTMAN COLLECTION PARSER
# ------------------------------------------------------------

class PostmanCollectionParser:
    """Parser for Postman Collection v2.1 format."""

    def __init__(self, collection_path: Path):
        self.collection_path = collection_path
        self.collection_name = ""
        self.endpoints: List[EndpointInfo] = []

    def parse(self) -> List[EndpointInfo]:
        """Parse the Postman collection and extract endpoints."""
        try:
            with open(self.collection_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(f"Error reading {self.collection_path}: {e}")
            return []

        self.collection_name = data.get("info", {}).get("name", "Unknown")
        print(f"  Parsing: {self.collection_name}")

        # Parse items recursively
        items = data.get("item", [])
        self._parse_items(items, category="")

        return self.endpoints

    def _parse_items(self, items: List[Dict], category: str = ""):
        """Recursively parse items (folders and requests)."""
        for item in items:
            if not isinstance(item, dict):
                continue

            item_name = item.get("name", "")

            # Check if this is a folder (has sub-items)
            if "item" in item:
                # It's a folder - recurse with updated category
                new_category = f"{category}_{item_name}" if category else item_name
                new_category = to_snake_case(new_category)
                self._parse_items(item["item"], category=new_category)
            elif "request" in item:
                # It's a request - parse it
                endpoint = self._parse_request(item, category)
                if endpoint:
                    self.endpoints.append(endpoint)

    def _parse_request(self, item: Dict, category: str) -> Optional[EndpointInfo]:
        """Parse a single request item into an EndpointInfo."""
        request = item.get("request", {})
        if not isinstance(request, dict):
            return None

        # Get HTTP method
        method = request.get("method", "GET").upper()

        # Get URL
        url_data = request.get("url", {})
        if isinstance(url_data, str):
            path = url_data
            query_params = []
            path_variables = []
        else:
            # Build path from host and path components
            path_parts = url_data.get("path", [])
            if isinstance(path_parts, list):
                path = "/" + "/".join(str(p) for p in path_parts if p)
            else:
                path = str(path_parts)

            # Get query parameters
            query_params = url_data.get("query", []) or []

            # Get path variables
            path_variables = url_data.get("variable", []) or []

        # Skip if path is empty or just variables
        if not path or path == "/":
            return None

        # Generate method name from item name
        item_name = item.get("name", "")
        method_name = to_snake_case(item_name) if item_name else f"{method.lower()}_{to_snake_case(path)}"

        # Get description
        description = request.get("description", "") or item_name
        description = clean_description(description)

        # Parse parameters
        parameters = []

        # Add path parameters
        path_param_names = extract_path_params(path)
        for pv in path_variables:
            if isinstance(pv, dict):
                name = pv.get("key", pv.get("name", ""))
                if name:
                    path_param_names.append(name)

        for param_name in set(path_param_names):
            # Skip Postman environment variables (but NOT 'version' as it's a valid API version param)
            if param_name in ["orgUrl", "apiVersion", "baseUrl", "_endpoint", "HOST",
                             "loginUrl", "_dcTenantUrl", "scrt-url", "org-id", "developer-name",
                             "tenant", "public_client_id", "private_client_id", "SHORT_CODE",
                             "ORGANIZATION_ID", "shortCode", "organizationId"]:
                continue

            parameters.append(ParameterInfo(
                name=param_name,
                location="path",
                required=True,
                description=f"Path parameter: {param_name}",
                param_type="str"
            ))

        # Add query parameters
        for qp in query_params:
            if not isinstance(qp, dict):
                continue

            name = qp.get("key", "")
            if not name:
                continue

            # Skip disabled parameters by default (they're optional)
            is_disabled = qp.get("disabled", False)

            parameters.append(ParameterInfo(
                name=name,
                location="query",
                required=not is_disabled,
                description=qp.get("description", "") or f"Query parameter: {name}",
                param_type="str"
            ))

        # Check for request body
        body = request.get("body", {})
        has_body = False
        body_content_type = "application/json"

        if isinstance(body, dict) and body.get("mode"):
            has_body = True
            mode = body.get("mode", "raw")
            if mode == "raw":
                options = body.get("options", {})
                if isinstance(options, dict):
                    raw_opts = options.get("raw", {})
                    if isinstance(raw_opts, dict):
                        lang = raw_opts.get("language", "json")
                        if lang == "xml":
                            body_content_type = "application/xml"
                        elif lang == "text":
                            body_content_type = "text/plain"
            elif mode == "urlencoded":
                body_content_type = "application/x-www-form-urlencoded"
            elif mode == "formdata":
                body_content_type = "multipart/form-data"

        # Normalize the path
        normalized_path = normalize_path(path)

        return EndpointInfo(
            name=method_name,
            method=method,
            path=normalized_path,
            description=description,
            category=category,
            parameters=parameters,
            has_request_body=has_body,
            body_content_type=body_content_type
        )


# ------------------------------------------------------------
# CODE GENERATOR
# ------------------------------------------------------------

class SalesforceDataSourceGenerator:
    """Generates Python code for SalesforceDataSource class."""

    def __init__(self, endpoints: List[EndpointInfo]):
        self.endpoints = endpoints
        self.used_method_names: Set[str] = set()

    def generate(self) -> str:
        """Generate complete Python class code."""
        parts = [
            self._generate_header(),
            self._generate_imports(),
            self._generate_class()
        ]
        return "\n".join(parts)

    def _generate_header(self) -> str:
        """Generate file header."""
        return '''"""
AUTO-GENERATED SALESFORCE DATA SOURCE - DO NOT MODIFY MANUALLY

This module provides a comprehensive data source for Salesforce APIs including:
- Platform APIs (Core CRM, SOQL, SOSL, Composite, Bulk API v2)
- Commerce B2B/D2C APIs
- Commerce B2C APIs (SCAPI)
- Marketing Cloud APIs
- Data 360 APIs (formerly Data Cloud)
- CRM Analytics Connect API (Wave)
- Messaging for In-App and Web (MIAW) API

Generated from Salesforce Postman collections.
"""
'''

    def _generate_imports(self) -> str:
        """Generate import statements."""
        return '''from typing import Any, Dict, List, Optional, Union
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.salesforce.salesforce import SalesforceClient, SalesforceResponse
'''

    def _generate_class(self) -> str:
        """Generate the main class."""
        lines = [
            '',
            'class SalesforceDataSource:',
            '    """',
            '    Comprehensive Salesforce API Data Source.',
            '    ',
            '    Provides access to multiple Salesforce API domains:',
            '    - Core CRM (Accounts, Contacts, Leads, Opportunities, Cases)',
            '    - Query & Search (SOQL, SOSL)',
            '    - Composite API (Batch, Tree, Collections)',
            '    - Bulk API v2 (Large data operations)',
            '    - Commerce B2B/D2C (Webstores, Carts, Checkouts, Orders)',
            '    - Commerce B2C/SCAPI (Shopper APIs)',
            '    - Marketing Cloud (Campaigns, Journeys)',
            '    - Data 360 (Customer profiles, Metadata)',
            '    - CRM Analytics (Dashboards, Datasets, Dataflows)',
            '    - Messaging (Conversations, Messages)',
            '    """',
            '',
            '    def __init__(self, client: SalesforceClient):',
            '        """',
            '        Initialize the Salesforce data source.',
            '        ',
            '        Args:',
            '            client: SalesforceClient instance for authentication and requests',
            '        """',
            '        self.client = client.get_client()',
            '        self.base_url = client.get_base_url()',
            '',
        ]

        # Add helper methods
        lines.extend(self._generate_helper_methods())

        # Group endpoints by category
        categories: Dict[str, List[EndpointInfo]] = {}
        for ep in self.endpoints:
            cat = ep.category or "general"
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(ep)

        # Generate methods for each category
        for category in sorted(categories.keys()):
            lines.append(f'    # ========================================================================')
            lines.append(f'    # {category.upper().replace("_", " ")} ENDPOINTS')
            lines.append(f'    # ========================================================================')
            lines.append('')

            for endpoint in categories[category]:
                method_code = self._generate_method(endpoint)
                lines.append(method_code)
                lines.append('')

        return '\n'.join(lines)

    def _generate_helper_methods(self) -> List[str]:
        """Generate helper methods for the class."""
        return [
            '    def _build_url(self, path: str) -> str:',
            '        """Build full URL from path."""',
            '        # Remove leading slash if base_url already has trailing slash',
            '        if self.base_url.endswith("/") and path.startswith("/"):',
            '            path = path[1:]',
            '        return f"{self.base_url}{path}"',
            '',
            '    def _build_params(self, **kwargs) -> Dict[str, Any]:',
            '        """Build query parameters, filtering out None values."""',
            '        return {k: v for k, v in kwargs.items() if v is not None}',
            '',
            '    async def _execute_request(',
            '        self,',
            '        method: str,',
            '        path: str,',
            '        params: Optional[Dict[str, Any]] = None,',
            '        body: Optional[Any] = None,',
            '        content_type: str = "application/json"',
            '    ) -> SalesforceResponse:',
            '        """Execute an HTTP request and return SalesforceResponse."""',
            '        url = self._build_url(path)',
            '        ',
            '        headers = self.client.headers.copy()',
            '        if content_type:',
            '            headers["Content-Type"] = content_type',
            '        ',
            '        request = HTTPRequest(',
            '            method=method,',
            '            url=url,',
            '            headers=headers,',
            '            query_params=params or {},',
            '            body=body',
            '        )',
            '        ',
            '        try:',
            '            response = await self.client.execute(request)',
            '            # Handle various success status codes',
            '            if response.status < 300:',
            '                data = response.json() if response.text and response.status != 204 else {}',
            '                return SalesforceResponse(success=True, data=data)',
            '            else:',
            '                error_text = response.text if hasattr(response, "text") else str(response)',
            '                return SalesforceResponse(',
            '                    success=False,',
            '                    error=f"HTTP {response.status}",',
            '                    message=error_text',
            '                )',
            '        except Exception as e:',
            '            return SalesforceResponse(success=False, error=str(e))',
            '',
        ]

    def _generate_method(self, endpoint: EndpointInfo) -> str:
        """Generate a single API method."""
        # Get unique method name
        method_name = self._get_unique_method_name(endpoint.name)

        # Separate parameters by type and deduplicate
        path_params = [p for p in endpoint.parameters if p.location == "path"]
        query_params = [p for p in endpoint.parameters if p.location == "query"]
        required_query = [p for p in query_params if p.required]
        optional_query = [p for p in query_params if not p.required]

        # Deduplicate path parameters by sanitized name
        seen_params: Set[str] = set()
        unique_path_params = []
        for p in path_params:
            safe_name = sanitize_param_name(p.name)
            if safe_name not in seen_params:
                seen_params.add(safe_name)
                unique_path_params.append(p)
        path_params = unique_path_params

        # Deduplicate required query parameters
        unique_required_query = []
        for p in required_query:
            safe_name = sanitize_param_name(p.name)
            if safe_name not in seen_params:
                seen_params.add(safe_name)
                unique_required_query.append(p)
        required_query = unique_required_query

        # Deduplicate optional query parameters
        unique_optional_query = []
        for p in optional_query:
            safe_name = sanitize_param_name(p.name)
            if safe_name not in seen_params:
                seen_params.add(safe_name)
                unique_optional_query.append(p)
        optional_query = unique_optional_query

        # Build method signature - order matters! Required params first, then optional
        sig_parts = ["self"]

        # Add path parameters (always required)
        for p in path_params:
            safe_name = sanitize_param_name(p.name)
            sig_parts.append(f"{safe_name}: str")

        # Add required query parameters (before any optional params)
        for p in required_query:
            safe_name = sanitize_param_name(p.name)
            sig_parts.append(f"{safe_name}: str")

        # Add body parameter if needed (optional, so after required params)
        if endpoint.has_request_body:
            sig_parts.append("data: Optional[Dict[str, Any]] = None")

        # Add optional query parameters (last)
        for p in optional_query:
            safe_name = sanitize_param_name(p.name)
            sig_parts.append(f"{safe_name}: Optional[str] = None")

        # Format signature
        if len(sig_parts) > 3:
            sig_formatted = ",\n        ".join(sig_parts)
            signature = f"    async def {method_name}(\n        {sig_formatted}\n    ) -> SalesforceResponse:"
        else:
            signature = f"    async def {method_name}({', '.join(sig_parts)}) -> SalesforceResponse:"

        # Build docstring
        docstring = self._generate_docstring(endpoint, path_params, query_params)

        # Build method body
        body_lines = []

        # Build path with f-string for path parameters
        # Use normalize_path_params_in_path to convert ALL {PARAM} to {param}
        path = normalize_path_params_in_path(endpoint.path)

        body_lines.append(f'        path = f"{path}"')

        # Build query parameters dict using dict literal (to handle special chars like $)
        all_query_params = required_query + optional_query
        if all_query_params:
            param_items = []
            for p in all_query_params:
                safe_name = sanitize_param_name(p.name)
                # Use dict literal format with quoted key to handle special characters
                param_items.append(f'"{p.name}": {safe_name}')
            body_lines.append(f"        params = self._build_params(**{{{', '.join(param_items)}}})")
        else:
            body_lines.append("        params = None")

        # Determine body value
        if endpoint.has_request_body:
            body_lines.append("        body = data")
        else:
            body_lines.append("        body = None")

        # Execute request
        body_lines.append("")
        body_lines.append(f'        return await self._execute_request(')
        body_lines.append(f'            method="{endpoint.method}",')
        body_lines.append(f'            path=path,')
        body_lines.append(f'            params=params,')
        body_lines.append(f'            body=body,')
        body_lines.append(f'            content_type="{endpoint.body_content_type}"')
        body_lines.append(f'        )')

        # Combine all parts
        return f"{signature}\n{docstring}\n" + "\n".join(body_lines)

    def _generate_docstring(
        self,
        endpoint: EndpointInfo,
        path_params: List[ParameterInfo],
        query_params: List[ParameterInfo]
    ) -> str:
        """Generate method docstring."""
        lines = [f'        """{endpoint.description}']
        lines.append('')
        lines.append(f'        HTTP {endpoint.method}: {endpoint.path}')

        if path_params or query_params or endpoint.has_request_body:
            lines.append('')
            lines.append('        Args:')

            for p in path_params:
                safe_name = sanitize_param_name(p.name)
                desc = clean_description(p.description) or f"Path parameter"
                lines.append(f'            {safe_name}: {desc}')

            if endpoint.has_request_body:
                lines.append('            data: Request body data')

            for p in query_params:
                safe_name = sanitize_param_name(p.name)
                desc = clean_description(p.description) or f"Query parameter"
                req_str = "(required)" if p.required else "(optional)"
                lines.append(f'            {safe_name}: {desc} {req_str}')

        lines.append('')
        lines.append('        Returns:')
        lines.append('            SalesforceResponse with success status and data/error')
        lines.append('        """')

        return '\n'.join(lines)

    def _get_unique_method_name(self, base_name: str) -> str:
        """Get a unique method name, appending suffix if needed."""
        name = base_name
        counter = 1

        while name in self.used_method_names:
            name = f"{base_name}_{counter}"
            counter += 1

        self.used_method_names.add(name)
        return name


# ------------------------------------------------------------
# ADDITIONAL CORE SALESFORCE ENDPOINTS
# ------------------------------------------------------------

# These are essential Salesforce REST API endpoints that may not be in the
# Postman collections but are commonly needed for CRM operations

CORE_SALESFORCE_ENDPOINTS = [
    # Query & Search - Core SOQL/SOSL APIs
    EndpointInfo(
        name="soql_query",
        method="GET",
        path="/services/data/v{api_version}/query",
        description="Execute a SOQL query to retrieve records from Salesforce objects",
        category="core_crm_query",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version (e.g., 59.0)"),
            ParameterInfo(name="q", location="query", required=True, description="The SOQL query string"),
        ]
    ),
    EndpointInfo(
        name="soql_query_all",
        method="GET",
        path="/services/data/v{api_version}/queryAll",
        description="Execute a SOQL query including deleted and archived records",
        category="core_crm_query",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version (e.g., 59.0)"),
            ParameterInfo(name="q", location="query", required=True, description="The SOQL query string"),
        ]
    ),
    EndpointInfo(
        name="sosl_search",
        method="GET",
        path="/services/data/v{api_version}/search",
        description="Execute a SOSL search across multiple Salesforce objects",
        category="core_crm_query",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version (e.g., 59.0)"),
            ParameterInfo(name="q", location="query", required=True, description="The SOSL search string"),
        ]
    ),

    # SObject Metadata Operations
    EndpointInfo(
        name="sobject_describe_global",
        method="GET",
        path="/services/data/v{api_version}/sobjects",
        description="Lists all available Salesforce objects and their metadata",
        category="core_crm_metadata",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
        ]
    ),
    EndpointInfo(
        name="sobject_describe",
        method="GET",
        path="/services/data/v{api_version}/sobjects/{sobject}/describe",
        description="Describes the individual metadata for the specified Salesforce object",
        category="core_crm_metadata",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
            ParameterInfo(name="sobject", location="path", required=True, description="Object API name (e.g., Account)"),
        ]
    ),

    # SObject CRUD - Core record operations
    EndpointInfo(
        name="sobject_create",
        method="POST",
        path="/services/data/v{api_version}/sobjects/{sobject}",
        description="Create a new record for the specified Salesforce object",
        category="core_crm_crud",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
            ParameterInfo(name="sobject", location="path", required=True, description="Object API name"),
        ],
        has_request_body=True
    ),
    EndpointInfo(
        name="sobject_get",
        method="GET",
        path="/services/data/v{api_version}/sobjects/{sobject}/{record_id}",
        description="Retrieve a Salesforce record by ID",
        category="core_crm_crud",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
            ParameterInfo(name="sobject", location="path", required=True, description="Object API name"),
            ParameterInfo(name="record_id", location="path", required=True, description="Record ID"),
            ParameterInfo(name="fields", location="query", required=False, description="Comma-separated list of fields to return"),
        ]
    ),
    EndpointInfo(
        name="sobject_update",
        method="PATCH",
        path="/services/data/v{api_version}/sobjects/{sobject}/{record_id}",
        description="Update a Salesforce record by ID",
        category="core_crm_crud",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
            ParameterInfo(name="sobject", location="path", required=True, description="Object API name"),
            ParameterInfo(name="record_id", location="path", required=True, description="Record ID"),
        ],
        has_request_body=True
    ),
    EndpointInfo(
        name="sobject_delete",
        method="DELETE",
        path="/services/data/v{api_version}/sobjects/{sobject}/{record_id}",
        description="Delete a Salesforce record by ID",
        category="core_crm_crud",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
            ParameterInfo(name="sobject", location="path", required=True, description="Object API name"),
            ParameterInfo(name="record_id", location="path", required=True, description="Record ID"),
        ]
    ),
    EndpointInfo(
        name="sobject_upsert",
        method="PATCH",
        path="/services/data/v{api_version}/sobjects/{sobject}/{external_id_field}/{external_id}",
        description="Upsert a Salesforce record using an external ID field",
        category="core_crm_crud",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
            ParameterInfo(name="sobject", location="path", required=True, description="Object API name"),
            ParameterInfo(name="external_id_field", location="path", required=True, description="External ID field name"),
            ParameterInfo(name="external_id", location="path", required=True, description="External ID value"),
        ],
        has_request_body=True
    ),

    # Composite API - Batch and multi-record operations
    EndpointInfo(
        name="composite_batch_request",
        method="POST",
        path="/services/data/v{api_version}/composite/batch",
        description="Execute up to 25 subrequests in a single batch call",
        category="core_crm_composite",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
        ],
        has_request_body=True
    ),
    EndpointInfo(
        name="composite_tree_create",
        method="POST",
        path="/services/data/v{api_version}/composite/tree/{sobject}",
        description="Create a tree of related records in a single request",
        category="core_crm_composite",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
            ParameterInfo(name="sobject", location="path", required=True, description="Root object API name"),
        ],
        has_request_body=True
    ),
    EndpointInfo(
        name="collections_create",
        method="POST",
        path="/services/data/v{api_version}/composite/sobjects",
        description="Create up to 200 records in a single request",
        category="core_crm_composite",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
        ],
        has_request_body=True
    ),
    EndpointInfo(
        name="collections_update",
        method="PATCH",
        path="/services/data/v{api_version}/composite/sobjects",
        description="Update up to 200 records in a single request",
        category="core_crm_composite",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
        ],
        has_request_body=True
    ),
    EndpointInfo(
        name="collections_delete",
        method="DELETE",
        path="/services/data/v{api_version}/composite/sobjects",
        description="Delete up to 200 records in a single request",
        category="core_crm_composite",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
            ParameterInfo(name="ids", location="query", required=True, description="Comma-separated list of record IDs"),
            ParameterInfo(name="allOrNone", location="query", required=False, description="Roll back on any failure"),
        ]
    ),

    # Bulk API v2 - Large data operations
    EndpointInfo(
        name="bulk_v2_create_job",
        method="POST",
        path="/services/data/v{api_version}/jobs/ingest",
        description="Create a new Bulk API v2 ingest job for large data operations",
        category="core_crm_bulk",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
        ],
        has_request_body=True
    ),
    EndpointInfo(
        name="bulk_v2_get_job",
        method="GET",
        path="/services/data/v{api_version}/jobs/ingest/{job_id}",
        description="Get information about a Bulk API v2 job",
        category="core_crm_bulk",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
            ParameterInfo(name="job_id", location="path", required=True, description="Job ID"),
        ]
    ),
    EndpointInfo(
        name="bulk_v2_upload_data",
        method="PUT",
        path="/services/data/v{api_version}/jobs/ingest/{job_id}/batches",
        description="Upload CSV data to a Bulk API v2 job",
        category="core_crm_bulk",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
            ParameterInfo(name="job_id", location="path", required=True, description="Job ID"),
        ],
        has_request_body=True,
        body_content_type="text/csv"
    ),
    EndpointInfo(
        name="bulk_v2_close_job",
        method="PATCH",
        path="/services/data/v{api_version}/jobs/ingest/{job_id}",
        description="Close a Bulk API v2 job to begin processing",
        category="core_crm_bulk",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
            ParameterInfo(name="job_id", location="path", required=True, description="Job ID"),
        ],
        has_request_body=True
    ),
    EndpointInfo(
        name="bulk_v2_get_results",
        method="GET",
        path="/services/data/v{api_version}/jobs/ingest/{job_id}/successfulResults",
        description="Get successful results from a Bulk API v2 job",
        category="core_crm_bulk",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
            ParameterInfo(name="job_id", location="path", required=True, description="Job ID"),
        ]
    ),

    # System - Org info and limits
    EndpointInfo(
        name="org_limits",
        method="GET",
        path="/services/data/v{api_version}/limits",
        description="Get information about Salesforce organization limits",
        category="core_crm_system",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
        ]
    ),
    EndpointInfo(
        name="recent_items",
        method="GET",
        path="/services/data/v{api_version}/recent",
        description="Get recently viewed items for the current user",
        category="core_crm_system",
        parameters=[
            ParameterInfo(name="api_version", location="path", required=True, description="API version"),
            ParameterInfo(name="limit", location="query", required=False, description="Maximum number of items to return"),
        ]
    ),
]


# ------------------------------------------------------------
# MAIN EXECUTION
# ------------------------------------------------------------

def main():
    """Main function to generate Salesforce data source."""
    parser = argparse.ArgumentParser(
        description="Generate SalesforceDataSource from Postman collections"
    )
    parser.add_argument(
        "--out",
        default="salesforce_data_source.py",
        help="Output filename (default: salesforce_data_source.py)"
    )
    parser.add_argument(
        "--collections-dir",
        default=str(COLLECTION_DIR),
        help="Directory containing Postman collection JSON files"
    )
    args = parser.parse_args()

    print("Salesforce DataSource Generator")
    print("=" * 50)

    # Collect all endpoints
    all_endpoints: List[EndpointInfo] = []

    # Start with core Salesforce endpoints
    all_endpoints.extend(CORE_SALESFORCE_ENDPOINTS)
    print(f"Added {len(CORE_SALESFORCE_ENDPOINTS)} core Salesforce endpoints")

    # Parse each Postman collection
    collections_dir = Path(args.collections_dir)
    print(f"\nLooking for collections in: {collections_dir}")

    for collection_file in POSTMAN_COLLECTIONS:
        collection_path = collections_dir / collection_file

        if not collection_path.exists():
            print(f"  Skipping (not found): {collection_file}")
            continue

        parser_instance = PostmanCollectionParser(collection_path)
        endpoints = parser_instance.parse()
        all_endpoints.extend(endpoints)
        print(f"    Found {len(endpoints)} endpoints")

    print(f"\nTotal endpoints collected: {len(all_endpoints)}")

    # Generate the code
    print("\nGenerating SalesforceDataSource class...")
    generator = SalesforceDataSourceGenerator(all_endpoints)
    code = generator.generate()

    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / args.out

    # Write the file
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(code)

    # Print summary
    print(f"\nGeneration complete!")
    print(f"Output file: {output_path}")
    print(f"Total methods generated: {len(generator.used_method_names)}")

    # Group by category for summary
    categories: Dict[str, int] = {}
    for ep in all_endpoints:
        cat = ep.category or "general"
        categories[cat] = categories.get(cat, 0) + 1

    print("\nEndpoints by category:")
    for cat in sorted(categories.keys()):
        print(f"  - {cat}: {categories[cat]}")

    print(f"\nCopy the generated file to: app/sources/external/salesforce/")


if __name__ == "__main__":
    main()
