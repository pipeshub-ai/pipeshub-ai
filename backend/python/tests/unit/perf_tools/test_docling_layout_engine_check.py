"""The ONNX-against-PyTorch layout comparison pairs boxes by overlap, not by sorted position."""

from tests.perf import docling_layout_engine_check as check


def _page(*clusters: tuple[str, list[float]]) -> list[dict]:
    return [{"doc": 0, "page": 1, "clusters": sorted(clusters)}]


def test_a_tiny_shift_that_reorders_the_sort_does_not_cross_the_pairs() -> None:
    onnx = _page(("text", [10, 0, 110, 10]), ("text", [10.1, 100, 110.1, 110]))
    torch = _page(("text", [10.1, 0, 110.1, 10]), ("text", [10, 100, 110, 110]))
    assert check.differences(onnx, torch, 0.99) == []


def test_a_box_that_has_no_partner_is_reported() -> None:
    onnx = _page(("text", [0, 0, 10, 10]), ("text", [50, 50, 60, 60]))
    torch = _page(("text", [0, 0, 10, 10]), ("text", [200, 200, 210, 210]))
    assert len(check.differences(onnx, torch, 0.99)) == 1


def test_one_box_cannot_serve_two_partners() -> None:
    onnx = _page(("text", [0, 0, 10, 10]), ("text", [0, 0, 10, 10]))
    torch = _page(("text", [0, 0, 10, 10]), ("text", [500, 500, 510, 510]))
    assert len(check.differences(onnx, torch, 0.99)) == 1


def test_different_labels_are_reported() -> None:
    assert check.differences(_page(("text", [0, 0, 1, 1])), _page(("table", [0, 0, 1, 1])), 0.99)
