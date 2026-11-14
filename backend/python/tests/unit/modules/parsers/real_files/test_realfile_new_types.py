"""msg, xlsm, log, cfg, docm, dotx and potx from real files, through both pipelines and the record handler.

odt and rtf need LibreOffice and are in ``test_realfile_libreoffice_types``.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.config import file_types
from app.services.parsing.interface import damaged_file_reason
from app.utils.user_errors import binary_text_file, file_too_large_to_read
from tests.support import new_type_samples as samples
from tests.support.office_samples import (
    encrypted_docm,
    encrypted_docx,
    encrypted_pptx,
    encrypted_xlsm,
)

from .new_type_harness import handler_run, legacy, service
from .samples import all_text

SENTINEL = "Sentinel-7731 quarterly"
_GOOD = {
    "docm": samples.make_docm(SENTINEL),
    "dotx": samples.make_dotx(SENTINEL),
    "potx": samples.make_potx(SENTINEL),
    "xlsm": samples.make_xlsm(SENTINEL),
    "log": samples.make_log(SENTINEL),
    "cfg": samples.make_cfg(SENTINEL),
}
_MSG_TEXT = "Sicher gesendet mit Tutanota"
# How a connector stores each: the type's own mime, octet-stream (an object store), or text/plain
# (a repository walker's default).
_MIMES = ("own", "application/octet-stream", "text/plain")

pytestmark = pytest.mark.timeout(240)


def _mime(ext: str, how: str) -> str | None:
    if how != "own":
        return how
    return file_types.by_extension(ext).canonical_mime  # type: ignore[union-attr]


def _text(containers: list) -> str:
    return "\n".join(all_text(c) for c in containers)


@pytest.mark.parametrize("how", _MIMES)
@pytest.mark.parametrize("ext", sorted(_GOOD))
async def test_the_legacy_pipeline_reads_each_type(ext: str, how: str) -> None:
    containers, _, _ = await legacy(ext, _mime(ext, how), _GOOD[ext])
    assert len(containers) == 1
    assert SENTINEL in _text(containers)


@pytest.mark.parametrize("how", _MIMES)
async def test_the_legacy_pipeline_reads_a_mail(how: str) -> None:
    containers, _, _ = await legacy("msg", _mime("msg", how), samples.msg_with_html_body())
    assert _MSG_TEXT in _text(containers)


@pytest.mark.parametrize("ext", sorted(_GOOD))
async def test_the_parsing_service_reads_each_type(ext: str) -> None:
    assert SENTINEL in all_text(await service(ext, _GOOD[ext]))


async def test_the_parsing_service_reads_a_mail() -> None:
    assert _MSG_TEXT in all_text(await service("msg", samples.msg_with_html_body()))


@pytest.mark.parametrize("ext", ["docm", "dotx", "potx"])
async def test_docling_is_handed_the_plain_package_without_its_macros(ext: str) -> None:
    _, docling_inputs, _ = await legacy(ext, None, _GOOD[ext])
    assert len(docling_inputs) == 1
    names = zipfile.ZipFile(io.BytesIO(docling_inputs[0])).namelist()
    assert not [n for n in names if "vba" in n.lower()]
    assert samples.VBA_MARKER not in docling_inputs[0]


async def test_the_macro_project_never_reaches_the_service_s_docling(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.parsing.providers.local_docling_parser import LocalDoclingParser

    seen: list[bytes] = []
    real = LocalDoclingParser.parse

    async def spy(self: LocalDoclingParser, content: bytes, name: str, config: object = None) -> object:
        seen.append(content)
        assert name.endswith(".docx")
        return await real(self, content, name, config)

    monkeypatch.setattr(LocalDoclingParser, "parse", spy)
    await service("docm", _GOOD["docm"])
    assert seen and samples.VBA_MARKER not in seen[0]


async def test_a_long_log_keeps_its_first_and_last_lines_and_says_what_it_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.parsers.text import capped_text

    monkeypatch.setattr(capped_text, "TEXT_LOG_MAX_BYTES", 50_000)
    monkeypatch.setattr(capped_text.prepare_capped_text, "__kwdefaults__", {"max_bytes": 50_000})
    data = samples.make_log(SENTINEL, lines=5000)
    last_line = data.decode().rstrip("\n").rsplit("\n", 1)[-1]
    containers, _, _ = await legacy("log", "application/octet-stream", data)
    text = _text(containers)
    assert text.startswith("2026-11-13T09:00:00Z INFO worker-0 handled request 0")
    assert last_line in text
    assert f"of {len(data):,}); the start and the end of the file are kept" in text


async def test_a_config_comment_is_text_not_a_heading() -> None:
    from app.models.blocks import BlockType

    containers, _, _ = await legacy("cfg", "application/octet-stream", _GOOD["cfg"])
    assert {b.type for b in containers[0].blocks} == {BlockType.TEXT}
    assert containers[0].blocks[0].data.startswith("# Service configuration")
    assert not containers[0].block_groups


# --------------------------------------------------------------------------- the record handler

_DAMAGED_OOXML = {
    "random bytes": b"\x13\x37 not an office file " * 10,
    "truncated": None,
    "duplicate members": None,
}


def _damaged(ext: str, kind: str) -> bytes:
    if kind == "random bytes":
        return _DAMAGED_OOXML[kind]  # type: ignore[return-value]
    if kind == "truncated":
        return _GOOD[ext][: len(_GOOD[ext]) // 2]
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(_GOOD[ext])) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            target.writestr("[Content_Types].xml", b"<Types/>")
    return out.getvalue()


@pytest.mark.parametrize("kind", sorted(_DAMAGED_OOXML))
@pytest.mark.parametrize("ext", ["docm", "dotx", "potx"])
async def test_a_damaged_package_fails_on_the_first_attempt_with_its_reason(ext: str, kind: str) -> None:
    record, downloaded = await handler_run(ext, None, _damaged(ext, kind))
    assert downloaded
    assert record["indexingStatus"] == "FAILED"
    assert record["reason"] == damaged_file_reason(ext)


async def test_a_damaged_mail_fails_on_the_first_attempt_from_a_real_parse_worker() -> None:
    record, _ = await handler_run("msg", None, samples.not_a_msg(), real_worker=True)
    assert record["indexingStatus"] == "FAILED"
    assert record["reason"] == damaged_file_reason("msg")


async def test_a_damaged_workbook_fails_on_the_first_attempt() -> None:
    record, _ = await handler_run("xlsm", None, b"\x13\x37 not a workbook " * 10)
    assert record["indexingStatus"] == "FAILED"
    assert record["reason"]


@pytest.mark.parametrize("ext", ["msg", "docm", "dotx", "potx", "xlsm", "log", "cfg"])
async def test_an_empty_file_is_empty(ext: str) -> None:
    # The handler's download treats zero bytes as a failed download; the empty check is the
    # dispatch's, which every pipeline runs first.
    containers, _, record = await legacy(ext, None, b"")
    assert containers == []
    assert record["indexingStatus"] == "EMPTY"


@pytest.mark.parametrize(
    ("ext", "content"),
    [
        ("docm", encrypted_docm()),
        ("xlsm", encrypted_xlsm()),
        # The encrypted container is the same whatever the package inside it.
        ("dotx", encrypted_docx()),
        ("potx", encrypted_pptx()),
    ],
)
async def test_a_password_protected_file_is_password_protected(ext: str, content: bytes) -> None:
    record, _ = await handler_run(ext, None, content)
    assert record["indexingStatus"] == "PASSWORD_PROTECTED"


@pytest.mark.parametrize("ext", ["log", "cfg"])
async def test_a_binary_file_named_like_text_is_not_supported(ext: str) -> None:
    record, _ = await handler_run(ext, "application/octet-stream", b"\x7fELF\x02\x01\x01\x00\x00\x00\x00binary\x00" * 50)
    assert record["indexingStatus"] == "FILE_TYPE_NOT_SUPPORTED"
    assert record["reason"] == binary_text_file(ext)


async def test_a_log_past_the_download_ceiling_is_not_downloaded() -> None:
    record, downloaded = await handler_run(
        "log", "application/octet-stream", b"x\n", size=file_types.TEXT_MAX_DOWNLOAD_BYTES + 1
    )
    assert not downloaded
    assert record["indexingStatus"] == "FILE_TYPE_NOT_SUPPORTED"
    assert record["reason"] == file_too_large_to_read("log")


async def test_a_log_under_the_ceiling_is_read() -> None:
    record, downloaded = await handler_run("log", None, _GOOD["log"], size=len(_GOOD["log"]))
    assert downloaded
    assert record["indexingStatus"] not in ("FILE_TYPE_NOT_SUPPORTED", "FAILED")


@pytest.mark.parametrize("ext", sorted([*_GOOD, "msg"]))
async def test_each_good_file_passes_the_handler_without_failing(ext: str) -> None:
    content = samples.msg_with_html_body() if ext == "msg" else _GOOD[ext]
    record, downloaded = await handler_run(ext, "application/octet-stream", content)
    assert downloaded
    assert record["indexingStatus"] not in ("FILE_TYPE_NOT_SUPPORTED", "FAILED", "PASSWORD_PROTECTED")


# --------------------------------------------------------------------------- D-16: what main indexed stays


@pytest.mark.parametrize("ext", ["log", "cfg"])
async def test_a_text_plain_log_keeps_main_s_whole_text_path(ext: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Main indexed a .log/.cfg stored as text/plain whole, as text: no cap, no new refusal."""
    from app.modules.parsers.text import capped_text

    monkeypatch.setattr(capped_text.prepare_capped_text, "__kwdefaults__", {"max_bytes": 1_000})
    data = samples.make_log(SENTINEL, lines=200)
    containers, _, _ = await legacy(ext, "text/plain", data)
    text = _text(containers)
    assert "bytes left out" not in text
    assert data.decode().rstrip("\n").rsplit("\n", 1)[-1] in text
    assert "worker-0 handled request 0" in text


async def test_a_text_plain_log_past_the_ceiling_is_still_downloaded() -> None:
    record, downloaded = await handler_run(
        "log", "text/plain", _GOOD["log"], size=file_types.TEXT_MAX_DOWNLOAD_BYTES + 1
    )
    assert downloaded
    assert record["indexingStatus"] != "FILE_TYPE_NOT_SUPPORTED"


async def test_a_text_plain_binary_log_is_read_as_main_read_it() -> None:
    record, downloaded = await handler_run("log", "text/plain", b"\x00\x01binary\x00" * 50)
    assert downloaded
    assert record["indexingStatus"] != "FILE_TYPE_NOT_SUPPORTED"
