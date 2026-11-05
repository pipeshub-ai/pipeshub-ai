"""The single-flight guard that imports ``docling`` off the event loop (app/utils/lazy_heavy.py)."""
from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import time
from typing import TYPE_CHECKING

import pytest

from app.utils import lazy_heavy

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _fresh_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lazy_heavy._state, "done", False)
    monkeypatch.setattr(lazy_heavy._state, "lock", None)
    monkeypatch.setattr(lazy_heavy._state, "lock_loop", None)


@pytest.fixture
def fake_heavy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, list[int]]]:
    """A stand-in library whose import takes a moment, plus a record of which thread imported it."""
    name = "fake_heavy_lib_for_lazy_heavy_test"
    (tmp_path / f"{name}.py").write_text("import time\ntime.sleep(0.2)\nVALUE = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    imported_on: list[int] = []
    attempts: list[int] = []

    class Recorder:
        def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
            if fullname == name:
                imported_on.append(threading.get_ident())

    recorder = Recorder()
    sys.meta_path.insert(0, recorder)
    monkeypatch.setattr(lazy_heavy, "_MODULES", (name,))
    def counted_import() -> None:
        attempts.append(threading.get_ident())
        importlib.import_module(name)

    monkeypatch.setattr(lazy_heavy, "_import_docling", counted_import)
    monkeypatch.setattr(lazy_heavy, "_attempts", attempts, raising=False)
    yield name, imported_on
    sys.meta_path.remove(recorder)
    sys.modules.pop(name, None)


def _assert_no_import_on(loop_thread: int, imported_on: list[int]) -> None:
    assert imported_on, "the import never ran"
    assert loop_thread not in imported_on, "an import ran on the event loop thread"


async def test_twenty_concurrent_first_calls_run_one_import_in_a_worker_thread(fake_heavy: tuple[str, list[int]]) -> None:
    name, imported_on = fake_heavy
    loop_thread = threading.get_ident()
    await asyncio.gather(*(lazy_heavy.ensure_docling() for _ in range(20)))
    # The interpreter's own import lock would dedupe the module load; count the guard's attempts.
    assert len(lazy_heavy._attempts) == 1  # type: ignore[attr-defined]
    assert len(imported_on) == 1
    _assert_no_import_on(loop_thread, imported_on)
    assert name in sys.modules


async def test_later_calls_return_without_importing_again(fake_heavy: tuple[str, list[int]]) -> None:
    _, imported_on = fake_heavy
    await lazy_heavy.ensure_docling()
    sys.modules.pop(fake_heavy[0])
    await lazy_heavy.ensure_docling()
    assert len(imported_on) == 1
    assert len(lazy_heavy._attempts) == 1  # type: ignore[attr-defined]


async def test_the_loop_keeps_running_while_the_import_runs(fake_heavy: tuple[str, list[int]]) -> None:
    ticks = []

    async def probe() -> None:
        while True:
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.01)

    task = asyncio.create_task(probe())
    await lazy_heavy.ensure_docling()
    task.cancel()
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert len(ticks) > 5 and max(gaps) < 0.1


async def test_a_failed_import_raises_and_is_retried_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def flaky() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise ImportError("broken install")

    monkeypatch.setattr(lazy_heavy, "_import_docling", flaky)
    with pytest.raises(ImportError, match="broken install"):
        await lazy_heavy.ensure_docling()
    await lazy_heavy.ensure_docling()
    await lazy_heavy.ensure_docling()
    assert len(calls) == 2


async def test_callers_waiting_on_a_failed_import_each_see_it_or_the_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def flaky() -> None:
        calls.append(1)
        time.sleep(0.05)
        if len(calls) == 1:
            raise ImportError("first fails")

    monkeypatch.setattr(lazy_heavy, "_import_docling", flaky)
    results = await asyncio.gather(*(lazy_heavy.ensure_docling() for _ in range(5)), return_exceptions=True)
    assert sum(isinstance(r, ImportError) for r in results) == 1
    assert len(calls) == 2


async def test_detector_counterexample_an_inline_import_is_caught(fake_heavy: tuple[str, list[int]]) -> None:
    """The 'off the loop' assertion must fail against a guard that imports inline."""
    name, imported_on = fake_heavy
    loop_thread = threading.get_ident()

    async def inline_ensure() -> None:
        importlib.import_module(name)  # what an unguarded call site does

    await inline_ensure()
    with pytest.raises(AssertionError, match="event loop thread"):
        _assert_no_import_on(loop_thread, imported_on)


def test_the_guard_is_usable_from_two_event_loops_in_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(lazy_heavy, "_import_docling", lambda: calls.append(1))
    asyncio.run(lazy_heavy.ensure_docling())
    monkeypatch.setattr(lazy_heavy._state, "done", False)
    asyncio.run(lazy_heavy.ensure_docling())
    assert len(calls) == 2


async def test_the_converter_properties_refuse_to_import_docling_on_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reaching a sync converter from a coroutine before ensure_docling() must fail, not stall."""
    from app.modules.parsers.html_parser.docling_html_parser import DoclingHtmlParser
    from app.modules.parsers.markdown.docling_markdown_parser import DoclingMarkdownParser
    from app.modules.parsers.pdf.docling_processor import DoclingProcessor
    from app.modules.parsers.pptx.pptx_parser import PPTXParser

    monkeypatch.delitem(sys.modules, "docling.document_converter", raising=False)
    for obj in (PPTXParser(), DoclingHtmlParser(), DoclingMarkdownParser(), DoclingProcessor(None, None)):  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="ensure_docling"):
            _ = obj.converter


def test_the_loop_check_passes_off_the_loop_and_once_docling_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "docling.document_converter", raising=False)
    lazy_heavy.require_off_loop()  # no running loop

    async def on_loop() -> None:
        monkeypatch.setitem(sys.modules, "docling.document_converter", object())  # type: ignore[arg-type]
        lazy_heavy.require_off_loop()

    asyncio.run(on_loop())
