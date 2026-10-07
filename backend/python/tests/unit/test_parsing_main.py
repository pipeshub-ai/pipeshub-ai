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
        assert json.loads(result.body) == {
            "status": "healthy",
            "service": "parsing",
            "formats": ["pdf"],
            "resource_governor": {"error": "unavailable"},
        }
        mock_container.logger.return_value.warning.assert_called_once_with(
            "Resource governor stats failed: %s", error
        )
