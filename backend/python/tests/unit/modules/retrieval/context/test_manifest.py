"""The manifest of a rendered result: which characters show which blocks."""

from __future__ import annotations

from app.models.blocks import BlockType, GroupType
from app.modules.retrieval.context.manifest import (
    BlockKey,
    ContentManifest,
    ManifestRegistry,
    ManifestSource,
    RecordSpan,
    Segment,
    manifest_registry,
)
from app.modules.retrieval.context.renderer import render_knowledge
from app.utils.chat_helpers import CitationRefMapper


def _record(vrid: str, name: str) -> dict:
    return {
        "id": f"id-{vrid}",
        "virtual_record_id": vrid,
        "context_metadata": f"Record ID: id-{vrid}\nName: {name}",
        "frontend_url": "https://app.example",
        "block_containers": {"blocks": [], "block_groups": []},
    }


def _text(vrid: str, index: int, text: str) -> dict:
    return {"virtual_record_id": vrid, "block_index": index,
            "block_type": BlockType.TEXT.value, "content": text, "metadata": {}}


def _table(vrid: str, rows: dict[int, str]) -> dict:
    children = [
        {"virtual_record_id": vrid, "block_index": index, "block_type": BlockType.TABLE_ROW.value,
         "content": text, "metadata": {}}
        for index, text in rows.items()
    ]
    return {"virtual_record_id": vrid, "block_index": min(rows), "block_group_index": 0,
            "block_type": GroupType.TABLE.value, "content": ("Revenue by quarter", children),
            "metadata": {}}


def _render(units: list[dict], records: dict) -> tuple[str, object]:
    rendered = render_knowledge(
        units, records, ref_mapper=CitationRefMapper(), is_multimodal_llm=False,
    )
    return rendered.text, rendered.manifest


RECORDS = {"a": _record("a", "Alpha"), "b": _record("b", "Beta")}


class TestSpans:
    def test_each_segment_is_exactly_its_units_text(self) -> None:
        units = [_text("a", 0, "alpha zero"), _text("a", 2, "alpha two"), _text("b", 5, "beta five")]

        text, manifest = _render(units, RECORDS)

        slices = [text[s.start:s.end] for s in manifest.segments]
        assert slices == ["[0|ref1] alpha zero\n\n", "[2|ref2] alpha two\n\n", "[5|ref3] beta five\n\n"]
        assert [s.blocks for s in manifest.segments] == [
            {BlockKey("a", 0)}, {BlockKey("a", 2)}, {BlockKey("b", 5)},
        ]

    def test_record_framing_belongs_to_no_segment(self) -> None:
        text, manifest = _render([_text("a", 0, "alpha zero")], RECORDS)

        covered = "".join(text[s.start:s.end] for s in manifest.segments)
        for framing in ("<record>", "Name: Alpha", "Record blocks (sorted):", "</record>"):
            assert framing in text
            assert framing not in covered

    def test_records_are_located_and_hold_their_segments(self) -> None:
        text, manifest = _render([_text("a", 0, "alpha"), _text("b", 1, "beta")], RECORDS)

        assert [(r.virtual_record_id, r.record_id) for r in manifest.records] == [
            ("a", "id-a"), ("b", "id-b"),
        ]
        for record, segment in zip(manifest.records, manifest.segments):
            assert record.start <= segment.start < segment.end <= record.end
            assert text[record.start:record.end].startswith("<record>")
            assert text[record.start:record.end].endswith("</record>")

    def test_segments_never_overlap(self) -> None:
        units = [_text("a", i, f"alpha {i}") for i in range(5)]
        _, manifest = _render(units, RECORDS)

        ends = [(s.start, s.end) for s in manifest.segments]
        assert all(a_end <= b_start for (_, a_end), (b_start, _) in zip(ends, ends[1:]))

    def test_the_manifest_does_not_change_the_text(self) -> None:
        units = [_text("a", 0, "alpha zero"), _text("b", 5, "beta five")]
        text, _ = _render(units, RECORDS)
        assert text.count("Record blocks (sorted):\n[") == 2


class TestWhatCanBeElided:
    def test_text_blocks_can(self) -> None:
        _, manifest = _render([_text("a", 0, "alpha")], RECORDS)
        assert manifest.segments[0].elidable is True

    def test_a_table_covers_its_rows(self) -> None:
        text, manifest = _render([_table("a", {4: "Q1 10", 5: "Q2 12"})], RECORDS)

        (segment,) = manifest.segments
        assert segment.blocks == {BlockKey("a", 4), BlockKey("a", 5)}
        assert "Revenue by quarter" in text[segment.start:segment.end]
        assert segment.elidable is True

    def test_an_image_cannot(self) -> None:
        image = {"virtual_record_id": "a", "block_index": 3, "block_type": BlockType.IMAGE.value,
                 "content": "a bar chart", "metadata": {}}
        _, manifest = _render([image], RECORDS)
        assert manifest.segments[0].elidable is False

    def test_a_record_summary_hit_has_no_blocks_to_stand_in_for(self) -> None:
        summary = {"virtual_record_id": "a", "block_index": None,
                   "block_type": BlockType.RECORD_SUMMARY.value, "content": "summary", "metadata": {}}
        _, manifest = _render([summary, _text("a", 0, "alpha")], RECORDS)

        assert all(s.elidable for s in manifest.segments if s.blocks)
        assert manifest.blocks == {BlockKey("a", 0)}


def test_the_source_is_recorded() -> None:
    rendered = render_knowledge(
        [_text("a", 0, "alpha")], RECORDS, ref_mapper=CitationRefMapper(),
        is_multimodal_llm=False, source=ManifestSource.PREFETCH,
    )
    assert rendered.manifest.source is ManifestSource.PREFETCH


class TestRegistry:
    def _manifest(self) -> ContentManifest:
        return ContentManifest(
            ManifestSource.SEARCH,
            (Segment(10, 20, frozenset({BlockKey("a", 0)}), True, 0),),
            (RecordSpan("a", "id-a", 5, 30),),
        )

    def test_a_result_is_found_by_its_exact_text(self) -> None:
        registry = ManifestRegistry()
        manifest = self._manifest()
        registry.register("result text", manifest)

        assert registry.lookup("result text") is manifest

    def test_one_changed_character_is_a_different_result(self) -> None:
        """A shaper that cleared, truncated or compacted it made it something
        the manifest no longer describes."""
        registry = ManifestRegistry()
        registry.register("result text", self._manifest())

        assert registry.lookup("result text.") is None

    def test_prefetch_is_kept_apart(self) -> None:
        registry = ManifestRegistry()
        registry.register_prefetch(self._manifest())
        assert len(registry.prefetch) == 1

    def test_the_request_has_one_registry(self) -> None:
        tool_state: dict = {}
        assert manifest_registry(tool_state) is manifest_registry(tool_state)
        tool_state["content_manifests"] = "not a registry"
        assert isinstance(manifest_registry(tool_state), ManifestRegistry)


def test_shifting_moves_every_span() -> None:
    manifest = ContentManifest(
        ManifestSource.SEARCH,
        (Segment(10, 20, frozenset(), True, 0),),
        (RecordSpan("a", "id-a", 5, 30),),
    ).shifted(100)

    assert (manifest.segments[0].start, manifest.segments[0].end) == (110, 120)
    assert (manifest.records[0].start, manifest.records[0].end) == (105, 130)


def test_a_fetch_manifest_shows_blocks_without_spans() -> None:
    manifest = ContentManifest(
        ManifestSource.FETCH, (), (), shown_blocks=frozenset({BlockKey("a", 1)}),
    )
    assert manifest.blocks == {BlockKey("a", 1)}
