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
Microsoft OneNote API Client Generator (Notebooks, Sections, Pages Focus)

Generates a focused OneNote API client with comprehensive endpoint coverage for:
- Personal OneNote (/me/onenote)
- User OneNote (/users/{user-id}/onenote)
- Group OneNote (/groups/{group-id}/onenote)
- Site OneNote (/sites/{site-id}/onenote)
- Notebooks (/onenote/notebooks, /onenote/notebooks/{notebook-id})
- Sections (/onenote/sections, /onenote/notebooks/{notebook-id}/sections)
- Section Groups (/onenote/sectionGroups)
- Pages (/onenote/pages, /onenote/sections/{section-id}/pages)
- Page Content (/onenote/pages/{page-id}/content)
- Page Preview (/onenote/pages/{page-id}/preview)
- Resources (/onenote/resources/{resource-id})
- Operations (/onenote/operations/{operation-id})

Explicitly excludes:
- OneDrive operations (/drive, /drives endpoints)
- Teams operations (/chats, /teams endpoints)
- SharePoint operations (/sites endpoints without onenote)
- Outlook operations (/messages, /events, /contacts, /calendar endpoints)
- Planner operations (/planner endpoints)
- Directory operations (/directoryObjects endpoints)
- Device operations (/devices endpoints)
- Security operations (/security endpoints)
- Compliance operations (/compliance endpoints)
- Admin operations (/admin endpoints)
- Non-OneNote Microsoft Graph endpoints

EASY MODIFICATION:
To add/remove excluded APIs, modify the EXCLUDED_KEYWORDS and EXCLUDED_PATHS lists at the top of this file.

Enhanced Features:
- Complete OneNote endpoint coverage with all path parameters
- Proper parameter extraction for notebook-id, section-id, page-id, resource-id
- Comprehensive method signatures with required and optional parameters
- Microsoft Graph SDK integration optimized for OneNote operations
- Specialized OneNote response handling and error management
- Support for notebook management, section operations, and page content
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
# EXCLUSION CONFIGURATION - Modify this section to add/remove excluded APIs
# =============================================================================

# Keywords to exclude from OneNote operations (ADD MORE HERE TO EXCLUDE)
EXCLUDED_KEYWORDS = [
    'drive',          # Exclude OneDrive operations
    'drives',         # Exclude OneDrive operations
    'chat',           # Exclude Teams chat operations
    'chats',          # Exclude Teams chat operations
    'team',           # Exclude Teams operations (keep group onenote)
    'teams',          # Exclude Teams operations (keep group onenote)
    'channel',        # Exclude Teams channel operations
    'channels',       # Exclude Teams channel operations
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
    'site',           # Exclude SharePoint site operations (keep site onenote)
    'sites',          # Exclude SharePoint site operations (keep site onenote)
    'list',           # Exclude SharePoint list operations
    'lists',          # Exclude SharePoint list operations
    'planner',        # Exclude Planner operations
    'deviceAppManagement',
    'deviceManagement',
    'devices',
    'directoryObjects',
    'applications',
    'servicePrincipals',
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
    'todo',           # Exclude To-Do operations
    'communications',
    'education',
    'identity',
    'inferenceClassification',
    'extensions',
]

# Path patterns to exclude (ADD MORE PATTERNS HERE TO EXCLUDE)
EXCLUDED_PATHS = [
    '/drives',
    '/drive',
    '/chats',
    '/teams/{team-id}',  # Exclude team management (keep site onenote)
    '/channels',
    '/messages',
    '/events',
    '/calendar',
    '/calendars',
    '/contacts',
    '/contactFolders',
    '/mailFolders',
    '/sites/{site-id}/lists',  # Exclude SharePoint lists (keep site onenote)
    '/sites/{site-id}/drives', # Exclude SharePoint drives (keep site onenote)
    '/planner',
    '/deviceAppManagement',
    '/deviceManagement',
    '/devices',
    '/directoryObjects',
    '/applications',
    '/servicePrincipals',
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
    '/communications',
    '/education',
    '/identity',
    '/inferenceClassification',
    '/extensions'
]

# =============================================================================

# Set up logger
logger = logging.getLogger(__name__)

# Microsoft Graph OpenAPI specification URLs
DEFAULT_SPEC_URL = "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/master/openapi/v1.0/openapi.yaml"
FALLBACK_SPEC_URLS = [
    "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/main/openapi/v1.0/openapi.yaml",
]

# Comprehensive OneNote endpoint patterns (MODIFY THIS LIST TO ADD/REMOVE ENDPOINTS)
ONENOTE_PATH_PATTERNS = [
    # Personal OneNote - Complete Operations
    "/me/onenote",
    "/me/onenote/notebooks",
    "/me/onenote/notebooks/{notebook-id}",
    "/me/onenote/notebooks/{notebook-id}/sections",
    "/me/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}",
    "/me/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}/pages",
    "/me/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}/pages/{onenote-page-id}",
    "/me/onenote/notebooks/{notebook-id}/sectionGroups",
    "/me/onenote/notebooks/{notebook-id}/sectionGroups/{sectiongroup-id}",
    "/me/onenote/notebooks/{notebook-id}/sectionGroups/{sectiongroup-id}/sections",
    "/me/onenote/notebooks/{notebook-id}/sectionGroups/{sectiongroup-id}/sectionGroups",
    "/me/onenote/notebooks/{notebook-id}/copyNotebook",
    "/me/onenote/sections",
    "/me/onenote/sections/{onenote-section-id}",
    "/me/onenote/sections/{onenote-section-id}/pages",
    "/me/onenote/sections/{onenote-section-id}/pages/{onenote-page-id}",
    "/me/onenote/sections/{onenote-section-id}/copyToNotebook",
    "/me/onenote/sections/{onenote-section-id}/copyToSectionGroup",
    "/me/onenote/sectionGroups",
    "/me/onenote/sectionGroups/{sectiongroup-id}",
    "/me/onenote/sectionGroups/{sectiongroup-id}/sections",
    "/me/onenote/sectionGroups/{sectiongroup-id}/sectionGroups",
    "/me/onenote/pages",
    "/me/onenote/pages/{onenote-page-id}",
    "/me/onenote/pages/{onenote-page-id}/content",
    "/me/onenote/pages/{onenote-page-id}/preview",
    "/me/onenote/pages/{onenote-page-id}/copyToSection",
    "/me/onenote/pages/{onenote-page-id}/onenotePatchContent",
    "/me/onenote/resources",
    "/me/onenote/resources/{resource-id}",
    "/me/onenote/resources/{resource-id}/content",
    "/me/onenote/operations",
    "/me/onenote/operations/{onenote-operation-id}",
    
    # User OneNote - Complete Operations
    "/users/{user-id}/onenote",
    "/users/{user-id}/onenote/notebooks",
    "/users/{user-id}/onenote/notebooks/{notebook-id}",
    "/users/{user-id}/onenote/notebooks/{notebook-id}/sections",
    "/users/{user-id}/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}",
    "/users/{user-id}/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}/pages",
    "/users/{user-id}/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}/pages/{onenote-page-id}",
    "/users/{user-id}/onenote/notebooks/{notebook-id}/sectionGroups",
    "/users/{user-id}/onenote/notebooks/{notebook-id}/sectionGroups/{sectiongroup-id}",
    "/users/{user-id}/onenote/notebooks/{notebook-id}/sectionGroups/{sectiongroup-id}/sections",
    "/users/{user-id}/onenote/notebooks/{notebook-id}/sectionGroups/{sectiongroup-id}/sectionGroups",
    "/users/{user-id}/onenote/notebooks/{notebook-id}/copyNotebook",
    "/users/{user-id}/onenote/sections",
    "/users/{user-id}/onenote/sections/{onenote-section-id}",
    "/users/{user-id}/onenote/sections/{onenote-section-id}/pages",
    "/users/{user-id}/onenote/sections/{onenote-section-id}/pages/{onenote-page-id}",
    "/users/{user-id}/onenote/sections/{onenote-section-id}/copyToNotebook",
    "/users/{user-id}/onenote/sections/{onenote-section-id}/copyToSectionGroup",
    "/users/{user-id}/onenote/sectionGroups",
    "/users/{user-id}/onenote/sectionGroups/{sectiongroup-id}",
    "/users/{user-id}/onenote/sectionGroups/{sectiongroup-id}/sections",
    "/users/{user-id}/onenote/sectionGroups/{sectiongroup-id}/sectionGroups",
    "/users/{user-id}/onenote/pages",
    "/users/{user-id}/onenote/pages/{onenote-page-id}",
    "/users/{user-id}/onenote/pages/{onenote-page-id}/content",
    "/users/{user-id}/onenote/pages/{onenote-page-id}/preview",
    "/users/{user-id}/onenote/pages/{onenote-page-id}/copyToSection",
    "/users/{user-id}/onenote/pages/{onenote-page-id}/onenotePatchContent",
    "/users/{user-id}/onenote/resources",
    "/users/{user-id}/onenote/resources/{resource-id}",
    "/users/{user-id}/onenote/resources/{resource-id}/content",
    "/users/{user-id}/onenote/operations",
    "/users/{user-id}/onenote/operations/{onenote-operation-id}",
    
    # Group OneNote - Complete Operations
    "/groups/{group-id}/onenote",
    "/groups/{group-id}/onenote/notebooks",
    "/groups/{group-id}/onenote/notebooks/{notebook-id}",
    "/groups/{group-id}/onenote/notebooks/{notebook-id}/sections",
    "/groups/{group-id}/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}",
    "/groups/{group-id}/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}/pages",
    "/groups/{group-id}/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}/pages/{onenote-page-id}",
    "/groups/{group-id}/onenote/notebooks/{notebook-id}/sectionGroups",
    "/groups/{group-id}/onenote/notebooks/{notebook-id}/sectionGroups/{sectiongroup-id}",
    "/groups/{group-id}/onenote/notebooks/{notebook-id}/sectionGroups/{sectiongroup-id}/sections",
    "/groups/{group-id}/onenote/notebooks/{notebook-id}/sectionGroups/{sectiongroup-id}/sectionGroups",
    "/groups/{group-id}/onenote/notebooks/{notebook-id}/copyNotebook",
    "/groups/{group-id}/onenote/sections",
    "/groups/{group-id}/onenote/sections/{onenote-section-id}",
    "/groups/{group-id}/onenote/sections/{onenote-section-id}/pages",
    "/groups/{group-id}/onenote/sections/{onenote-section-id}/pages/{onenote-page-id}",
    "/groups/{group-id}/onenote/sections/{onenote-section-id}/copyToNotebook",
    "/groups/{group-id}/onenote/sections/{onenote-section-id}/copyToSectionGroup",
    "/groups/{group-id}/onenote/sectionGroups",
    "/groups/{group-id}/onenote/sectionGroups/{sectiongroup-id}",
    "/groups/{group-id}/onenote/sectionGroups/{sectiongroup-id}/sections",
    "/groups/{group-id}/onenote/sectionGroups/{sectiongroup-id}/sectionGroups",
    "/groups/{group-id}/onenote/pages",
    "/groups/{group-id}/onenote/pages/{onenote-page-id}",
    "/groups/{group-id}/onenote/pages/{onenote-page-id}/content",
    "/groups/{group-id}/onenote/pages/{onenote-page-id}/preview",
    "/groups/{group-id}/onenote/pages/{onenote-page-id}/copyToSection",
    "/groups/{group-id}/onenote/pages/{onenote-page-id}/onenotePatchContent",
    "/groups/{group-id}/onenote/resources",
    "/groups/{group-id}/onenote/resources/{resource-id}",
    "/groups/{group-id}/onenote/resources/{resource-id}/content",
    "/groups/{group-id}/onenote/operations",
    "/groups/{group-id}/onenote/operations/{onenote-operation-id}",
    
    # Site OneNote - Complete Operations
    "/sites/{site-id}/onenote",
    "/sites/{site-id}/onenote/notebooks",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}/sections",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}/pages",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}/sections/{onenote-section-id}/pages/{onenote-page-id}",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}/sectionGroups",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}/sectionGroups/{sectiongroup-id}",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}/sectionGroups/{sectiongroup-id}/sections",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}/sectionGroups/{sectiongroup-id}/sectionGroups",
    "/sites/{site-id}/onenote/notebooks/{notebook-id}/copyNotebook",
    "/sites/{site-id}/onenote/sections",
    "/sites/{site-id}/onenote/sections/{onenote-section-id}",
    "/sites/{site-id}/onenote/sections/{onenote-section-id}/pages",
    "/sites/{site-id}/onenote/sections/{onenote-section-id}/pages/{onenote-page-id}",
    "/sites/{site-id}/onenote/sections/{onenote-section-id}/copyToNotebook",
    "/sites/{site-id}/onenote/sections/{onenote-section-id}/copyToSectionGroup",
    "/sites/{site-id}/onenote/sectionGroups",
    "/sites/{site-id}/onenote/sectionGroups/{sectiongroup-id}",
    "/sites/{site-id}/onenote/sectionGroups/{sectiongroup-id}/sections",
    "/sites/{site-id}/onenote/sectionGroups/{sectiongroup-id}/sectionGroups",
    "/sites/{site-id}/onenote/pages",
    "/sites/{site-id}/onenote/pages/{onenote-page-id}",
    "/sites/{site-id}/onenote/pages/{onenote-page-id}/content",
    "/sites/{site-id}/onenote/pages/{onenote-page-id}/preview",
    "/sites/{site-id}/onenote/pages/{onenote-page-id}/copyToSection",
    "/sites/{site-id}/onenote/pages/{onenote-page-id}/onenotePatchContent",
    "/sites/{site-id}/onenote/resources",
    "/sites/{site-id}/onenote/resources/{resource-id}",
    "/sites/{site-id}/onenote/resources/{resource-id}/content",
    "/sites/{site-id}/onenote/operations",
    "/sites/{site-id}/onenote/operations/{onenote-operation-id}",
    
    # NOTE: Excluded patterns (OneDrive, Teams, Outlook, SharePoint non-onenote) are filtered out by EXCLUDED_KEYWORDS and EXCLUDED_PATHS
    # To re-enable them, remove the keywords from the exclusion lists above
]

# ---- Operation Model -------------------------------------------------------
@dataclass
class OneNoteOperation:
    op_id: str
    http_method: str
    path: str
    summary: str
    description: str
    params: List[Mapping[str, Any]]
    request_body: Optional[Mapping[str, Any]] = None
    path_params: List[Dict[str, str]] = None
    operation_type: str = "general"  # notebooks, sections, sectionGroups, pages, content, resources, operations

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
        """Determine the type of OneNote operation for better organization."""
        path_lower = self.path.lower()
        op_lower = self.op_id.lower()
        
        if any(keyword in path_lower for keyword in ["notebook", "notebooks", "copynotebook"]):
            return "notebooks"
        elif any(keyword in path_lower for keyword in ["sectiongroup", "sectiongroups"]):
            return "sectionGroups"
        elif any(keyword in path_lower for keyword in ["section", "sections", "copytosection", "copytonotebook", "copytosectiongroup"]):
            return "sections"
        elif any(keyword in path_lower for keyword in ["page", "pages", "content", "preview", "onenotepatchcontent"]):
            return "pages"
        elif any(keyword in path_lower for keyword in ["resource", "resources"]):
            return "resources"
        elif any(keyword in path_lower for keyword in ["operation", "operations"]):
            return "operations"
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
    """Extract path parameters from OneNote path pattern."""
    import re
    
    # Find all {parameter} patterns
    pattern = r'\{([^}]+)\}'
    matches = re.findall(pattern, path)
    
    path_params = []
    for match in matches:
        # Clean up parameter name and convert to Python-friendly format
        param_name = match.replace('-', '_').replace('.', '_')
        
        # Determine parameter type based on common OneNote patterns
        param_type = 'str'
        description = f'OneNote path parameter: {match}'
        
        if 'id' in match.lower():
            description = f'OneNote {match.replace("-", " ").replace("_", " ")} identifier'
        elif 'user' in match.lower():
            description = f'User identifier: {match}'
        elif 'group' in match.lower():
            description = f'Group identifier: {match}'
        elif 'site' in match.lower():
            description = f'Site identifier: {match}'
        elif 'notebook' in match.lower():
            description = f'Notebook identifier: {match}'
        elif 'section' in match.lower():
            description = f'Section identifier: {match}'
        elif 'page' in match.lower():
            description = f'Page identifier: {match}'
        elif 'resource' in match.lower():
            description = f'Resource identifier: {match}'
        elif 'operation' in match.lower():
            description = f'Operation identifier: {match}'
        
        path_params.append({
            'name': param_name,
            'original': match,
            'type': param_type,
            'required': True,
            'description': description
        })
    
    return path_params

def _is_onenote_operation(path: str, op_id: str) -> bool:
    """Check if this operation is related to OneNote.
    
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
    
    # Include operations that are clearly OneNote related (CORE ONENOTE KEYWORDS)
    onenote_core_keywords = [
        'onenote', 'notebook', 'notebooks', 'section', 'sections',
        'sectiongroup', 'sectiongroups', 'page', 'pages', 'content',
        'preview', 'resource', 'resources', 'operation', 'operations',
        'copynotebook', 'copytosection', 'copytonotebook', 'copytosectiongroup',
        'onenotepatchcontent'
    ]
    
    return any(keyword in path_lower or keyword in op_lower for keyword in onenote_core_keywords)

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

def extract_onenote_operations(spec: Mapping[str, Any]) -> List[OneNoteOperation]:
    """Extract OneNote-specific operations from OpenAPI spec."""
    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("Invalid OpenAPI spec: missing 'paths'")

    ops: List[OneNoteOperation] = []
    skipped_count_ops = 0
    skipped_non_onenote = 0
    
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
            
        # Check if path matches any OneNote patterns
        if not any(_path_matches_pattern(path, pattern) for pattern in ONENOTE_PATH_PATTERNS):
            # Additional check for OneNote-related operations
            if not _is_onenote_operation(path, ""):
                skipped_non_onenote += 1
                continue
        
        # ============ EXCLUSION CHECK (MODIFY EXCLUDED_PATHS LIST ABOVE) ============
        # Explicitly exclude paths based on exclusion list
        should_exclude = False
        for excluded_path in EXCLUDED_PATHS:
            if excluded_path.lower() in path.lower():
                should_exclude = True
                break
        
        if should_exclude:
            skipped_non_onenote += 1
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
            
            # Skip non-OneNote operations
            if not _is_onenote_operation(path, str(op_id)):
                skipped_non_onenote += 1
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
                skipped_non_onenote += 1
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
                OneNoteOperation(
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
    uniq: Dict[str, OneNoteOperation] = {}
    for op in ops:
        key = f"{op.op_id}_{op.http_method}_{op.path}"
        if key not in uniq:
            uniq[key] = op
    
    print(f"OneNote filtering summary (MODIFY EXCLUDED_KEYWORDS/EXCLUDED_PATHS to change):")
    print(f"  - Skipped {skipped_count_ops} count operations")
    print(f"  - Skipped {skipped_non_onenote} excluded operations (OneDrive, Teams, Outlook, SharePoint non-onenote)")
    print(f"  - Found {len(uniq)} OneNote operations (Notebooks, Sections, Pages)")
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

def extract_operation_parameters(op: OneNoteOperation) -> Tuple[List[str], List[str], List[str]]:
    """Extract and categorize parameters from OneNote operation."""
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
    
    # Add OneNote-specific OData parameters
    onenote_odata_params = [
        "select: Optional[List[str]] = None",
        "expand: Optional[List[str]] = None", 
        "filter: Optional[str] = None",
        "orderby: Optional[str] = None",
        "search: Optional[str] = None",
        "top: Optional[int] = None",
        "skip: Optional[int] = None"
    ]
    optional_params.extend(onenote_odata_params)
    
    # Add OneNote OData parameter docs
    onenote_odata_docs = [
        "            select (optional): Select specific properties to return",
        "            expand (optional): Expand related entities (e.g., sections, pages, parentNotebook)",
        "            filter (optional): Filter the results using OData syntax", 
        "            orderby (optional): Order the results by specified properties",
        "            search (optional): Search for notebooks, sections, or pages by content",
        "            top (optional): Limit number of results returned",
        "            skip (optional): Skip number of results for pagination"
    ]
    param_docs.extend(onenote_odata_docs)
    
    # Add request body if needed (for OneNote operations)
    has_body = op.request_body is not None
    if has_body:
        optional_params.append("request_body: Optional[Mapping[str, Any]] = None")
        param_docs.append("            request_body (optional): Request body data for OneNote operations")
    
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
    """Get the appropriate query parameter classes for the OneNote endpoint."""
    path_lower = path.lower()
    method_lower = method.lower()
    
    # Map paths to their corresponding query parameter classes
    if '/onenote/notebooks' in path_lower and method_lower == 'get':
        return "NotebooksRequestBuilder.NotebooksRequestBuilderGetQueryParameters", "NotebooksRequestBuilder.NotebooksRequestBuilderGetRequestConfiguration"
    elif '/onenote/sections' in path_lower and method_lower == 'get':
        return "SectionsRequestBuilder.SectionsRequestBuilderGetQueryParameters", "SectionsRequestBuilder.SectionsRequestBuilderGetRequestConfiguration"
    elif '/onenote/sectionGroups' in path_lower and method_lower == 'get':
        return "SectionGroupsRequestBuilder.SectionGroupsRequestBuilderGetQueryParameters", "SectionGroupsRequestBuilder.SectionGroupsRequestBuilderGetRequestConfiguration"
    elif '/onenote/pages' in path_lower and method_lower == 'get':
        return "PagesRequestBuilder.PagesRequestBuilderGetQueryParameters", "PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration"
    elif '/onenote/resources' in path_lower and method_lower == 'get':
        return "ResourcesRequestBuilder.ResourcesRequestBuilderGetQueryParameters", "ResourcesRequestBuilder.ResourcesRequestBuilderGetRequestConfiguration"
    elif '/onenote/operations' in path_lower and method_lower == 'get':
        return "OperationsRequestBuilder.OperationsRequestBuilderGetQueryParameters", "OperationsRequestBuilder.OperationsRequestBuilderGetRequestConfiguration"
    elif '/onenote' in path_lower and method_lower == 'get':
        return "OnenoteRequestBuilder.OnenoteRequestBuilderGetQueryParameters", "OnenoteRequestBuilder.OnenoteRequestBuilderGetRequestConfiguration"
    else:
        # Fallback to generic RequestConfiguration for unmatched endpoints
        return "RequestConfiguration", "RequestConfiguration"

def build_onenote_method_code(op: OneNoteOperation) -> str:
    """Generate comprehensive async method code for OneNote operation."""
    required_params, optional_params, param_docs = extract_operation_parameters(op)

    # Build method signature
    all_params = required_params + optional_params
    
    if len(all_params) <= 5:
        sig = ", ".join(all_params)
    else:
        params_formatted = ",\n        ".join(all_params)
        sig = f"\n        {params_formatted}\n    "

    summary = op.summary or op.op_id
    params_doc = "\n".join(param_docs) if param_docs else "            (standard OneNote parameters)"

    # Generate OneNote-specific Graph SDK method call
    graph_method_call = _generate_onenote_graph_call(op)
    # Determine query parameter classes during generation
    query_param_class, config_class = _get_query_param_classes(op.path, op.http_method)
    response_type = "OneNoteResponse"

    method_code = f"""    async def {op.wrapper_name}({sig}) -> {response_type}:
        \"\"\"{summary}.
        OneNote operation: {op.http_method} {op.path}
        Operation type: {op.operation_type}
        Args:
{params_doc}
        Returns:
            {response_type}: OneNote response wrapper with success/data/error
        \"\"\"
        # Build query parameters including OData for OneNote
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

            # Add consistency level for search operations in OneNote
            if search:
                if not config.headers:
                    config.headers = {{}}
                config.headers['ConsistencyLevel'] = 'eventual'

{graph_method_call}
            return self._handle_onenote_response(response)
        except Exception as e:
            return {response_type}(
                success=False,
                error=f"OneNote API call failed: {{str(e)}}",
            )
"""

    return method_code

def _generate_onenote_graph_call(op: OneNoteOperation) -> str:
    """Generate OneNote-specific Microsoft Graph SDK method call."""
    path = op.path
    method = op.http_method.lower()
    has_body = op.request_body is not None
    
    # Build request builder chain optimized for OneNote
    request_builder = _build_onenote_request_builder_chain(path, op.path_params)
    
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

def _build_onenote_request_builder_chain(path: str, path_params: List[Dict[str, str]]) -> str:
    """Build OneNote-optimized Microsoft Graph SDK request builder chain."""
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
            elif prev_segment == "sites":
                builder += f".by_site_id({python_param})"
            elif prev_segment == "notebooks":
                builder += f".by_notebook_id({python_param})"
            elif prev_segment == "sections":
                builder += f".by_onenote_section_id({python_param})"
            elif prev_segment == "sectionGroups":
                builder += f".by_section_group_id({python_param})"
            elif prev_segment == "pages":
                builder += f".by_onenote_page_id({python_param})"
            elif prev_segment == "resources":
                builder += f".by_resource_id({python_param})"
            elif prev_segment == "operations":
                builder += f".by_onenote_operation_id({python_param})"
            else:
                # Generic fallback for other OneNote parameters
                builder += f".by_{prev_segment[:-1] if prev_segment.endswith('s') else prev_segment}_id({python_param})"
                
        else:
            # Regular path segment - convert to appropriate OneNote SDK method
            if segment == "me":
                builder += ".me"
            elif segment == "users":
                # Handle /users/{user-id} pattern
                if i + 1 < len(segments) and segments[i + 1].startswith('{'):
                    i += 1  # Skip the parameter segment, it will be handled in next iteration
                    param_segment = segments[i]
                    param_name = param_segment[1:-1].replace('-', '_')
                    python_param = sanitize_py_name(param_name)
                    builder += f".users.by_user_id({python_param})"
                else:
                    builder += ".users"
            elif segment == "groups":
                # Handle /groups/{group-id} pattern
                if i + 1 < len(segments) and segments[i + 1].startswith('{'):
                    i += 1  # Skip the parameter segment
                    param_segment = segments[i]
                    param_name = param_segment[1:-1].replace('-', '_')
                    python_param = sanitize_py_name(param_name)
                    builder += f".groups.by_group_id({python_param})"
                else:
                    builder += ".groups"
            elif segment == "sites":
                # Handle /sites/{site-id} pattern
                if i + 1 < len(segments) and segments[i + 1].startswith('{'):
                    i += 1  # Skip the parameter segment
                    param_segment = segments[i]
                    param_name = param_segment[1:-1].replace('-', '_')
                    python_param = sanitize_py_name(param_name)
                    builder += f".sites.by_site_id({python_param})"
                else:
                    builder += ".sites"
            elif segment == "onenote":
                builder += ".onenote"
            elif segment == "notebooks":
                builder += ".notebooks"
            elif segment == "sections":
                builder += ".sections"
            elif segment == "sectionGroups":
                builder += ".section_groups"
            elif segment == "pages":
                builder += ".pages"
            elif segment == "content":
                builder += ".content"
            elif segment == "preview":
                builder += ".preview"
            elif segment == "resources":
                builder += ".resources"
            elif segment == "operations":
                builder += ".operations"
            elif segment == "copyNotebook":
                builder += ".copy_notebook"
            elif segment == "copyToNotebook":
                builder += ".copy_to_notebook"
            elif segment == "copyToSection":
                builder += ".copy_to_section"
            elif segment == "copyToSectionGroup":
                builder += ".copy_to_section_group"
            elif segment == "onenotePatchContent":
                builder += ".onenote_patch_content"
            else:
                # Convert to snake_case for other segments
                snake_segment = to_snake(segment)
                builder += f".{snake_segment}"
        
        i += 1
    
    return builder

def build_onenote_class_code(ops: Sequence[OneNoteOperation]) -> str:
    """Generate the complete OneNote client class."""
    # Group methods by operation type for better organization
    methods_by_type = {}
    for op in ops:
        if op.operation_type not in methods_by_type:
            methods_by_type[op.operation_type] = []
        methods_by_type[op.operation_type].append(op)
    
    class_name = "OneNoteDataSource"
    response_class = "OneNoteResponse"

    header = f"""

import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Mapping, Optional

from kiota_abstractions.base_request_configuration import (  # type: ignore
    RequestConfiguration,
)

# Import MS Graph specific query parameter classes for OneNote
from msgraph.generated.users.item.onenote.onenote_request_builder import (  # type: ignore
    OnenoteRequestBuilder,
)
from msgraph.generated.users.item.onenote.notebooks.notebooks_request_builder import (  # type: ignore
    NotebooksRequestBuilder,
)
from msgraph.generated.users.item.onenote.sections.sections_request_builder import (  # type: ignore
    SectionsRequestBuilder,
)
from msgraph.generated.users.item.onenote.section_groups.section_groups_request_builder import (  # type: ignore
    SectionGroupsRequestBuilder,
)
from msgraph.generated.users.item.onenote.pages.pages_request_builder import (  # type: ignore
    PagesRequestBuilder,
)
from msgraph.generated.users.item.onenote.resources.resources_request_builder import (  # type: ignore
    ResourcesRequestBuilder,
)
from msgraph.generated.users.item.onenote.operations.operations_request_builder import (  # type: ignore
    OperationsRequestBuilder,
)

from app.sources.client.microsoft.microsoft import MSGraphClient

# OneNote-specific response wrapper
class OneNoteResponse:
    \"\"\"Standardized OneNote API response wrapper.\"\"\"
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
    Comprehensive Microsoft OneNote API client with complete Notebooks, Sections, and Pages coverage.

    Features:
    - Complete OneNote API coverage with {len(ops)} methods organized by operation type
    - Support for Personal OneNote, User OneNote, Group OneNote, and Site OneNote
    - Complete Notebook operations: create, read, update, delete, list, copy
    - Complete Section operations: create, read, update, delete, list, copy
    - Complete Section Group operations: create, read, update, delete, list
    - Complete Page operations: create, read, update, delete, list, copy
    - Content operations: HTML content retrieval and manipulation
    - Preview operations: Page previews and thumbnails
    - Resource operations: File attachments and embedded resources
    - Search capabilities across notebooks, sections, and pages
    - Microsoft Graph SDK integration with OneNote-specific optimizations
    - Async snake_case method names for all operations
    - Standardized {response_class} format for all responses
    - Comprehensive error handling and OneNote-specific response processing

    EXCLUDED OPERATIONS (modify EXCLUDED_KEYWORDS list to change):
    - OneDrive operations (drive, drives, items)
    - Teams operations (chats, teams, channels)
    - SharePoint operations (sites, lists, document libraries)
    - Outlook operations (messages, mailFolders)
    - Calendar operations (events, calendars)
    - Planner operations (plans, tasks, buckets)
    - Directory operations (directoryObjects, devices)
    - Admin operations (admin, compliance, security)
    - Device management operations
    - Communications operations

    Operation Types:
    - Notebook operations: Complete CRUD for notebooks
    - Section operations: Section management and organization
    - Section Group operations: Section group hierarchy management
    - Page operations: Page content and metadata operations
    - Content operations: HTML content and formatting
    - Resource operations: File attachments and media
    - Operations operations: Long-running operation tracking
    - General operations: Base OneNote functionality
    \"\"\"

    def __init__(self, client: MSGraphClient) -> None:
        \"\"\"Initialize with Microsoft Graph SDK client optimized for OneNote.\"\"\"
        self.client = client.get_client().get_ms_graph_service_client()
        if not hasattr(self.client, "me"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("OneNote client initialized with comprehensive method coverage")

    def _handle_onenote_response(self, response: object) -> OneNoteResponse:
        \"\"\"Handle OneNote API response with comprehensive error handling.\"\"\"
        try:
            if response is None:
                return OneNoteResponse(success=False, error="Empty response from OneNote API")

            success = True
            error_msg = None

            # Enhanced error response handling for OneNote
            if hasattr(response, 'error'):
                success = False
                error_msg = str(response.error)
            elif isinstance(response, dict) and 'error' in response:
                success = False
                error_info = response['error']
                if isinstance(error_info, dict):
                    error_msg = f"{{error_info.get('code', 'Unknown')}}: {{error_info.get('message', 'No message')}}"
                else:
                    error_msg = str(error_info)
            elif hasattr(response, 'code') and hasattr(response, 'message'):
                success = False  
                error_msg = f"{{response.code}}: {{response.message}}"
            
            return OneNoteResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling OneNote response: {{e}}")
            return OneNoteResponse(success=False, error=str(e))

    def get_data_source(self) -> '{class_name}':
        \"\"\"Get the underlying OneNote client.\"\"\"
        return self

"""

    # Add methods organized by operation type
    methods_section = ""
    
    # Define the order of operation types for better organization
    operation_order = ['general', 'notebooks', 'sections', 'sectionGroups', 'pages', 'resources', 'operations']
    
    for op_type in operation_order:
        if op_type in methods_by_type:
            methods = methods_by_type[op_type]
            methods_section += f"    # ========== {op_type.upper()} OPERATIONS ({len(methods)} methods) ==========\n\n"
            
            for op in methods:
                method_code = build_onenote_method_code(op)
                methods_section += method_code + "\n"
    
    return header + methods_section + "\n\n"

# ---- Public entrypoints ----------------------------------------------------
def generate_onenote_client(
    *,
    out_path: Optional[str] = None,
    spec_url: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> str:
    """Generate comprehensive OneNote client. Returns file path."""
    out_filename = out_path or "onenote_client.py"
    
    print(f"Loading Microsoft Graph OpenAPI specification for OneNote...")
    spec = load_spec(spec_url=spec_url, spec_path=spec_path)
    
    print(f"Extracting OneNote operations with comprehensive coverage...")
    ops = extract_onenote_operations(spec)
    
    print(f"Found {len(ops)} OneNote operations with full parameter support")
    
    # Show breakdown by operation type
    ops_by_type = {}
    for op in ops:
        if op.operation_type not in ops_by_type:
            ops_by_type[op.operation_type] = 0
        ops_by_type[op.operation_type] += 1
    
    print("Operation breakdown:")
    for op_type, count in sorted(ops_by_type.items()):
        print(f"  - {op_type}: {count} methods")
    
    print("Generating comprehensive OneNote async client code...")
    code = build_onenote_class_code(ops)
    
    # Create microsoft directory (reuse the existing structure)
    script_dir = Path(__file__).parent if __file__ else Path('.')
    microsoft_dir = script_dir / 'microsoft'
    microsoft_dir.mkdir(exist_ok=True)
    
    # Write file
    full_path = microsoft_dir / out_filename
    full_path.write_text(code, encoding="utf-8")
    print(f"Saved comprehensive OneNote client to: {full_path}")
    
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
        description="Generate comprehensive OneNote API client"
    )
    ap.add_argument("--out", help="Output .py file path (default: onenote_client.py)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--spec-url", default=None, help="Microsoft Graph OpenAPI spec URL")
    g.add_argument("--spec-path", help="Local spec file path (JSON/YAML)")
    ap.add_argument("--show-patterns", action="store_true", help="Show all OneNote path patterns")
    
    return ap.parse_args(argv)

def _show_patterns() -> None:
    """Show all OneNote path patterns that will be matched."""
    print("OneNote API Path Patterns (Comprehensive Coverage):")
    print(f"Total patterns: {len(ONENOTE_PATH_PATTERNS)}")
    print()
    
    # Group patterns by category
    pattern_groups = {
        "Personal OneNote": [p for p in ONENOTE_PATH_PATTERNS if p.startswith("/me/onenote")],
        "User OneNote": [p for p in ONENOTE_PATH_PATTERNS if p.startswith("/users/") and "/onenote" in p],
        "Group OneNote": [p for p in ONENOTE_PATH_PATTERNS if p.startswith("/groups/") and "/onenote" in p],
        "Site OneNote": [p for p in ONENOTE_PATH_PATTERNS if p.startswith("/sites/") and "/onenote" in p],
        "Notebooks": [p for p in ONENOTE_PATH_PATTERNS if "/notebooks" in p],
        "Sections": [p for p in ONENOTE_PATH_PATTERNS if "/sections" in p and "/sectionGroups" not in p],
        "Section Groups": [p for p in ONENOTE_PATH_PATTERNS if "/sectionGroups" in p],
        "Pages": [p for p in ONENOTE_PATH_PATTERNS if "/pages" in p],
        "Resources": [p for p in ONENOTE_PATH_PATTERNS if "/resources" in p],
        "Operations": [p for p in ONENOTE_PATH_PATTERNS if "/operations" in p],
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
    
    print(f"Starting comprehensive OneNote API client generation...")
    
    try:
        out_path = generate_onenote_client(
            out_path=ns.out,
            spec_url=ns.spec_url,
            spec_path=ns.spec_path,
        )
        
        print(f"\nOneNote comprehensive client generation completed!")
        print(f"Generated class: OneNoteDataSource")
        print(f"Output file: {out_path}")
        print(f"Features:")
        print(f"  - Complete OneNote API endpoint coverage (Notebooks, Sections, Pages)")
        print(f"  - Support for Personal, User, Group, and Site OneNote")
        print(f"  - Notebook operations: create, read, update, delete, list, copy")
        print(f"  - Section operations: create, read, update, delete, list, copy")
        print(f"  - Page operations: create, read, update, delete, list, copy, content")
        print(f"  - Advanced features: section groups, resources, operations tracking")
        print(f"  - Async methods with comprehensive error handling")
        print(f"")
        print(f"🔧 TO MODIFY EXCLUSIONS:")
        print(f"  - Edit EXCLUDED_KEYWORDS list to add/remove excluded API keywords")
        print(f"  - Edit EXCLUDED_PATHS list to add/remove excluded path patterns")
        print(f"  - Current exclusions: OneDrive, Teams, Outlook, SharePoint non-onenote")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()