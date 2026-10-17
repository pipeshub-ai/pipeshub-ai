"""resolve_web_search: the one provider lookup the chat and agent paths share."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.web.config import resolve_web_search


def _store(value: object = None, error: Exception | None = None) -> AsyncMock:
    store = AsyncMock()
    if error is not None:
        store.get_config.side_effect = error
    else:
        store.get_config.return_value = value
    return store


DDG = {"provider": "duckduckgo", "configuration": {}, "providers": []}


class TestDefaultProvider:
    @pytest.mark.parametrize(
        "stored",
        [
            {},
            None,
            {"providers": []},
            {"providers": None},
            {"providers": "garbage"},
        ],
    )
    async def test_falls_back_to_duckduckgo(self, stored: object) -> None:
        assert await resolve_web_search(_store(stored), MagicMock()) == DDG

    async def test_no_default_still_lists_the_stored_providers(self) -> None:
        stored = {"providers": [{"provider": "tavily", "isDefault": False, "configuration": {"apiKey": "t"}}]}
        assert await resolve_web_search(_store(stored), MagicMock()) == {
            "provider": "duckduckgo", "configuration": {}, "providers": [{"provider": "tavily", "configuration": {"apiKey": "t"}}],
        }

    async def test_uses_the_marked_default(self) -> None:
        stored = {"providers": [
            {"provider": "tavily", "isDefault": False},
            {"provider": "Serper ", "isDefault": True, "configuration": {"apiKey": "x"}},
        ]}
        assert await resolve_web_search(_store(stored), MagicMock()) == {
            "provider": "serper", "configuration": {"apiKey": "x"}, "providers": [{"provider": "tavily", "configuration": {}}],
        }

    async def test_unknown_default_is_passed_through_for_the_tool_to_report(self) -> None:
        stored = {"providers": [{"provider": "bing", "isDefault": True}]}
        assert await resolve_web_search(_store(stored), MagicMock()) == {"provider": "bing", "configuration": {}, "providers": []}

    async def test_non_dict_configuration_is_dropped(self) -> None:
        stored = {"providers": [{"provider": "tavily", "isDefault": True, "configuration": "bad"}]}
        assert await resolve_web_search(_store(stored), MagicMock()) == {"provider": "tavily", "configuration": {}, "providers": []}

    async def test_store_failure_disables_web_search(self) -> None:
        assert await resolve_web_search(_store(error=RuntimeError("down")), MagicMock()) is None

    async def test_cache_flag_is_passed_to_the_store(self) -> None:
        store = _store({})
        await resolve_web_search(store, MagicMock(), use_cache=True)
        assert store.get_config.call_args.kwargs["use_cache"] is True


class TestNamedProvider:
    async def test_uses_that_providers_configuration(self) -> None:
        stored = {"providers": [
            {"provider": "serper", "isDefault": True, "configuration": {"apiKey": "s"}},
            {"provider": "exa", "configuration": {"apiKey": "e"}},
        ]}
        result = await resolve_web_search(_store(stored), MagicMock(), provider=" EXA")
        assert result == {"provider": "exa", "configuration": {"apiKey": "e"}, "providers": [{"provider": "serper", "configuration": {"apiKey": "s"}}]}

    async def test_not_stored_gets_empty_configuration(self) -> None:
        stored = {"providers": [{"provider": "tavily", "configuration": {"key": "v"}}]}
        result = await resolve_web_search(_store(stored), MagicMock(), provider="serper")
        assert result == {"provider": "serper", "configuration": {}, "providers": [{"provider": "tavily", "configuration": {"key": "v"}}]}

    async def test_store_failure_keeps_the_provider(self) -> None:
        result = await resolve_web_search(_store(error=RuntimeError("down")), MagicMock(), provider="serper")
        assert result == {"provider": "serper", "configuration": {}, "providers": []}
