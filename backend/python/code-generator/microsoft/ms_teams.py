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
Microsoft Teams API Client Generator (Teams, Chats, Channels Focus)

Generates a focused Teams API client with comprehensive endpoint coverage for:
- Personal Teams (/me/joinedTeams, /me/chats)
- Teams (/teams, /teams/{team-id})
- Team Members (/teams/{team-id}/members, /teams/{team-id}/owners)
- Channels (/teams/{team-id}/channels, /teams/{team-id}/channels/{channel-id})
- Channel Messages (/teams/{team-id}/channels/{channel-id}/messages)
- Chats (/chats, /chats/{chat-id}, /me/chats)
- Chat Messages (/chats/{chat-id}/messages)
- Chat Members (/chats/{chat-id}/members)
- Team Apps (/teams/{team-id}/installedApps)
- Team Tabs (/teams/{team-id}/channels/{channel-id}/tabs)
- Team Operations (/teams/{team-id}/operations)
- Team Schedule (/teams/{team-id}/schedule)
- Teamwork (/teamwork)

Explicitly excludes:
- OneDrive operations (/drive, /drives endpoints)
- Outlook operations (/messages, /events, /contacts, /calendar endpoints)
- SharePoint operations (/sites endpoints)
- OneNote operations (/onenote endpoints)
- Planner operations (/planner endpoints)
- Directory operations (/directoryObjects endpoints)
- Device operations (/devices endpoints)
- Security operations (/security endpoints)
- Compliance operations (/compliance endpoints)
- Admin operations (/admin endpoints)
- Non-Teams Microsoft Graph endpoints

EASY MODIFICATION:
To add/remove excluded APIs, modify the EXCLUDED_KEYWORDS and EXCLUDED_PATHS lists at the top of this file.

Enhanced Features:
- Complete Teams endpoint coverage with all path parameters
- Proper parameter extraction for team-id, channel-id, chat-id, message-id, member-id
- Comprehensive method signatures with required and optional parameters
- Microsoft Graph SDK integration optimized for Teams operations
- Specialized Teams response handling and error management
- Support for team management, chat operations, and channel communications
"""

#from __future__ import annotations

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

# Keywords to exclude from Teams operations (ADD MORE HERE TO EXCLUDE)
EXCLUDED_KEYWORDS = [
    'drive',          # Exclude OneDrive operations
    'drives',         # Exclude OneDrive operations
    'message',        # Exclude Outlook message operations (keep Teams messages)
    'mailFolder',     # Exclude Outlook mail folder operations
    'mailFolders',    # Exclude Outlook mail folder operations
    'event',          # Exclude Outlook event operations (keep Teams events)
    'contact',        # Exclude Outlook contact operations
    'contacts',       # Exclude Outlook contact operations
    'calendar',       # Exclude Outlook calendar operations (keep Teams calendar)
    'calendars',      # Exclude Outlook calendar operations (keep Teams calendar)
    'site',           # Exclude SharePoint site operations
    'sites',          # Exclude SharePoint site operations
    'list',           # Exclude SharePoint list operations
    'lists',          # Exclude SharePoint list operations
    'onenote',        # Exclude OneNote operations
    'planner',        # Exclude Planner operations
    'device',         # Exclude Device operations
    'devices',        # Exclude Device operations
    'security',       # Exclude Security operations
    'compliance',     # Exclude Compliance operations
    'admin',          # Exclude Admin operations
    'directoryObject', # Exclude Directory operations
    'directoryObjects', # Exclude Directory operations
    'application',    # Exclude Application operations
    'applications',   # Exclude Application operations
    'servicePrincipal', # Exclude Service Principal operations
    'servicePrincipals', # Exclude Service Principal operations
    'identity',       # Exclude Identity operations
    'agreements',     # Exclude Agreements operations
    'auditLogs',      # Exclude Audit Logs operations
    'connections',    # Exclude Connections operations
    'deviceAppManagement', # Exclude Device App Management operations
    'deviceManagement', # Exclude Device Management operations
    'identityGovernance', # Exclude Identity Governance operations
    'analytics',      # Exclude Analytics operations
    'storage',        # Exclude Storage operations
    'solutions',      # Exclude Solutions operations
    'search',         # Exclude Search operations (keep Teams search)
    'external',       # Exclude External operations
    'workbook',       # Exclude Workbook operations
    'content',        # Exclude Upload operations
    'createUploadSession', # Exclude Upload operations
    'permission',     # Exclude Sharing operations
    'permissions',    # Exclude Sharing operations
    'invite',         # Exclude Sharing operations
    'createLink',     # Exclude Sharing operations
    'share',          # Exclude Sharing operations
]

# Specific paths to exclude from Teams operations (ADD MORE HERE TO EXCLUDE)
EXCLUDED_PATHS = [
    '/me/drive',
    '/me/drives',
    '/users/{user-id}/drive',
    '/users/{user-id}/drives',
    '/groups/{group-id}/drive',
    '/groups/{group-id}/drives',
    '/sites',
    '/sites/{site-id}',
    '/me/messages',
    '/me/mailFolders',
    '/me/events',
    '/me/calendar',
    '/me/calendars',
    '/me/contacts',
    '/me/contactFolders',
    '/users/{user-id}/messages',
    '/users/{user-id}/mailFolders',
    '/users/{user-id}/events',
    '/users/{user-id}/calendar',
    '/users/{user-id}/calendars',
    '/users/{user-id}/contacts',
    '/users/{user-id}/contactFolders',
    '/me/onenote',
    '/users/{user-id}/onenote',
    '/groups/{group-id}/onenote',
    '/me/planner',
    '/users/{user-id}/planner',
    '/groups/{group-id}/planner',
    '/devices',
    '/device',
    '/security',
    '/compliance',
    '/admin',
    '/directoryObjects',
    '/applications',
    '/servicePrincipals',
    '/identityGovernance',
    '/auditLogs',
    '/agreements',
    '/deviceAppManagement',
    '/deviceManagement',
    '/connections',
    '/analytics',
    '/storage',
    '/solutions',
    '/search',
    '/external',
]

# =============================================================================

# Set up logger
logger = logging.getLogger(__name__)

# Microsoft Graph OpenAPI specification URLs
DEFAULT_SPEC_URL = "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/master/openapi/v1.0/openapi.yaml"
FALLBACK_SPEC_URLS = [
    "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/main/openapi/v1.0/openapi.yaml",
]

# Comprehensive Teams path patterns for focused coverage
TEAMS_PATH_PATTERNS = [
    # Personal Teams
    "/me/joinedTeams",
    "/me/chats",
    "/me/teamwork",
    "/me/teamwork/installedApps",
    
    # Teams Operations
    "/teams",
    "/teams/{team-id}",
    "/teams/{team-id}/members",
    "/teams/{team-id}/owners",
    "/teams/{team-id}/guests",
    "/teams/{team-id}/channels",
    "/teams/{team-id}/channels/{channel-id}",
    "/teams/{team-id}/channels/{channel-id}/members",
    "/teams/{team-id}/channels/{channel-id}/messages",
    "/teams/{team-id}/channels/{channel-id}/messages/{message-id}",
    "/teams/{team-id}/channels/{channel-id}/messages/{message-id}/replies",
    "/teams/{team-id}/channels/{channel-id}/tabs",
    "/teams/{team-id}/channels/{channel-id}/tabs/{tab-id}",
    "/teams/{team-id}/operations",
    "/teams/{team-id}/photo",
    "/teams/{team-id}/schedule",
    "/teams/{team-id}/archive",
    "/teams/{team-id}/unarchive",
    "/teams/{team-id}/clone",
    "/teams/{team-id}/completeMigration",
    "/teams/{team-id}/sendActivityNotification",
    
    # Team Apps
    "/teams/{team-id}/installedApps",
    "/teams/{team-id}/installedApps/{installed-app-id}",
    "/teams/{team-id}/installedApps/{installed-app-id}/upgrade",
    
    # Chat Operations
    "/chats",
    "/chats/{chat-id}",
    "/chats/{chat-id}/members",
    "/chats/{chat-id}/members/{membership-id}",
    "/chats/{chat-id}/messages",
    "/chats/{chat-id}/messages/{message-id}",
    "/chats/{chat-id}/messages/{message-id}/replies",
    "/chats/{chat-id}/tabs",
    "/chats/{chat-id}/tabs/{tab-id}",
    "/chats/{chat-id}/installedApps",
    "/chats/{chat-id}/installedApps/{installed-app-id}",
    "/chats/{chat-id}/sendActivityNotification",
    
    # Teamwork
    "/teamwork",
    "/teamwork/deletedTeams",
    "/teamwork/deletedTeams/{deleted-team-id}",
    
    # User Teams Context
    "/users/{user-id}/joinedTeams",
    "/users/{user-id}/chats",
    "/users/{user-id}/teamwork",
    "/users/{user-id}/teamwork/installedApps",
    
    # Group Teams Context  
    "/groups/{group-id}/team",
    "/groups/{group-id}/team/members",
    "/groups/{group-id}/team/owners",
    "/groups/{group-id}/team/channels",
    "/groups/{group-id}/team/operations",
    "/groups/{group-id}/team/photo",
    "/groups/{group-id}/team/schedule",
    "/groups/{group-id}/team/archive",
    "/groups/{group-id}/team/unarchive",
    "/groups/{group-id}/team/clone",
]

@dataclass
class TeamsOperation:
    """Represents a Teams API operation with comprehensive parameter mapping."""
    op_id: str
    http_method: str
    path: str
    summary: str
    description: str
    params: List[Dict[str, Any]]
    request_body: Optional[Dict[str, Any]]
    path_params: Optional[List[Dict[str, str]]] = None
    operation_type: str = ""
    
    @property
    def py_name(self) -> str:
        """Get Python method name."""
        return to_snake(self.op_id)

    def __post_init__(self):
        if self.path_params is None:
            self.path_params = []
        # Determine operation type based on path and operation
        self.operation_type = self._determine_operation_type()

    def _determine_operation_type(self) -> str:
        """Determine the type of Teams operation for better organization."""
        path_lower = self.path.lower()
        op_lower = self.op_id.lower()
        
        if '/teams/{team-id}/channels' in path_lower:
            return "channels"
        elif '/teams/{team-id}/members' in path_lower or '/teams/{team-id}/owners' in path_lower:
            return "members"
        elif '/chats' in path_lower:
            return "chats"
        elif '/messages' in path_lower:
            return "messages"
        elif '/installedApps' in path_lower or '/tabs' in path_lower:
            return "apps"
        elif '/teams/{team-id}' in path_lower:
            return "teams"
        elif '/teamwork' in path_lower:
            return "teamwork"
        elif '/me/joinedTeams' in path_lower or '/me/chats' in path_lower:
            return "personal"
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
    """Extract path parameters from Teams path pattern."""
    import re
    
    # Find all {parameter} patterns
    pattern = r'\{([^}]+)\}'
    matches = re.findall(pattern, path)
    
    path_params = []
    for match in matches:
        # Clean up parameter name and convert to Python-friendly format
        param_name = match.replace('-', '_').replace('.', '_')
        
        # Determine parameter type based on common Teams patterns
        param_type = 'str'
        description = f'Teams path parameter: {match}'
        
        if 'id' in match.lower():
            description = f'Teams {match.replace("-", " ").replace("_", " ")} identifier'
        elif 'path' in match.lower():
            description = f'Teams item path: {match}'
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

def _is_teams_operation(path: str, op_id: str) -> bool:
    """Check if this operation is related to Teams.
    
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
    
    # Include operations that are clearly Teams related (CORE TEAMS KEYWORDS)
    teams_core_keywords = [
        'team', 'teams', 'chat', 'chats', 'channel', 'channels',
        'message', 'messages', 'member', 'members', 'teamwork'
    ]
    
    return any(keyword in path_lower or keyword in op_lower for keyword in teams_core_keywords)

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

def extract_teams_operations(spec: Mapping[str, Any]) -> List[TeamsOperation]:
    """Extract Teams-specific operations from OpenAPI spec."""
    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("Invalid OpenAPI spec: missing 'paths'")

    ops: List[TeamsOperation] = []
    skipped_count_ops = 0
    skipped_non_teams = 0
    
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
            
        # Check if path matches any Teams patterns
        if not any(_path_matches_pattern(path, pattern) for pattern in TEAMS_PATH_PATTERNS):
            # Additional check for Teams-related operations
            if not _is_teams_operation(path, ""):
                skipped_non_teams += 1
                continue
        
        # ============ EXCLUSION CHECK (MODIFY EXCLUDED_PATHS LIST ABOVE) ============
        # Explicitly exclude paths based on exclusion list
        should_exclude = False
        for excluded_path in EXCLUDED_PATHS:
            if excluded_path.lower() in path.lower():
                should_exclude = True
                break
        
        if should_exclude:
            skipped_non_teams += 1
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
                continue
            
            summary = op.get("summary", "")
            description = op.get("description", "")
            
            # Skip count operations
            if _is_count_operation(path, str(op_id), summary, description):
                skipped_count_ops += 1
                continue
            
            # ========= EXCLUSION CHECK (MODIFY EXCLUDED_KEYWORDS LIST ABOVE) ==========
            # Skip operations with excluded keywords in summary/description
            op_text = f"{summary} {description}".lower()
            should_skip = False
            for keyword in EXCLUDED_KEYWORDS:
                if keyword in op_text:
                    should_skip = True
                    break
            
            if should_skip:
                skipped_non_teams += 1
                continue
            # ===========================================================================
            
            # Additional Teams operation validation
            if not _is_teams_operation(path, str(op_id)):
                skipped_non_teams += 1
                continue
            
            # Collect all parameters for this operation
            merged_params = []
            
            # Add path-level parameters
            for p in item.get("parameters", []):
                if isinstance(p, Mapping):
                    merged_params.append(p)
            
            # Add operation-level parameters
            for p in op.get("parameters", []):
                if isinstance(p, Mapping):
                    merged_params.append(p)
            
            ops.append(
                TeamsOperation(
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
    uniq: Dict[str, TeamsOperation] = {}
    for op in ops:
        key = f"{op.op_id}_{op.http_method}_{op.path}"
        if key not in uniq:
            uniq[key] = op
    
    print(f"Teams filtering summary (MODIFY EXCLUDED_KEYWORDS/EXCLUDED_PATHS to change):")
    print(f"  - Skipped {skipped_count_ops} count operations")
    print(f"  - Skipped {skipped_non_teams} excluded operations (OneDrive, Outlook, SharePoint, etc.)")
    print(f"  - Found {len(uniq)} Teams operations")
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
    name = name.replace(".", "_")
    name = name.replace("-", "_")
    
    # Insert underscores between lower/digit and upper
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    # Clean up and lowercase
    name = _IDENT_RE.sub("_", name)
    name = re.sub(r"_+", "_", name)
    name = name.lower().strip("_")
    
    if not name:
        return "unknown"
    if name[0].isdigit():
        name = f"_{name}"
    if keyword.iskeyword(name):
        name += "_"
    
    return name

def get_teams_query_parameters(path: str, method: str) -> Tuple[str, str]:
    """Get appropriate query parameter classes for Teams operations."""
    path_lower = path.lower()
    method_lower = method.lower()
    
    # Map paths to their corresponding query parameter classes
    if '/teams' in path_lower and method_lower == 'get':
        return "TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters", "TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration"
    elif '/chats' in path_lower and method_lower == 'get':
        return "ChatsRequestBuilder.ChatsRequestBuilderGetQueryParameters", "ChatsRequestBuilder.ChatsRequestBuilderGetRequestConfiguration"
    elif '/channels' in path_lower and method_lower == 'get':
        return "ChannelsRequestBuilder.ChannelsRequestBuilderGetQueryParameters", "ChannelsRequestBuilder.ChannelsRequestBuilderGetRequestConfiguration"
    elif '/messages' in path_lower and method_lower == 'get':
        return "MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters", "MessagesRequestBuilder.MessagesRequestBuilderGetRequestConfiguration"
    elif '/members' in path_lower and method_lower == 'get':
        return "MembersRequestBuilder.MembersRequestBuilderGetQueryParameters", "MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration"
    else:
        # Fallback to generic RequestConfiguration for unmatched endpoints
        return "RequestConfiguration", "RequestConfiguration"

def build_teams_method_code(op: TeamsOperation) -> str:
    """Generate comprehensive async method code for Teams operation."""
    method_name = op.py_name
    
    # Build parameter list
    params = ["self"]
    param_docs = []
    
    # Add path parameters
    for param in op.path_params or []:
        safe_param = param['name']
        params.append(f"{safe_param}: str")
        param_docs.append(f"        {safe_param}: {param['description']}")
    
    # Add common query parameters for GET operations
    if op.http_method == 'GET':
        query_class, config_class = get_teams_query_parameters(op.path, op.http_method)
        params.extend([
            "select: Optional[List[str]] = None",
            "expand: Optional[List[str]] = None", 
            "filter: Optional[str] = None",
            "orderby: Optional[List[str]] = None",
            "search: Optional[str] = None",
            "top: Optional[int] = None",
            "skip: Optional[int] = None"
        ])
        param_docs.extend([
            "        select: Select specific fields",
            "        expand: Expand related entities", 
            "        filter: Filter results",
            "        orderby: Order results",
            "        search: Search in results",
            "        top: Limit number of results",
            "        skip: Skip number of results"
        ])
    
    # Add request body parameter for POST/PUT/PATCH
    if op.http_method in ['POST', 'PUT', 'PATCH'] and op.request_body:
        params.append("body: Optional[Dict[str, Any]] = None")
        param_docs.append("        body: Request body data")
    
    # Build method signature
    param_str = ", ".join(params)
    
    # Build request builder chain
    builder = build_teams_request_builder(op.path, op.path_params or [])
    
    # Build method body
    method_body = f"""
        \"\"\"
        {op.summary or f'{op.http_method} operation for {op.path}'}
        Teams operation: {op.http_method} {op.path}
        Operation type: {op.operation_type}
        Args:
{chr(10).join(param_docs) if param_docs else '            None'}
        Returns:
            TeamsResponse: Teams API response with success status and data
        \"\"\"
        try:"""
    
    if op.http_method == 'GET':
        query_class, config_class = get_teams_query_parameters(op.path, op.http_method)
        method_body += f"""
            # Build query parameters
            query_params = {query_class}()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = {config_class}(query_parameters=query_params)
            response = await {builder}.get(request_configuration=config)"""
    elif op.http_method == 'POST':
        method_body += f"""
            response = await {builder}.post(body=body)"""
    elif op.http_method == 'PUT':
        method_body += f"""
            response = await {builder}.put(body=body)"""
    elif op.http_method == 'PATCH':
        method_body += f"""
            response = await {builder}.patch(body=body)"""
    elif op.http_method == 'DELETE':
        method_body += f"""
            response = await {builder}.delete()"""
    
    method_body += f"""
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in {method_name}: {{e}}")
            return TeamsResponse(success=False, error=str(e))"""
    
    return f"""
    async def {method_name}({param_str}) -> TeamsResponse:
{method_body}
"""

def build_teams_request_builder(path: str, path_params: List[Dict[str, str]]) -> str:
    """Build request builder chain for Teams operations."""
    builder = "self.client"
    
    # Split path and process each segment
    segments = [s for s in path.split('/') if s]
    i = 0
    
    while i < len(segments):
        segment = segments[i]
        
        if segment.startswith('{') and segment.endswith('}'):
            # Parameter segment - use by_id method
            param_name = segment[1:-1].replace('-', '_')
            prev_segment = segments[i-1] if i > 0 else ""
            
            if prev_segment == "teams":
                builder += f".by_team_id({param_name})"
            elif prev_segment == "chats":
                builder += f".by_chat_id({param_name})"
            elif prev_segment == "channels":
                builder += f".by_channel_id({param_name})"
            elif prev_segment == "messages":
                builder += f".by_message_id({param_name})"
            elif prev_segment == "members":
                builder += f".by_membership_id({param_name})"
            elif prev_segment == "installedApps":
                builder += f".by_installed_app_id({param_name})"
            elif prev_segment == "tabs":
                builder += f".by_tab_id({param_name})"
            elif prev_segment == "users":
                builder += f".by_user_id({param_name})"
            elif prev_segment == "groups":
                builder += f".by_group_id({param_name})"
            elif prev_segment == "replies":
                builder += f".by_reply_id({param_name})"
            else:
                # Generic fallback
                builder += f".by_{prev_segment[:-1] if prev_segment.endswith('s') else prev_segment}_id({param_name})"
        else:
            # Regular path segment
            if segment == "me":
                builder += ".me"
            elif segment == "users":
                # Handle /users/{user-id} pattern
                if i + 1 < len(segments) and segments[i + 1].startswith('{'):
                    i += 1  # Skip the parameter segment
                    param_segment = segments[i]
                    param_name = param_segment[1:-1].replace('-', '_')
                    builder += f".users.by_user_id({param_name})"
                else:
                    builder += ".users"
            elif segment == "groups":
                # Handle /groups/{group-id} pattern
                if i + 1 < len(segments) and segments[i + 1].startswith('{'):
                    i += 1  # Skip the parameter segment
                    param_segment = segments[i]
                    param_name = param_segment[1:-1].replace('-', '_')
                    builder += f".groups.by_group_id({param_name})"
                else:
                    builder += ".groups"
            elif segment == "teams":
                # Handle /teams/{team-id} pattern
                if i + 1 < len(segments) and segments[i + 1].startswith('{'):
                    i += 1  # Skip the parameter segment
                    param_segment = segments[i]
                    param_name = param_segment[1:-1].replace('-', '_')
                    builder += f".teams.by_team_id({param_name})"
                else:
                    builder += ".teams"
            elif segment == "chats":
                # Handle /chats/{chat-id} pattern
                if i + 1 < len(segments) and segments[i + 1].startswith('{'):
                    i += 1  # Skip the parameter segment
                    param_segment = segments[i]
                    param_name = param_segment[1:-1].replace('-', '_')
                    builder += f".chats.by_chat_id({param_name})"
                else:
                    builder += ".chats"
            else:
                # Convert to snake_case for SDK
                snake_segment = to_snake(segment)
                # Handle special cases
                if snake_segment == "joined_teams":
                    builder += ".joined_teams"
                elif snake_segment == "installed_apps":
                    builder += ".installed_apps"
                else:
                    builder += f".{snake_segment}"
        
        i += 1
    
    return builder

def build_teams_class_code(ops: Sequence[TeamsOperation]) -> str:
    """Generate the complete Teams client class."""
    # Group methods by operation type for better organization
    methods_by_type = {}
    for op in ops:
        if op.operation_type not in methods_by_type:
            methods_by_type[op.operation_type] = []
        methods_by_type[op.operation_type].append(op)
    
    class_name = "TeamsDataSource"
    response_class = "TeamsResponse"

    header = f"""

import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Mapping, Optional

from kiota_abstractions.base_request_configuration import (# type: ignore
    RequestConfiguration,
)
# Import MS Graph specific query parameter classes for Teams
from msgraph.generated.teams.teams_request_builder import TeamsRequestBuilder # type: ignore
from msgraph.generated.chats.chats_request_builder import ChatsRequestBuilder # type: ignore
from msgraph.generated.teams.item.channels.channels_request_builder import ChannelsRequestBuilder # type: ignore
from msgraph.generated.teams.item.channels.item.messages.messages_request_builder import MessagesRequestBuilder # type: ignore
from msgraph.generated.teams.item.members.members_request_builder import MembersRequestBuilder # type: ignore
from msgraph.generated.teamwork.teamwork_request_builder import TeamworkRequestBuilder # type: ignore
from kiota_abstractions.base_request_configuration import RequestConfiguration # type: ignore

from app.sources.client.microsoft.microsoft import MSGraphClient

# Teams-specific response wrapper
class TeamsResponse:
    \"\"\"Standardized Teams API response wrapper.\"\"\"
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
    Microsoft Teams API client with comprehensive endpoint coverage.
    
    Supports Teams operations including:
    - Team management: Create, update, delete, archive teams
    - Channel operations: Manage team channels and channel messages
    - Chat operations: Handle team chats and chat messages
    - Member management: Add, remove, update team and chat members
    - App operations: Manage team and chat apps
    - Message operations: Send, read, reply to messages
    - General operations: Team settings and configurations
    
    Generated methods: {len(ops)}
    
    Operation categories:"""

    # Add operation type summary
    for op_type, type_ops in methods_by_type.items():
        header += f"""
    - {op_type}: {len(type_ops)} methods"""

    header += f"""
    \"\"\"

    def __init__(self, client: MSGraphClient) -> None:
        \"\"\"Initialize with Microsoft Graph SDK client optimized for Teams.\"\"\"
        self.client = client.get_client().get_ms_graph_service_client()
        if not hasattr(self.client, "me"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("Teams client initialized with {len(ops)} methods")

    def _handle_teams_response(self, response: object) -> TeamsResponse:
        \"\"\"Handle Teams API response with comprehensive error handling.\"\"\"
        try:
            if response is None:
                return TeamsResponse(success=False, error="Empty response from Teams API")
            
            success = True
            error_msg = None
            
            # Enhanced error response handling for Teams operations
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
            
            return TeamsResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling Teams response: {{e}}")
            return TeamsResponse(success=False, error=str(e))

    def get_data_source(self) -> '{class_name}':
        \"\"\"Get the underlying Teams client.\"\"\"
        return self

"""

    # Add methods organized by operation type
    methods_section = ""
    
    # Define the order of operation types for better organization
    operation_order = ['personal', 'teams', 'channels', 'chats', 'messages', 'members', 'apps', 'teamwork', 'general']
    
    for op_type in operation_order:
        if op_type in methods_by_type:
            methods = methods_by_type[op_type]
            methods_section += f"    # ========== {op_type.upper()} OPERATIONS ({len(methods)} methods) ==========\n\n"
            
            for op in methods:
                method_code = build_teams_method_code(op)
                methods_section += method_code + "\n"
    
    return header + methods_section + "\n\n"

# ---- Public entrypoints ----------------------------------------------------
def generate_teams_client(
    *,
    out_path: Optional[str] = None,
    spec_url: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> str:
    """Generate comprehensive Teams client. Returns file path."""
    out_filename = out_path or "teams_client.py"
    
    print(f"Loading Microsoft Graph OpenAPI specification for Teams...")
    spec = load_spec(spec_url=spec_url, spec_path=spec_path)
    
    print(f"Extracting Teams operations with comprehensive coverage...")
    ops = extract_teams_operations(spec)
    
    print(f"Found {len(ops)} Teams operations with full parameter support")
    
    # Show breakdown by operation type
    ops_by_type = {}
    for op in ops:
        if op.operation_type not in ops_by_type:
            ops_by_type[op.operation_type] = 0
        ops_by_type[op.operation_type] += 1
    
    print("Operation breakdown:")
    for op_type, count in sorted(ops_by_type.items()):
        print(f"  - {op_type}: {count} methods")
    
    print("Generating comprehensive Teams async client code...")
    code = build_teams_class_code(ops)
    
    # Create microsoft directory (reuse the existing structure)
    script_dir = Path(__file__).parent if __file__ else Path('.')
    microsoft_dir = script_dir / 'microsoft'
    microsoft_dir.mkdir(exist_ok=True)
    
    # Write file
    full_path = microsoft_dir / out_filename
    full_path.write_text(code, encoding="utf-8")
    print(f"Saved comprehensive Teams client to: {full_path}")
    
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
        description="Generate comprehensive Teams API client"
    )
    ap.add_argument("--out", help="Output .py file path (default: teams_client.py)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--spec-url", default=None, help="Microsoft Graph OpenAPI spec URL")
    g.add_argument("--spec-path", help="Local spec file path (JSON/YAML)")
    ap.add_argument("--show-patterns", action="store_true", help="Show all Teams path patterns")
    
    return ap.parse_args(argv)

def _show_patterns() -> None:
    """Show all Teams path patterns that will be matched."""
    print("Teams API Path Patterns (Comprehensive Coverage):")
    print(f"Total patterns: {len(TEAMS_PATH_PATTERNS)}")
    print()
    
    # Group patterns by category
    pattern_groups = {
        "Personal Teams": [p for p in TEAMS_PATH_PATTERNS if p.startswith("/me/")],
        "Team Management": [p for p in TEAMS_PATH_PATTERNS if p.startswith("/teams/") and "/channels/" not in p and "/members" not in p],
        "Channel Operations": [p for p in TEAMS_PATH_PATTERNS if "/channels/" in p],
        "Chat Operations": [p for p in TEAMS_PATH_PATTERNS if p.startswith("/chats/")],
        "Team Members": [p for p in TEAMS_PATH_PATTERNS if "/members" in p],
        "Team Apps": [p for p in TEAMS_PATH_PATTERNS if "/installedApps" in p],
        "Teamwork": [p for p in TEAMS_PATH_PATTERNS if p.startswith("/teamwork")],
        "User Context": [p for p in TEAMS_PATH_PATTERNS if p.startswith("/users/")],
        "Group Context": [p for p in TEAMS_PATH_PATTERNS if p.startswith("/groups/")],
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
    
    print(f"Starting comprehensive Teams API client generation...")
    
    try:
        out_path = generate_teams_client(
            out_path=ns.out,
            spec_url=ns.spec_url,
            spec_path=ns.spec_path,
        )
        
        print(f"\nTeams comprehensive client generation completed!")
        print(f"Generated class: TeamsDataSource")
        print(f"Output file: {out_path}")
        print(f"Features:")
        print(f"  - Comprehensive Teams API endpoint coverage")
        print(f"  - Support for Teams, Channels, Chats, Members, Messages")
        print(f"  - Team and Chat App management")
        print(f"  - Complete OData query support optimized for Teams")
        print(f"  - Async methods with enhanced error handling")
        print(f"")
        print(f"🔧 TO MODIFY EXCLUSIONS:")
        print(f"  - Edit EXCLUDED_KEYWORDS list to add/remove excluded API keywords")
        print(f"  - Edit EXCLUDED_PATHS list to add/remove excluded path patterns")
        print(f"  - Current exclusions: OneDrive, Outlook, SharePoint, OneNote operations")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()