# ruff: noqa
"""
=============================================================================
🔧 EASY MODIFICATION GUIDE:
To exclude more APIs, add keywords to EXCLUDED_KEYWORDS or paths to EXCLUDED_PATHS 
Search for "EXCLUSION CONFIGURATION" section below to find the lists
=============================================================================
"""
"""
Microsoft Outlook API Client Generator (Mail, Calendar, Contacts Focus)

Generates a focused Outlook API client with comprehensive endpoint coverage for:
- Personal Mail (/me/messages, /me/mailFolders)
- User Mail (/users/{user-id}/messages, /users/{user-id}/mailFolders)
- Personal Calendar (/me/calendar, /me/calendars, /me/events)
- User Calendar (/users/{user-id}/calendar, /users/{user-id}/calendars, /users/{user-id}/events)
- Personal Contacts (/me/contacts, /me/contactFolders)
- User Contacts (/users/{user-id}/contacts, /users/{user-id}/contactFolders)
- Group Calendar (/groups/{group-id}/calendar, /groups/{group-id}/events)
- Mail attachments, rules, categories, and extended properties
- Calendar invitations, reminders, and recurring events
- Contact photos and extended properties

Explicitly excludes:
- OneDrive operations (/drive, /drives endpoints)
- Teams operations (/chats, /teams endpoints)
- SharePoint operations (/sites endpoints)
- OneNote operations (/onenote endpoints)
- Planner operations (/planner endpoints)
- Directory operations (/directoryObjects endpoints)
- Device operations (/devices endpoints)
- Security operations (/security endpoints)
- Compliance operations (/compliance endpoints)
- Admin operations (/admin endpoints)
- Non-mail/calendar/contacts Microsoft Graph endpoints

EASY MODIFICATION:
To add/remove excluded APIs, modify the EXCLUDED_KEYWORDS and EXCLUDED_PATHS lists at the top of this file.

Enhanced Features:
- Complete Outlook endpoint coverage with all path parameters
- Proper parameter extraction for user-id, group-id, message-id, event-id, contact-id, folder-id
- Comprehensive method signatures with required and optional parameters
- Microsoft Graph SDK integration optimized for Outlook operations
- Specialized Outlook response handling and error management
- Support for mail search, calendar scheduling, and contact management
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

# Keywords to exclude from Outlook operations (ADD MORE HERE TO EXCLUDE)
EXCLUDED_KEYWORDS = [
    'drive',          # Exclude OneDrive operations
    'drives',         # Exclude OneDrive operations
    'chat',           # Exclude Teams chat operations
    'teams',          # Exclude Teams operations
    'site',           # Exclude SharePoint site operations
    'sites',          # Exclude SharePoint site operations
    'onenote',        # Exclude OneNote operations
    'planner',        # Exclude Planner operations
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
    'todo',           # Exclude To-Do operations (focus on calendar/mail)
    'notebook',       # Exclude notebook operations
    'section',        # Exclude OneNote section operations
    'page',           # Exclude OneNote page operations (conflict with mail pages)
    'communications',
    'education',
    'identity',
    'photo',
    'inferenceClassification',
    'extensions',
]

# Path patterns to exclude (ADD MORE PATTERNS HERE TO EXCLUDE)
EXCLUDED_PATHS = [
    '/drives',
    '/drive',
    '/chats',
    '/teams',
    '/sites',
    '/onenote',
    '/planner',
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
    '/extensions'
]

# =============================================================================

# Microsoft Graph OpenAPI specification URLs
DEFAULT_SPEC_URL = "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/master/openapi/v1.0/openapi.yaml"
FALLBACK_SPEC_URLS = [
    "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/main/openapi/v1.0/openapi.yaml",
]

# Comprehensive Outlook endpoint patterns (MODIFY THIS LIST TO ADD/REMOVE ENDPOINTS)
OUTLOOK_PATH_PATTERNS = [
    # Personal Mail - Complete Operations
    "/me/messages",
    "/me/messages/{message-id}",
    "/me/messages/{message-id}/attachments",
    "/me/messages/{message-id}/attachments/{attachment-id}",
    "/me/messages/{message-id}/extensions",
    "/me/messages/{message-id}/extensions/{extension-id}",
    "/me/messages/{message-id}/multiValueExtendedProperties",
    "/me/messages/{message-id}/singleValueExtendedProperties",
    "/me/mailFolders",
    "/me/mailFolders/{mailfolder-id}",
    "/me/mailFolders/{mailfolder-id}/messages",
    "/me/mailFolders/{mailfolder-id}/childFolders",
    "/me/mailFolders/{mailfolder-id}/messageRules",
    "/me/mailFolders/{mailfolder-id}/messageRules/{messagerule-id}",
    "/me/mailFolders/delta",
    "/me/messages/delta",
    
    # User Mail - Complete Operations
    "/users/{user-id}/messages",
    "/users/{user-id}/messages/{message-id}",
    "/users/{user-id}/messages/{message-id}/attachments",
    "/users/{user-id}/messages/{message-id}/attachments/{attachment-id}",
    "/users/{user-id}/messages/{message-id}/extensions",
    "/users/{user-id}/mailFolders",
    "/users/{user-id}/mailFolders/{mailfolder-id}",
    "/users/{user-id}/mailFolders/{mailfolder-id}/messages",
    "/users/{user-id}/mailFolders/{mailfolder-id}/childFolders",
    "/users/{user-id}/mailFolders/{mailfolder-id}/messageRules",
    "/users/{user-id}/mailFolders/delta",
    "/users/{user-id}/messages/delta",
    
    # Personal Calendar - Complete Operations
    "/me/calendar",
    "/me/calendars",
    "/me/calendars/{calendar-id}",
    "/me/events",
    "/me/events/{event-id}",
    "/me/events/{event-id}/attachments",
    "/me/events/{event-id}/attachments/{attachment-id}",
    "/me/events/{event-id}/calendar",
    "/me/events/{event-id}/instances",
    "/me/events/{event-id}/instances/{event-id1}",
    "/me/events/{event-id}/extensions",
    "/me/events/{event-id}/extensions/{extension-id}",
    "/me/events/{event-id}/multiValueExtendedProperties",
    "/me/events/{event-id}/singleValueExtendedProperties",
    "/me/calendars/{calendar-id}/events",
    "/me/calendars/{calendar-id}/events/{event-id}",
    "/me/calendars/{calendar-id}/calendarView",
    "/me/calendarView",
    "/me/events/delta",
    "/me/calendars/delta",
    "/me/calendar/events",
    "/me/calendar/calendarView",
    
    # User Calendar - Complete Operations
    "/users/{user-id}/calendar",
    "/users/{user-id}/calendars",
    "/users/{user-id}/calendars/{calendar-id}",
    "/users/{user-id}/events",
    "/users/{user-id}/events/{event-id}",
    "/users/{user-id}/events/{event-id}/attachments",
    "/users/{user-id}/events/{event-id}/calendar",
    "/users/{user-id}/events/{event-id}/instances",
    "/users/{user-id}/events/{event-id}/extensions",
    "/users/{user-id}/calendars/{calendar-id}/events",
    "/users/{user-id}/calendars/{calendar-id}/calendarView",
    "/users/{user-id}/calendarView",
    "/users/{user-id}/events/delta",
    "/users/{user-id}/calendars/delta",
    "/users/{user-id}/calendar/events",
    "/users/{user-id}/calendar/calendarView",
    
    # Group Calendar - Complete Operations
    "/groups/{group-id}/calendar",
    "/groups/{group-id}/events",
    "/groups/{group-id}/events/{event-id}",
    "/groups/{group-id}/events/{event-id}/attachments",
    "/groups/{group-id}/events/{event-id}/calendar",
    "/groups/{group-id}/events/{event-id}/instances",
    "/groups/{group-id}/calendarView",
    "/groups/{group-id}/calendar/events",
    "/groups/{group-id}/calendar/calendarView",
    
    # Personal Contacts - Complete Operations
    "/me/contacts",
    "/me/contacts/{contact-id}",
    "/me/contacts/{contact-id}/photo",
    "/me/contacts/{contact-id}/photo/$value",
    "/me/contacts/{contact-id}/extensions",
    "/me/contacts/{contact-id}/extensions/{extension-id}",
    "/me/contacts/{contact-id}/multiValueExtendedProperties",
    "/me/contacts/{contact-id}/singleValueExtendedProperties",
    "/me/contactFolders",
    "/me/contactFolders/{contactfolder-id}",
    "/me/contactFolders/{contactfolder-id}/contacts",
    "/me/contactFolders/{contactfolder-id}/childFolders",
    "/me/contacts/delta",
    "/me/contactFolders/delta",
    
    # User Contacts - Complete Operations
    "/users/{user-id}/contacts",
    "/users/{user-id}/contacts/{contact-id}",
    "/users/{user-id}/contacts/{contact-id}/photo",
    "/users/{user-id}/contacts/{contact-id}/extensions",
    "/users/{user-id}/contactFolders",
    "/users/{user-id}/contactFolders/{contactfolder-id}",
    "/users/{user-id}/contactFolders/{contactfolder-id}/contacts",
    "/users/{user-id}/contactFolders/{contactfolder-id}/childFolders",
    "/users/{user-id}/contacts/delta",
    "/users/{user-id}/contactFolders/delta",
    
    # Mail Categories and Rules
    "/me/outlook/masterCategories",
    "/me/outlook/masterCategories/{outlookcategory-id}",
    "/users/{user-id}/outlook/masterCategories",
    "/users/{user-id}/outlook/masterCategories/{outlookcategory-id}",
    
    # Focused Inbox
    "/me/inferenceClassification",
    "/me/inferenceClassification/overrides",
    "/me/inferenceClassification/overrides/{inferenceclassificationoverride-id}",
    "/users/{user-id}/inferenceClassification",
    "/users/{user-id}/inferenceClassification/overrides",
    
    # People (Outlook related)
    "/me/people",
    "/me/people/{person-id}",
    "/users/{user-id}/people",
    "/users/{user-id}/people/{person-id}",
    
    # NOTE: Excluded patterns (OneDrive, Teams, SharePoint) are filtered out by EXCLUDED_KEYWORDS and EXCLUDED_PATHS
    # To re-enable them, remove the keywords from the exclusion lists above
]

# ---- Operation Model -------------------------------------------------------
@dataclass
class OutlookOperation:
    op_id: str
    http_method: str
    path: str
    summary: str
    description: str
    params: List[Mapping[str, Any]]
    request_body: Optional[Mapping[str, Any]] = None
    path_params: List[Dict[str, str]] = None
    operation_type: str = "general"  # mail, calendar, contacts, categories, rules, people

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
        """Determine the type of Outlook operation for better organization."""
        path_lower = self.path.lower()
        op_lower = self.op_id.lower()
        
        if any(keyword in path_lower for keyword in ["message", "mailfolder", "mailbox"]):
            return "mail"
        elif any(keyword in path_lower for keyword in ["calendar", "event", "calendarview"]):
            return "calendar"
        elif any(keyword in path_lower for keyword in ["contact", "contactfolder"]):
            return "contacts"
        elif any(keyword in path_lower for keyword in ["categories", "mastercategories"]):
            return "categories"
        elif any(keyword in path_lower for keyword in ["messagerule", "inferenceclassification"]):
            return "rules"
        elif any(keyword in path_lower for keyword in ["people", "person"]):
            return "people"
        elif any(keyword in path_lower for keyword in ["attachment"]):
            return "attachments"
        elif any(keyword in path_lower for keyword in ["extension", "extendedproperties"]):
            return "extensions"
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
    """Extract path parameters from Outlook path pattern."""
    import re
    
    # Find all {parameter} patterns
    pattern = r'\{([^}]+)\}'
    matches = re.findall(pattern, path)
    
    path_params = []
    for match in matches:
        # Clean up parameter name and convert to Python-friendly format
        param_name = match.replace('-', '_').replace('.', '_')
        
        # Determine parameter type based on common Outlook patterns
        param_type = 'str'
        description = f'Outlook path parameter: {match}'
        
        if 'id' in match.lower():
            description = f'Outlook {match.replace("-", " ").replace("_", " ")} identifier'
        elif 'user' in match.lower():
            description = f'User identifier: {match}'
        elif 'group' in match.lower():
            description = f'Group identifier: {match}'
        elif 'message' in match.lower():
            description = f'Message identifier: {match}'
        elif 'event' in match.lower():
            description = f'Event identifier: {match}'
        elif 'contact' in match.lower():
            description = f'Contact identifier: {match}'
        elif 'calendar' in match.lower():
            description = f'Calendar identifier: {match}'
        elif 'folder' in match.lower():
            description = f'Folder identifier: {match}'
        elif 'attachment' in match.lower():
            description = f'Attachment identifier: {match}'
        
        path_params.append({
            'name': param_name,
            'original': match,
            'type': param_type,
            'required': True,
            'description': description
        })
    
    return path_params

def _is_outlook_operation(path: str, op_id: str) -> bool:
    """Check if this operation is related to Outlook (Mail, Calendar, Contacts).
    
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
    
    # Include operations that are clearly Outlook related (CORE OUTLOOK KEYWORDS)
    outlook_core_keywords = [
        'message', 'messages', 'mailfolder', 'mailfolders', 'mailbox',
        'calendar', 'calendars', 'event', 'events', 'calendarview',
        'contact', 'contacts', 'contactfolder', 'contactfolders',
        'attachment', 'attachments', 'extension', 'extensions',
        'categories', 'mastercategories', 'messagerule', 'messagerules',
        'inferenceclassification', 'people', 'person', 'photo',
        'extendedproperties', 'outlook'
    ]
    
    return any(keyword in path_lower or keyword in op_lower for keyword in outlook_core_keywords)

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

def extract_outlook_operations(spec: Mapping[str, Any]) -> List[OutlookOperation]:
    """Extract Outlook-specific operations from OpenAPI spec."""
    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("Invalid OpenAPI spec: missing 'paths'")

    ops: List[OutlookOperation] = []
    skipped_count_ops = 0
    skipped_non_outlook = 0
    
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
            
        # Check if path matches any Outlook patterns
        if not any(_path_matches_pattern(path, pattern) for pattern in OUTLOOK_PATH_PATTERNS):
            # Additional check for Outlook-related operations
            if not _is_outlook_operation(path, ""):
                skipped_non_outlook += 1
                continue
        
        # ============ EXCLUSION CHECK (MODIFY EXCLUDED_PATHS LIST ABOVE) ============
        # Explicitly exclude paths based on exclusion list
        should_exclude = False
        for excluded_path in EXCLUDED_PATHS:
            if excluded_path.lower() in path.lower():
                should_exclude = True
                break
        
        if should_exclude:
            skipped_non_outlook += 1
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
            
            # Skip non-Outlook operations
            if not _is_outlook_operation(path, str(op_id)):
                skipped_non_outlook += 1
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
                skipped_non_outlook += 1
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
                OutlookOperation(
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
    uniq: Dict[str, OutlookOperation] = {}
    for op in ops:
        key = f"{op.op_id}_{op.http_method}_{op.path}"
        if key not in uniq:
            uniq[key] = op
    
    print(f"Outlook filtering summary (MODIFY EXCLUDED_KEYWORDS/EXCLUDED_PATHS to change):")
    print(f"  - Skipped {skipped_count_ops} count operations")
    print(f"  - Skipped {skipped_non_outlook} excluded operations (OneDrive, Teams, SharePoint, etc.)")
    print(f"  - Found {len(uniq)} Outlook operations (Mail, Calendar, Contacts)")
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

def extract_operation_parameters(op: OutlookOperation) -> Tuple[List[str], List[str], List[str]]:
    """Extract and categorize parameters from Outlook operation."""
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
    
    # Add Outlook-specific OData parameters
    outlook_odata_params = [
        "select: Optional[List[str]] = None",
        "expand: Optional[List[str]] = None", 
        "filter: Optional[str] = None",
        "orderby: Optional[str] = None",
        "search: Optional[str] = None",
        "top: Optional[int] = None",
        "skip: Optional[int] = None"
    ]
    optional_params.extend(outlook_odata_params)
    
    # Add Outlook OData parameter docs
    outlook_odata_docs = [
        "            select (optional): Select specific properties to return",
        "            expand (optional): Expand related entities (e.g., attachments, calendar)",
        "            filter (optional): Filter the results using OData syntax", 
        "            orderby (optional): Order the results by specified properties",
        "            search (optional): Search for messages, events, or contacts by content",
        "            top (optional): Limit number of results returned",
        "            skip (optional): Skip number of results for pagination"
    ]
    param_docs.extend(outlook_odata_docs)
    
    # Add request body if needed (for mail/calendar/contact operations)
    has_body = op.request_body is not None
    if has_body:
        optional_params.append("request_body: Optional[Mapping[str, Any]] = None")
        param_docs.append("            request_body (optional): Request body data for Outlook operations")
    
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
    """Get the appropriate query parameter classes for the Outlook endpoint."""
    path_lower = path.lower()
    method_lower = method.lower()
    
    # Map paths to their corresponding query parameter classes
    # TODO: Fix these as we integrate the apis with the platform
    if '/me/messages' in path_lower and method_lower == 'get':
        return "MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters", "MessagesRequestBuilder.MessagesRequestBuilderGetRequestConfiguration"
    elif '/me/events' in path_lower and method_lower == 'get':
        return "EventsRequestBuilder.EventsRequestBuilderGetQueryParameters", "EventsRequestBuilder.EventsRequestBuilderGetRequestConfiguration"
    elif '/me/contacts' in path_lower and method_lower == 'get':
        return "ContactsRequestBuilder.ContactsRequestBuilderGetQueryParameters", "ContactsRequestBuilder.ContactsRequestBuilderGetRequestConfiguration"
    elif '/me/calendars' in path_lower and method_lower == 'get':
        return "CalendarsRequestBuilder.CalendarsRequestBuilderGetQueryParameters", "CalendarsRequestBuilder.CalendarsRequestBuilderGetRequestConfiguration"
    elif '/me/mailfolders' in path_lower and method_lower == 'get':
        return "MailFoldersRequestBuilder.MailFoldersRequestBuilderGetQueryParameters", "MailFoldersRequestBuilder.MailFoldersRequestBuilderGetRequestConfiguration"
    elif '/me/contactfolders' in path_lower and method_lower == 'get':
        return "ContactFoldersRequestBuilder.ContactFoldersRequestBuilderGetQueryParameters", "ContactFoldersRequestBuilder.ContactFoldersRequestBuilderGetRequestConfiguration"
    elif '/users/{user-id}/messages' in path_lower and method_lower == 'get':
        return "MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters", "MessagesRequestBuilder.MessagesRequestBuilderGetRequestConfiguration"
    elif '/users/{user-id}/events' in path_lower and method_lower == 'get':
        return "EventsRequestBuilder.EventsRequestBuilderGetQueryParameters", "EventsRequestBuilder.EventsRequestBuilderGetRequestConfiguration"
    elif '/users/{user-id}/contacts' in path_lower and method_lower == 'get':
        return "ContactsRequestBuilder.ContactsRequestBuilderGetQueryParameters", "ContactsRequestBuilder.ContactsRequestBuilderGetRequestConfiguration"
    else:
        # Fallback to generic RequestConfiguration for unmatched endpoints
        return "RequestConfiguration", "RequestConfiguration"

def build_outlook_method_code(op: OutlookOperation) -> str:
    """Generate comprehensive async method code for Outlook operation."""
    required_params, optional_params, param_docs = extract_operation_parameters(op)

    # Build method signature
    all_params = required_params + optional_params
    
    if len(all_params) <= 5:
        sig = ", ".join(all_params)
    else:
        params_formatted = ",\n        ".join(all_params)
        sig = f"\n        {params_formatted}\n    "

    summary = op.summary or op.op_id
    params_doc = "\n".join(param_docs) if param_docs else "            (standard Outlook parameters)"

    # Generate Outlook-specific Graph SDK method call
    graph_method_call = _generate_outlook_graph_call(op)
    # Determine query parameter classes during generation
    query_param_class, config_class = _get_query_param_classes(op.path, op.http_method)
    response_type = "OutlookResponse"

    method_code = f"""    async def {op.wrapper_name}({sig}) -> {response_type}:
        \"\"\"{summary}.
        Outlook operation: {op.http_method} {op.path}
        Operation type: {op.operation_type}
        Args:
{params_doc}
        Returns:
            {response_type}: Outlook response wrapper with success/data/error
        \"\"\"
        # Build query parameters including OData for Outlook
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
            
            # Add consistency level for search operations in Outlook
            if search:
                if not config.headers:
                    config.headers = {{}}
                config.headers['ConsistencyLevel'] = 'eventual'

{graph_method_call}
            return self._handle_outlook_response(response)
        except Exception as e:
            return {response_type}(
                success=False,
                error=f"Outlook API call failed: {{str(e)}}",
            )
"""

    return method_code

def _generate_outlook_graph_call(op: OutlookOperation) -> str:
    """Generate Outlook-specific Microsoft Graph SDK method call."""
    path = op.path
    method = op.http_method.lower()
    has_body = op.request_body is not None
    
    # Build request builder chain optimized for Outlook
    request_builder = _build_outlook_request_builder_chain(path, op.path_params)
    
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

def _build_outlook_request_builder_chain(path: str, path_params: List[Dict[str, str]]) -> str:
    """Build Outlook-optimized Microsoft Graph SDK request builder chain."""
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
            elif prev_segment == "messages":
                builder += f".by_message_id({python_param})"
            elif prev_segment == "events":
                builder += f".by_event_id({python_param})"
            elif prev_segment == "contacts":
                builder += f".by_contact_id({python_param})"
            elif prev_segment == "calendars":
                builder += f".by_calendar_id({python_param})"
            elif prev_segment == "mailFolders":
                builder += f".by_mail_folder_id({python_param})"
            elif prev_segment == "contactFolders":
                builder += f".by_contact_folder_id({python_param})"
            elif prev_segment == "attachments":
                builder += f".by_attachment_id({python_param})"
            elif prev_segment == "extensions":
                builder += f".by_extension_id({python_param})"
            elif prev_segment == "instances":
                builder += f".by_event_id({python_param})"  # Event instances use event ID
            elif prev_segment == "messageRules":
                builder += f".by_message_rule_id({python_param})"
            elif prev_segment == "masterCategories":
                builder += f".by_outlook_category_id({python_param})"
            elif prev_segment == "overrides":
                builder += f".by_inference_classification_override_id({python_param})"
            elif prev_segment == "people":
                builder += f".by_person_id({python_param})"
            else:
                # Generic fallback for other Outlook parameters
                builder += f".by_{prev_segment[:-1] if prev_segment.endswith('s') else prev_segment}_id({python_param})"
                
        elif segment == "$value":
            # Handle special $value endpoints (like photo/$value)
            builder += ".value"
        else:
            # Regular path segment - convert to appropriate Outlook SDK method
            if segment == "me":
                builder += ".me"
            elif segment == "messages":
                builder += ".messages"
            elif segment == "mailFolders":
                builder += ".mail_folders"
            elif segment == "calendar":
                builder += ".calendar"
            elif segment == "calendars":
                builder += ".calendars"
            elif segment == "events":
                builder += ".events"
            elif segment == "calendarView":
                builder += ".calendar_view"
            elif segment == "contacts":
                builder += ".contacts"
            elif segment == "contactFolders":
                builder += ".contact_folders"
            elif segment == "attachments":
                builder += ".attachments"
            elif segment == "extensions":
                builder += ".extensions"
            elif segment == "instances":
                builder += ".instances"
            elif segment == "childFolders":
                builder += ".child_folders"
            elif segment == "messageRules":
                builder += ".message_rules"
            elif segment == "delta":
                builder += ".delta"
            elif segment == "multiValueExtendedProperties":
                builder += ".multi_value_extended_properties"
            elif segment == "singleValueExtendedProperties":
                builder += ".single_value_extended_properties"
            elif segment == "outlook":
                builder += ".outlook"
            elif segment == "masterCategories":
                builder += ".master_categories"
            elif segment == "inferenceClassification":
                builder += ".inference_classification"
            elif segment == "overrides":
                builder += ".overrides"
            elif segment == "people":
                builder += ".people"
            elif segment == "photo":
                builder += ".photo"
            else:
                # Convert to snake_case for other segments
                snake_segment = to_snake(segment)
                builder += f".{snake_segment}"
        
        i += 1
    
    return builder

def build_outlook_class_code(ops: Sequence[OutlookOperation]) -> str:
    """Generate the complete Outlook client class."""
    # Group methods by operation type for better organization
    methods_by_type = {}
    for op in ops:
        if op.operation_type not in methods_by_type:
            methods_by_type[op.operation_type] = []
        methods_by_type[op.operation_type].append(op)
    
    class_name = "OutlookDataSource"
    response_class = "OutlookResponse"

    header = f"""

import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Mapping, Optional

# Import MS Graph specific query parameter classes for Outlook
from msgraph.generated.users.item.messages.messages_request_builder import MessagesRequestBuilder # type: ignore
from msgraph.generated.users.item.events.events_request_builder import EventsRequestBuilder # type: ignore
from msgraph.generated.users.item.contacts.contacts_request_builder import ContactsRequestBuilder # type: ignore
from msgraph.generated.users.item.calendars.calendars_request_builder import CalendarsRequestBuilder # type: ignore
from msgraph.generated.users.item.mail_folders.mail_folders_request_builder import MailFoldersRequestBuilder # type: ignore
from msgraph.generated.users.item.contact_folders.contact_folders_request_builder import ContactFoldersRequestBuilder # type: ignore
from kiota_abstractions.base_request_configuration import RequestConfiguration # type: ignore


from app.sources.client.microsoft.microsoft import MSGraphClient

# Outlook-specific response wrapper
class OutlookResponse:
    \"\"\"Standardized Outlook API response wrapper.\"\"\"
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
    Comprehensive Microsoft Outlook API client with complete Mail, Calendar, and Contacts coverage.

    Features:
    - Complete Outlook API coverage with {len(ops)} methods organized by operation type
    - Support for Personal and User Mail, Calendar, and Contacts
    - Group Calendar support for team scheduling
    - Complete Mail operations: messages, folders, attachments, rules, categories
    - Complete Calendar operations: events, calendars, calendar view, instances, recurring events
    - Complete Contact operations: contacts, contact folders, photos, extended properties
    - Search capabilities across mail, calendar, and contacts
    - Delta synchronization for efficient change tracking
    - Attachment handling for messages, events, and contacts
    - Extended properties support for custom data
    - Microsoft Graph SDK integration with Outlook-specific optimizations
    - Async snake_case method names for all operations
    - Standardized {response_class} format for all responses
    - Comprehensive error handling and Outlook-specific response processing

    EXCLUDED OPERATIONS (modify EXCLUDED_KEYWORDS list to change):
    - OneDrive operations (drive, drives, items)
    - Teams operations (chats, teams, channels)
    - SharePoint operations (sites, lists, document libraries)
    - OneNote operations (notebooks, sections, pages)
    - Planner operations (plans, tasks, buckets)
    - Directory operations (directoryObjects, devices)
    - Admin operations (admin, compliance, security)
    - To-Do operations (todo lists and tasks)
    - Device management operations
    - Communications operations (communications, education, identity)
    - Education operations (education)
    - Identity operations (identity)
    - Photos operations (photos)

    Operation Types:
    - Mail operations: Messages, mail folders, message rules, categories
    - Calendar operations: Events, calendars, calendar view, instances
    - Contacts operations: Contacts, contact folders, photos
    - Attachments operations: File attachments for messages, events, contacts
    - Extensions operations: Custom properties and extended data
    - Rules operations: Mail rules and inference classification
    - Categories operations: Outlook categories and master categories
    - People operations: People suggestions and directory
    - Photos operations: Photos
    - General operations: Base Outlook functionality
    \"\"\"

    def __init__(self, client: MSGraphClient) -> None:
        \"\"\"Initialize with Microsoft Graph SDK client optimized for Outlook.\"\"\"
        self.client = client.get_client().get_ms_graph_service_client()
        if not hasattr(self.client, "me"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("Outlook client initialized with {len(ops)} methods")

    def _handle_outlook_response(self, response: object) -> OutlookResponse:
        \"\"\"Handle Outlook API response with comprehensive error handling.\"\"\"
        try:
            if response is None:
                return OutlookResponse(success=False, error="Empty response from Outlook API")
            
            success = True
            error_msg = None
            
            # Enhanced error response handling for Outlook operations
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
            
            return OutlookResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling Outlook response: {{e}}")
            return OutlookResponse(success=False, error=str(e))

    def get_data_source(self) -> '{class_name}':
        \"\"\"Get the underlying Outlook client.\"\"\"
        return self

"""

    # Add methods organized by operation type
    methods_section = ""
    
    # Define the order of operation types for better organization
    operation_order = ['mail', 'calendar', 'contacts', 'attachments', 'extensions', 'rules', 'categories', 'people', 'general']
    
    for op_type in operation_order:
        if op_type in methods_by_type:
            methods = methods_by_type[op_type]
            methods_section += f"    # ========== {op_type.upper()} OPERATIONS ({len(methods)} methods) ==========\n\n"
            
            for op in methods:
                method_code = build_outlook_method_code(op)
                methods_section += method_code + "\n"
    
    return header + methods_section + "\n\n"

# ---- Public entrypoints ----------------------------------------------------
def generate_outlook_client(
    *,
    out_path: Optional[str] = None,
    spec_url: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> str:
    """Generate comprehensive Outlook client. Returns file path."""
    out_filename = out_path or "outlook_client.py"
    
    print(f"Loading Microsoft Graph OpenAPI specification for Outlook...")
    spec = load_spec(spec_url=spec_url, spec_path=spec_path)
    
    print(f"Extracting Outlook operations with comprehensive coverage...")
    ops = extract_outlook_operations(spec)
    
    print(f"Found {len(ops)} Outlook operations with full parameter support")
    
    # Show breakdown by operation type
    ops_by_type = {}
    for op in ops:
        if op.operation_type not in ops_by_type:
            ops_by_type[op.operation_type] = 0
        ops_by_type[op.operation_type] += 1
    
    print("Operation breakdown:")
    for op_type, count in sorted(ops_by_type.items()):
        print(f"  - {op_type}: {count} methods")
    
    print("Generating comprehensive Outlook async client code...")
    code = build_outlook_class_code(ops)
    
    # Create microsoft directory (reuse the existing structure)
    script_dir = Path(__file__).parent if __file__ else Path('.')
    microsoft_dir = script_dir / 'microsoft'
    microsoft_dir.mkdir(exist_ok=True)
    
    # Write file
    full_path = microsoft_dir / out_filename
    full_path.write_text(code, encoding="utf-8")
    print(f"Saved comprehensive Outlook client to: {full_path}")
    
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
        description="Generate comprehensive Outlook API client"
    )
    ap.add_argument("--out", help="Output .py file path (default: outlook_client.py)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--spec-url", default=None, help="Microsoft Graph OpenAPI spec URL")
    g.add_argument("--spec-path", help="Local spec file path (JSON/YAML)")
    ap.add_argument("--show-patterns", action="store_true", help="Show all Outlook path patterns")
    
    return ap.parse_args(argv)

def _show_patterns() -> None:
    """Show all Outlook path patterns that will be matched."""
    print("Outlook API Path Patterns (Comprehensive Coverage):")
    print(f"Total patterns: {len(OUTLOOK_PATH_PATTERNS)}")
    print()
    
    # Group patterns by category
    pattern_groups = {
        "Personal Mail": [p for p in OUTLOOK_PATH_PATTERNS if p.startswith("/me/messages") or p.startswith("/me/mailFolders")],
        "User Mail": [p for p in OUTLOOK_PATH_PATTERNS if p.startswith("/users/") and ("messages" in p or "mailFolders" in p)],
        "Personal Calendar": [p for p in OUTLOOK_PATH_PATTERNS if p.startswith("/me/") and ("calendar" in p or "events" in p)],
        "User Calendar": [p for p in OUTLOOK_PATH_PATTERNS if p.startswith("/users/") and ("calendar" in p or "events" in p)],
        "Group Calendar": [p for p in OUTLOOK_PATH_PATTERNS if p.startswith("/groups/") and ("calendar" in p or "events" in p)],
        "Personal Contacts": [p for p in OUTLOOK_PATH_PATTERNS if p.startswith("/me/contact")],
        "User Contacts": [p for p in OUTLOOK_PATH_PATTERNS if p.startswith("/users/") and "contact" in p],
        "Categories & Rules": [p for p in OUTLOOK_PATH_PATTERNS if "categories" in p or "messageRules" in p or "inference" in p],
        "People": [p for p in OUTLOOK_PATH_PATTERNS if "people" in p],
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
    
    print(f"Starting comprehensive Outlook API client generation...")
    
    try:
        out_path = generate_outlook_client(
            out_path=ns.out,
            spec_url=ns.spec_url,
            spec_path=ns.spec_path,
        )
        
        print(f"\nOutlook comprehensive client generation completed!")
        print(f"Generated class: OutlookDataSource")
        print(f"Output file: {out_path}")
        print(f"Features:")
        print(f"  - Complete Outlook API endpoint coverage (Mail, Calendar, Contacts)")
        print(f"  - Support for Personal, User, and Group operations")
        print(f"  - Mail operations: messages, folders, attachments, rules, categories")
        print(f"  - Calendar operations: events, calendars, scheduling, recurring events")
        print(f"  - Contact operations: contacts, folders, photos, extended properties")
        print(f"  - Advanced features: search, delta sync, extensions, attachments")
        print(f"  - Async methods with comprehensive error handling")
        print(f"")
        print(f"🔧 TO MODIFY EXCLUSIONS:")
        print(f"  - Edit EXCLUDED_KEYWORDS list to add/remove excluded API keywords")
        print(f"  - Edit EXCLUDED_PATHS list to add/remove excluded path patterns")
        print(f"  - Current exclusions: OneDrive, Teams, SharePoint, OneNote, Planner")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()