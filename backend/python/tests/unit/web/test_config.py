"""resolve_web_search: the one provider lookup the chat and agent paths share."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.web.config import image_settings, resolve_web_search

# What a run gets when an admin has not turned "Send images to LLM" on.
NO_IMAGES = {"includeImages": False, "maxImages": 3}

def _store(value: object = None, error: Exception | None = None) -> AsyncMock:
    store = AsyncMock()
    if error is not None:
        store.get_config.side_effect = error
    else:
        store.get_config.return_value = value
    return store




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
    async def test_nothing_stored_means_web_search_is_not_set_up(self, stored: object) -> None:
        assert await resolve_web_search(_store(stored), MagicMock()) is None

    async def test_providers_without_a_default_are_not_a_setup(self) -> None:
        stored = {"providers": [{"provider": "tavily", "isDefault": False, "configuration": {"apiKey": "t"}}]}
        assert await resolve_web_search(_store(stored), MagicMock()) is None

    async def test_a_default_without_a_provider_name_is_not_a_setup(self) -> None:
        stored = {"providers": [{"provider": " ", "isDefault": True}]}
        assert await resolve_web_search(_store(stored), MagicMock()) is None

    async def test_a_stored_duckduckgo_default_is_used(self) -> None:
        stored = {"providers": [{"provider": "duckduckgo", "isDefault": True, "configuration": {}}]}
        assert await resolve_web_search(_store(stored), MagicMock()) == {
            "settings": NO_IMAGES, "provider": "duckduckgo", "configuration": {}, "providers": [],
        }

    async def test_uses_the_marked_default(self) -> None:
        stored = {"providers": [
            {"provider": "tavily", "isDefault": False},
            {"provider": "Serper ", "isDefault": True, "configuration": {"apiKey": "x"}},
        ]}
        assert await resolve_web_search(_store(stored), MagicMock()) == {
            "settings": NO_IMAGES, "provider": "serper", "configuration": {"apiKey": "x"}, "providers": [{"provider": "tavily", "configuration": {}}],
        }

    async def test_unknown_default_is_passed_through_for_the_tool_to_report(self) -> None:
        stored = {"providers": [{"provider": "bing", "isDefault": True}]}
        assert await resolve_web_search(_store(stored), MagicMock()) == {"settings": NO_IMAGES, "provider": "bing", "configuration": {}, "providers": []}

    async def test_non_dict_configuration_is_dropped(self) -> None:
        stored = {"providers": [{"provider": "tavily", "isDefault": True, "configuration": "bad"}]}
        assert await resolve_web_search(_store(stored), MagicMock()) == {"settings": NO_IMAGES, "provider": "tavily", "configuration": {}, "providers": []}

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
        assert result == {"settings": NO_IMAGES, "provider": "exa", "configuration": {"apiKey": "e"}, "providers": [{"provider": "serper", "configuration": {"apiKey": "s"}}]}

    async def test_not_stored_gets_empty_configuration(self) -> None:
        stored = {"providers": [{"provider": "tavily", "configuration": {"key": "v"}}]}
        result = await resolve_web_search(_store(stored), MagicMock(), provider="serper")
        assert result == {"settings": NO_IMAGES, "provider": "serper", "configuration": {}, "providers": [{"provider": "tavily", "configuration": {"key": "v"}}]}

    async def test_store_failure_keeps_the_provider(self) -> None:
        result = await resolve_web_search(_store(error=RuntimeError("down")), MagicMock(), provider="serper")
        assert result == {"settings": NO_IMAGES, "provider": "serper", "configuration": {}, "providers": []}


class TestImageSettings:
    """"Send images to LLM", read as the Node API stores it: off unless turned on, 3 per page."""

    async def test_an_admins_choice_reaches_the_run(self) -> None:
        stored = {
            "providers": [{"provider": "tavily", "isDefault": True, "configuration": {}}],
            "settings": {"includeImages": True, "maxImages": 7},
        }
        result = await resolve_web_search(_store(stored), MagicMock())
        assert result["settings"] == {"includeImages": True, "maxImages": 7}

    async def test_an_agents_own_provider_gets_the_org_setting_too(self) -> None:
        stored = {"providers": [], "settings": {"includeImages": True, "maxImages": 2}}
        result = await resolve_web_search(_store(stored), MagicMock(), provider="serper")
        assert result["settings"] == {"includeImages": True, "maxImages": 2}

    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            (None, NO_IMAGES),
            ("garbage", NO_IMAGES),
            ({}, NO_IMAGES),
            ({"includeImages": "yes", "maxImages": 5}, {"includeImages": False, "maxImages": 5}),
            ({"includeImages": True, "maxImages": "5"}, {"includeImages": True, "maxImages": 5}),
            ({"includeImages": True, "maxImages": 5.0}, {"includeImages": True, "maxImages": 5}),
            ({"includeImages": True, "maxImages": 50}, {"includeImages": True, "maxImages": 50}),
            # Stored up to 500 by the Node API; no more than 50 are read from one page.
            ({"includeImages": True, "maxImages": 51}, {"includeImages": True, "maxImages": 50}),
            ({"includeImages": True, "maxImages": 500}, {"includeImages": True, "maxImages": 50}),
            ({"includeImages": True, "maxImages": 501}, {"includeImages": True, "maxImages": 3}),
            ({"includeImages": True, "maxImages": 0}, {"includeImages": True, "maxImages": 3}),
            ({"includeImages": True, "maxImages": 2.5}, {"includeImages": True, "maxImages": 3}),
            ({"includeImages": True, "maxImages": True}, {"includeImages": True, "maxImages": 3}),
            ({"includeImages": True, "maxImages": "many"}, {"includeImages": True, "maxImages": 3}),
        ],
    )
    def test_a_missing_or_out_of_range_value_takes_its_default(self, stored: object, expected: dict) -> None:
        assert image_settings(stored) == expected
