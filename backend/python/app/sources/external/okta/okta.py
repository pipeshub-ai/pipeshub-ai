# ruff: noqa
from __future__ import annotations

from typing import Dict, Optional, Union, cast

from okta.client import Client as OktaSDKClient  # type: ignore[reportMissingImports]

from app.sources.client.okta.okta import OktaResponse

class OktaDataSource:
    """
    Strict, typed async wrapper over okta-sdk-python for common Okta business operations.

    Accepts either an okta SDK `Client` instance *or* any object with `.get_sdk() -> Client`.
    All methods are async because the okta SDK is natively async.
    """

    def __init__(self, client_or_sdk: Union[OktaSDKClient, object]) -> None:  # type: ignore[reportUnknownParameterType]
        super().__init__()
        if hasattr(client_or_sdk, "get_sdk"):  # type: ignore[reportUnknownArgumentType]
            sdk_obj = getattr(client_or_sdk, "get_sdk")()  # type: ignore[reportUnknownArgumentType]
            self._sdk: OktaSDKClient = cast(OktaSDKClient, sdk_obj)  # type: ignore[reportUnknownMemberType]
        else:
            self._sdk = cast(OktaSDKClient, client_or_sdk)  # type: ignore[reportUnknownMemberType]

    # ---- helpers ----
    @staticmethod
    def _params(**kwargs: object) -> Dict[str, object]:
        out: Dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)) and len(v) == 0:  # type: ignore[reportUnknownArgumentType]
                continue
            out[k] = v
        return out
    async def list_users(self, q: Optional[str] = None, filter_expr: Optional[str] = None, search: Optional[str] = None, limit: Optional[int] = None, after: Optional[str] = None) -> OktaResponse:
        """List users with optional search/filter.  [users]"""
        query_params = self._params(q=q, filter=filter_expr, search=search, limit=limit, after=after)
        users, resp, err = await self._sdk.list_users(query_params=query_params)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to list users')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=users)  # type: ignore[reportUnknownArgumentType]
    async def get_user(self, user_id: str) -> OktaResponse:
        """Get a single user by ID or login.  [users]"""
        user, resp, err = await self._sdk.get_user(user_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to get user')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=user)  # type: ignore[reportUnknownArgumentType]
    async def get_current_user(self) -> OktaResponse:
        """Get the current authenticated user (me).  [users]"""
        user, resp, err = await self._sdk.get_user('me')  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to get current user')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=user)  # type: ignore[reportUnknownArgumentType]
    async def list_groups(self, q: Optional[str] = None, filter_expr: Optional[str] = None, limit: Optional[int] = None, after: Optional[str] = None) -> OktaResponse:
        """List groups with optional search/filter.  [groups]"""
        query_params = self._params(q=q, filter=filter_expr, limit=limit, after=after)
        groups, resp, err = await self._sdk.list_groups(query_params=query_params)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to list groups')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=groups)  # type: ignore[reportUnknownArgumentType]
    async def get_group(self, group_id: str) -> OktaResponse:
        """Get a single group by ID.  [groups]"""
        group, resp, err = await self._sdk.get_group(group_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to get group')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=group)  # type: ignore[reportUnknownArgumentType]
    async def list_group_members(self, group_id: str, limit: Optional[int] = None, after: Optional[str] = None) -> OktaResponse:
        """List members of a group.  [groups]"""
        query_params = self._params(limit=limit, after=after)
        users, resp, err = await self._sdk.list_group_users(group_id, query_params=query_params)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to list group members')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=users)  # type: ignore[reportUnknownArgumentType]
    async def list_applications(self, q: Optional[str] = None, limit: Optional[int] = None, after: Optional[str] = None) -> OktaResponse:
        """List applications.  [apps]"""
        query_params = self._params(q=q, limit=limit, after=after)
        apps, resp, err = await self._sdk.list_applications(query_params=query_params)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to list applications')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=apps)  # type: ignore[reportUnknownArgumentType]
    async def get_application(self, app_id: str) -> OktaResponse:
        """Get a specific application by ID.  [apps]"""
        app, resp, err = await self._sdk.get_application(app_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to get application')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=app)  # type: ignore[reportUnknownArgumentType]
    async def list_application_users(self, app_id: str, limit: Optional[int] = None, after: Optional[str] = None) -> OktaResponse:
        """List users assigned to an application.  [apps]"""
        query_params = self._params(limit=limit, after=after)
        users, resp, err = await self._sdk.list_application_users(app_id, query_params=query_params)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to list application users')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=users)  # type: ignore[reportUnknownArgumentType]
    async def get_system_logs(self, since: Optional[str] = None, until: Optional[str] = None, filter_expr: Optional[str] = None, q: Optional[str] = None, limit: Optional[int] = None, after: Optional[str] = None) -> OktaResponse:
        """Get system log events with optional filters.  [logs]"""
        query_params = self._params(since=since, until=until, filter=filter_expr, q=q, limit=limit, after=after)
        logs, resp, err = await self._sdk.get_logs(query_params=query_params)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to get system logs')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=logs)  # type: ignore[reportUnknownArgumentType]
    async def list_authorization_servers(self) -> OktaResponse:
        """List authorization servers.  [auth_servers]"""
        servers, resp, err = await self._sdk.list_authorization_servers()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to list authorization servers')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=servers)  # type: ignore[reportUnknownArgumentType]
    async def get_authorization_server(self, auth_server_id: str) -> OktaResponse:
        """Get a specific authorization server.  [auth_servers]"""
        server, resp, err = await self._sdk.get_authorization_server(auth_server_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to get authorization server')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=server)  # type: ignore[reportUnknownArgumentType]
    async def list_policies(self, type_filter: Optional[str] = None) -> OktaResponse:
        """List policies with optional type filter.  [policies]"""
        query_params = self._params(type=type_filter)
        policies, resp, err = await self._sdk.list_policies(query_params=query_params)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to list policies')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=policies)  # type: ignore[reportUnknownArgumentType]
    async def get_policy(self, policy_id: str) -> OktaResponse:
        """Get a specific policy by ID.  [policies]"""
        policy, resp, err = await self._sdk.get_policy(policy_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to get policy')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=policy)  # type: ignore[reportUnknownArgumentType]
    async def list_assigned_roles_for_user(self, user_id: str) -> OktaResponse:
        """List roles assigned to a user.  [roles]"""
        roles, resp, err = await self._sdk.list_assigned_roles_for_user(user_id)  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if err:
            return OktaResponse(success=False, error=str(err), message='Failed to list roles for user')  # type: ignore[reportUnknownArgumentType]
        return OktaResponse(success=True, data=roles)  # type: ignore[reportUnknownArgumentType]
