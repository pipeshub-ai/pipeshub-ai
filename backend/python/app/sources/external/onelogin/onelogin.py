# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
from __future__ import annotations

from typing import Dict, List, Optional, Union, cast

import onelogin

from app.sources.client.onelogin.onelogin import OneLoginResponse

class OneLoginDataSource:
    """
    Strict, typed wrapper over onelogin-python-sdk for common OneLogin business operations.

    Accepts either a onelogin `ApiClient` instance *or* any object with `.get_sdk() -> ApiClient`.
    """

    def __init__(self, client_or_sdk: Union[onelogin.ApiClient, object]) -> None:
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: onelogin.ApiClient = cast(onelogin.ApiClient, sdk_obj)
        else:
            self._sdk = cast(onelogin.ApiClient, client_or_sdk)

    # ---- helpers ----
    @staticmethod
    def _params(**kwargs: object) -> Dict[str, object]:
        out: Dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)) and len(v) == 0:
                continue
            out[k] = v
        return out
    def list_users(self, limit: Optional[int] = None, page: Optional[int] = None, search: Optional[str] = None) -> OneLoginResponse:
        """List all users.  [users]"""
        api = onelogin.UsersV2Api(self._sdk)
        params = self._params(limit=limit, page=page, search=search)
        users = api.list_users2(**params)
        return OneLoginResponse(success=True, data=users)
    def get_user(self, user_id: int) -> OneLoginResponse:
        """Get a specific user by ID.  [users]"""
        api = onelogin.UsersV2Api(self._sdk)
        user = api.get_user_by_id2(user_id)
        return OneLoginResponse(success=True, data=user)
    def list_groups(self) -> OneLoginResponse:
        """List all groups.  [groups]"""
        api = onelogin.GroupsApi(self._sdk)
        groups = api.get_groups()
        return OneLoginResponse(success=True, data=groups)
    def get_group(self, group_id: int) -> OneLoginResponse:
        """Get a specific group by ID.  [groups]"""
        api = onelogin.GroupsApi(self._sdk)
        group = api.get_group_by_id(str(group_id))
        return OneLoginResponse(success=True, data=group)
    def list_roles(self) -> OneLoginResponse:
        """List all roles.  [roles]"""
        api = onelogin.RolesApi(self._sdk)
        roles = api.list_roles()
        return OneLoginResponse(success=True, data=roles)
    def get_role(self, role_id: int) -> OneLoginResponse:
        """Get a specific role by ID.  [roles]"""
        api = onelogin.RolesApi(self._sdk)
        role = api.get_role_by_id(str(role_id))
        return OneLoginResponse(success=True, data=role)
    def list_apps(self, limit: Optional[int] = None, page: Optional[int] = None) -> OneLoginResponse:
        """List all apps.  [apps]"""
        api = onelogin.AppsApi(self._sdk)
        params = self._params(limit=limit, page=page)
        apps = api.list_apps(**params)
        return OneLoginResponse(success=True, data=apps)
    def get_app(self, app_id: int) -> OneLoginResponse:
        """Get a specific app by ID.  [apps]"""
        api = onelogin.AppsApi(self._sdk)
        app = api.get_app(app_id)
        return OneLoginResponse(success=True, data=app)
    def get_app_users(self, app_id: int, limit: Optional[int] = None, page: Optional[int] = None) -> OneLoginResponse:
        """Get users assigned to a specific app.  [apps]"""
        api = onelogin.AppsApi(self._sdk)
        params = self._params(limit=limit, page=page)
        users = api.list_app_users(app_id, **params)
        return OneLoginResponse(success=True, data=users)
    def list_events(self, limit: Optional[int] = None, page: Optional[int] = None) -> OneLoginResponse:
        """List all events.  [events]"""
        api = onelogin.EventsApi(self._sdk)
        params = self._params(limit=limit, page=page)
        events = api.get_events(**params)
        return OneLoginResponse(success=True, data=events)
    def get_event(self, event_id: int) -> OneLoginResponse:
        """Get a specific event by ID.  [events]"""
        api = onelogin.EventsApi(self._sdk)
        event = api.get_event_by_id(event_id)
        return OneLoginResponse(success=True, data=event)
    def list_privileges(self) -> OneLoginResponse:
        """List all privileges.  [privileges]"""
        api = onelogin.PrivilegesApi(self._sdk)
        privileges = api.list_privileges()
        return OneLoginResponse(success=True, data=privileges)
    def get_privilege(self, privilege_id: str) -> OneLoginResponse:
        """Get a specific privilege by ID.  [privileges]"""
        api = onelogin.PrivilegesApi(self._sdk)
        privilege = api.get_privilege(privilege_id)
        return OneLoginResponse(success=True, data=privilege)
    def list_mappings(self) -> OneLoginResponse:
        """List all user mappings.  [mappings]"""
        api = onelogin.MappingsApi(self._sdk)
        mappings = api.list_mappings()
        return OneLoginResponse(success=True, data=mappings)
    def list_brands(self) -> OneLoginResponse:
        """List all brands.  [brands]"""
        api = onelogin.BrandsApi(self._sdk)
        brands = api.list_brands()
        return OneLoginResponse(success=True, data=brands)

