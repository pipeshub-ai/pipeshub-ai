"""The import role (kernel_0012, platform_data_0002): the one login that copies an existing install's documents into PostgreSQL.
It holds the row policy and table privileges on exactly the tables the copy writes; the jobs login and the application roles gain
nothing from it; it cannot log in until an operator enables it."""
import psycopg
import pytest

from .conftest import run_migrate
from .test_integration import MIGRATE, connect

WRITTEN = sorted(
    "identity.orgs identity.org_logos identity.users identity.user_credentials identity.user_groups identity.user_group_members identity.org_auth_config "
    "identity.user_activities identity.used_reset_links identity.user_pictures identity.slug_counters conversation.chat_sessions "
    "conversation.chat_session_members conversation.chat_messages conversation.semantic_searches conversation.semantic_search_members "
    "conversation.citations content.documents content.projects "
    "content.project_members platform.notifications platform.mail_log platform.connectors_config platform.outbox oauth.apps "
    "oauth.access_tokens oauth.refresh_tokens oauth.authorization_codes oauth.device_authorizations oauth.service_account_delegations oauth.delegation_events oauth.agent_registrations "
    "integrations.slack_threads platform.docstore_changes".split())      # (the change log: the reverse follower reads and trims it)
# tables that had no jobs privilege before, or only SELECT/DELETE (the sweeper) or SELECT/UPDATE/DELETE (the dispatcher)
JOBS_NEVER_WRITES = ["content.documents", "content.projects", "content.project_members", "oauth.apps", "oauth.service_account_delegations",
                     "platform.mail_log", "platform.connectors_config", "integrations.slack_threads"]


def test_the_import_role_cannot_log_in_and_holds_no_membership(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        row = c.execute("SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE rolname = 'pipeshub_import'").fetchone()
        assert row == (False, False, False, False)
        assert c.execute("SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid IN (m.member, m.roleid) WHERE r.rolname = 'pipeshub_import'").fetchone()[0] == 0


def test_the_policy_is_on_exactly_the_tables_the_copy_writes(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        assert sorted(r for (r,) in c.execute("SELECT rel::text FROM kernel.rls_registry WHERE import_access")) == WRITTEN
        on = {r for (r,) in c.execute(
            "SELECT DISTINCT coalesce(pg_partition_root(p.polrelid), p.polrelid)::regclass::text "
            "FROM pg_policy p WHERE p.polname = 'import_all'")}
        assert sorted(on) == WRITTEN
        assert c.execute("SELECT count(*) FROM pg_policy p WHERE p.polname = 'import_all' AND (p.polroles <> ARRAY['pipeshub_import'::regrole::oid])").fetchone()[0] == 0
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []


def test_the_jobs_login_gains_nothing(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        for t in JOBS_NEVER_WRITES:
            for p in ("INSERT", "UPDATE", "DELETE", "SELECT"):
                assert c.execute("SELECT has_table_privilege('pipeshub_jobs', %s, %s)", (t, p)).fetchone()[0] is False, f"{t} {p}"
        assert c.execute("SELECT jobs_access FROM kernel.rls_registry WHERE rel = 'content.documents'::regclass").fetchone()[0] is False
        # what the dispatcher and the sweeper have always had is untouched
        assert c.execute("SELECT has_table_privilege('pipeshub_jobs', 'platform.outbox', 'UPDATE')").fetchone()[0] is True
        assert c.execute("SELECT has_table_privilege('pipeshub_jobs', 'platform.outbox', 'INSERT')").fetchone()[0] is False
        assert c.execute("SELECT has_table_privilege('pipeshub_jobs', 'oauth.access_tokens', 'INSERT')").fetchone()[0] is False


def test_the_import_role_writes_the_copied_tables_and_nothing_else(rdb):
    cluster, name = rdb
    org = "a" * 24
    with connect(cluster, name) as c:
        c.execute("SET ROLE pipeshub_import")
        c.execute("INSERT INTO content.documents (org_id, id, document_name, storage_vendor, created_at, updated_at) VALUES (%s, %s, 'x', 'local', now(), now())", (org, "b" * 24))
        assert c.execute("SELECT count(*) FROM content.documents").fetchone()[0] == 1
        c.execute("DELETE FROM content.documents")
        for stmt in ("UPDATE platform.store_epoch SET epoch = 9", "SELECT 1 FROM platform.jobs", "SELECT 1 FROM kernel.settings"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt)


def test_downgrade_removes_the_import_policies_and_keeps_the_dispatchers_privileges(rdb):
    cluster, name = rdb
    r = run_migrate(cluster.url(name, MIGRATE), "downgrade", "platform_data_0001", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT count(*) FROM pg_policy WHERE polname = 'import_all'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM kernel.rls_registry WHERE import_access").fetchone()[0] == 0
        assert c.execute("SELECT has_table_privilege('pipeshub_jobs', 'platform.outbox', 'UPDATE')").fetchone()[0] is True
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
    assert run_migrate(cluster.url(name, MIGRATE)).returncode == 0


def test_the_import_login_is_allowed_only_while_a_migration_job_runs(rdb):
    """The surface check refuses a login on the import role, except while a document-store migration job is running (the move scripts
    and the reverse follower enable it for that long); the migrate job (db.migrate) then passes, and fails again once the job has ended."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        c.execute("ALTER ROLE pipeshub_import LOGIN")
    r = run_migrate(cluster.url(name, MIGRATE))
    assert r.returncode == 4          # no job: a login on this role is a violation
    assert "ALTER ROLE pipeshub_import NOLOGIN" in r.stdout + r.stderr       # and the message says how to close it
    with connect(cluster, name) as c:
        c.execute("SELECT platform.job_start('docstore_migration')")
    r = run_migrate(cluster.url(name, MIGRATE))
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        c.execute("SELECT platform.job_finish((SELECT id FROM platform.jobs WHERE status = 'running'), 'done')")
    assert run_migrate(cluster.url(name, MIGRATE)).returncode == 4
    with connect(cluster, name) as c:
        c.execute("ALTER ROLE pipeshub_import NOLOGIN")
    assert run_migrate(cluster.url(name, MIGRATE)).returncode == 0


def test_downgrade_of_the_extension_list_keeps_the_names_the_kernel_allows(rdb):
    cluster, name = rdb
    r = run_migrate(cluster.url(name, MIGRATE), "downgrade", "platform_data_0003", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert {"pg_search", "pg_stat_statements"} <= set(c.execute("SELECT extensions FROM kernel.settings").fetchone()[0])
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
