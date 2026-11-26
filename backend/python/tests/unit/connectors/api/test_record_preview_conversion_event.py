"""The ``record_preview_conversion`` log event: one per converter run, with the record and outcome, no user."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.responses import Response

import app.edition_config  # noqa: F401  (binds the edition seams before the router loads)
from app.connectors.api import router as router_mod


def _events(logger: MagicMock) -> list[str]:
    return [c.args[0] % c.args[1:] for c in logger.info.call_args_list if c.args and "record_preview_conversion" in c.args[0]]


async def test_one_ok_event_per_converter_run(tmp_path) -> None:
    pdf = tmp_path / "out.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with patch.object(router_mod, "logger") as log, \
            patch.object(router_mod, "convert_to_pdf", new=AsyncMock(return_value=str(pdf))):
        await router_mod.convert_buffer_to_pdf_stream(
            Response(b"pptx bytes"), "deck.pptx", "pptx", record_id="rec-1",
        )
    assert _events(log) == ["record_preview_conversion record_id=rec-1 variant=pdf outcome=ok"]


async def test_a_failed_run_is_one_failed_event() -> None:
    with patch.object(router_mod, "logger") as log, \
            patch.object(router_mod, "convert_to_pdf", new=AsyncMock(side_effect=RuntimeError("soffice died"))):
        with pytest.raises(RuntimeError):
            await router_mod.convert_buffer_to_pdf_stream(
                Response(b"pptx bytes"), "deck.pptx", "pptx", record_id="rec-1",
            )
    assert _events(log) == ["record_preview_conversion record_id=rec-1 variant=pdf outcome=failed"]


async def test_a_refused_buffer_runs_no_converter_and_logs_no_event() -> None:
    bad = MagicMock(spec=Response)
    bad.body = "not-bytes"
    with patch.object(router_mod, "logger") as log, patch.object(router_mod, "convert_to_pdf", new=AsyncMock()) as run:
        with pytest.raises(HTTPException):
            await router_mod.convert_buffer_to_pdf_stream(bad, "deck.pptx", "pptx", record_id="rec-1")
    run.assert_not_awaited()
    assert _events(log) == []
