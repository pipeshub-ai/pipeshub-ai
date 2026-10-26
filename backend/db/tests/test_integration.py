"""Integration tests against real PostgreSQL (every version in MIGRATE_TEST_PG_BINS). See conftest.py."""
import textwrap
import time

import psycopg
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError

from db.lib import ddl

from .conftest import _free_port, run_migrate, start_migrate

MIGRATE = "pipeshub_migrate"


def connect(cluster, db, user="postgres"):
    return psycopg.connect(host="127.0.0.1", port=cluster.port, user=user, dbname=db, autocommit=True)


def epochs(cluster, db):
    with connect(cluster, db) as c:
        return {b: (e, m) for b, e, m in c.execute("SELECT branch, epoch, min_code_epoch FROM kernel.schema_epoch")}


def _applied(cluster, name):
    """What a run leaves behind: the revisions in the version table and the module epochs."""
    with connect(cluster, name) as c:
        if not c.execute("SELECT to_regclass('kernel.alembic_version') IS NOT NULL").fetchone()[0]:
            return None, None
        revisions = sorted(v for (v,) in c.execute("SELECT version_num FROM kernel.alembic_version"))
        has_epochs = c.execute("SELECT to_regclass('kernel.schema_epoch') IS NOT NULL").fetchone()[0]
    return revisions, (epochs(cluster, name) if has_epochs else None)


def edition_with(tmp_path, *module_dirs):
    ed = tmp_path / "edition.py"
    ed.write_text(f"EXTRA_MIGRATION_LOCATIONS = {[str(d) for d in module_dirs]!r}\n")
    return {"PIPESHUB_EDITION_FILE": str(ed), "PG_MIGRATE_SURFACE_CHECK": "skip"}      # the probe module ships no surface file


def write_probe(tmp_path, second_sql="CREATE TABLE probe.t2 (id int);\nSELECT kernel.register_internal('probe.t2')", third=False):
    d = tmp_path / "probe"
    d.mkdir(exist_ok=True)
    py = textwrap.dedent('''
        from alembic import op
        from db.lib.run_sql import run_sql
        revision = "probe_{n:04d}"
        down_revision = {down!r}
        branch_labels = {labels!r}
        depends_on = {dep!r}
        {extra}
        def upgrade():
            run_sql(__file__)
        def downgrade():
            {down_sql}
    ''')
    spec = [(1, None, ("probe",), "kernel_0010", "", "CREATE SCHEMA probe;\nCREATE TABLE probe.t1 (id int, note text DEFAULT 'a%b:c');\nSELECT kernel.register_internal('probe.t1');"),
            (2, "probe_0001", None, None, "", second_sql)]
    if third:
        spec.append((3, "probe_0002", None, None, "min_code_epoch = 3", "ALTER TABLE probe.t1 ADD COLUMN extra int"))
    for n, down, labels, dep, extra, sql in spec:
        (d / f"{n:04d}_p.py").write_text(py.format(n=n, down=down, labels=labels, dep=dep, extra=extra,
                                                    down_sql='op.execute("DROP SCHEMA probe CASCADE")' if n == 1 else "pass"))
        (d / f"{n:04d}_p.sql").write_text(sql)
    return d


# the modules besides the kernel: platform (the store fence, background jobs and the runtime tables)
MODULES = ("platform",)
HEADS = {"kernel": (15, 11), "platform": (4, 0)}
# platform depends on the kernel's last revision, so only platform's is a head
REVISION_HEADS = {"kernel_0015", "platform_0004"}


def test_fresh_install(db):
    cluster, name = db
    r = run_migrate(cluster.url(name, MIGRATE))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "epoch kernel=15 min_code=11" in r.stdout and "epoch platform=4 min_code=0" in r.stdout
    assert epochs(cluster, name) == HEADS
    with connect(cluster, name) as c:
        assert {n for (n,) in c.execute("SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname IN ('kernel','platform')")} \
            == {"pipeshub_owner"}
        assert c.execute("SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'kernel'").fetchone()[0] == "pipeshub_owner"
        assert c.execute("SELECT kernel.pg_major()").fetchone()[0] == cluster.major
        assert c.execute("SELECT kernel.has_extension('plpgsql'), kernel.has_extension('nope')").fetchone() == (True, False)
        assert {v for (v,) in c.execute("SELECT version_num FROM kernel.alembic_version")} \
            == REVISION_HEADS


def test_service_role_grants(db):
    cluster, name = db
    assert run_migrate(cluster.url(name, MIGRATE)).returncode == 0
    with connect(cluster, name, "pipeshub_node_login") as c:
        c.execute("SET ROLE pipeshub_node")
        assert c.execute("SELECT epoch FROM kernel.schema_epoch WHERE branch = 'kernel'").fetchone()[0] == 15
        c.execute("SELECT kernel.assert_profile_epoch(1)")
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="expects 2"):
            c.execute("SELECT kernel.assert_profile_epoch(2)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT kernel.set_epoch('x', 9, 9)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("CREATE TABLE kernel.nope (a int)")


def test_require_job_done(db):
    cluster, name = db
    assert run_migrate(cluster.url(name, MIGRATE)).returncode == 0
    with connect(cluster, name, MIGRATE) as c:
        c.execute("SET ROLE pipeshub_owner")
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match='backfill job "b1" has not completed'):
            c.execute("SELECT kernel.require_job_done('b1')")
        c.execute("INSERT INTO kernel.completed_jobs (name) VALUES ('b1')")
        c.execute("SELECT kernel.require_job_done('b1')")


def test_rerun_is_a_noop(db):
    cluster, name = db
    url = cluster.url(name, MIGRATE)
    assert run_migrate(url).returncode == 0
    with connect(cluster, name) as c:
        before = c.execute("SELECT * FROM kernel.schema_epoch").fetchall(), c.execute("SELECT * FROM kernel.alembic_version").fetchall()
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert (c.execute("SELECT * FROM kernel.schema_epoch").fetchall(), c.execute("SELECT * FROM kernel.alembic_version").fetchall()) == before


def test_bare_database_as_superuser(bare_db):
    cluster, name = bare_db
    url = cluster.url(name, "postgres")
    skip = {"PG_MIGRATE_SURFACE_CHECK": "skip"}                   # a bare database has a different (migration-made) role layout
    assert run_migrate(url, **skip).returncode == 0
    assert run_migrate(url, **skip).returncode == 0                                            # second run: ownership is consistent
    assert epochs(cluster, name) == HEADS
    with connect(cluster, name) as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'pipeshub\\_%' AND NOT rolcanlogin").fetchone()[0] == 14
        assert c.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'kernel' AND tableowner <> 'pipeshub_owner'").fetchone()[0] == 0
        assert c.execute("SELECT NOT has_database_privilege('pipeshub_query', current_database(), 'TEMP')").fetchone()[0]


def test_concurrent_runners_serialise(db):
    cluster, name = db
    url = cluster.url(name, MIGRATE)
    holder = connect(cluster, name, MIGRATE)
    holder.execute("SELECT pg_advisory_lock(hashtext('pipeshub:migrate'))")
    p = start_migrate(url)
    try:
        for _ in range(100):                                                           # it must be parked on the lock
            with connect(cluster, name) as c:
                waiting = c.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted").fetchone()[0]
            if waiting:
                break
            time.sleep(0.1)
        assert waiting == 1 and p.poll() is None
        with connect(cluster, name) as c:
            assert c.execute("SELECT to_regclass('kernel.schema_epoch')").fetchone()[0] is None
    finally:
        holder.close()
    out, _ = p.communicate(timeout=120)
    assert p.returncode == 0, out
    assert epochs(cluster, name) == HEADS


def test_two_runners_at_once_apply_once(db):
    cluster, name = db
    url = cluster.url(name, MIGRATE)
    procs = [start_migrate(url), start_migrate(url)]
    outs = [p.communicate(timeout=120)[0] for p in procs]
    assert [p.returncode for p in procs] == [0, 0], outs
    assert epochs(cluster, name) == HEADS
    with connect(cluster, name) as c:
        assert c.execute("SELECT count(*) FROM platform.deployment_profile").fetchone()[0] == 1


def test_lock_wait_times_out_with_exit_3(db):
    cluster, name = db
    holder = connect(cluster, name, MIGRATE)
    holder.execute("SELECT pg_advisory_lock(hashtext('pipeshub:migrate'))")
    try:
        r = run_migrate(cluster.url(name, MIGRATE), PG_MIGRATE_LOCK_TIMEOUT_S="1")
    finally:
        holder.close()
    assert r.returncode == 3 and "another migration run held the lock" in r.stdout


def test_configuration_errors_exit_2(db):
    cluster, name = db
    assert run_migrate("").returncode == 2
    assert run_migrate(f"postgresql+psycopg://{MIGRATE}@127.0.0.1:1/{name}").returncode == 2
    r = run_migrate(cluster.url(name, MIGRATE), "downgrade", "kernel_0001")
    assert r.returncode == 2 and "development only" in r.stdout


def test_failed_revision_rolls_back_cleanly(db, tmp_path):
    cluster, name = db
    probe = write_probe(tmp_path, second_sql="CREATE TABLE probe.t2 (id int);\nSELECT 1/0;")
    url, env = cluster.url(name, MIGRATE), edition_with(tmp_path, probe)
    r = run_migrate(url, **env)
    assert r.returncode == 1 and "DivisionByZero" in r.stdout
    assert "postgresql+psycopg://" not in r.stdout                                      # the DSN is never printed
    with connect(cluster, name) as c:
        assert c.execute("SELECT to_regclass('probe.t1') IS NOT NULL, to_regclass('probe.t2') IS NULL").fetchone() == (True, True)
        assert c.execute("SELECT note FROM probe.t1 LIMIT 1").fetchone() is None
        versions = {v for (v,) in c.execute("SELECT version_num FROM kernel.alembic_version")}
        # the probe fails mid-run; the branches that ran before it (in an order Alembic does not fix) are complete revisions, never partial
        assert "probe_0001" in versions and all(v == "probe_0001" or v.startswith(("kernel_", *(f"{b}_" for b in MODULES))) for v in versions)
    assert epochs(cluster, name)["probe"] == (1, 0)
    write_probe(tmp_path)                                                              # the fix is a plain re-run
    r = run_migrate(url, **env)
    assert r.returncode == 0, r.stdout
    assert epochs(cluster, name)["probe"] == (2, 0)
    with connect(cluster, name) as c:
        assert c.execute("SELECT to_regclass('probe.t2') IS NOT NULL").fetchone()[0]


def test_contract_epochs_and_dev_downgrade(db, tmp_path):
    cluster, name = db
    probe = write_probe(tmp_path, third=True)
    url, env = cluster.url(name, MIGRATE), edition_with(tmp_path, probe)
    assert run_migrate(url, **env).returncode == 0
    assert epochs(cluster, name) == {**HEADS, "probe": (3, 3)}
    r = run_migrate(url, "downgrade", "probe_0002", PG_MIGRATE_ALLOW_DOWNGRADE="1", **env)
    assert r.returncode == 0, r.stdout
    assert epochs(cluster, name)["probe"] == (2, 3)                                    # min_code_epoch stays: a contract stays one
    r = run_migrate(url, "downgrade", "probe@base", PG_MIGRATE_ALLOW_DOWNGRADE="1", **env)
    assert r.returncode == 0 and "probe" not in epochs(cluster, name), r.stdout
    r = run_migrate(url, "downgrade", "kernel_0001", PG_MIGRATE_ALLOW_DOWNGRADE="1", **env)
    assert r.returncode == 0, r.stdout
    assert epochs(cluster, name) == {"kernel": (1, 11)}                      # the contract stays
    assert run_migrate(url, **env).returncode == 0                                     # and forward again
    assert epochs(cluster, name)["kernel"] == (15, 11)


def test_one_module_down_and_up_leaves_a_clean_catalog(db):
    cluster, name = db
    url = cluster.url(name, MIGRATE)
    assert run_migrate(url).returncode == 0
    for down in ("platform_0002", "platform@base"):                                          # registry rows must go with their tables
        r = run_migrate(url, "downgrade", down, PG_MIGRATE_ALLOW_DOWNGRADE="1")
        assert r.returncode == 0, r.stdout
        with connect(cluster, name) as c:
            assert c.execute("SELECT count(*) FROM kernel.rls_registry WHERE rel::oid NOT IN (SELECT oid FROM pg_class)").fetchone()[0] == 0
            assert c.execute("SELECT count(*) FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)").fetchone()[0] == 0
            assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    assert epochs(cluster, name) == HEADS


def test_violations_in_the_catalog_view_fail_the_run(db):
    cluster, name = db
    url = cluster.url(name, MIGRATE)
    assert run_migrate(url).returncode == 0
    with connect(cluster, name) as c:                                                  # a table with org_id nobody registered
        c.execute("SET ROLE pipeshub_owner")
        c.execute("CREATE SCHEMA loose; CREATE TABLE loose.t (org_id text NOT NULL, x int)")
    r = run_migrate(url)
    assert r.returncode == 4 and "VIOLATION" in r.stdout and "01 unregistered" in r.stdout


# ---- lib.ddl ---------------------------------------------------------------------------------

@pytest.fixture
def autocommit(db):
    cluster, name = db
    assert run_migrate(cluster.url(name, MIGRATE)).returncode == 0
    with connect(cluster, name) as c:
        c.execute("CREATE SCHEMA d AUTHORIZATION pipeshub_owner")
    engine = create_engine(cluster.url(name, MIGRATE), isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.exec_driver_sql("SET ROLE pipeshub_owner")
        yield cluster, name, conn


def test_ensure_index_rebuilds_an_invalid_index(autocommit):
    cluster, name, conn = autocommit
    conn.exec_driver_sql("CREATE TABLE d.ih (org_id text, id text, k text, PRIMARY KEY (org_id, id))")
    conn.exec_driver_sql("INSERT INTO d.ih VALUES ('a','1','dup'), ('a','2','dup'), ('a','3','x')")
    create = "CREATE UNIQUE INDEX CONCURRENTLY ih_k ON d.ih (org_id, k)"
    with pytest.raises(DBAPIError):
        conn.exec_driver_sql(create)
    assert ddl._index_valid(conn, "d.ih_k") is False                                   # the failed build left it behind
    conn.exec_driver_sql("DELETE FROM d.ih WHERE id = '2'")
    conn.exec_driver_sql("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ih_k ON d.ih (org_id, k)")
    assert ddl._index_valid(conn, "d.ih_k") is False                                   # the naive retry keeps it invalid
    ddl.ensure_index(conn, "d.ih_k", create)
    assert ddl._index_valid(conn, "d.ih_k") is True
    ddl.ensure_index(conn, "d.ih_k", create)                                           # idempotent


def test_ensure_partitioned_index_on_a_nested_tree(autocommit):
    cluster, name, conn = autocommit
    conn.exec_driver_sql("""
      CREATE TABLE d.p (org_id text NOT NULL, ts date NOT NULL, k text) PARTITION BY LIST (org_id);
      CREATE TABLE d.p_a PARTITION OF d.p FOR VALUES IN ('a') PARTITION BY RANGE (ts);
      CREATE TABLE d.p_a_1 PARTITION OF d.p_a FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
      CREATE TABLE d.p_a_2 PARTITION OF d.p_a FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
      CREATE TABLE d.p_b PARTITION OF d.p FOR VALUES IN ('b')""")
    with pytest.raises(DBAPIError, match="partitioned"):
        conn.exec_driver_sql("CREATE INDEX CONCURRENTLY x ON d.p (org_id, k)")
    ddl.ensure_partitioned_index(conn, "d.p", "p_k", "org_id, k")
    ddl.ensure_partitioned_index(conn, "d.p", "p_k", "org_id, k")                      # idempotent
    rows = conn.exec_driver_sql("SELECT indexrelid::regclass::text, indisvalid FROM pg_index WHERE indexrelid::regclass::text LIKE 'd.%%p%%k'").all()
    assert len(rows) == 5 and all(v for _, v in rows)                                  # root, p_a, p_b, two leaves


def test_ensure_partitioned_index_can_be_partial(autocommit):
    cluster, name, conn = autocommit
    conn.exec_driver_sql("""
      CREATE TABLE d.q (org_id text NOT NULL, kind text, k text) PARTITION BY LIST (org_id);
      CREATE TABLE d.q_a PARTITION OF d.q FOR VALUES IN ('a');
      CREATE TABLE d.q_b PARTITION OF d.q DEFAULT""")
    conn.exec_driver_sql("INSERT INTO d.q_a VALUES ('a', 'x', 'k1')")                                 # one leaf holds a row (built concurrently), the other is empty
    ddl.ensure_partitioned_index(conn, "d.q", "q_k", "org_id, k", where="kind = 'x'")
    rows = conn.exec_driver_sql("SELECT pg_get_indexdef(indexrelid), indisvalid FROM pg_index WHERE indexrelid::regclass::text LIKE 'd.%%q%%k'").all()
    assert len(rows) == 3 and all(v and "WHERE (kind = 'x'::text)" in d for d, v in rows)           # root and both leaves


def test_ensure_months_creates_the_coming_months_and_drops_the_old_ones(autocommit):
    from datetime import date
    cluster, name, conn = autocommit
    conn.exec_driver_sql("""
      CREATE TABLE d.ev (org_id text NOT NULL, created_at timestamptz NOT NULL, v int) PARTITION BY RANGE (created_at);
      CREATE TABLE d.ev_y2025m01 PARTITION OF d.ev FOR VALUES FROM ('2025-01-01') TO ('2025-02-01')""")
    conn.exec_driver_sql("SELECT kernel.apply_rls('d.ev', 'org')")
    made, dropped = ddl.ensure_months(conn, "d.ev", 2, 3, today=date(2026, 11, 15))
    assert made == ["d.ev_y2026m10", "d.ev_y2026m11", "d.ev_y2026m12", "d.ev_y2027m01"] and dropped == ["d.ev_y2025m01"]
    assert ddl.ensure_months(conn, "d.ev", 2, 3, today=date(2026, 11, 15)) == ([], [])                  # idempotent
    assert conn.exec_driver_sql("SELECT count(*) FROM pg_inherits WHERE inhparent = 'd.ev'::regclass").scalar() == 4
    assert conn.exec_driver_sql("SELECT bool_and(relforcerowsecurity) FROM pg_class WHERE oid = 'd.ev_y2027m01'::regclass").scalar()


def test_add_partition_does_not_wait_for_a_reader(autocommit):
    cluster, name, conn = autocommit
    conn.exec_driver_sql("""
      CREATE TABLE d.ev (org_id text NOT NULL, ts timestamptz NOT NULL, v int) PARTITION BY RANGE (ts);
      CREATE TABLE d.ev_2026_01 PARTITION OF d.ev FOR VALUES FROM ('2026-01-01') TO ('2026-02-01')""")
    conn.exec_driver_sql("SELECT kernel.apply_rls('d.ev', 'org')")
    reader = connect(cluster, name, "postgres")
    reader.autocommit = False
    reader.execute("SELECT count(*) FROM d.ev")                                        # ACCESS SHARE on the tree, held open
    try:
        t = time.monotonic()
        ddl.add_partition(conn, "d.ev", "d.ev_2026_02", "FROM ('2026-02-01') TO ('2026-03-01')", "ts >= '2026-02-01' AND ts < '2026-03-01'")
        assert time.monotonic() - t < 2
    finally:
        reader.close()
    assert conn.exec_driver_sql("SELECT count(*) FROM pg_inherits WHERE inhparent = 'd.ev'::regclass").scalar() == 2
    assert conn.exec_driver_sql("SELECT count(*) FROM pg_constraint WHERE conrelid = 'd.ev_2026_02'::regclass AND conname = 'ev_2026_02_bounds'").scalar() == 0
    assert conn.exec_driver_sql("SELECT relforcerowsecurity, relowner::regrole::text FROM pg_class WHERE oid = 'd.ev_2026_02'::regclass").one() \
        == (True, "pipeshub_owner")                                                      # secured before it was attached, owned by the owner
    assert conn.exec_driver_sql("SELECT count(*) FROM kernel.rls_violations").scalar() == 0
    ddl.add_partition(conn, "d.ev", "d.ev_2026_02", "FROM ('2026-02-01') TO ('2026-03-01')", "ts >= '2026-02-01' AND ts < '2026-03-01'")  # idempotent


def test_add_partition_secures_a_leaf_that_an_interrupted_run_left_unattached(autocommit):
    cluster, name, conn = autocommit
    conn.exec_driver_sql("""
      CREATE TABLE d.ev (org_id text NOT NULL, ts timestamptz NOT NULL, v int) PARTITION BY RANGE (ts);
      CREATE TABLE d.ev_2026_01 PARTITION OF d.ev FOR VALUES FROM ('2026-01-01') TO ('2026-02-01')""")
    conn.exec_driver_sql("SELECT kernel.apply_rls('d.ev', 'org')")
    conn.exec_driver_sql("CREATE TABLE d.ev_2026_02 (LIKE d.ev INCLUDING ALL EXCLUDING IDENTITY)")    # the run stopped right here
    assert not conn.exec_driver_sql("SELECT relforcerowsecurity FROM pg_class WHERE oid = 'd.ev_2026_02'::regclass").scalar()
    ddl.add_partition(conn, "d.ev", "d.ev_2026_02", "FROM ('2026-02-01') TO ('2026-03-01')", "ts >= '2026-02-01' AND ts < '2026-03-01'")
    assert conn.exec_driver_sql("SELECT count(*) FROM pg_inherits WHERE inhparent = 'd.ev'::regclass").scalar() == 2
    assert conn.exec_driver_sql("SELECT relforcerowsecurity FROM pg_class WHERE oid = 'd.ev_2026_02'::regclass").scalar()   # secured before it joined
    assert conn.exec_driver_sql("SELECT count(*) FROM kernel.rls_violations").scalar() == 0


# ---- real PgBouncer (set MIGRATE_TEST_PGBOUNCER to the binary) ------------------------------------------------------

@pytest.mark.parametrize("pool_size", [1, 8])
def test_pooler_is_refused(db, tmp_path, pool_size):
    import os
    import shutil
    import subprocess
    pgb = os.environ.get("MIGRATE_TEST_PGBOUNCER") or shutil.which("pgbouncer")
    if not pgb:
        pytest.skip("no pgbouncer (set MIGRATE_TEST_PGBOUNCER)")
    cluster, name = db
    port = _free_port(cluster.port + 100)
    (tmp_path / "users.txt").write_text(f'"{MIGRATE}" ""\n')
    (tmp_path / "pgb.ini").write_text(f"""[databases]
{name} = host=127.0.0.1 port={cluster.port} dbname={name}
[pgbouncer]
listen_addr = 127.0.0.1
listen_port = {port}
unix_socket_dir =
pool_mode = transaction
auth_type = trust
auth_file = {tmp_path}/users.txt
server_reset_query = RESET ALL; SET SESSION AUTHORIZATION DEFAULT; CLOSE ALL; DISCARD TEMP; UNLISTEN *; SELECT pg_advisory_unlock_all()
server_reset_query_always = 1
default_pool_size = {pool_size}
ignore_startup_parameters = extra_float_digits,options
pidfile = {tmp_path}/pgb.pid
logfile = {tmp_path}/pgb.log
""")
    p = subprocess.Popen([pgb, str(tmp_path / "pgb.ini")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):
            try:
                psycopg.connect(host="127.0.0.1", port=port, user=MIGRATE, dbname=name).close()
                break
            except psycopg.Error:
                time.sleep(0.1)
        r = run_migrate(f"postgresql+psycopg://{MIGRATE}@127.0.0.1:{port}/{name}")
        assert r.returncode == 2 and "connection pooler" in r.stdout, r.stdout
        with connect(cluster, name) as c:                                             # and nothing was applied
            assert c.execute("SELECT to_regclass('kernel.schema_epoch')").fetchone()[0] is None
    finally:
        p.terminate()
        p.wait(timeout=10)


# ---- a killed runner does not leave its work running --------------------------------------------------------

def test_killed_runner_backend_is_cancelled(db, tmp_path):
    cluster, name = db
    d = write_probe(tmp_path, second_sql="SELECT pg_sleep(120)")
    p = start_migrate(cluster.url(name, MIGRATE), **edition_with(tmp_path, d))
    try:
        for _ in range(300):
            with connect(cluster, name) as c:
                n = c.execute("SELECT count(*) FROM pg_stat_activity WHERE query LIKE 'SELECT pg_sleep%' AND pid <> pg_backend_pid()").fetchone()[0]
            if n:
                break
            time.sleep(0.1)
        assert n == 1
        p.kill()
        p.wait()
        for _ in range(100):                                                         # client_connection_check_interval = 5s
            with connect(cluster, name) as c:
                n = c.execute("SELECT count(*) FROM pg_stat_activity WHERE query LIKE 'SELECT pg_sleep%' AND pid <> pg_backend_pid()").fetchone()[0]
                locks = c.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'").fetchone()[0]
            if not n:
                break
            time.sleep(0.2)
        assert n == 0 and locks == 0
    finally:
        p.kill()


def test_profile_fence_is_volatile_read_committed_and_callable_by_the_login(db):
    cluster, name = db
    assert run_migrate(cluster.url(name, MIGRATE)).returncode == 0
    with connect(cluster, name, "pipeshub_node_login") as c:
        c.autocommit = False
        # the fence is SECURITY DEFINER: the login runs it before any role switch and holds no privilege on the table it reads
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="deployment_profile"):
            c.execute("SELECT epoch FROM platform.deployment_profile")
        c.rollback()
        c.execute("SELECT kernel.assert_profile_epoch(1)")
        c.rollback()
        c.execute("SELECT set_config('role', 'pipeshub_node', true)")
        c.execute("SELECT kernel.assert_profile_epoch(1)")
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):               # not planned away although its result is unused
            c.execute("SELECT 1 FROM (SELECT kernel.assert_profile_epoch(2) AS x) s")
        c.rollback()
    with connect(cluster, name, "pipeshub_node_login") as c:
        c.autocommit = False
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        c.execute("SELECT set_config('role', 'pipeshub_node', true)")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            c.execute("SELECT kernel.assert_profile_epoch(1)")
        c.rollback()
    with connect(cluster, name) as c:
        assert c.execute("SELECT provolatile FROM pg_proc WHERE oid = 'kernel.assert_profile_epoch(int)'::regprocedure").fetchone()[0] == "v"
        grants = {(r[0], r[1]) for r in c.execute(
            "SELECT grantee::regrole::text, privilege_type FROM information_schema.role_usage_grants WHERE false UNION ALL "
            "SELECT a.grantee::regrole::text, a.privilege_type FROM pg_namespace n, aclexplode(n.nspacl) a WHERE n.nspname = 'kernel' AND a.grantee <> n.nspowner")}
        assert {("pipeshub_query_login", "USAGE"), ("pipeshub_node_login", "USAGE")} <= grants


def test_a_downgrade_to_the_previous_revision_leaves_a_clean_catalog_and_upgrades_again(db):
    cluster, name = db
    url = cluster.url(name, MIGRATE)
    assert run_migrate(url).returncode == 0
    r = run_migrate(url, "downgrade", "kernel_0010", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT to_regclass('platform.jobs')").fetchone()[0] is None
        assert c.execute("SELECT count(*) FROM kernel.rls_registry WHERE rel::oid NOT IN (SELECT oid FROM pg_class)").fetchone()[0] == 0
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    assert epochs(cluster, name) == HEADS


def test_the_one_running_job_index_comes_and_goes_with_the_platform_job_functions(db):
    """A database that is already past kernel_0011 gets the index from platform_0002, and a downgrade of it takes the index away again."""
    cluster, name = db
    url = cluster.url(name, MIGRATE)
    index = "SELECT to_regclass('platform.jobs_one_running_per_kind') IS NOT NULL"
    assert run_migrate(url).returncode == 0
    with connect(cluster, name) as c:
        assert c.execute(index).fetchone()[0] is True
    r = run_migrate(url, "downgrade", "platform_0001", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute(index).fetchone()[0] is False and c.execute("SELECT to_regclass('platform.jobs') IS NOT NULL").fetchone()[0] is True
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    assert epochs(cluster, name) == HEADS
    with connect(cluster, name) as c:
        assert c.execute(index).fetchone()[0] is True


def test_the_runtime_tables_come_and_go_with_platform_0003_and_0004(db):
    """The key-value, queue and scheduler tables are internal tables the four service roles may use; a downgrade takes them and their registry rows away."""
    cluster, name = db
    url = cluster.url(name, MIGRATE)
    tables = ("kv_entries", "queue_messages", "queue_groups", "queue_deliveries", "sched_jobs", "sched_repeats")
    assert run_migrate(url).returncode == 0
    with connect(cluster, name) as c:
        for t in tables:
            assert c.execute("SELECT class FROM kernel.rls_registry WHERE rel = %s::regclass", (f"platform.{t}",)).fetchone()[0] == "internal"
            who = {  # who may use each table: the Node API everything; the Python services the key-value and queue tables; the config-only role just key-value
                "sched_jobs": {"pipeshub_node"}, "sched_repeats": {"pipeshub_node"},
                "kv_entries": {"pipeshub_node", "pipeshub_connectors", "pipeshub_indexing", "pipeshub_query", "pipeshub_kv"},
            }.get(t, {"pipeshub_node", "pipeshub_connectors", "pipeshub_indexing", "pipeshub_query"})
            for role in ("pipeshub_node", "pipeshub_connectors", "pipeshub_indexing", "pipeshub_query", "pipeshub_kv", "pipeshub_jobs"):
                got = c.execute("SELECT has_table_privilege(%s, %s, 'SELECT') OR has_table_privilege(%s, %s, 'INSERT')",
                                (role, f"platform.{t}", role, f"platform.{t}")).fetchone()[0]
                assert got is (role in who), (t, role)
    r = run_migrate(url, "downgrade", "platform_0002", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert all(c.execute("SELECT to_regclass(%s)", (f"platform.{t}",)).fetchone()[0] is None for t in tables)
        assert c.execute("SELECT count(*) FROM kernel.rls_registry WHERE rel::oid NOT IN (SELECT oid FROM pg_class)").fetchone()[0] == 0
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    assert epochs(cluster, name) == HEADS


def test_the_runtimes_read_the_profile_epoch_through_the_definer_reader(db):
    cluster, name = db
    assert run_migrate(cluster.url(name, MIGRATE)).returncode == 0
    for login, role in (("pipeshub_node_login", "pipeshub_node"), ("pipeshub_query_login", "pipeshub_query")):
        with connect(cluster, name, login) as c:
            c.autocommit = False
            c.execute("SELECT set_config('role', %s, true)", (role,))
            assert c.execute("SELECT kernel.profile_epoch()").fetchone()[0] == 1
            with pytest.raises(psycopg.errors.InsufficientPrivilege):                 # and still not the table
                c.execute("SELECT epoch FROM platform.deployment_profile")
            c.rollback()
    with connect(cluster, name, "pipeshub_jobs") as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("SELECT kernel.profile_epoch()")                                    # reads are for the services
    with connect(cluster, name) as c:
        assert c.execute("SELECT provolatile, prosecdef, proconfig[1] FROM pg_proc WHERE oid = 'kernel.profile_epoch()'::regprocedure").fetchone() \
            == ("s", True, "search_path=pg_catalog, kernel, pg_temp")


def test_the_fence_is_a_barrier_not_a_check(db):
    """A writer holds the shared key to commit; the switch takes it exclusively and waits; later writers are refused (25006)."""
    cluster, name = db
    assert run_migrate(cluster.url(name, MIGRATE)).returncode == 0
    with connect(cluster, name, "pipeshub_node_login") as w, connect(cluster, name) as admin:
        w.autocommit = False
        w.execute("SELECT kernel.assert_profile_epoch(1)")                                # a writer in flight
        admin.autocommit = False
        got = admin.execute("SELECT pg_try_advisory_xact_lock(7341, 1)").fetchone()[0]
        assert got is False                                                               # the switch must wait for the writer
        admin.rollback()
        w.rollback()
        admin.execute("SELECT pg_advisory_xact_lock(7341, 1)")                            # the switch holds the key exclusively
        with connect(cluster, name, "pipeshub_node_login") as late:
            late.autocommit = False
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction, match="being switched"):
                late.execute("SELECT kernel.assert_profile_epoch(1)")
        admin.rollback()


def test_an_extra_location_extends_the_allowlist_with_its_own_file(db, tmp_path):
    cluster, name = db
    with cluster.admin() as admin:
        admin.execute("CREATE ROLE pipeshub_ext_reader NOLOGIN")
    d = tmp_path / "ext"
    d.mkdir()
    (d / "0001_grant.py").write_text(textwrap.dedent('''
        from db.lib.run_sql import run_sql
        revision = "ext_0001"
        down_revision = None
        branch_labels = ("ext",)
        depends_on = "kernel_0010"
        def upgrade():
            run_sql(__file__)
        def downgrade():
            pass
    '''))
    (d / "0001_grant.sql").write_text("GRANT USAGE ON SCHEMA kernel TO pipeshub_ext_reader;\n")
    ed = tmp_path / "edition.py"
    ed.write_text(f"EXTRA_MIGRATION_LOCATIONS = {[str(d)]!r}\n")
    env = {"PIPESHUB_EDITION_FILE": str(ed)}
    url = cluster.url(name, MIGRATE)
    r = run_migrate(url, **env)                                     # not listed: the snapshot refuses it
    assert r.returncode == 4 and "50 surface: unexpected" in r.stdout and "pipeshub_ext_reader" in r.stdout, r.stdout
    items = [line.split(", '", 1)[1].rstrip(")'") for line in r.stdout.splitlines() if "50 surface: unexpected" in line]
    assert len(items) == 2                                          # the role and the grant, nothing else
    (d / "allowlist.yaml").write_text("surface:\n" + "".join(f'  - "{i}"\n' for i in items))
    r = run_migrate(url, **env)                                     # listed: accepted
    assert r.returncode == 0, r.stdout
    (d / "allowlist.yaml").write_text("surface:\n" + f'  - "{items[0]}"\n')      # only half listed: still refused
    assert run_migrate(url, **env).returncode == 4


def _grant_revision(tmp_path):
    """An extra module whose only effect is a grant the surface snapshot does not list: a surface violation."""
    d = tmp_path / "ext"
    d.mkdir()
    (d / "0001_grant.py").write_text(textwrap.dedent('''
        from db.lib.run_sql import run_sql
        revision = "ext_0001"
        down_revision = None
        branch_labels = ("ext",)
        depends_on = "kernel_0010"
        def upgrade():
            run_sql(__file__)
        def downgrade():
            pass
    '''))
    (d / "0001_grant.sql").write_text("GRANT USAGE ON SCHEMA kernel TO pipeshub_ext_reader;\n")
    ed = tmp_path / "edition.py"
    ed.write_text(f"EXTRA_MIGRATION_LOCATIONS = {[str(d)]!r}\n")
    return {"PIPESHUB_EDITION_FILE": str(ed)}


def test_a_refused_upgrade_leaves_the_revisions_and_epochs_as_they_were(db, tmp_path):
    cluster, name = db
    url = cluster.url(name, MIGRATE)
    assert run_migrate(url).returncode == 0
    before = _applied(cluster, name)
    assert before[0] and before[1] == HEADS
    with cluster.admin() as admin:
        admin.execute("CREATE ROLE pipeshub_ext_reader NOLOGIN")
    r = run_migrate(url, **_grant_revision(tmp_path))
    assert r.returncode == 4 and "50 surface: unexpected" in r.stdout, r.stdout
    assert _applied(cluster, name) == before                                         # no ext_0001, no ext epoch
    with connect(cluster, name) as c:
        assert c.execute("SELECT has_schema_privilege('pipeshub_ext_reader', 'kernel', 'USAGE')").fetchone()[0] is False


def test_a_first_run_on_an_empty_database_commits_revision_by_revision(db, tmp_path):
    """Every revision in one transaction holds ~6,600 locks, more than the table a default server sizes: an empty database
    (nothing to protect) is built revision by revision and checked at the end, as before."""
    cluster, name = db
    with cluster.admin() as admin:
        admin.execute("CREATE ROLE pipeshub_ext_reader NOLOGIN")
    r = run_migrate(cluster.url(name, MIGRATE), **_grant_revision(tmp_path))
    assert r.returncode == 4 and "50 surface: unexpected" in r.stdout, r.stdout
    revisions, heads = _applied(cluster, name)
    assert "ext_0001" in revisions and heads["ext"] == (1, 0)


def test_skipping_the_surface_check_is_loud_and_remembered(db):
    cluster, name = db
    url = cluster.url(name, MIGRATE)
    assert run_migrate(url).returncode == 0
    with connect(cluster, name) as c:
        c.execute("ALTER ROLE pipeshub_node_login CONNECTION LIMIT 5")                 # a surface-only change
    assert run_migrate(url).returncode == 4
    r = run_migrate(url, PG_MIGRATE_SURFACE_CHECK="skip")
    assert r.returncode == 0 and "WARNING *** the security surface check was SKIPPED" in r.stdout, r.stdout
    with connect(cluster, name) as c:
        c.execute("ALTER ROLE pipeshub_node_login CONNECTION LIMIT -1")
        assert c.execute("SELECT count(*) FROM kernel.surface_skips").fetchone()[0] == 1
    r = run_migrate(url)                                                               # the next run reports it
    assert r.returncode == 0 and "skipped 1 time(s)" in r.stdout, r.stdout


# ---- a build older than the database (helm rollback of a deployment whose migrate Job is created again) ----------------------------

def _as_newer_build(cluster, name, revision="platform_0004", min_code=None):
    """Emulate a database migrated by a newer build: one head carries a revision id this tree does not have."""
    with cluster.admin(name) as c:
        c.execute("UPDATE kernel.alembic_version SET version_num = 'platform_0099' WHERE version_num = %s", (revision,))
        if min_code is not None:
            c.execute("UPDATE kernel.schema_epoch SET min_code_epoch = %s WHERE branch = 'platform'", (min_code,))


def test_an_older_build_finds_nothing_to_do_on_a_database_that_is_ahead_and_compatible(db):
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    assert run_migrate(url).returncode == 0
    _as_newer_build(cluster, name)
    r = run_migrate(url)
    assert r.returncode == 0, r.stdout
    assert "database is ahead of this build" in r.stdout and "platform_0099" in r.stdout
    with cluster.admin(name) as c:                                           # nothing was touched
        assert c.execute("SELECT version_num FROM kernel.alembic_version WHERE version_num LIKE 'platform%'").fetchall() == [("platform_0099",)]


def test_an_older_build_tells_the_operator_to_roll_forward_when_the_newer_schema_needs_newer_code(db):
    cluster, name = db
    url = cluster.url(name, "pipeshub_migrate")
    assert run_migrate(url).returncode == 0
    _as_newer_build(cluster, name, min_code=99)
    r = run_migrate(url)
    assert r.returncode == 2 and "roll forward" in r.stdout, r.stdout
