"""The hints of an admission are handed from ``on_event`` to the PDF route (run through the page-limit test world)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.modules.indexing import extension
from app.modules.indexing.extension import (
    Admit,
    DefaultIndexingLifecycle,
    IndexingExtension,
    PdfParseHints,
)
from tests.unit.modules.indexing.test_page_limit import World

pytestmark = pytest.mark.asyncio


class Admitting(DefaultIndexingLifecycle):
    def __init__(self, admission: Admit) -> None:
        self._admission = admission

    async def admit(self, record: object, graph: object) -> Admit:
        return self._admission


async def test_the_hints_of_the_admission_are_handed_to_the_pdf_route() -> None:
    hints = PdfParseHints(allow_docling=False)
    extension.install(IndexingExtension(lifecycle=Admitting(Admit(hints))))
    world = World({"indexingStatus": "QUEUED"})
    world.count(10)
    seen: list[object] = []

    async def spy(*_a: object, **kw: object):  # noqa: ANN202
        seen.append(kw.get("hints"))
        return
        yield

    world.stack.enter_context(patch.object(world.ep, "_dispatch_pdf_binary", spy))
    await world.run()
    assert seen == [hints]


async def test_a_default_admission_hands_no_hints() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.count(10)
    seen: list[object] = []

    async def spy(*_a: object, **kw: object):  # noqa: ANN202
        seen.append(kw.get("hints", "missing"))
        return
        yield

    world.stack.enter_context(patch.object(world.ep, "_dispatch_pdf_binary", spy))
    await world.run()
    assert seen == [None]
