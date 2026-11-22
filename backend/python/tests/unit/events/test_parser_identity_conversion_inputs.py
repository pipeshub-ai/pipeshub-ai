"""The key names every input the conversion depends on, not only the route.

Audit of the in-process handlers: they read the bytes and the route, except two. The LibreOffice handler converts from the
input format of the file type (``libreoffice_from``): an .odt and an .rtf share the route ``libreoffice_docx`` and are
converted differently. The image handler reads the extension of the record's mime (``data:image/<ext>``, svg to png). Every
other route that several file types share (docx, pptx, xlsx, html, plain text, code) reads them alike: the macro-enabled and
template packages are rewritten to the plain one first. The record's other labels only title and place it.
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


IMAGE_MIMES = ["image/png", "image/jpeg", "image/webp", "image/svg+xml", "image/jpg"]


def test_the_image_key_names_the_extension_the_handler_reads_from_the_mime() -> None:
    from app.utils.image_utils import get_extension_from_mimetype

    keys = {}
    for mime in IMAGE_MIMES:
        key = pi.parser_key(mime, None, "pic", "FILE", service=False)
        assert key == f"image.{get_extension_from_mimetype(mime)}@1"
        keys[mime] = key
    assert len(set(keys.values())) == len(IMAGE_MIMES)


@pytest.mark.asyncio
async def test_a_same_bytes_image_whose_mime_changes_runs_in_full() -> None:
    world = World(node={"mimeType": "image/png", "recordName": "pic.png"}, file_doc={"_key": "rec-1", "extension": "png"})
    _every_handler_parses(world)
    world.identified("inproc:image.png@1")
    world.state.update({"mimeType": "image/jpeg", "recordName": "pic.jpeg"})
    world.files = {"_key": "rec-1", "extension": "jpeg"}

    await world.run(world.event(mime="image/jpeg", ext="jpeg", name="pic.jpeg"))

    assert world.parses == 1
    assert world.state[pi.IDENTITY_FIELD] == "inproc:image.jpeg@1"


def test_only_the_image_handler_reads_the_record_mime() -> None:
    """The audit of the handlers' own reads of the record: ``process_image`` is the one that takes the extension from it."""
    import inspect

    from app.events.processor import Processor

    readers = [
        name for name, fn in inspect.getmembers(Processor, inspect.isfunction)
        if name.startswith("process_") and "mimeType" in inspect.getsource(fn)
    ]
    assert readers == ["process_image"]
