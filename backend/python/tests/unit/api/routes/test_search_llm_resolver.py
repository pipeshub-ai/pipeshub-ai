"""Tests for app.api.routes.search_llm_resolver — LLM resolution for search routes."""

from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# resolve_llm_for_search
# ---------------------------------------------------------------------------


class TestResolveLlmForSearch:
    """Tests for the async LLM resolution helper."""

    async def test_llm_already_set(self) -> None:
        """When retrieval_service.llm is already populated, return it directly."""
        from app.api.routes.search_llm_resolver import resolve_llm_for_search

        request = MagicMock()
        retrieval_service = MagicMock()
        retrieval_service.llm = MagicMock(name="existing-llm")

        result = await resolve_llm_for_search(request, retrieval_service)
        assert result is retrieval_service.llm

    async def test_llm_fetched_via_get_llm_instance(self) -> None:
        """When llm is None, fetch via get_llm_instance and return it."""
        from app.api.routes.search_llm_resolver import resolve_llm_for_search

        request = MagicMock()
        fetched_llm = MagicMock(name="fetched-llm")
        retrieval_service = MagicMock()
        retrieval_service.llm = None
        retrieval_service.get_llm_instance = AsyncMock(return_value=fetched_llm)

        result = await resolve_llm_for_search(request, retrieval_service)
        assert result is fetched_llm
        retrieval_service.get_llm_instance.assert_awaited_once()

    async def test_no_llm_configured_returns_none(self) -> None:
        """No chat model configured: return None so search runs without a rewrite."""
        from app.api.routes.search_llm_resolver import resolve_llm_for_search

        request = MagicMock()
        retrieval_service = MagicMock()
        retrieval_service.llm = None
        retrieval_service.get_llm_instance = AsyncMock(return_value=None)

        assert await resolve_llm_for_search(request, retrieval_service) is None
