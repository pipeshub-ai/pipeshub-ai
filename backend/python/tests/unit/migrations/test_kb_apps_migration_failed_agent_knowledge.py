"""The agent hub-knowledge step is not marked done when the provider reports a failed write.

The provider answers {"error": ...} instead of raising, and the step used to ignore it.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

from app.migrations.kb_apps_migration import KBAppsMigrationService


def _service(results: dict[str, dict]) -> KBAppsMigrationService:
    graph = MagicMock()
    graph.migrate_agent_hub_knowledge = AsyncMock(side_effect=lambda org_id: results[org_id])
    config = MagicMock()
    config.get_config = AsyncMock(return_value=None)
    config.set_config = AsyncMock()
    return KBAppsMigrationService(graph, config, logging.getLogger("test.kb_apps"))


async def test_a_failed_write_leaves_the_step_unmarked() -> None:
    svc = _service({
        "org-1": {"agents_migrated": 0, "knowledge_nodes_created": 0, "error": "Schema validation failed"},
        "org-2": {"agents_migrated": 2, "knowledge_nodes_created": 3},
    })

    assert await svc._migrate_agent_hub_knowledge_all_orgs([{"_key": "org-1"}, {"_key": "org-2"}]) == 2

    svc.config_service.set_config.assert_not_awaited()


async def test_a_clean_run_still_marks_the_step_done() -> None:
    svc = _service({"org-1": {"agents_migrated": 1, "knowledge_nodes_created": 1}})

    await svc._migrate_agent_hub_knowledge_all_orgs([{"_key": "org-1"}])

    svc.config_service.set_config.assert_awaited_once()
