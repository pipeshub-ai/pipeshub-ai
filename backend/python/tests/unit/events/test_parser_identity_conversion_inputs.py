"""The key names every input the conversion depends on, not only the route.

Audit of the in-process handlers: all read the bytes and the route only, except the LibreOffice one, which converts from the
input format of the file type (``libreoffice_from``): an .odt and an .rtf share the route ``libreoffice_docx`` and are
converted differently. Every other route that several file types share (docx, pptx, xlsx, html, image, plain text, code)
reads them alike: the macro-enabled and template packages are rewritten to the plain one first, and the image handler takes
no format.
"""
from __future__ import annotations

import collections
import dataclasses
from unittest.mock import MagicMock

import pytest

from app.config import file_types
from app.events.processor import Processor
from app.modules.pipeline import parser_identity as pi
from tests.support.parser_identity_world import World

ODT = ("application/vnd.oasis.opendocument.text", "odt", "notes.odt")
RTF = ("application/rtf", "rtf", "notes.rtf")


def _every_handler_parses(world: World) -> None:
    for name in dir(Processor):
        if name.startswith("process_"):
            setattr(world.processor, name, MagicMock(side_effect=world._parse))


@pytest.fixture(autouse=True)
def _identity_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")
    monkeypatch.delenv("USE_PARSING_SERVICE", raising=False)
    pi.end_run_identity()


def test_an_odt_and_an_rtf_have_different_in_process_keys_and_the_same_service_formats_as_before() -> None:
    odt = pi.parser_key(ODT[0], ODT[1], ODT[2], "FILE", service=False)
    rtf = pi.parser_key(RTF[0], RTF[1], RTF[2], "FILE", service=False)
    assert odt == "libreoffice_docx.odt@1" and rtf == "libreoffice_docx.rtf@1"
    assert pi.parser_key(ODT[0], ODT[1], ODT[2], "FILE", service=True) == "odt@1"
    assert pi.parser_key(RTF[0], RTF[1], RTF[2], "FILE", service=True) == "rtf@1"


@pytest.mark.asyncio
async def test_a_same_bytes_rename_from_odt_to_rtf_runs_in_full() -> None:
    world = World(node={"mimeType": ODT[0], "recordName": ODT[2]}, file_doc={"_key": "rec-1", "extension": "odt"})
    _every_handler_parses(world)
    world.identified("inproc:libreoffice_docx.odt@1")
    world.state.update({"mimeType": RTF[0], "recordName": RTF[2]})
    world.files = {"_key": "rec-1", "extension": "rtf"}

    await world.run(world.event(mime=RTF[0], ext="rtf", name=RTF[2]))

    assert world.parses == 1
    assert world.state[pi.IDENTITY_FIELD] == "inproc:libreoffice_docx.rtf@1"


@pytest.mark.asyncio
async def test_a_same_bytes_update_of_an_odt_still_skips() -> None:
    world = World(node={"mimeType": ODT[0], "recordName": ODT[2]}, file_doc={"_key": "rec-1", "extension": "odt"})
    _every_handler_parses(world)
    world.identified("inproc:libreoffice_docx.odt@1")

    await world.run(world.event(mime=ODT[0], ext="odt", name=ODT[2]))

    assert world.parses == 0


def test_no_other_route_has_file_types_that_a_handler_converts_differently() -> None:
    """The one input beyond the route that the handlers read is ``libreoffice_from``; it differs only within one route."""
    types = {id(t): t for t in file_types._BY_EXTENSION.values()}.values()
    by_route: dict[object, set[object]] = collections.defaultdict(set)
    for file_type in types:
        if file_type.route is not None:
            by_route[file_type.route].add(file_type.libreoffice_from)
    assert {r.value for r, values in by_route.items() if len(values) > 1} == {"libreoffice_docx"}
    assert "libreoffice_from" in {f.name for f in dataclasses.fields(file_types.FileType)}
