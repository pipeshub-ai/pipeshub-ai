"""The import role (kernel_0012, platform_data_0002): the one login that copies an existing install's documents into PostgreSQL.
It holds the row policy and table privileges on exactly the tables the copy writes; the jobs login and the application roles gain
nothing from it; it cannot log in until an operator enables it."""
import re
from pathlib import Path

import psycopg
import pytest

from db import locations

from .conftest import run_migrate
from .test_integration import MIGRATE, connect

WRITTEN = sorted(
    "identity.orgs identity.org_logos identity.users identity.user_credentials identity.user_groups identity.user_group_members identity.org_auth_config "
    "identity.user_activities identity.used_reset_links identity.user_pictures identity.slug_counters conversation.chat_sessions "
    "conversation.chat_session_members conversation.chat_messages conversation.semantic_searches conversation.semantic_search_members "
    "conversation.citations content.documents content.projects "
    "content.project_members platform.notifications platform.mail_log platform.connectors_config platform.outbox oauth.apps "
    "oauth.access_tokens oauth.refresh_tokens oauth.authorization_codes oauth.device_authorizations oauth.service_account_delegations oauth.delegation_events oauth.agent_registrations "
    "integrations.slack_threads integrations.slack_user_links platform.docstore_changes "      # (the change log: the reverse follower reads and trims it)
    # R45-CARRY: the key map, and the harness tables the carriers write
    "platform.docstore_keymap harness.personal_choices harness.data_keys "
    "harness.workspace_heads harness.workspace_manifests harness.workspace_keys "
    "harness.audit_records harness.audit_heads harness.audit_pending harness.audit_anchors "
    # R46-CARRY: durable runs, journal and payloads; ledgers, people's data and erasure; the control plane
    "harness.runs harness.run_events harness.session_counters harness.next_runs harness.session_events harness.stream_counters "
    "harness.journal_payloads harness.session_forks harness.sandbox_snapshots harness.command_receipts harness.admission_receipts "
    "harness.admission_quotas harness.budget_accounts harness.budget_reservations "
    "harness.engine_signins harness.memories harness.schedules harness.schedule_firings harness.schedule_counters "
    "harness.inbox_preferences harness.run_end_notices harness.session_erasures harness.agent_erasures "
    "harness.principal_erasures harness.erasure_backfill_items harness.erasure_backfill_markers harness.cp_org_heads harness.cp_policy_layers "
    "harness.cp_policy_history harness.cp_migration_ledger harness.cp_catalog_items harness.cp_installations harness.cp_installation_history "
    "harness.cp_grants harness.cp_capability_ledger harness.cp_bindings".split())
# R46-CARRY: deployment-wide tables (no org_id, no row policy) the import role writes by privilege alone
DEPLOYMENT_WIDE = ["platform.docstore_keymap_deployment", "platform.docstore_refused", "harness.sandbox_reaps", "harness.erasure_backfill_plans", "harness.cp_deployment_head"]
# tables that had no jobs privilege before, or only SELECT/DELETE (the sweeper) or SELECT/UPDATE/DELETE (the dispatcher)
JOBS_NEVER_WRITES = ["content.documents", "content.projects", "content.project_members", "oauth.apps", "oauth.service_account_delegations",
                     "platform.mail_log", "platform.connectors_config", "integrations.slack_threads"]


def test_the_import_role_cannot_log_in_and_holds_no_membership(rdb):
    cluster, name = rdb
    with connect(cluster, name) as c:
        row = c.execute("SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE rolname = 'pipeshub_import'").fetchone()
        assert row == (False, False, False, False)
        assert c.execute("SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid IN (m.member, m.roleid) WHERE r.rolname = 'pipeshub_import'").fetchone()[0] == 0


def _edition_written() -> list[str]:
    """Tables an edition's own migration modules (EXTRA_MIGRATION_LOCATIONS) open to the import role; none in this build."""
    found = set()
    for loc in locations.extra_locations():
        for sql in Path(loc).glob("*.sql"):
            found.update(re.findall(r"kernel\.grant_import_access\('([\w.]+)'\)", sql.read_text(encoding="utf-8")))
    return sorted(found)


def test_the_policy_is_on_exactly_the_tables_the_copy_writes(rdb):
    cluster, name = rdb
    written = sorted({*WRITTEN, *_edition_written()})
    with connect(cluster, name) as c:
        assert sorted(r for (r,) in c.execute("SELECT rel::text FROM kernel.rls_registry WHERE import_access")) == written
        on = {r for (r,) in c.execute(
            "SELECT DISTINCT coalesce(pg_partition_root(p.polrelid), p.polrelid)::regclass::text "
            "FROM pg_policy p WHERE p.polname = 'import_all'")}
        assert sorted(on) == written
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


def test_the_key_map_is_the_import_roles_alone(rdb):
    """R45-CARRY: docstore-migrate's key map is written by the import role only; no application or jobs login reads or writes it."""
    cluster, name = rdb
    org = "c" * 24
    with connect(cluster, name) as c:
        for role in ("pipeshub_node", "pipeshub_jobs", "pipeshub_python"):
            if c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
                continue
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert c.execute("SELECT has_table_privilege(%s, 'platform.docstore_keymap', %s)", (role, p)).fetchone()[0] is False, f"{role} {p}"
        c.execute("SET ROLE pipeshub_import")
        c.execute("INSERT INTO platform.docstore_keymap (org_id, collection, source_id, source_oid, row_key) VALUES (%s, 'agentWorkspaceHeads', %s, true, '[\"w\"]')",
                  (org, "d" * 24))
        with pytest.raises(psycopg.errors.UniqueViolation):     # one row key names one document
            c.execute("INSERT INTO platform.docstore_keymap (org_id, collection, source_id, source_oid, row_key) VALUES (%s, 'agentWorkspaceHeads', %s, true, '[\"w\"]')",
                      (org, "e" * 24))


def test_downgrade_of_the_key_map_removes_it_and_the_harness_import_access(rdb):
    cluster, name = rdb
    r = run_migrate(cluster.url(name, MIGRATE), "downgrade", "platform_data_0005", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT to_regclass('platform.docstore_keymap')").fetchone()[0] is None
        assert c.execute("SELECT count(*) FROM kernel.rls_registry WHERE import_access AND rel::text LIKE 'harness.%'").fetchone()[0] == 0
        assert c.execute("SELECT has_schema_privilege('pipeshub_import', 'harness', 'USAGE')").fetchone()[0] is False
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
    assert run_migrate(cluster.url(name, MIGRATE)).returncode == 0


def test_the_deployment_wide_key_map_and_tables_are_the_import_roles(rdb):
    """R46-CARRY: the key map's deployment-wide form (rows no org owns) is the import role's alone, one row key names one document,
    and the import role reaches the deployment-wide harness tables the carriers write."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        for role in ("pipeshub_node", "pipeshub_jobs", "pipeshub_python"):
            if c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
                continue
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert c.execute("SELECT has_table_privilege(%s, 'platform.docstore_keymap_deployment', %s)", (role, p)).fetchone()[0] is False, f"{role} {p}"
        for t in DEPLOYMENT_WIDE:
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert c.execute("SELECT has_table_privilege('pipeshub_import', %s, %s)", (t, p)).fetchone()[0] is True, f"{t} {p}"
        c.execute("SET ROLE pipeshub_import")
        c.execute("INSERT INTO platform.docstore_keymap_deployment (collection, source_id, source_oid, row_key) VALUES ('agentErasureBackfillPlans', %s, true, '[\"d\"]')", ("d" * 24,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute("INSERT INTO platform.docstore_keymap_deployment (collection, source_id, source_oid, row_key) VALUES ('agentErasureBackfillPlans', %s, true, '[\"d\"]')", ("e" * 24,))


def test_downgrade_of_the_remaining_carriers_removes_their_access_and_the_deployment_wide_map(rdb):
    cluster, name = rdb
    r = run_migrate(cluster.url(name, MIGRATE), "downgrade", "platform_data_0006", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT to_regclass('platform.docstore_keymap_deployment')").fetchone()[0] is None
        assert c.execute("SELECT has_table_privilege('pipeshub_import', 'harness.runs', 'INSERT')").fetchone()[0] is False
        assert c.execute("SELECT has_table_privilege('pipeshub_import', 'harness.sandbox_reaps', 'INSERT')").fetchone()[0] is False
        assert c.execute("SELECT import_access FROM kernel.rls_registry WHERE rel = 'harness.audit_records'::regclass").fetchone()[0] is True
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
    assert run_migrate(cluster.url(name, MIGRATE)).returncode == 0


def test_the_refusal_list_is_the_import_roles_alone(rdb):
    """R48MIGFIX-FU-2: every document the copy refused, deployment-wide (a refused document may name no organisation), read and
    written by the import role only, one entry per collection and document."""
    cluster, name = rdb
    with connect(cluster, name) as c:
        for role in ("pipeshub_node", "pipeshub_jobs", "pipeshub_python"):
            if c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
                continue
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert c.execute("SELECT has_table_privilege(%s, 'platform.docstore_refused', %s)", (role, p)).fetchone()[0] is False, f"{role} {p}"
        c.execute("SET ROLE pipeshub_import")
        c.execute("INSERT INTO platform.docstore_refused (collection, source_id, reason) VALUES ('slack_user_links', %s, 'no user')", ("d" * 24,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute("INSERT INTO platform.docstore_refused (collection, source_id, reason) VALUES ('slack_user_links', %s, 'again')", ("d" * 24,))


def test_downgrade_of_the_refusal_list_removes_it(rdb):
    cluster, name = rdb
    r = run_migrate(cluster.url(name, MIGRATE), "downgrade", "platform_data_0007", PG_MIGRATE_ALLOW_DOWNGRADE="1")
    assert r.returncode == 0, r.stdout
    with connect(cluster, name) as c:
        assert c.execute("SELECT to_regclass('platform.docstore_refused')").fetchone()[0] is None
        assert c.execute("SELECT to_regclass('platform.docstore_keymap_deployment')").fetchone()[0] is not None
        assert [tuple(x) for x in c.execute("SELECT rule, object FROM kernel.rls_violations")] == []
    assert run_migrate(cluster.url(name, MIGRATE)).returncode == 0
