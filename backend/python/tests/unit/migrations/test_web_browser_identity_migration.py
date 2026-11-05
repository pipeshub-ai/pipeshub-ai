"""The startup backfill stores the settings each Web connector actually crawls with, so the form
shows them and the next save, of any setting, keeps them and is accepted."""

import copy
import logging
from types import SimpleNamespace

import pytest

from app.core import org_context
from app.migrations.web_browser_identity_migration import (
    MIGRATION_FLAG_KEY,
    backfill_sync_settings,
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
        self.read_in: dict[str, str] = {}

    async def get_config(self, path: str, default: object = None, use_cache: bool = True, *,
                         raise_on_error: bool = False) -> object:
        if path.startswith("/services/connectors/"):
            self.read_in[path] = org_context.current_org_id()
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
    def __init__(self, ids: list[str], *, org_id: str | None = "org-1", edge_org: str | None = None,
                 edge_read_fails: bool = False) -> None:
        self.ids = ids
        self.org_id = org_id
        self.edge_org = edge_org
        self.edge_read_fails = edge_read_fails

    async def get_nodes_by_field_in(self, collection: str, field: str, values: list, fields: list, *,
                                    raise_on_error: bool = False) -> list[dict]:
        assert (field, values) == ("type", ["Web"])
        return [{"id": i, "orgId": self.org_id} for i in self.ids]

    async def get_edges_to_node(self, node_id: str, edge_collection: str, *,
                                raise_on_error: bool = False) -> list[dict]:
        if self.edge_read_fails:
            if raise_on_error:
                raise ConnectionError("graph did not answer")
            return []
        return [{"from_id": self.edge_org}] if self.edge_org else []


def _container(config: _MemoryConfigService) -> SimpleNamespace:
    return SimpleNamespace(config_service=lambda: config)


def _web_config(**sync: object) -> dict:
    """Saved through the form: Maximum pages stored, in both copies."""
    form = {"url": "https://docs.example.org/", "max_pages": 100}
    return {"sync": {**form, "customValues": dict(form), **sync}}


async def test_a_connector_from_before_the_setting_is_stored_as_browser_like() -> None:
    config = _MemoryConfigService({_path("old"): _web_config()})

    result = await run_web_browser_identity_backfill(_container(config), _Graph(["old"]), LOGGER)

    sync = config.store[_path("old")]["sync"]
    assert sync["browser_like_identity"] is True
    assert sync["customValues"]["browser_like_identity"] is True
    assert result == {"success": True, "skipped": False, "connectors_updated": 1, "failures": 0}
    assert config.store[MIGRATION_FLAG_KEY]["done"] is True


async def test_each_connector_is_backfilled_inside_its_org() -> None:
    config = _MemoryConfigService({_path("old"): _web_config()})

    await run_web_browser_identity_backfill(_container(config), _Graph(["old"]), LOGGER)

    assert config.read_in == {_path("old"): "org-1"}


async def test_an_app_saved_without_its_org_takes_it_from_the_org_edge() -> None:
    config = _MemoryConfigService({_path("old"): _web_config()})

    result = await run_web_browser_identity_backfill(
        _container(config), _Graph(["old"], org_id=None, edge_org="org-2"), LOGGER,
    )

    assert result["connectors_updated"] == 1
    assert config.read_in == {_path("old"): "org-2"}


async def test_an_app_with_no_org_is_skipped_and_the_flag_is_still_set() -> None:
    config = _MemoryConfigService({_path("old"): _web_config()})

    result = await run_web_browser_identity_backfill(_container(config), _Graph(["old"], org_id=None), LOGGER)

    assert result == {"success": True, "skipped": False, "connectors_updated": 0, "failures": 0}
    assert config.read_in == {}
    assert config.store[MIGRATION_FLAG_KEY]["done"] is True


async def test_an_unreadable_org_edge_is_a_failure_and_the_flag_stays_unset() -> None:
    config = _MemoryConfigService({_path("old"): _web_config()})

    result = await run_web_browser_identity_backfill(
        _container(config), _Graph(["old"], org_id=None, edge_read_fails=True), LOGGER,
    )

    assert result == {"success": False, "skipped": False, "connectors_updated": 0, "failures": 1}
    assert MIGRATION_FLAG_KEY not in config.store


async def test_a_stored_choice_is_kept_and_copied_to_the_form_values() -> None:
    config = _MemoryConfigService({
        _path("new"): _web_config(
            browser_like_identity=False,
            customValues={"url": "https://docs.example.org/", "max_pages": 100, "browser_like_identity": False},
        ),
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


async def test_a_connector_without_maximum_pages_keeps_the_1000_it_crawls_with() -> None:
    """The form showed its default, 100, so the first save of any setting cut the crawl to a tenth."""
    stored = {"sync": {"url": "https://docs.example.org/", "customValues": {"url": "https://docs.example.org/"}}}
    config = _MemoryConfigService({_path("api"): stored})

    await run_web_browser_identity_backfill(_container(config), _Graph(["api"]), LOGGER)

    sync = config.store[_path("api")]["sync"]
    assert sync["max_pages"] == 1000 and sync["customValues"]["max_pages"] == 1000


async def test_out_of_range_numbers_are_stored_as_the_crawler_clamps_them() -> None:
    """Saving any setting would otherwise be refused for a value the user never touched."""
    over = {"depth": 25, "max_pages": "50000", "max_size_mb": "0"}
    config = _MemoryConfigService({_path("big"): _web_config(**over, customValues={"url": "x", **over})})

    await run_web_browser_identity_backfill(_container(config), _Graph(["big"]), LOGGER)

    sync = config.store[_path("big")]["sync"]
    for values in (sync, sync["customValues"]):
        # The string "0" is truthy, so the crawler clamps it to 1.
        assert (values["depth"], values["max_pages"], values["max_size_mb"]) == (10, 10_000, 1)


async def test_a_numeric_zero_is_stored_as_the_default_the_crawler_reads_it_as() -> None:
    """The crawler reads ``int(value or default)``: 0 means 3 clicks, 1,000 pages, 10 MB, not 1."""
    zeros = {"depth": 0, "max_pages": 0.0, "max_size_mb": 0}
    config = _MemoryConfigService({_path("zero"): _web_config(**zeros, customValues={"url": "x", **zeros})})

    await run_web_browser_identity_backfill(_container(config), _Graph(["zero"]), LOGGER)

    sync = config.store[_path("zero")]["sync"]
    for values in (sync, sync["customValues"]):
        assert (values["depth"], values["max_pages"], values["max_size_mb"]) == (3, 1000, 10)


async def test_an_empty_form_copy_stays_empty_so_the_form_keeps_reading_every_setting() -> None:
    """The form reads customValues only when it holds something; one value there would hide the rest."""
    stored = {"sync": {"url": "https://docs.example.org/", "depth": 4, "customValues": {}}}
    config = _MemoryConfigService({_path("flat"): stored})

    await run_web_browser_identity_backfill(_container(config), _Graph(["flat"]), LOGGER)

    sync = config.store[_path("flat")]["sync"]
    assert sync["customValues"] == {}
    assert (sync["browser_like_identity"], sync["max_pages"], sync["depth"]) == (True, 1000, 4)


async def test_settings_already_as_crawled_are_not_written() -> None:
    config = _MemoryConfigService({_path("ok"): _web_config(browser_like_identity=True, customValues={
        "url": "https://docs.example.org/", "max_pages": "250", "depth": "3", "browser_like_identity": True,
    }, max_pages="250", depth="3")})

    await run_web_browser_identity_backfill(_container(config), _Graph(["ok"]), LOGGER)

    assert config.writes == [MIGRATION_FLAG_KEY]


def _crawled(value: object, default: int, high: int) -> int:
    # WebConnector._load_config: int(sync.get(name) or default), then clamped to 1..high.
    return min(max(int(value or default), 1), high)


@pytest.mark.parametrize("raw", [None, "", 0, 0.0, "0", 1, "1", 3, "3", 7.9, -4, "-4", 25, "25", 99_999, True, False])
@pytest.mark.parametrize("name, default, high", [("depth", 3, 10), ("max_pages", 1000, 10_000), ("max_size_mb", 10, 100)])
def test_the_backfill_never_changes_what_the_crawler_uses(raw: object, name: str, default: int, high: int) -> None:
    sync = {"url": "https://docs.example.org/", name: raw, "customValues": {}}

    backfill_sync_settings(sync)

    assert _crawled(sync[name], default, high) == _crawled(raw, default, high)
    assert _crawled(sync[name], default, high) == sync[name] or isinstance(sync[name], str)
