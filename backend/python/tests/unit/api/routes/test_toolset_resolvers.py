"""Unit tests for OSS toolset_resolvers (EE-only seams are no-ops)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.api.routes.toolset_resolvers import (
    is_redacted_placeholder,
    load_instances_for_mutation,
    mask_oauth_secrets,
    resolve_inherited_from_org_id,
)


@pytest.mark.asyncio
async def test_resolve_inherited_always_none() -> None:
    result = await resolve_inherited_from_org_id(
        toolset_type="jira",
        oauth_config_id="oauth-1",
        org_id="org-1",
        config_service=AsyncMock(),
    )
    assert result is None


def test_mask_oauth_secrets_identity() -> None:
    data = {"clientId": "id", "clientSecret": "sekrit"}
    assert mask_oauth_secrets(data, is_inherited=True) == data
    assert is_redacted_placeholder("••••••••") is False


@pytest.mark.asyncio
async def test_load_instances_for_mutation_filters_org() -> None:
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(
        return_value=[
            {"_id": "a", "orgId": "org-1"},
            {"_id": "b", "orgId": "org-2"},
        ]
    )
    result = await load_instances_for_mutation("org-1", config_service)
    assert result == [{"_id": "a", "orgId": "org-1"}]
