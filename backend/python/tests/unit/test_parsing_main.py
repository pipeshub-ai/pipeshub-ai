"""Unit tests for app.parsing_main — Parsing service FastAPI entrypoint."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestHealthCheck:
    async def test_governor_stats_failure_is_not_echoed(self) -> None:
        """Still healthy; the exception goes to the log and the payload says only that stats are unavailable."""
        from app.parsing_main import app as parsing_main_app
        from app.parsing_main import health_check

        error = RuntimeError("SENTINEL /sys/fs/cgroup/memory.max")
        mock_governor = MagicMock()
        mock_governor.stats.side_effect = error
        mock_governor.fleet_ceilings.heavy = 6
        mock_registry = MagicMock()
        mock_registry.list_all_formats.return_value = {"pdf": ["default"]}

        with (
            patch.object(parsing_main_app.state, "parser_registry", mock_registry, create=True),
            patch.object(parsing_main_app.state, "governor", mock_governor, create=True),
            patch("app.parsing_main.container") as mock_container,
        ):
            result = await health_check()

        assert result.status_code == 200
        body = json.loads(result.body)
        assert {key: body[key] for key in ("status", "service", "formats", "resource_governor")} == {
            "status": "healthy",
            "service": "parsing",
            "formats": ["pdf"],
            "resource_governor": {"error": "unavailable"},
        }
        assert set(body) == {"status", "service", "formats", "resource_governor", "llm_gateway", "cpu", "lanes"}
        assert "SENTINEL" not in result.body.decode()
        mock_container.logger.return_value.warning.assert_called_once_with(
            "Resource governor stats failed: %s", error
        )

    async def test_lane_stats_run_off_the_event_loop(self) -> None:
        """Both can wait up to a second on the lane mutex; neither may hold the loop
        nor queue behind parse work on the default executor."""
        import threading

        from app.parsing_main import app as parsing_main_app
        from app.parsing_main import health_check

        seen: dict[str, str] = {}
        mock_registry = MagicMock()
        mock_registry.list_all_formats.return_value = {}
        governor = MagicMock()
        governor.stats.side_effect = lambda: seen.setdefault("stats", threading.current_thread().name) and {}

        def fake_lanes_health(*_args: object) -> dict[str, object]:
            seen["lanes"] = threading.current_thread().name
            return {}

        with (
            patch.object(parsing_main_app.state, "parser_registry", mock_registry, create=True),
            patch.object(parsing_main_app.state, "governor", governor, create=True),
            patch("app.parsing_main.lanes_health", side_effect=fake_lanes_health),
        ):
            await health_check()

        assert set(seen) == {"stats", "lanes"}
        assert all(name.startswith("health-stats") for name in seen.values())

    async def test_health_answers_while_parse_work_fills_the_default_executor(self) -> None:
        """Conversions and to_thread offloads can occupy every default-executor
        thread; the liveness probe must still answer."""
        import asyncio
        import threading
        from concurrent.futures import ThreadPoolExecutor

        from app.parsing_main import app as parsing_main_app
        from app.parsing_main import health_check

        mock_registry = MagicMock()
        mock_registry.list_all_formats.return_value = {}
        governor = MagicMock()
        governor.stats.return_value = {}
        release = threading.Event()
        loop = asyncio.get_running_loop()
        busy = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(busy)
        blocker = asyncio.to_thread(release.wait, 30)
        blocker_task = asyncio.ensure_future(blocker)
        try:
            with (
                patch.object(parsing_main_app.state, "parser_registry", mock_registry, create=True),
                patch.object(parsing_main_app.state, "governor", governor, create=True),
                patch("app.parsing_main.lanes_health", return_value={}),
            ):
                response = await asyncio.wait_for(health_check(), 5)
            assert response.status_code == 200
        finally:
            release.set()
            await blocker_task
            busy.shutdown(wait=True)

    async def test_health_reports_lanes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.parsing_main import app as parsing_main_app
        from app.parsing_main import health_check

        mock_registry = MagicMock()
        mock_registry.list_all_formats.return_value = {}
        governor = MagicMock()
        governor.fleet_ceilings.heavy = 6
        governor.stats.return_value = {}
        # An explicit thread count in the environment would make the plan "explicit_threads".
        monkeypatch.delenv("PDF_TABLE_MODEL_THREADS", raising=False)
        with (
            patch.object(parsing_main_app.state, "parser_registry", mock_registry, create=True),
            patch.object(parsing_main_app.state, "governor", governor, create=True),
            patch("app.parsing_main.effective_cpu_count", return_value=8),
        ):
            body = json.loads((await health_check()).body)

        assert set(body["lanes"]) == {"mode", "plan", "in_use_domain"}
        assert body["lanes"]["mode"] == "off"
        assert body["lanes"]["in_use_domain"] is None
        assert body["lanes"]["plan"] == {
            "cpus": 8, "heavy": 6, "lanes": 3, "threads_per_lane": 2,
            "cpus_per_lane": 2, "source": "rule",
        }

    async def test_lanes_block_without_governor(self) -> None:
        from app.parsing_main import app as parsing_main_app
        from app.parsing_main import health_check

        mock_registry = MagicMock()
        mock_registry.list_all_formats.return_value = {}
        with (
            patch.object(parsing_main_app.state, "parser_registry", mock_registry, create=True),
            patch.object(parsing_main_app.state, "governor", None, create=True),
        ):
            body = json.loads((await health_check()).body)

        assert body["lanes"] == {"mode": "off", "plan": None, "in_use_domain": None}

    async def test_reports_cpu_source_and_pool_sizes(self) -> None:
        from app.parsing_main import app as parsing_main_app
        from app.parsing_main import health_check

        mock_registry = MagicMock()
        mock_registry.list_all_formats.return_value = {}
        with (
            patch.object(parsing_main_app.state, "parser_registry", mock_registry, create=True),
            patch.object(parsing_main_app.state, "governor", None, create=True),
            patch("app.parsing_main.effective_cpu_count", return_value=3),
            patch("app.parsing_main.effective_cpu_source", return_value="cgroup_v2"),
            patch("app.parsing_main.pdf_raster_pool_size", return_value=2),
        ):
            body = json.loads((await health_check()).body)

        assert body["cpu"] == {"effective_cpus": 3, "source": "cgroup_v2", "pdf_raster_workers": 2}

    async def test_reports_scaled_rasterizer_pool_size(self) -> None:
        from app.modules.parsers.pdf import pdf_rasterizer
        from app.parsing_main import app as parsing_main_app
        from app.parsing_main import health_check
        from app.utils import worker_scaling

        mock_registry = MagicMock()
        mock_registry.list_all_formats.return_value = {}
        with (
            patch.object(parsing_main_app.state, "parser_registry", mock_registry, create=True),
            patch.object(parsing_main_app.state, "governor", None, create=True),
            patch.object(pdf_rasterizer, "PDF_RASTER_WORKERS", 6),
            patch.dict(worker_scaling._state, {"workers": 3}),
        ):
            body = json.loads((await health_check()).body)

        assert body["cpu"]["pdf_raster_workers"] == 2


class TestPdfParserWiring:
    """The default PDF parser sends long text PDFs to PdfPlumber unless PdfPlumber takes every PDF."""

    @staticmethod
    def _default_pdf_parser(monkeypatch, flag: str | None) -> tuple[object, type, type]:
        from unittest.mock import MagicMock

        from app.parsing_main import _build_registry
        from app.services.parsing.interface import ParserProvider
        from app.services.parsing.providers.docling_service_parser import (
            DoclingServiceParser,
        )
        from app.services.parsing.providers.pdfplumber_parser import PdfPlumberParser

        if flag is None:
            monkeypatch.delenv("ENABLE_PDFPLUMBER_PROCESSOR", raising=False)
        else:
            monkeypatch.setenv("ENABLE_PDFPLUMBER_PROCESSOR", flag)
        registry = _build_registry(MagicMock(), MagicMock())
        parser = registry.resolve("application/pdf", "pdf", ParserProvider.DEFAULT)
        return parser, DoclingServiceParser, PdfPlumberParser

    def test_docling_first_with_pdfplumber_for_long_pdfs(self, monkeypatch) -> None:
        parser, docling_cls, plumber_cls = self._default_pdf_parser(monkeypatch, None)

        assert isinstance(parser._primary, docling_cls)
        assert isinstance(parser._large, plumber_cls)

    def test_the_opt_in_flag_still_sends_every_pdf_to_pdfplumber(self, monkeypatch) -> None:
        parser, _, plumber_cls = self._default_pdf_parser(monkeypatch, "true")

        assert isinstance(parser._primary, plumber_cls)
        assert parser._large is None


class TestLifespan:
    async def test_shutdown_clears_parse_pool_governor_and_pool(self) -> None:
        """A governor left set makes the next parse in this process start a new pool."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.modules.parsers import parse_pool
        from app.modules.parsers.pdf import docling_processor, pdf_rasterizer
        from app.parsing_main import lifespan

        container = MagicMock()
        governor = MagicMock()
        governor.run = AsyncMock()
        governor.ceilings = SimpleNamespace(heavy=1, light=1)
        app = MagicMock()
        pool = MagicMock()

        # The lifespan also hands the governor to these two; a later test expects them unset.
        saved = (
            parse_pool._State.governor,
            parse_pool._State.pool,
            docling_processor._resource_governor,
            pdf_rasterizer._resource_governor,
        )
        try:
            with (
                patch("app.parsing_main._get_initialized_container", new=AsyncMock(return_value=container)),
                patch("app.parsing_main._build_registry", return_value=MagicMock()),
                patch("app.parsing_main.is_local_cpu_embedding_configured", new=AsyncMock(return_value=False)),
                patch("app.parsing_main.ResourceGovernor", return_value=governor),
            ):
                async with lifespan(app):
                    assert parse_pool._State.governor is governor
                    parse_pool._State.pool = pool  # a pool started by a parse
            assert parse_pool._State.governor is None
            assert parse_pool._State.pool is None
            pool.shutdown.assert_called_once()
        finally:
            (
                parse_pool._State.governor,
                parse_pool._State.pool,
                docling_processor._resource_governor,
                pdf_rasterizer._resource_governor,
            ) = saved

    async def test_shutdown_order(self) -> None:
        """The pool stops before the governor closes the lane budget its workers lease from (T51)."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.modules.parsers import parse_pool
        from app.modules.parsers.pdf import docling_processor, pdf_rasterizer
        from app.parsing_main import lifespan

        order: list[str] = []
        governor = MagicMock()
        governor.run = AsyncMock()
        governor.ceilings = SimpleNamespace(heavy=1, light=1)
        governor.stop.side_effect = lambda: order.append("governor.stop")
        governor.close.side_effect = lambda: order.append("governor.close")
        pool = MagicMock()
        pool.shutdown.side_effect = lambda: order.append("pool.shutdown")
        saved = (
            parse_pool._State.governor,
            parse_pool._State.pool,
            docling_processor._resource_governor,
            pdf_rasterizer._resource_governor,
        )
        try:
            with (
                patch("app.parsing_main._get_initialized_container", new=AsyncMock(return_value=MagicMock())),
                patch("app.parsing_main._build_registry", return_value=MagicMock()),
                patch("app.parsing_main.is_local_cpu_embedding_configured", new=AsyncMock(return_value=False)),
                patch("app.parsing_main.ResourceGovernor", return_value=governor),
            ):
                async with lifespan(MagicMock()):
                    parse_pool._State.pool = pool
            assert order.index("pool.shutdown") < order.index("governor.close")
            assert order.index("pool.shutdown") < order.index("governor.stop")
        finally:
            (
                parse_pool._State.governor,
                parse_pool._State.pool,
                docling_processor._resource_governor,
                pdf_rasterizer._resource_governor,
            ) = saved

    def test_shutdown_order_in_indexing_main(self) -> None:
        """Same rule in the indexing service, read from its source (its lifespan needs the whole app)."""
        import ast
        from pathlib import Path

        tree = ast.parse((Path(__file__).parents[2] / "app" / "indexing_main.py").read_text())
        lifespan = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
        lines = {
            ast.unparse(n.func): n.lineno
            for n in ast.walk(lifespan)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert lines["parse_pool.shutdown_parse_pool"] < lines["governor.stop"] < lines["governor.close"]
