"""Unit tests for app.parsing_main — Parsing service FastAPI entrypoint."""

import json
from unittest.mock import MagicMock, patch


class TestHealthCheck:
    async def test_governor_stats_failure_is_not_echoed(self) -> None:
        """Still healthy; the exception goes to the log and the payload says only that stats are unavailable."""
        from app.parsing_main import app as parsing_main_app
        from app.parsing_main import health_check

        error = RuntimeError("SENTINEL /sys/fs/cgroup/memory.max")
        mock_governor = MagicMock()
        mock_governor.stats.side_effect = error
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
        assert set(body) == {"status", "service", "formats", "resource_governor", "llm_gateway", "cpu"}
        assert "SENTINEL" not in result.body.decode()
        mock_container.logger.return_value.warning.assert_called_once_with(
            "Resource governor stats failed: %s", error
        )


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
