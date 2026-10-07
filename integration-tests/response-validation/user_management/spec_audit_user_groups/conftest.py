"""Shared fixtures for the strict OpenAPI audit of /api/v1/userGroups."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.user_groups_client import UserGroupsClient  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from strict_openapi import assert_strict_openapi_exchange  # noqa: E402
from user_groups_audit_support import (  # noqa: E402
    GROUP_READ_SCOPE,
    GROUPS_ROUTE,
    NO_GROUP_SCOPE,
    ScopedCaller,
    forget_access_token,
    mint_scoped_token,
    purge_groups,
    unique_group_name,
)

GroupFactory = Callable[..., dict[str, Any]]


@pytest.fixture
def created_group_ids(user_groups_client: UserGroupsClient) -> Iterator[list[str]]:
    """Ids of groups a test created; each is deleted on teardown and its row removed."""
    ids: list[str] = []
    yield ids
    try:
        for group_id in ids:
            resp = user_groups_client.delete_group(group_id)
            assert resp.status_code in (200, 404), f"cleanup of group {group_id}: {resp.status_code} {resp.text[:300]}"
    finally:
        purge_groups(ids)


@pytest.fixture
def make_group(user_groups_client: UserGroupsClient, created_group_ids: list[str]) -> GroupFactory:
    """Create a custom group as the admin and return it."""

    def _make(**fields: Any) -> dict[str, Any]:
        fields.setdefault("name", unique_group_name())
        fields.setdefault("type", "custom")
        resp = user_groups_client.post("/", json=fields)
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_exchange(resp, GROUPS_ROUTE)
        group = resp.json()
        created_group_ids.append(group["_id"])
        return group

    return _make


@pytest.fixture
def group(make_group: GroupFactory) -> dict[str, Any]:
    return make_group()


def _scoped(pipeshub_client: PipeshubClient, scope: str) -> Iterator[ScopedCaller]:
    token = mint_scoped_token(pipeshub_client.base_url, scope, pipeshub_client.timeout_seconds)
    try:
        yield ScopedCaller(pipeshub_client.base_url, token, pipeshub_client.timeout_seconds)
    finally:
        forget_access_token(token)


@pytest.fixture
def no_group_scope(pipeshub_client: PipeshubClient) -> Iterator[ScopedCaller]:
    """OAuth token of the suite's client that holds no ``usergroup:*`` scope."""
    yield from _scoped(pipeshub_client, NO_GROUP_SCOPE)


@pytest.fixture
def group_read_scope(pipeshub_client: PipeshubClient) -> Iterator[ScopedCaller]:
    """OAuth token of the suite's client that holds ``usergroup:read`` and not ``usergroup:write``."""
    yield from _scoped(pipeshub_client, GROUP_READ_SCOPE)
