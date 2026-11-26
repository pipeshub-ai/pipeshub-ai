"""An operator script starts with no org bound, and the PostgreSQL graph runs every statement under one org: each script that acts
for one org (``--org``) binds it around its work."""
from __future__ import annotations

import asyncio
import contextvars
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.containers import indexing
from app.core import org_context
from app.scripts import (
    content_document_dates,
    kg_record_people,
    kg_taxonomy,
    named_entity_reextract,
)


class _Stop(ValueError):
    """Ends the run once the work has seen its binding (every script reports a ValueError and exits)."""


@pytest.mark.parametrize(("module", "work", "argv"), [
    (content_document_dates, "backfill_content_dates", ["--org", "org-7"]),
    (named_entity_reextract, "queue_reextractions", ["--org", "org-7"]),
    (kg_record_people, "backfill", ["backfill", "--org", "org-7"]),
    (kg_taxonomy, "run", ["duplicates", "--org", "org-7"]),
])
async def test_the_work_runs_under_the_org_given(monkeypatch: pytest.MonkeyPatch, module, work: str, argv: list[str]) -> None:
    container = MagicMock()
    container.logger.return_value = logging.getLogger("script-org")
    container.graph_provider = AsyncMock(return_value=AsyncMock())
    container.entity_vector_store = AsyncMock(return_value=None)
    monkeypatch.setattr(indexing.IndexingAppContainer, "init", MagicMock(return_value=container))
    seen: list[str] = []

    async def record(*_: object, **__: object) -> None:
        seen.append(org_context.current_org_id())
        raise _Stop("stop")
    monkeypatch.setattr(module, work, record)

    # As ``asyncio.run`` starts the script: an empty context.
    await asyncio.get_running_loop().create_task(module._main(argv), context=contextvars.Context())

    assert seen == ["org-7"]
