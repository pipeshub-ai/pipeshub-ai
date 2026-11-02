"""Integration fixtures: throwaway PostgreSQL clusters and a real PgBouncer, started from local binaries.

``KERNEL_TEST_PG_BINS`` is a comma-separated list of PostgreSQL ``bin`` directories (one cluster per entry),
``KERNEL_TEST_PGBOUNCER`` the PgBouncer binary. Without them these tests skip. ``KERNEL_TEST_PORT_BASE`` (default 61000)
is where the free-port search starts. Each test gets its own database cloned from a template; the cluster is deleted at
the end. The template database is the real kernel: ``db.bootstrap_roles`` and ``db.migrate``
(``KERNEL_TEST_MIGRATE_PYTHON`` names an interpreter with ``backend/db/requirements.txt``; default: this one), then ``fixture.sql``
adds the test tables through ``kernel.apply_rls``.
"""
from __future__ import annotations

import os
import random
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg
import pytest

from app.core.db import DbConfig, KernelDB

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

HERE = Path(__file__).resolve().parent
SERVICES = ("node", "connectors", "indexing", "query")
RESET = ("RESET ALL; SET SESSION AUTHORIZATION DEFAULT; CLOSE ALL; DISCARD TEMP; UNLISTEN *; SELECT pg_advisory_unlock_all()")

LOGINS = [f"pipeshub_{s}_login" for s in SERVICES] + ["pipeshub_jobs"]  # created by db.bootstrap_roles
BACKEND = HERE.parents[3]


def _py(module: str, env: dict[str, str], *args: str) -> None:
    """Run a kernel tool (db.bootstrap_roles, db.migrate) in the interpreter that has the migration requirements."""
    py = os.environ.get("KERNEL_TEST_MIGRATE_PYTHON", sys.executable)
    r = subprocess.run([py, "-m", module, *args], cwd=BACKEND, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(BACKEND), **env})
    if r.returncode:
        raise RuntimeError(f"{module} failed:\n{r.stdout}\n{r.stderr}")


def _bins() -> list[str]:
    return [b for b in os.environ.get("KERNEL_TEST_PG_BINS", "").split(",") if b]


def _free_port(start: int) -> int:
    start += random.randrange(0, 1500)  # other test runs pick ports at the same time; a random start avoids the race
    for port in range(start, start + 500):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("no free port")


def _wait_port(port: int) -> None:
    for _ in range(100):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError(f"nothing listens on {port}")


@dataclass
class Cluster:
    bin: str
    root: Path
    port: int
    major: int

    def psql(self, db: str, sql: str | None = None, file: Path | None = None) -> None:
        args = [f"{self.bin}/psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-p", str(self.port), "-U", "postgres", "-d", db]
        args += ["-c", sql] if sql is not None else ["-f", str(file)]
        subprocess.run(args, check=True, capture_output=True, text=True)


@pytest.fixture(scope="session", params=_bins() or [None], ids=lambda b: Path(b).parent.name if b else "no-postgres")
def cluster(request, tmp_path_factory) -> Iterator[Cluster]:
    b = request.param
    if b is None or not os.environ.get("KERNEL_TEST_PGBOUNCER"):
        pytest.skip("set KERNEL_TEST_PG_BINS and KERNEL_TEST_PGBOUNCER")
    root = tmp_path_factory.mktemp("pg")
    port = _free_port(int(os.environ.get("KERNEL_TEST_PORT_BASE", "61000")))
    subprocess.run([f"{b}/initdb", "-D", str(root / "data"), "-U", "postgres", "--auth=trust", "-E", "UTF8"], check=True, capture_output=True)
    subprocess.run([f"{b}/pg_ctl", "-D", str(root / "data"), "-w", "-l", str(root / "log"), "-o",
                    f"-p {port} -k {root} -c listen_addresses=127.0.0.1 -c max_connections=200", "start"], check=True, capture_output=True)
    try:
        major = int(subprocess.run([f"{b}/postgres", "--version"], capture_output=True, text=True).stdout.split()[2].split(".")[0])
        c = Cluster(b, root, port, major)
        c.psql("postgres", "CREATE DATABASE tpl")
        _py("db.bootstrap_roles", {"PG_ADMIN_URL": f"postgresql://postgres@127.0.0.1:{port}/tpl", "PG_BOOTSTRAP_EXTENSIONS": "pg_trgm"})
        _py("db.migrate", {"PG_MIGRATE_URL": f"postgresql+psycopg://pipeshub_migrate@127.0.0.1:{port}/tpl"})
        c.psql("tpl", file=HERE / "fixture.sql")
        yield c
    finally:
        subprocess.run([f"{b}/pg_ctl", "-D", str(root / "data"), "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)


@dataclass
class Env:
    cluster: Cluster
    db: str
    start_bouncer: Callable[..., int]
    stop_bouncer: Callable[[int], None]

    def dsn(self, svc: str = "query", port: int | None = None) -> str:
        return f"postgresql://pipeshub_{svc}{'' if svc == 'jobs' else '_login'}@127.0.0.1:{port or self.cluster.port}/{self.db}"

    async def admin(self) -> asyncpg.Connection:
        return await asyncpg.connect(f"postgresql://postgres@127.0.0.1:{self.cluster.port}/{self.db}")


@pytest.fixture
def env(cluster, request, tmp_path) -> Iterator[Env]:
    name = f"t_{abs(hash(request.node.nodeid)) % 10**9}"
    cluster.psql("postgres", f"CREATE DATABASE {name} TEMPLATE tpl")
    # the bootstrap revokes TEMP from PUBLIC; PostgreSQL checks the session user first, then the current role
    cluster.psql(name, f"GRANT TEMPORARY ON DATABASE {name} TO pipeshub_indexing, pipeshub_connectors, pipeshub_query, "
                       "pipeshub_indexing_login, pipeshub_connectors_login, pipeshub_query_login")
    procs: dict[int, subprocess.Popen] = {}
    ports = iter(range(_free_port(63000), 65000))

    def start_bouncer(*, reset: bool = True, max_prepared: int = 200, pool_size: int = 2) -> int:
        port = next(ports)
        while socket.socket().connect_ex(("127.0.0.1", port)) == 0:
            port = next(ports)
        (tmp_path / "userlist.txt").write_text("".join(f'"{u}" ""\n' for u in LOGINS))
        ini = tmp_path / f"pgb{port}.ini"
        ini.write_text(f"""[databases]
* = host=127.0.0.1 port={cluster.port}
[pgbouncer]
listen_addr = 127.0.0.1
listen_port = {port}
unix_socket_dir =
pool_mode = transaction
auth_type = trust
auth_file = {tmp_path}/userlist.txt
{"server_reset_query = " + RESET if reset else "server_reset_query ="}
server_reset_query_always = {1 if reset else 0}
max_prepared_statements = {max_prepared}
max_client_conn = 500
default_pool_size = {pool_size}
ignore_startup_parameters = extra_float_digits
logfile = {tmp_path}/pgb{port}.log
pidfile = {tmp_path}/pgb{port}.pid
""")
        procs[port] = (subprocess.Popen([os.environ["KERNEL_TEST_PGBOUNCER"], str(ini)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        _wait_port(port)
        return port

    def stop_bouncer(port: int) -> None:  # kills our own process; its server connections (and their session state) go with it
        p = procs.pop(port)
        p.kill()
        p.wait(10)

    yield Env(cluster, name, start_bouncer, stop_bouncer)
    for port in list(procs):
        stop_bouncer(port)
    cluster.psql("postgres", f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
async def make(env: Env) -> AsyncIterator[Callable[..., Awaitable[KernelDB]]]:
    """Factory for started KernelDBs; all are closed afterwards."""
    started: list[KernelDB] = []

    async def build(svc: str = "query", port: int | None = None, **kw) -> KernelDB:
        db = KernelDB(DbConfig(service=svc, dsn=env.dsn(svc, port), **kw))
        await db.start()
        started.append(db)
        return db

    yield build
    for db in started:
        await db.close()
