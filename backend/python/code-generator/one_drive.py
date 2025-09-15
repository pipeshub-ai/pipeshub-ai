# ruff: noqa
#!/usr/bin/env python3
"""
=============================================================================
🔧 EASY MODIFICATION GUIDE:
To exclude more APIs, add keywords to EXCLUDED_KEYWORDS or paths to EXCLUDED_PATHS 
Search for "EXCLUSION CONFIGURATION" section below to find the lists
=============================================================================
"""
"""
Microsoft OneDrive API Client Generator (Basic Operations Only)

Generates a simplified OneDrive API client with basic endpoint coverage for:
- Personal OneDrive (/me/drive)
- User OneDrive (/users/{user-id}/drive) 
- Group OneDrive (/groups/{group-id}/drive)
- SharePoint Document Libraries (/sites/{site-id}/drive, /sites/{site-id}/drives/{drive-id})
- File and folder operations (read, list, copy, move, search, thumbnails, etc.)
- File versioning and restore
- Delta changes and synchronization

Explicitly excludes:
- Chat operations (/chats endpoints)
- Upload operations (/content, /createUploadSession) 
- Sharing operations (/permissions, /invite)
- Workbook operations (/workbook)
- Onenote operations (/onenote)
- Device App Management operations (/deviceAppManagement)
- Device Management operations (/deviceManagement)
- Connections operations (/connections)
- Identity Governance operations (/identityGovernance)
- Analytics operations (/analytics)
- Mail Folders operations (/mailFolders)
- Storage operations (/storage)
- Admin operations (/admin)
- Agreements operations (/agreements)
- Security operations (/security)
- Directory operations (/directory)
- Solutions operations (/solutions)
- Onenote operations (/onenote)
- Non-file related Microsoft Graph endpoints

EASY MODIFICATION:
To add/remove excluded APIs, modify the EXCLUDED_KEYWORDS and EXCLUDED_PATHS lists at the top of this file.

Enhanced Features:
- Basic OneDrive endpoint coverage with all path parameters
- Proper parameter extraction for drive-id, item-id, user-id, group-id, site-id
- Simple method signatures with required and optional parameters
- Microsoft Graph SDK integration optimized for OneDrive operations
- Specialized OneDrive response handling and error management
"""
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
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union, Any

# =============================================================================

# Set up logger
logger = logging.getLogger(__name__)
# EXCLUSION CONFIGURATION - Modify this section to add/remove excluded APIs
# =============================================================================

# Keywords to exclude from OneDrive operations (ADD MORE HERE TO EXCLUDE)
EXCLUDED_KEYWORDS = [
    'chat',           # Exclude all chat operations
    'upload',         # Exclude upload operations (/content, /createUploadSession)
    'content',        # Exclude content upload operations  
    'createsession',  # Exclude upload session creation
    'permission',     # Exclude sharing/permissions operations
    'invite',         # Exclude invitation operations
    'workbook',       # Exclude Excel workbook operations
    'worksheet',      # Exclude Excel worksheet operations
    'contactFolders',
    'settings',
    'todo',
    'deviceAppManagement',
    'deviceManagement',
    'connections',
    'identityGovernance',
    'analytics',
    'mailFolders',
    'storage',
    'admin',
    'agreements',
    'security',
    'directory',
    'solutions',
    'onenote'
]

# Path patterns to exclude (ADD MORE PATTERNS HERE TO EXCLUDE)
EXCLUDED_PATHS = [
    '/chats',
    '/content',
    '/createUploadSession', 
    '/permissions',
    '/invite',
    '/workbook',
    '/worksheets',
    '/contactFolders',
    '/settings',
    '/todo',
    '/deviceAppManagement',
    '/deviceManagement',
    '/connections',
    '/identityGovernance',
    '/analytics',
    '/mailFolders',
    '/storage',
    '/admin',
    '/agreements',
    '/security',
    '/directory',
    '/solutions',
    '/onenote'
]

# =============================================================================

# Microsoft Graph OpenAPI specification URLs
DEFAULT_SPEC_URL = "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/master/openapi/v1.0/openapi.yaml"
FALLBACK_SPEC_URLS = [
    "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/main/openapi/v1.0/openapi.yaml",
]

# Comprehensive OneDrive endpoint patterns (MODIFY THIS LIST TO ADD/REMOVE ENDPOINTS)
ONEDRIVE_PATH_PATTERNS = [
    # Personal OneDrive - Basic Operations
    "/me/drive",
    "/me/drive/root",
    "/me/drive/items/{item-id}",
    "/me/drive/root/children",
    "/me/drive/items/{item-id}/children",
    "/me/drive/root:/item-path:",
    "/me/drive/root:/item-path:/children",
    "/me/drive/items/{item-id}/thumbnails",
    "/me/drive/items/{item-id}/versions",
    "/me/drive/items/{item-id}/analytics",
    "/me/drive/items/{item-id}/preview",
    "/me/drive/items/{item-id}/copy",
    "/me/drive/items/{item-id}/move",
    "/me/drive/items/{item-id}/restore",
    "/me/drive/items/{item-id}/delta",
    "/me/drive/search(q='{search-text}')",
    "/me/drive/sharedWithMe",
    "/me/drive/recent",
    "/me/drive/following",
    
    # User OneDrive - Basic Operations
    "/users/{user-id}/drive",
    "/users/{user-id}/drive/root",
    "/users/{user-id}/drive/items/{item-id}",
    "/users/{user-id}/drive/root/children", 
    "/users/{user-id}/drive/items/{item-id}/children",
    "/users/{user-id}/drive/root:/item-path:",
    "/users/{user-id}/drive/root:/item-path:/children",
    "/users/{user-id}/drive/items/{item-id}/thumbnails",
    "/users/{user-id}/drive/items/{item-id}/versions",
    "/users/{user-id}/drive/search(q='{search-text}')",
    "/users/{user-id}/drive/sharedWithMe",
    "/users/{user-id}/drive/recent",
    
    # Group OneDrive - Basic Operations
    "/groups/{group-id}/drive", 
    "/groups/{group-id}/drive/root",
    "/groups/{group-id}/drive/items/{item-id}",
    "/groups/{group-id}/drive/root/children",
    "/groups/{group-id}/drive/items/{item-id}/children",
    "/groups/{group-id}/drive/root:/item-path:",
    "/groups/{group-id}/drive/root:/item-path:/children",
    "/groups/{group-id}/drive/items/{item-id}/thumbnails",
    "/groups/{group-id}/drive/items/{item-id}/versions",
    "/groups/{group-id}/drive/search(q='{search-text}')",
    
    # SharePoint Drives - Basic Operations
    "/sites/{site-id}/drive",
    "/sites/{site-id}/drive/root", 
    "/sites/{site-id}/drive/items/{item-id}",
    "/sites/{site-id}/drive/root/children",
    "/sites/{site-id}/drive/items/{item-id}/children",
    "/sites/{site-id}/drive/root:/item-path:",
    "/sites/{site-id}/drive/root:/item-path:/children",
    "/sites/{site-id}/drive/items/{item-id}/thumbnails",
    "/sites/{site-id}/drive/items/{item-id}/versions",
    "/sites/{site-id}/drive/search(q='{search-text}')",
    "/sites/{site-id}/drives",
    "/sites/{site-id}/drives/{drive-id}",
    "/sites/{site-id}/drives/{drive-id}/root",
    "/sites/{site-id}/drives/{drive-id}/items/{item-id}",
    "/sites/{site-id}/drives/{drive-id}/root/children",
    "/sites/{site-id}/drives/{drive-id}/items/{item-id}/children",
    "/sites/{site-id}/drives/{drive-id}/root:/item-path:",
    "/sites/{site-id}/drives/{drive-id}/root:/item-path:/children", 
    "/sites/{site-id}/drives/{drive-id}/items/{item-id}/thumbnails",
    "/sites/{site-id}/drives/{drive-id}/items/{item-id}/versions",
    "/sites/{site-id}/drives/{drive-id}/search(q='{search-text}')",
    
    # Generic drives - Basic Operations
    "/drives",
    "/drives/{drive-id}",
    "/drives/{drive-id}/root",
    "/drives/{drive-id}/items/{item-id}",
    "/drives/{drive-id}/root/children",
    "/drives/{drive-id}/items/{item-id}/children", 
    "/drives/{drive-id}/root:/item-path:",
    "/drives/{drive-id}/root:/item-path:/children",
    "/drives/{drive-id}/items/{item-id}/thumbnails",
    "/drives/{drive-id}/items/{item-id}/versions",
    "/drives/{drive-id}/items/{item-id}/analytics",
    "/drives/{drive-id}/items/{item-id}/preview",
    "/drives/{drive-id}/items/{item-id}/copy",
    "/drives/{drive-id}/items/{item-id}/move", 
    "/drives/{drive-id}/items/{item-id}/restore",
    "/drives/{drive-id}/items/{item-id}/delta",
    "/drives/{drive-id}/search(q='{search-text}')",
    "/drives/{drive-id}/sharedWithMe",
    "/drives/{drive-id}/recent",
    "/drives/{drive-id}/following",
    
    # NOTE: Excluded patterns (upload, sharing, workbook) are filtered out by EXCLUDED_KEYWORDS and EXCLUDED_PATHS
    # To re-enable them, remove the keywords from the exclusion lists above
]

# ---- Operation Model -------------------------------------------------------
@dataclass
class OneDriveOperation:
    op_id: str
    http_method: str
    path: str
    summary: str
    description: str
    params: List[Mapping[str, Any]]
    request_body: Optional[Mapping[str, Any]] = None
    path_params: List[Dict[str, str]] = None
    operation_type: str = "general"  # general, file, folder, sharing, upload, workbook

    @property
    def wrapper_name(self) -> str:
        """Wrapper method name: Fully snake_case (lowercase)."""
        return to_snake(self.op_id)

    def __post_init__(self):
        if self.path_params is None:
            self.path_params = []
        # Determine operation type based on path and operation
        self.operation_type = self._determine_operation_type()

    def _determine_operation_type(self) -> str:
        """Determine the type of OneDrive operation for better organization."""
        path_lower = self.path.lower()
        op_lower = self.op_id.lower()
        
        if "workbook" in path_lower:
            return "workbook"
        elif any(keyword in path_lower for keyword in ["upload", "content", "createsession"]):
            return "upload"
        elif any(keyword in path_lower for keyword in ["permission", "invite", "createlink", "share"]):
            return "sharing"
        elif any(keyword in path_lower for keyword in ["thumbnail", "preview", "analytics"]):
            return "metadata"
        elif any(keyword in op_lower for keyword in ["create", "mkdir", "newfolder"]):
            return "folder"
        elif any(keyword in path_lower for keyword in ["search", "delta", "recent", "following"]):
            return "discovery"
        else:
            return "file"

# ---- Spec loading & parsing ------------------------------------------------
def _read_bytes_from_url(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:
        return resp.read()

def load_spec(*, spec_url: Optional[str] = None, spec_path: Optional[str] = None) -> Mapping[str, Any]:
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

def extract_path_parameters(path: str) -> List[Dict[str, str]]:
    """Extract path parameters from OneDrive path pattern."""
    import re
    
    # Find all {parameter} patterns
    pattern = r'\{([^}]+)\}'
    matches = re.findall(pattern, path)
    
    path_params = []
    for match in matches:
        # Clean up parameter name and convert to Python-friendly format
        param_name = match.replace('-', '_').replace('.', '_')
        
        # Determine parameter type based on common OneDrive patterns
        param_type = 'str'
        description = f'OneDrive path parameter: {match}'
        
        if 'id' in match.lower():
            description = f'OneDrive {match.replace("-", " ").replace("_", " ")} identifier'
        elif 'path' in match.lower():
            description = f'OneDrive item path: {match}'
        elif 'text' in match.lower():
            description = f'Search text: {match}'
        
        path_params.append({
            'name': param_name,
            'original': match,
            'type': param_type,
            'required': True,
            'description': description
        })
    
    return path_params

def _is_onedrive_operation(path: str, op_id: str) -> bool:
    """Check if this operation is related to OneDrive/Files.
    
    MODIFICATION POINT: Update EXCLUDED_KEYWORDS and EXCLUDED_PATHS lists above to exclude more APIs
    """
    path_lower = path.lower()
    op_lower = op_id.lower()
    
    # ============ EXCLUSION CHECK (MODIFY EXCLUDED_KEYWORDS LIST ABOVE) ============
    # Exclude operations based on keywords
    for keyword in EXCLUDED_KEYWORDS:
        if keyword in path_lower or keyword in op_lower:
            return False
    
    # Exclude operations based on path patterns  
    for excluded_path in EXCLUDED_PATHS:
        if excluded_path.lower() in path_lower:
            return False
    # ===============================================================================
    
    # Include operations that are clearly OneDrive/files related (CORE ONEDRIVE KEYWORDS)
    onedrive_core_keywords = [
        'drive', 'drives', 'item', 'items', 'file', 'folder',
        'thumbnail', 'version', 'following', 'recent'
    ]
    
    return any(keyword in path_lower or keyword in op_lower for keyword in onedrive_core_keywords)

def _is_count_operation(path: str, op_id: str, summary: str, description: str) -> bool:
    """Check if this is a count operation that should be excluded."""
    # Check path patterns
    if path.endswith('/$count') or '/$count/' in path or path.endswith('/count'):
        return True
    
    # Check operation ID
    if 'count' in op_id.lower():
        return True
    
    # Check summary and description
    summary_lower = summary.lower()
    description_lower = description.lower()
    
    count_indicators = [
        'count of', 'get count', 'retrieve count', 'return count',
        'number of', 'total count', 'item count'
    ]
    
    return any(indicator in summary_lower or indicator in description_lower 
              for indicator in count_indicators)

def extract_onedrive_operations(spec: Mapping[str, Any]) -> List[OneDriveOperation]:
    """Extract OneDrive-specific operations from OpenAPI spec."""
    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("Invalid OpenAPI spec: missing 'paths'")

    ops: List[OneDriveOperation] = []
    skipped_count_ops = 0
    skipped_non_onedrive = 0
    
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
            
        # Check if path matches any OneDrive patterns
        if not any(_path_matches_pattern(path, pattern) for pattern in ONEDRIVE_PATH_PATTERNS):
            # Additional check for OneDrive-related operations
            if not _is_onedrive_operation(path, ""):
                skipped_non_onedrive += 1
                continue
        
        # ============ EXCLUSION CHECK (MODIFY EXCLUDED_PATHS LIST ABOVE) ============
        # Explicitly exclude paths based on exclusion list
        should_exclude = False
        for excluded_path in EXCLUDED_PATHS:
            if excluded_path.lower() in path.lower():
                should_exclude = True
                break
        
        if should_exclude:
            skipped_non_onedrive += 1
            continue
        # ============================================================================
        
        # Extract path parameters
        path_params = extract_path_parameters(path)
        
        for method, op in item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(op, Mapping):
                continue
                
            op_id = op.get("operationId")
            if not op_id:
                path_clean = path.strip("/").replace("/", "_").replace("{", "").replace("}", "").replace("-", "_")
                op_id = f"{method.lower()}_{path_clean}"
            
            summary = str(op.get("summary") or "")
            description = str(op.get("description") or "")
            
            # Skip non-OneDrive operations
            if not _is_onedrive_operation(path, str(op_id)):
                skipped_non_onedrive += 1
                continue
            
            # ============ EXCLUSION CHECK (MODIFY EXCLUDED_KEYWORDS LIST ABOVE) ============
            # Skip operations based on exclusion keywords
            should_exclude_op = False
            for keyword in EXCLUDED_KEYWORDS:
                if (keyword in str(op_id).lower() or 
                    keyword in summary.lower() or 
                    keyword in description.lower()):
                    should_exclude_op = True
                    break
            
            if should_exclude_op:
                skipped_non_onedrive += 1
                continue
            # ===============================================================================
            
            # Skip count operations
            if _is_count_operation(path, str(op_id), summary, description):
                skipped_count_ops += 1
                continue
            
            # Merge parameters from path and operation
            merged_params: List[Mapping[str, Any]] = []
            
            # Add path-level parameters
            for p in item.get("parameters", []):
                if isinstance(p, Mapping):
                    merged_params.append(p)
            
            # Add operation-level parameters
            for p in op.get("parameters", []):
                if isinstance(p, Mapping):
                    merged_params.append(p)
            
            ops.append(
                OneDriveOperation(
                    op_id=str(op_id),
                    http_method=method.upper(),
                    path=str(path),
                    summary=summary,
                    description=description, 
                    params=merged_params,
                    request_body=op.get("requestBody"),
                    path_params=path_params
                )
            )

    # Remove duplicates and sort
    uniq: Dict[str, OneDriveOperation] = {}
    for op in ops:
        key = f"{op.op_id}_{op.http_method}_{op.path}"
        if key not in uniq:
            uniq[key] = op
    
    print(f"OneDrive filtering summary (MODIFY EXCLUDED_KEYWORDS/EXCLUDED_PATHS to change):")
    print(f"  - Skipped {skipped_count_ops} count operations")
    print(f"  - Skipped {skipped_non_onedrive} excluded operations (chats, uploads, sharing, workbooks)")
    print(f"  - Found {len(uniq)} basic OneDrive operations")
    print(f"  - Excluded keywords: {', '.join(EXCLUDED_KEYWORDS)}")
    print(f"  - Excluded paths: {', '.join(EXCLUDED_PATHS[:5])}{'...' if len(EXCLUDED_PATHS) > 5 else ''}")
    
    return sorted(uniq.values(), key=lambda o: (o.operation_type, o.path, o.op_id))

def _path_matches_pattern(path: str, pattern: str) -> bool:
    """Check if path matches the pattern with parameter placeholders."""
    # Convert pattern to regex
    regex_pattern = re.escape(pattern)
    regex_pattern = regex_pattern.replace(r'\{[^}]+\}', r'\{[^}]+\}')
    regex_pattern = regex_pattern.replace(r'\\\{[^}]+\\\}', r'[^/]+')
    regex_pattern = f"^{regex_pattern}"
    
    return bool(re.match(regex_pattern, path))

# ---- Code generation helpers ----------------------------------------------
_IDENT_RE = re.compile(r"[^0-9a-zA-Z_]")

def sanitize_py_name(name: str) -> str:
    """Sanitize name to valid Python identifier."""
    name = name.replace("$", "dollar_")
    name = name.replace("@", "at_")
    name = name.replace(".", "_")
    name = name.replace("-", "_")
    name = name.replace(":", "_")
    
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
    name = name.replace(".", "_").replace("-", "_").replace(":", "_")
    
    out: List[str] = []
    for i, ch in enumerate(name):
        if ch.isupper():
            if i > 0 and (name[i-1].islower() or (i+1 < len(name) and name[i+1].islower())):
                out.append("_")
            out.append(ch.lower())
        else:
            out.append("_" if ch in "- :" else ch)
    s = "".join(out)
    s = re.sub(r"__+", "_", s)
    return s.strip("_")

def extract_operation_parameters(op: OneDriveOperation) -> Tuple[List[str], List[str], List[str]]:
    """Extract and categorize parameters from OneDrive operation."""
    required_params = ["self"]
    optional_params = []
    param_docs = []
    
    # Add path parameters first (always required)
    for path_param in op.path_params:
        param_name = sanitize_py_name(path_param['name'])
        param_type = path_param.get('type', 'str')
        required_params.append(f"{param_name}: {param_type}")
        param_docs.append(f"            {param_name} ({param_type}, required): {path_param.get('description', 'Path parameter')}")
    
    # Add other parameters from OpenAPI spec
    for param in op.params:
        if not isinstance(param, dict):
            continue
            
        param_name = param.get('name', '')
        if not param_name:
            continue
            
        # Skip if this is already handled as a path parameter
        if any(path_param['original'] == param_name or path_param['name'] == param_name for path_param in op.path_params):
            continue
            
        clean_name = sanitize_py_name(param_name)
        param_schema = param.get('schema', {})
        param_type = _get_python_type(param_schema)
        param_location = param.get('in', 'query')
        is_required = param.get('required', False)
        description = param.get('description', f'{param_location} parameter')
        
        if is_required:
            required_params.append(f"{clean_name}: {param_type}")
            param_docs.append(f"            {clean_name} ({param_type}, required): {description}")
        else:
            optional_params.append(f"{clean_name}: Optional[{param_type}] = None")
            param_docs.append(f"            {clean_name} ({param_type}, optional): {description}")
    
    # Add OneDrive-specific OData parameters
    onedrive_odata_params = [
        "select: Optional[List[str]] = None",
        "expand: Optional[List[str]] = None", 
        "filter: Optional[str] = None",
        "orderby: Optional[str] = None",
        "search: Optional[str] = None",
        "top: Optional[int] = None",
        "skip: Optional[int] = None"
    ]
    optional_params.extend(onedrive_odata_params)
    
    # Add OneDrive OData parameter docs
    onedrive_odata_docs = [
        "            select (optional): Select specific properties to return",
        "            expand (optional): Expand related entities (e.g., children, permissions)",
        "            filter (optional): Filter the results using OData syntax", 
        "            orderby (optional): Order the results by specified properties",
        "            search (optional): Search for files and folders by name/content",
        "            top (optional): Limit number of results returned",
        "            skip (optional): Skip number of results for pagination"
    ]
    param_docs.extend(onedrive_odata_docs)
    
    # Add request body if needed (for basic operations)
    has_body = op.request_body is not None
    if has_body:
        optional_params.append("request_body: Optional[Mapping[str, Any]] = None")
        param_docs.append("            request_body (optional): Request body data for file operations")
    
    # Add headers parameter
    optional_params.append("headers: Optional[Dict[str, str]] = None")
    param_docs.append("            headers (optional): Additional headers for the request")
    
    # Add kwargs
    optional_params.append("**kwargs")
    param_docs.append("            **kwargs: Additional query parameters")
    
    return required_params, optional_params, param_docs

def _get_python_type(schema: Dict[str, Any]) -> str:
    """Convert OpenAPI schema to Python type."""
    if not schema:
        return "str"
    
    schema_type = schema.get('type', 'string')
    schema_format = schema.get('format', '')
    
    type_mapping = {
        'string': 'str',
        'integer': 'int',
        'number': 'float',
        'boolean': 'bool',
        'array': 'List[str]',
        'object': 'Dict[str, Any]'
    }
    
    base_type = type_mapping.get(schema_type, 'str')
    
    # Handle special formats
    if schema_format in ['date-time', 'date']:
        return 'str'  # Keep as string for easier handling
    elif schema_format == 'binary':
        return 'bytes'
    
    return base_type

def _get_query_param_classes(path: str, method: str) -> Tuple[Optional[str], Optional[str]]:
    """Get the appropriate query parameter classes for the endpoint."""
    path_lower = path.lower()
    method_lower = method.lower()
    
    # Map paths to their corresponding query parameter classes
    # Updated with correct nested class names from Microsoft Graph SDK
    if '/me/drive/items' in path_lower and method_lower == 'get':
        return "DrivesRequestBuilder.DrivesRequestBuilderGetQueryParameters", "DrivesRequestBuilder.DrivesRequestBuilderGetRequestConfiguration"
    elif '/me/drive' in path_lower and method_lower == 'get':
        return "DriveRequestBuilder.DriveRequestBuilderGetQueryParameters", "DriveRequestBuilder.DriveRequestBuilderGetRequestConfiguration"
    elif '/users/{user-id}/drives' in path_lower and method_lower == 'get':
        return "DrivesRequestBuilder.DrivesRequestBuilderGetQueryParameters", "DrivesRequestBuilder.DrivesRequestBuilderGetRequestConfiguration"
    elif '/users/{user-id}/drive' in path_lower and method_lower == 'get':
        return "DriveRequestBuilder.DriveRequestBuilderGetQueryParameters", "DriveRequestBuilder.DriveRequestBuilderGetRequestConfiguration"
    elif '/groups/{group-id}/drives' in path_lower and method_lower == 'get':
        return "DrivesRequestBuilder.DrivesRequestBuilderGetQueryParameters", "DrivesRequestBuilder.DrivesRequestBuilderGetRequestConfiguration"
    elif '/groups/{group-id}/drive' in path_lower and method_lower == 'get':
        return "DriveRequestBuilder.DriveRequestBuilderGetQueryParameters", "DriveRequestBuilder.DriveRequestBuilderGetRequestConfiguration"
    elif '/sites/{site-id}/drives' in path_lower and method_lower == 'get':
        return "DrivesRequestBuilder.DrivesRequestBuilderGetQueryParameters", "DrivesRequestBuilder.DrivesRequestBuilderGetRequestConfiguration"
    elif '/sites/{site-id}/drive' in path_lower and method_lower == 'get':
        return "DriveRequestBuilder.DriveRequestBuilderGetQueryParameters", "DriveRequestBuilder.DriveRequestBuilderGetRequestConfiguration"
    elif '/drives' in path_lower and method_lower == 'get':
        return "DrivesRequestBuilder.DrivesRequestBuilderGetQueryParameters", "DrivesRequestBuilder.DrivesRequestBuilderGetRequestConfiguration"
    else:
        # Fallback to generic RequestConfiguration for unmatched endpoints
        return "RequestConfiguration", "RequestConfiguration"

def build_onedrive_method_code(op: OneDriveOperation) -> str:
    """Generate comprehensive async method code for OneDrive operation."""
    required_params, optional_params, param_docs = extract_operation_parameters(op)

    # Build method signature
    all_params = required_params + optional_params
    
    if len(all_params) <= 5:
        sig = ", ".join(all_params)
    else:
        params_formatted = ",\n        ".join(all_params)
        sig = f"\n        {params_formatted}\n    "

    summary = op.summary or op.op_id
    params_doc = "\n".join(param_docs) if param_docs else "            (standard OneDrive parameters)"

    # Generate OneDrive-specific Graph SDK method call
    graph_method_call = _generate_onedrive_graph_call(op)
    # Determine query parameter classes during generation
    query_param_class, config_class = _get_query_param_classes(op.path, op.http_method)
    response_type = "OneDriveResponse"

    method_code = f"""    async def {op.wrapper_name}({sig}) -> {response_type}:
        \"\"\"{summary}.
        OneDrive operation: {op.http_method} {op.path}
        Operation type: {op.operation_type}
        Args:
{params_doc}
        Returns:
            {response_type}: OneDrive response wrapper with success/data/error
        \"\"\"
        # Build query parameters including OData for OneDrive
        try:
            # Use typed query parameters
            query_params = {query_param_class}()
            
            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip
            
            # Create proper typed request configuration
            config = {config_class}()
            config.query_parameters = query_params

            if headers:
                config.headers = headers
            
            # Add consistency level for search operations in OneDrive
            if search:
                if not config.headers:
                    config.headers = {{}}
                config.headers['ConsistencyLevel'] = 'eventual'

{graph_method_call}
            return self._handle_onedrive_response(response)
        except Exception as e:
            return {response_type}(
                success=False,
                error=f"OneDrive API call failed: {{str(e)}}",
            )
"""

    return method_code

def _generate_onedrive_graph_call(op: OneDriveOperation) -> str:
    """Generate OneDrive-specific Microsoft Graph SDK method call."""
    path = op.path
    method = op.http_method.lower()
    has_body = op.request_body is not None
    
    # Build request builder chain optimized for OneDrive
    request_builder = _build_onedrive_request_builder_chain(path, op.path_params)
    
    # Generate method call based on HTTP method (basic operations only)
    if method == "get":
        call = f"response = await {request_builder}.get(request_configuration=config)"
    elif method == "post":
        if has_body:
            call = f"response = await {request_builder}.post(body=request_body, request_configuration=config)"
        else:
            call = f"response = await {request_builder}.post(request_configuration=config)"
    elif method == "patch":
        if has_body:
            call = f"response = await {request_builder}.patch(body=request_body, request_configuration=config)"
        else:
            call = f"response = await {request_builder}.patch(request_configuration=config)"
    elif method == "put":
        if has_body:
            call = f"response = await {request_builder}.put(body=request_body, request_configuration=config)"
        else:
            call = f"response = await {request_builder}.put(request_configuration=config)"
    elif method == "delete":
        call = f"response = await {request_builder}.delete(request_configuration=config)"
    else:
        call = f"response = await {request_builder}.get(request_configuration=config)"

    return f"            {call}"

def _build_onedrive_request_builder_chain(path: str, path_params: List[Dict[str, str]]) -> str:
    """Build OneDrive-optimized Microsoft Graph SDK request builder chain."""
    # Start with base client
    builder = "self.client"
    
    # Split path into segments
    segments = [seg for seg in path.split('/') if seg]
    
    i = 0
    while i < len(segments):
        segment = segments[i]
        
        if segment.startswith('{') and segment.endswith('}'):
            # This is a parameter - use appropriate by_* method
            param_name = segment[1:-1].replace('-', '_')
            python_param = sanitize_py_name(param_name)
            
            # Get the previous segment to determine the by_* method
            prev_segment = segments[i-1] if i > 0 else ""
            
            if prev_segment == "users":
                builder += f".by_user_id({python_param})"
            elif prev_segment == "groups":
                builder += f".by_group_id({python_param})"
            elif prev_segment == "drives":
                builder += f".by_drive_id({python_param})"
            elif prev_segment == "sites":
                builder += f".by_site_id({python_param})"
            elif prev_segment == "items":
                builder += f".by_drive_item_id({python_param})"
            else:
                # Generic fallback for other OneDrive parameters
                builder += f".by_{prev_segment[:-1] if prev_segment.endswith('s') else prev_segment}_id({python_param})"
                
        elif segment.startswith("search("):
            # Handle search operations specially
            builder += ".search"
        elif ":" in segment and segment.endswith(":"):
            # Handle OneDrive path-based operations like root:/path/to/file:
            if segment.startswith("root:"):
                path_part = segment[5:-1]  # Remove "root:" and trailing ":"
                if path_part:
                    builder += f".item_with_path('{path_part}')"
                else:
                    builder += ".root"
            else:
                # Generic path-based operation
                builder += f".item_with_path('{segment[:-1]}')"
        else:
            # Regular path segment - convert to appropriate OneDrive SDK method
            if segment == "me":
                builder += ".me"
            elif segment == "drive":
                builder += ".drive"
            elif segment == "drives":
                builder += ".drives"
            elif segment == "root":
                builder += ".root"
            elif segment == "children":
                builder += ".children"
            elif segment == "content":
                builder += ".content"
            elif segment == "thumbnails":
                builder += ".thumbnails"
            elif segment == "permissions":
                builder += ".permissions"
            elif segment == "versions":
                builder += ".versions"
            elif segment == "delta":
                builder += ".delta"
            elif segment == "items":
                builder += ".items"
            elif segment == "analytics":
                builder += ".analytics"
            elif segment == "preview":
                builder += ".preview"
            elif segment == "sharedWithMe":
                builder += ".shared_with_me"
            elif segment == "recent":
                builder += ".recent"
            elif segment == "following":
                builder += ".following"
            elif segment == "copy":
                builder += ".copy"
            elif segment == "move":
                builder += ".move"
            elif segment == "restore":
                builder += ".restore"
            elif segment == "checkout":
                builder += ".checkout"
            elif segment == "checkin":
                builder += ".checkin"
            else:
                # Convert to snake_case for other segments
                snake_segment = to_snake(segment)
                builder += f".{snake_segment}"
        
        i += 1
    
    return builder

def build_onedrive_class_code(ops: Sequence[OneDriveOperation]) -> str:
    """Generate the complete OneDrive client class."""
    # Group methods by operation type for better organization
    methods_by_type = {}
    for op in ops:
        if op.operation_type not in methods_by_type:
            methods_by_type[op.operation_type] = []
        methods_by_type[op.operation_type].append(op)
    
    class_name = "OneDriveDataSource"
    response_class = "OneDriveResponse"

    header = f"""

import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Mapping, Optional

from kiota_abstractions.base_request_configuration import (# type: ignore
    RequestConfiguration,
)
# Import MS Graph specific query parameter classes
from msgraph.generated.drives.drives_request_builder import DrivesRequestBuilder # type: ignore
from msgraph.generated.users.item.drives.drives_request_builder import DrivesRequestBuilder as UserDrivesRequestBuilder # type: ignore
from msgraph.generated.users.item.drive.drive_request_builder import DriveRequestBuilder as UserDriveRequestBuilder # type: ignore
from msgraph.generated.groups.item.drive.drive_request_builder import DriveRequestBuilder as GroupDriveRequestBuilder # type: ignore
from msgraph.generated.sites.item.drives.drives_request_builder import DrivesRequestBuilder as SiteDrivesRequestBuilder # type: ignore
from msgraph.generated.sites.item.drive.drive_request_builder import DriveRequestBuilder as SiteDriveRequestBuilder # type: ignore
from msgraph.generated.drives.item.items.items_request_builder import ItemsRequestBuilder # type: ignore
from msgraph.generated.drives.item.root.root_request_builder import RootRequestBuilder # type: ignore
from kiota_abstractions.base_request_configuration import RequestConfiguration # type: ignore

from app.sources.client.microsoft.microsoft import MSGraphClient

# OneDrive-specific response wrapper
class OneDriveResponse:
    \"\"\"Standardized OneDrive API response wrapper.\"\"\"
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def __init__(self, success: bool, data: Optional[Dict[str, Any]] = None, error: Optional[str] = None, message: Optional[str] = None):
        self.success = success
        self.data = data
        self.error = error
        self.message = message

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

# Set up logger
logger = logging.getLogger(__name__)

class {class_name}:
    \"\"\"
    Basic Microsoft OneDrive API client with core endpoint coverage.

    Features:
    - Basic OneDrive API coverage with {len(ops)} methods organized by operation type
    - Support for Personal OneDrive, User OneDrive, Group OneDrive, and SharePoint Document Libraries
    - Basic file and folder operations (read, list, copy, move, delete)
    - Search, discovery, and delta synchronization capabilities
    - Thumbnail and preview generation
    - File versioning and restore operations
    - Basic OData query parameter support optimized for OneDrive
    - Microsoft Graph SDK integration with OneDrive-specific optimizations
    - Async snake_case method names for all operations
    - Standardized {response_class} format for all responses
    - Simple error handling and OneDrive-specific response processing

    EXCLUDED OPERATIONS (modify EXCLUDED_KEYWORDS list to change):
    - Upload operations (content, createUploadSession)
    - Sharing operations (permissions, invite, createLink)
    - Workbook operations (Excel file editing)
    - Chat operations (Teams chats)
    - Onenote operations (Onenote operations)
    - Device App Management operations (Device App Management operations)
    - Device Management operations (Device Management operations)
    - Connections operations (Connections operations)
    - Identity Governance operations (Identity Governance operations)
    - Analytics operations (Analytics operations)
    - Mail Folders operations (Mail Folders operations)
    - Storage operations (Storage operations)
    - Admin operations (Admin operations)
    - Agreements operations (Agreements operations)
    - Security operations (Security operations)
    - Directory operations (Directory operations)
    - Solutions operations (Solutions operations)
    - Onenote operations (Onenote operations)

    Operation Types:
    - File operations: Basic CRUD for drive items
    - Folder operations: Directory listing and navigation
    - Metadata operations: Thumbnails, analytics, preview
    - Discovery operations: Search, delta sync, recent files
    - General operations: Drive info and basic operations
    - Workbook operations: Excel file editing
    - Chat operations: Teams chats
    - Onenote operations: Onenote operations
    - Device App Management operations: Device App Management operations
    - Device Management operations: Device Management operations
    - Connections operations: Connections operations
    - Identity Governance operations: Identity Governance operations
    - Analytics operations: Analytics operations
    - Mail Folders operations: Mail Folders operations
    - Storage operations: Storage operations
    - Admin operations: Admin operations
    - Agreements operations: Agreements operations
    - Security operations: Security operations
    - Directory operations: Directory operations
    - Solutions operations: Solutions operations
    - Onenote operations: Onenote operations
    \"\"\"

    def __init__(self, client: MSGraphClient) -> None:
        \"\"\"Initialize with Microsoft Graph SDK client optimized for OneDrive.\"\"\"
        self.client = client.get_client().get_ms_graph_service_client()
        if not hasattr(self.client, "me"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("OneDrive client initialized with {len(ops)} methods")

    def _handle_onedrive_response(self, response: object) -> OneDriveResponse:
        \"\"\"Handle OneDrive API response with comprehensive error handling.\"\"\"
        try:
            if response is None:
                return OneDriveResponse(success=False, error="Empty response from OneDrive API")
            
            success = True
            error_msg = None
            
            # Enhanced error response handling for OneDrive operations
            if hasattr(response, 'error'):
                success = False
                error_msg = str(response.error)
            elif isinstance(response, dict) and 'error' in response:
                success = False
                error_info = response['error']
                if isinstance(error_info, dict):
                    error_code = error_info.get('code', 'Unknown')
                    error_message = error_info.get('message', 'No message')
                    error_msg = f"{{error_code}}: {{error_message}}"
                else:
                    error_msg = str(error_info)
            elif hasattr(response, 'code') and hasattr(response, 'message'):
                success = False  
                error_msg = f"{{response.code}}: {{response.message}}"
            
            return OneDriveResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling OneDrive response: {{e}}")
            return OneDriveResponse(success=False, error=str(e))

    def get_data_source(self) -> '{class_name}':
        \"\"\"Get the underlying OneDrive client.\"\"\"
        return self

"""

    # Add methods organized by operation type
    methods_section = ""
    
    # Define the order of operation types for better organization
    operation_order = ['file', 'folder', 'upload', 'sharing', 'metadata', 'discovery', 'workbook', 'general']
    
    for op_type in operation_order:
        if op_type in methods_by_type:
            methods = methods_by_type[op_type]
            methods_section += f"    # ========== {op_type.upper()} OPERATIONS ({len(methods)} methods) ==========\n\n"
            
            for op in methods:
                method_code = build_onedrive_method_code(op)
                methods_section += method_code + "\n"
    
    return header + methods_section + "\n\n"

# ---- Public entrypoints ----------------------------------------------------
def generate_onedrive_client(
    *,
    out_path: Optional[str] = None,
    spec_url: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> str:
    """Generate comprehensive OneDrive client. Returns file path."""
    out_filename = out_path or "onedrive_client.py"
    
    print(f"Loading Microsoft Graph OpenAPI specification for OneDrive...")
    spec = load_spec(spec_url=spec_url, spec_path=spec_path)
    
    print(f"Extracting OneDrive operations with comprehensive coverage...")
    ops = extract_onedrive_operations(spec)
    
    print(f"Found {len(ops)} OneDrive operations with full parameter support")
    
    # Show breakdown by operation type
    ops_by_type = {}
    for op in ops:
        if op.operation_type not in ops_by_type:
            ops_by_type[op.operation_type] = 0
        ops_by_type[op.operation_type] += 1
    
    print("Operation breakdown:")
    for op_type, count in sorted(ops_by_type.items()):
        print(f"  - {op_type}: {count} methods")
    
    print("Generating comprehensive OneDrive async client code...")
    code = build_onedrive_class_code(ops)
    
    # Create microsoft directory (reuse the existing structure)
    script_dir = Path(__file__).parent if __file__ else Path('.')
    microsoft_dir = script_dir / 'microsoft'
    microsoft_dir.mkdir(exist_ok=True)
    
    # Write file
    full_path = microsoft_dir / out_filename
    full_path.write_text(code, encoding="utf-8")
    print(f"Saved comprehensive OneDrive client to: {full_path}")
    
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
        description="Generate comprehensive OneDrive API client"
    )
    ap.add_argument("--out", help="Output .py file path (default: onedrive_client.py)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--spec-url", default=None, help="Microsoft Graph OpenAPI spec URL")
    g.add_argument("--spec-path", help="Local spec file path (JSON/YAML)")
    ap.add_argument("--show-patterns", action="store_true", help="Show all OneDrive path patterns")
    
    return ap.parse_args(argv)

def _show_patterns() -> None:
    """Show all OneDrive path patterns that will be matched."""
    print("OneDrive API Path Patterns (Comprehensive Coverage):")
    print(f"Total patterns: {len(ONEDRIVE_PATH_PATTERNS)}")
    print()
    
    # Group patterns by category
    pattern_groups = {
        "Personal OneDrive": [p for p in ONEDRIVE_PATH_PATTERNS if p.startswith("/me/drive")],
        "User OneDrive": [p for p in ONEDRIVE_PATH_PATTERNS if p.startswith("/users/")],
        "Group OneDrive": [p for p in ONEDRIVE_PATH_PATTERNS if p.startswith("/groups/")],
        "SharePoint Sites": [p for p in ONEDRIVE_PATH_PATTERNS if p.startswith("/sites/")],
        "Generic Drives": [p for p in ONEDRIVE_PATH_PATTERNS if p.startswith("/drives") and not p.startswith("/drives/")],
        "Drive Operations": [p for p in ONEDRIVE_PATH_PATTERNS if p.startswith("/drives/{drive-id}")],
    }
    
    for group_name, patterns in pattern_groups.items():
        if patterns:
            print(f"{group_name} ({len(patterns)} patterns):")
            for pattern in patterns[:5]:  # Show first 5 patterns
                print(f"  - {pattern}")
            if len(patterns) > 5:
                print(f"  ... and {len(patterns) - 5} more")
            print()

def main(argv: Optional[Sequence[str]] = None) -> None:
    """Main CLI entry point."""
    ns = _parse_args(argv)
    
    if ns.show_patterns:
        _show_patterns()
        return
    
    print(f"Starting comprehensive OneDrive API client generation...")
    
    try:
        out_path = generate_onedrive_client(
            out_path=ns.out,
            spec_url=ns.spec_url,
            spec_path=ns.spec_path,
        )
        
        print(f"\nOneDrive comprehensive client generation completed!")
        print(f"Generated class: OneDriveDataSource")
        print(f"Output file: {out_path}")
        print(f"Features:")
        print(f"  - Basic OneDrive API endpoint coverage (no uploads/sharing/workbooks)")
        print(f"  - Support for Personal, User, Group, and SharePoint OneDrives")
        print(f"  - File/folder operations: read, list, copy, move, delete, search")
        print(f"  - Basic OData query support optimized for OneDrive")
        print(f"  - Async methods with simple error handling")
        print(f"")
        print(f"🔧 TO MODIFY EXCLUSIONS:")
        print(f"  - Edit EXCLUDED_KEYWORDS list to add/remove excluded API keywords")
        print(f"  - Edit EXCLUDED_PATHS list to add/remove excluded path patterns")
        print(f"  - Current exclusions: uploads, sharing, workbooks, chats")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
