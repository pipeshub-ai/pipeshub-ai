"""A password sign-in's last step, the same for either edition's answer.

This repository's ``/userAccount/authenticate`` ends with the org's access token. An edition with workspace-first
sign-in ends with an email-verified session in the same ``accessToken`` field: the token lists the person's
workspaces, and ``/auth/token/switch`` exchanges it for the org's access token. Presented as an access token it is
refused (``invalid signature``: it is signed with another key), so a lane that signs in must finish the switch
(SIGNIN-SWITCH-1). Standard library only: the seed runs under the host's Python.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

EMAIL_VERIFIED_SCOPE = "email:verified"
WORKSPACES = "/api/v1/workspaces"
SWITCH = "/api/v1/auth/token/switch"

# (method, path, body, bearer token) -> the answer's JSON body; raises on a refusal.
Call = Callable[[str, str, Any, str], Any]


def claims(token: str) -> dict[str, Any]:
    """A JWT's claims, unverified: only to tell which kind of token an answer holds."""
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    body = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(body))
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def is_workspace_session(token: str) -> bool:
    scopes = claims(token).get("scopes")
    return isinstance(scopes, list) and EMAIL_VERIFIED_SCOPE in scopes


def org_access_token(answer: dict[str, Any], call: Call) -> str:
    """The org access token a password step's ``answer`` leads to: its own, or the workspace switch's.

    The org is the one the answer names (the server's auto-login choice), else the person's only workspace."""
    token = str(answer.get("accessToken") or "")
    if not token:
        raise RuntimeError(f"the password step answered without a token: {sorted(answer)}")
    if answer.get("refreshToken") or not is_workspace_session(token):
        return token
    org_id = answer.get("orgId")
    if not org_id:
        listed = call("GET", f"{WORKSPACES}?isMember", None, token) or {}
        mine = [w for w in listed.get("workspaces") or [] if isinstance(w, dict) and w.get("isMember")]
        if len(mine) != 1:
            raise RuntimeError(f"the person is a member of {len(mine)} workspaces; name the one to sign in to")
        org_id = mine[0].get("id")
    switched = call("POST", SWITCH, {"orgId": str(org_id)}, token) or {}
    access = str(switched.get("accessToken") or "")
    if not access:
        raise RuntimeError("the workspace switch answered without an access token")
    return access


__all__ = ["EMAIL_VERIFIED_SCOPE", "claims", "is_workspace_session", "org_access_token"]
