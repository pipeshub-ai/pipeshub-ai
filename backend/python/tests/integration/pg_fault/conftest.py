"""Fault tests of the PostgreSQL-only stack on a throwaway cluster and PgBouncer (``KERNEL_TEST_PG_BINS``, ``KERNEL_TEST_PGBOUNCER``,
``KERNEL_TEST_MIGRATE_PYTHON``, as tests/integration/db). The cluster is restarted and stopped by the tests, so they run in a pytest
process of their own (pg-runtime.yml), never next to other suites on the same cluster."""
from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from tests.integration.db.conftest import cluster, env  # noqa: F401 - the fixtures of the throwaway cluster
from tests.integration.pg_fault.load import Stack

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.integration.db.conftest import Cluster, Env


def pg_ctl(c: Cluster, *args: str) -> None:
    # -l on every start: a server started without it keeps our captured stdout open, and run() never returns
    log = ("-l", str(c.root / "log")) if {"start", "restart"} & set(args) else ()
    subprocess.run([f"{c.bin}/pg_ctl", "-D", str(c.root / "data"), "-w", "-t", "120", *log, *args], check=True, capture_output=True)


def start_server(c: Cluster) -> None:
    """Start the cluster with the options the ``cluster`` fixture gave it."""
    pg_ctl(c, "-o", f"-p {c.port} -k {c.root} -c listen_addresses=127.0.0.1 -c max_connections=200", "start")


@pytest.fixture
def stack(env: Env, monkeypatch: pytest.MonkeyPatch) -> Iterator[Stack]:  # noqa: F811
    # as deployment/docker-compose/postgres/pgbouncer.ini: a client waits at most 5 s for a server connection, a failed login is retried after 2 s
    port = env.start_bouncer(pool_size=10, settings={"query_wait_timeout": 5, "server_login_retry": 2})
    base = f"127.0.0.1:{env.cluster.port}/{env.db}"
    s = Stack(env.dsn, env.cluster.port, port, f"postgresql://postgres@{base}", f"postgresql+psycopg://pipeshub_migrate@{base}")
    for k, v in s.environ().items():
        monkeypatch.setenv(k, v)
    yield s
    # a test that failed mid-outage leaves the server down; the next test (and the fixture's own clean-up) needs it
    if subprocess.run([f"{env.cluster.bin}/pg_ctl", "-D", str(env.cluster.root / "data"), "status"], capture_output=True).returncode:
        start_server(env.cluster)
