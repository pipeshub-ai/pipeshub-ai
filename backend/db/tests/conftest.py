"""Integration fixtures: a throwaway PostgreSQL cluster per major version, started from local binaries.

``MIGRATE_TEST_PG_EXTERNAL=host:port`` uses an already running server instead (a CI service container): superuser
``postgres`` with trust authentication; the tests create and drop their own databases and ``pipeshub_*`` roles.
``MIGRATE_TEST_PG_BINS`` is a comma-separated list of PostgreSQL ``bin`` directories (default: the one on PATH).
Without binaries the integration tests skip. ``MIGRATE_TEST_PG_PORT_BASE`` (default 62000, above the kernel's ephemeral range) is where the free-port
search starts. Each test gets its own database; the cluster is stopped and deleted afterwards.
"""
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from db import bootstrap_roles

from .rls_fixture import FIXTURE_SQL, SEED_SQL, OrgDb, start_pgbouncer

BACKEND = Path(__file__).resolve().parents[2]
VETTED: list[str] = []
EXTENSIONS = ["pg_trgm"]      # the baseline extension; the administrator installs it
SURFACE_DIR = Path(__file__).parent
UPDATE_SURFACE = os.environ.get("UPDATE_SURFACE") == "1"


def _bins() -> list[str]:
    if os.environ.get("MIGRATE_TEST_PG_EXTERNAL"):
        return ["external"]
    raw = os.environ.get("MIGRATE_TEST_PG_BINS")
    if raw:
        return [b for b in raw.split(",") if b]
    found = shutil.which("initdb")
    return [str(Path(found).parent)] if found else []


def _free_port(start: int) -> int:
    for port in range(start, start + 200):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))          # bind, not connect: a port held by an outgoing connection is not free
            except OSError:
                continue
            return port
    raise RuntimeError("no free port")


class Cluster:
    def __init__(self, bin_dir: str, root: Path, port: int) -> None:
        self.bin, self.root, self.port = bin_dir, root, port
        with self.admin() as c:
            self.major = c.execute("SELECT current_setting('server_version_num')::int / 10000").fetchone()[0]

    def admin(self, db: str = "postgres") -> psycopg.Connection:
        return psycopg.connect(host="127.0.0.1", port=self.port, user="postgres", dbname=db, autocommit=True)

    def url(self, db: str, user: str) -> str:
        return f"postgresql+psycopg://{user}@127.0.0.1:{self.port}/{db}"


@pytest.fixture(scope="session", params=_bins() or [None], ids=lambda b: Path(b).parent.name if b and b != "external" else (b or "no-postgres"))
def cluster(request, tmp_path_factory):
    if request.param is None:
        pytest.skip("no PostgreSQL binaries (set MIGRATE_TEST_PG_BINS)")
    if request.param == "external":
        host, port = os.environ["MIGRATE_TEST_PG_EXTERNAL"].rsplit(":", 1)
        if host not in ("127.0.0.1", "localhost"):  # the fixtures drop databases and roles, and always connect to 127.0.0.1
            pytest.fail(f"MIGRATE_TEST_PG_EXTERNAL host {host!r} is not supported: the fixtures connect to 127.0.0.1")
        yield Cluster("", Path("."), int(port))
        return
    root = tmp_path_factory.mktemp("pg")
    port = _free_port(int(os.environ.get("MIGRATE_TEST_PG_PORT_BASE", "62000")))
    b = request.param
    subprocess.run([f"{b}/initdb", "-D", str(root / "data"), "-U", "postgres", "--auth=trust", "-E", "UTF8"],
                   check=True, capture_output=True)
    subprocess.run([f"{b}/pg_ctl", "-D", str(root / "data"), "-w", "-l", str(root / "log"), "-o",
                    f"-p {port} -k {root} -c listen_addresses=127.0.0.1", "start"], check=True, capture_output=True)
    try:
        yield Cluster(b, root, port)
    finally:
        subprocess.run([f"{b}/pg_ctl", "-D", str(root / "data"), "-m", "fast", "stop"], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)


def _drop_roles(admin):
    for (r,) in admin.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE 'pipeshub\\_%'").fetchall():
        admin.execute(f'DROP ROLE IF EXISTS "{r}"')


@pytest.fixture
def db(cluster, request):
    """A fresh database with the bootstrap roles in place; yields (cluster, name)."""
    name = f"t_{request.node.name.split('[')[0]}"[:50].lower()
    with cluster.admin() as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        _drop_roles(admin)
        admin.execute(f"CREATE DATABASE {name}")
    with cluster.admin(name) as admin:
        bootstrap_roles.bootstrap(admin, VETTED, extensions=EXTENSIONS)
    yield cluster, name
    with cluster.admin() as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        _drop_roles(admin)


@pytest.fixture
def rdb(db):
    """A fresh database migrated to the heads; yields (cluster, name)."""
    cluster, name = db
    r = run_migrate(cluster.url(name, "pipeshub_migrate"))
    assert r.returncode == 0, r.stdout
    return cluster, name


@pytest.fixture
def bare_db(cluster, request):
    """A fresh database and no roles at all (the migration creates what it needs)."""
    name = f"t_{request.node.name.split('[')[0]}"[:50].lower()
    with cluster.admin() as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        _drop_roles(admin)
        admin.execute(f"CREATE DATABASE {name}")
    yield cluster, name
    with cluster.admin() as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        _drop_roles(admin)


def run_migrate(url: str, *args: str, timeout: int = 120, **env) -> subprocess.CompletedProcess:
    e = {**os.environ, "PYTHONPATH": str(BACKEND), "PG_MIGRATE_URL": url, "PG_MIGRATE_RETRY_BASE_S": "0.1", **env}
    return subprocess.run([sys.executable, "-m", "db.migrate", *args], env=e, capture_output=True, text=True, timeout=timeout)


def start_migrate(url: str, **env) -> subprocess.Popen:
    e = {**os.environ, "PYTHONPATH": str(BACKEND), "PG_MIGRATE_URL": url, **env}
    return subprocess.Popen([sys.executable, "-m", "db.migrate"], env=e, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


@pytest.fixture(scope="module")
def org_db(cluster, tmp_path_factory):
    """Migrated to heads, fixture tables secured and seeded, PgBouncer (pool of 2) in front. One per cluster per module."""
    pgb = os.environ.get("MIGRATE_TEST_PGBOUNCER") or shutil.which("pgbouncer")
    if not pgb:
        pytest.skip("no pgbouncer (set MIGRATE_TEST_PGBOUNCER)")
    name = "t_rls"
    with cluster.admin() as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        _drop_roles(admin)
        admin.execute(f"CREATE DATABASE {name}")
    with cluster.admin(name) as admin:
        bootstrap_roles.bootstrap(admin, ["kt", *VETTED], extensions=EXTENSIONS)            # the fixture schema has vetted functions
    r = run_migrate(cluster.url(name, "pipeshub_migrate"), PG_MIGRATE_SURFACE_FILES=str(SURFACE_DIR / "surface.kt-roles.txt"),
                    PG_MIGRATE_SURFACE_CHECK="skip" if UPDATE_SURFACE else "on")
    assert r.returncode == 0, r.stdout + r.stderr
    with cluster.admin(name) as admin:
        admin.execute(FIXTURE_SQL)
        admin.execute(SEED_SQL)
    proc = start_pgbouncer(pgb, cluster, name, tmp_path_factory.mktemp("pgb"), _free_port(cluster.port + 300))
    t = OrgDb(cluster, name, proc.port)
    yield t
    proc.kill()
    proc.wait(timeout=10)
    with cluster.admin() as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        _drop_roles(admin)
