"""The platform event bus: what an emitter and a listener may rely on."""

import asyncio
import threading
import time
from typing import Never

import pytest

from app import platform_events


@pytest.fixture(autouse=True)
def _own_subscriptions():
    """Each test adds its own listeners and takes them away; the suite's stay."""
    added = []
    real_on = platform_events.on

    def on(name, handler):
        unsubscribe = real_on(name, handler)
        added.append(unsubscribe)
        return unsubscribe

    platform_events.on = on
    try:
        yield
    finally:
        platform_events.on = real_on
        for unsubscribe in added:
            unsubscribe()


def test_emit_reaches_a_listener_with_the_name_and_payload() -> None:
    seen = []
    platform_events.on("test.thing.happened", lambda name, payload: seen.append((name, dict(payload))))
    platform_events.emit("test.thing.happened", org_id="o1", count=3)
    assert seen == [("test.thing.happened", {"org_id": "o1", "count": 3})]


def test_emit_returns_none_and_a_payload_may_carry_a_name_field() -> None:
    seen = []
    platform_events.on("test.named", lambda name, payload: seen.append(payload["name"]))
    assert platform_events.emit("test.named", name="x") is None
    assert seen == ["x"]


def test_emit_with_no_listener_does_nothing() -> None:
    platform_events.emit("test.nobody.listens", a=1)


def test_emit_with_no_listener_is_cheap() -> None:
    """A guard against an accidental lock, copy or log line on the hot path, not a benchmark:
    the limit is some twenty times what an emit costs on a slow machine."""
    name = "test.nobody.listens.hot"
    wildcard = platform_events._listeners.pop("*", None)
    try:
        n = 200_000
        started = time.perf_counter()
        for _ in range(n):
            platform_events.emit(name, org_id="o1", duration_seconds=0.5)
        per_call = (time.perf_counter() - started) / n
    finally:
        if wildcard is not None:
            platform_events._listeners["*"] = wildcard
    assert per_call < 5e-6


def test_a_raising_listener_does_not_reach_the_emitter_and_is_counted() -> None:
    seen = []

    def broken(name, payload) -> Never:
        raise RuntimeError("listener bug")

    platform_events.on("test.broken", broken)
    platform_events.on("test.broken", lambda name, payload: seen.append(payload["n"]))
    before = platform_events.listener_errors().get("test.broken", 0)
    platform_events.emit("test.broken", n=1)
    platform_events.emit("test.broken", n=2)
    assert seen == [1, 2]  # the listener after the broken one still ran
    assert platform_events.listener_errors()["test.broken"] == before + 2


def test_wildcard_listener_sees_every_event() -> None:
    seen = []
    platform_events.on("*", lambda name, payload: seen.append(name) if name.startswith("test.wild.") else None)
    platform_events.emit("test.wild.one")
    platform_events.emit("test.wild.two", a=1)
    assert seen == ["test.wild.one", "test.wild.two"]
    assert platform_events.has_listeners("test.wild.anything")


def test_unsubscribe_stops_delivery_and_is_idempotent() -> None:
    seen = []
    unsubscribe = platform_events.on("test.unsub", lambda name, payload: seen.append(1))
    platform_events.emit("test.unsub")
    unsubscribe()
    unsubscribe()
    platform_events.emit("test.unsub")
    assert seen == [1]
    assert "test.unsub" not in platform_events._listeners


def test_unsubscribe_removes_only_its_own_subscription() -> None:
    seen = []

    def handler(name, payload) -> None:
        seen.append(1)

    first = platform_events.on("test.twice", handler)
    platform_events.on("test.twice", handler)
    first()
    first()
    platform_events.emit("test.twice")
    assert seen == [1]


def test_listeners_run_in_subscription_order_then_wildcards() -> None:
    order = []
    platform_events.on("*", lambda name, payload: order.append("wild") if name == "test.order" else None)
    platform_events.on("test.order", lambda name, payload: order.append("first"))
    platform_events.on("test.order", lambda name, payload: order.append("second"))
    platform_events.emit("test.order")
    assert order == ["first", "second", "wild"]


def test_a_listener_may_unsubscribe_during_an_emit() -> None:
    seen = []
    holder = {}

    def once(name, payload) -> None:
        seen.append("once")
        holder["unsubscribe"]()

    holder["unsubscribe"] = platform_events.on("test.reentrant", once)
    platform_events.on("test.reentrant", lambda name, payload: seen.append("other"))
    platform_events.emit("test.reentrant")
    platform_events.emit("test.reentrant")
    assert seen == ["once", "other", "other"]


def test_emit_and_subscribe_from_many_threads() -> None:
    counts = [0] * 8
    lock = threading.Lock()
    total = {"n": 0}

    def counted(name, payload) -> None:
        with lock:
            total["n"] += 1

    platform_events.on("test.threads", counted)
    stop = threading.Event()

    def churn() -> None:
        while not stop.is_set():
            platform_events.on("test.threads", lambda name, payload: None)()

    def emitter(index) -> None:
        for _ in range(2_000):
            platform_events.emit("test.threads", i=index)
            counts[index] += 1

    churner = threading.Thread(target=churn)
    churner.start()
    threads = [threading.Thread(target=emitter, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stop.set()
    churner.join()
    assert total["n"] == sum(counts) == 16_000
    assert platform_events.listener_errors().get("test.threads", 0) == 0


async def test_emit_from_asyncio_code_runs_the_listener_inline_without_awaiting() -> None:
    seen = []
    loop = asyncio.get_running_loop()
    platform_events.on("test.async", lambda name, payload: seen.append(asyncio.get_running_loop() is loop))

    async def worker(n) -> None:
        platform_events.emit("test.async", n=n)  # no await: emit is synchronous

    await asyncio.gather(*(worker(n) for n in range(5)))
    assert seen == [True] * 5


async def test_a_listener_hands_slow_work_to_the_loop_instead_of_blocking_the_emitter() -> None:
    queue: asyncio.Queue = asyncio.Queue()
    platform_events.on("test.handoff", lambda name, payload: queue.put_nowait(payload["n"]))
    started = time.perf_counter()
    platform_events.emit("test.handoff", n=7)
    assert time.perf_counter() - started < 0.05
    assert await asyncio.wait_for(queue.get(), 1) == 7
