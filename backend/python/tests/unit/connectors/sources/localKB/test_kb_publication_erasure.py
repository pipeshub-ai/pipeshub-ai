"""P2.13 ER-1: deleting a knowledge base removes its publications (flag on or off); a publication whose container
is gone anyway (a missed erasure, a connector removed) grants nothing and is erased at the next read."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.connectors.sources.localKB.handlers import kb_service as module
from app.harness.knowledge.service import Container, KnowledgePublications
from harness_core.knowledge.memory import InMemoryPublicationDirectory
from harness_core.knowledge.publications import Audience, Principal, PublicationDraft


def _deletable(service: Any) -> None:  # noqa: ANN401
    service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
    service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    service.graph_provider.delete_connector_instance = AsyncMock(return_value={"success": True, "virtual_record_ids": []})


async def test_er1_deleting_a_knowledge_base_erases_its_publications(service: Any) -> None:  # noqa: ANN401
    _deletable(service)
    publications = MagicMock()
    publications.erase_container = AsyncMock(return_value=2)
    with patch.object(module, "KnowledgePublications", return_value=publications):
        result = await service.delete_knowledge_base("kb1", "user1", "org1")
    assert result["success"] is True
    publications.erase_container.assert_awaited_once_with("org1", "apps/kb1")


async def test_er1_an_erasure_failure_does_not_fail_the_delete(service: Any) -> None:  # noqa: ANN401
    _deletable(service)
    publications = MagicMock()
    publications.erase_container = AsyncMock(side_effect=ConnectionError("graph down"))
    with patch.object(module, "KnowledgePublications", return_value=publications):
        result = await service.delete_knowledge_base("kb1", "user1", "org1")
    assert result["success"] is True


async def test_er1_an_orphaned_publication_grants_nothing_and_is_erased() -> None:
    directory = InMemoryPublicationDirectory()
    publications = KnowledgePublications(None, None, directory=directory, now_ms=lambda: 10)  # type: ignore[arg-type]

    async def gone(org_id: str, qualified: str) -> Container | None:
        return None

    publications.container = gone  # type: ignore[method-assign]
    await directory.create(PublicationDraft("org1", "apps/kb1", Audience(org=True), "full", "owner"), publication_id="p1", now_ms=1)
    assert await publications.admitting(Principal("org1", "alice")) == ()
    assert await directory.list_for_org("org1") == []
