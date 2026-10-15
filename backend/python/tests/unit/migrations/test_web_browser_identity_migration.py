"""Web connectors set up before Browser-like Identity existed crawl as a browser; the startup
backfill stores that value, so the settings form shows it and the next save keeps it."""

import copy
import logging
from types import SimpleNamespace

from app.migrations.web_browser_identity_migration import (
    MIGRATION_FLAG_KEY,
    run_web_browser_identity_backfill,
)

LOGGER = logging.getLogger("test-web-identity-backfill")


def _path(connector_id: str) -> str:
    return f"/services/connectors/{connector_id}/config"


class _MemoryConfigService:
    def __init__(self, store: dict, *, failing: frozenset[str] = frozenset()) -> None:
        self.store = store
        self.failing = failing
        self.writes: list[str] = []

    async def get_config(self, path: str, default: object = None, use_cache: bool = True, *,
                         raise_on_error: bool = False) -> object:
        if path in self.failing:
            if raise_on_error:
                raise ConnectionError(f"store did not answer for {path}")
            return default
        return copy.deepcopy(self.store.get(path, default))

    async def set_config(self, path: str, value: object) -> bool:
        self.writes.append(path)
        self.store[path] = copy.deepcopy(value)
        return True


class _Graph:
    def __init__(self, ids: list[str]) -> None:
        self.ids = ids

    async def get_nodes_by_field_in(self, collection: str, field: str, values: list, fields: list, *,
                                    raise_on_error: bool = False) -> list[dict]:
        assert (field, values) == ("type", ["Web"])
        return [{"id": i, "orgId": "org-1"} for i in self.ids]


def _container(config: _MemoryConfigService) -> SimpleNamespace:
    return SimpleNamespace(config_service=lambda: config)


def _web_config(**sync: object) -> dict:
    return {"sync": {"url": "https://docs.example.org/", "customValues": {"url": "https://docs.example.org/"}, **sync}}


async def test_a_connector_from_before_the_setting_is_stored_as_browser_like() -> None:
    config = _MemoryConfigService({_path("old"): _web_config()})

    result = await run_web_browser_identity_backfill(_container(config), _Graph(["old"]), LOGGER)

    sync = config.store[_path("old")]["sync"]
    assert sync["browser_like_identity"] is True
    assert sync["customValues"]["browser_like_identity"] is True
    assert result == {"success": True, "skipped": False, "connectors_updated": 1, "failures": 0}
    assert config.store[MIGRATION_FLAG_KEY]["done"] is True


async def test_a_stored_choice_is_kept_and_copied_to_the_form_values() -> None:
    config = _MemoryConfigService({
        _path("new"): _web_config(browser_like_identity=False, customValues={"browser_like_identity": False}),
        _path("half"): _web_config(browser_like_identity=False),
    })

    await run_web_browser_identity_backfill(_container(config), _Graph(["new", "half"]), LOGGER)

    assert config.writes == [_path("half"), MIGRATION_FLAG_KEY]
    assert config.store[_path("half")]["sync"]["customValues"]["browser_like_identity"] is False


async def test_a_failed_read_leaves_the_flag_unset_so_the_next_start_retries() -> None:
    config = _MemoryConfigService({_path("a"): _web_config(), _path("b"): _web_config()}, failing=frozenset({_path("a")}))

    result = await run_web_browser_identity_backfill(_container(config), _Graph(["a", "b"]), LOGGER)

    assert result["failures"] == 1 and result["connectors_updated"] == 1
    assert MIGRATION_FLAG_KEY not in config.store

    config.failing = frozenset()
    await run_web_browser_identity_backfill(_container(config), _Graph(["a", "b"]), LOGGER)

    assert config.store[_path("a")]["sync"]["browser_like_identity"] is True
    assert config.store[MIGRATION_FLAG_KEY]["done"] is True


async def test_it_runs_once() -> None:
    config = _MemoryConfigService({MIGRATION_FLAG_KEY: {"done": True}, _path("old"): _web_config()})

    result = await run_web_browser_identity_backfill(_container(config), _Graph(["old"]), LOGGER)

    assert result["skipped"] is True
    assert config.writes == []
