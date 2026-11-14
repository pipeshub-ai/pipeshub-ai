"""The verdict: when the embedding server's precision and the indexes disagree."""

import logging
import random
from types import SimpleNamespace

import pytest

from app.services.messaging.precision_check import (
    HANDBACKS_KEY,
    IndexState,
    PrecisionFilter,
    PrecisionVerdict,
    ServerState,
    counted,
    evaluate,
)

FP32, INT8 = "fp32", "int8-w"
log = logging.getLogger("verdict-test")


def _state(serving=None, fallback=False, intent=INT8):  # noqa: ANN202
    return ServerState(serving, intent, fallback)


class TestTheRule:
    def test_serving_the_other_precision_than_an_index_is_a_mismatch(self) -> None:
        assert evaluate({"m": _state(INT8)}, [IndexState("c", "m", FP32)]).mismatch
        assert evaluate({"m": _state(FP32)}, [IndexState("c", "m", INT8)]).mismatch

    def test_matching_precisions_and_an_unloaded_model_are_not(self) -> None:
        assert not evaluate({"m": _state(INT8)}, [IndexState("c", "m", INT8)]).mismatch
        assert not evaluate({"m": _state(None)}, [IndexState("c", "m", INT8)]).mismatch
        assert not evaluate({"m": _state(INT8)}, [IndexState("c", "m", None)]).mismatch  # a fresh index takes any

    def test_a_fallback_into_an_fp32_index_continues(self) -> None:
        assert not evaluate({"m": _state(FP32, fallback=True)}, [IndexState("c", "m", FP32)]).mismatch

    def test_a_fallback_against_an_int8_index_is_a_mismatch(self) -> None:
        assert evaluate({"m": _state(FP32, fallback=True)}, [IndexState("c", "m", INT8)]).mismatch

    def test_a_fallback_with_no_fp32_index_or_no_index_at_all_is_a_mismatch(self) -> None:
        assert evaluate({"m": _state(FP32, fallback=True)}, [IndexState("c", "m", None)]).mismatch
        assert evaluate({"m": _state(FP32, fallback=True)}, []).mismatch

    def test_each_model_is_compared_with_its_own_indexes(self) -> None:
        states = {"records": _state(INT8), "entities": _state(FP32, fallback=True)}
        indexes = [IndexState("r", "records", INT8), IndexState("e", "entities", FP32)]
        assert not evaluate(states, indexes).mismatch
        assert evaluate(states, [IndexState("r", "records", INT8), IndexState("e", "entities", INT8)]).mismatch

    def test_a_legacy_index_is_fp32_and_an_int8_server_is_a_mismatch(self) -> None:
        assert evaluate({"m": _state(INT8)}, [IndexState("c", "m", FP32)]).mismatch


class TestWhatToDo:
    def test_the_verdict_names_the_precision_the_indexes_hold(self) -> None:
        assert evaluate({"m": _state(INT8)}, [IndexState("c", "m", FP32)]).holds == [FP32]
        assert evaluate({"m": _state(FP32)}, [IndexState("c", "m", INT8)]).holds == [INT8]
        assert evaluate({"m": _state(INT8)}, [IndexState("c", "m", INT8)]).holds == []

    def test_the_log_names_the_value_to_set_and_what_the_cleanup_does(self, caplog) -> None:
        import asyncio

        verdict = PrecisionVerdict(
            fetch_states=lambda: asyncio.sleep(0, {"m": _state(INT8)}),
            indexes=lambda: asyncio.sleep(0, [IndexState("c", "m", FP32)]),
            logger=log,
        )
        with caplog.at_level(logging.ERROR, logger="verdict-test"):
            asyncio.run(verdict.refresh())
        text = caplog.text
        assert "EMBEDDING_SERVER_PRECISION=fp32" in text and "Clean up embeddings" in text and "deletes the whole search index" in text

    def test_the_value_to_set_follows_what_the_index_holds(self) -> None:
        from app.services.messaging.precision_flip import setting_for

        assert setting_for(INT8) == "EMBEDDING_SERVER_PRECISION=int8"
        assert setting_for(FP32).startswith("EMBEDDING_SERVER_PRECISION=fp32")
        assert "index was built with" in setting_for(None)


def _verdict(states, indexes, clock, publish=None):  # noqa: ANN202
    async def fetch():  # noqa: ANN202
        value = states()
        if isinstance(value, Exception):
            raise value
        return value

    async def idx():  # noqa: ANN202
        return indexes()

    return PrecisionVerdict(fetch_states=fetch, indexes=idx, publish=publish, logger=log, clock=lambda: clock[0], poll_seconds=10)


class TestThePoll:
    @pytest.mark.asyncio
    async def test_a_failed_poll_is_not_a_closed_door(self) -> None:
        now = [0.0]
        answer = [{"m": _state(INT8)}]
        verdict = _verdict(lambda: answer[0], lambda: [IndexState("c", "m", FP32)], now)
        await verdict.refresh()
        assert verdict.mismatch
        answer[0] = ConnectionError("down")
        now[0] = 15
        await verdict.refresh()
        assert verdict.mismatch  # the last answer is kept for two periods
        now[0] = 35
        await verdict.refresh()
        assert not verdict.mismatch

    @pytest.mark.asyncio
    async def test_a_flip_seen_in_a_write_lasts_at_least_one_period(self) -> None:
        now = [0.0]
        verdict = _verdict(lambda: {"m": _state(INT8)}, lambda: [IndexState("c", "m", INT8)], now)
        await verdict.refresh()
        verdict.force_mismatch()
        assert verdict.mismatch
        now[0] = 5
        await verdict.refresh()
        assert verdict.mismatch  # the poll says match, the forced period has not ended
        now[0] = 11
        assert not verdict.mismatch

    @pytest.mark.asyncio
    async def test_the_verdict_does_not_flap(self) -> None:
        now = [0.0]
        verdict = _verdict(lambda: {"m": _state(INT8)}, lambda: [IndexState("c", "m", FP32)], now)
        for step in range(30):
            now[0] = step * 10
            await verdict.refresh()
        assert verdict.changes == 1

    @pytest.mark.asyncio
    async def test_the_verdict_key_is_written_on_change_or_once_per_period(self) -> None:
        now = [0.0]
        written: list[bool] = []

        async def publish(v):  # noqa: ANN202
            written.append(v.mismatch)

        verdict = _verdict(lambda: {"m": _state(INT8)}, lambda: [IndexState("c", "m", FP32)], now, publish)
        await verdict.refresh()
        now[0] = 3
        await verdict.refresh()
        assert written == [True]
        now[0] = 12
        await verdict.refresh()
        assert written == [True, True]

    @pytest.mark.asyncio
    async def test_clearing_forgets_the_cached_state(self) -> None:
        now = [0.0]
        verdict = _verdict(lambda: {"m": _state(INT8)}, lambda: [IndexState("c", "m", FP32)], now)
        await verdict.refresh()
        verdict.clear()
        assert not verdict.mismatch


def _message(event, handbacks=0):  # noqa: ANN202
    return SimpleNamespace(eventType=event, payload={HANDBACKS_KEY: handbacks} if handbacks else {})


class TestTheFilter:
    @pytest.mark.asyncio
    async def test_only_vector_writing_events_are_handed_back(self) -> None:
        now = [0.0]
        verdict = _verdict(lambda: {"m": _state(INT8)}, lambda: [IndexState("c", "m", FP32)], now)
        await verdict.refresh()
        flt = PrecisionFilter(verdict, log, rng=random.Random(1))
        messages = [
            _message("newRecord"), _message("updateRecord"), _message("reindexRecord"),
            _message("deleteRecord"), _message("bulkDeleteRecords"), _message("deleteVectorCollection"),
            _message("syncVectorMembership"), None,
        ]
        assert sorted(flt.hand_back(messages)) == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_the_first_hand_back_is_a_minute_and_later_ones_ten(self) -> None:
        now = [0.0]
        verdict = _verdict(lambda: {"m": _state(INT8)}, lambda: [IndexState("c", "m", FP32)], now)
        await verdict.refresh()
        flt = PrecisionFilter(verdict, log, rng=random.Random(1))
        first = flt.hand_back([_message("newRecord")])[0]
        later = flt.hand_back([_message("newRecord", handbacks=1)])[0]
        assert 48 <= first <= 72 and 480 <= later <= 720
        assert counted({HANDBACKS_KEY: 1}) == {HANDBACKS_KEY: 2}

    @pytest.mark.asyncio
    async def test_a_message_still_waiting_out_a_delay_is_not_sent_round_again(self) -> None:
        import time

        now = [0.0]
        verdict = _verdict(lambda: {"m": _state(INT8)}, lambda: [IndexState("c", "m", FP32)], now)
        await verdict.refresh()
        flt = PrecisionFilter(verdict, log, rng=random.Random(1))
        waiting = SimpleNamespace(eventType="newRecord", payload={"_retry_not_before": time.time() + 60})
        due = SimpleNamespace(eventType="newRecord", payload={"_retry_not_before": time.time() - 1})
        junk = SimpleNamespace(eventType="newRecord", payload={"_retry_not_before": "soon"})
        assert sorted(flt.hand_back([waiting, due, junk])) == [1, 2]  # checked again when its delay is over

    @pytest.mark.asyncio
    async def test_nothing_is_handed_back_while_the_precisions_agree(self) -> None:
        now = [0.0]
        verdict = _verdict(lambda: {"m": _state(INT8)}, lambda: [IndexState("c", "m", INT8)], now)
        await verdict.refresh()
        assert PrecisionFilter(verdict, log).hand_back([_message("newRecord")]) == {}
