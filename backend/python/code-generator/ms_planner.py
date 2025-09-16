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
Microsoft Planner API Client Generator (Plans, Buckets, Tasks Focus)

Generates a focused Planner API client with comprehensive endpoint coverage for:
- Personal Planner (/me/planner)
- User Planner (/users/{user-id}/planner)
- Group Planner (/groups/{group-id}/planner)
- Plans (/planner/plans, /planner/plans/{plan-id})
- Buckets (/planner/buckets, /planner/buckets/{bucket-id})
- Tasks (/planner/tasks, /planner/tasks/{task-id})
- Plan Details (/planner/plans/{plan-id}/details)
- Bucket Details (/planner/buckets/{bucket-id}/details)
- Task Details (/planner/tasks/{task-id}/details)
- Assignments and Progress Tracking
- Task Boards and Organization
- Plan Categories and Labels

Explicitly excludes:
- OneDrive operations (/drive, /drives endpoints)
- Teams operations (/chats, /teams endpoints)
- SharePoint operations (/sites endpoints)
- OneNote operations (/onenote endpoints)
- Outlook operations (/messages, /events, /contacts, /calendar endpoints)
- Directory operations (/directoryObjects endpoints)
- Device operations (/devices endpoints)
- Security operations (/security endpoints)
- Compliance operations (/compliance endpoints)
- Admin operations (/admin endpoints)
- Non-Planner Microsoft Graph endpoints

EASY MODIFICATION:
To add/remove excluded APIs, modify the EXCLUDED_KEYWORDS and EXCLUDED_PATHS lists at the top of this file.

Enhanced Features:
- Complete Planner endpoint coverage with all path parameters
- Proper parameter extraction for plan-id, bucket-id, task-id, user-id, group-id
- Comprehensive method signatures with required and optional parameters
- Microsoft Graph SDK integration optimized for Planner operations
- Specialized Planner response handling and error management
- Support for task management, plan organization, and progress tracking
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

# Keywords to exclude from Planner operations (ADD MORE HERE TO EXCLUDE)
EXCLUDED_KEYWORDS = [
    'drive',          # Exclude OneDrive operations
    'drives',         # Exclude OneDrive operations
    'chat',           # Exclude Teams chat operations
    'teams',          # Exclude Teams operations
    'site',           # Exclude SharePoint site operations
    'sites',          # Exclude SharePoint site operations
    'onenote',        # Exclude OneNote operations
    'message',        # Exclude Outlook message operations
    'messages',       # Exclude Outlook message operations
    'event',          # Exclude Outlook event operations
    'events',         # Exclude Outlook event operations
    'contact',        # Exclude Outlook contact operations
    'contacts',       # Exclude Outlook contact operations
    'calendar',       # Exclude Outlook calendar operations
    'calendars',      # Exclude Outlook calendar operations
    'mailFolder',     # Exclude Outlook mail folder operations
    'mailFolders',    # Exclude Outlook mail folder operations
    'deviceAppManagement',
    'deviceManagement',
    'directoryObjects',
    'devices',
    'security',
    'compliance',
    'admin',
    'agreements',
    'directory',
    'solutions',
    'analytics',
    'auditLogs',
    'identityGovernance',
    'storage',
    'connections',
    'workbook',       # Exclude Excel workbook operations
    'worksheet',      # Exclude Excel worksheet operations
    'todo',           # Exclude To-Do operations (focus on Planner)
    'notebook',       # Exclude notebook operations
    'section',        # Exclude OneNote section operations
    'page',           # Exclude OneNote page operations
    'communications',
    'education',
    'identity',
    'photo',
    'inferenceClassification',
    'extensions',
    'termStore',
    'servicePrincipals',
    'appRoleAssignments',
    'print'
]

# Path patterns to exclude (ADD MORE PATTERNS HERE TO EXCLUDE)
EXCLUDED_PATHS = [
    '/drives',
    '/drive',
    '/chats',
    '/teams',
    '/sites',
    '/onenote',
    '/messages',
    '/events',
    '/contacts',
    '/calendar',
    '/calendars',
    '/mailFolders',
    '/deviceAppManagement',
    '/deviceManagement',
    '/directoryObjects',
    '/devices',
    '/security',
    '/compliance',
    '/admin',
    '/agreements',
    '/directory',
    '/solutions',
    '/analytics',
    '/auditLogs',
    '/identityGovernance',
    '/storage',
    '/connections',
    '/workbook',
    '/worksheets',
    '/todo',
    '/notebook',
    '/sections',
    '/pages',
    '/communications',
    '/education',
    '/identity',
    '/photo',
    '/inferenceClassification',
    '/extensions',
    '/termStore',
    '/servicePrincipals',
    '/appRoleAssignments',
    '/print'
]

# =============================================================================

# Microsoft Graph OpenAPI specification URLs
DEFAULT_SPEC_URL = "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/master/openapi/v1.0/openapi.yaml"
FALLBACK_SPEC_URLS = [
    "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/main/openapi/v1.0/openapi.yaml",
]

# Comprehensive Planner endpoint patterns (MODIFY THIS LIST TO ADD/REMOVE ENDPOINTS)
PLANNER_PATH_PATTERNS = [
    # Personal Planner - Complete Operations
    "/me/planner",
    "/me/planner/plans",
    "/me/planner/plans/{plan-id}",
    "/me/planner/tasks",
    "/me/planner/tasks/{task-id}",
    "/me/planner/favoritePlans",
    "/me/planner/recentPlans",
    "/me/planner/all",
    
    # User Planner - Complete Operations
    "/users/{user-id}/planner",
    "/users/{user-id}/planner/plans",
    "/users/{user-id}/planner/plans/{plan-id}",
    "/users/{user-id}/planner/tasks",
    "/users/{user-id}/planner/tasks/{task-id}",
    "/users/{user-id}/planner/favoritePlans",
    "/users/{user-id}/planner/recentPlans",
    "/users/{user-id}/planner/all",
    
    # Group Planner - Complete Operations
    "/groups/{group-id}/planner",
    "/groups/{group-id}/planner/plans",
    "/groups/{group-id}/planner/plans/{plan-id}",
    
    # Core Planner Operations
    "/planner",
    "/planner/plans",
    "/planner/plans/{plan-id}",
    "/planner/plans/{plan-id}/details",
    "/planner/plans/{plan-id}/buckets",
    "/planner/plans/{plan-id}/tasks",
    
    # Bucket Operations
    "/planner/buckets",
    "/planner/buckets/{bucket-id}",
    "/planner/buckets/{bucket-id}/details",
    "/planner/buckets/{bucket-id}/tasks",
    
    # Task Operations - Complete Coverage
    "/planner/tasks",
    "/planner/tasks/{task-id}",
    "/planner/tasks/{task-id}/details",
    "/planner/tasks/{task-id}/assignedToTaskBoardFormat",
    "/planner/tasks/{task-id}/bucketTaskBoardFormat",
    "/planner/tasks/{task-id}/progressTaskBoardFormat",
    
    # Plan Details and Configuration
    "/planner/plans/{plan-id}/categoryDescriptions",
    "/planner/plans/{plan-id}/contextDetails",
    "/planner/plans/{plan-id}/sharedWith",
    
    # Task Assignment and Progress
    "/planner/tasks/{task-id}/assignments",
    "/planner/tasks/{task-id}/assignments/{assignment-id}",
    "/planner/tasks/{task-id}/checklist",
    "/planner/tasks/{task-id}/checklist/{checklist-item-id}",
    
    # Planner Roster and Membership (if available)
    "/planner/rosters",
    "/planner/rosters/{roster-id}",
    "/planner/rosters/{roster-id}/plans",
    "/planner/rosters/{roster-id}/members",
    "/planner/rosters/{roster-id}/members/{user-id}",
    
    # Delta and Synchronization
    "/planner/plans/delta",
    "/planner/tasks/delta",
    "/planner/buckets/delta",
    
    # NOTE: Excluded patterns (OneDrive, Teams, SharePoint, Outlook) are filtered out by EXCLUDED_KEYWORDS and EXCLUDED_PATHS
    # To re-enable them, remove the keywords from the exclusion lists above
]

# ---- Operation Model -------------------------------------------------------
@dataclass
class PlannerOperation:
    op_id: str
    http_method: str
    path: str
    summary: str
    description: str
    params: List[Mapping[str, Any]]
    request_body: Optional[Mapping[str, Any]] = None
    path_params: List[Dict[str, str]] = None
    operation_type: str = "general"  # plans, buckets, tasks, details, assignments, rosters

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
        """Determine the type of Planner operation for better organization."""
        path_lower = self.path.lower()
        op_lower = self.op_id.lower()
        
        if any(keyword in path_lower for keyword in ["plans", "plan"]):
            return "plans"
        elif any(keyword in path_lower for keyword in ["buckets", "bucket"]):
            return "buckets"
        elif any(keyword in path_lower for keyword in ["tasks", "task"]):
            return "tasks"
        elif any(keyword in path_lower for keyword in ["details", "categoryDescriptions", "contextDetails"]):
            return "details"
        elif any(keyword in path_lower for keyword in ["assignments", "assignedTo", "checklist"]):
            return "assignments"
        elif any(keyword in path_lower for keyword in ["rosters", "roster", "members"]):
            return "rosters"
        elif any(keyword in path_lower for keyword in ["taskBoardFormat", "progressTaskBoard", "bucketTaskBoard"]):
            return "boardFormat"
        elif any(keyword in path_lower for keyword in ["favoritePlans", "recentPlans", "sharedWith"]):
            return "favorites"
        elif any(keyword in path_lower for keyword in ["delta"]):
            return "sync"
        else:
            return "general"

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
    """Extract path parameters from Planner path pattern."""
    import re
    
    # Ensure path is a string
    if not isinstance(path, str):
        path = str(path) if path is not None else ""
    
    # Find all {parameter} patterns
    pattern = r'\{([^}]+)\}'
    matches = re.findall(pattern, path)
    
    path_params = []
    for match in matches:
        # Clean up parameter name and convert to Python-friendly format
        param_name = match.replace('-', '_').replace('.', '_')
        
        # Determine parameter type based on common Planner patterns
        param_type = 'str'
        description = f'Planner path parameter: {match}'
        
        if 'id' in match.lower():
            description = f'Planner {match.replace("-", " ").replace("_", " ")} identifier'
        elif 'user' in match.lower():
            description = f'User identifier: {match}'
        elif 'group' in match.lower():
            description = f'Group identifier: {match}'
        elif 'plan' in match.lower():
            description = f'Plan identifier: {match}'
        elif 'bucket' in match.lower():
            description = f'Bucket identifier: {match}'
        elif 'task' in match.lower():
            description = f'Task identifier: {match}'
        elif 'roster' in match.lower():
            description = f'Roster identifier: {match}'
        elif 'assignment' in match.lower():
            description = f'Assignment identifier: {match}'
        elif 'checklist' in match.lower():
            description = f'Checklist item identifier: {match}'
        
        path_params.append({
            'name': param_name,
            'original': match,
            'type': param_type,
            'required': True,
            'description': description
        })
    
    return path_params

def _is_planner_operation(path: str, op_id: str) -> bool:
    """Check if this operation is related to Planner (Plans, Buckets, Tasks).
    
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
    
    # Include operations that are clearly Planner related (CORE PLANNER KEYWORDS)
    planner_core_keywords = [
        'planner', 'plans', 'plan', 'buckets', 'bucket', 'tasks', 'task',
        'assignments', 'assignment', 'checklist', 'rosters', 'roster',
        'taskBoardFormat', 'progressTaskBoard', 'bucketTaskBoard',
        'assignedToTaskBoard', 'categoryDescriptions', 'contextDetails',
        'favoritePlans', 'recentPlans', 'sharedWith'
    ]
    
    return any(keyword in path_lower or keyword in op_lower for keyword in planner_core_keywords)

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

def extract_planner_operations(spec: Mapping[str, Any]) -> List[PlannerOperation]:
    """Extract Planner-specific operations from OpenAPI spec."""
    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("Invalid OpenAPI spec: missing 'paths'")

    ops: List[PlannerOperation] = []
    skipped_count_ops = 0
    skipped_non_planner = 0
    
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
            
        # Check if path matches any Planner patterns
        if not any(_path_matches_pattern(path, pattern) for pattern in PLANNER_PATH_PATTERNS):
            # Additional check for Planner-related operations
            if not _is_planner_operation(path, ""):
                skipped_non_planner += 1
                continue
        
        # ============ EXCLUSION CHECK (MODIFY EXCLUDED_PATHS LIST ABOVE) ============
        # Explicitly exclude paths based on exclusion list
        should_exclude = False
        for excluded_path in EXCLUDED_PATHS:
            if excluded_path.lower() in path.lower():
                should_exclude = True
                break
        
        if should_exclude:
            skipped_non_planner += 1
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
            
            # Ensure op_id is a string
            if not isinstance(op_id, str):
                op_id = str(op_id) if op_id is not None else f"{method.lower()}_unknown"
            
            summary = str(op.get("summary") or "")
            description = str(op.get("description") or "")
            
            # Skip non-Planner operations
            if not _is_planner_operation(path, str(op_id)):
                skipped_non_planner += 1
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
                skipped_non_planner += 1
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
            
            try:
                ops.append(
                    PlannerOperation(
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
            except Exception as e:
                logger.warning(f"Error creating PlannerOperation for {op_id}: {e}")
                continue

    # Remove duplicates and sort
    uniq: Dict[str, PlannerOperation] = {}
    for op in ops:
        key = f"{op.op_id}_{op.http_method}_{op.path}"
        if key not in uniq:
            uniq[key] = op
    
    print(f"Planner filtering summary (MODIFY EXCLUDED_KEYWORDS/EXCLUDED_PATHS to change):")
    print(f"  - Skipped {skipped_count_ops} count operations")
    print(f"  - Skipped {skipped_non_planner} excluded operations (OneDrive, Teams, SharePoint, Outlook, etc.)")
    print(f"  - Found {len(uniq)} Planner operations (Plans, Buckets, Tasks)")
    print(f"  - Excluded keywords: {', '.join(EXCLUDED_KEYWORDS[:10])}{'...' if len(EXCLUDED_KEYWORDS) > 10 else ''}")
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
    # Ensure name is a string
    if not isinstance(name, str):
        name = str(name) if name is not None else "unknown"
    
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
    # Ensure name is a string
    if not isinstance(name, str):
        name = str(name) if name is not None else "unknown"
    
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

def extract_operation_parameters(op: PlannerOperation) -> Tuple[List[str], List[str], List[str]]:
    """Extract and categorize parameters from Planner operation."""
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
            
        # Ensure param_name is a string
        if not isinstance(param_name, str):
            param_name = str(param_name) if param_name is not None else "unknown"
            
        # Skip if this is already handled as a path parameter
        if any(path_param['original'] == param_name or path_param['name'] == param_name for path_param in op.path_params):
            continue
            
        clean_name = sanitize_py_name(param_name)
        param_schema = param.get('schema', {})
        param_type = _get_python_type(param_schema)
        param_location = param.get('in', 'query')
        is_required = param.get('required', False)
        description = param.get('description', f'{param_location} parameter')
        
        # Ensure description is a string
        if not isinstance(description, str):
            description = str(description) if description is not None else f'{param_location} parameter'
        
        if is_required:
            required_params.append(f"{clean_name}: {param_type}")
            param_docs.append(f"            {clean_name} ({param_type}, required): {description}")
        else:
            optional_params.append(f"{clean_name}: Optional[{param_type}] = None")
            param_docs.append(f"            {clean_name} ({param_type}, optional): {description}")
    
    # Add Planner-specific OData parameters
    planner_odata_params = [
        "select: Optional[List[str]] = None",
        "expand: Optional[List[str]] = None", 
        "filter: Optional[str] = None",
        "orderby: Optional[str] = None",
        "search: Optional[str] = None",
        "top: Optional[int] = None",
        "skip: Optional[int] = None"
    ]
    optional_params.extend(planner_odata_params)
    
    # Add Planner OData parameter docs
    planner_odata_docs = [
        "            select (optional): Select specific properties to return",
        "            expand (optional): Expand related entities (e.g., details, assignments, buckets)",
        "            filter (optional): Filter the results using OData syntax", 
        "            orderby (optional): Order the results by specified properties",
        "            search (optional): Search for plans, buckets, or tasks by content",
        "            top (optional): Limit number of results returned",
        "            skip (optional): Skip number of results for pagination"
    ]
    param_docs.extend(planner_odata_docs)
    
    # Add request body if needed (for Planner operations)
    has_body = op.request_body is not None
    if has_body:
        optional_params.append("request_body: Optional[Mapping[str, Any]] = None")
        param_docs.append("            request_body (optional): Request body data for Planner operations")
    
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
    """Get the appropriate query parameter classes for the Planner endpoint."""
    path_lower = path.lower()
    method_lower = method.lower()
    
    # Map paths to their corresponding query parameter classes
    if '/planner/plans' in path_lower and method_lower == 'get':
        return "PlansRequestBuilder.PlansRequestBuilderGetQueryParameters", "PlansRequestBuilder.PlansRequestBuilderGetRequestConfiguration"
    elif '/planner/buckets' in path_lower and method_lower == 'get':
        return "BucketsRequestBuilder.BucketsRequestBuilderGetQueryParameters", "BucketsRequestBuilder.BucketsRequestBuilderGetRequestConfiguration"
    elif '/planner/tasks' in path_lower and method_lower == 'get':
        return "TasksRequestBuilder.TasksRequestBuilderGetQueryParameters", "TasksRequestBuilder.TasksRequestBuilderGetRequestConfiguration"
    elif '/me/planner' in path_lower and method_lower == 'get':
        return "PlannerRequestBuilder.PlannerRequestBuilderGetQueryParameters", "PlannerRequestBuilder.PlannerRequestBuilderGetRequestConfiguration"
    elif '/users/{user-id}/planner' in path_lower and method_lower == 'get':
        return "PlannerRequestBuilder.PlannerRequestBuilderGetQueryParameters", "PlannerRequestBuilder.PlannerRequestBuilderGetRequestConfiguration"
    elif '/groups/{group-id}/planner' in path_lower and method_lower == 'get':
        return "PlannerRequestBuilder.PlannerRequestBuilderGetQueryParameters", "PlannerRequestBuilder.PlannerRequestBuilderGetRequestConfiguration"
    else:
        # Fallback to generic RequestConfiguration for unmatched endpoints
        return "RequestConfiguration", "RequestConfiguration"

def build_planner_method_code(op: PlannerOperation) -> str:
    """Generate comprehensive async method code for Planner operation."""
    required_params, optional_params, param_docs = extract_operation_parameters(op)

    # Build method signature
    all_params = required_params + optional_params
    
    if len(all_params) <= 5:
        sig = ", ".join(all_params)
    else:
        params_formatted = ",\n        ".join(all_params)
        sig = f"\n        {params_formatted}\n    "

    # Ensure summary is a string
    summary = op.summary or op.op_id
    if not isinstance(summary, str):
        summary = str(summary) if summary is not None else op.op_id
    
    params_doc = "\n".join(param_docs) if param_docs else "            (standard Planner parameters)"

    # Generate Planner-specific Graph SDK method call
    graph_method_call = _generate_planner_graph_call(op)
    # Determine query parameter classes during generation
    query_param_class, config_class = _get_query_param_classes(op.path, op.http_method)
    response_type = "PlannerResponse"

    method_code = f"""    async def {op.wrapper_name}({sig}) -> {response_type}:
        \"\"\"{summary}.
        Planner operation: {op.http_method} {op.path}
        Operation type: {op.operation_type}
        Args:
{params_doc}
        Returns:
            {response_type}: Planner response wrapper with success/data/error
        \"\"\"
        # Build query parameters including OData for Planner
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

            # Add consistency level for search operations in Planner
            if search:
                if not config.headers:
                    config.headers = {{}}
                config.headers['ConsistencyLevel'] = 'eventual'

{graph_method_call}
            return self._handle_planner_response(response)
        except Exception as e:
            return {response_type}(
                success=False,
                error=f"Planner API call failed: {{str(e)}}",
            )
"""

    return method_code

def _generate_planner_graph_call(op: PlannerOperation) -> str:
    """Generate Planner-specific Microsoft Graph SDK method call."""
    path = op.path
    method = op.http_method.lower()
    has_body = op.request_body is not None
    
    # Build request builder chain optimized for Planner
    request_builder = _build_planner_request_builder_chain(path, op.path_params)
    
    # Generate method call based on HTTP method
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

def _build_planner_request_builder_chain(path: str, path_params: List[Dict[str, str]]) -> str:
    """Build Planner-optimized Microsoft Graph SDK request builder chain."""
    # Start with base client
    builder = "self.client"
    
    # Ensure path is a string
    if not isinstance(path, str):
        path = str(path) if path is not None else ""
    
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
            elif prev_segment == "plans":
                builder += f".by_planner_plan_id({python_param})"
            elif prev_segment == "buckets":
                builder += f".by_planner_bucket_id({python_param})"
            elif prev_segment == "tasks":
                builder += f".by_planner_task_id({python_param})"
            elif prev_segment == "rosters":
                builder += f".by_planner_roster_id({python_param})"
            elif prev_segment == "assignments":
                builder += f".by_assignment_id({python_param})"
            elif prev_segment == "checklist":
                builder += f".by_checklist_item_id({python_param})"
            elif prev_segment == "members":
                builder += f".by_user_id({python_param})"
            else:
                # Generic fallback for other Planner parameters
                builder += f".by_{prev_segment[:-1] if prev_segment.endswith('s') else prev_segment}_id({python_param})"
                
        else:
            # Regular path segment - convert to appropriate Planner SDK method
            if segment == "me":
                builder += ".me"
            elif segment == "planner":
                builder += ".planner"
            elif segment == "plans":
                builder += ".plans"
            elif segment == "buckets":
                builder += ".buckets"
            elif segment == "tasks":
                builder += ".tasks"
            elif segment == "details":
                builder += ".details"
            elif segment == "assignments":
                builder += ".assignments"
            elif segment == "checklist":
                builder += ".checklist"
            elif segment == "rosters":
                builder += ".rosters"
            elif segment == "members":
                builder += ".members"
            elif segment == "assignedToTaskBoardFormat":
                builder += ".assigned_to_task_board_format"
            elif segment == "bucketTaskBoardFormat":
                builder += ".bucket_task_board_format"
            elif segment == "progressTaskBoardFormat":
                builder += ".progress_task_board_format"
            elif segment == "categoryDescriptions":
                builder += ".category_descriptions"
            elif segment == "contextDetails":
                builder += ".context_details"
            elif segment == "sharedWith":
                builder += ".shared_with"
            elif segment == "favoritePlans":
                builder += ".favorite_plans"
            elif segment == "recentPlans":
                builder += ".recent_plans"
            elif segment == "all":
                builder += ".all"
            elif segment == "delta":
                builder += ".delta"
            else:
                # Convert to snake_case for other segments
                snake_segment = to_snake(segment)
                builder += f".{snake_segment}"
        
        i += 1
    
    return builder

def build_planner_class_code(ops: Sequence[PlannerOperation]) -> str:
    """Generate the complete Planner client class."""
    # Group methods by operation type for better organization
    methods_by_type = {}
    for op in ops:
        if op.operation_type not in methods_by_type:
            methods_by_type[op.operation_type] = []
        methods_by_type[op.operation_type].append(op)
    
    class_name = "PlannerDataSource"
    response_class = "PlannerResponse"

    header = f"""

import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Mapping, Optional

# Import MS Graph specific query parameter classes for Planner
from msgraph.generated.planner.plans.plans_request_builder import PlansRequestBuilder # type: ignore
from msgraph.generated.planner.buckets.buckets_request_builder import BucketsRequestBuilder # type: ignore
from msgraph.generated.planner.tasks.tasks_request_builder import TasksRequestBuilder # type: ignore
from msgraph.generated.users.item.planner.planner_request_builder import PlannerRequestBuilder # type: ignore
from kiota_abstractions.base_request_configuration import RequestConfiguration # type: ignore

from app.sources.client.microsoft.microsoft import MSGraphClient

# Planner-specific response wrapper
class PlannerResponse:
    \"\"\"Standardized Planner API response wrapper.\"\"\"
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def __init__(self, success: bool, data: Optional[Dict[str, Any]] = None, error: Optional[str] = None, message: Optional[str] = None) -> None:
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
    Comprehensive Microsoft Planner API client with complete Plans, Buckets, and Tasks coverage.

    Features:
    - Complete Planner API coverage with {len(ops)} methods organized by operation type
    - Support for Personal Planner, User Planner, and Group Planner
    - Complete Plan operations: create, read, update, delete, manage plans
    - Complete Bucket operations: organize tasks into buckets within plans
    - Complete Task operations: create, assign, track, and manage tasks
    - Task Assignment and Progress tracking with detailed status updates
    - Task Board Format support: assigned-to, bucket, and progress views
    - Plan Details and Configuration: categories, descriptions, context
    - Roster Management for plan membership and permissions
    - Checklist support for task sub-items and detailed tracking
    - Search capabilities across plans, buckets, and tasks
    - Delta synchronization for efficient change tracking
    - Microsoft Graph SDK integration with Planner-specific optimizations
    - Async snake_case method names for all operations
    - Standardized {response_class} format for all responses
    - Comprehensive error handling and Planner-specific response processing

    EXCLUDED OPERATIONS (modify EXCLUDED_KEYWORDS list to change):
    - OneDrive operations (drive, drives, items)
    - Teams operations (chats, teams, channels)
    - SharePoint operations (sites, lists, document libraries)
    - OneNote operations (notebooks, sections, pages)
    - Outlook operations (messages, events, contacts, calendar, mail folders)
    - Directory operations (directoryObjects, devices)
    - Admin operations (admin, compliance, security)
    - To-Do operations (todo lists and tasks - separate from Planner)
    - Device management operations
    - Communications operations (communications, education, identity)

    Operation Types:
    - Plans operations: Plan management, creation, and configuration
    - Buckets operations: Task organization and bucket management
    - Tasks operations: Task creation, assignment, and tracking
    - Details operations: Plan details, task details, bucket details
    - Assignments operations: Task assignments, checklist items, progress
    - Rosters operations: Plan membership and roster management
    - Board Format operations: Task board views and formatting
    - Favorites operations: Favorite plans, recent plans, shared plans
    - Sync operations: Delta synchronization and change tracking
    - General operations: Base Planner functionality
    \"\"\"

    def __init__(self, client: MSGraphClient) -> None:
        \"\"\"Initialize with Microsoft Graph SDK client optimized for Planner.\"\"\"
        self.client = client.get_client().get_ms_graph_service_client()
        if not hasattr(self.client, "me"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("Planner client initialized with {len(ops)} methods")

    def _handle_planner_response(self, response: object) -> PlannerResponse:
        \"\"\"Handle Planner API response with comprehensive error handling.\"\"\"
        try:
            if response is None:
                return PlannerResponse(success=False, error="Empty response from Planner API")
            success = True
            error_msg = None

            # Enhanced error response handling for Planner operations
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

            return PlannerResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling Planner response: {{e}}")
            return PlannerResponse(success=False, error=str(e))

    def get_data_source(self) -> '{class_name}':
        \"\"\"Get the underlying Planner client.\"\"\"
        return self

"""

    # Add methods organized by operation type
    methods_section = ""
    
    # Define the order of operation types for better organization
    operation_order = ['plans', 'buckets', 'tasks', 'details', 'assignments', 'rosters', 'boardFormat', 'favorites', 'sync', 'general']
    
    for op_type in operation_order:
        if op_type in methods_by_type:
            methods = methods_by_type[op_type]
            methods_section += f"    # ========== {op_type.upper()} OPERATIONS ({len(methods)} methods) ==========\n\n"
            
            for op in methods:
                try:
                    method_code = build_planner_method_code(op)
                    methods_section += method_code + "\n"
                except Exception as e:
                    logger.warning(f"Error generating method code for {op.op_id}: {e}")
                    # Skip this method and continue
                    continue
    
    return header + methods_section + "\n\n"

# ---- Public entrypoints ----------------------------------------------------
def generate_planner_client(
    *,
    out_path: Optional[str] = None,
    spec_url: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> str:
    """Generate comprehensive Planner client. Returns file path."""
    out_filename = out_path or "ms_planner.py"
    
    print(f"Loading Microsoft Graph OpenAPI specification for Planner...")
    spec = load_spec(spec_url=spec_url, spec_path=spec_path)
    
    print(f"Extracting Planner operations with comprehensive coverage...")
    ops = extract_planner_operations(spec)
    
    print(f"Found {len(ops)} Planner operations with full parameter support")
    
    # Show breakdown by operation type
    ops_by_type = {}
    for op in ops:
        if op.operation_type not in ops_by_type:
            ops_by_type[op.operation_type] = 0
        ops_by_type[op.operation_type] += 1
    
    print("Operation breakdown:")
    for op_type, count in sorted(ops_by_type.items()):
        print(f"  - {op_type}: {count} methods")
    
    print("Generating comprehensive Planner async client code...")
    code = build_planner_class_code(ops)
    
    # Create microsoft directory (reuse the existing structure)
    script_dir = Path(__file__).parent if __file__ else Path('.')
    microsoft_dir = script_dir / 'microsoft'
    microsoft_dir.mkdir(exist_ok=True)
    
    # Write file
    full_path = microsoft_dir / out_filename
    full_path.write_text(code, encoding="utf-8")
    print(f"Saved comprehensive Planner client to: {full_path}")
    
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
        description="Generate comprehensive Planner API client"
    )
    ap.add_argument("--out", help="Output .py file path (default: planner_client.py)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--spec-url", default=None, help="Microsoft Graph OpenAPI spec URL")
    g.add_argument("--spec-path", help="Local spec file path (JSON/YAML)")
    ap.add_argument("--show-patterns", action="store_true", help="Show all Planner path patterns")
    
    return ap.parse_args(argv)

def _show_patterns() -> None:
    """Show all Planner path patterns that will be matched."""
    print("Planner API Path Patterns (Comprehensive Coverage):")
    print(f"Total patterns: {len(PLANNER_PATH_PATTERNS)}")
    print()
    
    # Group patterns by category
    pattern_groups = {
        "Personal Planner": [p for p in PLANNER_PATH_PATTERNS if p.startswith("/me/planner")],
        "User Planner": [p for p in PLANNER_PATH_PATTERNS if p.startswith("/users/") and "planner" in p],
        "Group Planner": [p for p in PLANNER_PATH_PATTERNS if p.startswith("/groups/") and "planner" in p],
        "Core Plans": [p for p in PLANNER_PATH_PATTERNS if p.startswith("/planner/plans")],
        "Core Buckets": [p for p in PLANNER_PATH_PATTERNS if p.startswith("/planner/buckets")],
        "Core Tasks": [p for p in PLANNER_PATH_PATTERNS if p.startswith("/planner/tasks")],
        "Task Assignments": [p for p in PLANNER_PATH_PATTERNS if "assignments" in p or "checklist" in p],
        "Board Formats": [p for p in PLANNER_PATH_PATTERNS if "TaskBoardFormat" in p],
        "Rosters": [p for p in PLANNER_PATH_PATTERNS if "rosters" in p],
        "Synchronization": [p for p in PLANNER_PATH_PATTERNS if "delta" in p],
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
    
    print(f"Starting comprehensive Planner API client generation...")
    
    try:
        out_path = generate_planner_client(
            out_path=ns.out,
            spec_url=ns.spec_url,
            spec_path=ns.spec_path,
        )
        
        print(f"\nPlanner comprehensive client generation completed!")
        print(f"Generated class: PlannerDataSource")
        print(f"Output file: {out_path}")
        print(f"Features:")
        print(f"  - Complete Planner API endpoint coverage (Plans, Buckets, Tasks)")
        print(f"  - Support for Personal, User, and Group Planner operations")
        print(f"  - Plan operations: create, read, update, delete, manage plans")
        print(f"  - Bucket operations: organize tasks into buckets within plans")
        print(f"  - Task operations: create, assign, track, and manage tasks")
        print(f"  - Advanced features: assignments, checklists, board formats, rosters")
        print(f"  - Async methods with comprehensive error handling")
        print(f"")
        print(f"🔧 TO MODIFY EXCLUSIONS:")
        print(f"  - Edit EXCLUDED_KEYWORDS list to add/remove excluded API keywords")
        print(f"  - Edit EXCLUDED_PATHS list to add/remove excluded path patterns")
        print(f"  - Current exclusions: OneDrive, Teams, SharePoint, OneNote, Outlook")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
