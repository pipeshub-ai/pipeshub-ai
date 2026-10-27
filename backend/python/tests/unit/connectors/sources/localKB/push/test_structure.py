"""The structural pre-parse pass."""

import re
import time
from collections.abc import Iterator
from typing import Never

import pytest

from app.connectors.sources.localKB.push import contract as C
from app.connectors.sources.localKB.push import structure as S
from app.connectors.sources.localKB.push.contract import PushRefusal

from .bodies import TEXT, cells_body, envelope, raw, schema_names

NAMES = schema_names()


def refused(body: bytes, names=NAMES) -> PushRefusal:
    with pytest.raises(PushRefusal) as e:
        S.structure_pass(body, names)
    return e.value


def test_valid_body_passes_and_counts() -> None:
    counts = S.structure_pass(raw(envelope()), NAMES)
    assert counts["objects"] == 3 and counts["keys"] == 11


# ---- the field map is derived from the DTO ------------------------------------------

EXPECTED_MAP = {
    "Envelope": {"recordName": S.S, "mimeType": S.S, "externalRecordId": S.S, "version": S.S, "blocksFormat": S.S,
                 "sourceLastModifiedTimestamp": S.S, "metadata": "Meta", "blocks": "Blocks"},
    "Meta": {"schemaId": S.S, "values": "Values"},
    "Blocks": {"block_groups": ("list", "Group"), "blocks": ("list", "Block")},
    "Block": {**dict.fromkeys(("index", "parent_index", "type", "sub_type", "name", "format", "weburl"), S.S),
              "data": "BlockData", "citation_metadata": "Citation", "list_metadata": "ListMd", "table_row_metadata": "RowMd"},
    "BlockData": {"uri": S.S, "cells": S.SL, "row_natural_language_text": S.S, "row_number": S.S},
    "Group": {**dict.fromkeys(("index", "parent_index", "type", "name", "description", "requires_processing", "format", "weburl"), S.S),
              "data": "GroupData", "children": "Children", "citation_metadata": "Citation", "list_metadata": "ListMd",
              "table_metadata": "TableMd"},
    "GroupData": {"table_summary": S.S},
    "Children": {"block_ranges": ("list", "Range"), "block_group_ranges": ("list", "Range")},
    "Range": {"start": S.S, "end": S.S},
    "Citation": dict.fromkeys(("section_title", "page_number", "line_number", "paragraph_number", "sheet_name", "row_number", "column_number", "slide_number"), S.S),
    "ListMd": {"list_style": S.S, "indent_level": S.S, "item_count": S.S},
    "TableMd": {"num_of_rows": S.S, "num_of_cols": S.S, "num_of_cells": S.S, "has_header": S.S,
                "column_names": S.SL, "captions": S.SL, "footnotes": S.SL},
    "RowMd": {"row_number": S.S, "row_span": S.S, "is_header": S.S},
}


def test_field_map_equals_the_reviewed_vocabulary() -> None:
    assert S.FIELD_MAP == EXPECTED_MAP


def test_field_map_has_every_dto_field_and_no_other() -> None:
    for kind, classes in S.MEMBERS.items():
        names = set()
        for cls in classes:
            names |= set(cls.model_fields) if hasattr(cls, "model_fields") else set(cls.__annotations__)
        assert set(S.FIELD_MAP[kind]) == names, kind


def test_a_new_dto_field_enters_the_map_without_a_second_edit() -> None:
    """Negative control for a hand-written map: adding a field to the DTO must reach the pass."""
    from pydantic import create_model

    cls = create_model("X", __base__=C.Range, extra_field=(int, 0))
    assert "extra_field" in S._fields_of(cls)


def test_shape_bounds_are_derived_from_the_dto() -> None:
    assert (S.MAX_VALID_OBJECTS, S.MAX_VALID_KEYS) == (740_004, 3_720_268)


# ---- unknown, misplaced and duplicate keys --------------------------------------

def body_with_block(**block) -> bytes:
    return raw(envelope({"blocks": [{**TEXT, **block}]}))


@pytest.mark.parametrize("body,reason,where", [
    (raw(envelope(extra=1)), "UNKNOWN_FIELD", "Envelope.extra"),
    (body_with_block(x=1), "UNKNOWN_FIELD", "Block.x"),
    (body_with_block(id="abc"), "UNKNOWN_FIELD", "Block.id"),
    (body_with_block(data={"cells": ["a"]}), None, None),  # allowed by the pass; the DTO refuses it
    (raw(envelope({"blocks": [{"index": 0, "type": "image", "data": {"uri": "u", "evil": 1}}]})), "UNKNOWN_FIELD", "BlockData.evil"),
    (raw(envelope({"blocks": [{"index": 0, "type": "table_row", "data": {"cells": ["a"], "evil": 1}}]})), "UNKNOWN_FIELD", "BlockData.evil"),
    (raw(envelope({"block_groups": [{"index": 0, "type": "table", "data": {"table_summary": "s", "evil": 1}}]})), "UNKNOWN_FIELD", "GroupData.evil"),
    (raw(envelope({"block_groups": [{"index": 0, "type": "table", "data": {"cells": ["a"]}}]})), "UNKNOWN_FIELD", "GroupData.cells"),
    (raw(envelope({"blocks": [{"index": 0, "type": "table_row", "data": {"table_summary": "s"}}]})), "UNKNOWN_FIELD", "BlockData.table_summary"),
    (raw(envelope({"block_groups": [{"index": 0, "type": "list", "children": {"block_ranges": [{"start": 0, "end": 0, "z": 1}]}}]})), "UNKNOWN_FIELD", "Range.z"),
])
def test_data_shapes(body, reason, where) -> None:
    if reason is None:
        S.structure_pass(body, NAMES)
        return
    e = refused(body)
    assert (e.status, e.reason, e.location) == (422, reason, where)


def test_group_data_map_open_would_store_evil() -> None:
    """Negative control: with GroupData allowing any key, ``evil`` passes the pass."""
    body = raw(envelope({"block_groups": [{"index": 0, "type": "table", "data": {"table_summary": "s", "evil": 1}}]}))
    S.FIELD_MAP["GroupData"]["evil"] = S.S
    try:
        S.structure_pass(body, NAMES)
    finally:
        del S.FIELD_MAP["GroupData"]["evil"]


def test_duplicate_key() -> None:
    e = refused(b'{"recordName":"a","recordName":"b"}')
    assert (e.reason, e.location) == ("DUPLICATE_FIELD", "Envelope.recordName")
    e = refused(b'{"blocks":{"blocks":[{"index":0,"index":1}]}}')
    assert e.location == "Block.index"


def test_escaped_key_is_unknown_and_named() -> None:
    e = refused(b'{"record\\u004eame":"x"}')
    assert e.reason == "UNKNOWN_FIELD" and "escaped key" in e.message
    e = refused(b'{"blocks":{"blocks":[{"\\u0069ndex":0}]}}')
    assert e.reason == "UNKNOWN_FIELD" and "escaped key" in e.message
    assert "escaped" not in refused(b'{"nope":1}').message


def test_misplaced_known_key_holding_objects() -> None:
    """``cells`` is a DTO name, but not a text block's: positional check, not a global name list."""
    e = refused(raw(envelope({"blocks": [{**TEXT, "cells": [{"a": 1}] * 3}]})))
    assert (e.reason, e.location) == ("UNKNOWN_FIELD", "Block.cells")


def test_global_name_check_would_let_the_misplaced_key_through() -> None:
    """Negative control: checking names globally (the whole vocabulary everywhere) accepts it."""
    for kind in ("Block",):
        S.FIELD_MAP[kind]["cells"] = S.SL
    try:
        with pytest.raises(PushRefusal) as e:
            S.structure_pass(raw(envelope({"blocks": [{**TEXT, "cells": [{"a": 1}]}]})), NAMES)
        assert e.value.reason == "NOT_A_SCALAR" or e.value.reason == "NOT_AN_OBJECT_HERE"
        S.structure_pass(raw(envelope({"blocks": [{**TEXT, "cells": ["a"]}]})), NAMES)  # the DTO would refuse this later
    finally:
        del S.FIELD_MAP["Block"]["cells"]


# ---- containers in the wrong place ---------------------------------------------------------

@pytest.mark.parametrize("body,reason", [
    (b'{"recordName":{"a":1}}', "NOT_AN_OBJECT_HERE"),
    (b'{"recordName":[1]}', "NOT_A_LIST_HERE"),
    (b'{"blocks":[]}', "NOT_A_LIST_HERE"),
    (b'{"blocks":{"blocks":{}}}', "NOT_AN_OBJECT_HERE"),
    (b'{"blocks":{"blocks":[[{}]]}}', "NESTED_LIST"),
    (b'{"blocks":{"blocks":[{"data":{"cells":[[1]]}}]}}', "NESTED_LIST"),
    (b'{"blocks":{"blocks":[{"data":{"cells":[{}]}}]}}', "NOT_A_SCALAR"),
    (b'[]', "NOT_AN_OBJECT_HERE"),
    (b'{}{}', "NOT_AN_OBJECT_HERE"),
])
def test_containers_where_they_do_not_belong(body, reason) -> None:
    assert refused(body).reason == reason


def test_scalar_list_ceiling() -> None:
    ok = {"blocks": [{"index": 0, "type": "table_row", "data": {"cells": ["a"] * 512}}]}
    S.structure_pass(raw(envelope(ok)), NAMES)
    e = refused(raw(envelope({"blocks": [{"index": 0, "type": "table_row", "data": {"cells": ["a"] * 513}}]})))
    assert e.reason == "LIST_TOO_LONG"


# ---- scalars inside a list of objects ------------------------------------------------------------

@pytest.mark.parametrize("name,items", [
    ("zeros", "{}," + "0," * 50 + "0"),
    ("strings", '{},"","","",""'),
    ("interleaved", ",".join(["0"] * 5 + ["{}"] + ["0"] * 5 + ["{}"])),
    ("leading", "0,{}"),
    ("trailing", "{},0"),
    ("only", "0"),
])
def test_scalar_in_object_list(name, items) -> None:
    e = refused(('{"blocks":{"blocks":[' + items + "]}}").encode())
    assert e.reason == "NOT_AN_OBJECT_HERE", name


def test_scalar_in_object_list_without_the_gap_check_passes() -> None:
    """Negative control: the first version of the pass accepted all of these."""
    body = b'{"blocks":{"blocks":[{},0,0,0,{}]}}'
    assert refused(body).reason == "NOT_AN_OBJECT_HERE"
    orig = S._GAP_JUNK
    S._GAP_JUNK = b" \t\r\n,0123456789\""  # a gap check blind to scalars
    try:
        S.structure_pass(body, NAMES)
    finally:
        S._GAP_JUNK = orig


def test_object_list_with_whitespace_and_commas_is_fine() -> None:
    S.structure_pass(b'{"blocks":{"blocks":[ {} , {}\n,\t{} ]}}', NAMES)


# ---- shape bounds inside the loop ----------------------------------------------------------------

def test_object_bound_is_checked_in_the_loop(monkeypatch) -> None:
    monkeypatch.setattr(S, "MAX_VALID_OBJECTS", 5)
    e = refused(b'{"blocks":{"blocks":[{},{},{},{}]}}')
    assert (e.status, e.reason) == (422, "BODY_NOT_VALID_SHAPE")


def test_key_bound_is_checked_in_the_loop(monkeypatch) -> None:
    monkeypatch.setattr(S, "MAX_VALID_KEYS", 3)
    assert refused(raw(envelope())).reason == "BODY_NOT_VALID_SHAPE"


def empty_object_body(size: int) -> bytes:
    head, tail = b'{"blocks":{"blocks":[', b"]}}"
    n = (size - len(head) - len(tail)) // 3
    return head + b",".join([b"{}"] * n) + tail


def test_empty_object_flood_is_refused_early(monkeypatch) -> None:
    """The in-loop bound stops the walk at the crossing token. Counted in tokens, not seconds: a
    wall or CPU limit fails on a slow shared runner without anything being wrong."""
    visited = 0
    real = S._TOKEN

    class Counting:
        def finditer(self, text: bytes) -> Iterator[re.Match[bytes]]:
            nonlocal visited
            for m in real.finditer(text):
                visited += 1
                yield m

    monkeypatch.setattr(S, "_TOKEN", Counting())
    body = empty_object_body(16 << 20)
    e = refused(body)
    assert e.reason == "BODY_NOT_VALID_SHAPE"
    # an empty object is two tokens; the body holds some 5 M of them
    assert visited <= 2 * S.MAX_VALID_OBJECTS + 8, visited


def test_empty_object_flood_without_the_loop_bound_is_a_full_pass(monkeypatch) -> None:
    """Negative control: with the bound out of reach the same body is counted to the end."""
    monkeypatch.setattr(S, "MAX_VALID_OBJECTS", 10**9)
    monkeypatch.setattr(S, "MAX_VALID_KEYS", 10**9)
    counts = S.structure_pass(empty_object_body(2 << 20), NAMES)
    assert counts["objects"] > 500_000  # counted to the end of the body, not stopped at the cap


def test_predictor_is_a_second_line(monkeypatch) -> None:
    monkeypatch.setattr(S, "WORKER_BOUND_BYTES", 1000)
    e = refused(raw(envelope()))
    assert e.status == 413 and e.extra["limit"] == "complexity"
    assert e.extra["budgetBytes"] == 1000 and e.extra["predictedBytes"] > 1000 and e.extra["hint"]


# ---- metadata.values ------------------------------------------------------------------------------

def with_values(values: dict) -> bytes:
    return raw(envelope(metadata={"schemaId": "o:k:1", "values": values}))


def test_values_keys_are_the_schema_names() -> None:
    S.structure_pass(with_values({"p001": "x", "p002": ["a", "b"], "p003": None, "p004": 3}), NAMES)
    e = refused(with_values({"nope": "x"}))
    assert (e.status, e.reason, e.location) == (422, "PROPERTY_UNKNOWN", "nope")
    e = refused(with_values({"orgId": "x"}))
    assert (e.status, e.reason, e.location) == (422, "PROPERTY_RESERVED", "orgId")


def test_values_hold_scalars_or_scalar_lists_only() -> None:
    assert refused(with_values({"p001": {"a": 1}})).reason == "NOT_AN_OBJECT_HERE"
    assert refused(with_values({"p001": [{"a": 1}]})).reason in ("NOT_A_SCALAR", "NOT_AN_OBJECT_HERE")
    assert refused(with_values({"p001": [["a"]]})).reason == "NESTED_LIST"
    assert refused(with_values({"p001": "a" if False else 1, "p001 ": 1})).reason == "PROPERTY_UNKNOWN"


def test_values_duplicate_name() -> None:
    e = refused(b'{"metadata":{"values":{"p001":1,"p001":2}}}')
    assert e.reason == "DUPLICATE_FIELD"


def test_values_lists_of_65_to_512_items_pass_the_pass_and_fail_the_dto() -> None:
    """R6-2 coverage: the DTO bound must hold even though the pass only stops at 513."""
    for n in (65, 512):
        body = with_values({"p001": ["a"] * n})
        S.structure_pass(body, NAMES)
        with pytest.raises(PushRefusal) as e:
            C.parse_envelope(body)
        assert (e.value.status, e.value.reason, e.value.location) == (422, "PROPERTY_INVALID", "p001")
    assert refused(with_values({"p001": ["a"] * 513})).reason == "LIST_TOO_LONG"


def test_values_string_of_1025_bytes_fails_the_dto() -> None:
    body = with_values({"p001": "a" * 1025})
    S.structure_pass(body, NAMES)
    with pytest.raises(PushRefusal) as e:
        C.parse_envelope(body)
    assert (e.value.status, e.value.reason) == (422, "PROPERTY_INVALID")


def test_values_with_256_names_and_no_more() -> None:
    big = dict.fromkeys(sorted(NAMES), 1)
    S.structure_pass(with_values(big), NAMES)
    C.parse_envelope(with_values(big))


# ---- shapes refused with no parse ---------------------------------------------

def never_parse(monkeypatch) -> None:
    def boom(*a, **k) -> Never:
        raise AssertionError("the body reached the parser")
    monkeypatch.setattr(C.Envelope, "model_validate_json", boom)


def test_unknown_key_shapes_never_reach_the_parser(monkeypatch) -> None:
    never_parse(monkeypatch)
    shapes = {
        "one_key_objects": [{"x": {"a": i}} for i in range(3000)],
        "deep_small": [{"x": {"a": {"b": {"c": [i]}}}} for i in range(3000)],
        "pair_objects": [{"x": {"a": 1, "b": 2}} for i in range(3000)],
        "unknown_short_strings": [{"x": ["s"] * 600}],
        "values_lists": None,
    }
    for name, blocks in shapes.items():
        if blocks is None:
            body = with_values({"p001": ["s"] * 600})
        else:
            body = raw(envelope({"blocks": [{**TEXT, **b} for b in blocks]}))
        e = refused(body)
        assert e.reason in ("UNKNOWN_FIELD", "LIST_TOO_LONG"), name


def test_misplaced_known_key_with_a_long_list(monkeypatch) -> None:
    never_parse(monkeypatch)
    e = refused(raw(envelope({"blocks": [{**TEXT, "cells": ["s"] * 600}]})))
    assert e.reason == "LIST_TOO_LONG"


def test_pass_only_reads_bytes() -> None:
    with pytest.raises(TypeError):
        S.structure_pass("not bytes", NAMES)  # type: ignore[arg-type]


# ---- the lexer is linear, also on bodies that are not JSON ---------------------------------------

def unclosed_escapes(n: int) -> bytes:
    return b'{"recordName":"' + b'\\"' * n


def unclosed_quote_backslash(n: int) -> bytes:
    return b'{"a":' + b'"\\' * n


def cpu_of(body: bytes) -> tuple[float, PushRefusal | None]:
    start = time.process_time()
    try:
        S.structure_pass(body, NAMES)
        refusal = None
    except PushRefusal as r:
        refusal = r
    return time.process_time() - start, refusal


def fastest_cpu(body: bytes, runs: int = 3) -> float:
    """The fastest of a few runs: load on a shared runner only ever makes a run slower."""
    return min(cpu_of(body)[0] for _ in range(runs))


# Linear work grows 16x with 16x the bytes and a quadratic scan 256x; 60x tells them apart on any runner.
LINEAR_SLACK = 60


@pytest.mark.parametrize("make", [unclosed_escapes, unclosed_quote_backslash])
@pytest.mark.parametrize("n", [1000, 4000, 16_000, 32_000])
def test_an_unclosed_string_is_refused_as_invalid_json_at_once(make, n) -> None:
    refusal = cpu_of(make(n))[1]
    assert refusal is not None and (refusal.status, refusal.reason) == (400, "INVALID_JSON")
    assert fastest_cpu(make(n)) < 0.25  # the quadratic lexer needed 2.4 s at 8 KB and 39 s at 32 KB


@pytest.mark.parametrize("make", [unclosed_escapes, lambda n: unclosed_escapes(n) + b'"}'])
def test_lexer_cpu_grows_linearly_with_the_body(make) -> None:
    small = min(cpu_of(make(32_000))[0] for _ in range(3))      # 64 KB
    big = min(cpu_of(make(512_000))[0] for _ in range(2))       # 1 MB, 16 times as many bytes
    assert big < max(small, 0.002) * LINEAR_SLACK, (small, big)


def test_an_unclosed_string_of_16_mib_is_cheap() -> None:
    refusal = cpu_of(unclosed_escapes(8_000_000))[1]
    assert refusal is not None and refusal.reason == "INVALID_JSON"
    # Against the same shape at 1/16 of the size, not against seconds.
    assert fastest_cpu(unclosed_escapes(8_000_000), 2) < max(fastest_cpu(unclosed_escapes(500_000)), 0.002) * LINEAR_SLACK


@pytest.mark.parametrize("body", [b'{"a":"x\\', b'"', b'{"a":"', b'{"a":"b"} "', b'{"recordName":"x\\"y'])
def test_other_unclosed_strings_are_invalid_json(body) -> None:
    assert refused(body).reason == "INVALID_JSON"


def test_closed_strings_with_escaped_quotes_still_pass() -> None:
    body = raw(envelope({"blocks": [{**TEXT, "data": 'say "hi" \\ \\" and \\\\'}]}))
    S.structure_pass(body, NAMES)
    assert S.structure_pass(raw(envelope(recordName='a"b')), NAMES)["objects"] == 3


def test_the_old_lexer_is_quadratic_on_the_same_body() -> None:
    """Negative control: the earlier regex, same bytes."""
    import re

    old = re.compile(rb'"((?:[^"\\]|\\.)*)"(?=\s*:)|"(?:[^"\\]|\\.)*"')

    def timed(n: int) -> float:
        body = unclosed_escapes(n)
        runs = []
        for _ in range(3):  # the fastest of three: a busy machine only ever makes a run slower
            start = time.process_time()
            old.sub(rb'"\1"', body)
            runs.append(time.process_time() - start)
        return min(runs)

    # Eight times the input: linear growth is about 8x, quadratic about 64x. A ratio, not a
    # number of seconds, so the runner's speed does not matter.
    assert timed(4000) > 16 * timed(500), "expected superlinear growth"


def test_escaped_key_and_gap_behaviour_survive_the_new_lexer() -> None:
    assert "escaped key" in refused(b'{"record\\u004eame":"x"}').message
    assert refused(b'{"blocks":{"blocks":[{},0,{}]}}').reason == "NOT_AN_OBJECT_HERE"
    assert refused(empty_object_body(16 << 20)).reason == "BODY_NOT_VALID_SHAPE"


# ---- the pass itself is flat in memory, and the predictor covers the measured shapes -------------

def test_the_pass_allocates_no_object_per_string() -> None:
    """The lexer used re.sub, which collected a Python object per string literal first: about 220 bytes
    each, 600 MiB for the 2.8 M cells of a 9 MB body, on top of the parse. Flat now."""
    import tracemalloc

    body = cells_body(600)  # 307,200 cells, about 1 MB
    tracemalloc.start()
    try:
        S.structure_pass(body, NAMES)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 6 << 20, peak / 2**20  # one buffer the size of the body, not one object per string


def test_the_round_7_lexer_allocates_per_string() -> None:
    """Negative control: re.sub with a template, as before."""
    import re
    import tracemalloc

    old = re.compile(rb'"((?:[^"\\]++|\\.)*+)"(?=\s*:)|"(?:[^"\\]++|\\.)*+"', re.DOTALL)
    body = cells_body(600)
    tracemalloc.start()
    try:
        old.sub(rb'"\1"', body)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak > 20 << 20, peak / 2**20


# (objects, keys, elements, string MiB, astral, measured MiB of the whole of task 1 from one baseline) -- see structure.py
MEASURED = {
    "worst valid": (460_001, 1_060_005, 840_004, 0.46, False, 588.7),
    "rich rows": (415_006, 996_016, 747_013, 1.41, False, 500.2),
    "worst warnings": (389_242, 1_018_430, 885_260, 0.54, False, 578.2),
    "sparse cells": (22_076, 66_226, 5_705_108, 0.18, False, 247.7),
    "astral text": (17, 67, 66, 15.0, True, 199.1),
}


@pytest.mark.parametrize("name", list(MEASURED))
def test_the_prediction_covers_the_measured_peak(name: str) -> None:
    objects, keys, elements, string_mib, astral, measured = MEASURED[name]
    predicted = S.predicted_bytes(objects, keys, elements, int(string_mib * 2**20), astral=astral) / 2**20
    assert measured <= predicted <= 704, (name, measured, predicted)


def test_the_list_heavy_shape_is_not_priced_at_the_old_rate() -> None:
    objects, keys, elements, string_mib, astral, measured = MEASURED["sparse cells"]
    predicted = S.predicted_bytes(objects, keys, elements, int(string_mib * 2**20), astral=astral) / 2**20
    assert predicted < 2 * measured  # the old 105 B per element gave 2.4x


def test_possessive_string_matching_keeps_big_invalid_bodies_cheap() -> None:
    """Without the possessive quantifiers every one of these costs several times more."""
    shapes = [
        lambda k: b'{"a":"' + b"\\\\" * k + b"\\",
        lambda k: b'{"a":' + b'"\\' * k,
        lambda k: b'{"a":"' + b"x" * (4 * k),
    ]
    for shape in shapes:
        refusal = cpu_of(shape(4_000_000))[1]
        assert refusal is not None and refusal.reason == "INVALID_JSON"
        big, small = fastest_cpu(shape(4_000_000), 2), fastest_cpu(shape(250_000))
        assert big < max(small, 0.002) * LINEAR_SLACK, (small, big)
