# ruff: noqa
#!/usr/bin/env python3
"""
Microsoft Enterprise Apps API Client Generator

Generates comprehensive API clients for Microsoft Graph services including:
- OneDrive, Outlook, SharePoint, Teams, Calendar, Contacts, and more
- Uses Microsoft Graph SDK client internally 
- Snake_case wrapper methods with proper typing
- Standardized response handling across all services

Usage:
    python microsoft_enterprise.py outlook
    python microsoft_enterprise.py sharepoint --version v1.0
    python microsoft_enterprise.py teams --out teams_client.py
    python microsoft_enterprise.py --service outlook --cleanup minimize
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import keyword
import logging
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union

# Set up logger
logger = logging.getLogger(__name__)

# Microsoft Graph OpenAPI specification URLs
DEFAULT_SPEC_URL = "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/master/openapi/v1.0/openapi.yaml"
FALLBACK_SPEC_URLS = [
    "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/main/openapi/v1.0/openapi.yaml",
]

# Service configurations for different Microsoft enterprise apps
MICROSOFT_SERVICES = {
    "outlook": {
        "class_name": "OutlookDataSource",
        "description": "Microsoft Outlook Mail and Calendar API",
        "path_filters": ["/me/messages", "/me/mailFolders", "/me/events", "/me/calendar", "/users/{id}/messages", "/users/{id}/mailFolders", "/users/{id}/events"],
        "scope_example": "https://graph.microsoft.com/mail.read",
        "sdk_base": "me().messages()",
    },
    "sharepoint": {
        "class_name": "SharePointDataSource", 
        "description": "Microsoft SharePoint API",
        "path_filters": ["/sites", "/me/followedSites", "/groups/{id}/sites"],
        "scope_example": "https://graph.microsoft.com/sites.read.all",
        "sdk_base": "sites()",
    },
    "teams": {
        "class_name": "TeamsDataSource",
        "description": "Microsoft Teams API", 
        "path_filters": ["/me/joinedTeams", "/teams", "/me/chats", "/chats", "/teamwork"],
        "scope_example": "https://graph.microsoft.com/team.readbasic.all",
        "sdk_base": "teams()",
    },
    "onedrive": {
        "class_name": "OneDriveDataSource",
        "description": "Microsoft OneDrive API",
        "path_filters": ["/me/drive", "/drives", "/users/{id}/drive", "/groups/{id}/drive"],
        "scope_example": "https://graph.microsoft.com/files.readwrite",
        "sdk_base": "me().drive()",
    },
    "calendar": {
        "class_name": "CalendarDataSource",
        "description": "Microsoft Calendar API",
        "path_filters": ["/me/calendar", "/me/calendars", "/me/events", "/users/{id}/calendar", "/users/{id}/calendars", "/users/{id}/events"],
        "scope_example": "https://graph.microsoft.com/calendars.readwrite", 
        "sdk_base": "me().calendar()",
    },
    "contacts": {
        "class_name": "ContactsDataSource",
        "description": "Microsoft Contacts API",
        "path_filters": ["/me/contacts", "/me/contactFolders", "/users/{id}/contacts", "/users/{id}/contactFolders"],
        "scope_example": "https://graph.microsoft.com/contacts.readwrite",
        "sdk_base": "me().contacts()",
    },
    "planner": {
        "class_name": "PlannerDataSource", 
        "description": "Microsoft Planner API",
        "path_filters": ["/planner", "/me/planner"],
        "scope_example": "https://graph.microsoft.com/tasks.readwrite",
        "sdk_base": "planner()",
    },
    "onenote": {
        "class_name": "OneNoteDataSource",
        "description": "Microsoft OneNote API", 
        "path_filters": ["/me/onenote", "/users/{id}/onenote", "/groups/{id}/onenote", "/sites/{id}/onenote"],
        "scope_example": "https://graph.microsoft.com/notes.readwrite",
        "sdk_base": "me().onenote()",
    },
}

# ---- Operation Model -------------------------------------------------------
@dataclass
class Operation:
    op_id: str
    http_method: str
    path: str
    summary: str
    description: str
    params: List[Mapping[str, object]]
    request_body: Optional[Mapping[str, object]] = None
    service_name: str = ""

    @property
    def wrapper_name(self) -> str:
        """Wrapper method name: Fully snake_case (lowercase)."""
        return to_snake(self.op_id)

# ---- Spec loading & parsing ------------------------------------------------
def _read_bytes_from_url(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:
        return resp.read()

def load_spec(*, spec_url: Optional[str] = None, spec_path: Optional[str] = None) -> Mapping[str, object]:
    """Load Microsoft Graph OpenAPI spec (JSON or YAML)."""
    if spec_path:
        data = Path(spec_path).read_bytes()
        try:
            return json.loads(data.decode("utf-8"))
        except Exception:
            try:
                import yaml
            except Exception as e:
                raise RuntimeError("Spec is not JSON and PyYAML is not installed") from e
            return yaml.safe_load(data)

    # Try URLs
    tried: List[str] = []
    url_candidates = [u for u in [spec_url or DEFAULT_SPEC_URL] if u] + FALLBACK_SPEC_URLS
    for url in url_candidates:
        tried.append(url)
        try:
            data = _read_bytes_from_url(url)
            try:
                return json.loads(data.decode("utf-8"))
            except Exception:
                try:
                    import yaml
                except Exception as e:
                    raise RuntimeError("Spec is not JSON and PyYAML is not installed") from e
                return yaml.safe_load(data)
        except Exception as e:
            logger.warning(f"Failed to load from {url}: {e}")
            continue

    raise RuntimeError(
        "Failed to load Microsoft Graph OpenAPI spec. Tried: " + ", ".join(tried)
    )

def extract_operations(
    spec: Mapping[str, object], 
    service_name: str,
    path_filters: Optional[List[str]] = None
) -> List[Operation]:
    """Extract operations from OpenAPI spec, filtering for service-specific paths."""
    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("Invalid OpenAPI spec: missing 'paths'")

    service_config = MICROSOFT_SERVICES.get(service_name, {})
    filters = path_filters or service_config.get("path_filters", [])

    ops: List[Operation] = []
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
            
        # Filter for service-specific paths
        if filters and not any(_path_matches_filter(path, filter_path) for filter_path in filters):
            continue
            
        # Exclude problematic paths
        if _is_excluded_path(path):
            continue
        
        for method, op in item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(op, Mapping):
                continue
                
            op_id = op.get("operationId")
            if not op_id:
                path_clean = path.strip("/").replace("/", "_").replace("{", "").replace("}", "").replace("-", "_")
                op_id = f"{method.lower()}_{path_clean}"
            
            # Merge parameters
            merged_params: List[Mapping[str, object]] = []
            for p in list(item.get("parameters", [])) + list(op.get("parameters", [])):
                if isinstance(p, Mapping):
                    merged_params.append(p)
            
            ops.append(
                Operation(
                    op_id=str(op_id),
                    http_method=method.upper(),
                    path=str(path),
                    summary=str(op.get("summary") or ""),
                    description=str(op.get("description") or ""), 
                    params=merged_params,
                    request_body=op.get("requestBody"),
                    service_name=service_name
                )
            )

    # Remove duplicates and sort
    uniq: Dict[str, Operation] = {}
    for op in ops:
        key = f"{op.op_id}_{op.http_method}"
        if key not in uniq:
            uniq[key] = op
    
    return sorted(uniq.values(), key=lambda o: o.op_id)

def _path_matches_filter(path: str, filter_path: str) -> bool:
    """Check if path matches the filter pattern."""
    # Handle parameter placeholders
    path_normalized = re.sub(r'\{[^}]+\}', '{id}', path)
    filter_normalized = re.sub(r'\{[^}]+\}', '{id}', filter_path)
    
    return path_normalized.startswith(filter_normalized) or path.startswith(filter_path)

def _is_excluded_path(path: str) -> bool:
    """Check if path should be excluded from generation."""
    excluded_patterns = [
        "/agreements", "/identityGovernance", "/privacy", "/security/alerts",
        "/reports", "/auditLogs", "/oauth2PermissionGrants", "/servicePrincipals",
        "/applications",  "/communications",
        "/workbook/worksheets/", "/sites/{site-id}/contentTypes", "/workbook"
    ]
    
    return any(pattern in path for pattern in excluded_patterns)

# ---- Code generation helpers ----------------------------------------------
_IDENT_RE = re.compile(r"[^0-9a-zA-Z_]")

def sanitize_py_name(name: str) -> str:
    """Sanitize name to valid Python identifier."""
    name = name.replace("$", "dollar_")
    name = name.replace("@", "at_")
    name = name.replace(".", "_")
    
    n = _IDENT_RE.sub("_", name)
    if n and n[0].isdigit():
        n = f"_{n}"
    if keyword.iskeyword(n):
        n += "_"
    if n.startswith("__"):
        n = f"_{n}"
    return n

def to_snake(name: str) -> str:
    """Convert a possibly camelCase/mixed name to snake_case (lower)."""
    name = name.replace("$", "dollar_")
    name = name.replace("@", "at_")
    name = name.replace(".", "_").replace("-", "_")
    
    out: List[str] = []
    for i, ch in enumerate(name):
        if ch.isupper():
            if i > 0 and (name[i-1].islower() or (i+1 < len(name) and name[i+1].islower())):
                out.append("_")
            out.append(ch.lower())
        else:
            out.append("_" if ch in "- " else ch)
    s = "".join(out)
    s = re.sub(r"__+", "_", s)
    return s.strip("_")

def param_sig_fragment(p: Mapping[str, object]) -> Tuple[str, str, str]:
    """Generate parameter signature fragment with proper typing."""
    api_name = str(p.get("name") or "param").strip()
    py_name = sanitize_py_name(api_name)
    
    # Get parameter type from schema
    schema = p.get("schema")
    param_type = "str"  # default
    if isinstance(schema, dict):
        schema_type = schema.get("type")
        if schema_type == "integer":
            param_type = "int" 
        elif schema_type == "boolean":
            param_type = "bool"
        elif schema_type == "array":
            param_type = "List[str]"
        elif schema_type == "object":
            param_type = "Dict[str, str]"
    
    default = "None" if not p.get("required") else "..."
    return py_name, api_name, default

def build_method_code(op: Operation) -> str:
    """Generate method code for an operation with proper typing."""
    required_parts: List[str] = []
    optional_parts: List[str] = []
    docs_param_lines: List[str] = []
    param_mapping_lines: List[str] = []
    
    seen: set[str] = set()
    for p in op.params:
        api_name = str(p.get("name") or "param")
        if api_name in seen:
            continue
        seen.add(api_name)

        py_name, api_name, default = param_sig_fragment(p)

        # Normalize generated param name (e.g. contactFolder_id -> contact_folder_id)
        norm_py_name = to_snake(py_name)

        # Get proper type
        param_type = _get_param_type(p)

        if default == "...":
            required_parts.append(f"{norm_py_name}: {param_type}")
        else:
            optional_parts.append(f"{norm_py_name}: Optional[{param_type}] = None")

        # Build mapping: support arrays -> CSV and hyphenated -> snake mirror
        mirror_key = sanitize_py_name(api_name)
        if _is_array_param(p) or _should_csv_param(api_name):
            param_mapping_lines.append(f"        if {norm_py_name} is not None:")
            param_mapping_lines.append(f"            _val = ','.join({norm_py_name}) if isinstance({norm_py_name}, list) else str({norm_py_name})")
            param_mapping_lines.append(f"            params['{api_name}'] = _val")
            if mirror_key != api_name:
                param_mapping_lines.append(f"            params['{mirror_key}'] = _val")
        else:
            param_mapping_lines.append(f"        if {norm_py_name} is not None:")
            param_mapping_lines.append(f"            params['{api_name}'] = {norm_py_name}")
            if mirror_key != api_name:
                param_mapping_lines.append(f"            params['{mirror_key}'] = {norm_py_name}")

        flag = "required" if default == "..." else "optional"
        desc = str(p.get("description") or "").replace("\n", " ")
        docs_param_lines.append(f"            {norm_py_name} ({flag}): {desc}")

    # Handle request body (broaden type to allow complex payloads)
    has_body = op.request_body is not None
    if has_body:
        optional_parts.append("request_body: Optional[Mapping[str, object]] = None")
        docs_param_lines.append("            request_body (optional): Request body data")

    # Format parameters for signature
    if required_parts or optional_parts:
        star = ["*"] if (required_parts or optional_parts) else []
        all_params = star + required_parts + optional_parts + ["**kwargs"]
        if len(all_params) <= 3:
            sig = ", ".join(all_params)
        else:
            params_formatted = ",\n        ".join(all_params)
            sig = f"\n        {params_formatted}\n    "
    else:
        sig = "**kwargs"

    summary = op.summary or op.op_id
    service_config = MICROSOFT_SERVICES.get(op.service_name, {})
    service_desc = service_config.get("description", "Microsoft Graph")
    params_doc = "\n".join(docs_param_lines) if docs_param_lines else "            (no parameters)"
    param_mapping = "\n".join(param_mapping_lines) if param_mapping_lines else "        # No parameters"

    # Ensure docstring lines end with a period
    method_line = f"{service_desc} method: {op.path} ({op.http_method})."

    # Generate Graph SDK method call
    graph_method_call = _generate_graph_call(op)
    response_type = "MSGraphResponse"

    method_code = f"""    def {op.wrapper_name}(self, {sig}) -> {response_type}:
        \"\"\"{summary}.

        {method_line}
        
        Args:
{params_doc}

        Returns:
            {response_type}: Standardized response wrapper
        \"\"\"
        params: Dict[str, object] = {{}}
{param_mapping}
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v
        
        try:
{graph_method_call}
            return self._handle_response(response)
        except Exception as e:
            return {response_type}(
                success=False,
                error=f"{service_desc} API call failed: {{str(e)}}",
            )
"""

    return method_code


def _is_array_param(p: Mapping[str, object]) -> bool:
    schema = p.get("schema")
    return isinstance(schema, dict) and schema.get("type") == "array"

def _should_csv_param(api_name: str) -> bool:
    # OData query options that must be comma-separated
    return api_name in ("$select", "$expand")

def _get_param_type(p: Mapping[str, object]) -> str:
    """Get proper Python type for parameter."""
    schema = p.get("schema")
    if not isinstance(schema, dict):
        return "str"
    
    schema_type = schema.get("type")
    if schema_type == "integer":
        return "int"
    elif schema_type == "boolean":
        return "bool" 
    elif schema_type == "array":
        items = schema.get("items", {})
        if isinstance(items, dict) and items.get("type") == "string":
            return "List[str]"
        return "List[Union[str, int]]"
    elif schema_type == "object":
        return "Dict[str, Union[str, int, bool]]"
    else:
        return "str"

def _generate_graph_call(op: Operation) -> str:
    """Generate Microsoft Graph SDK method call for the operation."""
    path = op.path
    method = op.http_method.lower()
    has_body = op.request_body is not None

    chain = _build_sdk_chain(path, op.service_name, MICROSOFT_SERVICES.get(op.service_name, {}))

    # Build call with params forwarded
    if method == "get":
        final_call = f"{chain}.get(params=params)"
    elif method == "delete":
        final_call = f"{chain}.delete(params=params)"
    elif method == "post":
        final_call = (
            f"{chain}.post(request_body or {{}}, params=params)"
            if has_body else f"{chain}.post(params=params)"
        )
    elif method == "patch":
        final_call = (
            f"{chain}.patch(request_body or {{}}, params=params)"
            if has_body else f"{chain}.patch(params=params)"
        )
    elif method == "put":
        final_call = (
            f"{chain}.put(request_body or {{}}, params=params)"
            if has_body else f"{chain}.put(params=params)"
        )
    else:
        final_call = f"{chain}.get(params=params)"

    # Auto-break long chains into multiple lines with type:ignore in first line
    # Format with 70-char first line + '# type:ignore' and then wrap so that
    # each subsequent line is <= 87 chars, breaking only at '.' boundaries.
    return _format_broken_call(final_call)

def _format_broken_call(call: str) -> str:
    """
    response = (
            self.client...  # type:ignore
            .next()...
        )
    First actual call line is capped at 70 chars (pre-comment) and ends with
    '# type:ignore'. Subsequent lines are indented and limited to <= 87 chars.
    """
    first_line = "            response = ("
    indent = " " * 12  # continuation indentation for call lines
    MAX_FIRST = 70     # chars before adding the comment on the first call line
    MAX_WIDTH = 87     # hard limit for all continuation lines (including dots)

# Split at '.' boundaries; keep tokens intact
    parts = call.split(".")
    lines: list[str] = []

    # --- Build the first actual call line (with '# type:ignore') ---
    if not parts:
        return "\n".join([first_line, indent + "# type:ignore", "            )"])

    # Start with 'self...' (no leading dot for the first call line)
    first_call = indent + parts[0]
    i = 1
    # Greedily append segments while staying <= MAX_FIRST before the comment
    while i < len(parts):
        seg = "." + parts[i]
        if len(first_call) + len(seg) <= MAX_FIRST:
            first_call += seg
            i += 1
        else:
            break
    # Add the ignore comment to the first call line
    first_call += "  # type:ignore"
    lines.append(first_call)


    # --- Append remaining segments on wrapped continuation lines ---
    if i < len(parts):
        current = indent + "." + parts[i]
        i += 1
    else:
        current = ""

    for idx in range(i, len(parts)):
        chunk = "." + parts[idx]
        if not current:
            current = indent + chunk
        elif len(current) + len(chunk) <= MAX_WIDTH:
            current += chunk
        else:
            lines.append(current)
            current = indent + chunk
    if current:
        lines.append(current)

    # Join into the final multiline string
    return "\n".join([first_line] + lines + ["            )"])

def _build_sdk_chain(path: str, service_name: str, service_config: Dict[str, object]) -> str:
    """Build the Microsoft Graph SDK method chain for the given path and service."""
    parts = [p for p in path.split("/") if p]
    
    # Service-specific base chains
    if service_name == "outlook":
        if any(part in path for part in ["messages", "mailFolders"]):
            base = "self.client.me().mail_folders()"
            start_idx = _find_start_index(parts, ["me", "messages", "mailFolders"])
        elif "events" in path or "calendar" in path:
            base = "self.client.me().calendar()"
            start_idx = _find_start_index(parts, ["me", "calendar", "events"])
        else:
            base = "self.client.me()"
            start_idx = _find_start_index(parts, ["me"])
    
    elif service_name == "sharepoint":
        if parts and parts[0] == "sites":
            base = "self.client.sites()"
            start_idx = 1
        else:
            base = "self.client.sites()"
            start_idx = 0
    
    elif service_name == "teams":
        if "chats" in path:
            base = "self.client.chats()"
            start_idx = _find_start_index(parts, ["chats"])
        elif "teams" in path or "joinedTeams" in path:
            base = "self.client.teams()" 
            start_idx = _find_start_index(parts, ["teams", "joinedTeams"])
        else:
            base = "self.client.me()"
            start_idx = _find_start_index(parts, ["me"])
    
    elif service_name == "onedrive":
        if parts[:2] == ["me", "drive"]:
            base = "self.client.me().drive()"
            start_idx = 2
        elif len(parts) >= 2 and parts[0] == "drives":
            base = "self.client.drives_by_id(params.get('drive_id', ''))"
            start_idx = 2
        else:
            base = "self.client.me().drive()"
            start_idx = 0
    
    elif service_name == "calendar":
        base = "self.client.me().calendar()"
        start_idx = _find_start_index(parts, ["me", "calendar", "calendars", "events"])
    
    elif service_name == "contacts":
        base = "self.client.me().contacts()"
        start_idx = _find_start_index(parts, ["me", "contacts", "contactFolders"])
    
    else:
        # Generic fallback
        base = "self.client.me()"
        start_idx = _find_start_index(parts, ["me"])

    # Build method chain from remaining parts
    chain_parts = _build_chain_parts(parts[start_idx:])
    full_chain = base + ("." + ".".join(chain_parts) if chain_parts else "")
    
    return full_chain

def _find_start_index(parts: List[str], prefixes: List[str]) -> int:
    """Find the starting index after matching any of the prefixes."""
    for i, part in enumerate(parts):
        if part in prefixes:
            return i + 1
    return 0

def _build_chain_parts(parts: List[str]) -> List[str]:
    """Build SDK method chain parts from path segments."""
    chain: List[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if part.startswith("{") and part.endswith("}"):
            param_name = part[1:-1].replace("-", "_")
            prev_part = parts[i-1] if i > 0 else ""
            if "id" in param_name or prev_part in {"items", "messages", "events", "sites"}:
                chain.append(f"by_id(params.get('{param_name}', ''))")
            i += 1
            continue
        
        # Map common path segments to SDK methods
        method_map = {
            "items": "items()",
            "children": "children()",
            "content": "content()",
            "messages": "messages()",
            "events": "events()",
            "calendars": "calendars()",
            "contacts": "contacts()",
            "sites": "sites()",
            "lists": "lists()",
            "drives": "drives()",
            "teams": "teams()",
            "channels": "channels()",
            "members": "members()",
        }
        
        if part in method_map:
            chain.append(method_map[part])
        elif part.startswith("$"):
            chain.append(part[1:] + "()")
        else:
            chain.append(to_snake(part) + "()")
        i += 1
    
    return chain

def build_class_code(class_name: str, service_name: str, ops: Sequence[Operation]) -> str:
    """Generate the complete Microsoft service client class."""
    methods = [build_method_code(o) for o in ops]
    service_config = MICROSOFT_SERVICES.get(service_name, {})
    service_desc = service_config.get("description", "Microsoft Graph Service")
    response_class = "MSGraphResponse"

    header = f"""
    # Auto-generated Microsoft {service_name.capitalize()} API client.
    # ruff: noqa: BLE001, D417, E501, ANN003, PGH003, PLR0912, C901, PLR0913, G004, TRY400, TRY003, EM101, D100, INP001, PLR0915, D401

import keyword as _kw
import logging
import re
from typing import Dict, List, Mapping, Optional, Union


from app.sources.client.microsoft.microsoft import MSGraphClient, MSGraphResponse

# Set up logger
logger = logging.getLogger(__name__)

class {class_name}:
    \"\"\"
    Auto-generated {service_desc} client wrapper.
    
    - Uses Microsoft Graph SDK client internally
    - Snake_case method names for all {service_desc} operations  
    - Standardized {response_class} format for all responses
    - No direct HTTP calls - all requests go through Graph SDK
    \"\"\"
    
    def __init__(self, client: MSGraphClient) -> None:
        \"\"\"Initialize with Microsoft Graph SDK client.\"\"\"
        self.client = client.get_client()
        if not hasattr(self.client, 'me'):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("{service_desc} client initialized successfully")

    def _sanitize_py_name(self, name: str) -> str:
        \"\"\"
        Runtime-safe version of sanitize_py_name for mirroring param keys.

        Turns '$select' -> 'dollar_select', 'message-id' -> 'message_id', etc.
        \"\"\"

        n = name.replace("$", "dollar_").replace("@", "at_").replace(".", "_")
        n = re.sub(r"[^0-9a-zA-Z_]", "_", n)
        if n and n[0].isdigit():
            n = f"_{{n}}"
        if _kw.iskeyword(n):
            n += "_"
        if n.startswith("__"):
            n = f"_{{n}}"
        return n
"""

    runtime_helpers = f"""
    def _handle_response(self, response: Dict[str, Union[str, int, bool, List[object]]]) -> MSGraphResponse:
        \"\"\"Handle Microsoft Graph API response.\"\"\"
        try:
            if response is None:
                    return MSGraphResponse(success=False, error="Empty response from Microsoft Graph",)
            
            success = True
            error_msg = None
            
            # Handle error responses
            if hasattr(response, 'error'):
                success = False
                error_msg = str(response.error) #type:ignore
            elif isinstance(response, dict) and 'error' in response:
                success = False
                error_msg = str(response['error'])
            elif hasattr(response, 'code') and hasattr(response, 'message'):
                success = False  
                error_msg = f"{{response.code}}: {{response.message}}" #type:ignore
            
            return MSGraphResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling {service_desc} response: {{e}}")
            return MSGraphResponse(success=False, error=str(e),)

    def get_data_source(self) -> '{class_name}':
        \"\"\"Get the underlying {service_desc} client.\"\"\"
        return self

"""

    return header + runtime_helpers + "\n" + "\n".join(methods) + "\n\n"

def clean_generated_code(code: str) -> str:
    """Clean up generated code by removing excessive whitespace."""
    lines = code.split('\n')
    cleaned_lines = []
    
    for line in lines:
        cleaned_line = line.rstrip()
        cleaned_lines.append(cleaned_line)
    
    # Remove excessive blank lines
    final_lines = []
    blank_count = 0
    
    for line in cleaned_lines:
        if line.strip() == "":
            blank_count += 1
            if blank_count <= 2:
                final_lines.append(line)
        else:
            blank_count = 0
            final_lines.append(line)
    
    # Clean start and end
    while final_lines and final_lines[0].strip() == "":
        final_lines.pop(0)
    
    while final_lines and final_lines[-1].strip() == "":
        final_lines.pop()
    
    if final_lines:
        final_lines.append("")
    
    return '\n'.join(final_lines)

def post_process_generated_file(file_path: str, cleanup_level: str = "clean") -> None:
    """Post-process the generated file to clean up whitespace and formatting."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            original_code = f.read()
        
        cleaned_code = clean_generated_code(original_code)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(cleaned_code)
        
        original_lines = len(original_code.split('\n'))
        cleaned_lines = len(cleaned_code.split('\n'))
        original_size = len(original_code)
        cleaned_size = len(cleaned_code)
        
        print(f"🧹 Code cleanup completed:")
        print(f"   • Lines: {original_lines} → {cleaned_lines} ({original_lines - cleaned_lines:+d})")
        print(f"   • Size: {original_size:,} → {cleaned_size:,} bytes ({original_size - cleaned_size:+d})")
        
    except Exception as e:
        print(f"⚠️  Warning: Could not clean up generated file: {e}")

# ---- Public entrypoints ----------------------------------------------------
def generate_microsoft_service_client(
    service_name: str,
    *,
    out_path: Optional[str] = None,
    spec_url: Optional[str] = None,
    spec_path: Optional[str] = None,
    path_filters: Optional[List[str]] = None,
    cleanup_level: str = "clean"
) -> str:
    """Generate Microsoft service client wrapper Python file. Returns its path."""
    if service_name not in MICROSOFT_SERVICES:
        raise ValueError(f"Unsupported service: {service_name}. Supported: {list(MICROSOFT_SERVICES.keys())}")
    
    service_config = MICROSOFT_SERVICES[service_name]
    class_name = service_config["class_name"]
    out_filename = out_path or f"{service_name}_client.py"
    
    print(f"🔥 Loading Microsoft Graph OpenAPI specification for {service_name.capitalize()}...")
    spec = load_spec(spec_url=spec_url, spec_path=spec_path)
    
    print(f"🔍 Extracting {service_name.capitalize()} operations from spec...")
    ops = extract_operations(spec, service_name, path_filters=path_filters)
    print(f"✅ Found {len(ops)} {service_name.capitalize()} operations")
    
    print("⚙️ Generating client code...")
    code = build_class_code(class_name, service_name, ops)
    
    # Create microsoft directory
    script_dir = Path(__file__).parent if __file__ else Path('.')
    microsoft_dir = script_dir / 'microsoft'
    microsoft_dir.mkdir(exist_ok=True)
    
    # Write file
    full_path = microsoft_dir / out_filename
    full_path.write_text(code, encoding="utf-8")
    print(f"💾 Saved {service_name.capitalize()} client to: {full_path}")
    
    # Clean up the generated file
    print("🧹 Cleaning up generated code...")
    post_process_generated_file(str(full_path), cleanup_level)
    
    return str(full_path)

def import_generated(path: str, symbol: str):
    """Import the generated module and return a symbol."""
    module_name = Path(path).stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        raise ImportError(f"Cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return getattr(module, symbol)

# ---- CLI & Main ------------------------------------------------------------
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Generate Microsoft Enterprise API clients from Microsoft Graph OpenAPI spec"
    )
    ap.add_argument(
        "service",
        nargs="?",
        choices=list(MICROSOFT_SERVICES.keys()),
        help="Microsoft service to generate (outlook, sharepoint, teams, onedrive, calendar, contacts, planner, onenote)"
    )
    ap.add_argument("--service", dest="service_alt", choices=list(MICROSOFT_SERVICES.keys()),
                    help="Alternative way to specify service")
    ap.add_argument("--out", help="Output .py file path")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--spec-url", default=None, help="Microsoft Graph OpenAPI spec URL")
    g.add_argument("--spec-path", help="Local spec file path (JSON/YAML)")
    ap.add_argument(
        "--filter-paths", nargs="*",
        help="Only include operations with these path prefixes"
    )
    ap.add_argument(
        "--cleanup", choices=["clean", "format", "minimize"], default="clean",
        help="Code cleanup level"
    )
    ap.add_argument("--list-services", action="store_true", help="List all supported services")
    ap.add_argument("--self-test", action="store_true", help="Run self-tests")
    ap.add_argument("--generate-all", action="store_true", help="Generate clients for all services")
    
    return ap.parse_args(argv)

def _self_tests() -> None:
    """Run basic self-tests."""
    test_paths = {
        "outlook": [
            "/me/messages",
            "/me/mailFolders/{id}/messages",
            "/me/events",
            "/me/calendar/events",
            "/users/{id}/messages"
        ],
        "sharepoint": [
            "/sites",
            "/sites/{site-id}/lists",
            "/sites/{site-id}/drives",
            "/me/followedSites"
        ],
        "teams": [
            "/me/joinedTeams",
            "/teams/{team-id}/channels",
            "/me/chats",
            "/chats/{chat-id}/messages"
        ],
        "onedrive": [
            "/me/drive/root/children",
            "/drives/{drive-id}/items/{item-id}",
            "/me/drive/items/{item-id}/content"
        ]
    }
    
    for service_name, paths in test_paths.items():
        service_config = MICROSOFT_SERVICES[service_name]
        filters = service_config.get("path_filters", [])
        
        matching_paths = []
        for path in paths:
            if any(_path_matches_filter(path, filter_path) for filter_path in filters):
                matching_paths.append(path)
        
        assert len(matching_paths) >= 2, f"Service {service_name} should match at least 2 paths"
        print(f"   ✅ {service_name.capitalize()}: {len(matching_paths)}/{len(paths)} paths matched")
    
    # Test snake case conversion
    assert to_snake("getMessages") == "get_messages"
    assert to_snake("createUploadSession") == "create_upload_session"
    assert to_snake("mailFolders.list") == "mail_folders_list"
    
    print("✅ Self-tests passed")

def _list_services() -> None:
    """List all supported Microsoft services."""
    print("🔧 Supported Microsoft Enterprise Services:")
    print()
    
    for service_name, config in MICROSOFT_SERVICES.items():
        print(f"  📌 {service_name}")
        print(f"     Class: {config['class_name']}")
        print(f"     Description: {config['description']}")
        print(f"     Scope Example: {config['scope_example']}")
        print(f"     Path Filters: {len(config.get('path_filters', []))} patterns")
        print()

def generate_all_services(
    *,
    spec_url: Optional[str] = None,
    spec_path: Optional[str] = None,
    cleanup_level: str = "clean"
) -> Dict[str, str]:
    """Generate clients for all supported Microsoft services."""
    print("🚀 Generating clients for all Microsoft Enterprise services...")
    
    results = {}
    for service_name in MICROSOFT_SERVICES:
        try:
            print(f"\n📦 Processing {service_name.capitalize()}...")
            path = generate_microsoft_service_client(
                service_name,
                spec_url=spec_url,
                spec_path=spec_path,
                cleanup_level=cleanup_level
            )
            results[service_name] = path
            print(f"✅ {service_name.capitalize()} client generated successfully")
        except Exception as e:
            print(f"❌ Failed to generate {service_name.capitalize()} client: {e}")
            results[service_name] = f"ERROR: {e}"
    
    return results

def main(argv: Optional[Sequence[str]] = None) -> None:
    """Main CLI entry point."""
    ns = _parse_args(argv)
    
    if ns.list_services:
        _list_services()
        return
        
    if ns.self_test:
        _self_tests()
        return
    
    if ns.generate_all:
        results = generate_all_services(
            spec_url=ns.spec_url,
            spec_path=ns.spec_path,
            cleanup_level=ns.cleanup
        )
        
        print(f"\n🎉 Batch generation completed!")
        success_count = sum(1 for v in results.values() if not v.startswith("ERROR"))
        print(f"📊 Successfully generated: {success_count}/{len(results)} clients")
        
        for service, result in results.items():
            status = "✅" if not result.startswith("ERROR") else "❌"
            print(f"   {status} {service.capitalize()}: {result}")
        return
    
    # Single service generation
    service = ns.service or ns.service_alt
    if not service:
        print("❌ Error: Must specify a service to generate")
        print("Use --list-services to see available services")
        sys.exit(1)
    
    print(f"🚀 Starting {service.capitalize()} API client generation...")
    
    try:
        out_path = generate_microsoft_service_client(
            service,
            out_path=ns.out,
            spec_url=ns.spec_url,
            spec_path=ns.spec_path,
            path_filters=ns.filter_paths,
            cleanup_level=ns.cleanup
        )
        
        service_config = MICROSOFT_SERVICES[service]
        print(f"\n🎉 {service.capitalize()} client generation completed!")
        print(f"🏗️  Generated class: {service_config['class_name']}")
        print(f"📄 Output file: {out_path}")
        
        print(f"\n📖 Usage example:")
        print(_get_usage_example(service, service_config))
        
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

def _get_usage_example(service_name: str, config: Dict[str, object]) -> str:
    """Generate usage example for the specific service."""
    class_name = config["class_name"]
    scope = config["scope_example"]
    
    examples = {
        "outlook": """
from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient
from microsoft.outlook_client import OutlookDataSource

# Set up authentication
credential = ClientSecretCredential(
    tenant_id="your-tenant-id",
    client_id="your-client-id", 
    client_secret="your-client-secret"
)

# Create Graph client
graph_client = GraphServiceClient(credential, ["https://graph.microsoft.com/.default"])

# Create Outlook client
outlook = OutlookDataSource(graph_client)

# Use generated methods
messages_response = outlook.get_me_messages(top=10)
if messages_response.success:
    print(f"Messages: {messages_response.data}")

events_response = outlook.get_me_events()
if events_response.success:
    print(f"Calendar events: {events_response.data}")
""",
        "sharepoint": """
from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient
from microsoft.sharepoint_client import SharePointDataSource

# Set up authentication
credential = ClientSecretCredential(
    tenant_id="your-tenant-id",
    client_id="your-client-id", 
    client_secret="your-client-secret"
)

# Create Graph client
graph_client = GraphServiceClient(credential, ["https://graph.microsoft.com/.default"])

# Create SharePoint client
sharepoint = SharePointDataSource(graph_client)

# Use generated methods
sites_response = sharepoint.get_sites()
if sites_response.success:
    print(f"Sites: {sites_response.data}")
""",
        "teams": """
from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient
from microsoft.teams_client import TeamsDataSource

# Set up authentication
credential = ClientSecretCredential(
    tenant_id="your-tenant-id",
    client_id="your-client-id", 
    client_secret="your-client-secret"
)

# Create Graph client
graph_client = GraphServiceClient(credential, ["https://graph.microsoft.com/.default"])

# Create Teams client
teams = TeamsDataSource(graph_client)

# Use generated methods
teams_response = teams.get_me_joined_teams()
if teams_response.success:
    print(f"Teams: {teams_response.data}")
"""
    }
    
    return examples.get(service_name, f"""
# Example usage for {class_name}:
from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient
from microsoft.{service_name}_client import {class_name}

credential = ClientSecretCredential(
    tenant_id="your-tenant-id",
    client_id="your-client-id", 
    client_secret="your-client-secret"
)

graph_client = GraphServiceClient(credential, ["{scope}"])
{service_name}_client = {class_name}(graph_client)

# Use the generated methods
response = {service_name}_client.some_method()
if response.success:
    print(f"Data: {{response.data}}")
""")

if __name__ == "__main__":
    main()