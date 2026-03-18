#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

"""
Okta (okta-sdk-python) -- Code Generator (strict, no `Any`, no `None` passthrough)

Emits an `OktaDataSource` with explicit, typed async methods mapped to the *real* okta SDK APIs.
- No `Any` in signatures or implementation.
- Never forwards None to the SDK (filters optionals via `_params`).
- Accepts either a raw `okta.client.Client` instance or any wrapper exposing `.get_sdk()`.

SDK API patterns (all methods are async and return (result, resp, err)):
- Users:      okta_client.list_users(query_params={}), .get_user(user_id)
- Groups:     okta_client.list_groups(query_params={}), .get_group(group_id), .list_group_users(group_id)
- Apps:       okta_client.list_applications(query_params={}), .get_application(app_id)
- Logs:       okta_client.get_logs(query_params={})
- Auth Srvrs: okta_client.list_authorization_servers(), .get_authorization_server(auth_server_id)
- Policies:   okta_client.list_policies(query_params={})

References:
- SDK: https://github.com/okta/okta-sdk-python
- API: https://developer.okta.com/docs/api/
"""

import argparse
import textwrap
from typing import List, Tuple

# -----------------------------
# Configuration knobs (CLI-set)
# -----------------------------

DEFAULT_RESPONSE_IMPORT = "from app.sources.client.okta.okta import OktaResponse"
DEFAULT_CLASS_NAME = "OktaDataSource"
DEFAULT_OUT = "okta_data_source.py"


HEADER = '''\
# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
from __future__ import annotations

from typing import Dict, List, Optional, Union, cast

from okta.client import Client as OktaSDKClient

{response_import}

class {class_name}:
    """
    Strict, typed async wrapper over okta-sdk-python for common Okta business operations.

    Accepts either an okta SDK `Client` instance *or* any object with `.get_sdk() -> Client`.
    All methods are async because the okta SDK is natively async.
    """

    def __init__(self, client_or_sdk: Union[OktaSDKClient, object]) -> None:
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: OktaSDKClient = cast(OktaSDKClient, sdk_obj)
        else:
            self._sdk = cast(OktaSDKClient, client_or_sdk)

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
        "async def list_users(self, q: Optional[str] = None, filter_expr: Optional[str] = None, search: Optional[str] = None, limit: Optional[int] = None, after: Optional[str] = None) -> OktaResponse",
        "            query_params = self._params(q=q, filter=filter_expr, search=search, limit=limit, after=after)\n"
        "            users, resp, err = await self._sdk.list_users(query_params=query_params)\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to list users')\n"
        "            return OktaResponse(success=True, data=users)",
        "List users with optional search/filter.  [users]",
    ),
    (
        "async def get_user(self, user_id: str) -> OktaResponse",
        "            user, resp, err = await self._sdk.get_user(user_id)\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to get user')\n"
        "            return OktaResponse(success=True, data=user)",
        "Get a single user by ID or login.  [users]",
    ),
    (
        "async def get_current_user(self) -> OktaResponse",
        "            user, resp, err = await self._sdk.get_user('me')\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to get current user')\n"
        "            return OktaResponse(success=True, data=user)",
        "Get the current authenticated user (me).  [users]",
    ),
]

# ---------- Groups ----------
METHODS += [
    (
        "async def list_groups(self, q: Optional[str] = None, filter_expr: Optional[str] = None, limit: Optional[int] = None, after: Optional[str] = None) -> OktaResponse",
        "            query_params = self._params(q=q, filter=filter_expr, limit=limit, after=after)\n"
        "            groups, resp, err = await self._sdk.list_groups(query_params=query_params)\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to list groups')\n"
        "            return OktaResponse(success=True, data=groups)",
        "List groups with optional search/filter.  [groups]",
    ),
    (
        "async def get_group(self, group_id: str) -> OktaResponse",
        "            group, resp, err = await self._sdk.get_group(group_id)\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to get group')\n"
        "            return OktaResponse(success=True, data=group)",
        "Get a single group by ID.  [groups]",
    ),
    (
        "async def list_group_members(self, group_id: str, limit: Optional[int] = None, after: Optional[str] = None) -> OktaResponse",
        "            query_params = self._params(limit=limit, after=after)\n"
        "            users, resp, err = await self._sdk.list_group_users(group_id, query_params=query_params)\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to list group members')\n"
        "            return OktaResponse(success=True, data=users)",
        "List members of a group.  [groups]",
    ),
]

# ---------- Applications ----------
METHODS += [
    (
        "async def list_applications(self, q: Optional[str] = None, limit: Optional[int] = None, after: Optional[str] = None) -> OktaResponse",
        "            query_params = self._params(q=q, limit=limit, after=after)\n"
        "            apps, resp, err = await self._sdk.list_applications(query_params=query_params)\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to list applications')\n"
        "            return OktaResponse(success=True, data=apps)",
        "List applications.  [apps]",
    ),
    (
        "async def get_application(self, app_id: str) -> OktaResponse",
        "            app, resp, err = await self._sdk.get_application(app_id)\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to get application')\n"
        "            return OktaResponse(success=True, data=app)",
        "Get a specific application by ID.  [apps]",
    ),
    (
        "async def list_application_users(self, app_id: str, limit: Optional[int] = None, after: Optional[str] = None) -> OktaResponse",
        "            query_params = self._params(limit=limit, after=after)\n"
        "            users, resp, err = await self._sdk.list_application_users(app_id, query_params=query_params)\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to list application users')\n"
        "            return OktaResponse(success=True, data=users)",
        "List users assigned to an application.  [apps]",
    ),
]

# ---------- System Logs ----------
METHODS += [
    (
        "async def get_system_logs(self, since: Optional[str] = None, until: Optional[str] = None, filter_expr: Optional[str] = None, q: Optional[str] = None, limit: Optional[int] = None, after: Optional[str] = None) -> OktaResponse",
        "            query_params = self._params(since=since, until=until, filter=filter_expr, q=q, limit=limit, after=after)\n"
        "            logs, resp, err = await self._sdk.get_logs(query_params=query_params)\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to get system logs')\n"
        "            return OktaResponse(success=True, data=logs)",
        "Get system log events with optional filters.  [logs]",
    ),
]

# ---------- Authorization Servers ----------
METHODS += [
    (
        "async def list_authorization_servers(self) -> OktaResponse",
        "            servers, resp, err = await self._sdk.list_authorization_servers()\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to list authorization servers')\n"
        "            return OktaResponse(success=True, data=servers)",
        "List authorization servers.  [auth_servers]",
    ),
    (
        "async def get_authorization_server(self, auth_server_id: str) -> OktaResponse",
        "            server, resp, err = await self._sdk.get_authorization_server(auth_server_id)\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to get authorization server')\n"
        "            return OktaResponse(success=True, data=server)",
        "Get a specific authorization server.  [auth_servers]",
    ),
]

# ---------- Policies ----------
METHODS += [
    (
        "async def list_policies(self, type_filter: Optional[str] = None) -> OktaResponse",
        "            query_params = self._params(type=type_filter)\n"
        "            policies, resp, err = await self._sdk.list_policies(query_params=query_params)\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to list policies')\n"
        "            return OktaResponse(success=True, data=policies)",
        "List policies with optional type filter.  [policies]",
    ),
    (
        "async def get_policy(self, policy_id: str) -> OktaResponse",
        "            policy, resp, err = await self._sdk.get_policy(policy_id)\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to get policy')\n"
        "            return OktaResponse(success=True, data=policy)",
        "Get a specific policy by ID.  [policies]",
    ),
]

# ---------- Roles ----------
METHODS += [
    (
        "async def list_assigned_roles_for_user(self, user_id: str) -> OktaResponse",
        "            roles, resp, err = await self._sdk.list_assigned_roles_for_user(user_id)\n"
        "            if err:\n"
        "                return OktaResponse(success=False, error=str(err), message='Failed to list roles for user')\n"
        "            return OktaResponse(success=True, data=roles)",
        "List roles assigned to a user.  [roles]",
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
        description="Generate OktaDataSource (okta-sdk-python)."
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help="Output path for the generated data source."
    )
    parser.add_argument(
        "--response-import",
        default=DEFAULT_RESPONSE_IMPORT,
        help="Import line to bring in OktaResponse.",
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
