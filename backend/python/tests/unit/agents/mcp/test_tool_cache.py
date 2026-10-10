"""`app.agents.mcp.tool_cache`: tool lists kept between chats, per sign-in, stored once."""
from __future__ import annotations

import copy
import time
from typing import Any
from unittest.mock import patch

import pytest

from app.agents.constants.mcp_server_constants import (
    get_mcp_tool_catalog_blob_path,
    get_mcp_tool_catalog_pointer_path,
)
from app.agents.mcp import tool_cache
from app.agents.mcp.discovery import tool_infos_from_listing
from app.agents.mcp.models import MCPToolInfo
from app.agents.mcp.service import instance_config_from_dict


class _ConfigService:
    """`ConfigurationService` as the cache uses it: no LRU, every write with a TTL."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}
        self.writes: list[str] = []
        self.deletes: list[str] = []
        self.reads_fail = False
        self.writes_fail = False

    async def get_config(self, key: str, default: Any = None, use_cache: bool = False, *, keep_in_cache: bool = True) -> Any:  # noqa: ANN401
        assert use_cache is False and keep_in_cache is False
        if self.reads_fail:
            raise RuntimeError("store down")
        return copy.deepcopy(self.values.get(key, default))

    async def set_config(self, key: str, value: Any, *, ttl_seconds: int | None = None, keep_in_cache: bool = True) -> bool:  # noqa: ANN401
        assert keep_in_cache is False
        assert ttl_seconds is not None and ttl_seconds > 0, "a TTL of 0 would never expire"
        if self.writes_fail:
            return False
        self.values[key] = copy.deepcopy(value)
        self.ttls[key] = ttl_seconds
        self.writes.append(key)
        return True

    async def delete_config(self, key: str) -> bool:
        self.deletes.append(key)
        return self.values.pop(key, None) is not None


def _instance(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    instance = {
        "_id": "inst-1", "orgId": "org-1", "createdBy": "admin-1", "name": "Jira", "typeId": None,
        "transport": "streamable_http", "url": "https://mcp.example.com/mcp", "authMode": "api_token",
        "isCustom": True, "createdAt": 0, "updatedAt": 0,
    }
    instance.update(overrides)
    return instance


def _tools(*names: str) -> list[MCPToolInfo]:
    return [
        MCPToolInfo(
            name=name, namespaced_name=f"mcp_jira_{name}", description=f"{name} things",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            annotations={"readOnlyHint": name.startswith("get")},
        )
        for name in names
    ]


def _catalog(*names: str, instructions: str | None = "Prefer search.") -> tool_cache.CachedCatalog:
    return tool_cache.catalog_from_tools(_tools(*names), instructions)


async def _write(store: _ConfigService, instance: dict[str, Any], owner: str, catalog: tool_cache.CachedCatalog, **kwargs: Any) -> Any:  # noqa: ANN401
    return await tool_cache.write(
        store, instance=instance, config=instance_config_from_dict(instance), owner_id=owner,  # type: ignore[arg-type]
        auth=kwargs.pop("auth", {}), catalog=catalog, **kwargs,
    )


async def _read(store: _ConfigService, instance: dict[str, Any], owner: str, auth: dict | None = None) -> Any:  # noqa: ANN401
    return await tool_cache.read(
        store, instance=instance, config=instance_config_from_dict(instance), owner_id=owner, auth=auth or {},  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _default_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tool_cache.TTL_ENV, raising=False)


class TestWhatIsKept:
    async def test_a_miss_until_something_was_written(self) -> None:
        assert await _read(_ConfigService(), _instance(), "u1") is None

    async def test_tools_hints_and_instructions_come_back(self) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("get_issue", "create_issue"))

        hit = await _read(store, _instance(), "u1")

        assert hit is not None and hit.owner_key == "u1"
        assert hit.catalog.instructions == "Prefer search."
        infos = tool_infos_from_listing(tool_cache.tool_listing(hit.catalog), instance_config_from_dict(_instance()), "jira")
        assert [(t.name, t.namespaced_name, t.annotations) for t in infos] == [
            ("get_issue", "mcp_jira_get_issue", {"readOnlyHint": True}),
            ("create_issue", "mcp_jira_create_issue", {"readOnlyHint": False}),
        ]
        assert infos[0].input_schema == {"type": "object", "properties": {"q": {"type": "string"}}}

    async def test_the_digest_doesnt_depend_on_tool_order(self) -> None:
        assert tool_cache.digest(_catalog("a", "b")) == tool_cache.digest(_catalog("b", "a"))
        assert tool_cache.digest(_catalog("a", "b")) != tool_cache.digest(_catalog("a", "b", instructions="Other."))

    async def test_an_empty_list_is_never_kept(self) -> None:
        """A server still starting up can list nothing for a moment."""
        store = _ConfigService()
        assert await _write(store, _instance(), "u1", _catalog()) is None
        assert store.writes == []

    async def test_a_list_too_large_to_store_stays_live(self) -> None:
        store = _ConfigService()
        with patch.object(tool_cache, "MAX_CATALOG_BYTES", 100):
            assert await _write(store, _instance(), "u1", _catalog("search")) is None
        assert store.writes == []


class TestWhoSeesWhat:
    async def test_identical_lists_are_stored_once(self) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"))
        await _write(store, _instance(), "u2", _catalog("search"))

        blobs = [k for k in store.values if "/blobs/" in k]
        pointers = [k for k in store.values if "/pointers/" in k]
        assert len(blobs) == 1 and len(pointers) == 2

    async def test_a_sign_in_never_reads_anothers_list(self) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"))
        await _write(store, _instance(), "u2", _catalog("admin_only"))

        assert [t.name for t in (await _read(store, _instance(), "u1")).catalog.tools] == ["search"]
        assert [t.name for t in (await _read(store, _instance(), "u2")).catalog.tools] == ["admin_only"]
        assert await _read(store, _instance(), "u3") is None

    async def test_a_shared_admin_credential_is_one_sign_in_for_everyone(self) -> None:
        store = _ConfigService()
        shared = _instance(useAdminAuth=True)
        await _write(store, shared, "admin-1", _catalog("search"))

        hit = await _read(store, shared, "someone-else")
        assert hit is not None and hit.owner_key == "_shared"
        assert get_mcp_tool_catalog_pointer_path("inst-1", "_shared") in store.values


class TestWhenItIsAMiss:
    async def test_another_target_is_a_miss(self) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"))
        assert await _read(store, _instance(url="https://other.example.com/mcp"), "u1") is None

    async def test_a_new_credential_is_a_miss_and_a_refreshed_one_isnt(self) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"), auth={"connectedAt": 111, "updatedAt": 1})

        assert await _read(store, _instance(), "u1", {"connectedAt": 111, "updatedAt": 999}) is not None
        assert await _read(store, _instance(), "u1", {"connectedAt": 222}) is None

    async def test_a_record_saved_before_stamps_matches_until_its_next_save(self) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"), auth={"isAuthenticated": True})
        assert await _read(store, _instance(), "u1", {"isAuthenticated": True}) is not None

    async def test_an_expired_pointer_is_a_miss_even_if_the_store_kept_it(self) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"))
        with patch.object(tool_cache.time, "time", return_value=time.time() + 86401):
            assert await _read(store, _instance(), "u1") is None

    async def test_a_blob_that_doesnt_match_its_hash_is_a_miss(self) -> None:
        store = _ConfigService()
        pointer = await _write(store, _instance(), "u1", _catalog("search"))
        store.values[get_mcp_tool_catalog_blob_path(pointer.digest)]["tools"][0]["name"] = "tampered"
        assert await _read(store, _instance(), "u1") is None

    @pytest.mark.parametrize("pointer", ["not a dict", {"digest": "x"}])
    async def test_a_malformed_pointer_is_a_miss(self, pointer: object) -> None:
        store = _ConfigService()
        store.values[get_mcp_tool_catalog_pointer_path("inst-1", "u1")] = pointer
        assert await _read(store, _instance(), "u1") is None

    async def test_a_missing_blob_is_a_miss(self) -> None:
        store = _ConfigService()
        pointer = await _write(store, _instance(), "u1", _catalog("search"))
        del store.values[get_mcp_tool_catalog_blob_path(pointer.digest)]
        assert await _read(store, _instance(), "u1") is None


class TestHowLongItLasts:
    async def test_a_day_by_default_and_the_blob_outlives_its_pointers(self) -> None:
        store = _ConfigService()
        pointer = await _write(store, _instance(), "u1", _catalog("search"))

        assert store.ttls[get_mcp_tool_catalog_pointer_path("inst-1", "u1")] == 86400
        assert store.ttls[get_mcp_tool_catalog_blob_path(pointer.digest)] == 172800

    @pytest.mark.parametrize("server_ttl,stored", [(600.0, 600), (1.2, 2), (999999.0, 86400)])
    async def test_a_shorter_server_hint_wins(self, server_ttl: float, stored: int) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"), server_ttl_seconds=server_ttl)
        assert store.ttls[get_mcp_tool_catalog_pointer_path("inst-1", "u1")] == stored

    async def test_a_server_that_says_not_to_cache_drops_what_was_kept(self) -> None:
        """A TTL it set on purpose: positive, under a second."""
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"))

        assert await _write(store, _instance(), "u1", _catalog("search"), server_ttl_seconds=0.5) is None
        assert await _read(store, _instance(), "u1") is None

    async def test_immediately_stale_the_2026_default_is_kept_briefly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`ttlMs: 0` is what every 2026-07-28 server that set nothing sends."""
        store = _ConfigService()
        assert await _write(store, _instance(), "u1", _catalog("search"), server_ttl_seconds=0.0) is not None
        assert store.ttls[get_mcp_tool_catalog_pointer_path("inst-1", "u1")] == 900

        monkeypatch.setenv(tool_cache.STALE_TTL_ENV, "0")
        assert await _write(store, _instance(), "u1", _catalog("search"), server_ttl_seconds=0.0) is None
        assert await _read(store, _instance(), "u1") is None

    async def test_the_kill_switch_reads_and_writes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"))
        monkeypatch.setenv(tool_cache.TTL_ENV, "0")

        assert await _read(store, _instance(), "u1") is None
        assert await _write(store, _instance(), "u2", _catalog("search")) is None
        assert len(store.writes) == 2

    async def test_the_same_list_again_rewrites_only_the_pointer(self) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"))
        store.writes.clear()

        await _write(store, _instance(), "u1", _catalog("search"))
        assert store.writes == [get_mcp_tool_catalog_pointer_path("inst-1", "u1")]

    async def test_a_blob_older_than_a_pointer_could_live_is_rewritten(self) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"))
        store.writes.clear()

        with patch.object(tool_cache.time, "time", return_value=time.time() + 86401):
            await _write(store, _instance(), "u1", _catalog("search"))
        assert len(store.writes) == 2

    async def test_a_pointer_past_half_its_life_wants_refreshing(self) -> None:
        pointer = await _write(_ConfigService(), _instance(), "u1", _catalog("search"))
        assert not tool_cache.needs_refresh(pointer)
        with patch.object(tool_cache.time, "time", return_value=pointer.discovered_at + 43201):
            assert tool_cache.needs_refresh(pointer)


class TestAPublicList:
    async def test_an_admins_server_that_says_public_serves_everyone(self) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"), public=True, auth={"connectedAt": 1})

        hit = await _read(store, _instance(), "u2", {"connectedAt": 2})
        assert hit is not None and hit.owner_key == "_public"

    async def test_a_personal_servers_claim_is_ignored(self) -> None:
        store = _ConfigService()
        personal = _instance(scope="personal")
        await _write(store, personal, "u1", _catalog("search"), public=True)

        assert get_mcp_tool_catalog_pointer_path("inst-1", "_public") not in store.values
        assert (await _read(store, personal, "u1")).owner_key == "u1"

    async def test_a_server_that_turns_private_stops_serving_the_public_list(self) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"), public=True)
        await _write(store, _instance(), "u1", _catalog("search", "private_tool"))

        assert await _read(store, _instance(), "u2") is None
        assert [t.name for t in (await _read(store, _instance(), "u1")).catalog.tools] == ["search", "private_tool"]

    async def test_a_server_that_then_says_not_to_cache_stops_serving_the_public_list(self) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"), public=True)

        assert await _write(store, _instance(), "u1", _catalog("search"), server_ttl_seconds=0.5) is None
        assert await _read(store, _instance(), "u2") is None


class TestAStoreThatFails:
    async def test_a_read_that_fails_is_a_miss(self) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"))
        store.reads_fail = True
        assert await _read(store, _instance(), "u1") is None

    async def test_a_write_that_fails_caches_nothing(self) -> None:
        store = _ConfigService()
        store.writes_fail = True
        assert await _write(store, _instance(), "u1", _catalog("search")) is None


class TestForget:
    async def test_drops_the_pointer(self) -> None:
        store = _ConfigService()
        await _write(store, _instance(), "u1", _catalog("search"))
        await tool_cache.forget(store, instance_id="inst-1", owner_key="u1")
        assert await _read(store, _instance(), "u1") is None

    async def test_a_missing_pointer_isnt_deleted(self) -> None:
        """Deleting a missing key logs an error."""
        store = _ConfigService()
        await tool_cache.forget(store, instance_id="inst-1", owner_key="u1")
        assert store.deletes == []
