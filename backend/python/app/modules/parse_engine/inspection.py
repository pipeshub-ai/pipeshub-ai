"""The cheap inspection a heavy-format record gets before it takes its parse slot (``PARSE_ADMISSION=classes``).

Most record events carry no size, so the class a record is admitted under starts as a prior. Reading the page count
and the text layer on the inspect pool (never a parse slot) lets the cost estimator correct it; the consumer then
moves the record in place (``reclass_index_slot``). The inspection is best effort: no facts means the prior stands.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.modules.parse_engine import wiring
from app.modules.parse_engine.cost import FormatKind

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InspectedFacts:
    pages: int | None = None
    scanned: bool | None = None
    cells: int | None = None  # a workbook's cell area


def wants_inspection(extension: str | None, mime_type: str | None, content: object) -> bool:
    """A PDF or an Office document whose bytes are in hand, with classes on."""
    return (
        wiring.classes_enabled()
        and isinstance(content, (bytes, bytearray))
        and wiring.format_kind(extension, mime_type) in _INSPECTED
    )


_INSPECTED = (FormatKind.PDF, FormatKind.DOCX, FormatKind.PPTX, FormatKind.XLSX)


async def inspect_facts(
    content: bytes, *, label: str = "", extension: str | None = None, mime_type: str | None = None
) -> InspectedFacts:
    """Page count and scanned flag of a PDF, the page or slide count of a docx or pptx, or the cell area of an
    xlsx; empty when it cannot be read cheaply."""
    kind = wiring.format_kind(extension, mime_type)
    if kind in (FormatKind.DOCX, FormatKind.PPTX, FormatKind.XLSX):
        import asyncio

        from app.modules.parse_engine.office_facts import office_facts

        try:
            office = await asyncio.to_thread(office_facts, bytes(content))
        except Exception:  # best effort: nothing here may fail the record's attempt
            _logger.info("No Office facts for %s; its class stays on the size prior", label, exc_info=True)
            return InspectedFacts()
        return InspectedFacts(pages=office.pages, cells=office.cells)
    from app.modules.parsers.pdf.untrusted_pdf import inspect_for_class

    facts = await inspect_for_class(bytes(content), label=label)
    if facts is None or facts.page_count <= 0:
        return InspectedFacts()
    return InspectedFacts(pages=facts.page_count, scanned=facts.scanned)
