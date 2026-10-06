"""Calls to Node's internal chat-content check, played the way Python's `NodePdpClient` plays them (PH-07)."""

from __future__ import annotations

import time
from typing import Any

import jwt
import requests

from helper.collab_stack.client import Api
from helper.collab_stack.identity import Actor
from helper.collab_stack.node_api import SCOPED_JWT_SECRET

AUTHZ_CHECK_SCOPE = "authz:check"
CHECK_PATH = "/api/v1/authz/internal/check"


def service_token(org_id: str, scopes: tuple[str, ...] = (AUTHZ_CHECK_SCOPE,), *, expires_in: int = 60) -> str:
    now = int(time.time())
    return jwt.encode({"userId": "svc", "orgId": org_id, "scopes": list(scopes), "iat": now, "exp": now + expires_in}, SCOPED_JWT_SECRET, algorithm="HS256")


def check_body(
    subject: Actor,
    rtype: str,
    record_id: str,
    owner_user_id: str,
    conversation_id: str | None = None,
    run_id: str | None = None,
    kind: dict[str, Any] | None = None,
    org_id: str | None = None,
) -> dict[str, Any]:
    resource: dict[str, Any] = {"type": rtype, "recordId": record_id, "ownerUserId": owner_user_id}
    if conversation_id is not None:
        resource["conversationId"] = conversation_id
    if run_id is not None:
        resource["runId"] = run_id
    if kind is not None:
        resource["kind"] = kind
    return {"userId": subject.user_id, "orgId": org_id or subject.org_id, "action": "read", "resource": resource}


def raw_check(api: Api, body: dict[str, Any], token: str | None = None) -> requests.Response:
    return api.post(CHECK_PATH, token=token if token is not None else service_token(body["orgId"]), json_body=body)


def verdict(api: Api, subject: Actor, rtype: str, record_id: str, owner_user_id: str, **kw: Any) -> dict[str, Any]:
    resp = raw_check(api, check_body(subject, rtype, record_id, owner_user_id, **kw))
    assert resp.status_code == 200, f"check: {resp.status_code} {resp.text[:300]}"
    return resp.json()


def attachment_allowed(api: Api, subject: Actor, record_id: str, uploader: Actor, conversation_id: str | None = None) -> bool:
    return verdict(api, subject, "chatAttachment", record_id, uploader.user_id, conversation_id=conversation_id)["allow"]


def artifact_allowed(
    api: Api, subject: Actor, record_id: str, creator: Actor, conversation_id: str | None, run_id: str | None, **kind: Any
) -> bool:
    return verdict(
        api, subject, "chatArtifact", record_id, creator.user_id, conversation_id=conversation_id, run_id=run_id, kind=kind or None
    )["allow"]


def explain(api: Api, caller: Actor, chat: str, subject: Actor | None = None) -> requests.Response:
    params = {"resource": f"chat:{chat}"}
    if subject is not None:
        params["subject"] = f"user:{subject.user_id}"
    return api.get("/api/v1/authz/explain", caller, params=params)


def preview(api: Api, caller: Actor, chat: str, **change: Any) -> requests.Response:
    return api.post("/api/v1/authz/explain/preview", caller, json_body={"resource": f"chat:{chat}", "change": change})


def principals(rows: list[dict[str, Any]]) -> dict[str, str]:
    """`{userId-or-team:<id>: role}` of a preview list."""
    return {r["userId"] if "userId" in r else f"team:{r['teamId']}": r["role"] for r in rows}
