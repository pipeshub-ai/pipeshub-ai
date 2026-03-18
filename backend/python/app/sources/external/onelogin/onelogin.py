# ruff: noqa
from __future__ import annotations

from typing import Any, Dict, Optional, Union, cast

import onelogin  # type: ignore[reportMissingImports]

from app.sources.client.onelogin.onelogin import OneLoginResponse

class OneLoginDataSource:
    """
    Strict, typed wrapper over onelogin-python-sdk for common OneLogin business operations.

    Accepts either a onelogin `ApiClient` instance *or* any object with `.get_sdk() -> ApiClient`.
    """

    def __init__(self, client_or_sdk: Union[onelogin.ApiClient, object]) -> None:  # type: ignore[reportUnknownMemberType]
        super().__init__()
        if hasattr(client_or_sdk, "get_sdk"):  # type: ignore[reportUnknownArgumentType]
            sdk_obj = getattr(client_or_sdk, "get_sdk")()  # type: ignore[reportUnknownArgumentType]
            self._sdk: onelogin.ApiClient = cast(onelogin.ApiClient, sdk_obj)  # type: ignore[reportUnknownMemberType]
        else:
            self._sdk = cast(onelogin.ApiClient, client_or_sdk)  # type: ignore[reportUnknownMemberType]

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
    def list_users(self, limit: Optional[int] = None, page: Optional[int] = None, search: Optional[str] = None) -> OneLoginResponse:
        """List all users.  [users]"""
        api = onelogin.UsersV2Api(self._sdk)  # type: ignore[reportUnknownMemberType]
        params = self._params(limit=limit, page=page, search=search)
        users: Any = api.list_users2(**params)  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=users)  # type: ignore[reportUnknownArgumentType]
    def get_user(self, user_id: int) -> OneLoginResponse:
        """Get a specific user by ID.  [users]"""
        api = onelogin.UsersV2Api(self._sdk)  # type: ignore[reportUnknownMemberType]
        user: Any = api.get_user_by_id2(user_id)  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=user)  # type: ignore[reportUnknownArgumentType]
    def list_groups(self) -> OneLoginResponse:
        """List all groups.  [groups]"""
        api = onelogin.GroupsApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        groups: Any = api.get_groups()  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=groups)  # type: ignore[reportUnknownArgumentType]
    def get_group(self, group_id: int) -> OneLoginResponse:
        """Get a specific group by ID.  [groups]"""
        api = onelogin.GroupsApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        group: Any = api.get_group_by_id(str(group_id))  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=group)  # type: ignore[reportUnknownArgumentType]
    def list_roles(self) -> OneLoginResponse:
        """List all roles.  [roles]"""
        api = onelogin.RolesApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        roles: Any = api.list_roles()  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=roles)  # type: ignore[reportUnknownArgumentType]
    def get_role(self, role_id: int) -> OneLoginResponse:
        """Get a specific role by ID.  [roles]"""
        api = onelogin.RolesApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        role: Any = api.get_role_by_id(str(role_id))  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=role)  # type: ignore[reportUnknownArgumentType]
    def list_apps(self, limit: Optional[int] = None, page: Optional[int] = None) -> OneLoginResponse:
        """List all apps.  [apps]"""
        api = onelogin.AppsApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        params = self._params(limit=limit, page=page)
        apps: Any = api.list_apps(**params)  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=apps)  # type: ignore[reportUnknownArgumentType]
    def get_app(self, app_id: int) -> OneLoginResponse:
        """Get a specific app by ID.  [apps]"""
        api = onelogin.AppsApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        app: Any = api.get_app(app_id)  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=app)  # type: ignore[reportUnknownArgumentType]
    def get_app_users(self, app_id: int, limit: Optional[int] = None, page: Optional[int] = None) -> OneLoginResponse:
        """Get users assigned to a specific app.  [apps]"""
        api = onelogin.AppsApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        params = self._params(limit=limit, page=page)
        users: Any = api.list_app_users(app_id, **params)  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=users)  # type: ignore[reportUnknownArgumentType]
    def list_events(self, limit: Optional[int] = None, page: Optional[int] = None) -> OneLoginResponse:
        """List all events.  [events]"""
        api = onelogin.EventsApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        params = self._params(limit=limit, page=page)
        events: Any = api.get_events(**params)  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=events)  # type: ignore[reportUnknownArgumentType]
    def get_event(self, event_id: int) -> OneLoginResponse:
        """Get a specific event by ID.  [events]"""
        api = onelogin.EventsApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        event: Any = api.get_event_by_id(event_id)  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=event)  # type: ignore[reportUnknownArgumentType]
    def list_privileges(self) -> OneLoginResponse:
        """List all privileges.  [privileges]"""
        api = onelogin.PrivilegesApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        privileges: Any = api.list_privileges()  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=privileges)  # type: ignore[reportUnknownArgumentType]
    def get_privilege(self, privilege_id: str) -> OneLoginResponse:
        """Get a specific privilege by ID.  [privileges]"""
        api = onelogin.PrivilegesApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        privilege: Any = api.get_privilege(privilege_id)  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=privilege)  # type: ignore[reportUnknownArgumentType]
    def list_mappings(self) -> OneLoginResponse:
        """List all user mappings.  [mappings]"""
        api = onelogin.MappingsApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        mappings: Any = api.list_mappings()  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=mappings)  # type: ignore[reportUnknownArgumentType]
    def list_brands(self) -> OneLoginResponse:
        """List all brands.  [brands]"""
        api = onelogin.BrandsApi(self._sdk)  # type: ignore[reportUnknownMemberType]
        brands: Any = api.list_brands()  # type: ignore[reportUnknownMemberType]
        return OneLoginResponse(success=True, data=brands)  # type: ignore[reportUnknownArgumentType]
