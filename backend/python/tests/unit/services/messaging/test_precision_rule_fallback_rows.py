"""The fallback rows of the verdict rule, including the one that no serving precision decides."""

from app.services.messaging.precision_check import IndexState, ServerState, evaluate

INT8, FP32 = "int8-w", "fp32"


def test_a_fallback_with_an_int8_index_is_a_mismatch_even_before_the_serving_precision_is_known() -> None:
    verdict = evaluate({"m": ServerState(None, INT8, True)}, [IndexState("a", "m", FP32), IndexState("c", "m", INT8)])
    assert verdict.mismatch and "'c' holds int8-w" in verdict.reasons[0]


def test_a_fallback_with_an_fp32_index_is_a_match() -> None:
    assert not evaluate({"m": ServerState(FP32, INT8, True)}, [IndexState("c", "m", FP32)]).mismatch


def test_a_fallback_with_no_fp32_index_is_a_mismatch() -> None:
    assert evaluate({"m": ServerState(FP32, INT8, True)}, []).mismatch
    assert evaluate({"m": ServerState(FP32, INT8, True)}, [IndexState("c", "m", None)]).mismatch
