"""The parse worker pool: real worker processes, driven the way the parsers drive them.

Jobs are stdlib and app functions on purpose. A worker imports a job's function
by name, and this test module is not importable from a fresh interpreter.
"""
from __future__ import annotations

import asyncio
import io
import os
import resource
import signal
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.parsers import parse_pool
from app.modules.parsers.code_parser.code_file_parser import (
    CodeFileParser,
    parse_code_to_blocks,
)
from app.modules.parsers.csv.csv_parser import CSVParser
from app.modules.parsers.csv.table_detection import read_csv_tables
from app.modules.parsers.markdown.image_references import extract_and_replace_images
from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser
from app.modules.parsers.markdown.markdown_to_blocks import (
    MarkdownToBlocksConverter,
    convert_markdown_to_blocks,
)
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.parsing.client import ParsingClientError
from app.services.parsing.interface import ParseErrorCode
from app.utils.cpu_offload import DEFAULT_OFFLOAD_THRESHOLD_BYTES
from app.utils.user_errors import (
    PARSE_WORKER_OUT_OF_MEMORY,
    PROCESSING_TIMED_OUT,
    to_user_reason,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models.blocks import BlocksContainer

LARGE = DEFAULT_OFFLOAD_THRESHOLD_BYTES


def _governor(heavy_ceiling: int = 4) -> MagicMock:
    governor = MagicMock()
    governor.ceilings = SimpleNamespace(heavy=heavy_ceiling)
    return governor


@pytest.fixture
def governor(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """A pool of one worker, torn down after the test."""
    monkeypatch.setenv(parse_pool.WORKERS_ENV, "1")
    fake = _governor()
    parse_pool.set_resource_governor(fake)
    try:
        yield fake
    finally:
        parse_pool.shutdown_parse_pool()
        parse_pool.set_resource_governor(None)


def _shape(container: BlocksContainer) -> dict:
    """Everything but the block ids, which are random per conversion."""
    ids = {"__all__": {"id"}}
    return container.model_dump(mode="json", exclude={"blocks": ids, "block_groups": ids})


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _wait_until_gone(pid: int, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _process_exists(pid):
            return True
        await asyncio.sleep(0.05)
    return False


# -- sizing: the governor decides, the setting can only lower it -------------


class TestWorkerCount:
    def test_no_governor_means_no_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(parse_pool.WORKERS_ENV, raising=False)
        parse_pool.set_resource_governor(None)

        assert parse_pool.parse_pool_workers() == 0
        assert parse_pool.should_offload(10 * LARGE) is False

    @pytest.mark.parametrize(
        ("heavy_ceiling", "expected"),
        [(1, 1), (2, 1), (3, 1), (6, 3), (8, 4), (14, 4), (64, 4)],
    )
    def test_default_is_half_the_cpu_bound_parse_ceiling_up_to_four(
        self, monkeypatch: pytest.MonkeyPatch, heavy_ceiling: int, expected: int
    ) -> None:
        monkeypatch.delenv(parse_pool.WORKERS_ENV, raising=False)
        parse_pool.set_resource_governor(_governor(heavy_ceiling))
        try:
            assert parse_pool.parse_pool_workers() == expected
        finally:
            parse_pool.set_resource_governor(None)

    @pytest.mark.parametrize(
        ("configured", "heavy_ceiling", "expected"),
        [("2", 6, 2), ("6", 6, 6), ("32", 6, 6), ("0", 6, 0), ("", 6, 3), ("many", 6, 3)],
    )
    def test_the_setting_is_held_to_what_the_governor_allows(
        self, monkeypatch: pytest.MonkeyPatch, configured: str, heavy_ceiling: int, expected: int
    ) -> None:
        monkeypatch.setenv(parse_pool.WORKERS_ENV, configured)
        parse_pool.set_resource_governor(_governor(heavy_ceiling))
        try:
            assert parse_pool.parse_pool_workers() == expected
        finally:
            parse_pool.set_resource_governor(None)

    def test_only_large_payloads_are_worth_a_worker(self, governor: MagicMock) -> None:
        assert parse_pool.should_offload(LARGE) is True
        assert parse_pool.should_offload(LARGE - 1) is False

    async def test_without_a_pool_the_job_still_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(parse_pool.WORKERS_ENV, "0")
        parse_pool.set_resource_governor(_governor())
        try:
            assert await parse_pool.submit(len, b"abc") == len(b"abc")
        finally:
            parse_pool.set_resource_governor(None)


# -- jobs run in another process and come back whole ---------------------------


class TestJobs:
    async def test_a_job_runs_in_a_worker_process(self, governor: MagicMock) -> None:
        worker_pid = await parse_pool.submit(os.getpid)

        assert worker_pid != os.getpid()
        assert _process_exists(worker_pid)

    async def test_markdown_blocks_cross_the_process_boundary_unchanged(
        self, governor: MagicMock
    ) -> None:
        markdown = "# Title\n\nA paragraph with **bold** text.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"

        from_worker = await parse_pool.submit(convert_markdown_to_blocks, markdown, None, 3)

        assert _shape(from_worker) == _shape(
            MarkdownToBlocksConverter().convert(markdown, page_number=3)
        )
        assert from_worker.blocks

    async def test_code_blocks_cross_the_process_boundary_unchanged(
        self, governor: MagicMock
    ) -> None:
        source = b"class A:\n    def run(self) -> int:\n        return 1\n\n\ndef helper():\n    return 2\n"

        from_worker = await parse_pool.submit(
            parse_code_to_blocks, source, "a.py", "src/a.py", "python"
        )

        assert _shape(from_worker) == _shape(
            CodeFileParser().parse_to_blocks(source, "a.py", "src/a.py", "python")
        )
        assert from_worker.blocks

    async def test_csv_tables_cross_the_process_boundary_unchanged(
        self, governor: MagicMock
    ) -> None:
        content = b"id,name\n1,Ada\n2,Grace\n\n\nx;y\n"
        parser = CSVParser(config_service=MagicMock())

        from_worker = await parse_pool.submit(read_csv_tables, content, ",", '"')

        rows = parser.read_raw_rows(io.StringIO(content.decode()))
        assert from_worker == parser.find_tables_in_csv(rows)
        assert len(from_worker) == 2

    async def test_image_references_are_found_in_a_worker(self, governor: MagicMock) -> None:
        markdown = "Intro\n\n![chart](https://example.com/chart.png)\n"

        assert await parse_pool.submit(extract_and_replace_images, markdown) == (
            extract_and_replace_images(markdown)
        )

    async def test_an_error_in_the_job_reaches_the_caller_as_itself(
        self, governor: MagicMock
    ) -> None:
        with pytest.raises(ValueError, match="invalid literal") as raised:
            await parse_pool.submit(int, "not a number")

        assert any("parse worker" in note for note in raised.value.__notes__)
        # An ordinary failure is not a crash: the same worker takes the next job.
        first = await parse_pool.submit(os.getpid)
        assert await parse_pool.submit(os.getpid) == first

    async def test_a_worker_starts_in_a_process_with_many_open_files(
        self, governor: MagicMock
    ) -> None:
        """The indexing service holds thousands of sockets, so a worker's pipes
        get descriptor numbers past the 1024 that ``select`` can wait on."""
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        wanted = 2048
        if soft < wanted:
            if hard != resource.RLIM_INFINITY and hard < wanted:
                pytest.skip("this runner cannot open more than 1024 files")
            resource.setrlimit(resource.RLIMIT_NOFILE, (wanted, hard))
        held = [os.open(os.devnull, os.O_RDONLY) for _ in range(1100)]
        try:
            assert max(held) >= 1024
            assert await parse_pool.submit(len, b"abc") == len(b"abc")
        finally:
            for fd in held:
                os.close(fd)
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))

    async def test_jobs_wait_their_turn_on_a_single_worker(self, governor: MagicMock) -> None:
        results = await asyncio.gather(*(parse_pool.submit(len, b"x" * n) for n in range(8)))

        assert results == list(range(8))


# -- a parser that takes the worker path gives the same answer -----------------


class TestParsersUseThePool:
    async def test_large_markdown_is_parsed_in_a_worker(self, governor: MagicMock) -> None:
        markdown = "# Heading\n\n" + ("A line of ordinary prose.\n\n" * 12_000)
        assert len(markdown) >= LARGE
        parser = MarkdownItParser()

        container = await parser.parse_to_blocks(markdown, name="notes.md")

        assert _shape(container) == _shape(MarkdownToBlocksConverter().convert(markdown))

    async def test_large_source_is_parsed_in_a_worker(self, governor: MagicMock) -> None:
        source = b"def f():\n    return 1\n\n\n" * 14_000
        assert len(source) >= LARGE
        parser = CodeFileParser()

        container = await parser.parse_to_blocks_off_loop(source, "f.py", "src/f.py", "python")

        assert _shape(container) == _shape(
            parser.parse_to_blocks(source, "f.py", "src/f.py", "python")
        )

    async def test_an_unreadable_large_csv_reads_as_empty(self, governor: MagicMock) -> None:
        parser = CSVParser(config_service=MagicMock())
        content = b'a,b\n"' + b"x" * LARGE  # one unterminated field past csv's size limit

        assert await parser.read_tables_in_worker(content, "broken.csv") is None

    async def test_a_large_csv_is_read_in_a_worker(self, governor: MagicMock) -> None:
        parser = CSVParser(config_service=MagicMock())
        content = b"id,name\n" + b"".join(b"%d,customer %d\n" % (i, i) for i in range(20_000))
        assert len(content) >= LARGE

        tables = await parser.read_tables_in_worker(content, "customers.csv")

        assert len(tables) == 1
        assert len(tables[0]["raw_rows"]) == 20_001


# -- one bad file costs one record ---------------------------------------------


class TestWorkerCrash:
    async def test_a_dying_worker_fails_one_record_and_the_next_one_parses(
        self, governor: MagicMock
    ) -> None:
        crashed_pid = await parse_pool.submit(os.getpid)

        with pytest.raises(parse_pool.ParseWorkerError) as raised:
            await parse_pool.submit(os._exit, 137, label="huge.csv", size=5_000_000)

        assert raised.value.cause == "crashed"
        assert str(raised.value) == PARSE_WORKER_OUT_OF_MEMORY
        assert await _wait_until_gone(crashed_pid)

        # The next record gets a fresh worker and parses.
        container = await parse_pool.submit(convert_markdown_to_blocks, "# Still working\n")
        assert container.blocks[0].data == "Still working"
        assert await parse_pool.submit(os.getpid) != crashed_pid

    async def test_a_crash_is_reported_to_the_governor_as_a_memory_incident(
        self, governor: MagicMock
    ) -> None:
        with pytest.raises(parse_pool.ParseWorkerError):
            await parse_pool.submit(os._exit, 137)

        governor.report_memory_incident.assert_called_once()

    async def test_only_the_job_that_was_running_fails(self, governor: MagicMock) -> None:
        """Jobs queued behind a crash never ran in the dead worker, so they are
        not failed with it the way a shared process pool fails everything."""
        outcomes = await asyncio.gather(
            parse_pool.submit(len, b"before"),
            parse_pool.submit(os._exit, 137),
            parse_pool.submit(len, b"after one"),
            parse_pool.submit(len, b"after two"),
            return_exceptions=True,
        )

        assert outcomes[0] == len(b"before")
        assert isinstance(outcomes[1], parse_pool.ParseWorkerError)
        assert outcomes[2:] == [len(b"after one"), len(b"after two")]

    async def test_a_worker_killed_while_idle_costs_no_record(self, governor: MagicMock) -> None:
        idle_pid = await parse_pool.submit(os.getpid)
        os.kill(idle_pid, signal.SIGKILL)
        await asyncio.sleep(0.2)

        assert await parse_pool.submit(os.getpid) != idle_pid
        governor.report_memory_incident.assert_not_called()

    async def test_the_crash_reason_is_what_people_are_shown(self) -> None:
        crash = parse_pool.ParseWorkerError(PARSE_WORKER_OUT_OF_MEMORY, "crashed")

        # In-process: the processor wraps whatever the parser raised.
        try:
            raise DocumentProcessingError("Failed to process document") from crash
        except DocumentProcessingError as wrapped:
            assert to_user_reason(wrapped) == PARSE_WORKER_OUT_OF_MEMORY
            assert MessageErrorClassifier.classify_by_exception(wrapped) == MessageErrorType.TERMINAL

        # Through the parsing service: the same message arrives in its error body.
        from_service = ParsingClientError(ParseErrorCode.PARSE_FAILED, PARSE_WORKER_OUT_OF_MEMORY)
        assert to_user_reason(from_service) == PARSE_WORKER_OUT_OF_MEMORY


# -- a job nobody is waiting for does not hold a worker ------------------------


class TestAbandonedJobs:
    async def test_a_parse_that_overruns_is_stopped_and_the_next_one_runs(
        self, governor: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RECORD_PROCESSING_TIMEOUT", "0.5")
        stuck_pid = await parse_pool.submit(os.getpid)

        with pytest.raises(parse_pool.ParseWorkerError) as raised:
            await parse_pool.submit(time.sleep, 600, label="endless.sql")

        assert raised.value.cause == "timed_out"
        assert str(raised.value) == PROCESSING_TIMED_OUT
        assert await _wait_until_gone(stuck_pid)
        monkeypatch.setenv("RECORD_PROCESSING_TIMEOUT", "1800")
        assert await parse_pool.submit(len, b"abc") == len(b"abc")

    async def test_cancelling_the_caller_stops_its_running_parse(self, governor: MagicMock) -> None:
        busy_pid = await parse_pool.submit(os.getpid)
        task = asyncio.create_task(parse_pool.submit(time.sleep, 600))
        await asyncio.sleep(0.5)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The worker that was sleeping is killed, so the next file is not stuck behind it.
        assert await _wait_until_gone(busy_pid)
        assert await parse_pool.submit(len, b"abc") == len(b"abc")

    async def test_cancelling_a_queued_parse_never_starts_it(self, governor: MagicMock) -> None:
        running = asyncio.create_task(parse_pool.submit(time.sleep, 1.0))
        await asyncio.sleep(0.2)
        queued = asyncio.create_task(parse_pool.submit(os._exit, 137))
        await asyncio.sleep(0)

        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued

        await running
        # Had the cancelled job run, it would have killed the worker.
        governor.report_memory_incident.assert_not_called()


# -- a worker holds the service's secrets no more loosely than the service ------


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="prctl is Linux-only")
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root bypasses the dumpable check"
)
async def test_a_workers_environment_cannot_be_read_by_another_process_of_the_same_user(
    governor: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker is started with the service's environment: database passwords,
    the JWT secret. The service marks itself non-dumpable so that tools it runs
    under the same uid cannot read /proc/<pid>/environ, and exec resets that
    mark, so each worker has to set it again for itself."""
    secret = f"s3cr3t-{uuid.uuid4().hex}"
    monkeypatch.setenv("PIPESHUB_FAKE_SECRET_KEY", secret)
    worker_pid = await parse_pool.submit(os.getpid)

    # The worker really was handed the secret; it is only others who cannot read it.
    assert await parse_pool.submit(os.getenv, "PIPESHUB_FAKE_SECRET_KEY") == secret
    sibling = subprocess.run(
        ["/bin/sh", "-c", f"tr '\\0' '\\n' < /proc/{worker_pid}/environ"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert secret not in sibling.stdout
    assert sibling.returncode != 0


# -- lifecycle -----------------------------------------------------------------


class TestLifecycle:
    async def test_shutdown_stops_the_workers_and_a_later_job_starts_new_ones(
        self, governor: MagicMock
    ) -> None:
        first_pid = await parse_pool.submit(os.getpid)

        assert parse_pool.shutdown_parse_pool() is True
        assert await _wait_until_gone(first_pid)
        assert parse_pool.shutdown_parse_pool() is False

        assert await parse_pool.submit(os.getpid) != first_pid

    async def test_shutdown_stops_a_parse_that_is_still_running(self, governor: MagicMock) -> None:
        busy_pid = await parse_pool.submit(os.getpid)
        task = asyncio.create_task(parse_pool.submit(time.sleep, 600))
        await asyncio.sleep(0.5)

        parse_pool.shutdown_parse_pool()

        # Retried rather than failed: a shutdown says nothing about the file.
        with pytest.raises(parse_pool.ParseAbandonedError) as raised:
            await task
        assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TRANSIENT
        assert await _wait_until_gone(busy_pid)

    async def test_an_idle_worker_exits_and_the_next_job_gets_a_new_one(
        self, governor: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(parse_pool, "IDLE_SECONDS", 0.3)
        idle_pid = await parse_pool.submit(os.getpid)

        assert await _wait_until_gone(idle_pid)
        assert await parse_pool.submit(os.getpid) != idle_pid


@pytest.mark.parametrize(
    "module",
    [
        "app.modules.parsers.parse_worker",
        "app.modules.parsers.markdown.markdown_to_blocks",
        "app.modules.parsers.markdown.image_references",
        "app.modules.parsers.code_parser.code_file_parser",
        "app.modules.parsers.csv.table_detection",
    ],
)
def test_what_a_worker_imports_stays_light(module: str) -> None:
    """A worker imports only the module of the function it is sent. One of those
    pulling in the LLM or Docling stack turns a 50 MB worker into a 700 MB one."""
    probe = (
        f"import sys; import {module}; "
        "print([m for m in ('langchain_core', 'transformers', 'torch', 'docling') if m in sys.modules])"
    )

    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, check=True, timeout=25, cwd=parse_pool._PYTHON_ROOT,
    )

    assert result.stdout.strip() == "[]"


# -- the point of it all -------------------------------------------------------


async def test_the_event_loop_keeps_ticking_while_a_worker_parses_a_large_file(
    governor: MagicMock,
) -> None:
    """Ticks are counted, not timed, so a slow runner cannot make this flaky:
    a loop held by the parse gives zero ticks, a free one gives dozens."""
    source = b"class A:\n    def run(self, n: int) -> int:\n        return n + 1\n\n\n" * 6_000
    assert len(source) >= LARGE
    parser = CodeFileParser()
    ticks = 0
    running = True

    async def heartbeat() -> None:
        nonlocal ticks
        while running:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    try:
        container = await parser.parse_to_blocks_off_loop(source, "a.py", "src/a.py", "python")
    finally:
        running = False
        await beat

    assert len(container.block_groups) == 6_000
    assert ticks >= 3
