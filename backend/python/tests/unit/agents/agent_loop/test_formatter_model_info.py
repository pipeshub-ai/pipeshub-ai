from types import SimpleNamespace

from app.agents.agent_loop.protocol.formatter import (
    AGUIFormatter,
    LegacyFormatter,
    with_model_info,
)


def _context(model_info: dict | None) -> SimpleNamespace:
    return SimpleNamespace(tool_state={"model_info": model_info}, run_id="r1", conversation_id="c1")


def test_legacy_complete_carries_auto_model_info() -> None:
    info = {"modelKey": "k", "modelName": "m", "autoRouted": True}
    [event] = LegacyFormatter().answer_final(_context(info), completion_data={"answer": "hi"})
    assert event["data"]["modelInfo"] == info and event["data"]["answer"] == "hi"


def test_agui_snapshot_and_run_finished_carry_model_info() -> None:
    info = {"modelKey": "k", "autoRouted": True}
    frames = AGUIFormatter().answer_final(_context(info), completion_data={"answer": "hi"})
    assert all("modelInfo" in str(f) for f in frames)


def test_no_auto_turn_leaves_completion_untouched() -> None:
    data = {"answer": "hi"}
    assert with_model_info(_context(None), data) is data
    assert with_model_info(SimpleNamespace(), data) is data


def test_existing_model_info_is_not_overwritten() -> None:
    data = {"answer": "hi", "modelInfo": {"modelKey": "explicit"}}
    assert with_model_info(_context({"modelKey": "auto"}), data)["modelInfo"] == {"modelKey": "explicit"}
