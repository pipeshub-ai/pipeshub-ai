import asyncio
import time
from typing import Any, Callable, Coroutine, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import httpx
import pytest


class BaseTest:
    """Base test utilities following clear, typed, single-responsibility helpers.

    Design Principles:
    - No hidden state: helpers accept explicit `httpx.AsyncClient` or values
    - Strong typing and explicit returns
    - Small, composable helpers for requests, retries and assertions
    - Consistent defaults and overridable timeouts
    """

    DEFAULT_TIMEOUT: float = 10.0
    RETRY_ATTEMPTS: int = 2
    RETRY_DELAY_SECONDS: float = 0.5

    # -----------------------------
    # HTTP request helpers
    # -----------------------------
    async def request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> httpx.Response:
        method_upper = method.upper()
        request_timeout = timeout or self.DEFAULT_TIMEOUT
        if method_upper == "GET":
            return await client.get(url, params=params, headers=headers, timeout=request_timeout)
        if method_upper == "POST":
            return await client.post(url, params=params, json=json, data=data, headers=headers, timeout=request_timeout)
        if method_upper == "PUT":
            return await client.put(url, params=params, json=json, data=data, headers=headers, timeout=request_timeout)
        if method_upper == "DELETE":
            return await client.delete(url, params=params, headers=headers, timeout=request_timeout)
        raise ValueError(f"Unsupported method: {method}")

    async def get(self, client: httpx.AsyncClient, url: str, *, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None) -> httpx.Response:
        return await self.request(client, "GET", url, params=params, headers=headers, timeout=timeout)

    async def post(self, client: httpx.AsyncClient, url: str, *, json: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None) -> httpx.Response:
        return await self.request(client, "POST", url, json=json, data=data, headers=headers, timeout=timeout)

    async def put(self, client: httpx.AsyncClient, url: str, *, json: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None) -> httpx.Response:
        return await self.request(client, "PUT", url, json=json, data=data, headers=headers, timeout=timeout)

    async def delete(self, client: httpx.AsyncClient, url: str, *, headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None) -> httpx.Response:
        return await self.request(client, "DELETE", url, headers=headers, timeout=timeout)

    # -----------------------------
    # JSON convenience helpers
    # -----------------------------
    async def get_json(self, client: httpx.AsyncClient, url: str, *, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None, expect_status: Optional[int] = None) -> Tuple[httpx.Response, Any]:
        response = await self.get(client, url, params=params, timeout=timeout)
        if expect_status is not None:
            self.assert_status(response, expect_status)
        return response, self._safe_json(response)

    async def post_json(self, client: httpx.AsyncClient, url: str, *, json: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None, expect_status: Optional[int] = None) -> Tuple[httpx.Response, Any]:
        response = await self.post(client, url, json=json, timeout=timeout)
        if expect_status is not None:
            self.assert_status(response, expect_status)
        return response, self._safe_json(response)

    def _safe_json(self, response: httpx.Response) -> Any:
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return None

    # -----------------------------
    # Retry helper
    # -----------------------------
    async def retry(
        self,
        func: Callable[[], Coroutine[Any, Any, Any]],
        *,
        attempts: Optional[int] = None,
        delay_seconds: Optional[float] = None,
    ) -> Any:
        total_attempts = attempts or self.RETRY_ATTEMPTS
        delay = delay_seconds or self.RETRY_DELAY_SECONDS
        last_err: Optional[BaseException] = None
        for _ in range(max(1, total_attempts)):
            try:
                return await func()
            except Exception as e:  # pragma: no cover - utility wrapper
                last_err = e
                await asyncio.sleep(delay)
        if last_err:
            raise last_err
        raise RuntimeError("retry: exhausted attempts without result and without exception")

    # -----------------------------
    # Assertion helpers
    # -----------------------------
    def assert_status(self, response: httpx.Response, expected_status: int) -> None:
        assert response.status_code == expected_status, f"Expected status {expected_status}, got {response.status_code}"

    def assert_content_type_json(self, response: httpx.Response) -> None:
        content_type = response.headers.get("content-type", "")
        assert "application/json" in content_type, f"Expected JSON content-type, got '{content_type}'"

    def assert_json_has_keys(self, data: Mapping[str, Any], required_keys: Sequence[str]) -> None:
        for key in required_keys:
            assert key in data, f"Missing key in response JSON: {key}"

    def assert_response_time_under(self, start_time: float, end_time: float, max_seconds: float) -> None:
        elapsed = end_time - start_time
        assert elapsed <= max_seconds, f"Response time {elapsed:.2f}s exceeds maximum {max_seconds:.2f}s"

    # Note: Keep BaseTest self-contained to avoid import-time issues in non-package test trees

    # -----------------------------
    # Control helpers
    # -----------------------------
    def skip_if(self, condition: bool, reason: str) -> None:
        if condition:
            pytest.skip(reason)


# Backwards compatibility alias
BaseTestCase = BaseTest
