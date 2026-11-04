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
]


def _kv_runtime(runtime, monkeypatch) -> RuntimeDb:
    monkeypatch.setenv(
        "PG_CORE_URL",
        runtime.cfg.dsn.replace("pipeshub_connectors_login", "pipeshub_kv_login"),
    )
    monkeypatch.setenv("PG_RUNTIME_SERVICE", "kv")
    return RuntimeDb()


async def test_the_kv_role_reads_and_writes_configuration(
    runtime, monkeypatch, uid
) -> None:
    store = PostgresKeyValueStore(
        lambda v: json.dumps(v).encode(),
        json.loads,
        runtime=_kv_runtime(runtime, monkeypatch),
    )
    assert await store.health_check()
    assert await store.create_key(f"/kv{uid}/a", {"x": 1}, overwrite=False)
    assert await store.get_key(f"/kv{uid}/a") == {"x": 1}
    assert await store.list_keys_in_directory(f"/kv{uid}") == [f"/kv{uid}/a"]
    await store.publish_change(f"/kv{uid}/a")
    assert await store.delete_key(f"/kv{uid}/a")


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
        for t in ("sched_jobs", "sched_repeats"):  # only the Node API schedules
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
