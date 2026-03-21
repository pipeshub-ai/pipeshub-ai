# ruff: noqa
"""
Gong REST API Code Generator

Generates GongDataSource class covering Gong API v2:
- Calls (list, get, extensive, add, transcripts, CRM associations, recording, sharing)
- Users (list, get, history, extensive)
- Stats (aggregate activity, day-by-day, scorecards, interaction)
- Library (folders, folder calls)
- Meetings
- Settings (scorecards, workspaces, trackers, smart trackers)
- CRM (objects, upload data/schema, integration, request status)
- Data Privacy (find people, purge email/phone)
- Engagement Data (content shared, customer engagement)
- Flows
- Digital Interactions
- Permission Profiles (list, get, create, update, delete)
- Company Hierarchy
- Coaching (daily briefs)
- Emails (extensive)
- Forecasting (submissions)

The generated DataSource accepts a GongClient and uses the client's
base URL (https://api.gong.io/v2) to construct request URLs.
Paths do NOT include /v2 prefix (they're relative to base_url).

All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional


# ================================================================================
# Gong API Endpoints - organized by resource
#
# Each endpoint defines:
#   method: HTTP verb
#   path: URL path (appended to base_url which is https://api.gong.io/v2)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (path/query/body), description}
#               Optional: api_name (the actual API parameter name if different from Python name)
#   required: List of required parameter names
# ================================================================================

GONG_API_ENDPOINTS = {
    # ================================================================================
    # CALLS
    # ================================================================================
    "list_calls": {
        "method": "GET",
        "path": "/calls",
        "description": "List calls with optional cursor pagination",
        "parameters": {
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Pagination cursor from previous response"},
        },
        "required": [],
    },
    "get_call": {
        "method": "GET",
        "path": "/calls/{id}",
        "description": "Get a specific call",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The call ID"},
        },
        "required": ["id"],
    },
    "get_calls_extensive": {
        "method": "POST",
        "path": "/calls/extensive",
        "description": "Get detailed call data with content selectors",
        "parameters": {
            "content_selector": {"type": "Optional[dict[str, Any]]", "location": "body", "description": "Content selection (context, exposedFields)", "api_name": "contentSelector"},
            "filter": {"type": "Optional[dict[str, Any]]", "location": "body", "description": "Filter criteria (fromDateTime, toDateTime, callIds, primaryUserIds, workspaceId)"},
            "cursor": {"type": "Optional[str]", "location": "body", "description": "Pagination cursor"},
        },
        "required": [],
    },
    "add_call": {
        "method": "POST",
        "path": "/calls",
        "description": "Add a new call recording",
        "parameters": {
            "actual_start": {"type": "str", "location": "body", "description": "ISO 8601 start time", "api_name": "actualStart"},
            "client_unique_id": {"type": "str", "location": "body", "description": "Unique ID from client", "api_name": "clientUniqueId"},
            "call_provider_code": {"type": "Optional[str]", "location": "body", "description": "Provider code", "api_name": "callProviderCode"},
            "download_media_url": {"type": "Optional[str]", "location": "body", "description": "URL to download media", "api_name": "downloadMediaUrl"},
            "custom_data": {"type": "Optional[str]", "location": "body", "description": "Custom data", "api_name": "customData"},
            "direction": {"type": "Optional[str]", "location": "body", "description": "Inbound/Outbound"},
            "disposition": {"type": "Optional[str]", "location": "body", "description": "Call disposition"},
            "parties": {"type": "Optional[list[Any]]", "location": "body", "description": "Call participants"},
            "primary_user": {"type": "Optional[str]", "location": "body", "description": "Primary user ID", "api_name": "primaryUser"},
            "title": {"type": "Optional[str]", "location": "body", "description": "Call title"},
            "purpose": {"type": "Optional[str]", "location": "body", "description": "Call purpose"},
            "meeting_url": {"type": "Optional[str]", "location": "body", "description": "Meeting URL", "api_name": "meetingUrl"},
            "scheduled_start": {"type": "Optional[str]", "location": "body", "description": "Scheduled start time", "api_name": "scheduledStart"},
            "scheduled_end": {"type": "Optional[str]", "location": "body", "description": "Scheduled end time", "api_name": "scheduledEnd"},
            "language": {"type": "Optional[str]", "location": "body", "description": "Language code"},
        },
        "required": ["actual_start", "client_unique_id"],
    },
    "get_call_transcripts": {
        "method": "POST",
        "path": "/calls/transcript",
        "description": "Get call transcripts",
        "parameters": {
            "filter": {"type": "dict[str, Any]", "location": "body", "description": "Filter with callIds array"},
        },
        "required": ["filter"],
    },
    "get_manual_crm_associations": {
        "method": "GET",
        "path": "/calls/manual-crm-associations",
        "description": "Get manual CRM associations for calls",
        "parameters": {
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Pagination cursor"},
        },
        "required": [],
    },
    "add_call_recording": {
        "method": "PUT",
        "path": "/calls/{id}/media",
        "description": "Upload media for a call",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The call ID"},
            "media_url": {"type": "Optional[str]", "location": "body", "description": "Media URL", "api_name": "mediaUrl"},
        },
        "required": ["id"],
    },
    "get_call_sharing": {
        "method": "POST",
        "path": "/calls/sharing",
        "description": "Get shared calls",
        "parameters": {
            "filter": {"type": "Optional[dict[str, Any]]", "location": "body", "description": "Filter criteria"},
            "cursor": {"type": "Optional[str]", "location": "body", "description": "Pagination cursor"},
        },
        "required": [],
    },

    # ================================================================================
    # USERS
    # ================================================================================
    "list_users": {
        "method": "GET",
        "path": "/users",
        "description": "List all users",
        "parameters": {
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Pagination cursor"},
            "include_avatars": {"type": "Optional[bool]", "location": "query", "description": "Include avatar URLs", "api_name": "includeAvatars"},
        },
        "required": [],
    },
    "get_user": {
        "method": "GET",
        "path": "/users/{id}",
        "description": "Get a specific user",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The user ID"},
        },
        "required": ["id"],
    },
    "get_user_history": {
        "method": "GET",
        "path": "/users/{id}/settings-history",
        "description": "Get user settings history",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The user ID"},
        },
        "required": ["id"],
    },
    "list_users_extensive": {
        "method": "POST",
        "path": "/users/extensive",
        "description": "List users by filter",
        "parameters": {
            "filter": {"type": "Optional[dict[str, Any]]", "location": "body", "description": "Filter (fromDateTime, toDateTime, createdFromDateTime, createdToDateTime, userIds)"},
            "cursor": {"type": "Optional[str]", "location": "body", "description": "Pagination cursor"},
            "include_avatars": {"type": "Optional[bool]", "location": "body", "description": "Include avatar URLs", "api_name": "includeAvatars"},
        },
        "required": [],
    },

    # ================================================================================
    # STATS
    # ================================================================================
    "get_aggregate_activity": {
        "method": "POST",
        "path": "/stats/activity/aggregate",
        "description": "Get aggregated activity stats by users",
        "parameters": {
            "filter": {"type": "dict[str, Any]", "location": "body", "description": "Filter (fromDate, toDate, userIds)"},
        },
        "required": ["filter"],
    },
    "get_aggregate_activity_by_period": {
        "method": "POST",
        "path": "/stats/activity/aggregate-by-period",
        "description": "Get activity aggregated by period",
        "parameters": {
            "filter": {"type": "dict[str, Any]", "location": "body", "description": "Filter (fromDate, toDate, userIds)"},
            "aggregation_period": {"type": "Optional[str]", "location": "body", "description": "Aggregation period (DAY, WEEK, MONTH, QUARTER)", "api_name": "aggregationPeriod"},
        },
        "required": ["filter"],
    },
    "get_activity_day_by_day": {
        "method": "POST",
        "path": "/stats/activity/day-by-day",
        "description": "Get day-by-day activity",
        "parameters": {
            "filter": {"type": "dict[str, Any]", "location": "body", "description": "Filter (fromDate, toDate, userIds)"},
            "aggregation_period": {"type": "Optional[str]", "location": "body", "description": "Unused but accepted for consistency", "api_name": "aggregationPeriod"},
        },
        "required": ["filter"],
    },
    "get_answered_scorecards": {
        "method": "POST",
        "path": "/stats/activity/scorecards",
        "description": "Get answered scorecards by user",
        "parameters": {
            "filter": {"type": "dict[str, Any]", "location": "body", "description": "Filter (fromDate, toDate, userIds, scorecardIds)"},
            "aggregation_period": {"type": "Optional[str]", "location": "body", "description": "Aggregation period", "api_name": "aggregationPeriod"},
        },
        "required": ["filter"],
    },
    "get_interaction_stats": {
        "method": "POST",
        "path": "/stats/interaction",
        "description": "Get interaction stats by user",
        "parameters": {
            "filter": {"type": "dict[str, Any]", "location": "body", "description": "Filter (fromDate, toDate, userIds)"},
        },
        "required": ["filter"],
    },

    # ================================================================================
    # LIBRARY (Gong Library / Coaching)
    # ================================================================================
    "list_library_folders": {
        "method": "GET",
        "path": "/library/folders",
        "description": "List library folders",
        "parameters": {
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Pagination cursor"},
            "workspace_id": {"type": "Optional[str]", "location": "query", "description": "Workspace ID", "api_name": "workspaceId"},
        },
        "required": [],
    },
    "get_library_folder_calls": {
        "method": "GET",
        "path": "/library/folder-calls",
        "description": "Get calls in a library folder",
        "parameters": {
            "folder_id": {"type": "str", "location": "query", "description": "The folder ID", "api_name": "folderId"},
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Pagination cursor"},
        },
        "required": ["folder_id"],
    },

    # ================================================================================
    # MEETINGS
    # ================================================================================
    "list_meetings": {
        "method": "GET",
        "path": "/meetings",
        "description": "List meetings",
        "parameters": {
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Pagination cursor"},
            "from_date_time": {"type": "Optional[str]", "location": "query", "description": "ISO 8601 start filter", "api_name": "fromDateTime"},
            "to_date_time": {"type": "Optional[str]", "location": "query", "description": "ISO 8601 end filter", "api_name": "toDateTime"},
        },
        "required": [],
    },

    # ================================================================================
    # SETTINGS
    # ================================================================================
    "list_scorecards": {
        "method": "GET",
        "path": "/settings/scorecards",
        "description": "List scorecards",
        "parameters": {
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Pagination cursor"},
        },
        "required": [],
    },
    "list_workspaces": {
        "method": "GET",
        "path": "/settings/workspaces",
        "description": "List workspaces",
        "parameters": {},
        "required": [],
    },
    "list_trackers": {
        "method": "GET",
        "path": "/settings/trackers",
        "description": "List trackers (keywords)",
        "parameters": {
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Pagination cursor"},
        },
        "required": [],
    },
    "list_smart_trackers": {
        "method": "GET",
        "path": "/settings/trackers/smart-trackers",
        "description": "List smart trackers",
        "parameters": {
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Pagination cursor"},
        },
        "required": [],
    },

    # ================================================================================
    # CRM
    # ================================================================================
    "get_crm_objects": {
        "method": "GET",
        "path": "/crm/objects",
        "description": "Get CRM objects synced to Gong",
        "parameters": {
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Pagination cursor"},
            "object_type": {"type": "Optional[str]", "location": "query", "description": "e.g., deals, accounts, contacts, leads", "api_name": "objectType"},
        },
        "required": [],
    },
    "upload_crm_data": {
        "method": "POST",
        "path": "/crm/object/list",
        "description": "Upload CRM object data (for custom CRM integrations)",
        "parameters": {
            "integration_id": {"type": "str", "location": "body", "description": "Integration ID", "api_name": "integrationId"},
            "objects": {"type": "list[Any]", "location": "body", "description": "CRM objects to upload"},
        },
        "required": ["integration_id", "objects"],
    },
    "upload_crm_schema": {
        "method": "POST",
        "path": "/crm/object/schema/list",
        "description": "Upload CRM object schema",
        "parameters": {
            "integration_id": {"type": "str", "location": "body", "description": "Integration ID", "api_name": "integrationId"},
            "schemas": {"type": "list[Any]", "location": "body", "description": "CRM schemas"},
        },
        "required": ["integration_id", "schemas"],
    },
    "delete_crm_integration": {
        "method": "DELETE",
        "path": "/crm/integration/delete",
        "description": "Delete a CRM integration",
        "parameters": {
            "integration_id": {"type": "str", "location": "query", "description": "Integration ID", "api_name": "integrationId"},
        },
        "required": ["integration_id"],
    },
    "register_crm_integration": {
        "method": "PUT",
        "path": "/crm/integration/register",
        "description": "Register a CRM integration",
        "parameters": {
            "integration_id": {"type": "str", "location": "body", "description": "Integration ID", "api_name": "integrationId"},
            "integration_name": {"type": "str", "location": "body", "description": "Integration name", "api_name": "integrationName"},
            "crm_system_type": {"type": "Optional[str]", "location": "body", "description": "CRM type", "api_name": "crmSystemType"},
        },
        "required": ["integration_id", "integration_name"],
    },
    "get_request_status": {
        "method": "GET",
        "path": "/crm/request-status",
        "description": "Get status of a CRM data upload",
        "parameters": {
            "integration_id": {"type": "str", "location": "query", "description": "Integration ID", "api_name": "integrationId"},
            "client_request_id": {"type": "str", "location": "query", "description": "Client request ID", "api_name": "clientRequestId"},
        },
        "required": ["integration_id", "client_request_id"],
    },

    # ================================================================================
    # DATA PRIVACY
    # ================================================================================
    "find_people_by_email_or_phone": {
        "method": "POST",
        "path": "/data-privacy/data-for-email-address",
        "description": "Find people matching email/phone for GDPR",
        "parameters": {
            "email_address": {"type": "Optional[str]", "location": "body", "description": "Email to search", "api_name": "emailAddress"},
            "phone_number": {"type": "Optional[str]", "location": "body", "description": "Phone to search", "api_name": "phoneNumber"},
        },
        "required": [],
    },
    "purge_email_address": {
        "method": "POST",
        "path": "/data-privacy/erase-data-for-email-address",
        "description": "Purge data by email",
        "parameters": {
            "email_address": {"type": "str", "location": "body", "description": "Email address to purge", "api_name": "emailAddress"},
        },
        "required": ["email_address"],
    },
    "purge_phone_number": {
        "method": "POST",
        "path": "/data-privacy/erase-data-for-phone-number",
        "description": "Purge data by phone number",
        "parameters": {
            "phone_number": {"type": "str", "location": "body", "description": "Phone number to purge", "api_name": "phoneNumber"},
        },
        "required": ["phone_number"],
    },

    # ================================================================================
    # ENGAGEMENT DATA
    # ================================================================================
    "get_content_shared_with_external": {
        "method": "POST",
        "path": "/engagement-data/content-shared",
        "description": "Get content shared with external parties",
        "parameters": {
            "filter": {"type": "dict[str, Any]", "location": "body", "description": "Filter criteria"},
            "cursor": {"type": "Optional[str]", "location": "body", "description": "Pagination cursor"},
        },
        "required": ["filter"],
    },
    "upload_customer_engagement": {
        "method": "POST",
        "path": "/engagement-data/customer-engagement",
        "description": "Upload customer engagement data",
        "parameters": {
            "entries": {"type": "list[Any]", "location": "body", "description": "Engagement entries"},
        },
        "required": ["entries"],
    },

    # ================================================================================
    # FLOWS
    # ================================================================================
    "list_flows": {
        "method": "GET",
        "path": "/flows",
        "description": "List flows (Gong Engage flows)",
        "parameters": {
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Pagination cursor"},
        },
        "required": [],
    },

    # ================================================================================
    # DIGITAL INTERACTIONS
    # ================================================================================
    "upload_digital_interactions": {
        "method": "POST",
        "path": "/digital-interactions",
        "description": "Upload digital interaction records",
        "parameters": {
            "records": {"type": "list[Any]", "location": "body", "description": "Digital interaction records"},
        },
        "required": ["records"],
    },

    # ================================================================================
    # PERMISSION PROFILES
    # ================================================================================
    "list_permission_profiles": {
        "method": "GET",
        "path": "/permission-profile",
        "description": "List permission profiles",
        "parameters": {},
        "required": [],
    },
    "get_permission_profile": {
        "method": "GET",
        "path": "/permission-profile/{id}",
        "description": "Get a permission profile",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The permission profile ID"},
        },
        "required": ["id"],
    },
    "create_permission_profile": {
        "method": "POST",
        "path": "/permission-profile",
        "description": "Create a permission profile",
        "parameters": {
            "name": {"type": "str", "location": "body", "description": "Profile name"},
            "permissions": {"type": "list[Any]", "location": "body", "description": "Permissions list"},
        },
        "required": ["name", "permissions"],
    },
    "update_permission_profile": {
        "method": "PUT",
        "path": "/permission-profile/{id}",
        "description": "Update a permission profile",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The permission profile ID"},
            "name": {"type": "Optional[str]", "location": "body", "description": "Profile name"},
            "permissions": {"type": "Optional[list[Any]]", "location": "body", "description": "Permissions list"},
        },
        "required": ["id"],
    },
    "delete_permission_profile": {
        "method": "DELETE",
        "path": "/permission-profile/{id}",
        "description": "Delete a permission profile",
        "parameters": {
            "id": {"type": "str", "location": "path", "description": "The permission profile ID"},
        },
        "required": ["id"],
    },

    # ================================================================================
    # COMPANY HIERARCHY
    # ================================================================================
    "list_company_users": {
        "method": "GET",
        "path": "/company/users",
        "description": "List company users with hierarchy info",
        "parameters": {
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Pagination cursor"},
        },
        "required": [],
    },

    # ================================================================================
    # COACHING
    # ================================================================================
    "get_daily_briefs": {
        "method": "POST",
        "path": "/coaching/daily-briefs",
        "description": "Get daily brief summaries",
        "parameters": {
            "filter": {"type": "dict[str, Any]", "location": "body", "description": "Filter criteria"},
        },
        "required": ["filter"],
    },

    # ================================================================================
    # EMAILS
    # ================================================================================
    "get_emails_extensive": {
        "method": "POST",
        "path": "/emails/extensive",
        "description": "Get detailed email activity data",
        "parameters": {
            "filter": {"type": "dict[str, Any]", "location": "body", "description": "Filter criteria"},
            "content_selector": {"type": "Optional[dict[str, Any]]", "location": "body", "description": "Content selection", "api_name": "contentSelector"},
            "cursor": {"type": "Optional[str]", "location": "body", "description": "Pagination cursor"},
        },
        "required": ["filter"],
    },

    # ================================================================================
    # FORECASTING
    # ================================================================================
    "list_forecast_submissions": {
        "method": "GET",
        "path": "/forecast/submissions",
        "description": "List forecast submissions",
        "parameters": {
            "cursor": {"type": "Optional[str]", "location": "query", "description": "Pagination cursor"},
            "from_date_time": {"type": "Optional[str]", "location": "query", "description": "ISO 8601 start filter", "api_name": "fromDateTime"},
            "to_date_time": {"type": "Optional[str]", "location": "query", "description": "ISO 8601 end filter", "api_name": "toDateTime"},
        },
        "required": [],
    },
}


class GongDataSourceGenerator:
    """Generator for comprehensive Gong REST API datasource class.

    Generates methods for Gong API v2 endpoints.
    The generated DataSource class accepts a GongClient whose base URL
    setting determines the API endpoint (https://api.gong.io/v2).

    All methods have explicit parameter signatures.
    """

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace("-", "_").replace(".", "_").replace("/", "_")
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == "_"):
            sanitized = f"param_{sanitized}"
        # Avoid shadowing Python builtins
        if sanitized == "format":
            sanitized = "image_format"
        return sanitized

    def _build_query_params(self, endpoint_info: Dict) -> List[str]:
        """Build query parameter handling code."""
        lines = ["        query_params: dict[str, Any] = {}"]
        required_set = set(endpoint_info.get("required", []))

        for param_name, param_info in endpoint_info["parameters"].items():
            if param_info["location"] == "query":
                sanitized_name = self._sanitize_parameter_name(param_name)
                api_name = param_info.get("api_name", param_name)
                is_required = param_name in required_set

                if is_required:
                    if "bool" in param_info["type"]:
                        lines.append(
                            f"        query_params['{api_name}'] = str({sanitized_name}).lower()"
                        )
                    elif "int" in param_info["type"] or "float" in param_info["type"]:
                        lines.append(
                            f"        query_params['{api_name}'] = str({sanitized_name})"
                        )
                    else:
                        lines.append(
                            f"        query_params['{api_name}'] = {sanitized_name}"
                        )
                elif "Optional[bool]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}'] = str({sanitized_name}).lower()",
                    ])
                elif "Optional[int]" in param_info["type"] or "Optional[float]" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}'] = str({sanitized_name})",
                    ])
                elif "List[" in param_info["type"]:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}[]'] = {sanitized_name}",
                    ])
                else:
                    lines.extend([
                        f"        if {sanitized_name} is not None:",
                        f"            query_params['{api_name}'] = {sanitized_name}",
                    ])

        return lines

    def _build_path_formatting(self, path: str, endpoint_info: Dict) -> str:
        """Build URL path with parameter substitution."""
        path_params = [
            name
            for name, info in endpoint_info["parameters"].items()
            if info["location"] == "path"
        ]

        if path_params:
            format_dict = ", ".join(
                f"{param}={self._sanitize_parameter_name(param)}"
                for param in path_params
            )
            return f'        url = self.base_url + "{path}".format({format_dict})'
        else:
            return f'        url = self.base_url + "{path}"'

    def _build_request_body(self, endpoint_info: Dict) -> List[str]:
        """Build request body handling."""
        body_params = {
            name: info
            for name, info in endpoint_info["parameters"].items()
            if info["location"] == "body"
        }

        if not body_params:
            return []

        lines = ["        body: dict[str, Any] = {}"]

        for param_name, param_info in body_params.items():
            sanitized_name = self._sanitize_parameter_name(param_name)
            api_name = param_info.get("api_name", param_name)

            if param_name in endpoint_info["required"]:
                lines.append(f"        body['{api_name}'] = {sanitized_name}")
            else:
                lines.extend([
                    f"        if {sanitized_name} is not None:",
                    f"            body['{api_name}'] = {sanitized_name}",
                ])

        return lines

    @staticmethod
    def _modernize_type(type_str: str) -> str:
        """Convert typing-style annotations to modern Python 3.10+ syntax.

        Optional[str] -> str | None, Dict[str, Any] -> dict[str, Any],
        List[str] -> list[str], etc.
        """
        if type_str.startswith("Optional[") and type_str.endswith("]"):
            inner = type_str[len("Optional["):-1]
            inner = GongDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = GongDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                GongDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = GongDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                GongDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"list[{modernized}]"
        if type_str == "List":
            return "list"
        return type_str

    @staticmethod
    def _split_type_args(s: str) -> List[str]:
        """Split type arguments respecting nested brackets."""
        parts = []
        depth = 0
        current = ""
        for ch in s:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            parts.append(current.strip())
        return parts

    def _generate_method_signature(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate method signature with explicit parameters."""
        params = ["self"]
        has_any_bool = False

        # Collect required params, split into non-bool and bool groups
        required_non_bool: List[str] = []
        required_bool: List[str] = []
        for param_name in endpoint_info["required"]:
            if param_name in endpoint_info["parameters"]:
                param_info = endpoint_info["parameters"][param_name]
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                param_str = f"{sanitized_name}: {modern_type}"
                if "bool" in param_info.get("type", ""):
                    required_bool.append(param_str)
                    has_any_bool = True
                else:
                    required_non_bool.append(param_str)

        # Collect optional parameters
        optional_params: List[str] = []
        for param_name, param_info in endpoint_info["parameters"].items():
            if param_name not in endpoint_info["required"]:
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                if "| None" not in modern_type:
                    modern_type = f"{modern_type} | None"
                optional_params.append(f"{sanitized_name}: {modern_type} = None")
                if "bool" in param_info.get("type", ""):
                    has_any_bool = True

        # Build signature: non-bool required first, then * if needed, then bool required + optional
        params.extend(required_non_bool)
        if has_any_bool and (required_bool or optional_params):
            params.append("*")
        params.extend(required_bool)
        params.extend(optional_params)

        signature_params = ",\n        ".join(params)
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> GongResponse:"

    def _generate_method_docstring(self, endpoint_info: Dict) -> List[str]:
        """Generate method docstring."""
        lines = [f'        """{endpoint_info["description"]}', ""]

        if endpoint_info["parameters"]:
            lines.append("        Args:")
            for param_name, param_info in endpoint_info["parameters"].items():
                sanitized_name = self._sanitize_parameter_name(param_name)
                lines.append(
                    f"            {sanitized_name}: {param_info['description']}"
                )
            lines.append("")

        lines.extend([
            "        Returns:",
            "            GongResponse with operation result",
            '        """',
        ])

        return lines

    def _generate_method(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate a complete method for an API endpoint."""
        lines = []

        # Method signature
        lines.append(self._generate_method_signature(method_name, endpoint_info))

        # Docstring
        lines.extend(self._generate_method_docstring(endpoint_info))

        # Query parameters
        has_query = any(
            info["location"] == "query"
            for info in endpoint_info["parameters"].values()
        )
        if has_query:
            query_lines = self._build_query_params(endpoint_info)
            lines.extend(query_lines)
            lines.append("")

        # URL construction
        lines.append(self._build_path_formatting(endpoint_info["path"], endpoint_info))

        # Request body
        body_lines = self._build_request_body(endpoint_info)
        if body_lines:
            lines.append("")
            lines.extend(body_lines)

        # Request construction and execution
        lines.append("")
        lines.append("        try:")
        lines.append("            request = HTTPRequest(")
        lines.append(f'                method="{endpoint_info["method"]}",')
        lines.append("                url=url,")
        lines.append('                headers={"Content-Type": "application/json"},')
        if has_query:
            lines.append("                query=query_params,")
        if body_lines:
            lines.append("                body=body,")
        lines.append("            )")
        lines.extend([
            "            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]",
            "            response_data = response.json() if response.text() else None",
            "            return GongResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return GongResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_gong_datasource(self) -> str:
        """Generate the complete Gong datasource class."""

        class_lines = [
            "# ruff: noqa",
            '"""',
            "Gong REST API DataSource - Auto-generated API wrapper",
            "",
            "Generated from Gong REST API v2 documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.gong.gong import GongClient, GongResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class GongDataSource:",
            '    """Gong REST API DataSource',
            "",
            "    Provides async wrapper methods for Gong REST API v2 operations:",
            "    - Calls (list, get, extensive, add, transcripts, CRM associations, recording, sharing)",
            "    - Users (list, get, history, extensive)",
            "    - Stats (aggregate activity, day-by-day, scorecards, interaction)",
            "    - Library (folders, folder calls)",
            "    - Meetings",
            "    - Settings (scorecards, workspaces, trackers, smart trackers)",
            "    - CRM (objects, upload data/schema, integration, request status)",
            "    - Data Privacy (find people, purge email/phone)",
            "    - Engagement Data (content shared, customer engagement)",
            "    - Flows",
            "    - Digital Interactions",
            "    - Permission Profiles (list, get, create, update, delete)",
            "    - Company Hierarchy",
            "    - Coaching (daily briefs)",
            "    - Emails (extensive)",
            "    - Forecasting (submissions)",
            "",
            "    The base URL is determined by the GongClient's configured base URL",
            "    (default: https://api.gong.io/v2).",
            "",
            "    All methods return GongResponse objects.",
            '    """',
            "",
            "    def __init__(self, client: GongClient) -> None:",
            '        """Initialize with GongClient.',
            "",
            "        Args:",
            "            client: GongClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'GongDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> GongClient:",
            '        """Return the underlying GongClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in GONG_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Gong datasource to a file."""
        if filename is None:
            filename = "gong.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        gong_dir = script_dir.parent / "app" / "sources" / "external" / "gong"
        gong_dir.mkdir(parents=True, exist_ok=True)

        full_path = gong_dir / filename

        class_code = self.generate_gong_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Gong data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by category
        resource_categories = {
            "Call": 0,
            "User": 0,
            "Stats": 0,
            "Library": 0,
            "Meeting": 0,
            "Settings": 0,
            "CRM": 0,
            "Data Privacy": 0,
            "Engagement Data": 0,
            "Flow": 0,
            "Digital Interaction": 0,
            "Permission Profile": 0,
            "Company Hierarchy": 0,
            "Coaching": 0,
            "Email": 0,
            "Forecasting": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            if "permission_profile" in name:
                resource_categories["Permission Profile"] += 1
            elif "call" in name and "scorecard" not in name:
                resource_categories["Call"] += 1
            elif "user" in name and "company" not in name:
                resource_categories["User"] += 1
            elif "aggregate" in name or "activity" in name or "scorecard" in name or "interaction_stats" in name:
                resource_categories["Stats"] += 1
            elif "library" in name:
                resource_categories["Library"] += 1
            elif "meeting" in name:
                resource_categories["Meeting"] += 1
            elif "scorecard" in name and "answered" not in name or "workspace" in name or "tracker" in name:
                resource_categories["Settings"] += 1
            elif "crm" in name or "request_status" in name:
                resource_categories["CRM"] += 1
            elif "purge" in name or "find_people" in name:
                resource_categories["Data Privacy"] += 1
            elif "engagement" in name or "content_shared" in name:
                resource_categories["Engagement Data"] += 1
            elif "flow" in name:
                resource_categories["Flow"] += 1
            elif "digital" in name:
                resource_categories["Digital Interaction"] += 1
            elif "company" in name:
                resource_categories["Company Hierarchy"] += 1
            elif "daily_brief" in name or "coaching" in name:
                resource_categories["Coaching"] += 1
            elif "email" in name:
                resource_categories["Email"] += 1
            elif "forecast" in name:
                resource_categories["Forecasting"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for Gong data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Gong REST API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = GongDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Gong data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
