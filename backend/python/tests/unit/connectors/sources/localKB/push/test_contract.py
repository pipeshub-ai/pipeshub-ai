"""The push DTO, normaliser and error translation."""

import copy
import hashlib
import json

import pytest
from pydantic import ValidationError

from app.connectors.sources.localKB.push import contract as C
from app.connectors.sources.localKB.push.contract import PushRefusal
from app.exceptions.indexing_exceptions import BlockContainerValidationError
from app.models.blocks import BlocksContainer
from app.modules.transformers.block_container_validator import BlockContainerValidator

from .bodies import OMIT, TEXT, envelope, raw

GOLDEN = "57b4ddd2ee5ec4b9f9f5e6dae2a75b551cb0bd1902cce3ecafe0853b10da9959"


def parse(obj) -> C.Envelope:
    return C.parse_envelope(raw(obj))


def digest_of(blocks) -> str:
    e = parse(envelope(blocks))
    return C.request_digest(C.blocks_digest(C.canonical_blocks(e.blocks)), None)


def refusal(obj) -> PushRefusal:
    with pytest.raises(PushRefusal) as e:
        parse(obj)
    return e.value


# ---- one digest per equivalence class ------------------------------------------------------------

def test_golden_digest() -> None:
    assert digest_of({"blocks": [TEXT]}) == GOLDEN


GROUP = {"index": 0, "type": "text_section"}
EQUIVALENT = {
    "block_groups omitted / null / []": [{"blocks": [TEXT]}, {"blocks": [TEXT], "block_groups": None}, {"blocks": [TEXT], "block_groups": []}],
    "citation omitted / null / {} / all-null": [
        {"blocks": [TEXT]}, {"blocks": [{**TEXT, "citation_metadata": None}]}, {"blocks": [{**TEXT, "citation_metadata": {}}]},
        {"blocks": [{**TEXT, "citation_metadata": {"page_number": None}}]}],
    "default indent_level": [{"blocks": [TEXT]}, {"blocks": [{**TEXT, "list_metadata": {"indent_level": 0}}]}],
    "key order": [{"blocks": [TEXT]}, {"blocks": [{"data": "hello", "format": "txt", "type": "text", "index": 0}]}],
    "children omitted / {} / empty ranges": [
        {"blocks": [TEXT], "block_groups": [GROUP]}, {"blocks": [TEXT], "block_groups": [{**GROUP, "children": {}}]},
        {"blocks": [TEXT], "block_groups": [{**GROUP, "children": {"block_ranges": []}}]},
        {"blocks": [TEXT], "block_groups": [{**GROUP, "children": {"block_ranges": None, "block_group_ranges": []}}]}],
    "explicit defaults": [{"blocks": [TEXT], "block_groups": [GROUP]}, {"blocks": [TEXT], "block_groups": [{**GROUP, "requires_processing": False}]}],
    "weburl port, case, slash": [
        {"blocks": [{**TEXT, "weburl": "https://EXAMPLE.com:443/a"}]}, {"blocks": [{**TEXT, "weburl": "https://example.com/a"}]}],
}


@pytest.mark.parametrize("name", list(EQUIVALENT))
def test_equivalent_requests_are_byte_identical(name) -> None:
    assert len({digest_of(b) for b in EQUIVALENT[name]}) == 1


def test_changed_content_changes_the_digest() -> None:
    d = {digest_of({"blocks": [TEXT]}), digest_of({"blocks": [{**TEXT, "data": "hellO"}]}), digest_of({"blocks": [{**TEXT, "name": "n"}]})}
    assert len(d) == 3


def test_naive_raw_body_canonicalisation_splits_the_equivalence_set() -> None:
    """Negative control: hashing the client's own JSON (sorted keys) gives at least 3 digests for what the normaliser treats as one."""
    forms = [b for name in EQUIVALENT for b in EQUIVALENT[name]]
    naive = {hashlib.sha256(json.dumps(b, sort_keys=True).encode()).hexdigest() for b in forms[:7]}
    assert len(naive) >= 3


def test_stored_bytes_are_sorted_in_declaration_order_and_have_no_ids() -> None:
    e = parse(envelope({"blocks": [{"data": "x", "index": 0, "type": "text"}]}))
    assert C.stored_bytes(e.blocks, None) == b'{"blocks":[{"index":0,"type":"text","data":"x"}],"push_format":1}'
    assert b'"id"' not in C.stored_bytes(e.blocks, None)


def test_stored_bytes_parse_as_a_blocks_container_and_validate() -> None:
    body = envelope({
        "block_groups": [
            {"index": 0, "type": "text_section", "children": {"block_ranges": [{"start": 0, "end": 1}]}},
            {"index": 1, "type": "table", "table_metadata": {"num_of_cells": 2}, "children": {"block_ranges": [{"start": 2, "end": 3}]}}],
        "blocks": [
            {"index": 0, "type": "text", "format": "txt", "parent_index": 0, "data": "a"},
            {"index": 1, "type": "text", "format": "markdown", "parent_index": 0, "data": "# b"},
            {"index": 2, "type": "table_row", "format": "json", "parent_index": 1, "data": {"cells": ["c", "d"], "row_number": 1}, "table_row_metadata": {"row_number": 1}},
            {"index": 3, "type": "table_row", "format": "json", "parent_index": 1, "data": {"row_natural_language_text": "t"}, "table_row_metadata": {"row_number": 2}}],
    })
    e = parse(body)
    stored = C.stored_bytes(e.blocks, C.canonical_properties("o:k:1", {"b": 1, "a": "x"}))
    container = BlocksContainer.model_validate_json(stored)
    assert [b.data for b in container.blocks][:2] == ["a", "# b"]
    BlockContainerValidator().validate(container)
    BlockContainerValidator().validate(e.blocks)  # and on the DTO itself
    assert json.loads(stored)["properties"] == {"schemaId": "o:k:1", "values": {"a": "x", "b": 1}}


def test_two_parses_of_the_stored_bytes_get_distinct_ids_per_parse() -> None:
    """Block ids come from indexing (uuid4 on parse), never from the push."""
    stored = C.stored_bytes(parse(envelope()).blocks, None)
    a, b = BlocksContainer.model_validate_json(stored), BlocksContainer.model_validate_json(stored)
    assert a.blocks[0].id != b.blocks[0].id



def stored_for(obj, values=None) -> dict:
    e = parse(obj)
    props = None if e.metadata is None else C.canonical_properties(e.metadata.schemaId, values or {})
    return json.loads(C.stored_bytes(e.blocks, props))


def test_metadata_absent_empty_null() -> None:
    assert "properties" not in stored_for(envelope())
    empty = envelope(metadata={"schemaId": "o:k:1", "values": {}})
    assert stored_for(empty)["properties"] == {"schemaId": "o:k:1", "values": {}}
    assert stored_for(empty, {"a": None})["properties"] == {"schemaId": "o:k:1", "values": {}}


# ---- the envelope --------------------------------------------------------------------------------

@pytest.mark.parametrize("ext", ["a/b", "a+b", "a@b", "a=b", "a b", "ok\n", "\nok", "-lead", "_lead", ".lead", "x" * 257, "", "é"])
def test_external_record_id_refused(ext) -> None:
    e = refusal(envelope(externalRecordId=ext))
    assert (e.status, e.reason) == (400, "ENVELOPE_INVALID") and e.location == "externalRecordId"


@pytest.mark.parametrize("ext", ["a", "A1", "k.v~w:x-y_z", "x" * 256, "0abc", "Ab", "ab"])
def test_external_record_id_accepted(ext) -> None:
    assert parse(envelope(externalRecordId=ext)).externalRecordId == ext


def test_case_differs() -> None:
    assert parse(envelope(externalRecordId="Ab")).externalRecordId != parse(envelope(externalRecordId="ab")).externalRecordId


@pytest.mark.parametrize("over", [
    {"version": 2**53}, {"version": -1}, {"version": "1"}, {"version": 1.0}, {"version": True},
    {"blocksFormat": OMIT}, {"blocksFormat": 2}, {"blocksFormat": "1"}, {"blocksFormat": True},
    {"mimeType": "text/plain"}, {"recordName": ""}, {"recordName": "   "}, {"recordName": "x" * 256},
    {"sourceLastModifiedTimestamp": 0}, {"sourceLastModifiedTimestamp": "5"},
])
def test_envelope_refused(over) -> None:
    e = refusal(envelope(**over))
    assert (e.status, e.reason) == (400, "ENVELOPE_INVALID")


def test_version_bounds_accepted() -> None:
    assert parse(envelope(version=0)).version == 0
    assert parse(envelope(version=2**53 - 1)).version == 2**53 - 1


@pytest.mark.parametrize("name", ["<b>x</b>", "a <script>x</script>", "a %s", "%n", "x<img src=1>"])
def test_record_name_with_html_or_format_specifier_is_400(name) -> None:
    assert refusal(envelope(recordName=name)).status == 400


def test_record_name_is_trimmed() -> None:
    assert parse(envelope(recordName="  hi  ")).recordName == "hi"


# ---- strict typing, data shapes, poison ----------------------------------------------------------

def block(**kw) -> dict:
    return {"blocks": [{**TEXT, **kw}]}


def row(**data) -> dict:
    return {"blocks": [{"index": 0, "type": "table_row", "data": data}]}


@pytest.mark.parametrize("blocks,loc", [
    (block(index="0"), "blocks[0].index"),
    (block(index=0.0), "blocks[0].index"),
    (block(index=True), "blocks[0].index"),
    (block(parent_index="0"), "blocks[0].parent_index"),
    ({"blocks": [{"index": 0, "type": "text"}]}, "blocks[0].data"),
    (block(data=None), "blocks[0].data"),
    (block(data=5), "blocks[0].data"),
    (block(data={"uri": "x"}), "blocks[0].data"),
    ({"blocks": [{"index": 0, "type": "image", "data": {"uri": "u", "extra": 1}}]}, "blocks[0].data.extra"),
    ({"blocks": [{"index": 0, "type": "image", "data": {"uri": None}}]}, "blocks[0].data.uri"),
    ({"blocks": [{"index": 0, "type": "image", "data": {"cells": ["a"]}}]}, "blocks[0].data.uri"),
    ({"blocks": [{"index": 0, "type": "image", "data": {}}]}, "blocks[0].data.uri"),
    (row(cells=[]), "blocks[0].data.cells"),
    (row(), "blocks[0].data"),
    (row(cells=["a"], extra="x"), "blocks[0].data.extra"),
    (row(cells=None), "blocks[0].data.cells"),
    (row(row_number="5", cells=["a"]), "blocks[0].data.row_number"),
    (row(row_number=0, cells=["a"]), "blocks[0].data.row_number"),
    (row(row_natural_language_text="", cells=["a"]), "blocks[0].data.row_natural_language_text"),
    (row(cells=[1]), "blocks[0].data.cells[0]"),
    (block(weburl="/relative"), "blocks[0].weburl"),
    (block(weburl="ftp://example.com/a"), "blocks[0].weburl"),
    (block(weburl="javascript:alert(1)"), "blocks[0].weburl"),
    (block(weburl="http://" + "a" * 2050), "blocks[0].weburl"),
    (block(id="b1"), "blocks[0].id"),
    (block(type="paragraph"), "blocks[0]"),
    (block(sub_type="nope"), "blocks[0].sub_type"),
    (block(citation_metadata={"page_number": "1"}), "blocks[0].citation_metadata.page_number"),
    (block(list_metadata={"indent_level": 33}), "blocks[0].list_metadata.indent_level"),
    (block(table_row_metadata={"is_header": 1}), "blocks[0].table_row_metadata.is_header"),
    ({"block_groups": [{"index": 0, "type": "table", "data": {}}]}, "block_groups[0].data.table_summary"),
    ({"block_groups": [{"index": 0, "type": "table", "data": {"table_summary": None}}]}, "block_groups[0].data.table_summary"),
    ({"block_groups": [{"index": 0, "type": "table", "data": {"table_summary": "s", "x": 1}}]}, "block_groups[0].data.x"),
    ({"block_groups": [{"index": 0, "type": "table", "data": "string"}]}, "block_groups[0].data"),
    ({"block_groups": [{"index": 0, "type": "list", "data": {"table_summary": "s"}}]}, "block_groups[0].data"),
    ({"block_groups": [{"index": 0, "type": "sheet"}]}, "block_groups[0]"),
    ({"block_groups": [{"index": 0, "type": "list", "id": "g"}]}, "block_groups[0].id"),
    ({"block_groups": [{"index": 0, "type": "list", "children": {"block_ranges": [{"start": 0, "end": "1"}]}}]},
     "block_groups[0].children.block_ranges[0].end"),
    (block(data="x" * ((1 << 20) + 1)), "blocks[0].data"),
])
def test_block_shapes_are_422_naming_the_location(blocks, loc) -> None:
    e = refusal(envelope(blocks))
    assert (e.status, e.reason) == (422, "BLOCKS_INVALID")
    assert e.location == "blocks." + loc, e.issues


def test_extra_forbidden_names_the_key() -> None:
    e = refusal(envelope({"blocks": [{"index": 0, "type": "image", "data": {"uri": "u", "k": 1}}]}))
    assert e.issues[0]["type"] == "extra_forbidden" and e.issues[0]["location"] == "blocks.blocks[0].data.k"


def test_empty_text_data_is_allowed() -> None:
    assert parse(envelope(block(data=""))).blocks.blocks[0].data == ""


@pytest.mark.parametrize("body", [
    b'{"recordName":"r"',
    b"",
    b"[]",
    b'{"recordName":"\\ud800"}',
])
def test_malformed_json_is_400(body) -> None:
    e = None
    with pytest.raises(PushRefusal) as ei:
        C.parse_envelope(body)
    e = ei.value
    assert e.status == 400


POISON = [
    ("1e400 in data", lambda: b'{"blocks":{"blocks":[{"index":0,"type":"table_row","data":{"row_number":1e400}}]}}'),
    ("1e400 in page_number", lambda: b'{"blocks":{"blocks":[{"index":0,"type":"text","data":"","citation_metadata":{"page_number":1e400}}]}}'),
    ("NaN", lambda: b'{"version":NaN}'),
    ("5000-digit int", lambda: b'{"version":' + b"9" * 5000 + b"}"),
    ("depth 10000", lambda: b'{"blocks":' + b"[" * 10000 + b"]" * 10000 + b"}"),
    ("lone surrogate in a block", lambda: b'{"blocks":{"blocks":[{"index":0,"type":"text","data":"\\ud800"}]}}'),
]


@pytest.mark.parametrize("name,make", POISON, ids=[p[0] for p in POISON])
def test_poison_json_is_a_refusal_never_a_crash(name, make) -> None:
    with pytest.raises(PushRefusal) as e:
        C.parse_envelope(make())
    assert e.value.status in (400, 422)


def test_1e400_in_metadata_values() -> None:
    body = b'{"recordName":"r","mimeType":"application/blocks","externalRecordId":"k","version":1,"blocksFormat":1,"metadata":{"schemaId":"s","values":{"p":1e400}},"blocks":{}}'
    with pytest.raises(PushRefusal) as e:
        C.parse_envelope(body)
    assert e.value.status in (400, 422)


def test_script_in_block_text_is_stored_byte_for_byte() -> None:
    text = "<script>alert(1)</script> & \"quoted\""
    e = parse(envelope(block(data=text)))
    assert json.loads(C.stored_bytes(e.blocks, None))["blocks"][0]["data"] == text


# ---- pipeline-owned fields -----------------------------------------------------------------------

PIPELINE_FIELDS = ["content_hash", "semantic_metadata", "code_facts_version", "children_records", "public_data_link",
                   "image_metadata", "comments", "source_id", "source_type", "file_metadata", "parent_block_index"]


@pytest.mark.parametrize("field", PIPELINE_FIELDS)
@pytest.mark.parametrize("where", ["block", "group", "container"])
def test_pipeline_fields_refused(field, where) -> None:
    if where == "block":
        blocks = block(**{field: "x"})
    elif where == "group":
        blocks = {"block_groups": [{"index": 0, "type": "list", field: "x"}]}
    else:
        blocks = {"blocks": [TEXT], field: "x"}
    e = refusal(envelope(blocks))
    assert (e.status, e.reason) == (422, "BLOCKS_INVALID") and field in e.location


def test_pipeline_fields_with_extra_ignore_would_be_stored() -> None:
    """Negative control: a DTO with extra='ignore' accepts the field and drops it silently."""
    class Loose(C.TextBlock):
        model_config = {"extra": "ignore", "strict": True}

    assert Loose.model_validate({"index": 0, "type": "text", "data": "x", "content_hash": "h"}).index == 0
    with pytest.raises(ValidationError):
        C.TextBlock.model_validate({"index": 0, "type": "text", "data": "x", "content_hash": "h"})


# ---- the structure checks that bound the validator -------------------------------------------------------------

def groups_over(n, **child) -> list:
    return [{"index": i, "type": "list", **child} for i in range(n)]


def test_structure_valid_nesting_and_children() -> None:
    blocks = {
        "block_groups": [{"index": 0, "type": "list", "children": {"block_ranges": [{"start": 0, "end": 0}], "block_group_ranges": [{"start": 1, "end": 1}]}},
                         {"index": 1, "type": "list", "parent_index": 0}],
        "blocks": [{**TEXT, "parent_index": 0}],
    }
    parse(envelope(blocks))


@pytest.mark.parametrize("name,blocks", [
    ("index != position", {"blocks": [{**TEXT, "index": 1}]}),
    ("block parent out of range", {"blocks": [{**TEXT, "parent_index": 0}]}),
    ("group parent out of range", {"block_groups": [{"index": 0, "type": "list", "parent_index": 3}]}),
    ("range past the list", {"blocks": [TEXT], "block_groups": [{"index": 0, "type": "list", "children": {"block_ranges": [{"start": 0, "end": 1}]}}]}),
    ("inverted range", {"blocks": [TEXT], "block_groups": [{"index": 0, "type": "list", "children": {"block_ranges": [{"start": 1, "end": 0}]}}]}),
    ("amplification: many full-width ranges", {"blocks": [{**TEXT, "index": i} for i in range(10)],
        "block_groups": [{"index": 0, "type": "list", "children": {"block_ranges": [{"start": 0, "end": 9}] * 5}}]}),
    ("duplicate child across groups", {"blocks": [TEXT], "block_groups": [
        {"index": 0, "type": "list", "children": {"block_ranges": [{"start": 0, "end": 0}]}},
        {"index": 1, "type": "list", "children": {"block_ranges": [{"start": 0, "end": 0}]}}]}),
    ("overlapping ranges", {"blocks": [{**TEXT, "index": i} for i in range(3)], "block_groups": [
        {"index": 0, "type": "list", "children": {"block_ranges": [{"start": 0, "end": 1}, {"start": 1, "end": 2}]}}]}),
    ("cycle", {"block_groups": [{"index": 0, "type": "list", "parent_index": 1}, {"index": 1, "type": "list", "parent_index": 0}]}),
    ("self parent", {"block_groups": [{"index": 0, "type": "list", "parent_index": 0}]}),
    ("depth 65", {"block_groups": [{"index": i, "type": "list", **({"parent_index": i - 1} if i else {})} for i in range(65)]}),
])
def test_structure_refused(name, blocks) -> None:
    e = refusal(envelope(blocks))
    assert (e.status, e.reason) == (422, "BLOCKS_INVALID"), name


def test_depth_64_is_accepted() -> None:
    parse(envelope({"block_groups": [{"index": i, "type": "list", **({"parent_index": i - 1} if i else {})} for i in range(64)]}))


def test_ranges_per_group_cap_and_counts() -> None:
    ranges = [{"start": 0, "end": 0}] * 1001
    e = refusal(envelope({"blocks": [TEXT], "block_groups": [{"index": 0, "type": "list", "children": {"block_ranges": ranges}}]}))
    assert e.status == 422


def test_caps_are_the_signed_numbers() -> None:
    assert (C.MAX_BLOCKS, C.MAX_GROUPS, C.MAX_RANGES_PER_GROUP, C.MAX_GROUP_DEPTH, C.MAX_CELLS, C.MAX_VALUES) == (
        100_000, 20_000, 1_000, 64, 512, 256)


def test_block_count_cap() -> None:
    ok = [{"index": i, "type": "text", "data": ""} for i in range(100_000)]
    parse(envelope({"blocks": ok}))
    e = refusal(envelope({"blocks": [*ok, {"index": 100000, "type": "text", "data": ""}]}))
    assert (e.status, e.location) == (422, "blocks.blocks")


def test_fail_fast_bounds_the_issue_list() -> None:
    blocks = {"blocks": [{"index": i, "type": "text"} for i in range(20_000)]}  # every block misses data
    body = raw(envelope(blocks))
    with pytest.raises(PushRefusal) as e:
        C.parse_envelope(body)
    assert len(e.value.issues) <= C.MAX_ISSUES and len(e.value.issues) >= 1


def test_without_fail_fast_the_issue_list_is_the_whole_body() -> None:
    """Negative control: fail_fast off on the blocks list lets every bad block raise its own error."""
    from pydantic import BaseModel, ValidationError

    class NoFF(BaseModel):
        blocks: list[C.TextBlock]

    bad = {"blocks": [{"index": i, "type": "text"} for i in range(2000)]}
    with pytest.raises(ValidationError) as e:
        NoFF.model_validate(bad)
    assert e.value.error_count() == 2000
    with pytest.raises(ValidationError) as e:
        C.Blocks.model_validate(bad)
    assert e.value.error_count() < 5


def test_validator_runs_on_the_dto_and_refuses_what_it_refuses_on_a_container() -> None:
    e = parse(envelope({
        "block_groups": [{"index": 0, "type": "table", "table_metadata": {"num_of_cells": 1}, "children": {"block_ranges": [{"start": 0, "end": 0}]}}],
        "blocks": [{"index": 0, "type": "table_row", "format": "txt", "parent_index": 0, "data": {"cells": ["a"]}}],  # a table row must be json
    }))
    with pytest.raises(BlockContainerValidationError):
        BlockContainerValidator().validate(e.blocks)


def test_deepcopy_of_a_parse_is_independent() -> None:
    e = parse(envelope())
    f = copy.deepcopy(e)
    assert C.canonical_blocks(e.blocks) == C.canonical_blocks(f.blocks)
