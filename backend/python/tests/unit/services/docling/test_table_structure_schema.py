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


def test_a_response_cannot_carry_text() -> None:
    cell = {"r0": 0, "r1": 1, "c0": 0, "c1": 1, "tokens": [0]}
    sch.TableResult.model_validate({"id": "a", "status": "ok", "cells": [cell]})
    with pytest.raises(ValidationError):
        sch.TableResult.model_validate({"id": "a", "status": "ok", "cells": [{**cell, "text": "hello"}]})
    with pytest.raises(ValidationError):
        sch.TableResult.model_validate({"id": "a", "status": "ok", "text": "hello"})


def test_config_hash_serialises_with_sorted_keys_and_covers_every_part() -> None:
    base = sch.config_hash("4.0.4", "accurate", "w")
    assert base == sch.config_hash("4.0.4", "accurate", "w")
    others = {sch.config_hash("4.0.5", "accurate", "w"), sch.config_hash("4.0.4", "fast", "w"), sch.config_hash("4.0.4", "accurate", "x"), sch.config_hash("4.0.4", "accurate", "w", "other-revision")}
    assert len(others | {base}) == 5
    import hashlib

    assert base == hashlib.sha256(('{"docling_ibm_models":"4.0.4","mode":"accurate","model_revision":"' + sch.MODEL_REVISION + '","weights_sha256":"w"}').encode()).hexdigest()


def test_response_model_block_carries_the_three_parts_and_the_hash() -> None:
    info = sch.ModelInfo(docling_ibm_models="4.0.4", mode="accurate", weights_sha256="w", config_sha256=sch.config_hash("4.0.4", "accurate", "w"))
    resp = sch.TableResponse(tables=[], model=info)
    assert set(resp.model_dump()["model"]) == {"docling_ibm_models", "mode", "weights_sha256", "model_revision", "config_sha256"}
