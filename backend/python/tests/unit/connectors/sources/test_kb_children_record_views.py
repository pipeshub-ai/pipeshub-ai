"""The knowledge-base listings pass their rows through the installed record view decorator, naming the surface, and show them as stored by default."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.record_views import DefaultRecordViewDecorator, set_decorator
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService

pytestmark = pytest.mark.asyncio


class Tag(DefaultRecordViewDecorator):
    def __init__(self) -> None:
        self.surfaces: list[str] = []

    async def decorate_rows(self, rows: list, graph: object, *, surface: str) -> list:
        self.surfaces.append(surface)
        return [{**r, "seen": surface} for r in rows]


def _service(result: dict) -> KnowledgeBaseService:
    svc = KnowledgeBaseService.__new__(KnowledgeBaseService)
    svc.logger = MagicMock()
    svc._resolve_user_and_kb_access = AsyncMock(return_value=("uk", "OWNER", None))
    svc.graph_provider = MagicMock()
    svc.graph_provider.get_kb_children = AsyncMock(return_value=dict(result))
    svc.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
    svc.graph_provider.get_folder_contents = AsyncMock(return_value=dict(result))
    svc.graph_provider.get_folder_children = AsyncMock(return_value=dict(result))
    return svc


RESULT = {"success": True, "records": [{"id": "a"}], "totalCount": 1, "counts": {"folders": 0, "records": 1}}


async def test_the_kb_root_listing_is_decorated_with_its_surface() -> None:
    tag = Tag()
    set_decorator(tag)

    out = await _service(RESULT).get_kb_children("kb", "u")

    assert tag.surfaces == ["kb_children"]
    assert out["records"] == [{"id": "a", "seen": "kb_children"}]


async def test_the_kb_root_listing_is_untouched_by_the_default_decorator() -> None:
    out = await _service(RESULT).get_kb_children("kb", "u")

    assert out["records"] == [{"id": "a"}]


async def test_an_empty_listing_is_not_decorated() -> None:
    tag = Tag()
    set_decorator(tag)

    await _service({"success": True, "records": [], "totalCount": 0, "counts": {"folders": 0, "records": 0}}).get_kb_children("kb", "u")

    assert tag.surfaces == []


async def test_the_folder_listing_is_decorated_with_its_surface() -> None:
    tag = Tag()
    set_decorator(tag)

    out = await _service(RESULT).get_folder_children("kb", "folder", "u")

    assert tag.surfaces == ["kb_children"]
    assert out["records"] == [{"id": "a", "seen": "kb_children"}]
