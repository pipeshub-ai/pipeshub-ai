"""SVGs from documents are rasterised in a process that can be stopped."""
from __future__ import annotations

import base64
import contextlib
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.parsers import parse_pool
from app.modules.parsers.image_parser import svg_worker
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.modules.parsers.image_parser.svg_core import check_svg_budget
from harness_core.testing.virtual_time import run_on_virtual_time

if TYPE_CHECKING:
    from collections.abc import Callable

NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
GOOD = f'<svg {NS} width="20" height="20"><rect width="20" height="20" fill="red"/></svg>'


def _b64(svg: str) -> str:
    return base64.b64encode(svg.encode()).decode()


def _slow_but_countable(n: int) -> str:
    # n uses of one symbol: well inside every static count, but cairosvg is slow on it.
    return (
        f'<svg {NS} width="200" height="200"><defs><symbol id="s"><circle r="2"/></symbol></defs>'
        + "".join(f'<use href="#s" x="{i % 190}" y="{i % 97}" width="4" height="4"/>' for i in range(n))
        + "</svg>"
    )


@dataclass
class DeadlineWatch:
    """The deadlines a render was given and waited on.

    The render runs in another process, so its duration on this host's clock also holds whatever kept either
    process off the CPU. What the parent waited until is the code's own decision, and exact.
    """

    asked: list[tuple[float, float]] = field(default_factory=list)  # (when acquire was called, its deadline)
    waited: list[float] = field(default_factory=list)  # every deadline a pipe write or read waited until
    renderers: list[svg_worker._Renderer] = field(default_factory=list)

    def assert_waited_only_until(self, timeout: float) -> None:
        """Every wait ended at the caller's deadline, which was set before the renderer was taken (or started)."""
        assert self.asked and all(deadline <= called_at + timeout for called_at, deadline in self.asked), self.asked
        assert set(self.waited) <= {deadline for _, deadline in self.asked}, (self.asked, self.waited)


def watch_deadlines(monkeypatch: pytest.MonkeyPatch, pool: svg_worker._Renderers) -> DeadlineWatch:
    watch = DeadlineWatch()
    acquire = pool.acquire

    def watched_acquire(deadline: float) -> svg_worker._Renderer:
        watch.asked.append((time.monotonic(), deadline))
        watch.renderers.append(acquire(deadline))
        return watch.renderers[-1]

    monkeypatch.setattr(pool, "acquire", watched_acquire)
    for name in ("_read_line", "_write_all"):
        real = getattr(svg_worker, name)

        def watched(*args: object, _real: Callable[..., object] = real) -> object:
            watch.waited.append(args[-1])  # type: ignore[arg-type]
            return _real(*args)

        monkeypatch.setattr(svg_worker, name, watched)
    return watch


def test_an_ordinary_svg_renders_in_the_child_process() -> None:
    assert ImageParser.svg_base64_to_png_base64(_b64(GOOD)).startswith("iVBOR")


def test_a_child_that_runs_past_its_deadline_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svg_worker, "SVG_DEADLINE_SECONDS", 1.0)
    watch = watch_deadlines(monkeypatch, svg_worker._renderers)
    # "timed out" is the parent giving up at its deadline with the renderer still rendering; a renderer that
    # finished replies, and one its CPU limit stopped is "failed"
    with pytest.raises(DocumentProcessingError, match="timed out"):
        svg_worker.run_bounded(_b64(_slow_but_countable(15000)), timeout=1.0)
    watch.assert_waited_only_until(1.0)
    assert not watch.renderers[-1].alive()


def test_the_memory_cap_stops_an_allocation_and_is_lifted_afterwards() -> None:
    import resource

    before = resource.getrlimit(resource.RLIMIT_AS)
    with svg_worker.memory_cap(extra_bytes=16 * 1024 * 1024), pytest.raises(MemoryError):
        bytearray(512 * 1024 * 1024)
    assert resource.getrlimit(resource.RLIMIT_AS) == before
    assert len(bytearray(64 * 1024 * 1024)) > 0


@pytest.mark.parametrize(
    "svg",
    [
        f'<svg {NS}><g id="a"><use href="  #b "/></g><g id="b"><use xlink:href=" #a"/></g></svg>',
        f'<svg {NS}><pattern id="p"><rect fill="url( #p )"/></pattern></svg>',
        "<svg><g></svg>",
        f'<svg {NS}><use xlink:href="#a"/></svg>'.replace(NS, 'xmlns="http://www.w3.org/2000/svg"'),
    ],
)
def test_the_budget_counts_padded_references_and_refuses_what_it_cannot_parse(svg: str) -> None:
    with pytest.raises(DocumentProcessingError):
        check_svg_budget(svg)


def test_a_padded_href_chain_is_counted() -> None:
    parts = ['<g id="g0"><rect width="1" height="1"/></g>'] + [
        f'<g id="g{i}"><use href=" #g{i - 1}"/><use href=" #g{i - 1}"/></g>' for i in range(1, 30)
    ]
    with pytest.raises(DocumentProcessingError, match="too much"):
        check_svg_budget(f'<svg {NS}>' + "".join(parts) + '<use href="#g29"/></svg>')


@pytest.mark.parametrize("attr", ["fill", "mask", "marker-start", "clip-path", "filter"])
def test_a_url_reference_chain_is_counted(attr: str) -> None:
    parts = ['<g id="q0"><rect width="1" height="1"/></g>'] + [
        f'<g id="q{i}"><rect {attr}="url(#q{i - 1})"/><rect {attr}="url(#q{i - 1})"/></g>' for i in range(1, 30)
    ]
    with pytest.raises(DocumentProcessingError, match="too much"):
        check_svg_budget(f'<svg {NS}>' + "".join(parts) + f'<rect {attr}="url(#q29)"/></svg>')


@pytest.mark.asyncio
async def test_without_a_pool_the_async_path_uses_a_bounded_child() -> None:
    assert parse_pool.parse_pool_workers() == 0
    assert (await ImageParser.svg_base64_to_png_base64_async(_b64(GOOD))).startswith("iVBOR")
    with pytest.raises(DocumentProcessingError):
        await ImageParser.svg_base64_to_png_base64_async(_b64("<svg><g></svg>"))


@pytest.mark.asyncio
async def test_a_hostile_data_uri_is_skipped_and_the_rest_still_render() -> None:
    hostile = "data:image/svg+xml;base64," + _b64(f'<svg {NS}><g id="a"><use href=" #a"/></g></svg>')
    good = "data:image/svg+xml;base64," + _b64(GOOD)
    result = await ImageParser.urls_to_base64([hostile, good], allow_remote_fetch=True)
    assert result[0] is None
    assert result[1] is not None
    assert result[1].startswith("data:image/png;base64,")


REAL_WORLD = {
    "icon with gradient and clip": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><defs>'
        '<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f00"/>'
        '<stop offset="1" stop-color="#00f"/></linearGradient><clipPath id="c"><circle cx="12" cy="12" r="10"/></clipPath>'
        '</defs><rect width="24" height="24" fill="url(#g)" clip-path="url(#c)"/></svg>'
    ),
    "sprite symbols with an undeclared xlink prefix": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="16"><defs><symbol id="dot" viewBox="0 0 8 8">'
        '<circle cx="4" cy="4" r="3"/></symbol></defs>'
        + "".join(f'<use xlink:href="#dot" x="{i * 16}" width="16" height="16"/>' for i in range(3))
        + "</svg>"
    ),
    "chart with text and many marks": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200"><style>.t{font:10px sans-serif}</style>'
        + "".join(f'<rect x="{i * 6}" y="{100 - i}" width="4" height="{i}" fill="#369"/>' for i in range(50))
        + '<text class="t" x="10" y="190">axis</text></svg>'
    ),
}


@pytest.mark.parametrize("name", sorted(REAL_WORLD))
def test_ordinary_svgs_still_render(name: str) -> None:
    assert ImageParser.svg_base64_to_png_base64(_b64(REAL_WORLD[name])).startswith("iVBOR")


def test_the_renderer_environment_is_an_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("MONGO_URI", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "JWT_SECRET", "PYTHONSTARTUP", "SOME_OTHER"):
        monkeypatch.setenv(key, "secret")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = svg_worker._renderer_environment()
    assert set(env) <= svg_worker._ENV_ALLOWLIST | {k for k in env if k.startswith("LC_")}
    assert env["PATH"] == "/usr/bin"
    assert env["LC_ALL"] == "C.UTF-8"
    assert "MONGO_URI" not in env and "SOME_OTHER" not in env


def test_a_renderer_is_not_returned_to_the_pool_after_a_protocol_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    pool = svg_worker._Renderers()
    monkeypatch.setattr(svg_worker, "_renderers", pool)
    (tmp_path / "fake_renderer.py").write_text(
        "import sys\nfor line in sys.stdin:\n    sys.stdout.write('[1, 2]\\n'); sys.stdout.flush()\n"
    )
    monkeypatch.setattr(svg_worker, "_MODULE", "fake_renderer")
    monkeypatch.setattr(svg_worker, "_PYTHON_ROOT", str(tmp_path))
    try:
        with pytest.raises(DocumentProcessingError):
            svg_worker.run_bounded(_b64(GOOD), timeout=3.0)
        assert pool._idle == [] or all(r.alive() for r in pool._idle) and len(pool._all) <= 1
    finally:
        pool.close_all()


def test_a_kill_leaves_a_warm_spare_and_the_deadline_holds_when_cold(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = svg_worker._Renderers()
    monkeypatch.setattr(svg_worker, "_renderers", pool)
    hostile = _slow_but_countable(15000)
    watch = watch_deadlines(monkeypatch, pool)
    try:
        assert not pool._all
        with pytest.raises(DocumentProcessingError, match="timed out"):
            svg_worker.run_bounded(_b64(hostile), timeout=2.0)  # cold renderer
        watch.assert_waited_only_until(2.0)  # its start-up came out of the same 2 s
        assert len(pool._idle) == 1 and pool._idle[0].alive()  # the spare
        spare = pool._idle[0]
        with pytest.raises(DocumentProcessingError, match="timed out"):
            svg_worker.run_bounded(_b64(hostile), timeout=2.0)
        watch.assert_waited_only_until(2.0)
        assert watch.renderers == [watch.renderers[0], spare] and not any(r.alive() for r in watch.renderers)
    finally:
        pool.close_all()


def test_at_most_four_renderers_exist_and_the_others_wait_for_their_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = svg_worker._Renderers()
    monkeypatch.setattr(svg_worker, "_renderers", pool)
    peak = 0
    results: list[object] = []
    lock = __import__("threading").Lock()

    def call() -> None:
        nonlocal peak
        try:
            results.append(svg_worker.run_bounded(_b64(GOOD), timeout=30.0))
        except DocumentProcessingError as exc:
            results.append(exc)
        with lock:
            peak = max(peak, len(pool._all))

    import threading

    threads = [threading.Thread(target=call) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    try:
        assert len(results) == 50
        assert peak <= svg_worker._MAX_RENDERERS
        assert all(isinstance(r, str) for r in results)
    finally:
        pool.close_all()


def test_a_caller_that_finds_every_renderer_busy_gives_up_at_its_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = svg_worker._Renderers()
    monkeypatch.setattr(svg_worker, "_renderers", pool)
    held = [pool.acquire(time.monotonic() + 5) for _ in range(svg_worker._MAX_RENDERERS)]
    watch = watch_deadlines(monkeypatch, pool)
    try:
        # None of the held renderers is ever released: only the caller's own deadline ends its wait.
        with pytest.raises(DocumentProcessingError, match="busy"):
            svg_worker.run_bounded(_b64(GOOD), timeout=1.0)
        watch.assert_waited_only_until(1.0)
        assert watch.renderers == [] and not pool._idle
    finally:
        pool.close_all()
        assert len(held) == svg_worker._MAX_RENDERERS


def test_a_renderer_is_killed_by_its_cpu_limit_even_if_the_parent_never_asks() -> None:
    # The CPU limit is a little above the deadline: ask for 1 s of wall clock
    # and the renderer's own limit (3 s) ends it even if nobody kills it.
    pool = svg_worker._Renderers()
    try:
        renderer = pool.acquire(time.monotonic() + 5)
        request = {**svg_worker._request(_b64(_slow_but_countable(15000)), None, None, 1.0, None), "cpu": 0}
        assert svg_worker._write_all(
            renderer._stdin_fd, (__import__("json").dumps(request) + "\n").encode(), time.monotonic() + 5
        )
        deadline = time.monotonic() + 20
        while renderer.alive() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not renderer.alive()
    finally:
        pool.close_all()


@pytest.mark.asyncio
async def test_the_async_path_never_queues_an_svg_on_the_parse_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """In the parse pool an SVG could wait behind heavy parses and for a cold worker
    before its timeout started, and a worker that failed to start ran the job in a
    service thread. The bounded renderer's one deadline covers all of that."""
    monkeypatch.setattr(parse_pool, "parse_pool_workers", lambda: 2)

    async def no_pool(*_: object, **__: object) -> object:
        raise AssertionError("an SVG was submitted to the parse pool")

    monkeypatch.setattr(parse_pool, "submit", no_pool)

    assert (await ImageParser.svg_base64_to_png_base64_async(_b64(GOOD))).startswith("iVBOR")
    with pytest.raises(DocumentProcessingError):
        await ImageParser.svg_base64_to_png_base64_async(_b64("<svg><g></svg>"))


@pytest.mark.asyncio
async def test_svg_waits_never_take_a_thread_of_the_default_executor() -> None:
    """Every asyncio.to_thread in the service shares the default executor; SVGs
    waiting for a renderer must not hold its threads."""
    import asyncio
    import concurrent.futures

    used: list[object] = []

    class Spy(concurrent.futures.ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs) -> concurrent.futures.Future:  # type: ignore[no-untyped-def,override]
            used.append(fn)
            return super().submit(fn, *args, **kwargs)

    loop = asyncio.get_running_loop()
    spy = Spy(max_workers=2)
    loop.set_default_executor(spy)
    try:
        results = await asyncio.gather(
            *(ImageParser.svg_base64_to_png_base64_async(_b64(GOOD)) for _ in range(12))
        )
    finally:
        spy.shutdown(wait=False)
    assert all(r.startswith("iVBOR") for r in results)
    assert used == []


def test_calls_past_the_renderer_slots_wait_unqueued_and_give_up_at_their_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every slot busy: further calls hold no queued work (and no SVG in the pool's
    queue), and each gives up at its own deadline without ever being submitted."""
    import asyncio

    monkeypatch.setattr(svg_worker, "SVG_DEADLINE_SECONDS", 0.3)
    waiters = svg_worker._waiters
    for _ in range(svg_worker._MAX_RENDERERS):
        assert waiters.slots.acquire(blocking=False)
    submitted: list[object] = []
    pool = waiters.get()
    monkeypatch.setattr(pool, "submit", lambda *a, **k: submitted.append(a))

    async def main() -> tuple[list[object], float]:
        loop = asyncio.get_running_loop()
        # the admission wait polls with asyncio.sleep: on the loop's clock its deadline is exact
        monkeypatch.setattr(svg_worker, "time", SimpleNamespace(monotonic=loop.time))
        start = loop.time()
        calls = [asyncio.ensure_future(ImageParser.svg_base64_to_png_base64_async(_b64(GOOD))) for _ in range(20)]
        outcomes = await asyncio.gather(*calls, return_exceptions=True)
        return outcomes, loop.time() - start

    try:
        outcomes, elapsed = run_on_virtual_time(main)
    finally:
        for _ in range(svg_worker._MAX_RENDERERS):
            waiters.slots.release()
    assert all(isinstance(o, DocumentProcessingError) for o in outcomes), outcomes
    assert all("waiting for a renderer" in str(o) for o in outcomes)
    assert submitted == []
    assert 0.3 <= elapsed < 0.3 + 2 * svg_worker._ADMISSION_POLL_SECONDS


@pytest.mark.asyncio
async def test_a_slot_freed_after_the_deadline_does_not_admit_the_late_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    monkeypatch.setattr(svg_worker, "SVG_DEADLINE_SECONDS", 0.1)
    # Polls far apart, so the slot frees after the deadline and before the next poll.
    monkeypatch.setattr(svg_worker, "_ADMISSION_POLL_SECONDS", 0.4)
    waiters = svg_worker._waiters
    for _ in range(svg_worker._MAX_RENDERERS):
        assert waiters.slots.acquire(blocking=False)
    submitted: list[object] = []
    monkeypatch.setattr(waiters.get(), "submit", lambda *a, **k: submitted.append(a))
    released = False
    try:
        call = asyncio.ensure_future(ImageParser.svg_base64_to_png_base64_async(_b64(GOOD)))
        await asyncio.sleep(0.2)  # past the deadline, before the next poll
        waiters.slots.release()
        released = True
        with pytest.raises(DocumentProcessingError, match="waiting for a renderer"):
            await asyncio.wait_for(call, 2)
    finally:
        for _ in range(svg_worker._MAX_RENDERERS - (1 if released else 0)):
            waiters.slots.release()
    assert submitted == []
    held = 0
    while waiters.slots.acquire(blocking=False):
        held += 1
    for _ in range(held):
        waiters.slots.release()
    assert held == svg_worker._MAX_RENDERERS, "the late call gave its slot back"


@pytest.mark.asyncio
async def test_every_path_gives_its_renderer_slot_back() -> None:
    import asyncio

    waiters = svg_worker._waiters
    results = await asyncio.gather(
        *(ImageParser.svg_base64_to_png_base64_async(_b64(GOOD)) for _ in range(6)),
        *(ImageParser.svg_base64_to_png_base64_async(_b64("<svg><g></svg>")) for _ in range(3)),
        return_exceptions=True,
    )
    assert sum(isinstance(r, str) for r in results) == 6
    cancelled = asyncio.ensure_future(ImageParser.svg_base64_to_png_base64_async(_b64(GOOD)))
    await asyncio.sleep(0)
    cancelled.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cancelled
    await asyncio.sleep(0.5)
    held = 0
    while waiters.slots.acquire(blocking=False):
        held += 1
    for _ in range(held):
        waiters.slots.release()
    assert held == svg_worker._MAX_RENDERERS


def test_closing_a_renderer_does_not_wait_for_a_process_that_will_not_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess
    import threading

    reaping = threading.Event()
    exits = threading.Event()  # set only once close() has returned: the process "will not exit" until then
    reapers: list[threading.Thread] = []

    class StuckProcess:
        pid = 2**22 + 7  # nothing to signal; killpg's error is suppressed
        stdin = stdout = None

        def wait(self, timeout: float | None = None) -> int:
            if timeout is not None:
                raise subprocess.TimeoutExpired("renderer", timeout)
            reapers.append(threading.current_thread())
            reaping.set()
            exits.wait(10)
            return -9

    renderer = svg_worker._Renderer.__new__(svg_worker._Renderer)
    renderer.process = StuckProcess()  # type: ignore[assignment]
    renderer._closed = False
    monkeypatch.setattr(renderer, "release_pipes", lambda: None)
    try:
        renderer.close()
        assert reaping.wait(2), "the stuck renderer is still reaped, in the background"
        assert reapers and threading.current_thread() not in reapers  # close() did not wait for it
    finally:
        exits.set()


def test_a_deadline_already_passed_is_refused_without_taking_a_renderer() -> None:
    with pytest.raises(DocumentProcessingError, match="timed out before it started"):
        svg_worker.run_bounded(_b64(GOOD), deadline=time.monotonic() - 1)
