"""The table-structure request and response models: round trips, limits, no text in a response."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.docling import table_structure_schema as sch


def spec(i: int = 0, cap: int = 100, tokens: int = 2) -> dict:
    return {
        "id": f"p1:b{i}", "image": f"c{i}", "scale_factor": 0.5, "page_w": 1224.0, "page_h": 1584.0,
        "table_bbox": [1, 2, 3, 4], "cap": cap,
        "tokens": [{"id": k, "bbox": [0, 0, 1, 1], "text": "x"} for k in range(tokens)],
    }


def test_request_round_trips() -> None:
    meta = sch.TableRequestMeta.model_validate({"v": 1, "tables": [spec(0), spec(1)]})
    assert sch.TableRequestMeta.model_validate_json(meta.model_dump_json()) == meta


def test_sum_of_caps_over_one_request_is_refused() -> None:
    sch.TableRequestMeta.model_validate({"v": 1, "tables": [spec(0, 512), spec(1, 512)]})
    with pytest.raises(ValidationError):
        sch.TableRequestMeta.model_validate({"v": 1, "tables": [spec(0, 512), spec(1, 513)]})


@pytest.mark.parametrize(
    "bad",
    [
        {"v": 2, "tables": [spec()]},
        {"v": 1, "tables": []},
        {"v": 1, "tables": [spec(i, 10) for i in range(17)]},
        {"v": 1, "tables": [spec(0, cap=1025)]},
        {"v": 1, "tables": [spec(0, cap=0)]},
        {"v": 1, "tables": [spec(0), spec(0)]},
    ],
)
def test_limits_are_enforced(bad) -> None:
    with pytest.raises(ValidationError):
        sch.TableRequestMeta.model_validate(bad)


def test_too_many_tokens_are_refused() -> None:
    big = [spec(i, 10, tokens=5000) for i in range(5)]
    with pytest.raises(ValidationError):
        sch.TableRequestMeta.model_validate({"v": 1, "tables": big})


def test_a_response_never_carries_text_and_unknown_fields_are_ignored() -> None:
    cell = {"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": [0]}
    parsed = sch.TableResult.model_validate({"id": "a", "status": "ok", "cells": [{**cell, "text": "hello", "future": 1}], "text": "hello", "added_later": [1]})
    dumped = parsed.model_dump()
    assert "text" not in dumped and "text" not in dumped["cells"][0] and "future" not in dumped["cells"][0]
    # an older reader meets a newer service's block and keeps working
    info = sch.ModelInfo.model_validate({"docling_ibm_models": "4", "mode": "accurate", "weights_sha256": "w", "config_sha256": "c", "something_new": True})
    assert info.mode == "accurate"
    assert "text" not in sch.TableResponse.model_fields and "text" not in sch.CellOut.model_fields


def test_config_hash_serialises_with_sorted_keys_and_covers_every_part() -> None:
    base = sch.config_hash("4.0.4", "accurate", "w")
    assert base == sch.config_hash("4.0.4", "accurate", "w")
    others = {sch.config_hash("4.0.5", "accurate", "w"), sch.config_hash("4.0.4", "fast", "w"), sch.config_hash("4.0.4", "accurate", "x"), sch.config_hash("4.0.4", "accurate", "w", "other-revision"), sch.config_hash("4.0.4", "accurate", "w", token_definition="other")}
    assert len(others | {base}) == 6
    import hashlib

    assert base == hashlib.sha256(('{"docling_ibm_models":"4.0.4","mode":"accurate","model_revision":"' + sch.MODEL_REVISION + '","token_definition":"' + sch.TOKEN_DEFINITION + '","weights_sha256":"w"}').encode()).hexdigest()


def test_response_model_block_carries_the_three_parts_and_the_hash() -> None:
    info = sch.ModelInfo(docling_ibm_models="4.0.4", mode="accurate", weights_sha256="w", config_sha256=sch.config_hash("4.0.4", "accurate", "w"))
    resp = sch.TableResponse(tables=[], model=info)
    assert set(resp.model_dump()["model"]) == {"docling_ibm_models", "mode", "weights_sha256", "model_revision", "token_definition", "config_sha256"}


def test_a_cell_without_header_flags_validates_and_the_flags_are_null() -> None:
    cell = sch.CellOut.model_validate({"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": [0]})
    assert cell.column_header is None and cell.row_header is None
    flagged = sch.CellOut.model_validate({"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": [0], "column_header": True, "row_header": False})
    assert (flagged.column_header, flagged.row_header) == (True, False)
    # a whole response written before the flags existed
    old = {"v": 1, "tables": [{"id": "a", "status": "ok", "cells": [{"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": [0]}]}]}
    assert sch.TableResponse.model_validate(old).tables[0].cells[0].column_header is None


def test_the_token_definition_is_the_same_on_both_sides() -> None:
    from app.modules.parsers.pdf import table_hybrid as th

    assert th.TOKEN_DEFINITION == sch.TOKEN_DEFINITION


@pytest.mark.parametrize("bad", [1, 0, "true", "yes"])
def test_header_flags_accept_real_booleans_or_null_only(bad) -> None:
    with pytest.raises(ValidationError):
        sch.CellOut.model_validate({"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": [], "column_header": bad})
    with pytest.raises(ValidationError):
        sch.CellOut.model_validate({"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": [], "row_header": bad})
    assert sch.CellOut.model_validate({"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": [], "row_header": None}).row_header is None
