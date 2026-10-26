"""MetadataSchemaRegistry on a real Redis, through the stores production builds.

``EncryptedKeyValueStore`` (KV_STORE_TYPE=redis) under ``ConfigurationService``:
versions, idempotence, registrations racing from many clients and processes,
org isolation and the version cap.

Needs a Redis; skips when it is not reachable. Environment:
REGISTRY_IT_REDIS_HOST (default 127.0.0.1), REGISTRY_IT_REDIS_PORT (default 6379).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import Connectors
from app.models.record_metadata import PropertyDefinition, PropertyType
from app.modules.record_metadata.builtin import TICKET_PROPERTIES
from app.modules.record_metadata.schemas import (
    MAX_SCHEMA_VERSIONS,
    MetadataSchemaError,
    MetadataSchemaRegistry,
    schema_key,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.config.configuration_service import ConfigurationService

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

HOST = os.environ.get("REGISTRY_IT_REDIS_HOST", "127.0.0.1")
PORT = os.environ.get("REGISTRY_IT_REDIS_PORT", "6379")
PREFIX = f"registry-it-{uuid.uuid4().hex[:8]}:"
ORG_A, ORG_B = "org-a", "org-b"


def _env() -> dict[str, str]:
    return {"REDIS_HOST": HOST, "REDIS_PORT": PORT, "REDIS_KV_PREFIX": PREFIX,
            "SECRET_KEY": "registry-it-secret", "KV_STORE_TYPE": "redis"}


def _config_service() -> ConfigurationService:
    from app.config.configuration_service import ConfigurationService
    from app.config.providers.encrypted_store import EncryptedKeyValueStore

    for k, v in _env().items():
        os.environ[k] = v
    logger = logging.getLogger("registry-it")
    return ConfigurationService(logger, EncryptedKeyValueStore(logger))


@pytest.fixture
async def config() -> AsyncIterator[ConfigurationService]:
    service = _config_service()
    try:
        await asyncio.wait_for(service.store.get_key("/registry-it/ping"), timeout=5)
    except Exception as exc:
        pytest.skip(f"Redis not reachable at {HOST}:{PORT}: {exc}")
    yield service


def _props(*names: str) -> tuple[PropertyDefinition, ...]:
    return tuple(PropertyDefinition(name=n, type=PropertyType.STRING, display_label=n.title()) for n in names)


def _connector() -> str:
    return uuid.uuid4().hex


async def test_versions_and_idempotence(config: ConfigurationService) -> None:
    registry, conn = MetadataSchemaRegistry(config), _connector()
    first = await registry.register(ORG_A, conn, TICKET_PROPERTIES)
    again = await registry.register(ORG_A, conn, tuple(reversed(TICKET_PROPERTIES)))
    changed = await registry.register(ORG_A, conn, (*TICKET_PROPERTIES, *_props("team")))
    back = await registry.register(ORG_A, conn, TICKET_PROPERTIES)

    assert first.schema_id == again.schema_id == f"{ORG_A}:{conn}:1"
    assert changed.schema_id == f"{ORG_A}:{conn}:2"
    # Going back to v1's properties is v1 again (an id always names the same properties), and the latest.
    assert back.schema_id == f"{ORG_A}:{conn}:1"
    assert await config.get_config(schema_key(conn, 3), use_cache=False) is None
    assert (await registry.get(ORG_A, conn, 1)).properties == first.properties
    assert (await registry.get(ORG_A, conn)).schema_id == back.schema_id
    assert await registry.get(ORG_A, _connector()) is None


async def test_builtin_registration_round_trips_the_stored_definitions(config: ConfigurationService) -> None:
    registry, conn = MetadataSchemaRegistry(config), _connector()
    schema = await registry.register_builtin(ORG_A, conn, Connectors.ZENDESK)
    assert schema is not None
    fresh = MetadataSchemaRegistry(_config_service())
    assert (await fresh.get(ORG_A, conn)).properties == TICKET_PROPERTIES
    assert await registry.register_builtin(ORG_A, conn, Connectors.SLACK) is None


async def test_same_properties_racing_from_many_clients_make_one_version(config: ConfigurationService) -> None:
    conn = _connector()
    registries = [MetadataSchemaRegistry(_config_service()) for _ in range(16)]
    results = await asyncio.gather(*(r.register(ORG_A, conn, TICKET_PROPERTIES) for r in registries))
    assert {r.schema_id for r in results} == {f"{ORG_A}:{conn}:1"}
    assert await config.get_config(schema_key(conn, 2), use_cache=False) is None


async def test_distinct_properties_racing_get_distinct_gap_free_versions(config: ConfigurationService) -> None:
    conn = _connector()
    proposals = [_props(f"field{i}") for i in range(12)]
    registries = [MetadataSchemaRegistry(_config_service()) for _ in proposals]
    results = await asyncio.gather(*(r.register(ORG_A, conn, p) for r, p in zip(registries, proposals)))

    versions = sorted(int(r.schema_id.rsplit(":", 1)[1]) for r in results)
    assert versions == list(range(1, 13))
    for result, proposed in zip(results, proposals):
        stored = await MetadataSchemaRegistry(config).get(ORG_A, conn, int(result.schema_id.rsplit(":", 1)[1]))
        assert stored.properties == proposed


def _register_in_process(conn: str, names: tuple[str, ...]) -> str:
    async def run() -> str:
        return (await MetadataSchemaRegistry(_config_service()).register(ORG_A, conn, _props(*names))).schema_id
    return asyncio.run(run())


async def test_registrations_racing_across_processes(config: ConfigurationService) -> None:
    conn = _connector()
    jobs = [("a",)] * 6 + [("b",)] * 6
    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor(max_workers=8) as pool:
        ids = await asyncio.gather(*(loop.run_in_executor(pool, _register_in_process, conn, j) for j in jobs))
    # Every id names the properties its caller proposed (integrity).
    reader = MetadataSchemaRegistry(config)
    for job, sid in zip(jobs, ids):
        stored = await reader.get(ORG_A, conn, int(sid.rsplit(":", 1)[1]))
        assert stored.properties == _props(*job), (job, sid)
    # Interleaved proposals of two property sets make exactly two versions: each registration
    # compares against every version, and a lost race against the winner.
    count = 0
    while await config.get_config(schema_key(conn, count + 1), use_cache=False) is not None:
        count += 1
    print(f"\n12 cross-process registrations of 2 property sets -> {count} versions")
    assert count == 2
    assert (await reader.get(ORG_A, conn)).schema_id in set(ids)


async def test_another_orgs_connector_is_refused(config: ConfigurationService) -> None:
    conn = _connector()
    await MetadataSchemaRegistry(config).register(ORG_A, conn, TICKET_PROPERTIES)
    other = MetadataSchemaRegistry(_config_service())
    with pytest.raises(MetadataSchemaError, match="another org"):
        await other.get(ORG_B, conn)
    with pytest.raises(MetadataSchemaError, match="another org"):
        await other.register(ORG_B, conn, TICKET_PROPERTIES)
    with pytest.raises(MetadataSchemaError, match="another org"):
        await other.get(ORG_B, conn, 1)


async def test_two_orgs_racing_for_one_connector_leave_one_owner(config: ConfigurationService) -> None:
    conn = _connector()
    regs = [MetadataSchemaRegistry(_config_service()) for _ in range(8)]
    outcomes = await asyncio.gather(
        *(r.register(ORG_A if i % 2 else ORG_B, conn, TICKET_PROPERTIES) for i, r in enumerate(regs)),
        return_exceptions=True,
    )
    owners = {o.schema_id.split(":", 1)[0] for o in outcomes if not isinstance(o, BaseException)}
    errors = [o for o in outcomes if isinstance(o, BaseException)]
    assert len(owners) == 1
    assert all(isinstance(e, MetadataSchemaError) for e in errors)
    assert len(errors) == 4


async def _seed(config: ConfigurationService, conn: str, count: int) -> None:
    for v in range(1, count + 1):
        await config.create_config_if_absent(schema_key(conn, v), {
            "orgId": ORG_A, "connectorId": conn, "version": v,
            "properties": [p.model_dump(mode="json") for p in _props(f"v{v}")],
        })


async def test_reading_the_latest_costs_two_reads(config: ConfigurationService) -> None:
    """Versions stored before the latest pointer existed are found by counting up; once a
    registration writes the pointer, the latest is two reads however many versions there are."""
    conn = _connector()
    await _seed(config, conn, 100)
    reads: list[str] = []
    original = config.get_config

    async def spy(key: str, *a: object, **kw: object) -> object:
        reads.append(key)
        return await original(key, *a, **kw)

    config.get_config = spy  # type: ignore[method-assign]
    registry = MetadataSchemaRegistry(config)
    assert (await registry.get(ORG_A, conn)).schema_id.endswith(":100")
    scanned = len(reads)
    assert scanned == 102  # the absent pointer, then versions 1..101

    await registry.register(ORG_A, conn, _props("v100"))
    reads.clear()
    started = time.perf_counter()
    latest = await registry.get(ORG_A, conn)
    elapsed = time.perf_counter() - started
    assert latest.schema_id.endswith(":100")
    assert len(reads) == 2
    print(f"\nlatest of 100 versions on Redis: {scanned} reads without the pointer, "
          f"{len(reads)} with it ({elapsed * 1000:.1f} ms)")


async def test_at_the_version_cap_the_latest_is_still_readable(config: ConfigurationService) -> None:
    conn = _connector()
    await _seed(config, conn, MAX_SCHEMA_VERSIONS)
    registry = MetadataSchemaRegistry(config)
    with pytest.raises(MetadataSchemaError, match="schema versions"):
        await registry.register(ORG_A, conn, _props("one_more"))
    assert await config.get_config(schema_key(conn, MAX_SCHEMA_VERSIONS + 1), use_cache=False) is None
    latest = await registry.get(ORG_A, conn)
    assert latest is not None and latest.schema_id.endswith(f":{MAX_SCHEMA_VERSIONS}")
    same = await registry.register(ORG_A, conn, _props(f"v{MAX_SCHEMA_VERSIONS}"))
    assert same.schema_id.endswith(f":{MAX_SCHEMA_VERSIONS}")


async def test_an_unreadable_stored_version_is_an_error_not_absent(config: ConfigurationService) -> None:
    conn = _connector()
    await config.create_config_if_absent(schema_key(conn, 1), {"orgId": ORG_A, "properties": [{"name": "Bad"}]})
    with pytest.raises(MetadataSchemaError, match="invalid"):
        await MetadataSchemaRegistry(config).get(ORG_A, conn)
