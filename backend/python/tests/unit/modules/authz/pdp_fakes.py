"""Test doubles for the chat-content PDP client."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.modules.authz.node_pdp_client import ChatContentCheck, PdpHttpResponse

SCOPED_SECRET = "scoped-secret-for-tests"
ORG = "a" * 24
B, C = "user-b", "user-c"


class FakeConfig:
    def __init__(self, endpoint: str = "http://node:3000") -> None:
        self.endpoint = endpoint

    async def get_config(self, key: str, default: object = None, use_cache: bool = True) -> object:
        if key == "/services/endpoints":
            return {"cm": {"endpoint": self.endpoint}}
        if key == "/services/secretKeys":
            return {"scopedJwtSecret": SCOPED_SECRET}
        return default


class FakePdpHttp:
    """Scripted `PdpHttp`: each call pops the next outcome (a response or an exception)."""

    def __init__(self, *outcomes: object, default: object = None) -> None:
        self.outcomes = list(outcomes)
        self.default = default if default is not None else PdpHttpResponse(
            status=200, body={"allow": True, "aclVersion": None},
        )
        self.calls: list[dict[str, Any]] = []

    async def post_json(self, url: str, *, json: dict, headers: dict, timeout_s: float) -> PdpHttpResponse:
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout_s": timeout_s})
        outcome = self.outcomes.pop(0) if self.outcomes else self.default
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def allow(acl_version: int | None = None) -> PdpHttpResponse:
    return PdpHttpResponse(status=200, body={"allow": True, "aclVersion": acl_version})


def deny(acl_version: int | None = None) -> PdpHttpResponse:
    return PdpHttpResponse(status=200, body={"allow": False, "aclVersion": acl_version})


class Graph:
    """Just enough of IGraphDBProvider for the access helper."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, dict]] = defaultdict(dict)
        self.perm: list[dict] = []
        self.acl: set[tuple[str, str]] = set()  # (userId, recordId) the ACL query grants
        self.acl_calls: list[tuple] = []
        self.records_by_id: dict[str, Any] = {}
        self.vrid_map: dict[str, list[str]] = {}

    def user(self, user_id: str) -> None:
        self.docs["users"][f"key-{user_id}"] = {"_key": f"key-{user_id}", "userId": user_id}

    def attachment(self, rid: str, owner: str | None = B, org: str = ORG) -> None:
        self.docs["records"][rid] = {
            "_key": rid, "orgId": org, "connectorName": "ATTACHMENTS", "recordType": "FILE",
        }
        if owner:
            self.perm.append({"from_id": f"key-{owner}", "from_collection": "users", "to_id": rid,
                              "type": "USER", "role": "OWNER"})

    def artifact(self, rid: str, conv: str | None = "conv-1", owner: str = B, **extra) -> None:
        self.docs["records"][rid] = {
            "_key": rid, "orgId": ORG, "connectorName": "CODING_SANDBOX", "recordType": "ARTIFACT",
        }
        self.docs["artifacts"][rid] = {
            "_key": rid, "conversationId": conv, "runId": "run-1", "visibility": "VISIBLE",
            "isTemporary": False, **extra,
        }
        self.perm.append({"from_id": f"key-{owner}", "from_collection": "users", "to_id": rid,
                          "type": "USER", "role": "OWNER"})

    async def get_document(self, key: str, collection: str, *a: object, **k: object) -> dict | None:
        return self.docs[collection].get(key)

    async def get_edges_to_node(self, node_id: str, collection: str, *a: object) -> list[dict]:
        assert collection == "permission"
        rid = node_id.split("/", 1)[1]
        return [e for e in self.perm if e["to_id"] == rid]

    async def check_record_access_with_details(self, user_id: str, org_id: str, record_id: str) -> dict[str, dict[str, str]] | None:
        self.acl_calls.append((user_id, org_id, record_id))
        return {"record": {"id": record_id}} if (user_id, record_id) in self.acl else None

    async def get_record_by_id(self, record_id: str, *a: object, **k: object) -> object:
        return self.records_by_id.get(record_id)

    async def get_records_by_virtual_record_id(self, vrid: str, **k: object) -> list[str]:
        return self.vrid_map.get(vrid, [])


class FakeNodeRules:
    """A stand-in Node PDP: H4 consent + membership, evaluated per request."""

    def __init__(self) -> None:
        self.members: dict[str, set[str]] = {}  # conversation -> user ids
        self.files_shared: dict[tuple[str, str], bool] = {}  # (conversation, record) -> consent
        self.down = False

    async def can_read_chat_content(self, req: ChatContentCheck) -> bool:
        if self.down:
            return False
        convs = [req.conversation_id] if req.conversation_id else list(self.members)
        return any(
            req.user_id in self.members.get(c, set()) and self.files_shared.get((c, req.record_id), False)
            for c in convs
        )
