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
Microsoft Users Groups API Client Generator (Users, Groups, Invitations Focus)

Generates a focused Users Groups API client with comprehensive endpoint coverage for:
- Users (/users, /users/{user-id})
- User Profile (/users/{user-id}/photo, /users/{user-id}/manager, /users/{user-id}/directReports)
- Groups (/groups, /groups/{group-id})
- Group Membership (/groups/{group-id}/members, /groups/{group-id}/owners)
- Group Settings (/groups/{group-id}/settings, /groups/{group-id}/photo)
- Invitations (/invitations, /invitations/{invitation-id})
- Directory Objects (/directoryObjects, /directoryObjects/{directory-object-id})
- User Extensions and Properties
- Group Extensions and Properties
- Organization (/organization)
- Directory Roles (/directoryRoles, /directoryRoleTemplates)

Explicitly excludes:
- OneDrive operations (/users/{user-id}/drive, /groups/{group-id}/drive endpoints)
- Teams operations (/users/{user-id}/chats, /users/{user-id}/joinedTeams endpoints)
- SharePoint operations (/users/{user-id}/sites endpoints)
- OneNote operations (/users/{user-id}/onenote endpoints)
- Planner operations (/users/{user-id}/planner endpoints)
- Outlook operations (/users/{user-id}/messages, /users/{user-id}/events, /users/{user-id}/contacts endpoints)
- Calendar operations (/users/{user-id}/calendar, /users/{user-id}/calendars endpoints)
- Device operations (/devices endpoints)
- Security operations (/security endpoints)
- Compliance operations (/compliance endpoints)
- Admin operations (/admin endpoints)
- Application-specific endpoints for users and groups

EASY MODIFICATION:
To add/remove excluded APIs, modify the EXCLUDED_KEYWORDS and EXCLUDED_PATHS lists at the top of this file.

Enhanced Features:
- Complete Users Groups endpoint coverage with all path parameters
- Proper parameter extraction for user-id, group-id, invitation-id, directory-object-id
- Comprehensive method signatures with required and optional parameters
- Microsoft Graph SDK integration optimized for Users Groups operations
- Specialized Users Groups response handling and error management
- Support for user management, group management, and invitation workflows
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

# Keywords to exclude from Users Groups operations (ADD MORE HERE TO EXCLUDE)
EXCLUDED_KEYWORDS = [
    'drive',          # Exclude OneDrive operations for users/groups
    'drives',         # Exclude OneDrive operations for users/groups
    'chat',           # Exclude Teams chat operations
    'chats',          # Exclude Teams chat operations
    'team',           # Exclude Teams operations
    'teams',          # Exclude Teams operations
    'joinedteams',    # Exclude Teams operations
    'channel',        # Exclude Teams channel operations
    'channels',       # Exclude Teams channel operations
    'site',           # Exclude SharePoint site operations
    'sites',          # Exclude SharePoint site operations
    'onenote',        # Exclude OneNote operations
    'planner',        # Exclude Planner operations
    'message',        # Exclude Outlook message operations
    'messages',       # Exclude Outlook message operations
    'event',          # Exclude Outlook event operations
    'events',         # Exclude Outlook event operations
    'contact',        # Exclude Outlook contact operations
    'contacts',       # Exclude Outlook contact operations
    'calendar',       # Exclude Outlook calendar operations
    'calendars',      # Exclude Outlook calendar operations
    'mailfolder',     # Exclude Outlook mail folder operations
    'mailfolders',    # Exclude Outlook mail folder operations
    'deviceAppManagement',
    'deviceManagement',
    'device',         # Keep basic device info but exclude device management
    'security',       # Exclude Security operations
    'compliance',     # Exclude Compliance operations
    'admin',          # Exclude Admin operations
    'agreements',     # Exclude Agreements operations
    'analytics',      # Exclude Analytics operations
    'auditLogs',      # Exclude Audit Logs operations
    'storage',        # Exclude Storage operations
    'connections',    # Exclude Connections operations
    'workbook',       # Exclude Excel workbook operations
    'worksheet',      # Exclude Excel worksheet operations
    'todo',           # Exclude To-Do operations
    'notebook',       # Exclude notebook operations
    'communications',
    'education',
    'identity',
    'inferenceClassification',
    'extensions',
    'termStore',
    'servicePrincipals',
    'applications',   # Keep basic application info but exclude app management
    'directory',
    'licenseDetails',
    'settings',
    'outlook',
    'conversations',
    'authentication',
    'print',
    'photo',
    'solutions',
    'threads',
    'copilot',
    'reports',
    'employeeExperience',
    'privacy',
    'onlineMeetings',
    'insights',
    'manager',
    'oauth2PermissionGrants'
]

# Path patterns to exclude (ADD MORE PATTERNS HERE TO EXCLUDE)
EXCLUDED_PATHS = [
    '/users/{user-id}/drive',
    '/users/{user-id}/drives',
    '/groups/{group-id}/drive',
    '/groups/{group-id}/drives',
    '/users/{user-id}/chats',
    '/users/{user-id}/joinedTeams',
    '/users/{user-id}/teamwork',
    '/groups/{group-id}/team',
    '/users/{user-id}/sites',
    '/groups/{group-id}/sites',
    '/users/{user-id}/onenote',
    '/groups/{group-id}/onenote',
    '/users/{user-id}/planner',
    '/groups/{group-id}/planner',
    '/users/{user-id}/messages',
    '/users/{user-id}/mailFolders',
    '/users/{user-id}/events',
    '/users/{user-id}/calendar',
    '/users/{user-id}/calendars',
    '/users/{user-id}/contacts',
    '/users/{user-id}/contactFolders',
    '/groups/{group-id}/calendar',
    '/groups/{group-id}/events',
    '/deviceAppManagement',
    '/deviceManagement',
    '/devices',
    '/security',
    '/compliance',
    '/admin',
    '/agreements',
    '/analytics',
    '/auditLogs',
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
    '/termStore',
    '/servicePrincipals',
    '/applications',
    '/directory',
    '/licenseDetails',
    '/settings',
    '/outlook',
    '/conversations',
    '/authentication',
    '/print',
    '/photo',
    '/solutions',
    '/threads',
    '/copilot',
    '/reports',
    '/employeeExperience',
    '/privacy',
    '/onlineMeetings',
    '/insights',
    '/manager',
    '/oauth2PermissionGrants'
]

# =============================================================================

# Microsoft Graph OpenAPI specification URLs
DEFAULT_SPEC_URL = "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/master/openapi/v1.0/openapi.yaml"
FALLBACK_SPEC_URLS = [
    "https://raw.githubusercontent.com/microsoftgraph/msgraph-metadata/main/openapi/v1.0/openapi.yaml",
]

# Comprehensive Users Groups endpoint patterns (MODIFY THIS LIST TO ADD/REMOVE ENDPOINTS)
USERS_GROUPS_PATH_PATTERNS = [
    # Users - Core Operations
    "/users",
    "/users/{user-id}",
    "/users/{user-id}/photo",
    "/users/{user-id}/photo/$value",
    "/users/{user-id}/photos",
    "/users/{user-id}/photos/{profile-photo-id}",
    "/users/{user-id}/photos/{profile-photo-id}/$value",
    "/users/{user-id}/manager",
    "/users/{user-id}/directReports",
    "/users/{user-id}/memberOf",
    "/users/{user-id}/transitiveMemberOf",
    "/users/{user-id}/ownedObjects",
    "/users/{user-id}/ownedDevices",
    "/users/{user-id}/registeredDevices",
    "/users/{user-id}/createdObjects",
    "/users/{user-id}/licenseDetails",
    "/users/{user-id}/assignLicense",
    "/users/{user-id}/changePassword",
    "/users/{user-id}/revokeSignInSessions",
    "/users/{user-id}/reprocessLicenseAssignment",
    "/users/{user-id}/exportPersonalData",
    "/users/{user-id}/getMailTips",
    "/users/{user-id}/translateExchangeIds",
    "/users/{user-id}/findMeetingTimes",
    "/users/{user-id}/reminderView",
    "/users/{user-id}/getManagedAppDiagnosticStatuses",
    "/users/{user-id}/getManagedAppPolicies",
    "/users/{user-id}/wipeManagedAppRegistrationsByDeviceTag",
    "/users/delta",
    "/users/getByIds",
    "/users/validateProperties",
    
    # User Settings and Preferences
    "/users/{user-id}/settings",
    "/users/{user-id}/settings/shiftPreferences",
    "/users/{user-id}/settings/regionalAndLanguageSettings",
    "/users/{user-id}/settings/contactMergeSuggestions",
    "/users/{user-id}/settings/contributionToContentDiscoveryAsOrganizationDisabled",
    "/users/{user-id}/settings/contributionToContentDiscoveryDisabled",
    "/users/{user-id}/settings/itemInsights",
    "/users/{user-id}/mailboxSettings",
    "/users/{user-id}/mailboxSettings/workingHours",
    "/users/{user-id}/mailboxSettings/userPurpose",
    
    # User Authentication
    "/users/{user-id}/authentication",
    "/users/{user-id}/authentication/methods",
    "/users/{user-id}/authentication/methods/{authentication-method-id}",
    "/users/{user-id}/authentication/fido2Methods",
    "/users/{user-id}/authentication/fido2Methods/{fido2-authentication-method-id}",
    "/users/{user-id}/authentication/microsoftAuthenticatorMethods",
    "/users/{user-id}/authentication/microsoftAuthenticatorMethods/{microsoft-authenticator-authentication-method-id}",
    "/users/{user-id}/authentication/passwordMethods",
    "/users/{user-id}/authentication/passwordMethods/{password-authentication-method-id}",
    "/users/{user-id}/authentication/phoneMethods",
    "/users/{user-id}/authentication/phoneMethods/{phone-authentication-method-id}",
    "/users/{user-id}/authentication/softwareOathMethods",
    "/users/{user-id}/authentication/softwareOathMethods/{software-oath-authentication-method-id}",
    "/users/{user-id}/authentication/temporaryAccessPassMethods",
    "/users/{user-id}/authentication/temporaryAccessPassMethods/{temporary-access-pass-authentication-method-id}",
    "/users/{user-id}/authentication/windowsHelloForBusinessMethods",
    "/users/{user-id}/authentication/windowsHelloForBusinessMethods/{windows-hello-for-business-authentication-method-id}",
    
    # Groups - Core Operations
    "/groups",
    "/groups/{group-id}",
    "/groups/{group-id}/photo",
    "/groups/{group-id}/photo/$value",
    "/groups/{group-id}/photos",
    "/groups/{group-id}/photos/{profile-photo-id}",
    "/groups/{group-id}/photos/{profile-photo-id}/$value",
    "/groups/{group-id}/members",
    "/groups/{group-id}/members/{directory-object-id}",
    "/groups/{group-id}/members/$ref",
    "/groups/{group-id}/owners",
    "/groups/{group-id}/owners/{directory-object-id}",
    "/groups/{group-id}/owners/$ref",
    "/groups/{group-id}/memberOf",
    "/groups/{group-id}/transitiveMemberOf",
    "/groups/{group-id}/transitiveMembers",
    "/groups/{group-id}/createdOnBehalfOf",
    "/groups/{group-id}/extensions",
    "/groups/{group-id}/extensions/{extension-id}",
    "/groups/{group-id}/groupLifecyclePolicies",
    "/groups/{group-id}/checkMemberGroups",
    "/groups/{group-id}/checkMemberObjects",
    "/groups/{group-id}/getMemberGroups",
    "/groups/{group-id}/getMemberObjects",
    "/groups/{group-id}/restore",
    "/groups/{group-id}/renew",
    "/groups/{group-id}/addFavorite",
    "/groups/{group-id}/removeFavorite",
    "/groups/{group-id}/resetUnseenCount",
    "/groups/{group-id}/subscribeByMail",
    "/groups/{group-id}/unsubscribeByMail",
    "/groups/{group-id}/validateProperties",
    "/groups/delta",
    "/groups/getByIds",
    "/groups/validateProperties",
    "/groups/getAvailableExtensionProperties",
    
    # Group Settings
    "/groups/{group-id}/settings",
    "/groups/{group-id}/settings/{group-setting-id}",
    "/groupSettings",
    "/groupSettings/{group-setting-id}",
    "/groupSettingTemplates",
    "/groupSettingTemplates/{group-setting-template-id}",
    
    # Invitations
    "/invitations",
    "/invitations/{invitation-id}",
    "/invitations/{invitation-id}/invitedUser",
    
    # Directory Objects
    "/directoryObjects",
    "/directoryObjects/{directory-object-id}",
    "/directoryObjects/{directory-object-id}/checkMemberGroups",
    "/directoryObjects/{directory-object-id}/checkMemberObjects",
    "/directoryObjects/{directory-object-id}/getMemberGroups",
    "/directoryObjects/{directory-object-id}/getMemberObjects",
    "/directoryObjects/{directory-object-id}/restore",
    "/directoryObjects/getByIds",
    "/directoryObjects/validateProperties",
    "/directoryObjects/getAvailableExtensionProperties",
    
    # Organization
    "/organization",
    "/organization/{organization-id}",
    "/organization/{organization-id}/extensions",
    "/organization/{organization-id}/extensions/{extension-id}",
    "/organization/{organization-id}/branding",
    "/organization/{organization-id}/branding/localizations",
    "/organization/{organization-id}/branding/localizations/{organizational-branding-localization-id}",
    "/organization/{organization-id}/certificateBasedAuthConfiguration",
    "/organization/{organization-id}/certificateBasedAuthConfiguration/{certificate-based-auth-configuration-id}",
    
    # Directory Roles
    "/directoryRoles",
    "/directoryRoles/{directory-role-id}",
    "/directoryRoles/{directory-role-id}/members",
    "/directoryRoles/{directory-role-id}/members/{directory-object-id}",
    "/directoryRoles/{directory-role-id}/members/$ref",
    "/directoryRoles/{directory-role-id}/scopedMembers",
    "/directoryRoles/{directory-role-id}/scopedMembers/{scoped-role-membership-id}",
    "/directoryRoleTemplates",
    "/directoryRoleTemplates/{directory-role-template-id}",
    
    # Administrative Units
    "/administrativeUnits",
    "/administrativeUnits/{administrative-unit-id}",
    "/administrativeUnits/{administrative-unit-id}/members",
    "/administrativeUnits/{administrative-unit-id}/members/{directory-object-id}",
    "/administrativeUnits/{administrative-unit-id}/members/$ref",
    "/administrativeUnits/{administrative-unit-id}/scopedRoleMembers",
    "/administrativeUnits/{administrative-unit-id}/scopedRoleMembers/{scoped-role-membership-id}",
    "/administrativeUnits/{administrative-unit-id}/extensions",
    "/administrativeUnits/{administrative-unit-id}/extensions/{extension-id}",
    
    # Domains
    "/domains",
    "/domains/{domain-id}",
    "/domains/{domain-id}/domainNameReferences",
    "/domains/{domain-id}/serviceConfigurationRecords",
    "/domains/{domain-id}/serviceConfigurationRecords/{domain-dns-record-id}",
    "/domains/{domain-id}/verificationDnsRecords",
    "/domains/{domain-id}/verificationDnsRecords/{domain-dns-record-id}",
    "/domains/{domain-id}/promote",
    "/domains/{domain-id}/verify",
    
    # Subscriptions
    "/subscriptions",
    "/subscriptions/{subscription-id}",
    "/subscriptions/{subscription-id}/reauthorize",
    
    # Directory Extensions
    "/schemaExtensions",
    "/schemaExtensions/{schema-extension-id}",
    
    # NOTE: Excluded patterns (OneDrive, Teams, SharePoint, Outlook) are filtered out by EXCLUDED_KEYWORDS and EXCLUDED_PATHS
    # To re-enable them, remove the keywords from the exclusion lists above
]

# ---- Operation Model -------------------------------------------------------
@dataclass
class UsersGroupsOperation:
    op_id: str
    http_method: str
    path: str
    summary: str
    description: str
    params: List[Mapping[str, Any]]
    request_body: Optional[Mapping[str, Any]] = None
    path_params: List[Dict[str, str]] = None
    operation_type: str = "general"  # users, groups, invitations, directory, organization, roles, domains

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
        """Determine the type of Users Groups operation for better organization."""
        path_lower = self.path.lower()
        op_lower = self.op_id.lower()
        
        if any(keyword in path_lower for keyword in ["users", "user", "manager", "directreports", "memberof"]):
            return "users"
        elif any(keyword in path_lower for keyword in ["groups", "group", "members", "owners"]):
            return "groups"
        elif any(keyword in path_lower for keyword in ["invitations", "invitation", "inviteduser"]):
            return "invitations"
        elif any(keyword in path_lower for keyword in ["directoryobjects", "directoryobject"]):
            return "directory"
        elif any(keyword in path_lower for keyword in ["organization", "branding"]):
            return "organization"
        elif any(keyword in path_lower for keyword in ["directoryroles", "directoryrole", "roletemplate", "administrativeunits"]):
            return "roles"
        elif any(keyword in path_lower for keyword in ["domains", "domain"]):
            return "domains"
        elif any(keyword in path_lower for keyword in ["subscriptions", "subscription"]):
            return "subscriptions"
        elif any(keyword in path_lower for keyword in ["schemaextensions", "schemaextension"]):
            return "extensions"
        elif any(keyword in path_lower for keyword in ["authentication", "methods", "fido2", "authenticator"]):
            return "authentication"
        elif any(keyword in path_lower for keyword in ["settings", "preferences", "mailboxsettings"]):
            return "settings"
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
    """Extract path parameters from Users Groups path pattern."""
    import re
    
    # Find all {parameter} patterns
    pattern = r'\{([^}]+)\}'
    matches = re.findall(pattern, path)
    
    path_params = []
    for match in matches:
        # Clean up parameter name and convert to Python-friendly format
        param_name = match.replace('-', '_').replace('.', '_')
        
        # Determine parameter type based on common Users Groups patterns
        param_type = 'str'
        description = f'Users Groups path parameter: {match}'
        
        if 'id' in match.lower():
            description = f'Users Groups {match.replace("-", " ").replace("_", " ")} identifier'
        elif 'user' in match.lower():
            description = f'User identifier: {match}'
        elif 'group' in match.lower():
            description = f'Group identifier: {match}'
        elif 'invitation' in match.lower():
            description = f'Invitation identifier: {match}'
        elif 'directory' in match.lower():
            description = f'Directory object identifier: {match}'
        elif 'organization' in match.lower():
            description = f'Organization identifier: {match}'
        elif 'role' in match.lower():
            description = f'Role identifier: {match}'
        elif 'domain' in match.lower():
            description = f'Domain identifier: {match}'
        elif 'extension' in match.lower():
            description = f'Extension identifier: {match}'
        
        path_params.append({
            'name': param_name,
            'original': match,
            'type': param_type,
            'required': True,
            'description': description
        })
    
    return path_params

def _is_users_groups_operation(path: str, op_id: str) -> bool:
    """Check if this operation is related to Users Groups (Users, Groups, Invitations).
    
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
    
    # Include operations that are clearly Users Groups related (CORE USERS GROUPS KEYWORDS)
    users_groups_core_keywords = [
        'users', 'user', 'groups', 'group', 'invitations', 'invitation',
        'directoryobjects', 'directoryobject', 'organization', 'directoryroles',
        'directoryrole', 'directoryroletemplates', 'administrativeunits',
        'domains', 'domain', 'subscriptions', 'subscription', 'schemaextensions',
        'schemaextension', 'authentication', 'manager', 'directreports',
        'memberof', 'members', 'owners', 'photo', 'settings', 'mailboxsettings',
        'licensedetails', 'assignlicense', 'changepassword', 'revokesigninsessions',
        'branding', 'extensions', 'groupsettings', 'groupsettingtemplates'
    ]
    
    return any(keyword in path_lower or keyword in op_lower for keyword in users_groups_core_keywords)

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

def extract_users_groups_operations(spec: Mapping[str, Any]) -> List[UsersGroupsOperation]:
    """Extract Users Groups-specific operations from OpenAPI spec."""
    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("Invalid OpenAPI spec: missing 'paths'")

    ops: List[UsersGroupsOperation] = []
    skipped_count_ops = 0
    skipped_non_users_groups = 0
    
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
            
        # Check if path matches any Users Groups patterns
        if not any(_path_matches_pattern(path, pattern) for pattern in USERS_GROUPS_PATH_PATTERNS):
            # Additional check for Users Groups-related operations
            if not _is_users_groups_operation(path, ""):
                skipped_non_users_groups += 1
                continue
        
        # ============ EXCLUSION CHECK (MODIFY EXCLUDED_PATHS LIST ABOVE) ============
        # Explicitly exclude paths based on exclusion list
        should_exclude = False
        for excluded_path in EXCLUDED_PATHS:
            if excluded_path.lower() in path.lower():
                should_exclude = True
                break
        
        if should_exclude:
            skipped_non_users_groups += 1
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
            
            # Skip non-Users Groups operations
            if not _is_users_groups_operation(path, str(op_id)):
                skipped_non_users_groups += 1
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
                skipped_non_users_groups += 1
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
                UsersGroupsOperation(
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
    uniq: Dict[str, UsersGroupsOperation] = {}
    for op in ops:
        key = f"{op.op_id}_{op.http_method}_{op.path}"
        if key not in uniq:
            uniq[key] = op
    
    print(f"Users Groups filtering summary (MODIFY EXCLUDED_KEYWORDS/EXCLUDED_PATHS to change):")
    print(f"  - Skipped {skipped_count_ops} count operations")
    print(f"  - Skipped {skipped_non_users_groups} excluded operations (OneDrive, Teams, SharePoint, Outlook, etc.)")
    print(f"  - Found {len(uniq)} Users Groups operations (Users, Groups, Invitations)")
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

def extract_operation_parameters(op: UsersGroupsOperation) -> Tuple[List[str], List[str], List[str]]:
    """Extract and categorize parameters from Users Groups operation."""
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
    
    # Add Users Groups-specific OData parameters
    users_groups_odata_params = [
        "select: Optional[List[str]] = None",
        "expand: Optional[List[str]] = None", 
        "filter: Optional[str] = None",
        "orderby: Optional[str] = None",
        "search: Optional[str] = None",
        "top: Optional[int] = None",
        "skip: Optional[int] = None"
    ]
    optional_params.extend(users_groups_odata_params)
    
    # Add Users Groups OData parameter docs
    users_groups_odata_docs = [
        "            select (optional): Select specific properties to return",
        "            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)",
        "            filter (optional): Filter the results using OData syntax", 
        "            orderby (optional): Order the results by specified properties",
        "            search (optional): Search for users, groups, or directory objects by content",
        "            top (optional): Limit number of results returned",
        "            skip (optional): Skip number of results for pagination"
    ]
    param_docs.extend(users_groups_odata_docs)
    
    # Add request body if needed (for Users Groups operations)
    has_body = op.request_body is not None
    if has_body:
        optional_params.append("request_body: Optional[Mapping[str, Any]] = None")
        param_docs.append("            request_body (optional): Request body data for Users Groups operations")
    
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
    """Get the appropriate query parameter classes for the Users Groups endpoint."""
    path_lower = path.lower()
    method_lower = method.lower()
    
    # Map paths to their corresponding query parameter classes
    if '/users' in path_lower and method_lower == 'get':
        return "UsersRequestBuilder.UsersRequestBuilderGetQueryParameters", "UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration"
    elif '/groups' in path_lower and method_lower == 'get':
        return "GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters", "GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration"
    elif '/invitations' in path_lower and method_lower == 'get':
        return "InvitationsRequestBuilder.InvitationsRequestBuilderGetQueryParameters", "InvitationsRequestBuilder.InvitationsRequestBuilderGetRequestConfiguration"
    elif '/directoryobjects' in path_lower and method_lower == 'get':
        return "DirectoryObjectsRequestBuilder.DirectoryObjectsRequestBuilderGetQueryParameters", "DirectoryObjectsRequestBuilder.DirectoryObjectsRequestBuilderGetRequestConfiguration"
    elif '/directoryroles' in path_lower and method_lower == 'get':
        return "DirectoryRolesRequestBuilder.DirectoryRolesRequestBuilderGetQueryParameters", "DirectoryRolesRequestBuilder.DirectoryRolesRequestBuilderGetRequestConfiguration"
    elif '/organization' in path_lower and method_lower == 'get':
        return "OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters", "OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration"
    else:
        # Fallback to generic RequestConfiguration for unmatched endpoints
        return "RequestConfiguration", "RequestConfiguration"

def build_users_groups_method_code(op: UsersGroupsOperation) -> str:
    """Generate comprehensive async method code for Users Groups operation."""
    required_params, optional_params, param_docs = extract_operation_parameters(op)

    # Build method signature
    all_params = required_params + optional_params
    
    if len(all_params) <= 5:
        sig = ", ".join(all_params)
    else:
        params_formatted = ",\n        ".join(all_params)
        sig = f"\n        {params_formatted}\n    "

    summary = op.summary or op.op_id
    params_doc = "\n".join(param_docs) if param_docs else "            (standard Users Groups parameters)"

    # Generate Users Groups-specific Graph SDK method call
    graph_method_call = _generate_users_groups_graph_call(op)
    # Determine query parameter classes during generation
    query_param_class, config_class = _get_query_param_classes(op.path, op.http_method)
    response_type = "UsersGroupsResponse"

    method_code = f"""    async def {op.wrapper_name}({sig}) -> {response_type}:
        \"\"\"{summary}.
        Users Groups operation: {op.http_method} {op.path}
        Operation type: {op.operation_type}
        Args:
{params_doc}
        Returns:
            {response_type}: Users Groups response wrapper with success/data/error
        \"\"\"
        # Build query parameters including OData for Users Groups
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

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {{}}
                config.headers['ConsistencyLevel'] = 'eventual'

{graph_method_call}
            return self._handle_users_groups_response(response)
        except Exception as e:
            return {response_type}(
                success=False,
                error=f"Users Groups API call failed: {{str(e)}}",
            )
"""

    return method_code

def _generate_users_groups_graph_call(op: UsersGroupsOperation) -> str:
    """Generate Users Groups-specific Microsoft Graph SDK method call."""
    path = op.path
    method = op.http_method.lower()
    has_body = op.request_body is not None
    
    # Build request builder chain optimized for Users Groups
    request_builder = _build_users_groups_request_builder_chain(path, op.path_params)
    
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

def _build_users_groups_request_builder_chain(path: str, path_params: List[Dict[str, str]]) -> str:
    """Build Users Groups-optimized Microsoft Graph SDK request builder chain."""
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
            elif prev_segment == "invitations":
                builder += f".by_invitation_id({python_param})"
            elif prev_segment == "directoryObjects":
                builder += f".by_directory_object_id({python_param})"
            elif prev_segment == "organization":
                builder += f".by_organization_id({python_param})"
            elif prev_segment == "directoryRoles":
                builder += f".by_directory_role_id({python_param})"
            elif prev_segment == "directoryRoleTemplates":
                builder += f".by_directory_role_template_id({python_param})"
            elif prev_segment == "administrativeUnits":
                builder += f".by_administrative_unit_id({python_param})"
            elif prev_segment == "domains":
                builder += f".by_domain_id({python_param})"
            elif prev_segment == "subscriptions":
                builder += f".by_subscription_id({python_param})"
            elif prev_segment == "schemaExtensions":
                builder += f".by_schema_extension_id({python_param})"
            elif prev_segment == "extensions":
                builder += f".by_extension_id({python_param})"
            elif prev_segment == "photos":
                builder += f".by_profile_photo_id({python_param})"
            elif prev_segment == "members":
                builder += f".by_directory_object_id({python_param})"
            elif prev_segment == "owners":
                builder += f".by_directory_object_id({python_param})"
            elif prev_segment == "groupSettings":
                builder += f".by_group_setting_id({python_param})"
            elif prev_segment == "groupSettingTemplates":
                builder += f".by_group_setting_template_id({python_param})"
            elif prev_segment == "scopedMembers":
                builder += f".by_scoped_role_membership_id({python_param})"
            elif prev_segment == "settings":
                builder += f".by_group_setting_id({python_param})"
            elif prev_segment == "methods":
                builder += f".by_authentication_method_id({python_param})"
            elif prev_segment == "fido2Methods":
                builder += f".by_fido2_authentication_method_id({python_param})"
            elif prev_segment == "microsoftAuthenticatorMethods":
                builder += f".by_microsoft_authenticator_authentication_method_id({python_param})"
            elif prev_segment == "passwordMethods":
                builder += f".by_password_authentication_method_id({python_param})"
            elif prev_segment == "phoneMethods":
                builder += f".by_phone_authentication_method_id({python_param})"
            elif prev_segment == "softwareOathMethods":
                builder += f".by_software_oath_authentication_method_id({python_param})"
            elif prev_segment == "temporaryAccessPassMethods":
                builder += f".by_temporary_access_pass_authentication_method_id({python_param})"
            elif prev_segment == "windowsHelloForBusinessMethods":
                builder += f".by_windows_hello_for_business_authentication_method_id({python_param})"
            elif prev_segment == "localizations":
                builder += f".by_organizational_branding_localization_id({python_param})"
            elif prev_segment == "certificateBasedAuthConfiguration":
                builder += f".by_certificate_based_auth_configuration_id({python_param})"
            elif prev_segment == "serviceConfigurationRecords":
                builder += f".by_domain_dns_record_id({python_param})"
            elif prev_segment == "verificationDnsRecords":
                builder += f".by_domain_dns_record_id({python_param})"
            else:
                # Generic fallback for other Users Groups parameters
                builder += f".by_{prev_segment[:-1] if prev_segment.endswith('s') else prev_segment}_id({python_param})"
                
        elif segment == "$ref":
            # Handle special $ref endpoints for relationships
            builder += ".ref"
        elif segment == "$value":
            # Handle special $value endpoints (like photo/$value)
            builder += ".value"
        else:
            # Regular path segment - convert to appropriate Users Groups SDK method
            if segment == "users":
                builder += ".users"
            elif segment == "groups":
                builder += ".groups"
            elif segment == "invitations":
                builder += ".invitations"
            elif segment == "directoryObjects":
                builder += ".directory_objects"
            elif segment == "organization":
                builder += ".organization"
            elif segment == "directoryRoles":
                builder += ".directory_roles"
            elif segment == "directoryRoleTemplates":
                builder += ".directory_role_templates"
            elif segment == "administrativeUnits":
                builder += ".administrative_units"
            elif segment == "domains":
                builder += ".domains"
            elif segment == "subscriptions":
                builder += ".subscriptions"
            elif segment == "schemaExtensions":
                builder += ".schema_extensions"
            elif segment == "groupSettings":
                builder += ".group_settings"
            elif segment == "groupSettingTemplates":
                builder += ".group_setting_templates"
            elif segment == "photo":
                builder += ".photo"
            elif segment == "photos":
                builder += ".photos"
            elif segment == "manager":
                builder += ".manager"
            elif segment == "directReports":
                builder += ".direct_reports"
            elif segment == "memberOf":
                builder += ".member_of"
            elif segment == "transitiveMemberOf":
                builder += ".transitive_member_of"
            elif segment == "transitiveMembers":
                builder += ".transitive_members"
            elif segment == "ownedObjects":
                builder += ".owned_objects"
            elif segment == "ownedDevices":
                builder += ".owned_devices"
            elif segment == "registeredDevices":
                builder += ".registered_devices"
            elif segment == "createdObjects":
                builder += ".created_objects"
            elif segment == "licenseDetails":
                builder += ".license_details"
            elif segment == "members":
                builder += ".members"
            elif segment == "owners":
                builder += ".owners"
            elif segment == "createdOnBehalfOf":
                builder += ".created_on_behalf_of"
            elif segment == "extensions":
                builder += ".extensions"
            elif segment == "groupLifecyclePolicies":
                builder += ".group_lifecycle_policies"
            elif segment == "settings":
                builder += ".settings"
            elif segment == "shiftPreferences":
                builder += ".shift_preferences"
            elif segment == "regionalAndLanguageSettings":
                builder += ".regional_and_language_settings"
            elif segment == "contactMergeSuggestions":
                builder += ".contact_merge_suggestions"
            elif segment == "contributionToContentDiscoveryAsOrganizationDisabled":
                builder += ".contribution_to_content_discovery_as_organization_disabled"
            elif segment == "contributionToContentDiscoveryDisabled":
                builder += ".contribution_to_content_discovery_disabled"
            elif segment == "itemInsights":
                builder += ".item_insights"
            elif segment == "mailboxSettings":
                builder += ".mailbox_settings"
            elif segment == "workingHours":
                builder += ".working_hours"
            elif segment == "userPurpose":
                builder += ".user_purpose"
            elif segment == "authentication":
                builder += ".authentication"
            elif segment == "methods":
                builder += ".methods"
            elif segment == "fido2Methods":
                builder += ".fido2_methods"
            elif segment == "microsoftAuthenticatorMethods":
                builder += ".microsoft_authenticator_methods"
            elif segment == "passwordMethods":
                builder += ".password_methods"
            elif segment == "phoneMethods":
                builder += ".phone_methods"
            elif segment == "softwareOathMethods":
                builder += ".software_oath_methods"
            elif segment == "temporaryAccessPassMethods":
                builder += ".temporary_access_pass_methods"
            elif segment == "windowsHelloForBusinessMethods":
                builder += ".windows_hello_for_business_methods"
            elif segment == "invitedUser":
                builder += ".invited_user"
            elif segment == "scopedMembers":
                builder += ".scoped_members"
            elif segment == "scopedRoleMembers":
                builder += ".scoped_role_members"
            elif segment == "branding":
                builder += ".branding"
            elif segment == "localizations":
                builder += ".localizations"
            elif segment == "certificateBasedAuthConfiguration":
                builder += ".certificate_based_auth_configuration"
            elif segment == "domainNameReferences":
                builder += ".domain_name_references"
            elif segment == "serviceConfigurationRecords":
                builder += ".service_configuration_records"
            elif segment == "verificationDnsRecords":
                builder += ".verification_dns_records"
            elif segment == "assignLicense":
                builder += ".assign_license"
            elif segment == "changePassword":
                builder += ".change_password"
            elif segment == "revokeSignInSessions":
                builder += ".revoke_sign_in_sessions"
            elif segment == "reprocessLicenseAssignment":
                builder += ".reprocess_license_assignment"
            elif segment == "exportPersonalData":
                builder += ".export_personal_data"
            elif segment == "getMailTips":
                builder += ".get_mail_tips"
            elif segment == "translateExchangeIds":
                builder += ".translate_exchange_ids"
            elif segment == "findMeetingTimes":
                builder += ".find_meeting_times"
            elif segment == "reminderView":
                builder += ".reminder_view"
            elif segment == "getManagedAppDiagnosticStatuses":
                builder += ".get_managed_app_diagnostic_statuses"
            elif segment == "getManagedAppPolicies":
                builder += ".get_managed_app_policies"
            elif segment == "wipeManagedAppRegistrationsByDeviceTag":
                builder += ".wipe_managed_app_registrations_by_device_tag"
            elif segment == "checkMemberGroups":
                builder += ".check_member_groups"
            elif segment == "checkMemberObjects":
                builder += ".check_member_objects"
            elif segment == "getMemberGroups":
                builder += ".get_member_groups"
            elif segment == "getMemberObjects":
                builder += ".get_member_objects"
            elif segment == "restore":
                builder += ".restore"
            elif segment == "renew":
                builder += ".renew"
            elif segment == "addFavorite":
                builder += ".add_favorite"
            elif segment == "removeFavorite":
                builder += ".remove_favorite"
            elif segment == "resetUnseenCount":
                builder += ".reset_unseen_count"
            elif segment == "subscribeByMail":
                builder += ".subscribe_by_mail"
            elif segment == "unsubscribeByMail":
                builder += ".unsubscribe_by_mail"
            elif segment == "validateProperties":
                builder += ".validate_properties"
            elif segment == "delta":
                builder += ".delta"
            elif segment == "getByIds":
                builder += ".get_by_ids"
            elif segment == "getAvailableExtensionProperties":
                builder += ".get_available_extension_properties"
            elif segment == "promote":
                builder += ".promote"
            elif segment == "verify":
                builder += ".verify"
            elif segment == "reauthorize":
                builder += ".reauthorize"
            else:
                # Convert to snake_case for other segments
                snake_segment = to_snake(segment)
                builder += f".{snake_segment}"
        
        i += 1
    
    return builder

def build_users_groups_class_code(ops: Sequence[UsersGroupsOperation]) -> str:
    """Generate the complete Users Groups client class."""
    # Group methods by operation type for better organization
    methods_by_type = {}
    for op in ops:
        if op.operation_type not in methods_by_type:
            methods_by_type[op.operation_type] = []
        methods_by_type[op.operation_type].append(op)
    
    class_name = "UsersGroupsDataSource"
    response_class = "UsersGroupsResponse"

    header = f"""

import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Mapping, Optional

# Import MS Graph specific query parameter classes for Users Groups
from msgraph.generated.users.users_request_builder import UsersRequestBuilder # type: ignore
from msgraph.generated.groups.groups_request_builder import GroupsRequestBuilder # type: ignore
from msgraph.generated.invitations.invitations_request_builder import InvitationsRequestBuilder # type: ignore
from msgraph.generated.directory_objects.directory_objects_request_builder import DirectoryObjectsRequestBuilder # type: ignore
from msgraph.generated.directory_roles.directory_roles_request_builder import DirectoryRolesRequestBuilder # type: ignore
from msgraph.generated.organization.organization_request_builder import OrganizationRequestBuilder # type: ignore
from kiota_abstractions.base_request_configuration import RequestConfiguration # type: ignore

from app.sources.client.microsoft.microsoft import MSGraphClient

# Users Groups-specific response wrapper
class UsersGroupsResponse:
    \"\"\"Standardized Users Groups API response wrapper.\"\"\"
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
    Comprehensive Microsoft Users Groups API client with complete Users, Groups, and Invitations coverage.

    Features:
    - Complete Users Groups API coverage with {len(ops)} methods organized by operation type
    - Support for Users, Groups, Invitations, and Directory Objects
    - Complete User operations: CRUD, profile management, authentication, settings
    - Complete Group operations: CRUD, membership management, settings, lifecycle
    - Complete Invitation operations: invite users, manage invitations
    - Directory operations: directory objects, roles, administrative units
    - Organization operations: organization settings, branding, configuration
    - Domain operations: domain management, verification, DNS records
    - Schema Extensions: custom properties and data extensions
    - Authentication operations: multi-factor authentication, methods management
    - License operations: license assignment, management, details
    - Photo operations: user and group photos, profile pictures
    - Microsoft Graph SDK integration with Users Groups-specific optimizations
    - Async snake_case method names for all operations
    - Standardized {response_class} format for all responses
    - Comprehensive error handling and Users Groups-specific response processing

    EXCLUDED OPERATIONS (modify EXCLUDED_KEYWORDS list to change):
    - OneDrive operations (/users/{{user-id}}/drive, /groups/{{group-id}}/drive)
    - Teams operations (/users/{{user-id}}/chats, /users/{{user-id}}/joinedTeams)
    - SharePoint operations (/users/{{user-id}}/sites, /groups/{{group-id}}/sites)
    - OneNote operations (/users/{{user-id}}/onenote, /groups/{{group-id}}/onenote)
    - Planner operations (/users/{{user-id}}/planner, /groups/{{group-id}}/planner)
    - Outlook operations (/users/{{user-id}}/messages, /users/{{user-id}}/events)
    - Calendar operations (/users/{{user-id}}/calendar, /users/{{user-id}}/calendars)
    - Contact operations (/users/{{user-id}}/contacts, /users/{{user-id}}/contactFolders)
    - Device management operations (deviceAppManagement, deviceManagement)
    - Security operations (security, compliance, admin)
    - Analytics operations (analytics, auditLogs)
    - Storage operations (storage, connections)
    - Communications operations (communications, education, identity)

    Operation Types:
    - Users operations: User CRUD, profile, authentication, settings, licenses
    - Groups operations: Group CRUD, membership, settings, lifecycle policies
    - Invitations operations: User invitations and invitation management
    - Directory operations: Directory objects, roles, administrative units
    - Organization operations: Organization settings, branding, configuration
    - Domains operations: Domain management, verification, DNS configuration
    - Roles operations: Directory roles, role templates, scoped memberships
    - Extensions operations: Schema extensions and custom properties
    - Authentication operations: Authentication methods and MFA management
    - Settings operations: User settings, preferences, mailbox settings
    - Subscriptions operations: Subscription management and webhooks
    - General operations: Base Users Groups functionality
    \"\"\"

    def __init__(self, client: MSGraphClient) -> None:
        \"\"\"Initialize with Microsoft Graph SDK client optimized for Users Groups.\"\"\"
        self.client = client.get_client().get_ms_graph_service_client()
        if not hasattr(self.client, "users"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("Users Groups client initialized with {len(ops)} methods")

    def _handle_users_groups_response(self, response: object) -> UsersGroupsResponse:
        \"\"\"Handle Users Groups API response with comprehensive error handling.\"\"\"
        try:
            if response is None:
                return UsersGroupsResponse(success=False, error="Empty response from Users Groups API")
            
            success = True
            error_msg = None
            
            # Enhanced error response handling for Users Groups operations
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

            return UsersGroupsResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling Users Groups response: {{e}}")
            return UsersGroupsResponse(success=False, error=str(e))

    def get_data_source(self) -> '{class_name}':
        \"\"\"Get the underlying Users Groups client.\"\"\"
        return self

"""

    # Add methods organized by operation type
    methods_section = ""
    
    # Define the order of operation types for better organization
    operation_order = ['users', 'groups', 'invitations', 'directory', 'organization', 'roles', 'domains', 'extensions', 'authentication', 'settings', 'subscriptions', 'general']
    
    for op_type in operation_order:
        if op_type in methods_by_type:
            methods = methods_by_type[op_type]
            methods_section += f"    # ========== {op_type.upper()} OPERATIONS ({len(methods)} methods) ==========\n\n"
            
            for op in methods:
                try:
                    method_code = build_users_groups_method_code(op)
                    methods_section += method_code + "\n"
                except Exception as e:
                    logger.warning(f"Error generating method code for {op.op_id}: {e}")
                    # Skip this method and continue
                    continue
    
    return header + methods_section + "\n\n"

# ---- Public entrypoints ----------------------------------------------------
def generate_users_groups_client(
    *,
    out_path: Optional[str] = None,
    spec_url: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> str:
    """Generate comprehensive Users Groups client. Returns file path."""
    out_filename = out_path or "users_groups_client.py"
    
    print(f"Loading Microsoft Graph OpenAPI specification for Users Groups...")
    spec = load_spec(spec_url=spec_url, spec_path=spec_path)
    
    print(f"Extracting Users Groups operations with comprehensive coverage...")
    ops = extract_users_groups_operations(spec)
    
    print(f"Found {len(ops)} Users Groups operations with full parameter support")
    
    # Show breakdown by operation type
    ops_by_type = {}
    for op in ops:
        if op.operation_type not in ops_by_type:
            ops_by_type[op.operation_type] = 0
        ops_by_type[op.operation_type] += 1
    
    print("Operation breakdown:")
    for op_type, count in sorted(ops_by_type.items()):
        print(f"  - {op_type}: {count} methods")
    
    print("Generating comprehensive Users Groups async client code...")
    code = build_users_groups_class_code(ops)
    
    # Create microsoft directory (reuse the existing structure)
    script_dir = Path(__file__).parent if __file__ else Path('.')
    microsoft_dir = script_dir / 'microsoft'
    microsoft_dir.mkdir(exist_ok=True)
    
    # Write file
    full_path = microsoft_dir / out_filename
    full_path.write_text(code, encoding="utf-8")
    print(f"Saved comprehensive Users Groups client to: {full_path}")
    
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
        description="Generate comprehensive Users Groups API client"
    )
    ap.add_argument("--out", help="Output .py file path (default: users_groups_client.py)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--spec-url", default=None, help="Microsoft Graph OpenAPI spec URL")
    g.add_argument("--spec-path", help="Local spec file path (JSON/YAML)")
    ap.add_argument("--show-patterns", action="store_true", help="Show all Users Groups path patterns")
    
    return ap.parse_args(argv)

def _show_patterns() -> None:
    """Show all Users Groups path patterns that will be matched."""
    print("Users Groups API Path Patterns (Comprehensive Coverage):")
    print(f"Total patterns: {len(USERS_GROUPS_PATH_PATTERNS)}")
    print()
    
    # Group patterns by category
    pattern_groups = {
        "Users Core": [p for p in USERS_GROUPS_PATH_PATTERNS if p.startswith("/users") and not any(x in p for x in ["authentication", "settings", "mailbox"])],
        "Users Authentication": [p for p in USERS_GROUPS_PATH_PATTERNS if "/authentication" in p],
        "Users Settings": [p for p in USERS_GROUPS_PATH_PATTERNS if "/settings" in p or "/mailbox" in p],
        "Groups Core": [p for p in USERS_GROUPS_PATH_PATTERNS if p.startswith("/groups") and "/settings" not in p],
        "Group Settings": [p for p in USERS_GROUPS_PATH_PATTERNS if "/groupSettings" in p or "/groupSettingTemplates" in p],
        "Invitations": [p for p in USERS_GROUPS_PATH_PATTERNS if p.startswith("/invitations")],
        "Directory Objects": [p for p in USERS_GROUPS_PATH_PATTERNS if p.startswith("/directoryObjects")],
        "Organization": [p for p in USERS_GROUPS_PATH_PATTERNS if p.startswith("/organization")],
        "Directory Roles": [p for p in USERS_GROUPS_PATH_PATTERNS if p.startswith("/directoryRoles") or p.startswith("/directoryRoleTemplates")],
        "Administrative Units": [p for p in USERS_GROUPS_PATH_PATTERNS if p.startswith("/administrativeUnits")],
        "Domains": [p for p in USERS_GROUPS_PATH_PATTERNS if p.startswith("/domains")],
        "Extensions & Subscriptions": [p for p in USERS_GROUPS_PATH_PATTERNS if p.startswith("/schemaExtensions") or p.startswith("/subscriptions")],
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
    
    print(f"Starting comprehensive Users Groups API client generation...")
    
    try:
        out_path = generate_users_groups_client(
            out_path=ns.out,
            spec_url=ns.spec_url,
            spec_path=ns.spec_path,
        )
        
        print(f"\nUsers Groups comprehensive client generation completed!")
        print(f"Generated class: UsersGroupsDataSource")
        print(f"Output file: {out_path}")
        print(f"Features:")
        print(f"  - Complete Users Groups API endpoint coverage (Users, Groups, Invitations)")
        print(f"  - User operations: CRUD, profile, authentication, settings, licenses")
        print(f"  - Group operations: CRUD, membership, settings, lifecycle policies")
        print(f"  - Invitation operations: user invitations and invitation management")
        print(f"  - Directory operations: directory objects, roles, administrative units")
        print(f"  - Organization operations: settings, branding, configuration")
        print(f"  - Advanced features: authentication methods, schema extensions, domains")
        print(f"  - Async methods with comprehensive error handling")
        print(f"")
        print(f"🔧 TO MODIFY EXCLUSIONS:")
        print(f"  - Edit EXCLUDED_KEYWORDS list to add/remove excluded API keywords")
        print(f"  - Edit EXCLUDED_PATHS list to add/remove excluded path patterns")
        print(f"  - Current exclusions: OneDrive, Teams, SharePoint, OneNote, Planner, Outlook")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()