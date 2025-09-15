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
Microsoft SharePoint API Client Generator (Sites, Lists, Libraries Focus)

Generates a focused SharePoint API client with comprehensive endpoint coverage for:
- Sites (/sites, /sites/{site-id})
- Site Collections and Subsites (/sites/{site-id}/sites)
- Lists (/sites/{site-id}/lists, /sites/{site-id}/lists/{list-id})
- List Items (/sites/{site-id}/lists/{list-id}/items)
- Document Libraries (/sites/{site-id}/drives, /sites/{site-id}/drive)
- Content Types (/sites/{site-id}/contentTypes)
- Columns (/sites/{site-id}/columns, /sites/{site-id}/lists/{list-id}/columns)
- Pages (/sites/{site-id}/pages)
- Navigation (/sites/{site-id}/onenote/notebooks - SharePoint notebooks)
- Site Analytics (/sites/{site-id}/analytics)
- Site Permissions (/sites/{site-id}/permissions)
- Site Search (/sites/{site-id}/search)
- Site Templates and Features

Explicitly excludes:
- Personal OneDrive operations (/me/drive endpoints)
- User OneDrive operations (/users/{user-id}/drive endpoints)
- Outlook operations (/messages, /events, /contacts, /calendar endpoints)
- Teams operations (/chats, /teams endpoints)
- Personal OneNote operations (/me/onenote endpoints)
- User OneNote operations (/users/{user-id}/onenote endpoints)
- Planner operations (/planner endpoints)
- Directory operations (/directoryObjects, /users, /groups endpoints)
- Device operations (/devices endpoints)
- Security operations (/security endpoints)
- Compliance operations (/compliance endpoints)
- Admin operations (/admin endpoints)
- Non-SharePoint Microsoft Graph endpoints

EASY MODIFICATION:
To add/remove excluded APIs, modify the EXCLUDED_KEYWORDS and EXCLUDED_PATHS lists at the top of this file.

Enhanced Features:
- Complete SharePoint endpoint coverage with all path parameters
- Proper parameter extraction for site-id, list-id, item-id, drive-id, content-type-id
- Comprehensive method signatures with required and optional parameters
- Microsoft Graph SDK integration optimized for SharePoint operations
- Specialized SharePoint response handling and error management
- Support for site search, list management, and document library operations
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

# Keywords to exclude from SharePoint operations (ADD MORE HERE TO EXCLUDE)
EXCLUDED_KEYWORDS = [
    'me/drive',       # Exclude personal OneDrive operations
    'users/{user-id}/drive',  # Exclude user OneDrive operations
    'groups/{group-id}/drive',  # Exclude group OneDrive operations (keep site drives)
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
    'chat',           # Exclude Teams chat operations
    'chats',          # Exclude Teams chat operations
    'team',           # Exclude Teams operations (keep sites)
    'teams',          # Exclude Teams operations (keep sites)
    'channel',        # Exclude Teams channel operations
    'channels',       # Exclude Teams channel operations
    'me/onenote',     # Exclude personal OneNote operations (keep site notebooks)
    'users/{user-id}/onenote',  # Exclude user OneNote operations
    'planner',        # Exclude Planner operations
    'deviceAppManagement',
    'deviceManagement',
    'devices',
    'directoryObjects',
    'users/{user-id}',  # Exclude user management (keep site users in context)
    'groups/{group-id}',  # Exclude group management (keep site groups in context)
    'security',
    'compliance',
    'admin',
    'agreements',
    'directory',
    'solutions',
    'analytics/userActivity',  # Exclude user analytics (keep site analytics)
    'auditLogs',
    'identityGovernance',
    'storage',
    'connections',
    'workbook',       # Exclude Excel workbook operations (keep document libraries)
    'worksheet',      # Exclude Excel worksheet operations
    'todo',           # Exclude To-Do operations
    'communications',
    'education',
    'identity',
    'inferenceClassification',
    'extensions',
    'onenote',
    'termStore',
]

# Path patterns to exclude (ADD MORE PATTERNS HERE TO EXCLUDE)
EXCLUDED_PATHS = [
    '/me/drive',
    '/users/{user-id}/drive',
    '/groups/{group-id}/drive',
    '/drives/{drive-id}',  # Exclude generic drives (keep site drives)
    '/me/messages',
    '/users/{user-id}/messages',
    '/me/events',
    '/users/{user-id}/events',
    '/me/calendar',
    '/users/{user-id}/calendar',
    '/me/contacts',
    '/users/{user-id}/contacts',
    '/me/mailFolders',
    '/users/{user-id}/mailFolders',
    '/chats',
    '/teams/{team-id}',  # Exclude team management (keep site collections)
    '/me/onenote',
    '/users/{user-id}/onenote',
    '/groups/{group-id}/onenote',  # Exclude group notebooks (keep site notebooks)
    '/planner',
    '/deviceAppManagement',
    '/deviceManagement',
    '/devices',
    '/directoryObjects',
    '/users',
    '/groups',  # Exclude group management (keep site-specific group operations)
    '/security',
    '/compliance',
    '/admin',
    '/agreements',
    '/directory',
    '/solutions',
    '/auditLogs',
    '/identityGovernance',
    '/storage',
    '/connections',
    '/workbook',
    '/worksheets',
    '/todo',
    '/communications',
    '/education',
    '/identity',
    '/inferenceClassification',
    '/extensions',
    '/onenote',
    '/termStore'
]

# =============================================================================

# Microsoft Graph OpenAPI specification URLs
DEFAULT_SPEC_URL = "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/master/openapi/v1.0/openapi.yaml"
FALLBACK_SPEC_URLS = [
    "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/main/openapi/v1.0/openapi.yaml",
]

# Comprehensive SharePoint endpoint patterns (MODIFY THIS LIST TO ADD/REMOVE ENDPOINTS)
SHAREPOINT_PATH_PATTERNS = [
    # Sites - Core Operations
    "/sites",
    "/sites/{site-id}",
    "/sites/{site-id}/sites",  # Subsites
    "/sites/root",
    "/sites/{hostname}:/{server-relative-path}",
    "/sites/getAllSites",
    "/sites/getByPath(path='{site-path}')",
    "/sites/{site-id}/analytics",
    "/sites/{site-id}/analytics/allTime",
    "/sites/{site-id}/analytics/lastSevenDays",
    "/sites/{site-id}/analytics/itemActivityStats",
    "/sites/{site-id}/analytics/itemActivityStats/{item-activity-stat-id}",
    
    # Site Information and Settings
    "/sites/{site-id}/operations",
    "/sites/{site-id}/operations/{rich-long-running-operation-id}",
    "/sites/{site-id}/permissions",
    "/sites/{site-id}/permissions/{permission-id}",
    "/sites/{site-id}/termStores",
    "/sites/{site-id}/termStores/{termstore-id}",
    "/sites/{site-id}/termStores/{termstore-id}/sets",
    "/sites/{site-id}/termStores/{termstore-id}/sets/{set-id}",
    "/sites/{site-id}/termStores/{termstore-id}/sets/{set-id}/terms",
    "/sites/{site-id}/termStores/{termstore-id}/sets/{set-id}/terms/{term-id}",
    
    # Lists - Complete Operations
    "/sites/{site-id}/lists",
    "/sites/{site-id}/lists/{list-id}",
    "/sites/{site-id}/lists/{list-id}/items",
    "/sites/{site-id}/lists/{list-id}/items/{item-id}",
    "/sites/{site-id}/lists/{list-id}/items/{item-id}/versions",
    "/sites/{site-id}/lists/{list-id}/items/{item-id}/versions/{listitemversion-id}",
    "/sites/{site-id}/lists/{list-id}/items/{item-id}/driveItem",
    "/sites/{site-id}/lists/{list-id}/items/{item-id}/fields",
    "/sites/{site-id}/lists/{list-id}/items/{item-id}/analytics",
    "/sites/{site-id}/lists/{list-id}/contentTypes",
    "/sites/{site-id}/lists/{list-id}/contentTypes/{contenttype-id}",
    "/sites/{site-id}/lists/{list-id}/columns",
    "/sites/{site-id}/lists/{list-id}/columns/{columndefinition-id}",
    "/sites/{site-id}/lists/{list-id}/subscriptions",
    "/sites/{site-id}/lists/{list-id}/subscriptions/{subscription-id}",
    "/sites/{site-id}/lists/{list-id}/operations",
    "/sites/{site-id}/lists/{list-id}/operations/{rich-long-running-operation-id}",
    
    # Document Libraries (Site Drives)
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
    "/sites/{site-id}/drives/{drive-id}/items/{item-id}/permissions",
    "/sites/{site-id}/drives/{drive-id}/items/{item-id}/analytics",
    "/sites/{site-id}/drives/{drive-id}/search(q='{search-text}')",
    "/sites/{site-id}/drives/{drive-id}/sharedWithMe",
    "/sites/{site-id}/drives/{drive-id}/recent",
    "/sites/{site-id}/drives/{drive-id}/following",
    "/sites/{site-id}/drive",  # Default document library
    "/sites/{site-id}/drive/root",
    "/sites/{site-id}/drive/items/{item-id}",
    "/sites/{site-id}/drive/root/children",
    "/sites/{site-id}/drive/items/{item-id}/children",
    "/sites/{site-id}/drive/root:/item-path:",
    "/sites/{site-id}/drive/root:/item-path:/children",
    "/sites/{site-id}/drive/items/{item-id}/thumbnails",
    "/sites/{site-id}/drive/items/{item-id}/versions",
    "/sites/{site-id}/drive/items/{item-id}/permissions",
    "/sites/{site-id}/drive/search(q='{search-text}')",
    
    # Content Types - Site Level
    "/sites/{site-id}/contentTypes",
    "/sites/{site-id}/contentTypes/{contenttype-id}",
    "/sites/{site-id}/contentTypes/{contenttype-id}/columns",
    "/sites/{site-id}/contentTypes/{contenttype-id}/columns/{columndefinition-id}",
    "/sites/{site-id}/contentTypes/{contenttype-id}/columnLinks",
    "/sites/{site-id}/contentTypes/{contenttype-id}/columnLinks/{columnlink-id}",
    "/sites/{site-id}/contentTypes/{contenttype-id}/base",
    "/sites/{site-id}/contentTypes/{contenttype-id}/baseTypes",
    
    # Columns - Site Level
    "/sites/{site-id}/columns",
    "/sites/{site-id}/columns/{columndefinition-id}",
    
    # Pages - SharePoint Modern Pages
    "/sites/{site-id}/pages",
    "/sites/{site-id}/pages/{basesitepage-id}",
    "/sites/{site-id}/pages/{basesitepage-id}/canvasLayout",
    "/sites/{site-id}/pages/{basesitepage-id}/canvasLayout/horizontalSections",
    "/sites/{site-id}/pages/{basesitepage-id}/canvasLayout/horizontalSections/{horizontalsection-id}",
    "/sites/{site-id}/pages/{basesitepage-id}/canvasLayout/verticalSection",
    "/sites/{site-id}/pages/{basesitepage-id}/canvasLayout/verticalSection/webparts",
    "/sites/{site-id}/pages/{basesitepage-id}/canvasLayout/horizontalSections/{horizontalsection-id}/columns",
    "/sites/{site-id}/pages/{basesitepage-id}/canvasLayout/horizontalSections/{horizontalsection-id}/columns/{horizontalsectioncolumn-id}",
    "/sites/{site-id}/pages/{basesitepage-id}/canvasLayout/horizontalSections/{horizontalsection-id}/columns/{horizontalsectioncolumn-id}/webparts",
    "/sites/{site-id}/pages/{basesitepage-id}/canvasLayout/horizontalSections/{horizontalsection-id}/columns/{horizontalsectioncolumn-id}/webparts/{webpart-id}",
    
    # SharePoint OneNote Notebooks (Site-specific)
    "/sites/{site-id}/onenote",
    "/sites/{site-id}/onenote/notebooks",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}/sections",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}/pages",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}/pages/{onenote-page-id}",
    "/sites/{site-id}/onenote/sections",
    "/sites/{site-id}/onenote/sections/{onenote-section-id}",
    "/sites/{site-id}/onenote/sections/{onenote-section-id}/pages",
    "/sites/{site-id}/onenote/pages",
    "/sites/{site-id}/onenote/pages/{onenote-page-id}",
    
    # Site External Columns (Business Data)
    "/sites/{site-id}/externalColumns",
    "/sites/{site-id}/externalColumns/{columndefinition-id}",
    
    # Site Information Architecture
    "/sites/{site-id}/informationProtection",
    "/sites/{site-id}/informationProtection/policy",
    "/sites/{site-id}/informationProtection/policy/labels",
    "/sites/{site-id}/informationProtection/policy/labels/{informationprotectionlabel-id}",
    
    # Site Recycle Bin
    "/sites/{site-id}/recycleBin",
    "/sites/{site-id}/recycleBin/items",
    "/sites/{site-id}/recycleBin/items/{recyclebinitem-id}",
    
    # NOTE: Excluded patterns (personal OneDrive, Outlook, Teams personal) are filtered out by EXCLUDED_KEYWORDS and EXCLUDED_PATHS
    # To re-enable them, remove the keywords from the exclusion lists above
]

# ---- Operation Model -------------------------------------------------------
@dataclass
class SharePointOperation:
    op_id: str
    http_method: str
    path: str
    summary: str
    description: str
    params: List[Mapping[str, Any]]
    request_body: Optional[Mapping[str, Any]] = None
    path_params: List[Dict[str, str]] = None
    operation_type: str = "general"  # sites, lists, drives, pages, contentTypes, columns, onenote, permissions

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
        """Determine the type of SharePoint operation for better organization."""
        path_lower = self.path.lower()
        op_lower = self.op_id.lower()
        
        if any(keyword in path_lower for keyword in ["list", "lists", "items"]):
            return "lists"
        elif any(keyword in path_lower for keyword in ["drives", "drive", "root:", "children", "driveitem"]):
            return "drives"
        elif any(keyword in path_lower for keyword in ["pages", "canvaslayout", "webparts", "horizontalsection"]):
            return "pages"
        elif any(keyword in path_lower for keyword in ["contenttypes", "contenttype"]):
            return "contentTypes"
        elif any(keyword in path_lower for keyword in ["columns", "columndefinition", "columnlinks"]):
            return "columns"
        elif any(keyword in path_lower for keyword in ["onenote", "notebooks", "sections"]):
            return "onenote"
        elif any(keyword in path_lower for keyword in ["permissions", "permission"]):
            return "permissions"
        elif any(keyword in path_lower for keyword in ["analytics", "itemactivitystats"]):
            return "analytics"
        elif any(keyword in path_lower for keyword in ["termstores", "termstore", "sets", "terms"]):
            return "termStore"
        elif any(keyword in path_lower for keyword in ["operations", "subscriptions"]):
            return "operations"
        elif any(keyword in path_lower for keyword in ["recyclebin", "recycleBin"]):
            return "recycleBin"
        elif any(keyword in path_lower for keyword in ["informationprotection", "labels"]):
            return "informationProtection"
        elif any(keyword in path_lower for keyword in ["sites", "site", "subsites"]):
            return "sites"
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
    """Extract path parameters from SharePoint path pattern."""
    import re
    
    # Find all {parameter} patterns
    pattern = r'\{([^}]+)\}'
    matches = re.findall(pattern, path)
    
    path_params = []
    for match in matches:
        # Clean up parameter name and convert to Python-friendly format
        param_name = match.replace('-', '_').replace('.', '_')
        
        # Determine parameter type based on common SharePoint patterns
        param_type = 'str'
        description = f'SharePoint path parameter: {match}'
        
        if 'id' in match.lower():
            description = f'SharePoint {match.replace("-", " ").replace("_", " ")} identifier'
        elif 'site' in match.lower():
            description = f'SharePoint site identifier: {match}'
        elif 'list' in match.lower():
            description = f'SharePoint list identifier: {match}'
        elif 'item' in match.lower():
            description = f'SharePoint item identifier: {match}'
        elif 'drive' in match.lower():
            description = f'SharePoint drive identifier: {match}'
        elif 'page' in match.lower():
            description = f'SharePoint page identifier: {match}'
        elif 'content' in match.lower():
            description = f'SharePoint content type identifier: {match}'
        elif 'column' in match.lower():
            description = f'SharePoint column identifier: {match}'
        elif 'notebook' in match.lower():
            description = f'SharePoint notebook identifier: {match}'
        elif 'section' in match.lower():
            description = f'SharePoint section identifier: {match}'
        elif 'path' in match.lower():
            description = f'SharePoint path: {match}'
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

def _is_sharepoint_operation(path: str, op_id: str) -> bool:
    """Check if this operation is related to SharePoint (Sites, Lists, Libraries).
    
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
    
    # Include operations that are clearly SharePoint related (CORE SHAREPOINT KEYWORDS)
    sharepoint_core_keywords = [
        'sites', 'site', 'lists', 'list', 'items', 'item',
        'drives', 'drive', 'pages', 'page', 'contenttypes', 'contenttype',
        'columns', 'column', 'termstores', 'termstore', 'sets', 'terms',
        'permissions', 'permission', 'analytics', 'operations',
        'recyclebin', 'informationprotection', 'subsites',
        'canvaslayout', 'webparts', 'horizontalsection', 'verticalsection',
        'onenote', 'notebooks', 'sections',  # Site-specific OneNote
        'externalcolumns', 'columnlinks', 'basetypes'
    ]
    
    # Special case: include site-specific operations only
    if path_lower.startswith('/sites/'):
        return any(keyword in path_lower or keyword in op_lower for keyword in sharepoint_core_keywords)
    
    return False

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

def extract_sharepoint_operations(spec: Mapping[str, Any]) -> List[SharePointOperation]:
    """Extract SharePoint-specific operations from OpenAPI spec."""
    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("Invalid OpenAPI spec: missing 'paths'")

    ops: List[SharePointOperation] = []
    skipped_count_ops = 0
    skipped_non_sharepoint = 0
    
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
            
        # Check if path matches any SharePoint patterns
        if not any(_path_matches_pattern(path, pattern) for pattern in SHAREPOINT_PATH_PATTERNS):
            # Additional check for SharePoint-related operations
            if not _is_sharepoint_operation(path, ""):
                skipped_non_sharepoint += 1
                continue
        
        # ============ EXCLUSION CHECK (MODIFY EXCLUDED_PATHS LIST ABOVE) ============
        # Explicitly exclude paths based on exclusion list
        should_exclude = False
        for excluded_path in EXCLUDED_PATHS:
            if excluded_path.lower() in path.lower():
                should_exclude = True
                break
        
        if should_exclude:
            skipped_non_sharepoint += 1
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
            
            # Skip non-SharePoint operations
            if not _is_sharepoint_operation(path, str(op_id)):
                skipped_non_sharepoint += 1
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
                skipped_non_sharepoint += 1
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
                SharePointOperation(
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
    uniq: Dict[str, SharePointOperation] = {}
    for op in ops:
        key = f"{op.op_id}_{op.http_method}_{op.path}"
        if key not in uniq:
            uniq[key] = op
    
    print(f"SharePoint filtering summary (MODIFY EXCLUDED_KEYWORDS/EXCLUDED_PATHS to change):")
    print(f"  - Skipped {skipped_count_ops} count operations")
    print(f"  - Skipped {skipped_non_sharepoint} excluded operations (OneDrive personal, Outlook, Teams, etc.)")
    print(f"  - Found {len(uniq)} SharePoint operations (Sites, Lists, Libraries)")
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

def extract_operation_parameters(op: SharePointOperation) -> Tuple[List[str], List[str], List[str]]:
    """Extract and categorize parameters from SharePoint operation."""
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
    
    # Add SharePoint-specific OData parameters
    sharepoint_odata_params = [
        "select: Optional[List[str]] = None",
        "expand: Optional[List[str]] = None", 
        "filter: Optional[str] = None",
        "orderby: Optional[str] = None",
        "search: Optional[str] = None",
        "top: Optional[int] = None",
        "skip: Optional[int] = None"
    ]
    optional_params.extend(sharepoint_odata_params)
    
    # Add SharePoint OData parameter docs
    sharepoint_odata_docs = [
        "            select (optional): Select specific properties to return",
        "            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)",
        "            filter (optional): Filter the results using OData syntax", 
        "            orderby (optional): Order the results by specified properties",
        "            search (optional): Search for sites, lists, or items by content",
        "            top (optional): Limit number of results returned",
        "            skip (optional): Skip number of results for pagination"
    ]
    param_docs.extend(sharepoint_odata_docs)
    
    # Add request body if needed (for SharePoint operations)
    has_body = op.request_body is not None
    if has_body:
        optional_params.append("request_body: Optional[Mapping[str, Any]] = None")
        param_docs.append("            request_body (optional): Request body data for SharePoint operations")
    
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
    """Get the appropriate query parameter classes for the SharePoint endpoint."""
    path_lower = path.lower()
    method_lower = method.lower()
    
    # Map paths to their corresponding query parameter classes
    # Updated with correct nested class names from Microsoft Graph SDK
    if '/sites/{site-id}/lists' in path_lower and method_lower == 'get':
        return "ListsRequestBuilder.ListsRequestBuilderGetQueryParameters", "ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration"
    elif '/sites/{site-id}/drives' in path_lower and method_lower == 'get':
        return "DrivesRequestBuilder.DrivesRequestBuilderGetQueryParameters", "DrivesRequestBuilder.DrivesRequestBuilderGetRequestConfiguration"
    elif '/sites/{site-id}/contentTypes' in path_lower and method_lower == 'get':
        return "ContentTypesRequestBuilder.ContentTypesRequestBuilderGetQueryParameters", "ContentTypesRequestBuilder.ContentTypesRequestBuilderGetRequestConfiguration"
    elif '/sites/{site-id}/columns' in path_lower and method_lower == 'get':
        return "ColumnsRequestBuilder.ColumnsRequestBuilderGetQueryParameters", "ColumnsRequestBuilder.ColumnsRequestBuilderGetRequestConfiguration"
    elif '/sites/{site-id}/pages' in path_lower and method_lower == 'get':
        return "PagesRequestBuilder.PagesRequestBuilderGetQueryParameters", "PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration"
    elif '/sites' in path_lower and method_lower == 'get':
        return "SitesRequestBuilder.SitesRequestBuilderGetQueryParameters", "SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration"
    else:
        # Fallback to generic RequestConfiguration for unmatched endpoints
        return "RequestConfiguration", "RequestConfiguration"

def build_sharepoint_method_code(op: SharePointOperation) -> str:
    """Generate comprehensive async method code for SharePoint operation."""
    required_params, optional_params, param_docs = extract_operation_parameters(op)

    # Build method signature
    all_params = required_params + optional_params
    
    if len(all_params) <= 5:
        sig = ", ".join(all_params)
    else:
        params_formatted = ",\n        ".join(all_params)
        sig = f"\n        {params_formatted}\n    "

    summary = op.summary or op.op_id
    params_doc = "\n".join(param_docs) if param_docs else "            (standard SharePoint parameters)"

    # Generate SharePoint-specific Graph SDK method call
    graph_method_call = _generate_sharepoint_graph_call(op)
    # Determine query parameter classes during generation
    query_param_class, config_class = _get_query_param_classes(op.path, op.http_method)
    response_type = "SharePointResponse"

    method_code = f"""    async def {op.wrapper_name}({sig}) -> {response_type}:
        \"\"\"{summary}.
        SharePoint operation: {op.http_method} {op.path}
        Operation type: {op.operation_type}
        Args:
{params_doc}
        Returns:
            {response_type}: SharePoint response wrapper with success/data/error
        \"\"\"
        # Build query parameters including OData for SharePoint
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
            
            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {{}}
                config.headers['ConsistencyLevel'] = 'eventual'

{graph_method_call}
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return {response_type}(
                success=False,
                error=f"SharePoint API call failed: {{str(e)}}",
            )
"""

    return method_code

def _generate_sharepoint_graph_call(op: SharePointOperation) -> str:
    """Generate SharePoint-specific Microsoft Graph SDK method call."""
    path = op.path
    method = op.http_method.lower()
    has_body = op.request_body is not None
    
    # Build request builder chain optimized for SharePoint
    request_builder = _build_sharepoint_request_builder_chain(path, op.path_params)
    
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

def _build_sharepoint_request_builder_chain(path: str, path_params: List[Dict[str, str]]) -> str:
    """Build SharePoint-optimized Microsoft Graph SDK request builder chain."""
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
            
            if prev_segment == "sites":
                builder += f".by_site_id({python_param})"
            elif prev_segment == "lists":
                builder += f".by_list_id({python_param})"
            elif prev_segment == "items":
                builder += f".by_list_item_id({python_param})"
            elif prev_segment == "drives":
                builder += f".by_drive_id({python_param})"
            elif prev_segment == "pages":
                builder += f".by_base_site_page_id({python_param})"
            elif prev_segment == "contentTypes":
                builder += f".by_content_type_id({python_param})"
            elif prev_segment == "columns":
                builder += f".by_column_definition_id({python_param})"
            elif prev_segment == "notebooks":
                builder += f".by_notebook_id({python_param})"
            elif prev_segment == "sections":
                builder += f".by_onenote_section_id({python_param})"
            elif prev_segment == "permissions":
                builder += f".by_permission_id({python_param})"
            elif prev_segment == "operations":
                builder += f".by_rich_long_running_operation_id({python_param})"
            elif prev_segment == "subscriptions":
                builder += f".by_subscription_id({python_param})"
            elif prev_segment == "versions":
                builder += f".by_list_item_version_id({python_param})"
            elif prev_segment == "termStores":
                builder += f".by_term_store_id({python_param})"
            elif prev_segment == "sets":
                builder += f".by_set_id({python_param})"
            elif prev_segment == "terms":
                builder += f".by_term_id({python_param})"
            elif prev_segment == "horizontalSections":
                builder += f".by_horizontal_section_id({python_param})"
            elif prev_segment == "webparts":
                builder += f".by_web_part_id({python_param})"
            elif prev_segment == "columnLinks":
                builder += f".by_column_link_id({python_param})"
            else:
                # Generic fallback for other SharePoint parameters
                builder += f".by_{prev_segment[:-1] if prev_segment.endswith('s') else prev_segment}_id({python_param})"
                
        elif segment.startswith("search("):
            # Handle search operations specially
            builder += ".search"
        elif ":" in segment and segment.endswith(":"):
            # Handle SharePoint path-based operations like root:/path/to/file:
            if segment.startswith("root:"):
                path_part = segment[5:-1]  # Remove "root:" and trailing ":"
                if path_part:
                    builder += f".item_with_path('{path_part}')"
                else:
                    builder += ".root"
            else:
                # Generic path-based operation
                builder += f".item_with_path('{segment[:-1]}')"
        elif segment.startswith("getByPath("):
            # Handle getByPath operations
            builder += ".get_by_path"
        elif segment == "getAllSites":
            # Handle getAllSites operation
            builder += ".get_all_sites"
        else:
            # Regular path segment - convert to appropriate SharePoint SDK method
            if segment == "sites":
                builder += ".sites"
            elif segment == "root":
                builder += ".root"
            elif segment == "lists":
                builder += ".lists"
            elif segment == "items":
                builder += ".items"
            elif segment == "drives":
                builder += ".drives"
            elif segment == "drive":
                builder += ".drive"
            elif segment == "children":
                builder += ".children"
            elif segment == "pages":
                builder += ".pages"
            elif segment == "contentTypes":
                builder += ".content_types"
            elif segment == "columns":
                builder += ".columns"
            elif segment == "columnLinks":
                builder += ".column_links"
            elif segment == "baseTypes":
                builder += ".base_types"
            elif segment == "base":
                builder += ".base"
            elif segment == "fields":
                builder += ".fields"
            elif segment == "versions":
                builder += ".versions"
            elif segment == "driveItem":
                builder += ".drive_item"
            elif segment == "analytics":
                builder += ".analytics"
            elif segment == "allTime":
                builder += ".all_time"
            elif segment == "lastSevenDays":
                builder += ".last_seven_days"
            elif segment == "itemActivityStats":
                builder += ".item_activity_stats"
            elif segment == "permissions":
                builder += ".permissions"
            elif segment == "operations":
                builder += ".operations"
            elif segment == "subscriptions":
                builder += ".subscriptions"
            elif segment == "termStores":
                builder += ".term_stores"
            elif segment == "sets":
                builder += ".sets"
            elif segment == "terms":
                builder += ".terms"
            elif segment == "onenote":
                builder += ".onenote"
            elif segment == "notebooks":
                builder += ".notebooks"
            elif segment == "sections":
                builder += ".sections"
            elif segment == "canvasLayout":
                builder += ".canvas_layout"
            elif segment == "horizontalSections":
                builder += ".horizontal_sections"
            elif segment == "verticalSection":
                builder += ".vertical_section"
            elif segment == "webparts":
                builder += ".webparts"
            elif segment == "externalColumns":
                builder += ".external_columns"
            elif segment == "informationProtection":
                builder += ".information_protection"
            elif segment == "policy":
                builder += ".policy"
            elif segment == "labels":
                builder += ".labels"
            elif segment == "recycleBin":
                builder += ".recycle_bin"
            elif segment == "thumbnails":
                builder += ".thumbnails"
            elif segment == "sharedWithMe":
                builder += ".shared_with_me"
            elif segment == "recent":
                builder += ".recent"
            elif segment == "following":
                builder += ".following"
            else:
                # Convert to snake_case for other segments
                snake_segment = to_snake(segment)
                builder += f".{snake_segment}"
        
        i += 1
    
    return builder

def build_sharepoint_class_code(ops: Sequence[SharePointOperation]) -> str:
    """Generate the complete SharePoint client class."""
    # Group methods by operation type for better organization
    methods_by_type = {}
    for op in ops:
        if op.operation_type not in methods_by_type:
            methods_by_type[op.operation_type] = []
        methods_by_type[op.operation_type].append(op)
    
    class_name = "SharePointDataSource"
    response_class = "SharePointResponse"

    header = f"""

import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Mapping, Optional

# Import MS Graph specific query parameter classes for SharePoint
from msgraph.generated.sites.sites_request_builder import SitesRequestBuilder # type: ignore
from msgraph.generated.sites.item.lists.lists_request_builder import ListsRequestBuilder # type: ignore
from msgraph.generated.sites.item.drives.drives_request_builder import DrivesRequestBuilder # type: ignore
from msgraph.generated.sites.item.content_types.content_types_request_builder import ContentTypesRequestBuilder # type: ignore
from msgraph.generated.sites.item.columns.columns_request_builder import ColumnsRequestBuilder # type: ignore
from msgraph.generated.sites.item.pages.pages_request_builder import PagesRequestBuilder # type: ignore
from kiota_abstractions.base_request_configuration import RequestConfiguration # type: ignore

from app.sources.client.microsoft.microsoft import MSGraphClient

# SharePoint-specific response wrapper
class SharePointResponse:
    \"\"\"Standardized SharePoint API response wrapper.\"\"\"
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
    Comprehensive Microsoft SharePoint API client with complete Sites, Lists, and Libraries coverage.

    Features:
    - Complete SharePoint API coverage with {len(ops)} methods organized by operation type
    - Support for Sites, Site Collections, and Subsites
    - Complete List operations: lists, items, content types, columns
    - Complete Document Library operations: drives, folders, files
    - Modern Page operations: pages, canvas layout, web parts
    - Site-specific OneNote operations: notebooks, sections, pages
    - Site Analytics and Activity tracking
    - Site Permissions and Information Protection
    - Term Store and Metadata management
    - Site Search and Discovery capabilities
    - Microsoft Graph SDK integration with SharePoint-specific optimizations
    - Async snake_case method names for all operations
    - Standardized {response_class} format for all responses
    - Comprehensive error handling and SharePoint-specific response processing

    EXCLUDED OPERATIONS (modify EXCLUDED_KEYWORDS list to change):
    - Personal OneDrive operations (/me/drive, /users/{{user-id}}/drive)
    - Outlook operations (messages, events, contacts, calendar, mail folders)
    - Teams operations (chats, teams, channels)
    - Personal OneNote operations (/me/onenote, /users/{{user-id}}/onenote)
    - Planner operations (plans, tasks, buckets)
    - Directory operations (users, groups, directory objects)
    - Device management operations (devices, device management)
    - Admin operations (admin, compliance, security)
    - Generic drives operations (drives without site context)
    - User activity analytics (keep site analytics)
    - Communications operations (communications, education, identity)

    Operation Types:
    - Sites operations: Site collections, subsites, site information
    - Lists operations: Lists, list items, fields, content types
    - Drives operations: Document libraries, folders, files
    - Pages operations: Modern pages, canvas layout, web parts
    - Content Types operations: Site and list content types
    - Columns operations: Site and list columns, column definitions
    - OneNote operations: Site-specific notebooks, sections, pages
    - Permissions operations: Site and item permissions
    - Analytics operations: Site analytics and activity stats
    - Term Store operations: Managed metadata, term sets, terms
    - Operations operations: Long-running operations, subscriptions
    - Recycle Bin operations: Deleted items and restoration
    - Information Protection operations: Labels and policies
    - General operations: Base SharePoint functionality
    \"\"\"

    def __init__(self, client: MSGraphClient) -> None:
        \"\"\"Initialize with Microsoft Graph SDK client optimized for SharePoint.\"\"\"
        self.client = client.get_client().get_ms_graph_service_client()
        if not hasattr(self.client, "sites"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("SharePoint client initialized with {len(ops)} methods")

    def _handle_sharepoint_response(self, response: object) -> SharePointResponse:
        \"\"\"Handle SharePoint API response with comprehensive error handling.\"\"\"
        try:
            if response is None:
                return SharePointResponse(success=False, error="Empty response from SharePoint API")
            
            success = True
            error_msg = None
            
            # Enhanced error response handling for SharePoint operations
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
            
            return SharePointResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling SharePoint response: {{e}}")
            return SharePointResponse(success=False, error=str(e))

    def get_data_source(self) -> '{class_name}':
        \"\"\"Get the underlying SharePoint client.\"\"\"
        return self

"""

    # Add methods organized by operation type
    methods_section = ""
    
    # Define the order of operation types for better organization
    operation_order = ['sites', 'lists', 'drives', 'pages', 'contentTypes', 'columns', 'onenote', 'permissions', 'analytics', 'termStore', 'operations', 'recycleBin', 'informationProtection', 'general']
    
    for op_type in operation_order:
        if op_type in methods_by_type:
            methods = methods_by_type[op_type]
            methods_section += f"    # ========== {op_type.upper()} OPERATIONS ({len(methods)} methods) ==========\n\n"
            
            for op in methods:
                method_code = build_sharepoint_method_code(op)
                methods_section += method_code + "\n"
    
    return header + methods_section + "\n\n"

# ---- Public entrypoints ----------------------------------------------------
def generate_sharepoint_client(
    *,
    out_path: Optional[str] = None,
    spec_url: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> str:
    """Generate comprehensive SharePoint client. Returns file path."""
    out_filename = out_path or "sharepoint_client.py"
    
    print(f"Loading Microsoft Graph OpenAPI specification for SharePoint...")
    spec = load_spec(spec_url=spec_url, spec_path=spec_path)
    
    print(f"Extracting SharePoint operations with comprehensive coverage...")
    ops = extract_sharepoint_operations(spec)
    
    print(f"Found {len(ops)} SharePoint operations with full parameter support")
    
    # Show breakdown by operation type
    ops_by_type = {}
    for op in ops:
        if op.operation_type not in ops_by_type:
            ops_by_type[op.operation_type] = 0
        ops_by_type[op.operation_type] += 1
    
    print("Operation breakdown:")
    for op_type, count in sorted(ops_by_type.items()):
        print(f"  - {op_type}: {count} methods")
    
    print("Generating comprehensive SharePoint async client code...")
    code = build_sharepoint_class_code(ops)
    
    # Create microsoft directory (reuse the existing structure)
    script_dir = Path(__file__).parent if __file__ else Path('.')
    microsoft_dir = script_dir / 'microsoft'
    microsoft_dir.mkdir(exist_ok=True)
    
    # Write file
    full_path = microsoft_dir / out_filename
    full_path.write_text(code, encoding="utf-8")
    print(f"Saved comprehensive SharePoint client to: {full_path}")
    
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
        description="Generate comprehensive SharePoint API client"
    )
    ap.add_argument("--out", help="Output .py file path (default: sharepoint_client.py)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--spec-url", default=None, help="Microsoft Graph OpenAPI spec URL")
    g.add_argument("--spec-path", help="Local spec file path (JSON/YAML)")
    ap.add_argument("--show-patterns", action="store_true", help="Show all SharePoint path patterns")
    
    return ap.parse_args(argv)

def _show_patterns() -> None:
    """Show all SharePoint path patterns that will be matched."""
    print("SharePoint API Path Patterns (Comprehensive Coverage):")
    print(f"Total patterns: {len(SHAREPOINT_PATH_PATTERNS)}")
    print()
    
    # Group patterns by category
    pattern_groups = {
        "Sites & Site Collections": [p for p in SHAREPOINT_PATH_PATTERNS if p.startswith("/sites") and "/lists" not in p and "/drives" not in p and "/pages" not in p],
        "Lists & List Items": [p for p in SHAREPOINT_PATH_PATTERNS if "/lists" in p],
        "Document Libraries": [p for p in SHAREPOINT_PATH_PATTERNS if "/drives" in p or "/drive" in p],
        "Modern Pages": [p for p in SHAREPOINT_PATH_PATTERNS if "/pages" in p or "canvas" in p.lower()],
        "Content Types": [p for p in SHAREPOINT_PATH_PATTERNS if "contentTypes" in p],
        "Columns": [p for p in SHAREPOINT_PATH_PATTERNS if "/columns" in p],
        "Site OneNote": [p for p in SHAREPOINT_PATH_PATTERNS if "/onenote" in p],
        "Analytics & Operations": [p for p in SHAREPOINT_PATH_PATTERNS if "/analytics" in p or "/operations" in p],
        "Term Store": [p for p in SHAREPOINT_PATH_PATTERNS if "termStores" in p or "sets" in p or "terms" in p],
        "Permissions & Protection": [p for p in SHAREPOINT_PATH_PATTERNS if "/permissions" in p or "informationProtection" in p],
        "Other Features": [p for p in SHAREPOINT_PATH_PATTERNS if "recycleBin" in p or "external" in p.lower()],
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
    
    print(f"Starting comprehensive SharePoint API client generation...")
    
    try:
        out_path = generate_sharepoint_client(
            out_path=ns.out,
            spec_url=ns.spec_url,
            spec_path=ns.spec_path,
        )
        
        print(f"\nSharePoint comprehensive client generation completed!")
        print(f"Generated class: SharePointDataSource")
        print(f"Output file: {out_path}")
        print(f"Features:")
        print(f"  - Complete SharePoint API endpoint coverage (Sites, Lists, Libraries)")
        print(f"  - Support for Site Collections, Subsites, and Site Management")
        print(f"  - List operations: lists, items, content types, columns, fields")
        print(f"  - Document Library operations: drives, folders, files, permissions")
        print(f"  - Modern Page operations: pages, canvas layout, web parts")
        print(f"  - Site-specific OneNote: notebooks, sections, pages")
        print(f"  - Advanced features: analytics, term store, permissions, search")
        print(f"  - Async methods with comprehensive error handling")
        print(f"")
        print(f"🔧 TO MODIFY EXCLUSIONS:")
        print(f"  - Edit EXCLUDED_KEYWORDS list to add/remove excluded API keywords")
        print(f"  - Edit EXCLUDED_PATHS list to add/remove excluded path patterns")
        print(f"  - Current exclusions: Personal OneDrive, Outlook, Teams, Personal OneNote")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()