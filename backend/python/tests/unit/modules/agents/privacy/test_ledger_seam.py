"""The shared ledger seam: a build that labels nothing sends nothing, and the
binding reaches every task a request starts."""

from __future__ import annotations

import asyncio
from typing import Any

from app.modules.agents.chat_extension import ChatExtension
from app.modules.agents.privacy import ledger as privacy
from app.modules.agents.privacy.ledger import (
    NOOP_LEDGER,
    CallKind,
    LabelLedger,
    bind_ledger,
    current_ledger,
    current_model_call,
    model_call_scope,
    note_shown,
    with_provenance,
)
from tests.unit.agents.adapter.conftest import make_context


class _Recording(LabelLedger):
    """Records what the writers tell it, and labels every frame."""

    labels = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, CallKind]] = []
        self.shown_records: list[tuple[str, str | None]] = []

    def observe_call(self, site: str, kind: CallKind, messages: Any = None) -> None:  # noqa: ANN401
        self.calls.append((site, kind))

    def shown(self, *, records=(), vrids=(), nodes=(), call_id=None) -> None:
        self.shown_records.extend((r, call_id) for r in records)

    def wire(self) -> dict[str, Any]:
        return {"v": 5, "complete": True}


class TestTheDefaultLabelsNothing:
    def test_the_default_extension_hands_out_the_no_op(self) -> None:
        opened = ChatExtension().label_ledger(asker_id="u1", conversation_id="c1", run_id="r1", service_identity=False)

        assert opened is NOOP_LEDGER
        assert ChatExtension().unopened_label() is None

    def test_with_nothing_bound_a_frame_is_unchanged(self) -> None:
        data = {"answer": "hi"}

        assert current_ledger() is NOOP_LEDGER
        assert with_provenance(data) is data

    def test_the_no_op_sends_no_label(self) -> None:
        with bind_ledger(NOOP_LEDGER):
            assert with_provenance({"answer": "hi"}) == {"answer": "hi"}
            note_shown(records=["r1"])

    async def test_the_no_op_reads_nothing_about_a_request(self) -> None:
        assert await NOOP_LEDGER.note_request(make_context()) is None


class TestOpening:
    @staticmethod
    def _opened_with(monkeypatch: Any, *, service_agent: bool) -> dict[str, Any]:  # noqa: ANN401
        seen: dict[str, Any] = {}

        class _Edition:
            def label_ledger(self, **kwargs: Any) -> LabelLedger:  # noqa: ANN401
                seen.update(kwargs)
                return NOOP_LEDGER

        monkeypatch.setattr("app.modules.agents.chat_extension.get_chat_extension", lambda: _Edition())
        privacy.open_ledger(make_context(is_service_account=False), service_agent=service_agent)
        return seen

    def test_a_service_agents_run_opens_a_service_ledger_though_the_run_is_the_askers(self, monkeypatch) -> None:
        assert self._opened_with(monkeypatch, service_agent=True)["service_identity"] is True

    def test_any_other_run_opens_the_askers_ledger(self, monkeypatch) -> None:
        assert self._opened_with(monkeypatch, service_agent=False)["service_identity"] is False


class TestBinding:
    def test_a_bound_ledger_labels_the_frame(self) -> None:
        ledger = _Recording()

        with bind_ledger(ledger):
            assert with_provenance({"answer": "hi"}) == {"answer": "hi", "provenance": {"v": 5, "complete": True}}
        assert current_ledger() is NOOP_LEDGER

    def test_a_frame_that_already_carries_a_label_keeps_it(self) -> None:
        with bind_ledger(_Recording()):
            data = {"answer": "card", "provenance": {"v": 5, "complete": False}}
            assert with_provenance(data) is data

    async def test_tasks_started_inside_the_request_see_its_ledger(self) -> None:
        ledger = _Recording()

        async def tool() -> None:
            note_shown(records=["r1"])

        with bind_ledger(ledger):
            await asyncio.gather(asyncio.create_task(tool()), asyncio.create_task(tool()))

        assert [r for r, _ in ledger.shown_records] == ["r1", "r1"]

    def test_an_exit_out_of_order_does_not_revive_a_closed_ledger(self) -> None:
        outer, inner = _Recording(), _Recording()
        first = bind_ledger(outer)
        second = bind_ledger(inner)
        first.__enter__()
        second.__enter__()

        first.__exit__(None, None, None)
        assert current_ledger() is inner
        second.__exit__(None, None, None)
        assert current_ledger() is NOOP_LEDGER

    def test_a_writer_that_raises_never_fails_the_tool(self) -> None:
        class _Broken(_Recording):
            def shown(self, **_: object) -> None:
                raise RuntimeError("boom")

        with bind_ledger(_Broken()):
            note_shown(records=["r1"])


class TestModelCallScope:
    def test_a_scope_tells_the_ledger_and_is_visible_inside(self) -> None:
        ledger = _Recording()

        with bind_ledger(ledger):
            assert current_model_call() is None
            with model_call_scope("grep_generation", CallKind.INSIDE_TOOL):
                assert current_model_call() is not None
                assert current_model_call().site == "grep_generation"
            assert current_model_call() is None

        assert ledger.calls == [("grep_generation", CallKind.INSIDE_TOOL)]

    async def test_a_scope_works_with_async_with(self) -> None:
        ledger = _Recording()

        with bind_ledger(ledger):
            async with model_call_scope("image_generation", CallKind.INSIDE_TOOL):
                assert current_model_call() is not None

        assert ledger.calls == [("image_generation", CallKind.INSIDE_TOOL)]

    def test_a_scope_with_nothing_bound_is_harmless(self) -> None:
        with model_call_scope("auto_model_classifier", CallKind.MODEL_CHOICE):
            assert current_model_call() is not None


class TestToolAttribution:
    def test_what_a_tool_shows_is_attributed_to_the_running_call(self) -> None:
        ledger = _Recording()

        with bind_ledger(ledger):
            token = privacy.enter_tool_call("call-1")
            note_shown(records=["r1"])
            privacy.leave_tool_call(token)
            note_shown(records=["r2"])

        assert ledger.shown_records == [("r1", "call-1"), ("r2", None)]
