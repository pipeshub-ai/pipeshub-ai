"""odt and rtf: converted by LibreOffice to DOCX, through both pipelines and the record handler.

The conversions need LibreOffice Writer; CI installs it (``libreoffice-writer-nogui``) and fails
the run if any test here is skipped. Damaged files, a hung converter and an encrypted .odt use a
stand-in ``libreoffice`` (or none at all) and run everywhere.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from typing import TYPE_CHECKING

import pytest

from app.services.parsing.interface import damaged_file_reason
from app.utils import libreoffice_convert
from tests.support import new_type_samples as samples

from .new_type_harness import handler_run, legacy
from .samples import all_text

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

SENTINEL = "Sentinel-4410 converted"
_GOOD = {"odt": samples.make_odt(SENTINEL), "rtf": samples.make_rtf(SENTINEL)}

pytestmark = pytest.mark.timeout(300)
needs_libreoffice = pytest.mark.skipif(
    shutil.which("libreoffice") is None, reason="LibreOffice Writer is not installed on this host"
)


@needs_libreoffice
@pytest.mark.parametrize("how", ["own", "application/octet-stream", "text/plain"])
@pytest.mark.parametrize("ext", ["odt", "rtf"])
async def test_the_legacy_pipeline_reads_it(ext: str, how: str) -> None:
    from app.config import file_types

    mime = file_types.by_extension(ext).canonical_mime if how == "own" else how  # type: ignore[union-attr]
    containers, docling_inputs, _ = await legacy(ext, mime, _GOOD[ext])
    assert SENTINEL in "\n".join(all_text(c) for c in containers)
    assert docling_inputs and docling_inputs[0][:2] == b"PK"


@needs_libreoffice
@pytest.mark.parametrize("ext", ["odt", "rtf"])
async def test_the_parsing_service_reads_it(ext: str) -> None:
    from app.modules.parsers.docx.docparser import DocParser
    from app.modules.parsers.pdf.docling_processor import DoclingProcessor
    from app.services.parsing.providers.local_docling_parser import LocalDoclingParser

    from .new_type_harness import LOGGER

    docling = DoclingProcessor(LOGGER, None)
    result = await DocParser(LocalDoclingParser(docling), input_ext=ext).parse(_GOOD[ext], f"Sample.{ext}")
    from docling_core.types.doc.document import DoclingDocument

    container = await docling.create_blocks(
        DoclingDocument.model_validate_json(result.raw_document), skip_table_enrichment=True
    )
    assert SENTINEL in all_text(container)


@pytest.mark.parametrize("ext", ["odt", "rtf"])
async def test_a_file_libreoffice_cannot_open_fails_on_the_first_attempt_with_its_reason(
    ext: str, fake_libreoffice: Callable[..., None]
) -> None:
    fake_libreoffice()
    record, _ = await handler_run(ext, None, b"\x13\x37 damaged " * 20)
    assert record["indexingStatus"] == "FAILED"
    assert record["reason"] == damaged_file_reason(ext)


async def test_the_parsing_service_reports_the_same_reason(fake_libreoffice: Callable[..., None]) -> None:
    from unittest.mock import MagicMock

    from app.modules.parsers.docx.docparser import DocParser
    from app.services.parsing.interface import UnreadableFileError

    fake_libreoffice()
    with pytest.raises(UnreadableFileError) as caught:
        await DocParser(MagicMock(), input_ext="rtf").parse(b"damaged", "Sample.rtf")
    assert caught.value.details["user_reason"] == damaged_file_reason("rtf")


async def test_a_hung_converter_ends_failed_with_no_process_left(
    fake_libreoffice: Callable[..., None], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "converter.pid"
    fake_libreoffice(body=f"echo $$ > '{pid_file}'\ntrap '' TERM\nexec sleep 60\n")
    monkeypatch.setattr(libreoffice_convert, "LIBREOFFICE_CONVERT_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(libreoffice_convert, "_STOP_GRACE_SECONDS", 0.3)
    record, _ = await handler_run("odt", None, _GOOD["odt"])
    assert record["indexingStatus"] == "FAILED"
    pid = int(pid_file.read_text())
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_an_encrypted_odt_is_password_protected_before_libreoffice_runs(
    fake_libreoffice: Callable[..., None], tmp_path: Path
) -> None:
    fake_libreoffice()
    record, _ = await handler_run("odt", None, samples.make_odt(SENTINEL, encrypted=True))
    assert record["indexingStatus"] == "PASSWORD_PROTECTED"
    with contextlib.suppress(FileNotFoundError):
        assert "--convert-to" not in (tmp_path / "libreoffice-args.log").read_text()


@pytest.mark.parametrize("ext", ["odt", "rtf"])
async def test_an_empty_file_is_empty(ext: str) -> None:
    _, _, record = await legacy(ext, None, b"")
    assert record["indexingStatus"] == "EMPTY"
