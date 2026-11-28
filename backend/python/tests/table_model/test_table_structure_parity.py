"""Same-job parity: the product path (worker request builder, then the service's child) against the evaluation
harness's call (``multi_table_predict`` with the full page image), in one process on one machine, on committed fixtures.
No golden file made elsewhere."""
from __future__ import annotations

from tests.table_model import support


def boxes():  # noqa: ANN201
    out = []
    for page in support.fixture_pages():
        for box in page["boxes"]:
            words = support.box_words(page["words"], box)
            out.append((page["page"], box, words, support.plan(words)))
    return out


def run_both(harness, supervisor, mutate=None):  # noqa: ANN201
    pdf = str(support.FIXTURES / "tables.pdf")
    diffs = compared = 0
    for page_no, box, words, plan in boxes():
        if plan.reason != "none":
            continue
        reference = harness.run(pdf, page_no, words, box, plan.cap)
        spec, png = support.product_request(pdf, page_no, words, box, plan.cap, mutate=mutate)
        got = support.product_signature(supervisor, spec, png)
        compared += 1
        diffs += reference != got
    return compared, diffs


def test_the_fixtures_hold_enough_boxes() -> None:
    assert sum(1 for *_, plan in boxes() if plan.reason == "none") >= 8


def test_product_path_equals_the_harness_call_on_every_fixture_box(harness, supervisor) -> None:
    compared, diffs = run_both(harness, supervisor)
    assert compared >= 8
    assert diffs == 0


def test_negative_control_shifted_token_ids_differ(harness, supervisor) -> None:
    compared, diffs = run_both(harness, supervisor, mutate="shift_ids")
    assert diffs == compared  # every box's membership moves


def test_negative_control_a_scale_factor_off_by_one_percent_is_reported(harness, supervisor) -> None:
    compared, diffs = run_both(harness, supervisor, mutate="scale")
    print(f"scale x1.01: {diffs} of {compared} fixture boxes differ")
    assert compared >= 8  # measured 0 of 8 here: generated pages are too forgiving to need it; the shifted-id control is the guaranteed one


def test_a_crop_only_request_is_reported(harness, supervisor) -> None:
    compared, diffs = run_both(harness, supervisor, mutate="crop_only")
    print(f"crop only: {diffs} of {compared} fixture boxes differ")
    assert compared >= 8  # measured 0 of 8 here (21 of 203 on real documents); reported, not gated


def test_resize_page_equals_the_library_function(harness) -> None:
    import numpy as np

    from app.modules.parsers.pdf.table_hybrid_flow import resize_page

    img = np.random.default_rng(3).integers(0, 255, (1584, 1224, 3), dtype=np.uint8)
    ours, sf = resize_page(img)
    theirs, sf2 = harness.pred.resize_img(img, height=1024)
    assert sf == sf2 and np.array_equal(ours, theirs)
