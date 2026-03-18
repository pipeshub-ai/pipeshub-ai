#!/usr/bin/env python3
# ruff: noqa
"""
DocuSign Unified Code Generator

Generates a single ``DocuSignDataSource`` class with ALL methods across:
- eSignature (SDK-based via docusign-esign)
- Admin, Rooms, Click, Monitor, WebForms (HTTP-based via HTTPClient)

SDK methods are emitted as direct docusign_esign SDK calls (same pattern as
the previous generator).  HTTP methods are emitted as HTTPRequest-based calls
following the ClickUp datasource pattern.

Run:
    cd backend/python
    python code-generator/docusign.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Output configuration
# ---------------------------------------------------------------------------

DEFAULT_OUT = "app/sources/external/docusign/docusign.py"

# ---------------------------------------------------------------------------
# SDK-based methods (eSignature)
# ---------------------------------------------------------------------------
# Each tuple: (signature, body, short_doc)

SDK_METHODS: List[Tuple[str, str, str]] = []

# ---------- Envelopes ----------
SDK_METHODS += [
    (
        "list_envelopes(self, from_date: str, to_date: str | None = None, status: str | None = None, search_text: str | None = None, count: str | None = None, start_position: str | None = None, order: str | None = None, order_by: str | None = None, folder_ids: str | None = None) -> DocuSignResponse",
        "            params = self._params(from_date=from_date, to_date=to_date, status=status, search_text=search_text, count=count, start_position=start_position, order=order, order_by=order_by, folder_ids=folder_ids)\n"
        "            result = self.envelopes_api.list_status_changes(account_id=self._account_id, **params)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List envelopes for the account. from_date is required by the API.  [eSign]",
    ),
    (
        "get_envelope(self, envelope_id: str) -> DocuSignResponse",
        "            result = self.envelopes_api.get_envelope(account_id=self._account_id, envelope_id=envelope_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "Get details for a specific envelope.  [eSign]",
    ),
    (
        "create_envelope(self, envelope_definition: dict[str, object]) -> DocuSignResponse",
        "            body = docusign_esign.EnvelopeDefinition(**envelope_definition)\n"
        "            result = self.envelopes_api.create_envelope(account_id=self._account_id, envelope_definition=body)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "Create and optionally send a new envelope from an envelope definition dict.  [eSign]",
    ),
    (
        "update_envelope(self, envelope_id: str, envelope: dict[str, object]) -> DocuSignResponse",
        "            body = docusign_esign.Envelope(**envelope)\n"
        "            result = self.envelopes_api.update(account_id=self._account_id, envelope_id=envelope_id, envelope=body)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "Update an existing envelope (e.g. change status to sent or voided).  [eSign]",
    ),
    (
        "list_envelope_documents(self, envelope_id: str) -> DocuSignResponse",
        "            result = self.envelopes_api.list_documents(account_id=self._account_id, envelope_id=envelope_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List documents in an envelope.  [eSign]",
    ),
    (
        "get_envelope_document(self, envelope_id: str, document_id: str) -> DocuSignResponse",
        "            result = self.envelopes_api.get_document(account_id=self._account_id, envelope_id=envelope_id, document_id=document_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "Download a specific document from an envelope.  [eSign]",
    ),
    (
        "list_envelope_recipients(self, envelope_id: str) -> DocuSignResponse",
        "            result = self.envelopes_api.list_recipients(account_id=self._account_id, envelope_id=envelope_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List recipients for an envelope.  [eSign]",
    ),
    (
        "get_envelope_audit_events(self, envelope_id: str) -> DocuSignResponse",
        "            result = self.envelopes_api.list_audit_events(account_id=self._account_id, envelope_id=envelope_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "Get audit trail events for an envelope.  [eSign]",
    ),
]

# ---------- Templates ----------
SDK_METHODS += [
    (
        "list_templates(self, count: str | None = None, start_position: str | None = None, search_text: str | None = None, folder: str | None = None, order: str | None = None, order_by: str | None = None) -> DocuSignResponse",
        "            params = self._params(count=count, start_position=start_position, search_text=search_text, folder=folder, order=order, order_by=order_by)\n"
        "            result = self.templates_api.list_templates(account_id=self._account_id, **params)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List templates for the account.  [eSign]",
    ),
    (
        "get_template(self, template_id: str) -> DocuSignResponse",
        "            result = self.templates_api.get(account_id=self._account_id, template_id=template_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "Get details for a specific template.  [eSign]",
    ),
]

# ---------- Users ----------
SDK_METHODS += [
    (
        "list_users(self, count: str | None = None, start_position: str | None = None, status: str | None = None, email: str | None = None) -> DocuSignResponse",
        "            params = self._params(count=count, start_position=start_position, status=status, email=email)\n"
        "            result = self.users_api.list(account_id=self._account_id, **params)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List users in the account.  [eSign]",
    ),
    (
        "get_user(self, user_id: str) -> DocuSignResponse",
        "            result = self.users_api.get_information(account_id=self._account_id, user_id=user_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "Get details for a specific user.  [eSign]",
    ),
]

# ---------- Folders ----------
SDK_METHODS += [
    (
        "list_folders(self) -> DocuSignResponse",
        "            result = self.folders_api.list(account_id=self._account_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List folders in the account.  [eSign]",
    ),
    (
        "list_folder_items(self, folder_id: str, from_date: str | None = None, to_date: str | None = None, status: str | None = None, search_text: str | None = None, count: str | None = None, start_position: str | None = None) -> DocuSignResponse",
        "            params = self._params(from_date=from_date, to_date=to_date, status=status, search_text=search_text, count=count, start_position=start_position)\n"
        "            result = self.folders_api.list_items(account_id=self._account_id, folder_id=folder_id, **params)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List items (envelopes) in a specific folder.  [eSign]",
    ),
]

# ---------- Account (Brands / Custom Fields) ----------
SDK_METHODS += [
    (
        "list_brands(self) -> DocuSignResponse",
        "            result = self.accounts_api.list_brands(account_id=self._account_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List brands for the account.  [eSign]",
    ),
    (
        "list_custom_fields(self) -> DocuSignResponse",
        "            result = self.accounts_api.list_custom_fields(account_id=self._account_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List custom fields for the account.  [eSign]",
    ),
]

# ---------------------------------------------------------------------------
# HTTP-based endpoints (Admin, Rooms, Click, Monitor, WebForms)
# ---------------------------------------------------------------------------
# Each endpoint dict follows the ClickUp pattern:
#   method, path, description, parameters, required, api
#
# The ``api`` key maps to the lazy HTTP client accessor:
#   "admin" -> _get_admin_http()   base = ADMIN_BASE_URL
#   "rooms" -> _get_rooms_http()   base = ROOMS_BASE_URL
#   "click" -> _get_click_http()   base = CLICK_BASE_URL
#   "monitor" -> _get_monitor_http()   base = MONITOR_BASE_URL
#   "webforms" -> _get_webforms_http() base = WEBFORMS_BASE_URL

DOCUSIGN_HTTP_ENDPOINTS: Dict[str, dict] = {
    # ========================================================================
    # ADMIN
    # ========================================================================
    "admin_get_organizations": {
        "method": "GET",
        "path": "/v2/organizations",
        "description": "Get all organizations",
        "parameters": {},
        "required": [],
        "api": "admin",
    },
    "admin_get_users": {
        "method": "GET",
        "path": "/v2.1/organizations/{org_id}/users",
        "description": "Get users for an organization",
        "parameters": {
            "org_id": {"type": "str", "location": "path", "description": "Organization ID"},
            "account_id": {"type": "str | None", "location": "query", "description": "Filter by account ID"},
            "email": {"type": "str | None", "location": "query", "description": "Filter by email address"},
            "start": {"type": "int | None", "location": "query", "description": "Start index for pagination"},
            "take": {"type": "int | None", "location": "query", "description": "Number of results to return"},
        },
        "required": ["org_id"],
        "api": "admin",
    },
    "admin_get_user_profile": {
        "method": "GET",
        "path": "/v2.1/organizations/{org_id}/users/profile",
        "description": "Get user profile by email",
        "parameters": {
            "org_id": {"type": "str", "location": "path", "description": "Organization ID"},
            "email": {"type": "str | None", "location": "query", "description": "Email address to look up"},
        },
        "required": ["org_id"],
        "api": "admin",
    },
    "admin_get_ds_groups": {
        "method": "GET",
        "path": "/v2.1/organizations/{org_id}/accounts/{account_id}/dsGroups",
        "description": "Get DocuSign groups for an account",
        "parameters": {
            "org_id": {"type": "str", "location": "path", "description": "Organization ID"},
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
        },
        "required": ["org_id", "account_id"],
        "api": "admin",
    },
    "admin_get_permission_profiles": {
        "method": "GET",
        "path": "/v2.1/organizations/{org_id}/accounts/{account_id}/products/permission_profiles",
        "description": "Get permission profiles for an account",
        "parameters": {
            "org_id": {"type": "str", "location": "path", "description": "Organization ID"},
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
        },
        "required": ["org_id", "account_id"],
        "api": "admin",
    },

    # ========================================================================
    # ROOMS
    # ========================================================================
    "rooms_get_rooms": {
        "method": "GET",
        "path": "/v2/accounts/{account_id}/rooms",
        "description": "Get rooms for the account",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "count": {"type": "int | None", "location": "query", "description": "Number of results to return"},
            "startPosition": {"type": "int | None", "location": "query", "description": "Start position for pagination"},
            "roomStatus": {"type": "str | None", "location": "query", "description": "Filter by room status"},
        },
        "required": ["account_id"],
        "api": "rooms",
    },
    "rooms_get_room": {
        "method": "GET",
        "path": "/v2/accounts/{account_id}/rooms/{room_id}",
        "description": "Get a specific room",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "room_id": {"type": "str", "location": "path", "description": "Room ID"},
        },
        "required": ["account_id", "room_id"],
        "api": "rooms",
    },
    "rooms_create_room": {
        "method": "POST",
        "path": "/v2/accounts/{account_id}/rooms",
        "description": "Create a new room",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "name": {"type": "str", "location": "body", "description": "Room name"},
            "roleId": {"type": "int", "location": "body", "description": "Role ID for the room creator"},
            "transactionSideId": {"type": "str | None", "location": "body", "description": "Transaction side ID"},
        },
        "required": ["account_id", "name", "roleId"],
        "api": "rooms",
    },
    "rooms_delete_room": {
        "method": "DELETE",
        "path": "/v2/accounts/{account_id}/rooms/{room_id}",
        "description": "Delete a room",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "room_id": {"type": "str", "location": "path", "description": "Room ID"},
        },
        "required": ["account_id", "room_id"],
        "api": "rooms",
    },
    "rooms_get_room_documents": {
        "method": "GET",
        "path": "/v2/accounts/{account_id}/rooms/{room_id}/documents",
        "description": "Get documents in a room",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "room_id": {"type": "str", "location": "path", "description": "Room ID"},
        },
        "required": ["account_id", "room_id"],
        "api": "rooms",
    },
    "rooms_get_room_templates": {
        "method": "GET",
        "path": "/v2/accounts/{account_id}/room_templates",
        "description": "Get room templates",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "count": {"type": "int | None", "location": "query", "description": "Number of results to return"},
            "startPosition": {"type": "int | None", "location": "query", "description": "Start position for pagination"},
        },
        "required": ["account_id"],
        "api": "rooms",
    },
    "rooms_get_roles": {
        "method": "GET",
        "path": "/v2/accounts/{account_id}/roles",
        "description": "Get roles for the account",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
        },
        "required": ["account_id"],
        "api": "rooms",
    },
    "rooms_get_offices": {
        "method": "GET",
        "path": "/v2/accounts/{account_id}/offices",
        "description": "Get offices for the account",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
        },
        "required": ["account_id"],
        "api": "rooms",
    },
    "rooms_get_regions": {
        "method": "GET",
        "path": "/v2/accounts/{account_id}/regions",
        "description": "Get regions for the account",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
        },
        "required": ["account_id"],
        "api": "rooms",
    },
    "rooms_get_form_libraries": {
        "method": "GET",
        "path": "/v2/accounts/{account_id}/form_libraries",
        "description": "Get form libraries for the account",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
        },
        "required": ["account_id"],
        "api": "rooms",
    },

    # ========================================================================
    # CLICK
    # ========================================================================
    "click_get_clickwraps": {
        "method": "GET",
        "path": "/v1/accounts/{account_id}/clickwraps",
        "description": "Get all clickwraps for the account",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
        },
        "required": ["account_id"],
        "api": "click",
    },
    "click_get_clickwrap": {
        "method": "GET",
        "path": "/v1/accounts/{account_id}/clickwraps/{clickwrap_id}",
        "description": "Get a specific clickwrap",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "clickwrap_id": {"type": "str", "location": "path", "description": "Clickwrap ID"},
        },
        "required": ["account_id", "clickwrap_id"],
        "api": "click",
    },
    "click_create_clickwrap": {
        "method": "POST",
        "path": "/v1/accounts/{account_id}/clickwraps",
        "description": "Create a new clickwrap",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "clickwrapName": {"type": "str", "location": "body", "description": "Clickwrap name"},
            "documents": {"type": "list[dict[str, object]]", "location": "body", "description": "Documents for the clickwrap"},
            "requireReacceptance": {"type": "bool | None", "location": "body", "description": "Whether re-acceptance is required"},
        },
        "required": ["account_id", "clickwrapName", "documents"],
        "api": "click",
    },
    "click_delete_clickwrap": {
        "method": "DELETE",
        "path": "/v1/accounts/{account_id}/clickwraps/{clickwrap_id}",
        "description": "Delete a clickwrap",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "clickwrap_id": {"type": "str", "location": "path", "description": "Clickwrap ID"},
        },
        "required": ["account_id", "clickwrap_id"],
        "api": "click",
    },
    "click_get_clickwrap_agreements": {
        "method": "GET",
        "path": "/v1/accounts/{account_id}/clickwraps/{clickwrap_id}/users",
        "description": "Get clickwrap agreements (user acceptances)",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "clickwrap_id": {"type": "str", "location": "path", "description": "Clickwrap ID"},
        },
        "required": ["account_id", "clickwrap_id"],
        "api": "click",
    },
    "click_get_service_info": {
        "method": "GET",
        "path": "/v1/accounts/{account_id}/service_information",
        "description": "Get Click service information for the account",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
        },
        "required": ["account_id"],
        "api": "click",
    },

    # ========================================================================
    # MONITOR
    # ========================================================================
    "monitor_get_stream": {
        "method": "GET",
        "path": "/api/v2.0/datasets/monitor/stream",
        "description": "Get monitor audit stream events",
        "parameters": {
            "cursor": {"type": "str | None", "location": "query", "description": "Cursor for pagination"},
            "limit": {"type": "int | None", "location": "query", "description": "Number of events to return"},
        },
        "required": [],
        "api": "monitor",
    },

    # ========================================================================
    # WEBFORMS
    # ========================================================================
    "webforms_list_forms": {
        "method": "GET",
        "path": "/accounts/{account_id}/forms",
        "description": "List web forms for the account",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "search": {"type": "str | None", "location": "query", "description": "Search filter"},
            "state": {"type": "str | None", "location": "query", "description": "Filter by form state"},
            "status": {"type": "str | None", "location": "query", "description": "Filter by form status"},
        },
        "required": ["account_id"],
        "api": "webforms",
    },
    "webforms_get_form": {
        "method": "GET",
        "path": "/accounts/{account_id}/forms/{form_id}",
        "description": "Get a specific web form",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "form_id": {"type": "str", "location": "path", "description": "Form ID"},
        },
        "required": ["account_id", "form_id"],
        "api": "webforms",
    },
    "webforms_list_instances": {
        "method": "GET",
        "path": "/accounts/{account_id}/forms/{form_id}/instances",
        "description": "List instances of a web form",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "form_id": {"type": "str", "location": "path", "description": "Form ID"},
        },
        "required": ["account_id", "form_id"],
        "api": "webforms",
    },
    "webforms_get_instance": {
        "method": "GET",
        "path": "/accounts/{account_id}/forms/{form_id}/instances/{instance_id}",
        "description": "Get a specific web form instance",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "form_id": {"type": "str", "location": "path", "description": "Form ID"},
            "instance_id": {"type": "str", "location": "path", "description": "Instance ID"},
        },
        "required": ["account_id", "form_id", "instance_id"],
        "api": "webforms",
    },
    "webforms_create_instance": {
        "method": "POST",
        "path": "/accounts/{account_id}/forms/{form_id}/instances",
        "description": "Create a new web form instance",
        "parameters": {
            "account_id": {"type": "str", "location": "path", "description": "Account ID"},
            "form_id": {"type": "str", "location": "path", "description": "Form ID"},
            "clientUserId": {"type": "str | None", "location": "body", "description": "Client user ID"},
            "tags": {"type": "list[str] | None", "location": "body", "description": "Tags for the instance"},
            "returnUrl": {"type": "str | None", "location": "body", "description": "Return URL after form completion"},
        },
        "required": ["account_id", "form_id"],
        "api": "webforms",
    },
}

# Map api key -> (base_url_constant_name, lazy_accessor_name)
API_INFO = {
    "admin":    ("ADMIN_BASE_URL",    "_get_admin_http"),
    "rooms":    ("ROOMS_BASE_URL",    "_get_rooms_http"),
    "click":    ("CLICK_BASE_URL",    "_get_click_http"),
    "monitor":  ("MONITOR_BASE_URL",  "_get_monitor_http"),
    "webforms": ("WEBFORMS_BASE_URL", "_get_webforms_http"),
}


# ---------------------------------------------------------------------------
# Code emission helpers
# ---------------------------------------------------------------------------


def _emit_sdk_method(sig: str, body: str, doc: str) -> str:
    """Emit a synchronous SDK-based method."""
    lines = []
    lines.append(f"    def {sig}:")
    lines.append(f'        """{doc}"""')
    lines.append("        try:")
    # Body lines are already indented at 12 spaces
    for line in body.split("\n"):
        lines.append(line)
    lines.append("        except Exception as e:")
    lines.append('            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")')
    return "\n".join(lines)


def _sanitize_param(name: str) -> str:
    """Sanitize parameter name for Python (replace reserved words)."""
    reserved = {"type", "from", "class", "import", "global", "return", "lambda"}
    if name in reserved:
        return f"{name}_"
    return name


def _build_query_params(endpoint: dict) -> List[str]:
    """Build query_params dict construction lines."""
    lines = ["        query_params: dict[str, object] = {}"]
    for param_name, param_info in endpoint["parameters"].items():
        if param_info["location"] != "query":
            continue
        sanitized = _sanitize_param(param_name)
        if param_name in endpoint["required"]:
            lines.append(f"        query_params['{param_name}'] = {sanitized}")
        else:
            lines.append(f"        if {sanitized} is not None:")
            lines.append(f"            query_params['{param_name}'] = {sanitized}")
    return lines


def _build_path_format(path: str, endpoint: dict) -> str:
    """Build URL construction line with path parameter formatting."""
    path_params = [
        p for p, info in endpoint["parameters"].items()
        if info["location"] == "path"
    ]
    if path_params:
        # Use f-string with direct variable references
        return f'        url = base_url + f"{path}"'
    else:
        return f'        url = base_url + "{path}"'


def _build_body_params(endpoint: dict) -> List[str]:
    """Build request body dict construction lines."""
    body_params = [
        (name, info) for name, info in endpoint["parameters"].items()
        if info["location"] == "body"
    ]
    if not body_params:
        return []

    lines = ["        body: dict[str, object] = {}"]
    for param_name, param_info in body_params:
        sanitized = _sanitize_param(param_name)
        if param_name in endpoint["required"]:
            lines.append(f"        body['{param_name}'] = {sanitized}")
        else:
            lines.append(f"        if {sanitized} is not None:")
            lines.append(f"            body['{param_name}'] = {sanitized}")
    return lines


def _emit_http_method(method_name: str, endpoint: dict) -> str:
    """Emit an async HTTP-based method following the ClickUp pattern."""
    api_key = endpoint["api"]
    _, accessor = API_INFO[api_key]

    # Build method signature
    params = ["self"]
    # Required params (non-body first, then body)
    for param_name in endpoint["required"]:
        if param_name in endpoint["parameters"]:
            pinfo = endpoint["parameters"][param_name]
            sanitized = _sanitize_param(param_name)
            ptype = pinfo["type"]
            params.append(f"{sanitized}: {ptype}")

    # Optional params
    for param_name, pinfo in endpoint["parameters"].items():
        if param_name not in endpoint["required"]:
            sanitized = _sanitize_param(param_name)
            ptype = pinfo["type"]
            if "| None" not in ptype:
                ptype = f"{ptype} | None"
            params.append(f"{sanitized}: {ptype} = None")

    sig_params = ",\n        ".join(params)
    api_label = endpoint["api"].capitalize()

    lines = []
    lines.append(f"    async def {method_name}(")
    lines.append(f"        {sig_params}")
    lines.append("    ) -> DocuSignResponse:")
    lines.append(f'        """{endpoint["description"]}  [{api_label}]')
    lines.append("")

    # Args section
    if endpoint["parameters"]:
        lines.append("        Args:")
        for pname, pinfo in endpoint["parameters"].items():
            sanitized = _sanitize_param(pname)
            lines.append(f"            {sanitized}: {pinfo['description']}")
        lines.append("")
    lines.append("        Returns:")
    lines.append("            DocuSignResponse with operation result")
    lines.append('        """')

    # Query params
    has_query = any(
        info["location"] == "query"
        for info in endpoint["parameters"].values()
    )
    if has_query:
        lines.extend(_build_query_params(endpoint))
        lines.append("")

    # URL construction
    base_url_const, _ = API_INFO[api_key]
    lines.append(f"        base_url = self.{base_url_const}")
    lines.append(_build_path_format(endpoint["path"], endpoint))

    # Body
    body_lines = _build_body_params(endpoint)
    if body_lines:
        lines.append("")
        lines.extend(body_lines)

    # Request execution
    lines.append("")
    lines.append("        try:")
    lines.append("            request = HTTPRequest(")
    lines.append(f'                method="{endpoint["method"]}",')
    lines.append("                url=url,")
    lines.append('                headers={"Content-Type": "application/json"},')
    if has_query:
        lines.append("                query=query_params,")
    if body_lines:
        lines.append("                body=body,")
    lines.append("            )")
    lines.append(f"            response = await self.{accessor}().execute(request)  # type: ignore[reportUnknownMemberType]")
    lines.append("            response_data = response.json() if response.text() else None")
    lines.append("            return DocuSignResponse(")
    lines.append("                success=response.status < HTTP_ERROR_THRESHOLD,")
    lines.append("                data=response_data,")
    lines.append(f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"')
    lines.append("            )")
    lines.append("        except Exception as e:")
    lines.append(f'            return DocuSignResponse(success=False, error=str(e), message="Failed to execute {method_name}")')

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Class assembly
# ---------------------------------------------------------------------------


def build_class() -> str:
    """Build the complete DocuSignDataSource class source code."""
    parts: List[str] = []

    # File header
    parts.append("""\
# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportArgumentType=false
\"\"\"
DocuSign Unified DataSource - Auto-generated API wrapper

Covers all DocuSign APIs:
- eSignature (SDK-based via docusign-esign)
- Admin, Rooms, Click, Monitor, WebForms (HTTP-based)

All eSign methods are synchronous (SDK). All HTTP methods are async.
\"\"\"

from __future__ import annotations

from typing import cast

import docusign_esign  # type: ignore[reportMissingImports]
from docusign_esign import ApiClient  # type: ignore[reportMissingImports]

from app.sources.client.docusign.docusign import DocuSignClient, DocuSignResponse
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class DocuSignDataSource:
    \"\"\"DocuSign Unified DataSource

    Provides wrapper methods for all DocuSign API operations:
    - eSignature: Envelopes, Templates, Users, Folders, Brands (SDK-based, sync)
    - Admin: Organizations, Users, Groups, Permissions (HTTP-based, async)
    - Rooms: Rooms, Documents, Templates, Roles (HTTP-based, async)
    - Click: Clickwraps, Agreements, Service Info (HTTP-based, async)
    - Monitor: Audit stream events (HTTP-based, async)
    - WebForms: Forms, Instances (HTTP-based, async)

    All methods return DocuSignResponse objects.
    \"\"\"

    # Base URLs for non-eSign APIs
    ADMIN_BASE_URL = "https://api-d.docusign.net/management"
    ROOMS_BASE_URL = "https://demo.rooms.docusign.com/restapi"
    CLICK_BASE_URL = "https://demo.docusign.net/clickapi"
    MONITOR_BASE_URL = "https://lens-d.docusign.net"
    WEBFORMS_BASE_URL = "https://apps-d.docusign.com/api/webforms/v1.1"

    def __init__(self, client: DocuSignClient) -> None:
        \"\"\"Initialize with DocuSignClient.

        Args:
            client: DocuSignClient instance with configured authentication
        \"\"\"
        self._client = client
        # eSign SDK
        self._sdk: ApiClient = cast(ApiClient, client.get_client().get_sdk())
        self._account_id: str = client.get_client().get_account_id()
        # Lazy HTTP clients for each API
        self._admin_http: HTTPClient | None = None
        self._rooms_http: HTTPClient | None = None
        self._click_http: HTTPClient | None = None
        self._monitor_http: HTTPClient | None = None
        self._webforms_http: HTTPClient | None = None

        # Lazy SDK API instances
        self._envelopes_api: docusign_esign.EnvelopesApi | None = None
        self._templates_api: docusign_esign.TemplatesApi | None = None
        self._users_api: docusign_esign.UsersApi | None = None
        self._folders_api: docusign_esign.FoldersApi | None = None
        self._accounts_api: docusign_esign.AccountsApi | None = None

    # ---- lazy HTTP client accessors ----

    def _get_admin_http(self) -> HTTPClient:
        if self._admin_http is None:
            self._admin_http = self._client.get_client().get_http_client(self.ADMIN_BASE_URL)
        return self._admin_http

    def _get_rooms_http(self) -> HTTPClient:
        if self._rooms_http is None:
            self._rooms_http = self._client.get_client().get_http_client(self.ROOMS_BASE_URL)
        return self._rooms_http

    def _get_click_http(self) -> HTTPClient:
        if self._click_http is None:
            self._click_http = self._client.get_client().get_http_client(self.CLICK_BASE_URL)
        return self._click_http

    def _get_monitor_http(self) -> HTTPClient:
        if self._monitor_http is None:
            self._monitor_http = self._client.get_client().get_http_client(self.MONITOR_BASE_URL)
        return self._monitor_http

    def _get_webforms_http(self) -> HTTPClient:
        if self._webforms_http is None:
            self._webforms_http = self._client.get_client().get_http_client(self.WEBFORMS_BASE_URL)
        return self._webforms_http

    # ---- lazy SDK API accessors ----

    @property
    def envelopes_api(self) -> docusign_esign.EnvelopesApi:
        if self._envelopes_api is None:
            self._envelopes_api = docusign_esign.EnvelopesApi(self._sdk)
        return self._envelopes_api

    @property
    def templates_api(self) -> docusign_esign.TemplatesApi:
        if self._templates_api is None:
            self._templates_api = docusign_esign.TemplatesApi(self._sdk)
        return self._templates_api

    @property
    def users_api(self) -> docusign_esign.UsersApi:
        if self._users_api is None:
            self._users_api = docusign_esign.UsersApi(self._sdk)
        return self._users_api

    @property
    def folders_api(self) -> docusign_esign.FoldersApi:
        if self._folders_api is None:
            self._folders_api = docusign_esign.FoldersApi(self._sdk)
        return self._folders_api

    @property
    def accounts_api(self) -> docusign_esign.AccountsApi:
        if self._accounts_api is None:
            self._accounts_api = docusign_esign.AccountsApi(self._sdk)
        return self._accounts_api

    # ---- helpers ----

    def get_data_source(self) -> 'DocuSignDataSource':
        \"\"\"Return the data source instance.\"\"\"
        return self

    def get_client(self) -> DocuSignClient:
        \"\"\"Return the underlying DocuSignClient.\"\"\"
        return self._client

    @staticmethod
    def _params(**kwargs: object) -> dict[str, object]:
        \"\"\"Filter out Nones to avoid overriding SDK defaults.\"\"\"
        out: dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)) and len(v) == 0:
                continue
            out[k] = v
        return out

    # ---- eSign SDK methods (synchronous) ----
""")

    # Emit SDK methods
    for sig, body, doc in SDK_METHODS:
        parts.append(_emit_sdk_method(sig, body, doc))
        parts.append("")

    # Section header for HTTP methods
    parts.append("    # ---- HTTP-based methods (async) ----\n")

    # Emit HTTP methods
    for method_name, endpoint in DOCUSIGN_HTTP_ENDPOINTS.items():
        parts.append(_emit_http_method(method_name, endpoint))
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Generate and write DocuSignDataSource."""
    out_path = DEFAULT_OUT

    # Support --out flag
    if "--out" in sys.argv:
        idx = sys.argv.index("--out")
        if idx + 1 < len(sys.argv):
            out_path = sys.argv[idx + 1]

    code = build_class()

    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code, encoding="utf-8")

    sdk_count = len(SDK_METHODS)
    http_count = len(DOCUSIGN_HTTP_ENDPOINTS)
    total = sdk_count + http_count

    print(f"Generated DocuSignDataSource -> {out_path}  ({total} methods)")
    print(f"  - eSign SDK methods: {sdk_count}")
    print(f"  - HTTP methods: {http_count}")

    # Breakdown by API
    api_counts: Dict[str, int] = {}
    for ep in DOCUSIGN_HTTP_ENDPOINTS.values():
        api = ep["api"]
        api_counts[api] = api_counts.get(api, 0) + 1
    for api, count in sorted(api_counts.items()):
        print(f"    - {api}: {count}")


if __name__ == "__main__":
    main()
