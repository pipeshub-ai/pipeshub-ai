"""``storage://`` image references: caps, sentinels, the splice token and the size arithmetic."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from app.connectors.sources.localKB.push import images as I
from app.connectors.sources.localKB.push import worker as W
from app.connectors.sources.localKB.push.contract import PushRefusal, parse_envelope
from app.connectors.sources.localKB.push.spool import Spool

from .bodies import envelope, raw

if TYPE_CHECKING:
    from collections.abc import Iterator

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def ref(n: int) -> str:
    return f"storage://{n:024x}"


def img(uri: str, index: int = 0) -> dict:
    return {"index": index, "type": "image", "format": "base64", "data": {"uri": uri}}


def body_of(uris: list[str]) -> bytes:
    return raw(envelope({"blocks": [img(u, i) for i, u in enumerate(uris)]}))


def check(uris: list[str]) -> I.ImageRefs:
    return I.check_blocks_images(parse_envelope(body_of(uris)).blocks)


def refused(uris: list[str]) -> PushRefusal:
    with pytest.raises(PushRefusal) as e:
        check(uris)
    return e.value


class TestCaps:
    def test_64_distinct_ok_65th_refused(self) -> None:
        assert len(check([ref(i) for i in range(64)]).order) == 64
        e = refused([ref(i) for i in range(65)])
        assert (e.status, e.reason) == (422, "LIMIT_EXCEEDED") and e.location == "blocks.blocks[64].data.uri"

    def test_16_occurrences_ok_17th_refused(self) -> None:
        assert check([ref(1)] * 16).counts == {ref(1)[10:]: 16}
        assert refused([ref(1)] * 17).reason == "LIMIT_EXCEEDED"

    def test_256_total_ok_257th_refused(self) -> None:
        # 16 refs x 16 occurrences = 256
        ok = [ref(i) for i in range(16) for _ in range(16)]
        assert check(ok).occurrences == 256
        assert refused([*ok, ref(99)]).reason == "LIMIT_EXCEEDED"

    def test_1000_images_ok_1001st_refused_counting_data_and_storage(self) -> None:
        data = f"data:image/png;base64,{PNG}"
        ok = [data] * 990 + [ref(i) for i in range(10)]
        assert check(ok).images == 1000
        e = refused([*ok, data])
        assert e.reason == "LIMIT_EXCEEDED" and e.location == "blocks.blocks[1000].data.uri"

    def test_data_uris_alone_count_toward_the_image_cap(self) -> None:
        data = f"data:image/png;base64,{PNG}"
        assert refused([data] * 1001).reason == "LIMIT_EXCEEDED"


class TestSentinel:
    @pytest.fixture
    def spool(self, tmp_path) -> Iterator[object]:
        s = Spool(str(tmp_path))
        yield s
        s.close()

    def run(self, body: bytes, spool: Spool) -> W.Validated:
        return W.validate_body(body, schema_id=None, schema=None, out_path=spool.new_path("out"))

    def test_refs_become_sentinels_and_the_size_waits_for_the_materialiser(self, spool) -> None:
        v = self.run(body_of([ref(1), ref(2), ref(1)]), spool)
        stored = open(v.stored_path, "rb").read()
        assert v.refs == [(ref(1)[10:], 2), (ref(2)[10:], 1)] and v.needs_materialising
        assert (v.size, v.content_rev) == (None, None)
        assert stored.count(I.sentinel_token(v.image_nonce, 0)) == 2 and stored.count(I.sentinel_token(v.image_nonce, 1)) == 1
        assert b"storage://" not in stored

    def test_the_digest_sees_the_references_as_sent(self, spool) -> None:
        a = self.run(body_of([ref(1)]), spool)
        b = self.run(body_of([ref(2)]), spool)
        assert a.blocks_sha != b.blocks_sha and a.image_nonce != b.image_nonce

    def test_body_text_equal_to_the_sentinel_is_not_rewritten(self, spool) -> None:
        trap = I.sentinel("0" * 32, 0)
        body = raw(envelope({"blocks": [{"index": 0, "type": "text", "format": "txt", "data": trap}, img(ref(1), 1)]}))
        v = self.run(body, spool)
        stored = open(v.stored_path, "rb").read()
        assert stored.count(I.sentinel_token(v.image_nonce, 0)) == 1  # only the image block carries the live token
        assert I.sentinel_token("0" * 32, 0) in stored  # kept as text; the splice only touches the live nonce (test_materialiser)

    def test_json_escaped_u0000_prefix_is_not_rewritten(self, spool) -> None:
        body = (b'{"recordName":"r","mimeType":"application/blocks","externalRecordId":"k","version":1,"blocksFormat":1,"blocks":{"blocks":['
                b'{"index":0,"type":"text","format":"txt","data":"\\u0000pb-img:deadbeef:0"},'
                b'{"index":1,"type":"image","format":"base64","data":{"uri":"' + ref(1).encode() + b'"}}]}}')
        v = self.run(body, spool)
        stored = open(v.stored_path, "rb").read()
        assert stored.count(b"\\u0000pb-img:") == 2
        assert stored.count(I.sentinel_token(v.image_nonce, 0)) == 1

    def test_text_block_exactly_storage_uri_is_not_rewritten(self, spool) -> None:
        body = raw(envelope({"blocks": [{"index": 0, "type": "text", "format": "txt", "data": ref(1)}, img(ref(1), 1)]}))
        v = self.run(body, spool)
        assert v.refs == [(ref(1)[10:], 1)]
        assert ref(1).encode() in open(v.stored_path, "rb").read()  # the text stays as sent

    def test_nonce_is_fresh_per_push_and_drawn_after_the_parse(self, spool, monkeypatch) -> None:
        a = self.run(body_of([ref(1)]), spool)
        b = self.run(body_of([ref(1)]), spool)
        assert a.image_nonce != b.image_nonce and len(a.image_nonce) == 32
        order: list[str] = []
        real_parse, real_nonce = parse_envelope, I.new_nonce
        import app.connectors.sources.localKB.push.contract as C
        monkeypatch.setattr(C, "parse_envelope", lambda body: (order.append("parse"), real_parse(body))[1])
        monkeypatch.setattr(I, "new_nonce", lambda: (order.append("nonce"), real_nonce())[1])
        self.run(body_of([ref(1)]), spool)
        assert order == ["parse", "nonce"]

    def test_no_refs_means_no_nonce_and_a_known_size(self, spool) -> None:
        v = self.run(raw(envelope()), spool)
        assert v.refs == [] and v.image_nonce is None and v.size and v.content_rev and not v.needs_materialising


class TestArithmetic:
    def test_lower_bound_is_never_above_the_true_inlined_size(self) -> None:
        refs = I.ImageRefs(order=["a" * 24], counts={"a" * 24: 3})
        nonce = "0" * 32
        token = I.sentinel_token(nonce, 0)
        stored = len(b'{"blocks":[' + b",".join([token] * 3) + b"]}")
        raw_len = 1000
        bound = I.inlined_size_lower_bound(stored, nonce, refs, {"a" * 24: raw_len})
        exact = len(json.dumps({"blocks": ["data:image/jpeg;base64," + "A" * (4 * -(-raw_len // 3))] * 3}, separators=(",", ":")))
        assert bound <= exact
        # png is the shortest subtype, so the bound is exact for png
        png = len(json.dumps({"blocks": ["data:image/png;base64," + "A" * (4 * -(-raw_len // 3))] * 3}, separators=(",", ":")))
        assert bound == png

    def test_uses_ceil_not_floor_for_base64(self) -> None:
        refs = I.ImageRefs(order=["a" * 24], counts={"a" * 24: 1})
        one = I.inlined_size_lower_bound(0, "0" * 32, refs, {"a" * 24: 1})
        two = I.inlined_size_lower_bound(0, "0" * 32, refs, {"a" * 24: 3})
        assert one == two  # 1 byte and 3 bytes both encode to 4 characters
