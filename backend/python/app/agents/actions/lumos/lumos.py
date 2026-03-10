import json
import logging
from typing import List, Optional, Tuple

from app.agents.actions.utils import run_async
from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolDefinition,
    ToolsetBuilder,
    ToolsetCategory,
)
from app.sources.client.http.exception.exception import HttpStatusCode
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.lumos.lumos import LumosClient
from app.sources.external.lumos.lumos import LumosDataSource

logger = logging.getLogger(__name__)


tools: List[ToolDefinition] = [
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
            {"name": "request_config", "type": "string", "description": "Optional request config", "required": False},
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
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/lumos.svg"))\
    .build_decorator()
class Lumos:
    """Lumos toolset exposed to agents."""

    def __init__(self, client: LumosClient) -> None:
        self.client = LumosDataSource(client)

    def _handle_response(self, response: HTTPResponse, success_message: str) -> Tuple[bool, str]:
        success_codes = {
            HttpStatusCode.SUCCESS.value,
            HttpStatusCode.CREATED.value,
            HttpStatusCode.NO_CONTENT.value,
        }
        if response.status in success_codes:
            data = {} if response.status == HttpStatusCode.NO_CONTENT.value else response.json()
            return True, json.dumps({"message": success_message, "data": data})

        error_text = response.text() if hasattr(response, "text") else str(response)
        return False, json.dumps({"error": f"HTTP {response.status}", "details": error_text})

    def _confirm_mutation(self, confirm: bool, operation: str) -> Optional[Tuple[bool, str]]:
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

    @tool(app_name="lumos", tool_name="list_platforms", description="List Lumos platforms (apps)")
    def list_platforms(
        self,
        name_search: Optional[str] = None,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> Tuple[bool, str]:
        response = run_async(self.client.list_apps(name_search=name_search, page=page, size=size))
        return self._handle_response(response, "Fetched platforms successfully")

    @tool(
        app_name="lumos",
        tool_name="get_platform",
        description="Get a Lumos platform (app) by ID",
        parameters=[
            ToolParameter(name="app_id", type=ParameterType.STRING, description="Lumos app ID", required=True),
        ],
    )
    def get_platform(self, app_id: str) -> Tuple[bool, str]:
        response = run_async(self.client.get_app(app_id=app_id))
        return self._handle_response(response, "Fetched platform successfully")

    @tool(app_name="lumos", tool_name="list_users", description="List Lumos users")
    def list_users(
        self,
        search_term: Optional[str] = None,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> Tuple[bool, str]:
        response = run_async(self.client.list_users(search_term=search_term, page=page, size=size))
        return self._handle_response(response, "Fetched users successfully")

    @tool(
        app_name="lumos",
        tool_name="get_user",
        description="Get Lumos user by ID",
        parameters=[
            ToolParameter(name="user_id", type=ParameterType.STRING, description="Lumos user ID", required=True),
        ],
    )
    def get_user(self, user_id: str) -> Tuple[bool, str]:
        response = run_async(self.client.get_user(user_id=user_id))
        return self._handle_response(response, "Fetched user successfully")

    @tool(
        app_name="lumos",
        tool_name="get_user_accounts",
        description="Get accounts assigned to a Lumos user",
        parameters=[
            ToolParameter(name="user_id", type=ParameterType.STRING, description="Lumos user ID", required=True),
        ],
    )
    def get_user_accounts(
        self,
        user_id: str,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> Tuple[bool, str]:
        response = run_async(self.client.get_user_accounts(user_id=user_id, page=page, size=size))
        return self._handle_response(response, "Fetched user accounts successfully")

    @tool(
        app_name="lumos",
        tool_name="get_user_roles",
        description="Get roles assigned to a Lumos user",
        parameters=[
            ToolParameter(name="user_id", type=ParameterType.STRING, description="Lumos user ID", required=True),
        ],
    )
    def get_user_roles(self, user_id: str) -> Tuple[bool, str]:
        response = run_async(self.client.get_user_roles_users_user_id_roles_get(user_id=user_id))
        return self._handle_response(response, "Fetched user roles successfully")

    @tool(app_name="lumos", tool_name="list_groups", description="List Lumos groups")
    def list_groups(
        self,
        name: Optional[str] = None,
        app_id: Optional[str] = None,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> Tuple[bool, str]:
        response = run_async(self.client.get_groups(name=name, app_id=app_id, page=page, size=size))
        return self._handle_response(response, "Fetched groups successfully")

    @tool(
        app_name="lumos",
        tool_name="get_group",
        description="Get Lumos group by ID",
        parameters=[
            ToolParameter(name="group_id", type=ParameterType.STRING, description="Lumos group ID", required=True),
        ],
    )
    def get_group(self, group_id: str) -> Tuple[bool, str]:
        response = run_async(self.client.get_group(group_id=group_id))
        return self._handle_response(response, "Fetched group successfully")

    @tool(
        app_name="lumos",
        tool_name="get_group_members",
        description="Get members of a Lumos group",
        parameters=[
            ToolParameter(name="group_id", type=ParameterType.STRING, description="Lumos group ID", required=True),
        ],
    )
    def get_group_members(
        self,
        group_id: str,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> Tuple[bool, str]:
        response = run_async(self.client.get_group_membership(group_id=group_id, page=page, size=size))
        return self._handle_response(response, "Fetched group members successfully")

    @tool(app_name="lumos", tool_name="list_permissions", description="List Lumos requestable permissions")
    def list_permissions(
        self,
        app_id: Optional[str] = None,
        search_term: Optional[str] = None,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> Tuple[bool, str]:
        response = run_async(
            self.client.get_appstore_permissions_appstore_requestable_permissions_get(
                app_id=app_id,
                search_term=search_term,
                page=page,
                size=size,
            )
        )
        return self._handle_response(response, "Fetched permissions successfully")

    @tool(
        app_name="lumos",
        tool_name="list_app_permissions",
        description="List Lumos requestable permissions for a specific app",
        parameters=[
            ToolParameter(name="app_id", type=ParameterType.STRING, description="Lumos app ID", required=True),
        ],
    )
    def list_app_permissions(
        self,
        app_id: str,
        search_term: Optional[str] = None,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> Tuple[bool, str]:
        response = run_async(
            self.client.get_appstore_permissions_for_app_appstore_apps_app_id_requestable_permissions_get(
                app_id=app_id,
                search_term=search_term,
                page=page,
                size=size,
            )
        )
        return self._handle_response(response, "Fetched app permissions successfully")

    @tool(app_name="lumos", tool_name="list_access_requests", description="List Lumos access requests")
    def list_access_requests(
        self,
        user_id: Optional[str] = None,
        statuses: Optional[list[str]] = None,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> Tuple[bool, str]:
        response = run_async(
            self.client.get_access_requests(
                user_id=user_id,
                statuses=statuses,
                page=page,
                size=size,
            )
        )
        return self._handle_response(response, "Fetched access requests successfully")

    @tool(
        app_name="lumos",
        tool_name="get_access_request",
        description="Get Lumos access request by ID",
        parameters=[
            ToolParameter(name="request_id", type=ParameterType.STRING, description="Access request ID", required=True),
        ],
    )
    def get_access_request(self, request_id: str) -> Tuple[bool, str]:
        response = run_async(self.client.get_access_request(id=request_id))
        return self._handle_response(response, "Fetched access request successfully")

    @tool(
        app_name="lumos",
        tool_name="create_access_request",
        description="Create a Lumos access request",
    )
    def create_access_request(
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
    ) -> Tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "create_access_request")
        if blocked:
            return blocked
        response = run_async(
            self.client.create_access_request(
                app_id=app_id,
                requester_user_id=requester_user_id,
                target_user_id=target_user_id,
                note=note,
                business_justification=business_justification,
                expiration_in_seconds=expiration_in_seconds,
                access_length=access_length,
                requestable_permission_ids=requestable_permission_ids,
            )
        )
        return self._handle_response(response, "Created access request successfully")

    @tool(
        app_name="lumos",
        tool_name="cancel_access_request",
        description="Cancel a Lumos access request",
        parameters=[
            ToolParameter(name="request_id", type=ParameterType.STRING, description="Access request ID", required=True),
        ],
    )
    def cancel_access_request(
        self,
        request_id: str,
        reason: Optional[str] = None,
        confirm: bool = False,
    ) -> Tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "cancel_access_request")
        if blocked:
            return blocked
        response = run_async(self.client.cancel_access_request(id=request_id, reason=reason))
        return self._handle_response(response, "Cancelled access request successfully")

    @tool(app_name="lumos", tool_name="list_access_policies", description="List Lumos access policies")
    def list_access_policies(
        self,
        name: Optional[str] = None,
        page: Optional[int] = 1,
        size: Optional[int] = 25,
    ) -> Tuple[bool, str]:
        response = run_async(self.client.get_access_policies(name=name, page=page, size=size))
        return self._handle_response(response, "Fetched access policies successfully")

    @tool(
        app_name="lumos",
        tool_name="get_access_policy",
        description="Get Lumos access policy by ID",
        parameters=[
            ToolParameter(name="access_policy_id", type=ParameterType.STRING, description="Access policy ID", required=True),
        ],
    )
    def get_access_policy(self, access_policy_id: str) -> Tuple[bool, str]:
        response = run_async(self.client.get_access_policy(access_policy_id=access_policy_id))
        return self._handle_response(response, "Fetched access policy successfully")

    @tool(app_name="lumos", tool_name="create_access_policy", description="Create Lumos access policy")
    def create_access_policy(
        self,
        name: str,
        business_justification: str,
        apps: list[dict],
        access_condition: Optional[str] = None,
        is_everyone_condition: Optional[bool] = None,
        confirm: bool = False,
    ) -> Tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "create_access_policy")
        if blocked:
            return blocked
        response = run_async(
            self.client.create_access_policy(
                name=name,
                business_justification=business_justification,
                apps=apps,
                access_condition=access_condition,
                is_everyone_condition=is_everyone_condition,
            )
        )
        return self._handle_response(response, "Created access policy successfully")

    @tool(app_name="lumos", tool_name="update_access_policy", description="Update Lumos access policy")
    def update_access_policy(
        self,
        access_policy_id: str,
        name: str,
        business_justification: str,
        apps: list[dict],
        access_condition: Optional[str] = None,
        is_everyone_condition: Optional[bool] = None,
        confirm: bool = False,
    ) -> Tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "update_access_policy")
        if blocked:
            return blocked
        response = run_async(
            self.client.update_access_policy(
                access_policy_id=access_policy_id,
                name=name,
                business_justification=business_justification,
                apps=apps,
                access_condition=access_condition,
                is_everyone_condition=is_everyone_condition,
            )
        )
        return self._handle_response(response, "Updated access policy successfully")

    @tool(
        app_name="lumos",
        tool_name="delete_access_policy",
        description="Delete Lumos access policy",
        parameters=[
            ToolParameter(name="access_policy_id", type=ParameterType.STRING, description="Access policy ID", required=True),
        ],
    )
    def delete_access_policy(self, access_policy_id: str, confirm: bool = False) -> Tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "delete_access_policy")
        if blocked:
            return blocked
        response = run_async(self.client.delete_access_policy(access_policy_id=access_policy_id))
        return self._handle_response(response, "Deleted access policy successfully")

    @tool(app_name="lumos", tool_name="create_requestable_permission", description="Create requestable permission")
    def create_requestable_permission(
        self,
        app_id: str,
        label: str,
        include_inherited_configs: Optional[bool] = None,
        app_class_id: Optional[str] = None,
        app_instance_id: Optional[str] = None,
        request_config: Optional[str] = None,
        confirm: bool = False,
    ) -> Tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "create_requestable_permission")
        if blocked:
            return blocked
        response = run_async(
            self.client.create_appstore_requestable_permission_appstore_requestable_permissions_post(
                app_id=app_id,
                label=label,
                include_inherited_configs=include_inherited_configs,
                app_class_id=app_class_id,
                app_instance_id=app_instance_id,
                request_config=request_config,
            )
        )
        return self._handle_response(response, "Created requestable permission successfully")

    @tool(
        app_name="lumos",
        tool_name="get_requestable_permission",
        description="Get requestable permission by ID",
        parameters=[
            ToolParameter(name="permission_id", type=ParameterType.STRING, description="Permission ID", required=True),
        ],
    )
    def get_requestable_permission(
        self,
        permission_id: str,
        include_inherited_configs: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        response = run_async(
            self.client.get_appstore_permission_appstore_requestable_permissions_permission_id_get(
                permission_id=permission_id,
                include_inherited_configs=include_inherited_configs,
            )
        )
        return self._handle_response(response, "Fetched requestable permission successfully")

    @tool(
        app_name="lumos",
        tool_name="update_requestable_permission",
        description="Update requestable permission",
        parameters=[
            ToolParameter(name="permission_id", type=ParameterType.STRING, description="Permission ID", required=True),
        ],
    )
    def update_requestable_permission(
        self,
        permission_id: str,
        include_inherited_configs: Optional[bool] = None,
        app_id: Optional[str] = None,
        app_class_id: Optional[str] = None,
        app_instance_id: Optional[str] = None,
        label: Optional[str] = None,
        request_config: Optional[str] = None,
        confirm: bool = False,
    ) -> Tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "update_requestable_permission")
        if blocked:
            return blocked
        response = run_async(
            self.client.update_appstore_permission_appstore_requestable_permissions_permission_id_patch(
                permission_id=permission_id,
                include_inherited_configs=include_inherited_configs,
                app_id=app_id,
                app_class_id=app_class_id,
                app_instance_id=app_instance_id,
                label=label,
                request_config=request_config,
            )
        )
        return self._handle_response(response, "Updated requestable permission successfully")

    @tool(
        app_name="lumos",
        tool_name="delete_requestable_permission",
        description="Delete requestable permission",
        parameters=[
            ToolParameter(name="permission_id", type=ParameterType.STRING, description="Permission ID", required=True),
        ],
    )
    def delete_requestable_permission(self, permission_id: str, confirm: bool = False) -> Tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "delete_requestable_permission")
        if blocked:
            return blocked
        response = run_async(
            self.client.delete_appstore_permission_appstore_requestable_permissions_permission_id_delete(
                permission_id=permission_id
            )
        )
        return self._handle_response(response, "Deleted requestable permission successfully")

    @tool(
        app_name="lumos",
        tool_name="add_user_role",
        description="Add role to Lumos user",
        parameters=[
            ToolParameter(name="user_id", type=ParameterType.STRING, description="Lumos user ID", required=True),
            ToolParameter(name="role_name", type=ParameterType.STRING, description="Role name", required=True),
        ],
    )
    def add_user_role(self, user_id: str, role_name: str, confirm: bool = False) -> Tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "add_user_role")
        if blocked:
            return blocked
        response = run_async(
            self.client.add_role_to_user_users_user_id_roles_role_name_post(
                user_id=user_id,
                role_name=role_name,
            )
        )
        return self._handle_response(response, "Added user role successfully")

    @tool(
        app_name="lumos",
        tool_name="remove_user_role",
        description="Remove role from Lumos user",
        parameters=[
            ToolParameter(name="user_id", type=ParameterType.STRING, description="Lumos user ID", required=True),
            ToolParameter(name="role_name", type=ParameterType.STRING, description="Role name", required=True),
        ],
    )
    def remove_user_role(self, user_id: str, role_name: str, confirm: bool = False) -> Tuple[bool, str]:
        blocked = self._confirm_mutation(confirm, "remove_user_role")
        if blocked:
            return blocked
        response = run_async(
            self.client.remove_role_from_user_users_user_id_roles_role_name_delete(
                user_id=user_id,
                role_name=role_name,
            )
        )
        return self._handle_response(response, "Removed user role successfully")
