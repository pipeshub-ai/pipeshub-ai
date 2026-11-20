"""The step cap binds: it sets the decode loop's private bound, so a library that renames it must fail here."""
from __future__ import annotations

from tests.table_model import support
from tests.table_model.test_table_structure_parity import boxes


def first_table():  # noqa: ANN201
    page_no, box, words, plan = next(b for b in boxes() if b[3].reason == "none")
    return page_no, box, words, plan


def test_a_cap_of_ten_steps_is_a_cap_hit_with_no_grid(supervisor) -> None:
    page_no, box, words, plan = first_table()
    spec, png = support.product_request(str(support.FIXTURES / "tables.pdf"), page_no, words, box, 10)
    result = support.run_async(supervisor.run_table({k: v for k, v in spec.items() if k != "image"}, png))
    assert result["status"] == "cap_hit" and result["steps"] <= 10 + 1 and "cells" not in result


def test_the_planned_cap_lets_the_same_table_finish(supervisor) -> None:
    page_no, box, words, plan = first_table()
    spec, png = support.product_request(str(support.FIXTURES / "tables.pdf"), page_no, words, box, plan.cap)
    result = support.run_async(supervisor.run_table({k: v for k, v in spec.items() if k != "image"}, png))
    assert result["status"] == "ok" and result["steps"] <= plan.cap


def test_the_decode_loop_reads_the_attribute_the_service_sets(harness) -> None:
    assert hasattr(harness.model, "_max_pred_len")
    from importlib.metadata import version

    assert version("docling-ibm-models") == "4.0.4"


def test_cells_carry_the_models_header_tags_or_null(supervisor) -> None:
    page_no, box, words, plan = first_table()
    spec, png = support.product_request(str(support.FIXTURES / "tables.pdf"), page_no, words, box, plan.cap)
    result = support.run_async(supervisor.run_table({k: v for k, v in spec.items() if k != "image"}, png))
    flags = [(c["column_header"], c["row_header"]) for c in result["cells"]]
    assert all(a in (True, False, None) and b in (True, False, None) for a, b in flags)
    print("header tags seen:", sorted(set(flags), key=str))
