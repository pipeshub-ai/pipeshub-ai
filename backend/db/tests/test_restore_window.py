"""A restore happens only in maintenance mode, for one org, and the migrator sets the sequences."""
import csv
import subprocess
import sys
import time
import uuid

import psycopg
import pytest
from psycopg import errors as pg

from db import restore_window

from .conftest import BACKEND
from .rls_fixture import ORG_A, ORG_B

NEW_ORG = "d" * 24
PASSWORD = "window-secret"


@pytest.fixture
def conns(org_db):
    admin = org_db.admin()
    migrate = psycopg.connect(host="127.0.0.1", port=org_db.cluster.port, user="pipeshub_migrate", dbname=org_db.name, autocommit=True)
    backup = psycopg.connect(host="127.0.0.1", port=org_db.cluster.port, user="pipeshub_backup", dbname=org_db.name, autocommit=True)
    yield org_db, admin, migrate, backup
    try:
        restore_window.close_window(admin, migrate, abandon=True)
        admin.execute("DELETE FROM kt.docs WHERE org_id = ANY(%s)", ([NEW_ORG],))
        admin.execute("DELETE FROM kt.events WHERE org_id = ANY(%s)", ([NEW_ORG],))
    finally:
        for c in (admin, migrate, backup):
            c.close()


def wait_until_pooled(org_db):
    """PgBouncer remembers a failed server login for server_login_retry seconds."""
    for _ in range(50):
        try:
            return org_db.pooled(autocommit=True).execute("SELECT 1")
        except psycopg.Error:
            time.sleep(0.2)
    raise AssertionError("applications cannot connect after the window closed")


def as_restore(org_db):
    return psycopg.connect(host="127.0.0.1", port=org_db.cluster.port, user="pipeshub_restore", dbname=org_db.name, password=PASSWORD, autocommit=True)


def test_window_refuses_an_org_that_already_has_rows(conns):
    org_db, admin, migrate, backup = conns
    with pytest.raises(restore_window.Refused, match="already has rows"):
        restore_window.open_window(admin, migrate, backup, ORG_A, PASSWORD)
    assert migrate.execute("SELECT count(*) FROM kernel.restore_window").fetchone()[0] == 0
    assert admin.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'pipeshub_node_login'").fetchone()[0] is True   # logins restored
    assert admin.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'pipeshub_restore'").fetchone()[0] is False
    with pytest.raises(restore_window.Refused, match="not an org id"):
        restore_window.open_window(admin, migrate, backup, "org_x", PASSWORD)


def test_window_is_maintenance_mode_and_scoped_to_one_org(conns):
    org_db, admin, migrate, backup = conns
    app = org_db.pooled(autocommit=True)                                              # an application session is connected
    app.execute("SELECT 1")
    restore_window.open_window(admin, migrate, backup, NEW_ORG, PASSWORD, hours=1)
    # maintenance mode: the session was disconnected and no application login can connect
    with pytest.raises(psycopg.OperationalError):
        app.execute("SELECT 1")
    with pytest.raises(psycopg.OperationalError, match="not permitted to log in|server login has been failing"):
        org_db.pooled(autocommit=True).execute("SELECT 1")                            # the pooler accepts the client; PostgreSQL refuses the login (PgBouncer may answer for it while it retries)
    with pytest.raises(restore_window.Refused, match="already open"):
        restore_window.open_window(admin, migrate, backup, "e" * 24, PASSWORD)
    assert org_db.violations() == []                                                  # a declared window is not a violation
    with as_restore(org_db) as r:
        r.execute("INSERT INTO kt.docs (org_id, id, title) VALUES (%s, 'r1', 't')", (NEW_ORG,))
        r.execute("INSERT INTO kt.events (org_id, id, created_at) OVERRIDING SYSTEM VALUE VALUES (%s, 900, '2026-02-01')", (NEW_ORG,))
        for stmt, args in (("INSERT INTO kt.docs (org_id, id) VALUES (%s, 'x')", (ORG_A,)),          # another org: refused
                           ("INSERT INTO kt.cfg (org_id, k) VALUES (%s, 'x')", ("e" * 24,)),
                           ("INSERT INTO kt.tokens VALUES ('h', %s, 'u')", (NEW_ORG,))):             # a global table: no privilege
            with pytest.raises(pg.InsufficientPrivilege):
                r.execute(stmt, args)
        with pytest.raises(pg.InsufficientPrivilege):
            r.execute("SELECT setval('kt.events_id_seq', 1000)")                                    # sequences are the migrator's
        # the window expires by itself, even if close is forgotten
        admin.execute("UPDATE kernel.restore_window SET until = now() - interval '1 second'")
        with pytest.raises(pg.InsufficientPrivilege):
            r.execute("INSERT INTO kt.docs (org_id, id) VALUES (%s, 'late')", (NEW_ORG,))
    assert any(rule.startswith("28 restore window expired") for rule, _ in org_db.violations())
    admin.execute("UPDATE kernel.restore_window SET until = now() + interval '1 hour'")
    # close refuses until the migrator has set the sequences
    with pytest.raises(restore_window.Refused, match="sequences"):
        restore_window.close_window(admin, migrate)
    assert restore_window.restore_sequences(migrate, backup) == ["kt.events_id_seq"]
    assert admin.execute("SELECT last_value FROM kt.events_id_seq").fetchone()[0] == 900
    restore_window.close_window(admin, migrate)
    assert admin.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'pipeshub_restore'").fetchone()[0] is False
    assert admin.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'pipeshub_node_login'").fetchone()[0] is True
    wait_until_pooled(org_db)                                                         # applications can connect again
    with org_db.pooled() as c, c.transaction():                                        # and the next generated id is above the restored one
        c.execute("SELECT set_config('role', 'pipeshub_node', true), set_config('app.org_id', %s, true)", (NEW_ORG,))
        assert c.execute("INSERT INTO kt.events (org_id, created_at) VALUES (%s, '2026-02-02') RETURNING id", (NEW_ORG,)).fetchone()[0] > 900
    assert org_db.violations() == []
    with pytest.raises(psycopg.OperationalError):
        as_restore(org_db)                                                            # the login is gone (NOLOGIN, password cleared)
    admin.execute("DELETE FROM kt.docs WHERE org_id = %s", (NEW_ORG,))
    admin.execute("DELETE FROM kt.events WHERE org_id = %s", (NEW_ORG,))


def test_restore_login_expires_with_valid_until(conns):
    org_db, admin, migrate, backup = conns
    restore_window.open_window(admin, migrate, backup, NEW_ORG, PASSWORD, hours=1)
    until = admin.execute("SELECT rolvaliduntil - now() FROM pg_roles WHERE rolname = 'pipeshub_restore'").fetchone()[0]
    assert 0 < until.total_seconds() <= 3600
    restore_window.close_window(admin, migrate, abandon=True)


def test_command_line(org_db, monkeypatch):
    env = {"PG_ADMIN_URL": f"postgresql://postgres@127.0.0.1:{org_db.cluster.port}/{org_db.name}",
           "PG_MIGRATE_URL": f"postgresql+psycopg://pipeshub_migrate@127.0.0.1:{org_db.cluster.port}/{org_db.name}",
           "PG_BACKUP_URL": f"postgresql://pipeshub_backup@127.0.0.1:{org_db.cluster.port}/{org_db.name}",
           "PG_RESTORE_PASSWORD": PASSWORD, "PYTHONPATH": str(BACKEND)}

    def run(*args):
        return subprocess.run([sys.executable, "-m", "db.restore_window", *args], env=env, capture_output=True, text=True)
    r = run("open", "--org", ORG_A)
    assert r.returncode == restore_window.EXIT_REFUSED and "already has rows" in r.stderr
    assert run("open", "--org", NEW_ORG, "2").returncode == 2                         # usage
    assert run("open", "--org", NEW_ORG, "--hours", "99").returncode == restore_window.EXIT_REFUSED
    assert run("open", "--org", NEW_ORG, "--hours", "1").returncode == 0
    assert run("sequences").returncode == 0
    assert run("close").returncode == 0 and org_db.violations() == []


def test_a_batch_with_another_orgs_row_is_refused_loudly_and_inserts_nothing(conns):
    org_db, admin, migrate, backup = conns
    restore_window.open_window(admin, migrate, backup, NEW_ORG, PASSWORD, hours=1)
    cols = ["org_id", "id", "title"]
    with as_restore(org_db) as r:
        with pytest.raises(restore_window.Refused, match=r"1 row\(s\) belong to other orgs"):
            restore_window.restore_rows(r, NEW_ORG, "kt.docs", cols, [[NEW_ORG, "x1", "t"], [ORG_A, "x2", "t"]])
        assert admin.execute("SELECT count(*) FROM kt.docs WHERE id = 'x1'").fetchone()[0] == 0     # not silently half-loaded
        assert restore_window.restore_rows(r, NEW_ORG, "kt.docs", cols, [[NEW_ORG, "x1", "t"], [NEW_ORG, "x3", None]]) == 2
    assert admin.execute("SELECT count(*) FROM kt.docs WHERE org_id = %s", (NEW_ORG,)).fetchone()[0] == 2
    restore_window.close_window(admin, migrate, abandon=True)
    admin.execute("DELETE FROM kt.docs WHERE org_id = %s", (NEW_ORG,))


def test_export_and_load_move_one_org_without_losing_a_row(conns, tmp_path):
    org_db, admin, migrate, backup = conns
    admin.execute("INSERT INTO kt.cfg (org_id, k, v) VALUES (%s, 'empty', ''), (%s, 'null', NULL)", (ORG_B, ORG_B))
    try:
        order = restore_window.export_org(migrate, backup, ORG_B, tmp_path)
        assert "kt.docs" in order and (tmp_path / "kt.cfg.csv").is_file()
        assert ORG_A not in "".join(p.read_text() for p in tmp_path.glob("*.csv"))                  # one org only
        admin.execute("DELETE FROM kt.docs WHERE org_id = %s", (ORG_B,))
        admin.execute("DELETE FROM kt.events WHERE org_id = %s", (ORG_B,))
        admin.execute("DELETE FROM kt.cfg WHERE org_id = %s", (ORG_B,))
        admin.execute("DELETE FROM kt.apps WHERE org_id = %s", (ORG_B,))
        before = dict.fromkeys(order, 0)
        registered = set(order)
        restore_window.open_window(admin, migrate, backup, ORG_B, PASSWORD, hours=1)
        with as_restore(org_db) as r:
            counts = restore_window.load_org(r, ORG_B, tmp_path, registered)
        assert counts["kt.docs"] == 5 and counts["kt.events"] == 3 and counts["kt.cfg"] == 3 and counts["kt.apps"] == 1 and before
        assert admin.execute("SELECT v FROM kt.cfg WHERE org_id = %s AND k IN ('empty', 'null') ORDER BY k", (ORG_B,)).fetchall() == [("",), (None,)]
        # a file that holds another org's row is refused before anything is inserted
        (tmp_path / "kt.cfg.csv").write_text("org_id,k,v\n" + f"{ORG_A},x,y\n")
        with as_restore(org_db) as r, pytest.raises(restore_window.Refused, match="other orgs"):
            restore_window.load_org(r, ORG_B, tmp_path, registered)
    finally:
        restore_window.close_window(admin, migrate, abandon=True)
        admin.execute("DELETE FROM kt.cfg WHERE k IN ('empty', 'null')")


def test_closing_after_an_interrupted_open_brings_the_logins_back(conns):
    org_db, admin, migrate, backup = conns
    admin.execute("ALTER ROLE pipeshub_node_login NOLOGIN; ALTER ROLE pipeshub_jobs NOLOGIN")      # as if open died after switching off
    assert any(r.startswith("37 unexpected membership pipeshub_node_login") for r, _ in org_db.violations())   # doctor sees it
    restore_window.close_window(admin, migrate, abandon=True)
    assert org_db.violations() == []
    assert admin.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('pipeshub_node_login', 'pipeshub_jobs') AND rolcanlogin").fetchone()[0] == 2
    assert admin.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'pipeshub_restore'").fetchone()[0] is False


def test_load_is_one_transaction_and_never_runs_file_supplied_names(conns, tmp_path):
    org_db, admin, migrate, backup = conns
    order = restore_window.export_org(migrate, backup, ORG_B, tmp_path)
    for f in tmp_path.glob("*.csv"):                                      # retarget the export at a new, empty org
        f.write_text(f.read_text().replace(ORG_B, NEW_ORG))
    apps = list(csv.reader((tmp_path / "kt.apps.csv").read_text().splitlines()))
    apps[1][apps[0].index("client_id")] = str(uuid.uuid4())             # client_id is unique across orgs: the copy needs its own
    with (tmp_path / "kt.apps.csv").open("w", newline="") as f:
        csv.writer(f).writerows(apps)
    cfg = (tmp_path / "kt.cfg.csv").read_text().splitlines()
    (tmp_path / "kt.cfg.csv").write_text("\n".join([*cfg, cfg[1]]) + "\n")      # a duplicate key in a later table
    restore_window.open_window(admin, migrate, backup, NEW_ORG, PASSWORD, hours=1)
    try:
        with as_restore(org_db) as r:
            with pytest.raises(pg.UniqueViolation) as exc:
                restore_window.load_org(r, NEW_ORG, tmp_path, set(order))
            assert exc.value.diag.table_name == "cfg"                       # the duplicate key, not an earlier table
            assert admin.execute("SELECT count(*) FROM kt.apps WHERE org_id = %s", (NEW_ORG,)).fetchone()[0] == 0      # kt.apps loaded first and was rolled back
            assert admin.execute("SELECT count(*) FROM kt.docs WHERE org_id = %s", (NEW_ORG,)).fetchone()[0] == 0     # nothing is left behind
            (tmp_path / "order.txt").write_text("kt.docs\nkt.docs; DROP TABLE kt.cfg; --\n")
            with pytest.raises(restore_window.Refused, match="not a registered table"):
                restore_window.load_org(r, NEW_ORG, tmp_path, set(order))
        assert admin.execute("SELECT to_regclass('kt.cfg') IS NOT NULL").fetchone()[0]
    finally:
        restore_window.close_window(admin, migrate, abandon=True)


def test_the_surface_is_still_compared_while_a_window_is_open(conns):
    org_db, admin, migrate, backup = conns
    restore_window.open_window(admin, migrate, backup, NEW_ORG, PASSWORD, hours=1)
    try:
        assert org_db.violations() == []                                               # the window's own role changes are left out
        admin.execute("CREATE SCHEMA sneaky")
        assert any("schema sneaky" in line for _, line in org_db.violations())
        admin.execute("DROP SCHEMA sneaky")
    finally:
        restore_window.close_window(admin, migrate, abandon=True)
    assert org_db.violations() == []
