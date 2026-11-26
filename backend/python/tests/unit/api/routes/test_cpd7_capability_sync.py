"""DW-1..DW-3 (CPD-7), query side: after an agent or an instance changed, Node is asked to sync it, and a sync
that fails never fails the write it follows. An org whose harness is off (ADR-OPS-08) is never synced and logs nothing.

The network edge is the one fake: ``_Httpx.post``, recording what would reach Node and answering a status.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

import app.edition_config  # noqa: F401 - binds the routes in the order their circular imports need
from app.agents import capability_sync
from app.agents.capability_sync import SYNC_PATH, CapabilitySync, sync_capabilities

SECRET = "cpd7-scoped-secret-0123456789abcdef"
NODE = "http://node.test:3000"


class Kv:
    def __init__(self, extra: dict[str, Any] | None = None, *, secret: bool = True, labs: bool = True) -> None:
        self.data: dict[str, Any] = {
            "/services/endpoints": {"nodejs": {"endpoint": NODE}},
            "/services/platform/settings": {"featureFlags": {"ENABLE_AGENT_HARNESS": labs}},
            **(extra or {}),
        }
        if secret:
            self.data["/services/secretKeys"] = {"scopedJwtSecret": SECRET}

    async def get_config(self, key: str, default: object = None, use_cache: bool = False, **_: object) -> object:
        return self.data.get(key, default)

    async def set_config(self, key: str, value: object) -> bool:
        self.data[key] = value
        return True

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return [k for k in self.data if k.startswith(directory)]

    async def delete_config(self, key: str) -> bool:
        return self.data.pop(key, None) is not None


class Node:
    """What reached Node: url, body and the token's claims; answers ``status`` or raises ``fail``."""

    def __init__(self, status: int = 200, fail: Exception | None = None) -> None:
        self.status, self.fail = status, fail
        self.calls: list[tuple[str, dict[str, object], dict[str, Any]]] = []

    async def post(self, url: str, *, json: dict[str, object], headers: dict[str, str]) -> int:
        if self.fail is not None:
            raise self.fail
        claims = jwt.decode(headers["Authorization"].removeprefix("Bearer "), SECRET, algorithms=["HS256"])
        self.calls.append((url, json, claims))
        return self.status


@pytest.fixture(autouse=True)
def _switch_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", "true")


@pytest.fixture
def node(monkeypatch: pytest.MonkeyPatch) -> Node:
    fake = Node()
    monkeypatch.setattr(capability_sync._Httpx, "post", lambda _self, url, *, json, headers: fake.post(url, json=json, headers=headers))
    return fake


async def test_dw1_an_agent_sync_names_the_agent_with_an_org_scoped_sync_token(node: Node) -> None:
    assert await sync_capabilities(Kv(), "org-a", agent_key="a7") is True  # type: ignore[arg-type]
    [(url, body, claims)] = node.calls
    assert url == f"{NODE}{SYNC_PATH}"
    assert body == {"agentKey": "a7"}
    assert claims["orgId"] == "org-a" and claims["scopes"] == ["capabilities:sync"] and "exp" in claims


@pytest.mark.parametrize(
    ("node_answer", "kv"),
    [(Node(status=503), Kv()), (Node(fail=ConnectionError("down")), Kv()), (Node(), Kv(secret=False))],
    ids=["refused", "unreachable", "no-secret"],
)
async def test_dw3_a_failed_sync_answers_false_and_never_raises(node_answer: Node, kv: Kv, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capability_sync._Httpx, "post", lambda _s, url, *, json, headers: node_answer.post(url, json=json, headers=headers))
    with patch.object(capability_sync._log, "warning") as warning:
        assert await sync_capabilities(kv, "org-a") is False  # type: ignore[arg-type]
    assert warning.call_count == 1
    assert warning.call_args.kwargs["reasonCode"] in {"control_plane_refused", "control_plane_unreachable", "service_secret_unavailable"}


async def test_dw1_deleting_an_agent_syncs_its_grant_and_a_refusal_does_not_fail_the_delete(node: Node) -> None:
    from app.api.routes.agent import delete_agent

    graph = AsyncMock()
    graph.check_agent_permission = AsyncMock(return_value={"can_edit": True, "can_delete": True})
    graph.get_agent = AsyncMock(return_value={"isServiceAccount": False, "_key": "a1"})
    graph.begin_transaction = AsyncMock(return_value="txn1")
    graph.delete_agent = AsyncMock(return_value=True)
    graph.commit_transaction = AsyncMock()
    services = {"graph_provider": graph, "config_service": Kv(), "logger": MagicMock(), "capability_sync": CapabilitySync(Kv())}  # type: ignore[arg-type]
    node.status = 503
    with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
         patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "org-a"}), \
         patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
        result = await delete_agent(MagicMock(), "a1")
    assert result.status_code == 200
    assert [(body, claims["orgId"]) for _url, body, claims in node.calls] == [({"agentKey": "a1"}, "org-a")]


async def test_dw2_creating_and_deleting_a_toolset_instance_syncs_the_orgs_instances(node: Node) -> None:
    from app.api.routes.toolsets import create_toolset_instance, delete_toolset_instance

    kv = Kv({"/services/toolset-instances": []})
    request = MagicMock()
    request.body = AsyncMock(return_value=json.dumps({"instanceName": "Calc", "toolsetType": "calculator", "authType": "NONE"}).encode())
    request.app.state.toolset_registry.get_toolset_metadata.return_value = {
        "name": "calculator", "display_name": "Calculator", "supported_auth_types": ["NONE"], "tools": [],
    }
    graph = MagicMock()
    graph.check_toolset_instance_in_use = AsyncMock(return_value=[])
    with patch.multiple(
        "app.api.routes.toolsets",
        _get_user_context=MagicMock(return_value={"user_id": "u1", "org_id": "org-a"}),
        _check_user_is_admin=AsyncMock(return_value=True),
        _get_graph_provider=MagicMock(return_value=graph),
    ):
        created = await create_toolset_instance(request, config_service=kv)  # type: ignore[arg-type]
        await delete_toolset_instance(created["instance"]["_id"], MagicMock(), config_service=kv)  # type: ignore[arg-type]
    assert [(body, claims["orgId"]) for _url, body, claims in node.calls] == [
        ({"installations": True}, "org-a"),
        ({"installations": True}, "org-a"),
    ]


@pytest.mark.parametrize("switch", ["true", "false"], ids=["labs-off", "switch-off"])
async def test_an_org_whose_harness_is_off_is_never_synced_and_logs_nothing(
    switch: str, node: Node, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", switch)
    with patch.object(capability_sync._log, "warning") as warning:
        assert await sync_capabilities(Kv(labs=False), "org-a", agent_key="a7") is False  # type: ignore[arg-type]
        assert await sync_capabilities(Kv(labs=False, secret=False), "org-a") is False  # type: ignore[arg-type]
    assert node.calls == []
    assert warning.call_count == 0
