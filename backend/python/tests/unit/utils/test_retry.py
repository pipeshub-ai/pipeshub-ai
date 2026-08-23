import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.utils.retry import retry_async


class TestRetryAsync:
    @pytest.mark.asyncio
    async def test_succeeds_first_attempt_no_sleep(self):
        func = AsyncMock(return_value="ok")
        with patch("app.utils.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await retry_async(func, max_attempts=3)
        assert result == "ok"
        func.assert_awaited_once()
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_recovers_after_transient_failures(self):
        func = AsyncMock(side_effect=[ConnectionError("boom"), ConnectionError("boom"), "ok"])
        with patch("app.utils.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await retry_async(func, max_attempts=3, base_delay_seconds=0.1)
        assert result == "ok"
        assert func.await_count == 3
        assert mock_sleep.await_count == 2
        assert mock_sleep.await_args_list[0].args[0] == pytest.approx(0.1)
        assert mock_sleep.await_args_list[1].args[0] == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_raises_last_exception_after_exhausting_attempts(self):
        func = AsyncMock(side_effect=ConnectionError("still down"))
        with patch("app.utils.retry.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ConnectionError, match="still down"):
                await retry_async(func, max_attempts=3, base_delay_seconds=0.01)
        assert func.await_count == 3

    @pytest.mark.asyncio
    async def test_logs_each_failed_attempt(self):
        func = AsyncMock(side_effect=[ValueError("bad"), "ok"])
        logger = logging.getLogger("test-retry")
        with patch("app.utils.retry.asyncio.sleep", new_callable=AsyncMock), \
             patch.object(logger, "warning") as mock_warning:
            result = await retry_async(func, max_attempts=3, logger=logger, description="thing")
        assert result == "ok"
        mock_warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_non_positive_max_attempts(self):
        with pytest.raises(ValueError):
            await retry_async(AsyncMock(), max_attempts=0)
