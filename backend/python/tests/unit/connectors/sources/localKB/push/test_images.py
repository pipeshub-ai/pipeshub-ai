"""``storage://`` image references: caps, sentinels, the splice token and the size arithmetic."""

from __future__ import annotations

import base64
import contextlib
import gc
import json
import random
import time
import tracemalloc
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


# ---- images inside markdown and HTML groups ------------------------------------------------------

DATA_PNG = f"data:image/png;base64,{PNG}"
GIF = "R0lGODlhAQABAAAAACw="  # GIF89a magic, a 1x1 frame header is enough for the check


def group(data: object, fmt: str | None = "markdown", index: int = 0, kind: str = "text_section", **over: object) -> dict:
    return {"index": index, "type": kind, "requires_processing": True, "format": fmt, "data": data, **over}


def check_groups(*groups: dict, blocks: list[dict] | None = None) -> I.ImageRefs:
    body = envelope({"block_groups": list(groups), **({"blocks": blocks} if blocks else {})})
    return I.check_blocks_images(parse_envelope(raw(body)).blocks)


def group_refusal(*groups: dict, **kw: object) -> PushRefusal:
    with pytest.raises(PushRefusal) as e:
        check_groups(*groups, **kw)
    return e.value


# Remote references written the ways a pattern-based scan has missed.
MARKDOWN_REMOTE = [
    "![a](http://spy.test/a.png)",
    "![a]( http://spy.test/b.png )",
    "![a](http://spy.test/c.png 'T')",
    '![a](http://spy.test/d.png "T")',
    "![a](http://spy.test/e.png (T))",
    "![a](<http://spy.test/f.png>)",
    "![a](\n  http://spy.test/g.png\n)",
    "![a](//spy.test/h.png)",
    "![a](https://203.0.113.9/i.png)",
    "![a](http://3405803785/j.png)",
    "![a](http://0xcb007109/k.png)",
    "![a][r]\n\n   [r]: http://spy.test/l.png",
    "![a][r]\n\n[r]: <http://spy.test/m.png> 'T'",
    "![a][undefined]",
    '<img/src="http://spy.test/n.png">',
    '<IMG SRC="http://spy.test/o.png">',
    '<img\nsrc="http://spy.test/p.png">',
    '<p>![a](http://spy.test/q.png)</p>',
    "![a](&#104;ttp://spy.test/r.png)",
    "![a](storage://" + "a" * 24 + ")",
    "![a](ftp://spy.test/s.png)",
    "![a](relative/t.png)",
]
HTML_REMOTE = [
    '<img src="http://spy.test/a.png">',
    "<IMG SRC=http://spy.test/b.png>",
    '<img\nsrc="http://spy.test/c.png">',
    '<img/src="http://spy.test/d.png">',
    '<img srcset="http://spy.test/e.png 1x, http://spy.test/e2.png 2x">',
    '<img src="" srcset="http://spy.test/f.png">',
    '<picture><source srcset="http://spy.test/g.png"><img src="http://spy.test/h.png"></picture>',
    '<img src="&#104;ttp://spy.test/i.png">',
    '<img src=" http://spy.test/j.png">',
    # Forms an HTML tokenizer reads as a tag and the CommonMark tag grammar does not.
    '<img src="http://spy.test/k.png"junk>',
    '<img src="http://spy.test/l.png" / >',
    '<img/ src="http://spy.test/m.png">',
    '<img src="http://spy.test/n.png" =x>',
    '<img src="http://spy.test/o.png"\x00>',
    "<img src=a\"b.png>",
    '<img src="http://spy.test/p.png"alt="x">',
    '<img src="http://spy.test/q.png"/ alt="x">',
    '<img src = "http://spy.test/r.png" / >',
    '<img src="storage://' + "b" * 24 + '">',
    '<img alt="no source">',
    '<img src="//spy.test/s.png">',
]


class TestDataUriCase:
    """Indexing reads an inline image only when its URI starts with a lower-case ``data:``, so an
    upper-case one that passed the push would vanish from the record without a word."""

    @pytest.mark.parametrize(
        "uri",
        [f"DATA:image/png;base64,{PNG}", f"Data:image/png;base64,{PNG}", f"data:IMAGE/PNG;BASE64,{PNG}"],
    )
    def test_a_data_uri_not_in_lower_case_is_refused_with_a_clear_reason(self, uri: str) -> None:
        with pytest.raises(PushRefusal) as raised:
            I.check_data_uri(uri, "blocks.blocks[0].data.uri")
        assert raised.value.reason == "IMAGE_REF_INVALID"
        assert "lower case" in raised.value.message

    @pytest.mark.parametrize("fmt, data", [
        ("markdown", f"![a](DATA:IMAGE/PNG;BASE64,{PNG})"),
        ("html", f'<img src="DATA:image/png;base64,{PNG}">'),
    ])
    def test_a_group_image_not_in_lower_case_is_refused(self, fmt: str, data: str) -> None:
        with pytest.raises(PushRefusal) as raised:
            check_groups(group(data, fmt))
        assert raised.value.reason == "IMAGE_REF_INVALID"


class TestGroupImageSourceSpacing:
    """The markdown parser hands indexing an angle-bracket destination with its spaces kept, and an
    inline image is read only when its URI starts with ``data:``, so a padded one would be dropped."""

    @pytest.mark.parametrize("data", [
        f"![a](< data:image/png;base64,{PNG}>)",
        f"![a](<data:image/png;base64,{PNG} >)",
    ])
    def test_a_padded_image_source_is_refused_with_a_clear_reason(self, data: str) -> None:
        with pytest.raises(PushRefusal) as raised:
            check_groups(group(data))
        assert raised.value.reason == "IMAGE_REF_INVALID"
        assert "space" in raised.value.message


    @pytest.mark.parametrize("data", [
        "![a](< http://example.com/a.png>)",
        "![a](<//example.com/a.png >)",
        "![a](< storage://000000000000000000000001>)",
    ])
    def test_a_padded_source_that_is_not_inline_keeps_its_refusal(self, data: str) -> None:
        assert group_refusal(group(data)).reason == "IMAGE_REF_REFUSED"


class TestGroupImages:
    @pytest.mark.parametrize(
        "data",
        [
            f"intro\n\n![a]({DATA_PNG})\n\nend",
            f"![a]({DATA_PNG} \"title\")",
            f"![a][r]\n\n  [r]: {DATA_PNG}",
            f'<img src="{DATA_PNG}" alt="a">',
            f"![a](data:image/gif;base64,{GIF})",
            "no image here",
        ],
    )
    def test_markdown_group_with_inline_images_is_accepted(self, data: str) -> None:
        refs = check_groups(group(data))
        assert refs.order == [] and refs.images == data.count("![") + data.count("<img")

    @pytest.mark.parametrize(
        "data",
        [
            f'<p>x</p><img src="{DATA_PNG}">',
            f'<IMG SRC="{DATA_PNG}">',
            f'<img\nsrc="{DATA_PNG}"/>',
            "<p>no image</p>",
        ],
    )
    def test_html_group_with_inline_images_is_accepted(self, data: str) -> None:
        assert check_groups(group(data, "html")).images == data.lower().count("<img")

    @pytest.mark.parametrize("fmt", [None, "txt", "markdown"])
    def test_every_format_but_html_is_read_as_markdown(self, fmt: str | None) -> None:
        """Indexing reads a group as HTML only when its format says so."""
        e = group_refusal(group("![a](http://spy.test/a.png)", fmt))
        assert (e.status, e.reason, e.location) == (422, "IMAGE_REF_REFUSED", "blocks.block_groups[0].data")

    @pytest.mark.parametrize("data", MARKDOWN_REMOTE)
    def test_remote_forms_are_refused_in_a_markdown_group(self, data: str) -> None:
        e = group_refusal(group("clean", index=0), group(f"intro\n\n{data}\n\nend", index=1))
        assert (e.status, e.reason, e.location) == (422, "IMAGE_REF_REFUSED", "blocks.block_groups[1].data")

    @pytest.mark.parametrize("data", HTML_REMOTE)
    def test_remote_forms_are_refused_in_an_html_group(self, data: str) -> None:
        e = group_refusal(group("clean", index=0), group(f"<p>x</p>{data}", "html", index=1))
        assert (e.status, e.reason, e.location) == (422, "IMAGE_REF_REFUSED", "blocks.block_groups[1].data")

    @pytest.mark.parametrize("fmt", ["markdown", "html"])
    @pytest.mark.parametrize(
        ("uri", "reason"),
        [
            ("data:image/svg+xml;base64,PHN2Zy8+", "IMAGE_REF_INVALID"),
            ("data:text/html;base64,PGI+", "IMAGE_REF_INVALID"),
            ("data:image/png;base64,!!!!", "IMAGE_REF_INVALID"),
            ("data:image/png;base64,AAAA", "IMAGE_REF_INVALID"),  # not a PNG
            ("data:image/png,AAAA", "IMAGE_REF_INVALID"),
            ("data:image/bmp;base64,Qk0=", "IMAGE_REF_INVALID"),
            ("DATA:image/png;base64,AAAA", "IMAGE_REF_INVALID"),  # upper case is checked, not skipped
            ("DATA:IMAGE/SVG+XML;base64,PHN2Zy8+", "IMAGE_REF_INVALID"),
            (f"data:image/jpeg;base64,{PNG}", "IMAGE_REF_INVALID"),  # PNG bytes under a JPEG label
        ],
    )
    def test_malformed_and_unsafe_data_uris_are_refused(self, fmt: str, uri: str, reason: str) -> None:
        data = f"![a]({uri})" if fmt == "markdown" else f'<img src="{uri}">'
        e = group_refusal(group(data, fmt))
        assert (e.status, e.reason, e.location) == (422, reason, "blocks.block_groups[0].data")

    def test_a_group_that_is_not_processed_is_not_scanned(self) -> None:
        """Without requires_processing indexing keeps the text as it is and never reads an image from it."""
        refs = check_groups(group("![a](http://spy.test/a.png)", requires_processing=False))
        assert refs.images == 0

    def test_blocks_are_checked_before_groups_and_group_images_count_with_them(self) -> None:
        blocks = [img(DATA_PNG, i) for i in range(990)]
        ok = check_groups(group("".join(f"![a]({DATA_PNG})" for _ in range(10))), blocks=blocks)
        assert ok.images == 1000
        e = group_refusal(group("".join(f"![a]({DATA_PNG})" for _ in range(11))), blocks=blocks)
        assert (e.status, e.reason, e.location) == (422, "LIMIT_EXCEEDED", "blocks.block_groups[0].data")

    def test_group_images_count_across_groups(self) -> None:
        one = group("".join(f"![a]({DATA_PNG})" for _ in range(500)))
        assert check_groups(one, {**one, "index": 1}).images == 1000
        e = group_refusal(one, {**one, "index": 1}, group(f"![a]({DATA_PNG})", index=2))
        assert (e.reason, e.location) == ("LIMIT_EXCEEDED", "blocks.block_groups[2].data")

    def test_html_group_images_count_against_the_cap(self) -> None:
        tag = f'<img src="{DATA_PNG}">'
        assert check_groups(group(tag * 1000, "html")).images == 1000
        assert group_refusal(group(tag * 1001, "html")).reason == "LIMIT_EXCEEDED"

    def test_an_image_flood_is_cut_at_the_cap_without_building_the_list(self) -> None:
        flood = "![a](u)" * ((1 << 20) // 7)
        tracemalloc.start()
        try:
            e = group_refusal(group(flood))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        assert e.reason == "LIMIT_EXCEEDED"
        # About 150,000 entries of five strings each are tens of MiB; the first 1,001 are a fraction of one.
        assert peak < 8 << 20, peak


class TestHtmlBudget:
    """The text of processed HTML groups is capped per record, and the cap is checked before anything is scanned."""

    @staticmethod
    def html_groups(total: int) -> list[dict]:
        """HTML groups of at most 1 MiB each holding ``total`` characters in all (no tags: nothing to parse)."""
        groups, left = [], total
        while left > 0:
            n = min(left, 1 << 20)
            groups.append(group("x" * n, "html", index=len(groups)))
            left -= n
        return groups

    def test_exactly_the_cap_is_accepted(self) -> None:
        assert check_groups(*self.html_groups(I.MAX_HTML_GROUP_BYTES)).images == 0

    def test_one_character_over_is_a_422_naming_the_limit(self) -> None:
        e = group_refusal(*self.html_groups(I.MAX_HTML_GROUP_BYTES + 1))
        assert (e.status, e.reason, e.location) == (422, "LIMIT_EXCEEDED", "blocks.block_groups")
        assert e.extra["limit"] == "htmlGroupBytes" and e.extra["limitBytes"] == I.MAX_HTML_GROUP_BYTES

    def test_the_refusal_comes_before_any_scan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[object] = []
        monkeypatch.setattr(I, "group_image_sources", lambda *a, **k: calls.append(a) or [])
        # A group that would be refused for its image comes first; the budget is still what answers.
        bad = group("<img src='http://spy.test/a.png'>", "html", index=0)
        e = group_refusal(bad, *[{**g, "index": g["index"] + 1} for g in self.html_groups(I.MAX_HTML_GROUP_BYTES)])
        assert e.reason == "LIMIT_EXCEEDED" and e.location == "blocks.block_groups" and calls == []

    @staticmethod
    def inline_image_groups(payload_chars: int, start: int) -> list[dict]:
        """Groups of one valid PNG each (at most 1 MiB per group), holding ``payload_chars`` of base64 in all."""
        per = 900_000 // 4 * 4
        groups, left = [], payload_chars
        while left > 0:
            n = min(left, per)
            n -= n % 4
            raw_len = n // 4 * 3
            png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(raw_len - 8)).decode()
            groups.append(group(f'<img src="data:image/png;base64,{png}">', "html", index=start + len(groups)))
            left -= n
        return groups

    def test_inline_image_bytes_are_not_counted(self) -> None:
        """An honest group is not refused for its images: 1.9 MiB of markup next to 5 MiB of image data is accepted."""
        images = self.inline_image_groups(5 << 20, 0)
        markup = (19 << 20) // 10 - sum(len(g["data"]) - len(g["data"].split(",", 1)[1]) + 2 for g in images)
        groups = [*images, *[{**g, "index": g["index"] + len(images)} for g in self.html_groups(markup)]]
        assert sum(len(g["data"]) for g in groups) > 6 << 20
        assert check_groups(*groups).images == len(images)

    def test_the_payload_must_be_base64_to_be_left_out(self) -> None:
        """Tag characters after a data: prefix are still markup."""
        prefix = "data:image/png;base64,"
        filler = "<b>" * ((I.MAX_HTML_GROUP_BYTES // 3) + 1)
        step = (1 << 20) - len(prefix)
        groups = [group(prefix + filler[i : i + step], "html", index=n) for n, i in enumerate(range(0, len(filler), step))]
        e = group_refusal(*groups)
        assert e.reason == "LIMIT_EXCEEDED" and e.extra["limit"] == "htmlGroupBytes"

    def test_many_data_prefixes_are_counted_in_linear_time(self) -> None:
        started = time.perf_counter()
        I._check_html_budget([parse_envelope(raw(envelope({"block_groups": [group("data:" * 200_000, "html")]}))).blocks.block_groups[0]])
        assert time.perf_counter() - started < 1.0

    def test_markdown_of_the_same_size_is_not_capped(self) -> None:
        groups = [{**g, "format": "markdown"} for g in self.html_groups(I.MAX_HTML_GROUP_BYTES + (1 << 20))]
        assert check_groups(*groups).images == 0

    def test_html_that_is_not_processed_is_not_counted(self) -> None:
        groups = [{**g, "requires_processing": False} for g in self.html_groups(I.MAX_HTML_GROUP_BYTES + (1 << 20))]
        assert check_groups(*groups).images == 0


class TestGroupShapes:
    @pytest.mark.parametrize(
        "g",
        [
            {"index": 0, "type": "table", "requires_processing": True, "data": {"table_summary": "s"}},
            {"index": 0, "type": "table", "requires_processing": True},
            {"index": 0, "type": "text_section", "requires_processing": True},
            {"index": 0, "type": "list", "requires_processing": True, "data": ""},
            {"index": 0, "type": "ordered_list", "requires_processing": True, "format": "html"},
        ],
    )
    def test_requires_processing_needs_a_text_group_with_a_non_empty_string(self, g: dict) -> None:
        e = group_refusal(g)
        assert (e.status, e.reason, e.location) == (422, "BLOCKS_INVALID", "blocks.block_groups[0].requires_processing")

    def test_a_table_group_without_requires_processing_is_unaffected(self) -> None:
        g = {"index": 0, "type": "table", "data": {"table_summary": "s"}, "table_metadata": {"num_of_cells": 1}}
        assert check_groups(g).images == 0


_ADVERSARIAL = [
    "![", "![a](", "![a][", '![a](u "', "![a](u '", "![a]( ", "[a\n", "[a]: u '\n", "<img a ", "<img a='", '<img a="',
    "<img ", "<img a='<img b=\"", "<![CDATA[", "]", "](", "][]", "<", "<!--", "<div>",
]


class TestTimeBounds:
    """A hostile group is refused or accepted in well under a second, wherever its text is aimed."""

    @staticmethod
    def timed(data: str, fmt: str) -> float:
        body = group(data, fmt)
        # A full collection walks the whole test process's heap (over a second here), which the worker's small heap never pays.
        gc.disable()
        try:
            started = time.perf_counter()
            with contextlib.suppress(PushRefusal):
                check_groups(body)
            return time.perf_counter() - started
        finally:
            gc.enable()

    def test_seventy_thousand_image_openers(self) -> None:
        assert self.timed("![" * 70_000, "markdown") < 1.0

    @pytest.mark.parametrize("unit", _ADVERSARIAL)
    @pytest.mark.parametrize("fmt", ["markdown", "html"])
    def test_crafted_classes_at_64_kib(self, unit: str, fmt: str) -> None:
        assert self.timed(unit * (65536 // len(unit)), fmt) < 1.0

    @pytest.mark.parametrize("unit", ["<div>", "<a b='", "<!--", "<", "<![CDATA[", "<p>x</p>"])
    def test_html_with_one_image_tag_pays_for_one_parse_only(self, unit: str) -> None:
        data = unit * (65536 // len(unit)) + f'<img src="{DATA_PNG}">'
        assert self.timed(data, "html") < 1.0

    @pytest.mark.parametrize("tail", ["][]", "][x]", "]", "](", "]( ", "][", '](u "', "]: u"])
    def test_openers_sharing_one_tail(self, tail: str) -> None:
        assert self.timed("![" * 32_000 + tail, "markdown") < 1.0


# ---- the scan sees every URL the lightweight parsers extract (a second layer is only valid if it does) ----


def _scan(data: str, fmt: str) -> set[str]:
    return set(I.group_image_sources(data, fmt, 10**9))


def _extracted(data: str, fmt: str) -> set[str]:
    from app.modules.parsers.html_parser.selectolax_html_parser import (
        SelectolaxHtmlParser,
    )
    from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser

    if fmt == "html":
        return {image["url"] for image in SelectolaxHtmlParser().extract_and_replace_images(data)[1]}
    return {image["url"] for image in MarkdownItParser().extract_and_replace_images(data)[1]}


_DIFFERENTIAL_TOKENS = [
    "![", "]", "[", "(", ")", "a", "http://spy.test/y.png", "<img ", "<IMG\n", 'src="u"', "src='v'", "src=w", " ", "\n", "\t", '"', "'",
    "<", ">", "/", "/>", "=", "]:", "[r]:", "![a][r]", "![a](u)", '![a](u "T")', "<img src=z>", "`", "\\", "&#104;ttp://e/x", "DATA:image/png;base64,AAAA",
    "srcset=", "<picture>", "<source ", "\x00", "junk", "<!--", "-->", "<script>", "</script>", "<p title='", "<a href=\"",
]


class TestScanCoversParserExtraction:
    @pytest.mark.parametrize("fmt", ["markdown", "html"])
    @pytest.mark.parametrize("data", MARKDOWN_REMOTE + HTML_REMOTE + [f"![a]({DATA_PNG})", f'<img src="{DATA_PNG}">', "<img/>", "<img src>"])
    def test_corpus(self, fmt: str, data: str) -> None:
        text = f"intro\n\n{data}\n\nend"
        missing = _extracted(text, fmt) - _scan(text, fmt)
        assert not missing, (fmt, data, missing)

    @pytest.mark.parametrize("fmt", ["markdown", "html"])
    def test_seeded_random_compositions(self, fmt: str) -> None:
        rng = random.Random(20261107)
        for _ in range(10_000):
            text = "".join(rng.choice(_DIFFERENTIAL_TOKENS) for _ in range(rng.randint(1, 14)))
            missing = _extracted(text, fmt) - _scan(text, fmt)
            assert not missing, (fmt, text, missing)

    def test_the_markdown_tag_scan_alone_would_not_be_enough_for_html(self) -> None:
        """The reason an HTML group has its own tag walk: the CommonMark tag grammar misses these."""
        from app.modules.parsers.markdown.image_references import (
            extract_and_replace_images,
        )

        text = '<img src="http://spy.test/k.png"junk>'
        assert "http://spy.test/k.png" in _extracted(text, "html")
        assert "http://spy.test/k.png" not in {i["url"] for i in extract_and_replace_images(text)[1]}
        assert "http://spy.test/k.png" in _scan(text, "html")
