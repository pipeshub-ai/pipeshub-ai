# ruff: noqa: ANN201, ANN202, ANN401
"""The stream route's validators: ETag, 304, and the headers on every answer.

The route runs its whole refusal chain first; only then is the stored object's version read, and only a
caller who could have had the bytes can be told "not modified". The storage service is faked at
``StorageDocuments``; the route, the dispatch and the localKB connector's ``served_version`` are real.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.responses import Response, StreamingResponse

import app.edition_config  # noqa: F401  (binds the edition seams before the router loads)
from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.api import router as router_mod
from app.connectors.sources.localKB import connector as kb_mod
from app.connectors.sources.localKB.connector import KnowledgeBaseConnector
from app.services.storage_documents import (
    BoundReadChanged,
    StorageUploadError,
    parse_document_info,
)
from app.utils.byte_range import MAX_RANGE_SPAN, ByteRange
from app.utils.content_tag import content_tag
from tests.support.record_access import InMemoryRecordAccess

RECORD_ID = "rec-1"
PIPELINE = "lo:test|conv:1"


def _info(fp: str = "fp-1", trusted: bool = True, size: int = 5):
    return parse_document_info(
        {
            "document": {"_id": "doc-1", "sizeInBytes": 5},
            "storageFingerprint": {
                "version": 1, "value": fp, "verified": trusted, "trusted": trusted,
                "object": {"size": size, "etag": "abc"},
            },
        },
        verify=True,
    )


TAG = content_tag(RECORD_ID, _info(), "original")


def _record(**over: Any) -> MagicMock:
    record = MagicMock()
    record.id = RECORD_ID
    record.org_id = "org-1"
    record.connector_id = "conn-1"
    record.connector_name = Connectors.KNOWLEDGE_BASE
    record.origin = OriginTypes.UPLOAD
    record.external_record_id = "doc-1"
    record.record_name = "a.pdf"
    record.is_placeholder = False
    record.record_type = "FILE"
    for k, v in over.items():
        setattr(record, k, v)
    return record


class FakeKB:
    """The localKB connector: its real ``served_version``, and a ``stream_record`` with its refusals."""

    served_version = KnowledgeBaseConnector.served_version

    def __init__(self) -> None:
        self.config_service = MagicMock()
        self.logger = MagicMock()
        self.read = AsyncMock(return_value=Response(b"bytes", media_type="application/pdf"))

    def get_app_name(self):
        return Connectors.KNOWLEDGE_BASE

    display_name = "Collections"

    async def stream_record(self, record, *a):
        if record.origin != OriginTypes.UPLOAD:
            raise HTTPException(400, "Cannot stream record with origin")
        if not record.external_record_id:
            raise HTTPException(404, "Record has no externalRecordId")
        return await self.read(record)


class World:
    def __init__(self, record: MagicMock | None = None, *, infos=None, app_doc=None, in_memory=True) -> None:
        self.record = record or _record()
        self.kb = FakeKB()
        self.infos = list(infos) if infos is not None else [_info(), _info()]
        self.info_calls = 0
        self.app_doc = {"_key": "conn-1", "type": "KB", "isActive": True, "name": "Collections"} if app_doc is None else (app_doc or None)
        self.access: Any = {"id": RECORD_ID}
        self.in_memory = in_memory

        async def info(_self, org, doc, verify=False):
            self.info_calls += 1
            item = self.infos[min(self.info_calls - 1, len(self.infos) - 1)]
            if isinstance(item, BaseException):
                raise item
            if callable(item):
                return await item()
            return item

        self.info = info

    def graph(self):
        async def get_document(key, collection, *_a, **_k):
            if collection == CollectionNames.ORGS.value:
                return {"_key": key}
            return self.app_doc

        graph = MagicMock()
        graph.get_record_by_id = AsyncMock(return_value=self.record)
        graph.get_document = AsyncMock(side_effect=get_document)
        access = InMemoryRecordAccess().user("u1", "org-1").record(RECORD_ID, "org-1")
        if self.access is not None:
            access.grant(RECORD_ID, user="u1", role="OWNER")
        return access.serve(graph)

    def request(self, inm: str | None = None, org: str = "org-1", asks: bool = True, extra: dict[str, str] | None = None) -> SimpleNamespace:
        headers = {"if-none-match": inm} if inm is not None else {}
        headers.update(extra or {})
        if asks:
            headers["x-record-content-open"] = "1"  # what the web client sends while its preview cache is on
        connectors = {"conn-1": self.kb} if self.in_memory else {}
        container = SimpleNamespace(logger=lambda: MagicMock(), connectors_map=connectors, config_service=lambda: MagicMock())
        app_ = SimpleNamespace(container=container, state=SimpleNamespace(connector_registry=MagicMock()))
        return SimpleNamespace(state=SimpleNamespace(user={"userId": "u1", "orgId": org, "role": "member"}), app=app_, headers=headers)

    async def stream(self, inm: str | None = None, convert_to: str | None = None, org: str = "org-1", asks: bool = True, extra: dict[str, str] | None = None) -> Any:
        with patch("app.connectors.sources.localKB.connector.StorageDocuments") as sd, \
                patch.object(router_mod, "preview_pdf_pipeline", return_value=PIPELINE), \
                patch.object(router_mod, "refuse_hidden_demo_record", new=AsyncMock()), \
                patch.object(router_mod, "refuse_outside_connection", new=AsyncMock()):
            sd.return_value.info = lambda org_id, doc, verify=False: self.info(None, org_id, doc, verify=verify)
            return await router_mod.stream_record(
                self.request(inm, org, asks, extra), RECORD_ID, convertTo=convert_to, version=None,
                graph_provider=self.graph(), config_service=MagicMock(),
            )


def _headers(obj: object) -> dict[str, str]:
    return {k.lower(): v for k, v in dict(obj.headers or {}).items()}


async def _refusal(coro) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        await coro
    return exc.value


# --- the 304 and the 200 -----------------------------------------------------------------------------------

async def test_not_modified_reads_no_bytes() -> None:
    world = World()
    out = await world.stream(inm=TAG)
    assert out.status_code == 304
    assert out.body == b""
    assert _headers(out)["etag"] == TAG
    assert _headers(out)["cache-control"] == "private, no-store"
    assert world.info_calls == 1
    world.kb.read.assert_not_awaited()


async def test_a_stale_validator_gets_the_bytes_and_the_new_tag() -> None:
    world = World()
    out = await world.stream(inm='"someoldtag"')
    assert out.status_code == 200
    assert out.body == b"bytes"
    assert _headers(out)["etag"] == TAG
    assert _headers(out)["cache-control"] == "private, no-store"
    assert _headers(out)["accept-ranges"] == "none"
    assert world.info_calls == 2


async def test_etag_only_when_bracketed() -> None:
    # A write between the first read and the byte read: the second answer differs, so no ETag.
    world = World(infos=[_info("fp-1"), _info("fp-2")])
    out = await world.stream()
    assert out.status_code == 200 and "etag" not in _headers(out)
    assert _headers(out)["cache-control"] == "private, no-store"
    # Equal and trusted: a tag.
    ok = World(infos=[_info("fp-1"), _info("fp-1")])
    assert _headers(await ok.stream())["etag"] == TAG
    # An untrusted first answer: no tag, and the second read is not needed.
    untrusted = World(infos=[_info(trusted=False), _info()])
    out = await untrusted.stream(inm=TAG)
    assert out.status_code == 200 and "etag" not in _headers(out)
    # An untrusted second answer.
    late = World(infos=[_info(), _info(trusted=False)])
    assert "etag" not in _headers(await late.stream())


async def test_the_pdf_variant_has_its_own_weak_tag() -> None:
    record = _record(record_name="deck.pptx", mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
    world = World(record)
    converted = Response(b"%PDF", media_type="application/pdf")
    with patch.object(router_mod, "convert_buffer_to_pdf_stream", new=AsyncMock(return_value=converted)):
        out = await world.stream(convert_to="application/pdf")
    weak = content_tag(RECORD_ID, _info(), "pdf", pipeline=PIPELINE)
    assert weak.startswith("W/") and _headers(out)["etag"] == weak
    again = World(record)
    out = await again.stream(inm=weak, convert_to="application/pdf")
    assert out.status_code == 304


async def test_a_connector_without_served_version_never_answers_304() -> None:
    world = World(_record(connector_name=Connectors.GOOGLE_DRIVE, origin=OriginTypes.CONNECTOR))
    world.kb.served_version = None
    world.kb.stream_record = AsyncMock(return_value=Response(b"bytes"))
    out = await world.stream(inm="*")
    assert out.status_code == 200 and "etag" not in _headers(out)
    assert _headers(out)["cache-control"] == "private, no-store"


# --- a request that asks for nothing is served as it always was ---------------------------------------------

async def test_a_plain_request_costs_what_it_always_did() -> None:
    plain = World()
    out = await plain.stream(asks=False)
    assert out.status_code == 200 and out.body == b"bytes"
    assert plain.info_calls == 0  # no storage version read at all
    assert not {"etag", "cache-control", "accept-ranges"} & set(_headers(out))
    plain.kb.read.assert_awaited_once()
    # The same request asking for a validator pays for it, once before and once after the bytes.
    asking = World()
    out = await asking.stream(asks=True)
    assert asking.info_calls == 2 and _headers(out)["etag"] == TAG


async def test_a_plain_request_for_the_pdf_variant_reads_no_version() -> None:
    record = _record(record_name="deck.pptx", mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
    world = World(record)
    converted = Response(b"%PDF", media_type="application/pdf")
    with patch.object(router_mod, "convert_buffer_to_pdf_stream", new=AsyncMock(return_value=converted)):
        out = await world.stream(convert_to="application/pdf", asks=False)
    assert world.info_calls == 0 and "etag" not in _headers(out)


def test_only_the_two_headers_ask_for_validators() -> None:
    assert router_mod.wants_validators({"if-none-match": '"x"'})
    assert router_mod.wants_validators({"x-record-content-open": "1"})
    assert not router_mod.wants_validators({"x-record-content-open": "0"})
    assert not router_mod.wants_validators({})


# --- ranges and the streamed 200 ---------------------------------------------------------------------

def _bound_kb(world: World, *, total: int = 5, changes: int = 0) -> list[tuple[Any, ByteRange | None]]:
    """Give the fake connector the bound read; it raises BoundReadChanged ``changes`` times first. Returns its call log."""
    calls: list[tuple[Any, ByteRange | None]] = []
    left = {"n": changes}

    async def bound(record, info, byte_range=None):
        calls.append((info, byte_range))
        if left["n"] > 0:
            left["n"] -= 1
            raise BoundReadChanged
        if byte_range is None:
            return StreamingResponse(iter([b"whole"]), status_code=200, headers={"Content-Length": str(total)})
        return StreamingResponse(
            iter([b"part"]), status_code=206,
            headers={"Content-Range": f"bytes {byte_range.start}-{byte_range.end}/{total}", "Content-Length": str(byte_range.length)},
        )

    world.kb.stream_record_bound = bound
    return calls


async def _body(out: Any) -> bytes:
    return b"".join([chunk async for chunk in out.body_iterator])


async def test_one_closed_range_is_a_206_bound_to_the_verified_version() -> None:
    world = World(infos=[_info(size=1000)])
    calls = _bound_kb(world, total=1000)
    out = await world.stream(extra={"range": "bytes=10-109"})
    assert out.status_code == 206 and await _body(out) == b"part"
    h = _headers(out)
    assert h["etag"] == TAG and h["content-range"] == "bytes 10-109/1000" and h["accept-ranges"] == "bytes"
    assert h["cache-control"] == "private, no-store"
    assert calls == [(calls[0][0], ByteRange(10, 109))]
    assert world.info_calls == 1  # the read is bound to the version already verified: no second fingerprint read
    world.kb.read.assert_not_awaited()


async def test_a_whole_body_is_streamed_bound_with_one_fingerprint_read() -> None:
    world = World(infos=[_info(size=1000)])
    calls = _bound_kb(world, total=1000)
    out = await world.stream()
    assert out.status_code == 200 and await _body(out) == b"whole"
    assert _headers(out)["etag"] == TAG and _headers(out)["accept-ranges"] == "bytes"
    assert [c[1] for c in calls] == [None] and world.info_calls == 1
    world.kb.read.assert_not_awaited()


@pytest.mark.parametrize(
    "header",
    ["bytes=-5", "bytes=0-1,5-9", "bytes=abc", "bytes=9-3", "items=0-5", f"bytes=0-{MAX_RANGE_SPAN}", "bytes= 0-5", "bytes=0-5 "],
    ids=["suffix", "multi", "garbage", "reversed", "other_unit", "over_8mib", "space_inside", "trailing_space"],
)
async def test_a_range_that_is_not_one_strict_small_range_gets_the_whole_body(header: str) -> None:
    world = World(infos=[_info(size=20 * MAX_RANGE_SPAN)])
    calls = _bound_kb(world, total=20 * MAX_RANGE_SPAN)
    out = await world.stream(extra={"range": header})
    expected = 206 if header == "bytes=0-5 " else 200  # surrounding white space is trimmed, as RFC 9110 allows
    assert out.status_code == expected
    assert (calls[0][1] is None) == (expected == 200)


async def test_a_range_of_exactly_8_mib_and_an_open_ended_one_are_served() -> None:
    world = World(infos=[_info(size=20 * MAX_RANGE_SPAN)])
    calls = _bound_kb(world, total=20 * MAX_RANGE_SPAN)
    out = await world.stream(extra={"range": f"bytes=0-{MAX_RANGE_SPAN - 1}"})
    assert out.status_code == 206 and calls[0][1] == ByteRange(0, MAX_RANGE_SPAN - 1)
    near_end = World(infos=[_info(size=1000)])
    calls = _bound_kb(near_end, total=1000)
    out = await near_end.stream(extra={"range": "bytes=900-"})
    assert out.status_code == 206 and calls[0][1] == ByteRange(900, 999)
    clamped = World(infos=[_info(size=1000)])
    calls = _bound_kb(clamped, total=1000)
    await clamped.stream(extra={"range": "bytes=900-5000"})
    assert calls[0][1] == ByteRange(900, 999)


async def test_a_range_past_the_end_is_416_with_the_length_and_no_tag() -> None:
    world = World(infos=[_info(size=1000)])
    calls = _bound_kb(world, total=1000)
    exc = await _refusal(world.stream(extra={"range": "bytes=1000-1100"}))
    assert exc.status_code == 416
    h = _headers(exc)
    assert h["content-range"] == "bytes */1000" and h["cache-control"] == "no-store" and "etag" not in h
    assert calls == []


async def test_if_range_must_be_the_current_strong_tag() -> None:
    for if_range, expect in [(TAG, 206), ('"someoldtag"', 200), ("W/" + TAG, 200), (None, 206)]:
        world = World(infos=[_info(size=1000)])
        calls = _bound_kb(world, total=1000)
        extra = {"range": "bytes=0-9", **({"if-range": if_range} if if_range else {})}
        out = await world.stream(extra=extra)
        assert out.status_code == expect, if_range
        assert (calls[0][1] is None) == (expect == 200), if_range


async def test_a_change_between_the_check_and_the_read_sends_the_new_bytes_whole_under_their_own_tag() -> None:
    world = World(infos=[_info("fp-1", size=1000), _info("fp-2", size=1000)])
    calls = _bound_kb(world, total=1000, changes=1)
    out = await world.stream(extra={"range": "bytes=0-9"})
    assert out.status_code == 200  # the range was against the old bytes
    assert _headers(out)["etag"] == content_tag(RECORD_ID, _info("fp-2", size=1000), "original")
    assert [c[1] for c in calls] == [ByteRange(0, 9), None]


async def test_a_second_change_serves_the_bytes_untagged_the_old_way() -> None:
    world = World(infos=[_info("fp-1", size=1000), _info("fp-2", size=1000)])
    _bound_kb(world, total=1000, changes=2)
    out = await world.stream(extra={"range": "bytes=0-9"})
    assert out.status_code == 200 and out.body == b"bytes"
    assert "etag" not in _headers(out) and _headers(out)["accept-ranges"] == "none"


async def test_ranges_are_ignored_for_the_pdf_variant_and_for_a_plain_request() -> None:
    record = _record(record_name="deck.pptx", mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
    world = World(record, infos=[_info(size=1000), _info(size=1000)])
    calls = _bound_kb(world, total=1000)
    converted = Response(b"%PDF", media_type="application/pdf")
    with patch.object(router_mod, "convert_buffer_to_pdf_stream", new=AsyncMock(return_value=converted)):
        out = await world.stream(convert_to="application/pdf", extra={"range": "bytes=0-9"})
    assert out.status_code == 200 and calls == []
    plain = World(infos=[_info(size=1000)])
    calls = _bound_kb(plain, total=1000)
    out = await plain.stream(asks=False, extra={"range": "bytes=0-9"})
    assert out.status_code == 200 and calls == [] and plain.info_calls == 0


async def test_a_connector_without_bound_reads_ignores_the_range() -> None:
    world = World(infos=[_info(size=1000), _info(size=1000)])
    out = await world.stream(extra={"range": "bytes=0-9"})
    assert out.status_code == 200 and out.body == b"bytes"
    assert _headers(out)["accept-ranges"] == "none"


async def test_a_304_of_a_range_capable_object_says_so() -> None:
    world = World(infos=[_info(size=1000)])
    _bound_kb(world, total=1000)
    out = await world.stream(inm=TAG)
    assert out.status_code == 304 and _headers(out)["accept-ranges"] == "bytes"


# --- refusals -----------------------------------------------------------------------------------------------

async def test_no_access_is_403_even_with_matching_etag() -> None:
    world = World()
    world.access = None
    exc = await _refusal(world.stream(inm=TAG))
    assert exc.status_code == 403
    assert "etag" not in _headers(exc)
    assert world.info_calls == 0
    world.kb.read.assert_not_awaited()


async def test_cross_org_is_404_without_etag() -> None:
    world = World(_record(org_id="org-2"))
    exc = await _refusal(world.stream(inm=TAG))
    assert exc.status_code == 404
    assert "etag" not in _headers(exc)
    assert world.info_calls == 0


def _refuse(name: str) -> Any:
    def hidden(w: World) -> Any:
        return patch.object(router_mod, "refuse_hidden_demo_record", new=AsyncMock(side_effect=HTTPException(404, "Record not found")))

    def outside(w: World) -> Any:
        return patch.object(router_mod, "refuse_outside_connection", new=AsyncMock(side_effect=HTTPException(403, "outside")))

    return {"hidden": hidden, "outside": outside}[name]


REFUSALS = [
    ("org_mismatch", lambda: World(_record(org_id="org-2")), 404),
    ("no_access", lambda: _with(World(), access=None), 403),
    ("placeholder", lambda: World(_record(is_placeholder=True)), 422),
    ("connector_deleted", lambda: World(app_doc=False), 404),
    ("connector_disabled_not_in_memory", lambda: World(app_doc={"_key": "conn-1", "type": "KB", "isActive": False, "name": "Collections"}, in_memory=False), 409),
    ("origin_not_upload", lambda: World(_record(origin=OriginTypes.CONNECTOR)), 400),
    ("no_external_record_id", lambda: World(_record(external_record_id=None)), 404),
]


def _with(world: World, **over: object) -> World:
    for k, v in over.items():
        setattr(world, k, v)
    return world


@pytest.mark.parametrize("name,make,status", REFUSALS, ids=[r[0] for r in REFUSALS])
async def test_each_refusal_refuses_a_valid_inm(name, make, status) -> None:
    exc = await _refusal(make().stream(inm=TAG))
    plain = await _refusal(make().stream(inm=None))
    assert exc.status_code == status == plain.status_code
    assert exc.detail == plain.detail
    assert "etag" not in _headers(exc)
    assert _headers(exc)["cache-control"] == "no-store"


@pytest.mark.parametrize("which", ["hidden", "outside"])
async def test_the_connection_refusals_refuse_a_valid_inm(which: str) -> None:
    world = World()
    with patch.object(router_mod, "preview_pdf_pipeline", return_value=PIPELINE), \
            patch("app.connectors.sources.localKB.connector.StorageDocuments") as sd, \
            patch.object(router_mod, "refuse_hidden_demo_record", new=AsyncMock(side_effect=HTTPException(404, "x") if which == "hidden" else None)), \
            patch.object(router_mod, "refuse_outside_connection", new=AsyncMock(side_effect=HTTPException(403, "x") if which == "outside" else None)):
        sd.return_value.info = AsyncMock(return_value=_info())
        exc = await _refusal(router_mod.stream_record(
            world.request(TAG), RECORD_ID, convertTo=None, version=None, graph_provider=world.graph(), config_service=MagicMock(),
        ))
    assert "etag" not in _headers(exc)
    assert exc.status_code in (403, 404)
    assert world.info_calls == 0


async def test_the_epub_pdf_refusal_refuses_a_valid_inm() -> None:
    record = _record(record_name="book.epub")
    world = World(record)
    with patch.object(router_mod, "_refuse_epub_pdf_preview", side_effect=HTTPException(422, "epub")):
        exc = await _refusal(world.stream(inm=TAG, convert_to="application/pdf"))
    assert exc.status_code == 422 and "etag" not in _headers(exc)
    assert world.info_calls == 0


async def test_an_in_memory_disabled_connector_answers_as_it_always_did() -> None:
    disabled = {"_key": "conn-1", "type": "KB", "isActive": False, "name": "Collections"}
    out = await World(app_doc=disabled, in_memory=True).stream()
    assert out.status_code == 200
    out = await World(app_doc=disabled, in_memory=True).stream(inm=TAG)
    assert out.status_code == 304


async def test_errors_carry_no_store_and_no_etag() -> None:
    world = World()
    world.kb.read = AsyncMock(side_effect=HTTPException(502, "bad gateway"))
    exc = await _refusal(world.stream())
    assert exc.status_code == 502
    assert _headers(exc)["cache-control"] == "no-store"
    assert "etag" not in _headers(exc)


# --- the validator read is bounded and total ----------------------------------------------------------------

@pytest.mark.parametrize(
    "failure",
    [StorageUploadError("down"), TimeoutError(), OSError("reset"), RuntimeError("boom")],
    ids=["upload_error", "timeout", "transport", "other"],
)
async def test_served_version_is_bounded_and_total(failure) -> None:
    world = World(infos=[failure, failure])
    out = await world.stream(inm=TAG)
    # No tag and no 304: the bytes answer as they always did, without an ETag.
    assert out.status_code == 200 and out.body == b"bytes"
    assert "etag" not in _headers(out)


async def test_a_slow_storage_answer_times_out_without_a_tag(monkeypatch) -> None:
    async def slow() -> None:
        await asyncio.sleep(5)

    monkeypatch.setattr(kb_mod, "SERVED_VERSION_TIMEOUT_SECONDS", 0.05)
    world = World(infos=[slow, slow])
    out = await world.stream(inm=TAG)
    assert out.status_code == 200 and "etag" not in _headers(out)


async def test_a_timeout_of_the_second_read_drops_the_etag(monkeypatch) -> None:
    async def slow() -> None:
        await asyncio.sleep(5)

    monkeypatch.setattr(kb_mod, "SERVED_VERSION_TIMEOUT_SECONDS", 0.05)
    world = World(infos=[_info(), slow])
    out = await world.stream()
    assert out.status_code == 200 and "etag" not in _headers(out)
