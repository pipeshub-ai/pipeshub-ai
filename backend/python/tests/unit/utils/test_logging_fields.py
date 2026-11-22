"""bind_log_fields / log_fields / log_event / log_once (app.utils.logging_fields)."""

from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import TYPE_CHECKING

import pytest

from app.core import org_context
from app.utils import logging_fields as F
from app.utils.logger import TextFormatter, log_record_fields

if TYPE_CHECKING:
    from collections.abc import Iterator


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture() -> Iterator[tuple[logging.Logger, list[logging.LogRecord]]]:
    logger = logging.getLogger("test.logging_fields")
    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, handler.records
    logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)
    logger.propagate = True


def _fields(records: list[logging.LogRecord]) -> dict[str, object]:
    assert len(records) == 1
    return log_record_fields(records[0])


class TestBind:
    def test_bound_field_is_on_the_line(self, capture) -> None:
        logger, records = capture
        with F.log_fields(connector_id="c1", run_id="r1"):
            logger.info("x")
        assert _fields(records) == {"connector_id": "c1", "run_id": "r1"}

    def test_nested_merge_inner_wins_and_reset_restores(self, capture) -> None:
        logger, records = capture
        with F.log_fields(connector_id="c1", run_id="r1"):
            with F.log_fields(run_id="r2", record_id="x"):
                assert dict(F.current_log_fields()) == {"connector_id": "c1", "run_id": "r2", "record_id": "x"}
            assert dict(F.current_log_fields()) == {"connector_id": "c1", "run_id": "r1"}
        assert dict(F.current_log_fields()) == {}

    def test_none_is_skipped(self) -> None:
        with F.log_fields(connector_id=None, run_id="r1"):
            assert dict(F.current_log_fields()) == {"run_id": "r1"}

    def test_unknown_key_raises_naming_it(self) -> None:
        with pytest.raises(ValueError, match="not_a_field"):
            F.bind_log_fields(not_a_field="x")

    @pytest.mark.parametrize("key", ["org_id", "user_id"])
    def test_identity_cannot_be_bound(self, key: str) -> None:
        with pytest.raises(ValueError, match="org_context"):
            F.bind_log_fields(**{key: "x"})

    def test_reset_with_a_token_from_another_context_does_not_raise(self) -> None:
        token = contextvars.Context().run(F.bind_log_fields, run_id="r1")
        F.reset_log_fields(token)
        assert dict(F.current_log_fields()) == {}

    def test_bind_without_reset_ends_with_its_task(self) -> None:
        async def producer() -> dict:
            F.bind_log_fields(run_id="r1")
            return dict(F.current_log_fields())

        async def main() -> tuple[dict, dict]:
            inner = await asyncio.create_task(producer())
            return inner, dict(F.current_log_fields())

        inner, outer = asyncio.run(main())
        assert inner == {"run_id": "r1"} and outer == {}

    def test_copied_into_tasks_and_to_thread_not_into_detached(self) -> None:
        async def main() -> tuple[dict, dict, dict, dict]:
            with F.log_fields(run_id="r1"):
                in_task = await asyncio.create_task(asyncio.sleep(0, result=dict(F.current_log_fields())))
                in_thread = await asyncio.to_thread(lambda: dict(F.current_log_fields()))

                async def read() -> dict:
                    return dict(F.current_log_fields())

                in_request = await org_context.spawn_in_request(read())
                detached = await org_context.spawn_detached(read())
            return in_task, in_thread, in_request, detached

        in_task, in_thread, in_request, detached = asyncio.run(main())
        assert in_task == in_thread == in_request == {"run_id": "r1"}
        assert detached == {}


class TestSubjectUserFields:
    """target_user_id / source_user_id replace an email on per-user lines (docs/logging.md, "Personal data")."""

    def test_both_are_bindable_and_printed_unmasked(self, capture) -> None:
        logger, records = capture
        with F.log_fields(target_user_id="u-0001", source_user_id="src-0001"):
            logger.info("user.synced")
        assert TextFormatter().format(records[0]).endswith(" | target_user_id=u-0001 source_user_id=src-0001")

    def test_bound_inside_each_gathered_coroutine_not_around_gather(self, capture) -> None:
        logger, records = capture

        async def sync_user(source_user_id: str) -> None:
            with F.log_fields(source_user_id=source_user_id):
                await asyncio.sleep(0)
                logger.info("user.synced")

        async def run() -> None:
            await asyncio.gather(sync_user("src-0001"), sync_user("src-0002"))

        asyncio.run(run())
        assert sorted(log_record_fields(r)["source_user_id"] for r in records) == ["src-0001", "src-0002"]

    def test_a_binding_around_gather_reaches_every_coroutine_one_made_before_it_none(self, capture) -> None:
        logger, records = capture

        async def sync_user() -> None:
            await asyncio.sleep(0)
            logger.info("user.synced")

        async def run() -> None:
            with F.log_fields(source_user_id="src-0001"):  # each task copies the context at the gather call
                await asyncio.gather(sync_user(), sync_user())
            coros = []
            for source_user_id in ("src-0002", "src-0003"):
                with F.log_fields(source_user_id=source_user_id):
                    coros.append(sync_user())
            await asyncio.gather(*coros)

        asyncio.run(run())
        assert [log_record_fields(r).get("source_user_id") for r in records] == ["src-0001", "src-0001", None, None]


class TestLogEvent:
    def test_message_is_the_event_and_fields_render(self, capture) -> None:
        logger, records = capture
        F.log_event(logger, "sync_run.end", status="ok", duration_ms=12, skipped=None)
        record = records[0]
        assert record.getMessage() == "sync_run.end" and record.levelno == logging.INFO
        assert record.event_name == "sync_run.end"
        assert _fields(records) == {"status": "ok", "duration_ms": 12}

    def test_reserved_and_identity_names_are_dropped_not_raised(self, capture) -> None:
        logger, records = capture
        F.log_event(logger, "x.end", name="n", msg="m", org_id="o", stack="s", level_name_ok=1)
        assert _fields(records) == {"level_name_ok": 1}

    def test_file_and_line_are_the_callers(self, capture) -> None:
        logger, records = capture
        F.log_event(logger, "x.end")
        assert records[0].filename == "test_logging_fields.py"

    def test_below_level_nothing_is_built(self, capture) -> None:
        logger, records = capture
        logger.setLevel(logging.WARNING)
        F.log_event(logger, "x.end", level=logging.INFO)
        assert records == []

    def test_extra_key_equal_to_a_bound_key_wins_without_keyerror(self, capture) -> None:
        logger, records = capture
        with F.log_fields(connector_id="bound"):
            F.log_event(logger, "x.end", connector_id="explicit")
            logger.info("raw", extra={"connector_id": "raw"})
        assert log_record_fields(records[0]) == {"connector_id": "explicit"}
        assert log_record_fields(records[1]) == {"connector_id": "raw"}
        assert TextFormatter().format(records[1]).endswith(" | connector_id=raw")


class TestLogOnce:
    def test_first_true_then_false_until_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        now = [100.0]
        monkeypatch.setattr(F, "_log_once_last", {})
        monkeypatch.setattr(F.time, "monotonic", lambda: now[0])
        assert F.log_once("k", 30) is True
        now[0] = 129.9
        assert F.log_once("k", 30) is False
        now[0] = 130.0
        assert F.log_once("k", 30) is True

    def test_default_is_once_and_keys_are_independent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(F, "_log_once_last", {})
        assert F.log_once("a") is True and F.log_once("a") is False
        assert F.log_once("b") is True
