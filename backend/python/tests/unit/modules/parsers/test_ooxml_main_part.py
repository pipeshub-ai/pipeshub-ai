"""docm, dotx and potx rewritten as the plain package they hold: what changes, what is dropped, and
what is refused before any member is read."""
from __future__ import annotations

import io
import zipfile

import docx
import pptx
import pytest
from lxml import etree

from app.modules.parsers.ooxml.main_part import as_plain_package
from app.services.parsing.interface import UnreadableFileError, damaged_file_reason
from tests.support.new_type_samples import (
    DOCM_MAIN,
    DOTX_MAIN,
    POTX_MAIN,
    VBA_MARKER,
    _rewrite,
    make_docm,
    make_dotx,
    make_potx,
)
from tests.unit.modules.parsers.real_files.samples import make_docx, make_pptx

_CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_WORD_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_SLIDES_MAIN = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"


def _content_types(package: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        root = etree.fromstring(archive.read("[Content_Types].xml"))
    return {o.get("PartName"): o.get("ContentType") for o in root.iter(f"{_CT}Override")}


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in members:
            archive.writestr(zipfile.ZipInfo(name), data)
    return out.getvalue()


def _members(package: bytes) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        return [(i.filename, archive.read(i)) for i in archive.infolist()]


@pytest.mark.parametrize(
    ("label", "make", "family", "reader", "plain"),
    [
        ("docm", make_docm, "word", docx.Document, _WORD_MAIN),
        ("dotx", make_dotx, "word", docx.Document, _WORD_MAIN),
        ("potx", make_potx, "slides", pptx.Presentation, _SLIDES_MAIN),
    ],
)
def test_the_main_part_becomes_the_plain_one_and_the_library_opens_it(label, make, family, reader, plain) -> None:
    original = make("Sentinel 9131")
    with pytest.raises(ValueError):
        reader(io.BytesIO(original))
    rewritten = as_plain_package(original, family, label=label)
    reader(io.BytesIO(rewritten))
    main = "/word/document.xml" if family == "word" else "/ppt/presentation.xml"
    assert _content_types(rewritten)[main] == plain
    before, after = _content_types(original), _content_types(rewritten)
    assert {k: v for k, v in after.items() if k != main} == {
        k: v for k, v in before.items() if k != main and "vbaProject" not in k
    }


def test_every_member_but_the_vba_project_is_copied_unchanged() -> None:
    original = make_docm("Sentinel")
    rewritten = as_plain_package(original, "word", label="docm")
    before, after = dict(_members(original)), dict(_members(rewritten))
    assert set(before) - set(after) == {"word/vbaProject.bin"}
    for name, data in after.items():
        if name not in ("[Content_Types].xml", "word/_rels/document.xml.rels"):
            assert data == before[name], name
    assert next(iter(after)) == "[Content_Types].xml"


def test_the_macros_are_gone_from_the_bytes_and_the_relationships() -> None:
    original = make_docm("Sentinel")
    assert VBA_MARKER in zipfile.ZipFile(io.BytesIO(original)).read("word/vbaProject.bin")
    rewritten = as_plain_package(original, "word", label="docm")
    names = zipfile.ZipFile(io.BytesIO(rewritten)).namelist()
    assert not [n for n in names if "vba" in n.lower()]
    rels = zipfile.ZipFile(io.BytesIO(rewritten)).read("word/_rels/document.xml.rels")
    assert b"vbaProject" not in rels
    assert VBA_MARKER not in rewritten


@pytest.mark.parametrize(
    ("main", "family"),
    [
        ("application/vnd.ms-word.template.macroEnabledTemplate.main+xml", "word"),
        (DOTX_MAIN, "word"),
        (DOCM_MAIN, "word"),
        ("application/vnd.ms-powerpoint.template.macroEnabled.main+xml", "slides"),
        (POTX_MAIN, "slides"),
    ],
)
def test_each_rewritten_content_type_maps_to_the_plain_one(main: str, family: str) -> None:
    if family == "word":
        source, plain_main, plain = make_docx("T", ["Sentinel"]), _WORD_MAIN, _WORD_MAIN
    else:
        source, plain_main, plain = make_pptx([("T", "Sentinel")]), _SLIDES_MAIN, _SLIDES_MAIN
    package = _rewrite(source, plain_main=plain_main, main=main, vba_dir=None)
    rewritten = as_plain_package(package, family, label="x")
    assert plain in _content_types(rewritten).values()


def test_a_plain_package_and_a_macro_enabled_deck_pass_through() -> None:
    plain_docx = make_docx("T", ["Sentinel"])
    assert _WORD_MAIN in _content_types(as_plain_package(plain_docx, "word", label="docx")).values()
    pptm_main = "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml"
    pptm = _rewrite(make_pptx([("T", "x")]), plain_main=_SLIDES_MAIN, main=pptm_main, vba_dir=None)
    assert pptm_main in _content_types(as_plain_package(pptm, "slides", label="pptm")).values()


def test_the_namespace_prefixes_of_the_rewritten_parts_are_kept() -> None:
    rewritten = as_plain_package(make_docm("Sentinel"), "word", label="docm")
    types = zipfile.ZipFile(io.BytesIO(rewritten)).read("[Content_Types].xml")
    assert b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' in types
    rels = zipfile.ZipFile(io.BytesIO(rewritten)).read("word/_rels/document.xml.rels")
    assert b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' in rels


def _damaged(label: str = "docm") -> str:
    return damaged_file_reason(label)


def test_a_deck_handed_to_the_word_rewrite_is_refused() -> None:
    with pytest.raises(UnreadableFileError) as caught:
        as_plain_package(make_potx("x"), "word", label="docm")
    assert caught.value.message == _damaged()


@pytest.mark.parametrize(
    "content",
    [b"", b"random bytes that are not a zip", b"PK\x03\x04" + b"\x00" * 40],
    ids=["empty", "random", "truncated header"],
)
def test_bytes_that_are_not_a_package_are_damaged(content: bytes) -> None:
    with pytest.raises(UnreadableFileError) as caught:
        as_plain_package(content, "word", label="docm")
    assert caught.value.details == {"extension": "docm", "user_reason": _damaged()}


def test_a_truncated_package_is_damaged() -> None:
    data = make_docm("Sentinel")
    with pytest.raises(UnreadableFileError):
        as_plain_package(data[: len(data) // 2], "word", label="docm")


def test_duplicate_member_names_are_refused_before_any_is_read() -> None:
    members = _members(make_docm("Sentinel"))
    out = io.BytesIO()
    with pytest.warns(UserWarning, match="Duplicate name"), zipfile.ZipFile(out, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)
        archive.writestr("word/document.xml", b"<w:document/>")
    with pytest.raises(UnreadableFileError):
        as_plain_package(out.getvalue(), "word", label="docm")


@pytest.mark.parametrize("name", ["../evil.xml", "/abs/evil.xml", "word\\evil.xml", "C:evil.xml", "word/../../x"])
def test_unsafe_member_names_are_refused(name: str) -> None:
    members = [*_members(make_docm("Sentinel")), (name, b"x")]
    with pytest.raises(UnreadableFileError):
        as_plain_package(_zip(members), "word", label="docm")


def test_a_member_declared_past_the_size_limits_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from docling.datamodel import backend_options

    class _Tiny(backend_options.MsWordBackendOptions):
        max_total_bytes: int = 1000

    monkeypatch.setattr(backend_options, "MsWordBackendOptions", _Tiny)
    with pytest.raises(UnreadableFileError):
        as_plain_package(make_docm("Sentinel"), "word", label="docm")


def test_an_external_entity_is_not_resolved() -> None:
    members = dict(_members(make_dotx("Sentinel")))
    members["[Content_Types].xml"] = (
        b'<?xml version="1.0"?><!DOCTYPE t [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        + members["[Content_Types].xml"].split(b"?>", 1)[1].replace(b"</Types>", b"<!-- &x; --></Types>")
    )
    rewritten = as_plain_package(_zip(list(members.items())), "word", label="dotx")
    assert b"root:" not in zipfile.ZipFile(io.BytesIO(rewritten)).read("[Content_Types].xml")


def test_a_package_without_its_main_part_s_override_is_damaged() -> None:
    members = dict(_members(make_dotx("Sentinel")))
    members["[Content_Types].xml"] = members["[Content_Types].xml"].replace(
        b'PartName="/word/document.xml"', b'PartName="/word/other.xml"'
    )
    with pytest.raises(UnreadableFileError):
        as_plain_package(_zip(list(members.items())), "word", label="dotx")
