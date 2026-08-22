"""Unit tests for vector DB health check integration."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vector_db.models import HealthStatus, VectorDBHealth


def _make_container(health_result: VectorDBHealth):
    container = MagicMock()
    container.logger = MagicMock(return_value=MagicMock())

    mock_service = AsyncMock()
    mock_service.health_check = AsyncMock(return_value=health_result)
    mock_service.get_service_name = MagicMock(return_value="mock")

    async def _get_service():
        return mock_service

    container.vector_db_service = _get_service
    return container


class TestVectorDBHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy_service_does_not_raise(self):
        from app.health.health import Health

        container = _make_container(
            VectorDBHealth(
                status=HealthStatus.HEALTHY,
                latency_ms=5.0,
                server_version="8.4.0",
                message="OK",
            )
        )
        # Should not raise
        await Health.health_check_vector_db(container)

    @pytest.mark.asyncio
    async def test_unhealthy_service_raises(self):
        from app.health.health import Health

        container = _make_container(
            VectorDBHealth(
                status=HealthStatus.UNHEALTHY,
                latency_ms=0.0,
                message="Connection refused",
            )
        )
        with pytest.raises(Exception, match="Connection refused"):
            await Health.health_check_vector_db(container)

    @pytest.mark.asyncio
    async def test_missing_vector_db_service_skips_check(self):
        from app.health.health import Health

        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        del container.vector_db_service  # simulate missing attribute

        # Should not raise — just skip
        await Health.health_check_vector_db(container)
