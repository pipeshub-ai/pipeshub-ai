"""Unit tests for `create_agent_from_task` -- Part A4's "promote to agent"
one-way copy. Uses an in-memory transactional `IGraphDBProvider` fake (only
the calls this module actually makes: `begin_transaction`,
`batch_upsert_nodes`, `batch_create_edges`, `get_document`,
`commit_transaction`/`rollback_transaction`) since the real adapter's own
correctness is proven by `test_task_store_contract.py`."""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.tasks.application.promote_to_agent import create_agent_from_task
from app.services.tasks.domain.errors import PrerequisiteError
from app.services.tasks.domain.models import TaskDefinition, TaskPrincipal


class FakeTransactionalGraphProvider:
    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}
        self.committed: list[str] = []
        self.rolled_back: list[str] = []
        self.fail_on_commit = False

    def _col(self, name: str) -> dict[str, dict[str, Any]]:
        return self._collections.setdefault(name, {})

    async def begin_transaction(self, read: list[str], write: list[str]) -> str:
        return str(uuid.uuid4())

    async def commit_transaction(self, transaction: str) -> None:
        if self.fail_on_commit:
            raise RuntimeError("commit failed")
        self.committed.append(transaction)

    async def rollback_transaction(self, transaction: str) -> None:
        self.rolled_back.append(transaction)

    async def batch_upsert_nodes(self, nodes: list[dict[str, Any]], collection: str, transaction: str | None = None) -> bool:
        col = self._col(collection)
        for node in nodes:
            col[str(node["_key"])] = dict(node)
        return True

    async def batch_create_edges(self, edges: list[dict[str, Any]], collection: str, transaction: str | None = None) -> bool:
        col = self._col(collection)
        for edge in edges:
            col[str(uuid.uuid4())] = dict(edge)
        return True

    async def get_document(self, document_key: str, collection: str, transaction: str | None = None) -> dict | None:
        return self._col(collection).get(document_key)

    def seed_skill(self, org_id: str, name: str, *, source: str = "builtin", created_by: str | None = None) -> None:
        key = f"{org_id}_{name}"
        self._col(CollectionNames.AGENT_SKILLS.value)[key] = {
            "_key": key, "orgId": org_id, "source": source, "createdBy": created_by,
        }


class FakeConfigService:
    def __init__(self, llm_configs: list[dict[str, Any]] | None = None) -> None:
        self._llm_configs = llm_configs if llm_configs is not None else [
            {"modelKey": "openai-key", "modelName": "gpt-4o"},
        ]

    async def get_config(self, key: str, use_cache: bool = False) -> dict[str, Any]:
        return {"llm": self._llm_configs}


def _make_task(**overrides: object) -> TaskDefinition:
    defaults: dict[str, Any] = {
        "org_id": "org-1",
        "created_by_user_id": "user-1",
        "principal": TaskPrincipal(org_id="org-1", user_id="user-1", user_email="a@b.com"),
        "title": "Daily digest",
        "description": "summarize tickets",
        "instructions": "Summarize yesterday's tickets",
    }
    defaults.update(overrides)
    return TaskDefinition(**defaults)


class TestCreateAgentFromTask:
    async def test_raises_when_no_model_configured(self) -> None:
        provider = FakeTransactionalGraphProvider()
        config_service = FakeConfigService(llm_configs=[])
        task = _make_task()
        with pytest.raises(PrerequisiteError):
            await create_agent_from_task(task, graph_provider=provider, config_service=config_service)

    async def test_creates_minimal_agent_with_no_tools_knowledge_or_skills(self) -> None:
        provider = FakeTransactionalGraphProvider()
        config_service = FakeConfigService()
        task = _make_task()
        agent_id = await create_agent_from_task(task, graph_provider=provider, config_service=config_service)

        agent_doc = provider._col(CollectionNames.AGENT_INSTANCES.value)[agent_id]
        assert agent_doc["name"] == "Daily digest"
        assert agent_doc["models"] == ["openai-key_gpt-4o"]
        assert agent_doc["isActive"] is True
        assert len(provider.committed) == 1
        assert provider.rolled_back == []

    async def test_creates_permission_edge_for_creator(self) -> None:
        provider = FakeTransactionalGraphProvider()
        config_service = FakeConfigService()
        task = _make_task(created_by_user_id="user-42")
        agent_id = await create_agent_from_task(task, graph_provider=provider, config_service=config_service)

        permission_edges = list(provider._col(CollectionNames.PERMISSION.value).values())
        assert len(permission_edges) == 1
        assert permission_edges[0]["_from"] == f"{CollectionNames.USERS.value}/user-42"
        assert permission_edges[0]["_to"] == f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}"
        assert permission_edges[0]["role"] == "OWNER"

    async def test_links_toolset_when_tool_names_present(self) -> None:
        provider = FakeTransactionalGraphProvider()
        config_service = FakeConfigService()
        task = _make_task(tool_names=["slack_send_message", "jira_create_issue"])
        await create_agent_from_task(task, graph_provider=provider, config_service=config_service)

        toolsets = list(provider._col(CollectionNames.AGENT_TOOLSETS.value).values())
        assert len(toolsets) == 1
        assert toolsets[0]["name"] == "workflow-agent-tools"

        tools = list(provider._col(CollectionNames.AGENT_TOOLS.value).values())
        assert {t["name"] for t in tools} == {"slack_send_message", "jira_create_issue"}

        agent_toolset_edges = provider._col(CollectionNames.AGENT_HAS_TOOLSET.value)
        assert len(agent_toolset_edges) == 1
        toolset_tool_edges = provider._col(CollectionNames.TOOLSET_HAS_TOOL.value)
        assert len(toolset_tool_edges) == 2

    async def test_no_toolset_created_when_tool_names_empty(self) -> None:
        provider = FakeTransactionalGraphProvider()
        config_service = FakeConfigService()
        task = _make_task(tool_names=[])
        await create_agent_from_task(task, graph_provider=provider, config_service=config_service)
        assert provider._col(CollectionNames.AGENT_TOOLSETS.value) == {}

    async def test_links_knowledge_from_collections_and_connectors(self) -> None:
        provider = FakeTransactionalGraphProvider()
        config_service = FakeConfigService()
        task = _make_task(collection_ids=["kb-1"], connector_ids=["conn-1"])
        agent_id = await create_agent_from_task(task, graph_provider=provider, config_service=config_service)

        knowledge_nodes = list(provider._col(CollectionNames.AGENT_KNOWLEDGE.value).values())
        assert {n["connectorId"] for n in knowledge_nodes} == {"kb-1", "conn-1"}
        knowledge_edges = provider._col(CollectionNames.AGENT_HAS_KNOWLEDGE.value)
        assert len(knowledge_edges) == 2
        assert all(e["_from"] == f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}" for e in knowledge_edges.values())

    async def test_links_owned_and_builtin_skills_only(self) -> None:
        provider = FakeTransactionalGraphProvider()
        provider.seed_skill("org-1", "digest-formatter", source="builtin")
        provider.seed_skill("org-1", "my-private-skill", source="custom", created_by="user-1")
        provider.seed_skill("org-1", "someone-elses-skill", source="custom", created_by="user-99")
        config_service = FakeConfigService()
        task = _make_task(
            created_by_user_id="user-1",
            skill_names=["digest-formatter", "my-private-skill", "someone-elses-skill", "nonexistent"],
        )
        await create_agent_from_task(task, graph_provider=provider, config_service=config_service)

        skill_edges = list(provider._col(CollectionNames.AGENT_HAS_SKILL.value).values())
        linked_names = {e["skillName"] for e in skill_edges}
        assert linked_names == {"digest-formatter", "my-private-skill"}

    async def test_rolls_back_transaction_on_failure(self) -> None:
        provider = FakeTransactionalGraphProvider()
        provider.fail_on_commit = True
        config_service = FakeConfigService()
        task = _make_task()
        with pytest.raises(RuntimeError):
            await create_agent_from_task(task, graph_provider=provider, config_service=config_service)
        assert len(provider.rolled_back) == 1
