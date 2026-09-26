import json
import logging
from collections.abc import Awaitable
from http import HTTPStatus
from typing import Any, Optional

import httpx

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolDefinition,
    ToolsetBuilder,
    ToolsetCategory,
)
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.lumos.lumos import LumosClient
from app.sources.external.lumos.lumos import LumosDataSource

logger = logging.getLogger(__name__)

_MAX_DETAIL_CHARS = 300


class _LumosInputError(ValueError):
    """Bad tool arguments; the message says what to change and is safe to show the agent."""


def _as_object(value: dict[str, Any] | str | None, field: str) -> dict[str, Any] | None:
    """Lumos needs a JSON object here; models often send one as JSON text."""
    if value is None or isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        parsed = None
    if not isinstance(parsed, dict):
        raise _LumosInputError(
            f"{field} must be a JSON object, not plain text. Pass it as an object built from "
            "Lumos condition operators such as equals, in, and, or."
            if field == "access_condition"
            else f"{field} must be a JSON object, not plain text. Pass it as an object and try again."
        )
    return parsed


def _failure(message: str) -> tuple[bool, str]:
    return False, json.dumps({"error": message})


def _detail(response: HTTPResponse) -> object:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("detail") if isinstance(body, dict) else None


def _validation_problems(detail: object) -> str:
    """FastAPI-style ``detail`` entries as "field: message"; Lumos returns these for bad arguments."""
    if not isinstance(detail, list):
        return detail if isinstance(detail, str) and len(detail) <= _MAX_DETAIL_CHARS else ""
    problems = []
    for entry in detail:
        if not isinstance(entry, dict) or not isinstance(entry.get("msg"), str):
            continue
        loc = [str(part) for part in entry.get("loc") or [] if part not in ("body", "query", "path")]
        problems.append(f"{'.'.join(loc)}: {entry['msg']}" if loc else entry["msg"])
    return "; ".join(problems)


def _lumos_error_message(response: HTTPResponse) -> str:
    """Plain-language failure the agent can relay; never the raw body, which may be an HTML error page."""
    status = response.status
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        retry_after = str(response.headers.get("retry-after") or "").strip()
        wait = f"Wait {retry_after} seconds" if retry_after.isdigit() else "Wait a minute"
        return f"Lumos is receiving too many requests right now. {wait} and try again."
    if status == HTTPStatus.UNAUTHORIZED:
        return (
            "Lumos did not accept the saved API key. Ask an admin to check the Lumos API key "
            "in Settings > Toolsets, then try again."
        )
    if status == HTTPStatus.FORBIDDEN:
        return (
            "The Lumos API key does not have permission to do this. Ask a Lumos admin to grant "
            "that access, or try a different action."
        )
    if status == HTTPStatus.NOT_FOUND:
        return (
            "Lumos could not find that item. Check the ID, or use a list tool such as list_users, "
            "list_platforms or list_groups to find the right one."
        )
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return "Lumos is having a temporary problem. Try again in a few minutes."
    problems = _validation_problems(_detail(response))
    if problems:
        return f"Lumos rejected the request: {problems}. Fix those arguments and try again."
    return f"Lumos rejected the request (status {status}). Check the arguments and try again."


def _more_pages(data: object) -> dict[str, object]:
    """A Lumos page that is not the last one says so, so the agent does not treat it as the full list."""
    if not isinstance(data, dict):
        return {}
    page, pages, total = data.get("page"), data.get("pages"), data.get("total")
    if not (isinstance(page, int) and isinstance(pages, int) and page < pages):
        return {}
    shown = len(data.get("items") or [])
    return {
        "next_page": page + 1,
        "note": (
            f"This is page {page} of {pages}: {shown} of {total} results. "
            f"Call again with page={page + 1} for more."
        ),
    }


def _reply_data(response: HTTPResponse) -> object:
    # The change already happened; an unreadable body must not turn it into a reported failure.
    if response.status == HTTPStatus.NO_CONTENT or not response.bytes():
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


tools: list[ToolDefinition] = [
    ToolDefinition(
        name="list_platforms",
        description="List all platforms (apps) from Lumos",
        parameters=[
            {"name": "name_search", "type": "string", "description": "Optional platform name search query", "required": False},
            {"name": "page", "type": "integer", "description": "Page number", "required": False},
            {"name": "size", "type": "integer", "description": "Page size", "required": False},
        ],
        tags=["platforms", "apps", "read"],
    ),
    ToolDefinition(
        name="get_platform",
        description="Get a platform (app) by ID",
        parameters=[
            {"name": "app_id", "type": "string", "description": "Lumos app ID", "required": True},
        ],
        tags=["platforms", "apps", "read"],
    ),
    ToolDefinition(
        name="list_users",
        description="List users in Lumos",
        parameters=[
            {"name": "search_term", "type": "string", "description": "Optional user search query", "required": False},
            {"name": "page", "type": "integer", "description": "Page number", "required": False},
            {"name": "size", "type": "integer", "description": "Page size", "required": False},
        ],
        tags=["users", "read"],
    ),
    ToolDefinition(
        name="get_user",
        description="Get a user by ID",
        parameters=[
            {"name": "user_id", "type": "string", "description": "Lumos user ID", "required": True},
        ],
        tags=["users", "read"],
    ),
    ToolDefinition(
        name="get_user_accounts",
        description="Get accounts assigned to a user",
        parameters=[
            {"name": "user_id", "type": "string", "description": "Lumos user ID", "required": True},
            {"name": "page", "type": "integer", "description": "Page number", "required": False},
            {"name": "size", "type": "integer", "description": "Page size", "required": False},
        ],
        tags=["users", "accounts", "read"],
    ),
    ToolDefinition(
        name="get_user_roles",
        description="Get roles assigned to a user",
        parameters=[
            {"name": "user_id", "type": "string", "description": "Lumos user ID", "required": True},
        ],
        tags=["users", "roles", "permissions", "read"],
    ),
    ToolDefinition(
        name="list_groups",
        description="List groups in Lumos",
        parameters=[
            {"name": "name", "type": "string", "description": "Optional group name filter", "required": False},
            {"name": "app_id", "type": "string", "description": "Optional app ID filter", "required": False},
            {"name": "page", "type": "integer", "description": "Page number", "required": False},
            {"name": "size", "type": "integer", "description": "Page size", "required": False},
        ],
        tags=["groups", "read"],
    ),
    ToolDefinition(
        name="get_group",
        description="Get a group by ID",
        parameters=[
            {"name": "group_id", "type": "string", "description": "Lumos group ID", "required": True},
        ],
        tags=["groups", "read"],
    ),
    ToolDefinition(
        name="get_group_members",
        description="Get users in a group",
        parameters=[
            {"name": "group_id", "type": "string", "description": "Lumos group ID", "required": True},
            {"name": "page", "type": "integer", "description": "Page number", "required": False},
            {"name": "size", "type": "integer", "description": "Page size", "required": False},
        ],
        tags=["groups", "members", "read"],
    ),
    ToolDefinition(
        name="list_permissions",
        description="List requestable permissions across app store",
        parameters=[
            {"name": "app_id", "type": "string", "description": "Optional app ID filter", "required": False},
            {"name": "search_term", "type": "string", "description": "Optional permission search query", "required": False},
            {"name": "page", "type": "integer", "description": "Page number", "required": False},
            {"name": "size", "type": "integer", "description": "Page size", "required": False},
        ],
        tags=["permissions", "read"],
    ),
    ToolDefinition(
        name="list_app_permissions",
        description="List requestable permissions for a specific app",
        parameters=[
            {"name": "app_id", "type": "string", "description": "Lumos app ID", "required": True},
            {"name": "search_term", "type": "string", "description": "Optional permission search query", "required": False},
            {"name": "page", "type": "integer", "description": "Page number", "required": False},
            {"name": "size", "type": "integer", "description": "Page size", "required": False},
        ],
        tags=["permissions", "apps", "read"],
    ),
    ToolDefinition(
        name="list_access_requests",
        description="List access requests",
        parameters=[
            {"name": "user_id", "type": "string", "description": "Optional user ID filter", "required": False},
            {"name": "statuses", "type": "array", "description": "Optional status filters", "required": False},
            {"name": "page", "type": "integer", "description": "Page number", "required": False},
            {"name": "size", "type": "integer", "description": "Page size", "required": False},
        ],
        tags=["access", "requests", "read"],
    ),
    ToolDefinition(
        name="get_access_request",
        description="Get a single access request by ID",
        parameters=[
            {"name": "request_id", "type": "string", "description": "Access request ID", "required": True},
        ],
        tags=["access", "requests", "read"],
    ),
]

tools.extend([
    ToolDefinition(
        name="create_access_request",
        description="Create an access request",
        parameters=[
            {"name": "app_id", "type": "string", "description": "Lumos app ID", "required": True},
            {"name": "target_user_id", "type": "string", "description": "Optional target user ID", "required": False},
            {"name": "business_justification", "type": "string", "description": "Optional business justification", "required": False},
            {"name": "requestable_permission_ids", "type": "array", "description": "Optional permission IDs to request", "required": False},
            {"name": "confirm", "type": "boolean", "description": "Set true to execute mutation", "required": False},
        ],
        tags=["access", "requests", "write"],
    ),
    ToolDefinition(
        name="cancel_access_request",
        description="Cancel an access request by ID",
        parameters=[
            {"name": "request_id", "type": "string", "description": "Access request ID", "required": True},
            {"name": "reason", "type": "string", "description": "Optional cancellation reason", "required": False},
            {"name": "confirm", "type": "boolean", "description": "Set true to execute mutation", "required": False},
        ],
        tags=["access", "requests", "write"],
    ),
    ToolDefinition(
        name="list_access_policies",
        description="List access policies",
        parameters=[
            {"name": "name", "type": "string", "description": "Optional policy name filter", "required": False},
            {"name": "page", "type": "integer", "description": "Page number", "required": False},
            {"name": "size", "type": "integer", "description": "Page size", "required": False},
        ],
        tags=["access", "policies", "read"],
    ),
    ToolDefinition(
        name="get_access_policy",
        description="Get access policy by ID",
        parameters=[
            {"name": "access_policy_id", "type": "string", "description": "Access policy ID", "required": True},
        ],
        tags=["access", "policies", "read"],
    ),
    ToolDefinition(
        name="create_access_policy",
        description="Create access policy",
        parameters=[
            {"name": "name", "type": "string", "description": "Policy name", "required": True},
            {"name": "business_justification", "type": "string", "description": "Policy justification", "required": True},
            {"name": "apps", "type": "array", "description": "App policy definitions", "required": True},
            {"name": "confirm", "type": "boolean", "description": "Set true to execute mutation", "required": False},
        ],
        tags=["access", "policies", "write", "admin"],
    ),
    ToolDefinition(
        name="update_access_policy",
        description="Update access policy",
        parameters=[
            {"name": "access_policy_id", "type": "string", "description": "Access policy ID", "required": True},
            {"name": "name", "type": "string", "description": "Policy name", "required": True},
            {"name": "business_justification", "type": "string", "description": "Policy justification", "required": True},
            {"name": "apps", "type": "array", "description": "App policy definitions", "required": True},
            {"name": "confirm", "type": "boolean", "description": "Set true to execute mutation", "required": False},
        ],
        tags=["access", "policies", "write", "admin"],
    ),
    ToolDefinition(
        name="delete_access_policy",
        description="Delete access policy",
        parameters=[
            {"name": "access_policy_id", "type": "string", "description": "Access policy ID", "required": True},
            {"name": "confirm", "type": "boolean", "description": "Set true to execute mutation", "required": False},
        ],
        tags=["access", "policies", "write", "admin"],
    ),
    ToolDefinition(
        name="create_requestable_permission",
        description="Create requestable permission",
        parameters=[
            {"name": "app_id", "type": "string", "description": "Lumos app ID", "required": True},
            {"name": "label", "type": "string", "description": "Permission label", "required": True},
            {"name": "confirm", "type": "boolean", "description": "Set true to execute mutation", "required": False},
        ],
        tags=["permissions", "write", "admin"],
    ),
    ToolDefinition(
        name="get_requestable_permission",
        description="Get requestable permission by ID",
        parameters=[
            {"name": "permission_id", "type": "string", "description": "Permission ID", "required": True},
        ],
        tags=["permissions", "read"],
    ),
    ToolDefinition(
        name="update_requestable_permission",
        description="Update requestable permission",
        parameters=[
            {"name": "permission_id", "type": "string", "description": "Permission ID", "required": True},
            {"name": "label", "type": "string", "description": "Optional new label", "required": False},
            {"name": "request_config", "type": "object", "description": "Optional request config", "required": False},
            {"name": "confirm", "type": "boolean", "description": "Set true to execute mutation", "required": False},
        ],
        tags=["permissions", "write", "admin"],
    ),
    ToolDefinition(
        name="delete_requestable_permission",
        description="Delete requestable permission",
        parameters=[
            {"name": "permission_id", "type": "string", "description": "Permission ID", "required": True},
            {"name": "confirm", "type": "boolean", "description": "Set true to execute mutation", "required": False},
        ],
        tags=["permissions", "write", "admin"],
    ),
    ToolDefinition(
        name="add_user_role",
        description="Add role to user",
        parameters=[
            {"name": "user_id", "type": "string", "description": "Lumos user ID", "required": True},
            {"name": "role_name", "type": "string", "description": "Role name", "required": True},
            {"name": "confirm", "type": "boolean", "description": "Set true to execute mutation", "required": False},
        ],
        tags=["roles", "users", "write", "restricted"],
    ),
    ToolDefinition(
        name="remove_user_role",
        description="Remove role from user",
        parameters=[
            {"name": "user_id", "type": "string", "description": "Lumos user ID", "required": True},
            {"name": "role_name", "type": "string", "description": "Role name", "required": True},
            {"name": "confirm", "type": "boolean", "description": "Set true to execute mutation", "required": False},
        ],
        tags=["roles", "users", "write", "restricted"],
    ),
])


@ToolsetBuilder("Lumos")\
    .in_group("Identity & Access")\
    .with_description("Lumos integration for access governance, users, groups, and platform permissions")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            CommonFields.api_token("Lumos API Key", "lumos_your_api_key_here", field_name="apiKey")
        ])
    ])\
    .with_tools(tools)\
    .configure(lambda builder: builder.with_icon("/icons/connectors/lumos.svg"))\
    .build_decorator()
class Lumos:
    """Lumos toolset exposed to agents."""

    def __init__(self, client: LumosClient) -> None:
        self.client = LumosDataSource(client)

    async def _call(self, request: Awaitable[HTTPResponse], success_message: str) -> tuple[bool, str]:
        try:
            response = await request
        except httpx.TimeoutException:
            logger.warning("Lumos request timed out")
            return _failure("Lumos did not answer in time. Try again in a moment.")
        except httpx.HTTPError as exc:
            logger.warning("Lumos request failed before a reply: %s", type(exc).__name__)
            return _failure("Could not reach Lumos. Check the network connection and try again in a moment.")
        if HTTPStatus.OK <= response.status < HTTPStatus.MULTIPLE_CHOICES:
            data = _reply_data(response)
            return True, json.dumps({"message": success_message, "data": data, **_more_pages(data)})
        logger.warning("Lumos returned HTTP %s", response.status)
        return _failure(_lumos_error_message(response))

    def _confirm_mutation(self, confirm: bool, operation: str) -> Optional[tuple[bool, str]]:
        if confirm:
            return None
        return (
            False,
            json.dumps(
                {
                    "error": "confirmation_required",
                    "details": f"Set confirm=true to execute '{operation}'",
                }
            ),
        )

    @tool(
        path="/tools/lumos/list_platforms",
        short_description="List Lumos platforms (apps)",
        description="List all platforms (apps) registered in Lumos, with optional name search and pagination.",
        parameters=[
            ToolParameter(name="name_search", type=ParameterType.STRING, description="Optional platform name search query", required=False),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number", required=False, default=1),
            ToolParameter(name="size", type=ParameterType.INTEGER, description="Page size", required=False, default=25),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def list_platforms(
        self,
        name_search: Optional[str] = None,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> tuple[bool, str]:
        return await self._call(
            self.client.list_apps(name_search=name_search, page=page, size=size),
            "Fetched platforms successfully",
        )

    @tool(
        path="/tools/lumos/get_platform",
        short_description="Get a Lumos platform by ID",
        description="Get a single Lumos platform (app) by its ID.",
        parameters=[
            ToolParameter(name="app_id", type=ParameterType.STRING, description="Lumos app ID", required=True),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def get_platform(self, app_id: str) -> tuple[bool, str]:
        return await self._call(
            self.client.get_app(app_id=app_id),
            "Fetched platform successfully",
        )

    @tool(
        path="/tools/lumos/list_users",
        short_description="List Lumos users",
        description="List users in Lumos, with optional search and pagination.",
        parameters=[
            ToolParameter(name="search_term", type=ParameterType.STRING, description="Optional user search query", required=False),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number", required=False, default=1),
            ToolParameter(name="size", type=ParameterType.INTEGER, description="Page size", required=False, default=25),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def list_users(
        self,
        search_term: Optional[str] = None,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> tuple[bool, str]:
        return await self._call(
            self.client.list_users(search_term=search_term, page=page, size=size),
            "Fetched users successfully",
        )

    @tool(
        path="/tools/lumos/get_user",
        short_description="Get a Lumos user by ID",
        description="Get a single Lumos user by their ID.",
        parameters=[
            ToolParameter(name="user_id", type=ParameterType.STRING, description="Lumos user ID", required=True),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def get_user(self, user_id: str) -> tuple[bool, str]:
        return await self._call(
            self.client.get_user(user_id=user_id),
            "Fetched user successfully",
        )

    @tool(
        path="/tools/lumos/get_user_accounts",
        short_description="Get accounts assigned to a Lumos user",
        description="Get all accounts assigned to a specific Lumos user, with pagination.",
        parameters=[
            ToolParameter(name="user_id", type=ParameterType.STRING, description="Lumos user ID", required=True),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number", required=False, default=1),
            ToolParameter(name="size", type=ParameterType.INTEGER, description="Page size", required=False, default=25),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def get_user_accounts(
        self,
        user_id: str,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> tuple[bool, str]:
        return await self._call(
            self.client.get_user_accounts(user_id=user_id, page=page, size=size),
            "Fetched user accounts successfully",
        )

    @tool(
        path="/tools/lumos/get_user_roles",
        short_description="Get roles assigned to a Lumos user",
        description="Get all roles assigned to a specific Lumos user.",
        parameters=[
            ToolParameter(name="user_id", type=ParameterType.STRING, description="Lumos user ID", required=True),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def get_user_roles(self, user_id: str) -> tuple[bool, str]:
        return await self._call(
            self.client.get_user_roles_users_user_id_roles_get(user_id=user_id),
            "Fetched user roles successfully",
        )

    @tool(
        path="/tools/lumos/list_groups",
        short_description="List Lumos groups",
        description="List groups in Lumos, with optional name and app ID filters and pagination.",
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, description="Optional group name filter", required=False),
            ToolParameter(name="app_id", type=ParameterType.STRING, description="Optional app ID filter", required=False),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number", required=False, default=1),
            ToolParameter(name="size", type=ParameterType.INTEGER, description="Page size", required=False, default=25),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def list_groups(
        self,
        name: Optional[str] = None,
        app_id: Optional[str] = None,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> tuple[bool, str]:
        return await self._call(
            self.client.get_groups(name=name, app_id=app_id, page=page, size=size),
            "Fetched groups successfully",
        )

    @tool(
        path="/tools/lumos/get_group",
        short_description="Get a Lumos group by ID",
        description="Get a single Lumos group by its ID.",
        parameters=[
            ToolParameter(name="group_id", type=ParameterType.STRING, description="Lumos group ID", required=True),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def get_group(self, group_id: str) -> tuple[bool, str]:
        return await self._call(
            self.client.get_group(group_id=group_id),
            "Fetched group successfully",
        )

    @tool(
        path="/tools/lumos/get_group_members",
        short_description="Get members of a Lumos group",
        description="Get all users that belong to a specific Lumos group, with pagination.",
        parameters=[
            ToolParameter(name="group_id", type=ParameterType.STRING, description="Lumos group ID", required=True),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number", required=False, default=1),
            ToolParameter(name="size", type=ParameterType.INTEGER, description="Page size", required=False, default=25),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def get_group_members(
        self,
        group_id: str,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> tuple[bool, str]:
        return await self._call(
            self.client.get_group_membership(group_id=group_id, page=page, size=size),
            "Fetched group members successfully",
        )

    @tool(
        path="/tools/lumos/list_permissions",
        short_description="List Lumos requestable permissions",
        description="List requestable permissions across the Lumos app store, with optional app ID and search filters.",
        parameters=[
            ToolParameter(name="app_id", type=ParameterType.STRING, description="Optional app ID filter", required=False),
            ToolParameter(name="search_term", type=ParameterType.STRING, description="Optional permission search query", required=False),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number", required=False, default=1),
            ToolParameter(name="size", type=ParameterType.INTEGER, description="Page size", required=False, default=25),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def list_permissions(
        self,
        app_id: Optional[str] = None,
        search_term: Optional[str] = None,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> tuple[bool, str]:
        return await self._call(
            self.client.get_appstore_permissions_appstore_requestable_permissions_get(
                app_id=app_id,
                search_term=search_term,
                page=page,
                size=size,
            ),
            "Fetched permissions successfully",
        )

    @tool(
        path="/tools/lumos/list_app_permissions",
        short_description="List requestable permissions for a specific Lumos app",
        description="List requestable permissions for a specific app in Lumos, with optional search and pagination.",
        parameters=[
            ToolParameter(name="app_id", type=ParameterType.STRING, description="Lumos app ID", required=True),
            ToolParameter(name="search_term", type=ParameterType.STRING, description="Optional permission search query", required=False),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number", required=False, default=1),
            ToolParameter(name="size", type=ParameterType.INTEGER, description="Page size", required=False, default=25),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def list_app_permissions(
        self,
        app_id: str,
        search_term: Optional[str] = None,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> tuple[bool, str]:
        return await self._call(
            self.client.get_appstore_permissions_for_app_appstore_apps_app_id_requestable_permissions_get(
                app_id=app_id,
                search_term=search_term,
                page=page,
                size=size,
            ),
            "Fetched app permissions successfully",
        )

    @tool(
        path="/tools/lumos/list_access_requests",
        short_description="List Lumos access requests",
        description="List access requests in Lumos, with optional user ID and status filters and pagination.",
        parameters=[
            ToolParameter(name="user_id", type=ParameterType.STRING, description="Optional user ID filter", required=False),
            ToolParameter(name="statuses", type=ParameterType.ARRAY, description="Optional status filters", required=False, items={"type": "string"}),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number", required=False, default=1),
            ToolParameter(name="size", type=ParameterType.INTEGER, description="Page size", required=False, default=25),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def list_access_requests(
        self,
        user_id: Optional[str] = None,
        statuses: Optional[list[str]] = None,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> tuple[bool, str]:
        return await self._call(
            self.client.get_access_requests(
                user_id=user_id,
                statuses=statuses,
                page=page,
                size=size,
            ),
            "Fetched access requests successfully",
        )

    @tool(
        path="/tools/lumos/get_access_request",
        short_description="Get a Lumos access request by ID",
        description="Get a single Lumos access request by its ID.",
        parameters=[
            ToolParameter(name="request_id", type=ParameterType.STRING, description="Access request ID", required=True),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def get_access_request(self, request_id: str) -> tuple[bool, str]:
        return await self._call(
            self.client.get_access_request(id=request_id),
            "Fetched access request successfully",
        )

    @tool(
        path="/tools/lumos/create_access_request",
        short_description="Create a Lumos access request",
        description="Create a new access request in Lumos for a given app. Set confirm=true to execute the mutation.",
        parameters=[
            ToolParameter(name="app_id", type=ParameterType.STRING, description="Lumos app ID", required=True),
            ToolParameter(name="requester_user_id", type=ParameterType.STRING, description="Requester user ID", required=False),
            ToolParameter(name="target_user_id", type=ParameterType.STRING, description="Target user ID", required=False),
            ToolParameter(name="note", type=ParameterType.STRING, description="Optional note", required=False),
            ToolParameter(name="business_justification", type=ParameterType.STRING, description="Business justification", required=False),
            ToolParameter(name="expiration_in_seconds", type=ParameterType.INTEGER, description="Access expiration in seconds", required=False),
            ToolParameter(name="access_length", type=ParameterType.STRING, description="Access length", required=False),
            ToolParameter(name="requestable_permission_ids", type=ParameterType.ARRAY, description="Permission IDs to request", required=False, items={"type": "string"}),
            ToolParameter(name="confirm", type=ParameterType.BOOLEAN, description="Set true to execute mutation", required=False, default=False),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="write")],
    )
    async def create_access_request(
        self,
        app_id: str,
        requester_user_id: Optional[str] = None,
        target_user_id: Optional[str] = None,
        note: Optional[str] = None,
        business_justification: Optional[str] = None,
        expiration_in_seconds: Optional[int] = None,
        access_length: Optional[str] = None,
        requestable_permission_ids: Optional[list[str]] = None,
        confirm: bool = False,
    ) -> tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "create_access_request")
        if blocked:
            return blocked
        return await self._call(
            self.client.create_access_request(
                app_id=app_id,
                requester_user_id=requester_user_id,
                target_user_id=target_user_id,
                note=note,
                business_justification=business_justification,
                expiration_in_seconds=expiration_in_seconds,
                access_length=access_length,
                requestable_permission_ids=requestable_permission_ids,
            ),
            "Created access request successfully",
        )

    @tool(
        path="/tools/lumos/cancel_access_request",
        short_description="Cancel a Lumos access request",
        description="Cancel an existing Lumos access request by its ID. Set confirm=true to execute the mutation.",
        parameters=[
            ToolParameter(name="request_id", type=ParameterType.STRING, description="Access request ID", required=True),
            ToolParameter(name="reason", type=ParameterType.STRING, description="Cancellation reason", required=False),
            ToolParameter(name="confirm", type=ParameterType.BOOLEAN, description="Set true to execute mutation", required=False, default=False),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="write")],
    )
    async def cancel_access_request(
        self,
        request_id: str,
        reason: Optional[str] = None,
        confirm: bool = False,
    ) -> tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "cancel_access_request")
        if blocked:
            return blocked
        return await self._call(
            self.client.cancel_access_request(id=request_id, reason=reason),
            "Cancelled access request successfully",
        )

    @tool(
        path="/tools/lumos/list_access_policies",
        short_description="List Lumos access policies",
        description="List access policies in Lumos, with optional name filter and pagination.",
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, description="Optional policy name filter", required=False),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number", required=False, default=1),
            ToolParameter(name="size", type=ParameterType.INTEGER, description="Page size", required=False, default=25),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def list_access_policies(
        self,
        name: Optional[str] = None,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> tuple[bool, str]:
        return await self._call(
            self.client.get_access_policies(name=name, page=page, size=size),
            "Fetched access policies successfully",
        )

    @tool(
        path="/tools/lumos/get_access_policy",
        short_description="Get a Lumos access policy by ID",
        description="Get a single Lumos access policy by its ID.",
        parameters=[
            ToolParameter(name="access_policy_id", type=ParameterType.STRING, description="Access policy ID", required=True),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def get_access_policy(self, access_policy_id: str) -> tuple[bool, str]:
        return await self._call(
            self.client.get_access_policy(access_policy_id=access_policy_id),
            "Fetched access policy successfully",
        )

    @tool(
        path="/tools/lumos/create_access_policy",
        short_description="Create a Lumos access policy",
        description="Create a new access policy in Lumos. Set confirm=true to execute the mutation.",
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, description="Policy name", required=True),
            ToolParameter(name="business_justification", type=ParameterType.STRING, description="Policy justification", required=True),
            ToolParameter(name="apps", type=ParameterType.ARRAY, description="App policy definitions", required=True, items={"type": "object"}),
            ToolParameter(name="access_condition", type=ParameterType.OBJECT, description="Lumos condition object deciding who the policy applies to (operators such as equals, in, and, or, not). Required unless is_everyone_condition is true.", required=False),
            ToolParameter(name="is_everyone_condition", type=ParameterType.BOOLEAN, description="Whether policy applies to everyone", required=False),
            ToolParameter(name="confirm", type=ParameterType.BOOLEAN, description="Set true to execute mutation", required=False, default=False),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="write")],
    )
    async def create_access_policy(
        self,
        name: str,
        business_justification: str,
        apps: list[dict],
        access_condition: Optional[dict[str, Any] | str] = None,
        is_everyone_condition: Optional[bool] = None,
        confirm: bool = False,
    ) -> tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "create_access_policy")
        if blocked:
            return blocked
        try:
            access_condition = _as_object(access_condition, "access_condition")
        except _LumosInputError as exc:
            return _failure(str(exc))
        return await self._call(
            self.client.create_access_policy(
                name=name,
                business_justification=business_justification,
                apps=apps,
                access_condition=access_condition,
                is_everyone_condition=is_everyone_condition,
            ),
            "Created access policy successfully",
        )

    @tool(
        path="/tools/lumos/update_access_policy",
        short_description="Update a Lumos access policy",
        description="Update an existing Lumos access policy. Set confirm=true to execute the mutation.",
        parameters=[
            ToolParameter(name="access_policy_id", type=ParameterType.STRING, description="Access policy ID", required=True),
            ToolParameter(name="name", type=ParameterType.STRING, description="Policy name", required=True),
            ToolParameter(name="business_justification", type=ParameterType.STRING, description="Policy justification", required=True),
            ToolParameter(name="apps", type=ParameterType.ARRAY, description="App policy definitions", required=True, items={"type": "object"}),
            ToolParameter(name="access_condition", type=ParameterType.OBJECT, description="Lumos condition object deciding who the policy applies to (operators such as equals, in, and, or, not). Required unless is_everyone_condition is true.", required=False),
            ToolParameter(name="is_everyone_condition", type=ParameterType.BOOLEAN, description="Whether policy applies to everyone", required=False),
            ToolParameter(name="confirm", type=ParameterType.BOOLEAN, description="Set true to execute mutation", required=False, default=False),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="write")],
    )
    async def update_access_policy(
        self,
        access_policy_id: str,
        name: str,
        business_justification: str,
        apps: list[dict],
        access_condition: Optional[dict[str, Any] | str] = None,
        is_everyone_condition: Optional[bool] = None,
        confirm: bool = False,
    ) -> tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "update_access_policy")
        if blocked:
            return blocked
        try:
            access_condition = _as_object(access_condition, "access_condition")
        except _LumosInputError as exc:
            return _failure(str(exc))
        return await self._call(
            self.client.update_access_policy(
                access_policy_id=access_policy_id,
                name=name,
                business_justification=business_justification,
                apps=apps,
                access_condition=access_condition,
                is_everyone_condition=is_everyone_condition,
            ),
            "Updated access policy successfully",
        )

    @tool(
        path="/tools/lumos/delete_access_policy",
        short_description="Delete a Lumos access policy",
        description="Delete a Lumos access policy by its ID. Set confirm=true to execute the mutation.",
        parameters=[
            ToolParameter(name="access_policy_id", type=ParameterType.STRING, description="Access policy ID", required=True),
            ToolParameter(name="confirm", type=ParameterType.BOOLEAN, description="Set true to execute mutation", required=False, default=False),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="write")],
    )
    async def delete_access_policy(self, access_policy_id: str, confirm: bool = False) -> tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "delete_access_policy")
        if blocked:
            return blocked
        return await self._call(
            self.client.delete_access_policy(access_policy_id=access_policy_id),
            "Deleted access policy successfully",
        )

    @tool(
        path="/tools/lumos/create_requestable_permission",
        short_description="Create a requestable permission in Lumos",
        description="Create a new requestable permission for an app in Lumos. Set confirm=true to execute the mutation.",
        parameters=[
            ToolParameter(name="app_id", type=ParameterType.STRING, description="Lumos app ID", required=True),
            ToolParameter(name="label", type=ParameterType.STRING, description="Permission label", required=True),
            ToolParameter(name="include_inherited_configs", type=ParameterType.BOOLEAN, description="Include inherited configurations", required=False),
            ToolParameter(name="app_class_id", type=ParameterType.STRING, description="App class ID", required=False),
            ToolParameter(name="app_instance_id", type=ParameterType.STRING, description="App instance ID", required=False),
            ToolParameter(name="request_config", type=ParameterType.OBJECT, description="Request configuration object (approval, provisioning and access-length settings)", required=False),
            ToolParameter(name="confirm", type=ParameterType.BOOLEAN, description="Set true to execute mutation", required=False, default=False),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="write")],
    )
    async def create_requestable_permission(
        self,
        app_id: str,
        label: str,
        include_inherited_configs: Optional[bool] = None,
        app_class_id: Optional[str] = None,
        app_instance_id: Optional[str] = None,
        request_config: Optional[dict[str, Any] | str] = None,
        confirm: bool = False,
    ) -> tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "create_requestable_permission")
        if blocked:
            return blocked
        try:
            request_config = _as_object(request_config, "request_config")
        except _LumosInputError as exc:
            return _failure(str(exc))
        return await self._call(
            self.client.create_appstore_requestable_permission_appstore_requestable_permissions_post(
                app_id=app_id,
                label=label,
                include_inherited_configs=include_inherited_configs,
                app_class_id=app_class_id,
                app_instance_id=app_instance_id,
                request_config=request_config,
            ),
            "Created requestable permission successfully",
        )

    @tool(
        path="/tools/lumos/get_requestable_permission",
        short_description="Get a requestable permission by ID",
        description="Get a single requestable permission from Lumos by its ID.",
        parameters=[
            ToolParameter(name="permission_id", type=ParameterType.STRING, description="Permission ID", required=True),
            ToolParameter(name="include_inherited_configs", type=ParameterType.BOOLEAN, description="Include inherited configurations", required=False),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="read")],
    )
    async def get_requestable_permission(
        self,
        permission_id: str,
        include_inherited_configs: Optional[bool] = None,
    ) -> tuple[bool, str]:
        return await self._call(
            self.client.get_appstore_permission_appstore_requestable_permissions_permission_id_get(
                permission_id=permission_id,
                include_inherited_configs=include_inherited_configs,
            ),
            "Fetched requestable permission successfully",
        )

    @tool(
        path="/tools/lumos/update_requestable_permission",
        short_description="Update a requestable permission in Lumos",
        description="Update an existing requestable permission in Lumos. Set confirm=true to execute the mutation.",
        parameters=[
            ToolParameter(name="permission_id", type=ParameterType.STRING, description="Permission ID", required=True),
            ToolParameter(name="include_inherited_configs", type=ParameterType.BOOLEAN, description="Include inherited configurations", required=False),
            ToolParameter(name="app_id", type=ParameterType.STRING, description="Lumos app ID", required=False),
            ToolParameter(name="app_class_id", type=ParameterType.STRING, description="App class ID", required=False),
            ToolParameter(name="app_instance_id", type=ParameterType.STRING, description="App instance ID", required=False),
            ToolParameter(name="label", type=ParameterType.STRING, description="Permission label", required=False),
            ToolParameter(name="request_config", type=ParameterType.OBJECT, description="Request configuration object (approval, provisioning and access-length settings)", required=False),
            ToolParameter(name="confirm", type=ParameterType.BOOLEAN, description="Set true to execute mutation", required=False, default=False),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="write")],
    )
    async def update_requestable_permission(
        self,
        permission_id: str,
        include_inherited_configs: Optional[bool] = None,
        app_id: Optional[str] = None,
        app_class_id: Optional[str] = None,
        app_instance_id: Optional[str] = None,
        label: Optional[str] = None,
        request_config: Optional[dict[str, Any] | str] = None,
        confirm: bool = False,
    ) -> tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "update_requestable_permission")
        if blocked:
            return blocked
        try:
            request_config = _as_object(request_config, "request_config")
        except _LumosInputError as exc:
            return _failure(str(exc))
        return await self._call(
            self.client.update_appstore_permission_appstore_requestable_permissions_permission_id_patch(
                permission_id=permission_id,
                include_inherited_configs=include_inherited_configs,
                app_id=app_id,
                app_class_id=app_class_id,
                app_instance_id=app_instance_id,
                label=label,
                request_config=request_config,
            ),
            "Updated requestable permission successfully",
        )

    @tool(
        path="/tools/lumos/delete_requestable_permission",
        short_description="Delete a requestable permission in Lumos",
        description="Delete a requestable permission from Lumos by its ID. Set confirm=true to execute the mutation.",
        parameters=[
            ToolParameter(name="permission_id", type=ParameterType.STRING, description="Permission ID", required=True),
            ToolParameter(name="confirm", type=ParameterType.BOOLEAN, description="Set true to execute mutation", required=False, default=False),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="write")],
    )
    async def delete_requestable_permission(self, permission_id: str, confirm: bool = False) -> tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "delete_requestable_permission")
        if blocked:
            return blocked
        return await self._call(
            self.client.delete_appstore_permission_appstore_requestable_permissions_permission_id_delete(
                permission_id=permission_id
            ),
            "Deleted requestable permission successfully",
        )

    @tool(
        path="/tools/lumos/add_user_role",
        short_description="Add a role to a Lumos user",
        description="Add a role to a Lumos user. Set confirm=true to execute the mutation.",
        parameters=[
            ToolParameter(name="user_id", type=ParameterType.STRING, description="Lumos user ID", required=True),
            ToolParameter(name="role_name", type=ParameterType.STRING, description="Role name", required=True),
            ToolParameter(name="confirm", type=ParameterType.BOOLEAN, description="Set true to execute mutation", required=False, default=False),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="write")],
    )
    async def add_user_role(self, user_id: str, role_name: str, confirm: bool = False) -> tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "add_user_role")
        if blocked:
            return blocked
        return await self._call(
            self.client.add_role_to_user_users_user_id_roles_role_name_post(
                user_id=user_id,
                role_name=role_name,
            ),
            "Added user role successfully",
        )

    @tool(
        path="/tools/lumos/remove_user_role",
        short_description="Remove a role from a Lumos user",
        description="Remove a role from a Lumos user. Set confirm=true to execute the mutation.",
        parameters=[
            ToolParameter(name="user_id", type=ParameterType.STRING, description="Lumos user ID", required=True),
            ToolParameter(name="role_name", type=ParameterType.STRING, description="Role name", required=True),
            ToolParameter(name="confirm", type=ParameterType.BOOLEAN, description="Set true to execute mutation", required=False, default=False),
        ],
        tags=[Tag(key="category", value="identity_access"), Tag(key="type", value="write")],
    )
    async def remove_user_role(self, user_id: str, role_name: str, confirm: bool = False) -> tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "remove_user_role")
        if blocked:
            return blocked
        return await self._call(
            self.client.remove_role_from_user_users_user_id_roles_role_name_delete(
                user_id=user_id,
                role_name=role_name,
            ),
            "Removed user role successfully",
        )
