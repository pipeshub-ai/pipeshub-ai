"""EU-23 (CPD-7 FB-3): an MCP instance store that cannot be read is `unavailable`, never "no MCP servers"."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.agents.agent_loop.error_classification import (
    MCP_UNAVAILABLE,
    classify_exception,
)
from app.agents.mcp.instance_scope import list_instances_for_org
from app.agents.mcp.service import MCPInstancesUnavailable, load_org_instances
from app.api.routes import mcp_servers


def _store(*, keys: object, records: list[object]) -> MagicMock:
    cfg = MagicMock()
    cfg.list_keys_in_directory = AsyncMock(side_effect=keys) if isinstance(keys, Exception) else AsyncMock(
        return_value=keys
    )
    cfg.get_config = AsyncMock(side_effect=records)
    return cfg


async def test_eu_23_a_store_that_cannot_list_is_unavailable_through_the_org_scope() -> None:
    with pytest.raises(MCPInstancesUnavailable) as raised:
        _ = await list_instances_for_org(_store(keys=ConnectionError("etcd down"), records=[]), "org-1")  # type: ignore[arg-type]
    assert raised.value.reason_code == "mcp_instances_unavailable"
    assert "etcd down" not in str(raised.value)


async def test_eu_23_a_store_whose_every_record_fails_is_unavailable_one_bad_record_is_skipped() -> None:
    with pytest.raises(MCPInstancesUnavailable):
        _ = await load_org_instances(_store(keys=["/a", "/b"], records=[TimeoutError(), TimeoutError()]), "org-1")  # type: ignore[arg-type]
    one = {"_id": "inst-1", "orgId": "org-1"}
    assert await load_org_instances(_store(keys=["/a", "/b"], records=[TimeoutError(), one]), "org-1") == [one]  # type: ignore[arg-type]
    assert await load_org_instances(_store(keys=[], records=[]), "org-1") == []  # type: ignore[arg-type]


async def test_eu_23_the_admin_list_answers_503_naming_the_reason() -> None:
    with pytest.raises(HTTPException) as raised:
        _ = await mcp_servers._visible_instances(  # pyright: ignore[reportPrivateUsage]
            _store(keys=ConnectionError("etcd down"), records=[]), "org-1",  # type: ignore[arg-type]
        )
    assert raised.value.status_code == 503
    assert raised.value.detail["reasonCode"] == "mcp_instances_unavailable"  # type: ignore[index]


def test_eu_23_a_run_ends_with_the_code_the_web_app_words() -> None:
    code, message = classify_exception(MCPInstancesUnavailable("MCP instances could not be listed: ConnectionError"))
    assert code == MCP_UNAVAILABLE == "mcp_unavailable"
    assert "ConnectionError" not in message


async def test_eu_23_the_assistant_agent_does_not_quietly_lose_its_mcp_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.mcp import instance_scope
    from app.api.routes.agent import get_assistant_agent

    async def unreadable(config_service: object, org_id: str, user_id: str | None = None) -> list[dict[str, object]]:
        raise MCPInstancesUnavailable("MCP instances could not be listed: ConnectionError")

    monkeypatch.setattr(instance_scope, "list_instances_for_org", unreadable)
    with pytest.raises(MCPInstancesUnavailable):
        _ = await get_assistant_agent(
            "user-1", "org-1", MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            actions_enabled=False, mcp_enabled=True, user_doc={"_key": "user-1"},
        )
