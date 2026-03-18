#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

"""
OneLogin (onelogin-python-sdk) -- Code Generator

Emits a `OneLoginDataSource` with explicit, typed methods mapped to the *real* onelogin SDK APIs.
- Accepts either a raw `onelogin.ApiClient` instance or any wrapper exposing `.get_sdk() -> ApiClient`.
- Each method instantiates the appropriate API class (UsersV2Api, GroupsApi, etc.) from the SDK.

SDK API patterns (all synchronous):
- Users:      UsersV2Api(api_client).list_users2(), .get_user_by_id2(user_id)
- Groups:     GroupsApi(api_client).get_groups(), .get_group_by_id(group_id)
- Roles:      RolesApi(api_client).list_roles(), .get_role_by_id(role_id)
- Apps:       AppsApi(api_client).list_apps(), .get_app(app_id)
- Events:     EventsApi(api_client).get_events(), .get_event_by_id(event_id)
- Privileges: PrivilegesApi(api_client).list_privileges(), .get_privilege(privilege_id)
- Mappings:   MappingsApi(api_client).list_mapping_action_values(mapping_id), .list_mappings()
- Brands:     BrandsApi(api_client).list_brands()
- Auth Srvrs: SmartHooksApi(api_client).list_hooks()

References:
- SDK: https://github.com/onelogin/onelogin-python-sdk
- API: https://developers.onelogin.com/api-docs/2/getting-started/dev-overview
"""

import argparse
import textwrap
from typing import List, Tuple

# -----------------------------
# Configuration knobs (CLI-set)
# -----------------------------

DEFAULT_RESPONSE_IMPORT = "from app.sources.client.onelogin.onelogin import OneLoginResponse"
DEFAULT_CLASS_NAME = "OneLoginDataSource"
DEFAULT_OUT = "onelogin_data_source.py"


HEADER = '''\
# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
from __future__ import annotations

from typing import Dict, List, Optional, Union, cast

import onelogin

{response_import}

class {class_name}:
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
'''

FOOTER = """
"""

# Each tuple: (signature, body, short_doc)
METHODS: List[Tuple[str, str, str]] = []

# ---------- Users ----------
METHODS += [
    (
        "def list_users(self, limit: Optional[int] = None, page: Optional[int] = None, search: Optional[str] = None) -> OneLoginResponse",
        "            api = onelogin.UsersV2Api(self._sdk)\n"
        "            params = self._params(limit=limit, page=page, search=search)\n"
        "            users = api.list_users2(**params)\n"
        "            return OneLoginResponse(success=True, data=users)",
        "List all users.  [users]",
    ),
    (
        "def get_user(self, user_id: int) -> OneLoginResponse",
        "            api = onelogin.UsersV2Api(self._sdk)\n"
        "            user = api.get_user_by_id2(user_id)\n"
        "            return OneLoginResponse(success=True, data=user)",
        "Get a specific user by ID.  [users]",
    ),
]

# ---------- Groups ----------
METHODS += [
    (
        "def list_groups(self) -> OneLoginResponse",
        "            api = onelogin.GroupsApi(self._sdk)\n"
        "            groups = api.get_groups()\n"
        "            return OneLoginResponse(success=True, data=groups)",
        "List all groups.  [groups]",
    ),
    (
        "def get_group(self, group_id: int) -> OneLoginResponse",
        "            api = onelogin.GroupsApi(self._sdk)\n"
        "            group = api.get_group_by_id(str(group_id))\n"
        "            return OneLoginResponse(success=True, data=group)",
        "Get a specific group by ID.  [groups]",
    ),
]

# ---------- Roles ----------
METHODS += [
    (
        "def list_roles(self) -> OneLoginResponse",
        "            api = onelogin.RolesApi(self._sdk)\n"
        "            roles = api.list_roles()\n"
        "            return OneLoginResponse(success=True, data=roles)",
        "List all roles.  [roles]",
    ),
    (
        "def get_role(self, role_id: int) -> OneLoginResponse",
        "            api = onelogin.RolesApi(self._sdk)\n"
        "            role = api.get_role_by_id(str(role_id))\n"
        "            return OneLoginResponse(success=True, data=role)",
        "Get a specific role by ID.  [roles]",
    ),
]

# ---------- Apps ----------
METHODS += [
    (
        "def list_apps(self, limit: Optional[int] = None, page: Optional[int] = None) -> OneLoginResponse",
        "            api = onelogin.AppsApi(self._sdk)\n"
        "            params = self._params(limit=limit, page=page)\n"
        "            apps = api.list_apps(**params)\n"
        "            return OneLoginResponse(success=True, data=apps)",
        "List all apps.  [apps]",
    ),
    (
        "def get_app(self, app_id: int) -> OneLoginResponse",
        "            api = onelogin.AppsApi(self._sdk)\n"
        "            app = api.get_app(app_id)\n"
        "            return OneLoginResponse(success=True, data=app)",
        "Get a specific app by ID.  [apps]",
    ),
    (
        "def get_app_users(self, app_id: int, limit: Optional[int] = None, page: Optional[int] = None) -> OneLoginResponse",
        "            api = onelogin.AppsApi(self._sdk)\n"
        "            params = self._params(limit=limit, page=page)\n"
        "            users = api.list_app_users(app_id, **params)\n"
        "            return OneLoginResponse(success=True, data=users)",
        "Get users assigned to a specific app.  [apps]",
    ),
]

# ---------- Events ----------
METHODS += [
    (
        "def list_events(self, limit: Optional[int] = None, page: Optional[int] = None) -> OneLoginResponse",
        "            api = onelogin.EventsApi(self._sdk)\n"
        "            params = self._params(limit=limit, page=page)\n"
        "            events = api.get_events(**params)\n"
        "            return OneLoginResponse(success=True, data=events)",
        "List all events.  [events]",
    ),
    (
        "def get_event(self, event_id: int) -> OneLoginResponse",
        "            api = onelogin.EventsApi(self._sdk)\n"
        "            event = api.get_event_by_id(event_id)\n"
        "            return OneLoginResponse(success=True, data=event)",
        "Get a specific event by ID.  [events]",
    ),
]

# ---------- Privileges ----------
METHODS += [
    (
        "def list_privileges(self) -> OneLoginResponse",
        "            api = onelogin.PrivilegesApi(self._sdk)\n"
        "            privileges = api.list_privileges()\n"
        "            return OneLoginResponse(success=True, data=privileges)",
        "List all privileges.  [privileges]",
    ),
    (
        "def get_privilege(self, privilege_id: str) -> OneLoginResponse",
        "            api = onelogin.PrivilegesApi(self._sdk)\n"
        "            privilege = api.get_privilege(privilege_id)\n"
        "            return OneLoginResponse(success=True, data=privilege)",
        "Get a specific privilege by ID.  [privileges]",
    ),
]

# ---------- Mappings ----------
METHODS += [
    (
        "def list_mappings(self) -> OneLoginResponse",
        "            api = onelogin.MappingsApi(self._sdk)\n"
        "            mappings = api.list_mappings()\n"
        "            return OneLoginResponse(success=True, data=mappings)",
        "List all user mappings.  [mappings]",
    ),
]

# ---------- Brands ----------
METHODS += [
    (
        "def list_brands(self) -> OneLoginResponse",
        "            api = onelogin.BrandsApi(self._sdk)\n"
        "            brands = api.list_brands()\n"
        "            return OneLoginResponse(success=True, data=brands)",
        "List all brands.  [brands]",
    ),
]

# -------------------------
# Code emission utilities
# -------------------------


def _emit_method(sig: str, body: str, doc: str) -> str:
    normalized_body = textwrap.indent(textwrap.dedent(body), "        ")
    return f'    {sig}:\n        """{doc}"""\n{normalized_body}\n'


def build_class(
    response_import: str = DEFAULT_RESPONSE_IMPORT, class_name: str = DEFAULT_CLASS_NAME
) -> str:
    parts: List[str] = []
    header = HEADER.replace("{response_import}", response_import).replace(
        "{class_name}", class_name
    )
    parts.append(header)
    for sig, body, doc in METHODS:
        parts.append(_emit_method(sig, body, doc))
    parts.append(FOOTER)
    return "".join(parts)


def write_output(path: str, code: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate OneLoginDataSource (onelogin-python-sdk)."
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help="Output path for the generated data source."
    )
    parser.add_argument(
        "--response-import",
        default=DEFAULT_RESPONSE_IMPORT,
        help="Import line to bring in OneLoginResponse.",
    )
    parser.add_argument(
        "--class-name",
        default=DEFAULT_CLASS_NAME,
        help="Name of the generated datasource class.",
    )
    parser.add_argument(
        "--print",
        dest="do_print",
        action="store_true",
        help="Also print generated code to stdout.",
    )
    args = parser.parse_args()

    code = build_class(response_import=args.response_import, class_name=args.class_name)
    write_output(args.out, code)
    if args.do_print:
        print(code)


if __name__ == "__main__":
    main()
