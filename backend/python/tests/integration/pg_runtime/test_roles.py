"""Least privilege: the processes that only read their configuration use a role that holds the key-value table and nothing else."""

import json

import pytest

from app.config.providers.postgres_store import PostgresKeyValueStore
from app.services.pg_runtime import RuntimeDb

TABLES = [
    "queue_messages",
    "queue_groups",
    "queue_deliveries",
    "sched_jobs",
    "sched_repeats",
    "cache_entries",
]


def _kv_runtime(runtime, monkeypatch) -> RuntimeDb:
    monkeypatch.setenv(
        "PG_CORE_URL",
        runtime.cfg.dsn.replace("pipeshub_connectors_login", "pipeshub_kv_login"),
    )
    monkeypatch.setenv("PG_RUNTIME_SERVICE", "kv")
    return RuntimeDb()


async def test_the_kv_role_uses_the_key_value_table_and_writes_only_while_access_by_prefix_is_in_shadow(
    runtime, monkeypatch, uid
) -> None:
    db = await runtime.db()
    async with db.runtime_uow() as tx:
        enforce = await tx.fetchval("/* pipeshub:test.kvmode */ SELECT enforce FROM platform.kv_access")
    store = PostgresKeyValueStore(
        lambda v: json.dumps(v).encode(),
        json.loads,
        runtime=_kv_runtime(runtime, monkeypatch),
    )
    assert await store.health_check()
    await store.publish_change(f"/kv{uid}/a")
    if enforce:  # the kv role writes no prefix
        with pytest.raises(ConnectionError, match="row-level security"):
            await store.create_key(f"/kv{uid}/a", {"x": 1}, overwrite=False)
        assert await store.list_keys_in_directory(f"/kv{uid}") == []
        return
    assert await store.create_key(f"/kv{uid}/a", {"x": 1}, overwrite=False)
    assert await store.get_key(f"/kv{uid}/a") == {"x": 1}
    assert await store.list_keys_in_directory(f"/kv{uid}") == [f"/kv{uid}/a"]
    assert await store.delete_key(f"/kv{uid}/a")


def _config_as(runtime, monkeypatch, service: str):  # noqa: ANN202
    """The configuration service a process of ``service`` builds at start, on its own login."""
    import logging

    from app.config.configuration_service import ConfigurationService
    from app.config.providers.encrypted_store import EncryptedKeyValueStore

    dsn = runtime.cfg.dsn.replace("pipeshub_connectors_login", f"pipeshub_{service}_login")
    monkeypatch.setenv("KV_STORE_TYPE", "postgres")
    monkeypatch.setenv("PG_CORE_URL", dsn)
    monkeypatch.setenv("PG_LISTEN_URL", runtime.cfg.dsn)        # change notifications on the suite's one listen connection (the hub keeps one per DSN)
    monkeypatch.setenv("PG_RUNTIME_SERVICE", service)
    log = logging.getLogger(f"spend-guard-{service}")
    return ConfigurationService(log, EncryptedKeyValueStore(log))


async def test_the_services_that_bind_the_spend_guard_start_its_key_path_in_either_mode(runtime, monkeypatch) -> None:
    """The breaker's key path at start: ``fleet_breaker.load_install_secret`` (create the install secret if absent, then read it) in
    indexing (indexing_main) and in parsing and extraction (the kv login, ``fleet_redis.bind_service_fleet``, which also reads the Redis
    the breaker lives in: ``fleet_redis_config``); then indexing's pause judge reads the limits an admin saved (``spend_limits_source``).
    Refused, the create raises and the service does not start, the Redis settings read as absent (the environment's), and so do the limits."""
    from app.modules.pipeline.pause_budget import PauseLimits
    from app.modules.pipeline.pause_judge import SPEND_GUARD_KEY, spend_limits_source
    from app.services.llm_gateway.fleet_breaker import load_install_secret
    from app.services.llm_gateway.fleet_redis import fleet_redis_config

    admin = _config_as(runtime, monkeypatch, "node")
    redis_key = "/services/redis"
    saved_redis = await admin.get_config(redis_key, use_cache=False)
    try:
        assert await admin.set_config(SPEND_GUARD_KEY, {"pause": {"sends": 7, "absoluteSends": 9, "wallSeconds": 11}})     # the admin API saves the limits
        assert await admin.set_config(redis_key, {"host": "fleet-redis.invalid", "port": 6390})                              # and the Redis
        for service in ("indexing", "kv"):
            svc = _config_as(runtime, monkeypatch, service)
            try:
                await load_install_secret(svc)
                assert (await fleet_redis_config(svc)).host == "fleet-redis.invalid", service
                if service == "indexing":
                    got = await spend_limits_source(svc, PauseLimits())()
                    assert (got.charged_sends, got.absolute_sends, got.wall_seconds) == (7, 9, 11)
            finally:
                await svc.close()
    finally:
        await admin.delete_config(SPEND_GUARD_KEY)
        await (admin.set_config(redis_key, saved_redis) if saved_redis is not None else admin.delete_config(redis_key))
        await admin.close()


async def test_indexing_reads_the_parse_admission_numbers_an_admin_saved_in_either_mode(runtime, monkeypatch) -> None:
    """The class admission's live numbers, read the way indexing does (``indexing_main.attach_parse_admission_config``):
    ``LiveAdmissionConfig.refresh`` on the indexing login. Refused, the read sees no row and the environment defaults stay."""
    from app.modules.parse_engine.admission import (
        ADMISSION_CONFIG_KEY,
        AdmissionNumbers,
        LiveAdmissionConfig,
    )

    admin = _config_as(runtime, monkeypatch, "node")
    try:
        assert await admin.set_config(ADMISSION_CONFIG_KEY, {"largeCap": 0.6, "agingS": 120})
        svc = _config_as(runtime, monkeypatch, "indexing")
        try:
            got = await LiveAdmissionConfig(svc, env_defaults=AdmissionNumbers()).refresh()
            assert (got.large_cap, got.aging_s) == (0.6, 120)
        finally:
            await svc.close()
    finally:
        await admin.delete_config(ADMISSION_CONFIG_KEY)
        await admin.close()


async def test_the_kv_role_holds_nothing_else(runtime) -> None:
    db = await runtime.db()
    async with db.runtime_uow() as tx:

        async def can(role: str, table: str) -> bool:
            return bool(
                await tx.fetchval(
                    "/* pipeshub:test.priv */ SELECT has_table_privilege($1, $2, 'SELECT') OR has_table_privilege($1, $2, 'INSERT')"
                    " OR has_table_privilege($1, $2, 'UPDATE') OR has_table_privilege($1, $2, 'DELETE')",
                    role,
                    f"platform.{table}",
                )
            )

        assert await can("pipeshub_kv", "kv_entries")
        for t in TABLES:
            assert not await can("pipeshub_kv", t), t
        for t in ("sched_jobs", "sched_repeats", "cache_entries"):  # only the Node API schedules and holds sessions
            for role in ("pipeshub_connectors", "pipeshub_indexing", "pipeshub_query"):
                assert not await can(role, t), (role, t)
            assert await can("pipeshub_node", t)
        # no org-scoped table, whatever its schema
        n = await tx.fetchval(
            "/* pipeshub:test.orgtables */ SELECT count(*) FROM information_schema.tables t WHERE t.table_schema IN "
            "('identity','conversation','content','oauth','integrations','access','retrieval') AND t.table_type = 'BASE TABLE' "
            "AND (has_table_privilege('pipeshub_kv', format('%I.%I', t.table_schema, t.table_name), 'SELECT') "
            "OR has_table_privilege('pipeshub_kv', format('%I.%I', t.table_schema, t.table_name), 'INSERT'))"
        )
        assert n == 0


@pytest.mark.parametrize("table", TABLES)
async def test_the_kv_login_cannot_read_the_queue_or_scheduler(
    runtime, monkeypatch, table
) -> None:
    import asyncpg

    con = await asyncpg.connect(
        runtime.cfg.dsn.replace("pipeshub_connectors_login", "pipeshub_kv_login")
    )
    try:
        await con.execute("SET ROLE pipeshub_kv")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await con.fetch(f"SELECT 1 FROM platform.{table} LIMIT 1")
    finally:
        await con.close()
