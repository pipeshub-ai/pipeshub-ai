"""The headings a hit sits under, shown inline with the hit."""

from __future__ import annotations

from app.models.blocks import BlockType, GroupType
from app.modules.retrieval.context.renderer import RenderedKnowledge, render_knowledge
from app.utils.chat_helpers import CitationRefMapper, _attach_section_paths


def _record(vrid: str) -> dict:
    return {
        "id": f"id-{vrid}",
        "virtual_record_id": vrid,
        "context_metadata": f"Record ID: id-{vrid}",
        "frontend_url": "https://app.example",
        "block_containers": {
            "blocks": [
                {"type": BlockType.TEXT.value, "data": "Seedlings are watered at dawn.",
                 "citation_metadata": {"heading_path": ["Daily care", "Watering"]}},
                {"type": BlockType.TEXT.value, "data": "No heading above this."},
                {"type": BlockType.TABLE_ROW.value, "parent_index": 0,
                 "data": {"row_natural_language_text": "Rack: North, Lamps: 6"}},
                {"type": BlockType.TEXT.value, "data": "A legacy location.",
                 "citation_metadata": {"section_title": "$.racks"}},
            ],
            "block_groups": [
                {"type": GroupType.TABLE.value, "data": {"table_summary": "Lamps per rack"},
                 "citation_metadata": {"heading_path": ["Daily care", "Lighting"]}},
            ],
        },
    }


def _text_unit(vrid: str, index: int, text: str) -> dict:
    return {"virtual_record_id": vrid, "block_index": index, "block_type": BlockType.TEXT.value,
            "content": text, "metadata": {}}


def _render(units: list[dict], records: dict, **kwargs: int) -> RenderedKnowledge:
    _attach_section_paths(units, records)
    return render_knowledge(units, records, ref_mapper=CitationRefMapper(),
                            is_multimodal_llm=False, by_relevance=False, **kwargs)


def test_text_hit_shows_its_headings_inline() -> None:
    records = {"a": _record("a")}
    rendered = _render([_text_unit("a", 0, "Seedlings are watered at dawn.")], records)
    assert "[0|ref1] (§ Daily care › Watering) Seedlings are watered at dawn." in rendered.text


def test_hits_without_stored_headings_render_as_before() -> None:
    records = {"a": _record("a")}
    units = [_text_unit("a", 1, "No heading above this."), _text_unit("a", 3, "A legacy location.")]
    rendered = _render(units, records)
    assert "[1|ref1] No heading above this." in rendered.text
    assert "[3|ref2] A legacy location." in rendered.text
    assert "§" not in rendered.text


def test_the_note_adds_no_citation_ref() -> None:
    records = {"a": _record("a")}
    mapper = CitationRefMapper()
    units = [_text_unit("a", 0, "Seedlings are watered at dawn.")]
    _attach_section_paths(units, records)
    render_knowledge(units, records, ref_mapper=mapper, is_multimodal_llm=False, by_relevance=False)
    assert len(mapper.ref_to_url) == 1


def test_table_shows_its_headings_after_its_header() -> None:
    records = {"a": _record("a")}
    row = {"virtual_record_id": "a", "block_index": 2, "block_type": BlockType.TABLE_ROW.value,
           "content": "Rack: North, Lamps: 6", "metadata": {}}
    table = {"virtual_record_id": "a", "block_index": 2, "block_group_index": 0,
             "block_type": GroupType.TABLE.value, "content": ("Lamps per rack", [row]), "metadata": {}}
    rendered = _render([table], records)
    assert "[Table #0: Lamps per rack] (§ Daily care › Lighting)\n[2|" in rendered.text


def test_render_with_notes_stays_within_max_chars() -> None:
    records = {v: _record(v) for v in ("a", "b", "c")}
    units = [_text_unit(v, 0, "Seedlings are watered at dawn. " * 10) for v in ("a", "b", "c")]
    one = len(_render(units[:1], records).text)
    budget = one * 2 + 10

    rendered = _render(units, records, max_chars=budget)

    assert len(rendered.text) <= budget
    assert rendered.omitted_records == 1
    assert rendered.text.count("(§ Daily care › Watering)") == 2
