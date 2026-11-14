"""Shared pieces of the weekly tests."""

from __future__ import annotations

from typing import Any

from helper.clients.conversations_client import ConversationsClient
from helper.clients.kb_client import KBClient
from helper.clients.user_groups_client import UserGroupsClient
from helper.clients.users_client import UsersClient


def _total(body: Any, key: str) -> int:
    """The server's own total when the page carries one, else what the page lists."""
    pagination = body.get("pagination") or {}
    for name in ("totalCount", "totalItems", "total"):
        if isinstance(pagination.get(name), int):
            return pagination[name]
    return len(body.get(key) or [])


def snapshot(client: Any, kb_id: str | None = None) -> dict[str, int]:
    """What the API says exists now: users, user groups, KBs, conversations and, given a KB, its records."""
    def read(resp: Any, key: str) -> int:
        resp.raise_for_status()
        return _total(resp.json(), key)

    kb = KBClient(client)
    counts = {
        "users": read(UsersClient(client).get_all_users(limit=1), "users"),
        "user_groups": read(UserGroupsClient(client).get_all_groups(limit=1), "groups"),
        "knowledge_bases": read(kb.get("/", params={"limit": 1}), "knowledgeBases"),
        "conversations": read(ConversationsClient(client).list_conversations(limit=1), "conversations"),
    }
    if kb_id:
        counts["records_in_seeded_kb"] = _total(kb.list_records(kb_id, limit=200), "items")
    return counts
