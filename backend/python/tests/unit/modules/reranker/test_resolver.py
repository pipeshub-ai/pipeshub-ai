"""``RerankerResolver``: the Labs flag and the admin's model choice, read per request."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from app.modules.reranker.resolver import RerankerResolver

if TYPE_CHECKING:
    from collections.abc import Iterator

_MODULE = "app.modules.reranker.resolver"
_COHERE = {"provider": "cohere", "configuration": {"model": "rerank-v3.5", "apiKey": "k"}}


@pytest.fixture
def world() -> Iterator[dict[str, Any]]:
    """The flag, the stored config and the factory, each settable per test."""
    state = {"enabled": True, "config": _COHERE}

    async def enabled(_config_service: object) -> bool:
        return state["enabled"]

    async def config(_config_service: object) -> dict | None:
        return state["config"]

    factory = MagicMock(side_effect=lambda cfg: MagicMock(name=cfg["provider"]))
    with (
        patch(f"{_MODULE}.is_reranker_enabled", side_effect=enabled),
        patch(f"{_MODULE}.get_reranker_config", side_effect=config) as read_config,
        patch(f"{_MODULE}.create_reranker", factory),
    ):
        state["factory"] = factory
        state["read_config"] = read_config
        yield state


async def test_flag_off_means_no_reranker_and_no_config_read(world) -> None:
    world["enabled"] = False

    assert await RerankerResolver(MagicMock()).active() is None
    world["read_config"].assert_not_called()
    world["factory"].assert_not_called()


async def test_builds_the_configured_reranker_once(world) -> None:
    resolver = RerankerResolver(MagicMock())

    first = await resolver.active()
    second = await resolver.active()

    assert first is second
    world["factory"].assert_called_once_with(_COHERE)


async def test_a_config_change_in_the_admin_ui_applies_to_the_next_search(world) -> None:
    resolver = RerankerResolver(MagicMock())
    before = await resolver.active()

    world["config"] = {"provider": "voyage", "configuration": {"model": "rerank-2", "apiKey": "k"}}
    after = await resolver.active()

    assert after is not before
    assert world["factory"].call_count == 2


async def test_flag_on_with_nothing_configured_uses_the_system_default(world) -> None:
    world["config"] = None

    await RerankerResolver(MagicMock()).active()

    assert world["factory"].call_args.args[0]["provider"] == "defaultReranker"


async def test_a_broken_config_disables_reranking_instead_of_failing_search(world) -> None:
    world["factory"].side_effect = ValueError("Reranker configuration has no model name")

    assert await RerankerResolver(MagicMock()).active() is None


async def test_the_flag_defaults_to_off() -> None:
    from app.services.featureflag.platform_settings import is_reranker_enabled

    config_service = MagicMock()

    async def no_settings(*_args: object, **_kwargs: object) -> dict:
        return {}

    config_service.get_config.side_effect = no_settings
    assert await is_reranker_enabled(config_service) is False
