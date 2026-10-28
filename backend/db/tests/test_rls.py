"""Org isolation kernel on real PostgreSQL behind a real PgBouncer: apply_rls, the catalog check with every defect planted,
and the fault-injection suite generated per registered table. See rls_fixture.py for the schema under test."""
import concurrent.futures
import os
import threading
from pathlib import Path

import psycopg
import pytest
from psycopg import errors as pg

from db import surface

from .rls_fixture import KT_ROLES, KT_SURFACE, ORG_A, ORG_B, ORG_P, uow

BASE = Path(__file__).resolve().parents[1] / "surface.snapshot.txt"

# kernel-registered fixture tables: root, rows to try inserting for an org, leaves (all partitions)
TABLES = {
    "kt.docs": "INSERT INTO kt.docs (org_id, id, title) VALUES (%s, 'new', 't')",
    "kt.events": "INSERT INTO kt.events (org_id, created_at) VALUES (%s, '2026-02-20')",
    "kt.cfg": "INSERT INTO kt.cfg (org_id, k, v) VALUES (%s, 'new', 'v')",
}


def leaves(t, root):
    with t.admin() as c:
        return [r[0] for r in c.execute("SELECT relid::regclass::text FROM pg_partition_tree(%s::regclass) WHERE relid::regclass::text <> %s", (root, root))]


def count(conn, rel, where="true", *args):
    return conn.execute(f"SELECT count(*) FROM {rel} WHERE {where}", args).fetchone()[0]


# ---- the catalog and the helpers ------------------------------------------------------------------------------

def test_catalog_is_clean_and_covers_every_kind(org_db):
    assert org_db.violations() == []
    with org_db.admin() as c:
        rows = dict(c.execute("SELECT rel::text, class FROM kernel.rls_registry"))
        conversation = {f"conversation.{t}": "org" for t in ("chat_sessions", "chat_session_members", "chat_messages", "citations", "semantic_searches", "semantic_search_members")}
        stored = {f"content.{t}": "org" for t in ("documents", "projects", "project_members")}
        stored |= {f"platform.{t}": "org" for t in ("notifications", "mail_log", "connectors_config", "outbox")}
        assert rows == {"kt.docs": "org", "kt.events": "org", "kt.cfg": "lineage", "kt.apps": "org", "kt.tokens": "global",
                "platform.consumer_inbox": "org", "platform.deployment_profile": "internal", "platform.jobs": "internal",
                "platform.store_epoch": "internal", **conversation, **stored,
                **{f"platform.{t}": "internal" for t in ("kv_entries", "queue_messages", "queue_groups", "queue_deliveries", "sched_jobs", "sched_repeats")}}
        # every relation of every tree has RLS enabled and forced (root + 5 docs leaves, root + 3 events leaves, ...)
        assert c.execute("SELECT count(*) FROM pg_class WHERE relname LIKE 'docs%' AND relnamespace = 'kt'::regnamespace "
                         "AND relkind IN ('r','p') AND relrowsecurity AND relforcerowsecurity").fetchone()[0] == 6
        assert c.execute("SELECT count(*) FROM pg_class WHERE relname LIKE 'events\\_2026%' AND relforcerowsecurity").fetchone()[0] == 3
        assert c.execute("SELECT relrowsecurity FROM pg_class WHERE oid = 'kt.tokens'::regclass").fetchone()[0] is False   # global: grants, not RLS


def test_apply_rls_is_idempotent_and_validates(org_db):
    with org_db.admin() as c:
        c.execute("SET ROLE pipeshub_owner")
        assert c.execute("SELECT kernel.apply_rls('kt.docs', 'org', p_jobs => true, p_vetted => true)").fetchone()[0] == 6
        assert c.execute("SELECT kernel.apply_rls('kt.events', 'org')").fetchone()[0] == 4
        assert c.execute("SELECT kernel.apply_rls('kt.cfg', 'lineage')").fetchone()[0] == 1       # a plain table: pg_partition_tree is empty
        c.execute("CREATE TABLE kt.noorg (id int)")
        with pytest.raises(psycopg.errors.RaiseException, match="needs a column org_id text NOT NULL"):
            c.execute("SELECT kernel.apply_rls('kt.noorg', 'org')")
        c.execute("DROP TABLE kt.noorg")
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("SELECT kernel.apply_rls('kt.cfg', 'bogus')")
    assert org_db.violations() == []


def test_org_id_format_rejects_a_comma_even_for_a_superuser(org_db):
    with org_db.admin() as c, pytest.raises(pg.CheckViolation, match="org_id_format"):
        c.execute("INSERT INTO kt.cfg (org_id, k) VALUES ('org_a,org_b', 'x')")


def test_fixture_surface_files_are_current(org_db):
    """The expected text for the fixture schema: roles (needed to migrate) and everything else. UPDATE_SURFACE=1 rewrites it."""
    with org_db.admin() as c:
        live = set(surface.live_lines(c))
        version = surface.major(c)
    extra = sorted(live - surface.for_major(surface.expected_lines([BASE]), version))
    roles = [x for x in extra if x.startswith(("role ", "member ")) and "pipeshub_vetted_kt" in x]
    if os.environ.get("UPDATE_SURFACE") == "1":
        KT_ROLES.write_text("\n".join(roles) + "\n", encoding="utf-8")
        KT_SURFACE.write_text("\n".join(x for x in extra if x not in roles) + "\n", encoding="utf-8")
    assert org_db.surface_only(org_db.admin()) == []


# ---- planted defects: each one must be reported by the named check ---------------------------------------

SET_OWNER = "SET LOCAL ROLE pipeshub_owner; "
PLANTS = [
    # (id, SQL planted in a transaction that is rolled back, rule prefix that must appear)
    ("01 unregistered table with org_id", SET_OWNER + "CREATE TABLE kt.loose (org_id text, x int)", "01"),
    ("02 NO FORCE on one partition", SET_OWNER + "ALTER TABLE kt.docs_h2 NO FORCE ROW LEVEL SECURITY", "02"),
    ("02 RLS disabled on a time partition", SET_OWNER + "ALTER TABLE kt.events_2026_02 DISABLE ROW LEVEL SECURITY", "02"),
    ("03 missing policy", SET_OWNER + "DROP POLICY backup_read ON kt.docs_h1", "03"),
    ("03 missing restore policy on the root", SET_OWNER + "DROP POLICY restore_insert ON kt.cfg", "03"),
    ("04 stray permissive policy", "CREATE POLICY debug_all ON kt.docs FOR SELECT TO pipeshub_app USING (true)", "04"),
    ("04 stray policy on a time partition", "CREATE POLICY debug_all ON kt.events_2026_01 FOR ALL TO pipeshub_node USING (true)", "04"),
    ("05 edited org policy on a partition", "ALTER POLICY org_scope ON kt.docs_h1 USING (org_id IS NOT NULL)", "05"),
    ("05 org policy pointed at another role", "ALTER POLICY org_scope ON kt.cfg TO pipeshub_node", "05"),
    ("05 restore policy made unconditional", "ALTER POLICY restore_insert ON kt.docs WITH CHECK (true)", "05"),
    ("06 org_id CHECK dropped", SET_OWNER + "ALTER TABLE kt.cfg DROP CONSTRAINT org_id_format", "06"),
    ("06 org_id nullable", SET_OWNER + "ALTER TABLE kt.cfg DROP CONSTRAINT cfg_pkey; ALTER TABLE kt.cfg ALTER COLUMN org_id DROP NOT NULL", "06"),
    ("07 unique index not led by org_id", SET_OWNER + "CREATE UNIQUE INDEX docs_title_uq ON kt.docs_h0 (title)", "07 unique"),
    ("07 exempted constraint changed",
     SET_OWNER + "ALTER TABLE kt.apps DROP CONSTRAINT apps_client_id_key; ALTER TABLE kt.apps ADD CONSTRAINT apps_client_id_key UNIQUE (client_id, redirect)",
     "07 exempted constraint"),
    ("07 exempted key without a server-side default", SET_OWNER + "ALTER TABLE kt.apps ALTER COLUMN client_id DROP DEFAULT", "07 exempted key"),
    ("08 FK not led by org_id",
     SET_OWNER + "CREATE TABLE kt.refs (org_id text NOT NULL, id text NOT NULL, doc_org text, doc_id text, PRIMARY KEY (org_id, id), "
     "FOREIGN KEY (doc_org, doc_id) REFERENCES kt.docs (org_id, id)); SELECT kernel.apply_rls('kt.refs', 'org')", "08 FK not led"),
    ("08 FK into an org-scoped table from an org-less table",
     SET_OWNER + "CREATE TABLE kt.probe (o text, i text, FOREIGN KEY (o, i) REFERENCES kt.docs (org_id, id))", "08 FK into"),
    ("09 table owned by another role", "CREATE TABLE kt.rogue (x int); ALTER TABLE kt.rogue OWNER TO pipeshub_jobs", "09"),
    ("10 partition with a grant", "GRANT SELECT ON kt.docs_h1 TO pipeshub_node", "10"),
    ("10 time partition with a grant", "GRANT SELECT ON kt.events_2026_01 TO pipeshub_connectors", "10"),
    ("11 grant to the policy group", "GRANT SELECT ON kt.cfg TO pipeshub_app", "11"),
    ("12 TRUNCATE to a service role", "GRANT TRUNCATE ON kt.docs TO pipeshub_connectors", "12"),
    ("12 REFERENCES to the jobs role", "GRANT REFERENCES ON kt.docs TO pipeshub_jobs", "12"),
    ("13 CREATE on a schema to a service role", "GRANT CREATE ON SCHEMA kt TO pipeshub_connectors", "13 CREATE on schema"),
    ("13 TEMP on the database to PUBLIC", "GRANT TEMPORARY ON DATABASE t_rls TO PUBLIC", "13 TEMPORARY"),
    ("13 CREATE on the database to a service role", "GRANT CREATE ON DATABASE t_rls TO pipeshub_query", "13 CREATE on database"),
    ("14 view without security_invoker", SET_OWNER + "CREATE VIEW kt.v_docs AS SELECT id FROM kt.docs", "14 view"),
    ("14 materialized view", SET_OWNER + "CREATE MATERIALIZED VIEW kt.mv AS SELECT 1 AS x", "14 materialized"),
    ("15 SECURITY DEFINER owned by the owner role",
     SET_OWNER + "CREATE FUNCTION kernel.sneaky() RETURNS int LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, kernel, pg_temp AS 'SELECT 1'", "15"),
    ("16 search_path without pg_temp", "ALTER FUNCTION kt.docs_search(text) SET search_path = pg_catalog, kt", "16"),
    ("16 pg_temp not last", "ALTER FUNCTION kt.docs_search(text) SET search_path = pg_catalog, pg_temp, kt", "16"),
    ("17 SECURITY DEFINER executable by PUBLIC", "GRANT EXECUTE ON FUNCTION kt.docs_search(text) TO PUBLIC", "17"),
    ("18 service role member of a vetted owner", "GRANT pipeshub_vetted_kt TO pipeshub_connectors", "18 member of vetted"),
    ("18 jobs member of the owner", "GRANT pipeshub_owner TO pipeshub_jobs", "18 member of owner"),
    ("18 backup member of the owner", "GRANT pipeshub_owner TO pipeshub_backup", "18 member of owner"),
    ("19 login can SET ROLE to the policy group", "GRANT pipeshub_app TO pipeshub_connectors_login WITH SET TRUE", "19"),
    ("20 login reaches two service roles", "GRANT pipeshub_node TO pipeshub_connectors_login WITH INHERIT FALSE, SET TRUE", "20"),
    ("21 ALTER ROLE ... SET app.org_id", "ALTER ROLE pipeshub_ro SET app.org_id = 'x'", "21"),
    ("21 ALTER ROLE login SET role", "ALTER ROLE pipeshub_connectors_login SET role = 'pipeshub_connectors'", "21"),
    ("22 BYPASSRLS on a service login", "ALTER ROLE pipeshub_connectors_login BYPASSRLS", "22"),
    ("22 BYPASSRLS on the backup role", "ALTER ROLE pipeshub_backup BYPASSRLS", "22"),
    ("22 SUPERUSER on the jobs role", "ALTER ROLE pipeshub_jobs SUPERUSER", "22"),
    ("24 registry row for a dropped relation",
     SET_OWNER + "CREATE TABLE kt.t24 (org_id text NOT NULL); SELECT kernel.apply_rls('kt.t24', 'org'); DROP TABLE kt.t24", "24"),
    ("25 login inheriting its service role", "CREATE ROLE pipeshub_bad_login LOGIN; GRANT pipeshub_connectors TO pipeshub_bad_login WITH INHERIT TRUE, SET FALSE", "25"),
    ("25 login inheriting through a helper role",
     "CREATE ROLE helper_role NOLOGIN; GRANT pipeshub_connectors TO helper_role; GRANT helper_role TO pipeshub_connectors_login", "25"),
    ("26 grant on a global table to a service role", "GRANT SELECT, UPDATE ON kt.tokens TO pipeshub_node", "26 app actor"),
    ("26 global table privilege parked on a helper role",
     "CREATE ROLE helper_role NOLOGIN; GRANT SELECT, UPDATE ON kt.tokens TO helper_role; GRANT helper_role TO pipeshub_node", "26 app actor"),
    ("26 helper reachable only by SET ROLE",
     "CREATE ROLE helper_role NOLOGIN; GRANT UPDATE ON kt.tokens TO helper_role; GRANT helper_role TO pipeshub_node WITH INHERIT FALSE, SET TRUE", "26 app actor"),
    ("26 PUBLIC grant on a global table", "GRANT SELECT ON kt.tokens TO PUBLIC", "26 app actor/PUBLIC"),
    ("26 restore role holds INSERT on a global table", "GRANT INSERT ON kt.tokens TO pipeshub_restore", "26 restore"),
    ("27 backup role granted DELETE", "GRANT DELETE ON kt.docs TO pipeshub_backup", "27 backup role holds"),
    ("27 backup role member of a role", "GRANT pipeshub_jobs TO pipeshub_backup", "27 backup role owns"),
    ("28 restore role can log in outside a window", "ALTER ROLE pipeshub_restore LOGIN", "28 restore role can log in"),
    ("28 restore role granted SELECT", "GRANT SELECT ON kt.docs TO pipeshub_restore", "28 restore role holds"),
    ("28 restore role member of a role", "GRANT pipeshub_jobs TO pipeshub_restore", "28 restore role owns"),
    ("28 expired window left behind",
     "INSERT INTO kernel.restore_window (org, until) VALUES ('" + ORG_A + "', now() - interval '1 minute')", "28 restore window expired"),
    ("29 exempted table granted to a service role", "GRANT SELECT, INSERT, UPDATE ON kt.apps TO pipeshub_node", "29"),
    ("29 exempted table privilege parked on a helper role",
     "CREATE ROLE helper_role NOLOGIN; GRANT SELECT, INSERT ON kt.apps TO helper_role; GRANT helper_role TO pipeshub_node", "29"),
    ("30 grant on a kernel table", "GRANT INSERT ON kernel.rls_exemptions TO pipeshub_node", "30"),
    ("30 grant on the registry", "GRANT SELECT ON kernel.rls_registry TO pipeshub_app", "30"),
    ("31 jobs-only function executable by a service role", "GRANT EXECUTE ON FUNCTION kt.import_tokens(text, text, text) TO pipeshub_node", "31 jobs-only"),
    ("31 import-named function not registered as jobs-only",
     SET_OWNER + "SELECT kernel.register_function('kt.import_tokens(text, text, text)', 'write', 'pipeshub_node')", "31 import/sweep"),
    ("32 prologue function STABLE", "ALTER FUNCTION kt.fence(int) STABLE", "32 prologue function is not VOLATILE"),
    ("32 prologue fence without the isolation assertion",
     "CREATE OR REPLACE FUNCTION kt.fence(p_epoch int) RETURNS int LANGUAGE sql VOLATILE SECURITY DEFINER "
     "SET search_path = pg_catalog, kt, pg_temp AS 'SELECT p_epoch'", "32 prologue fence"),
    ("32 prologue function executable by PUBLIC", "GRANT EXECUTE ON FUNCTION kt.fence(int) TO PUBLIC", "32 prologue function executable"),
    ("33 write function executable by the read-only login", "GRANT EXECUTE ON FUNCTION kt.set_redirect(uuid, text) TO pipeshub_ro", "33"),
    ("33 write function executable by the policy group", "GRANT EXECUTE ON FUNCTION kt.set_redirect(uuid, text) TO pipeshub_app", "33"),
    ("33 write function reachable by the read-only login through a helper role",
     "CREATE ROLE helper_role NOLOGIN; GRANT EXECUTE ON FUNCTION kt.set_redirect(uuid, text) TO helper_role; GRANT helper_role TO pipeshub_ro", "33"),
    ("33 jobs-only function executable by the read-only login", "GRANT EXECUTE ON FUNCTION kt.import_tokens(text, text, text) TO pipeshub_ro", "31"),
    ("33 write function executable by a second service role", "GRANT EXECUTE ON FUNCTION kt.set_redirect(uuid, text) TO pipeshub_connectors", "33"),
    ("34 write function executable by no declared writer", "REVOKE EXECUTE ON FUNCTION kt.set_redirect(uuid, text) FROM pipeshub_node", "34"),
    ("35 unclassified SECURITY DEFINER function",
     SET_OWNER + "SELECT 1; RESET ROLE; SET LOCAL ROLE postgres; "
     "CREATE FUNCTION kt.sneaky2() RETURNS int LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, kt, pg_temp AS 'SELECT 1'; "
     "ALTER FUNCTION kt.sneaky2() OWNER TO pipeshub_vetted_kt", "35"),
    ("36 read-class function that is VOLATILE", "ALTER FUNCTION kt.docs_search(text) VOLATILE", "36 read-class"),
    ("36 registry row for a dropped function",
     SET_OWNER + "SELECT kernel.register_function('kt.fence(int)', 'prologue'); "
     "INSERT INTO kernel.function_registry VALUES ('kt.fence(int)'::regprocedure, 'read', NULL) ON CONFLICT (proc) DO UPDATE SET class = 'read'; "
     "RESET ROLE; SET LOCAL ROLE postgres; CREATE FUNCTION kt.gone() RETURNS int LANGUAGE sql AS 'SELECT 1'; "
     "INSERT INTO kernel.function_registry VALUES ('kt.gone()'::regprocedure, 'read', NULL); DROP FUNCTION kt.gone()", "36 registry row"),
    ("37 service role member of a predefined role", "GRANT pg_read_all_data TO pipeshub_connectors", "37"),
    ("37 login member of a helper role", "CREATE ROLE helper_role NOLOGIN; GRANT helper_role TO pipeshub_connectors_login", "37"),
    ("37 jobs role member of a helper role", "CREATE ROLE helper_role NOLOGIN; GRANT helper_role TO pipeshub_jobs", "37"),
    ("38 column privilege on a global table", "GRANT UPDATE (user_id) ON kt.tokens TO pipeshub_node", "38"),
    ("38 column privilege to PUBLIC on an org-scoped table", "GRANT SELECT (body) ON kt.docs TO PUBLIC", "38"),
    # ---- planted defects: roles outside our naming, login flags, other grantees, odd constraints ----------------
    ("39 vetted role can log in", "ALTER ROLE pipeshub_vetted_kt LOGIN", "39"),
    ("39 owner role can log in", "ALTER ROLE pipeshub_owner LOGIN", "39"),
    ("39 policy group can log in", "ALTER ROLE pipeshub_app LOGIN", "39"),
    ("39 service role can log in", "ALTER ROLE pipeshub_node LOGIN", "39"),
    ("39 role that owns a table can log in", "CREATE ROLE tbl_owner LOGIN; CREATE TABLE kt.t39 (x int); ALTER TABLE kt.t39 OWNER TO tbl_owner", "39"),
    ("37 other-named login in backup", "CREATE ROLE etl LOGIN; GRANT pipeshub_backup TO etl", "37"),
    ("37 other-named login in jobs", "CREATE ROLE etl LOGIN; GRANT pipeshub_jobs TO etl", "37"),
    ("37 other-named login in restore", "CREATE ROLE etl LOGIN; GRANT pipeshub_restore TO etl", "37"),
    ("37 other-named login in pg_read_all_data", "CREATE ROLE etl LOGIN; GRANT pg_read_all_data TO etl", "37"),
    ("37 other-named login in pg_write_all_data", "CREATE ROLE etl LOGIN; GRANT pg_write_all_data TO etl", "37"),
    ("25 other-named login inheriting a service role", "CREATE ROLE etl LOGIN; GRANT pipeshub_node TO etl WITH INHERIT TRUE", "25"),
    ("37 other-named role in the owner role", "CREATE ROLE etl LOGIN; GRANT pipeshub_owner TO etl", "37"),
    ("37 two ordinary roles", "CREATE ROLE etl_a LOGIN; CREATE ROLE etl_b NOLOGIN; GRANT etl_b TO etl_a", "37"),
    ("26 other-named login with SELECT on a global table", "CREATE ROLE etl LOGIN; GRANT SELECT ON kt.tokens TO etl", "26 app actor"),
    ("22 other-named BYPASSRLS login", "CREATE ROLE etl LOGIN BYPASSRLS; GRANT SELECT ON kt.docs TO etl", "22"),
    ("22 BYPASSRLS helper role granted to a service role", "CREATE ROLE helper_role NOLOGIN BYPASSRLS; GRANT helper_role TO pipeshub_node", "22"),
    ("38 other-named role with a column grant on a global table", "CREATE ROLE etl LOGIN; GRANT SELECT (token_hash) ON kt.tokens TO etl", "38"),
    ("38 restore role with a column grant on its window row", "GRANT UPDATE (org, until) ON kernel.restore_window TO pipeshub_restore", "38"),
    ("38 service role with a column grant on exemptions", "GRANT INSERT (reason) ON kernel.rls_exemptions TO pipeshub_node", "38"),
    ("38 backup role with a column UPDATE on a global table", "GRANT UPDATE (user_id) ON kt.tokens TO pipeshub_backup", "38"),
    ("33 write function executable by the backup role", "GRANT EXECUTE ON FUNCTION kt.set_redirect(uuid, text) TO pipeshub_backup", "33"),
    ("33 write function executable by another service's login", "GRANT EXECUTE ON FUNCTION kt.set_redirect(uuid, text) TO pipeshub_query_login", "33"),
    ("33 write function executable by the restore role", "GRANT EXECUTE ON FUNCTION kt.set_redirect(uuid, text) TO pipeshub_restore", "33"),
    ("31 jobs function executable by the backup role", "GRANT EXECUTE ON FUNCTION kt.import_tokens(text, text, text) TO pipeshub_backup", "31"),
    ("31 jobs function executable by a service login", "GRANT EXECUTE ON FUNCTION kt.import_tokens(text, text, text) TO pipeshub_node_login", "31"),
    ("32 prologue function executable by the backup role", "GRANT EXECUTE ON FUNCTION kt.fence(int) TO pipeshub_backup", "32 prologue function executable by pipeshub_backup"),
    ("07 EXCLUDE constraint without org_id", SET_OWNER + "ALTER TABLE kt.cfg ADD CONSTRAINT cfg_v_excl EXCLUDE USING btree (v WITH =)", "07"),
    ("16 duplicate pg_temp in search_path", "ALTER FUNCTION kt.docs_search(text) SET search_path = pg_catalog, pg_temp, kt, pg_temp", "16"),
    ("40 rule on an org-scoped table updating a global table",
     SET_OWNER + "CREATE RULE r40 AS ON INSERT TO kt.cfg DO ALSO UPDATE kt.tokens SET user_id = 'x'", "40"),
    ("14 view in the kernel schema over a global table",
     SET_OWNER + "CREATE VIEW kernel.tok AS SELECT token_hash FROM kt.tokens; GRANT SELECT ON kernel.tok TO pipeshub_node", "14 view"),
    ("30 view in the kernel schema granted to a service role",
     SET_OWNER + "CREATE VIEW kernel.tok2 WITH (security_invoker = true) AS SELECT 1 AS x; GRANT SELECT ON kernel.tok2 TO pipeshub_node", "30"),
    ("09 schema owned by a service role", "CREATE SCHEMA evil AUTHORIZATION pipeshub_node", "09 schema"),
    ("13 CREATE on a schema owned by a service role", "CREATE SCHEMA evil AUTHORIZATION pipeshub_node", "13 CREATE on schema"),
    ("21 upper-case app.* role setting", 'ALTER ROLE pipeshub_node_login SET "APP.ORG_ID" = \'' + "b" * 24 + "'", "21"),
    ("41 login with REPLICATION", "ALTER ROLE pipeshub_connectors_login REPLICATION", "41"),
    ("41 login with CREATEROLE", "ALTER ROLE pipeshub_connectors_login CREATEROLE", "41"),
    ("41 login with CREATEDB", "ALTER ROLE pipeshub_connectors_login CREATEDB", "41"),
    ("41 other-named role with CREATEROLE", "CREATE ROLE etl LOGIN CREATEROLE", "41"),
    ("12 MAINTAIN on an org-scoped table", "GRANT MAINTAIN ON kt.docs TO pipeshub_connectors", "12"),
    ("35 read-class function calling a write function",
     "CREATE FUNCTION kt.wrap(uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, kt, pg_temp "
     "AS 'SELECT kt.set_redirect($1, ''https://x/cb'')'; ALTER FUNCTION kt.wrap(uuid) OWNER TO pipeshub_vetted_kt; "
     "SELECT kernel.register_function('kt.wrap(uuid)', 'read')", "35 read-class function calls"),
    ("36 read-class function with DML",
     "CREATE FUNCTION kt.wr2() RETURNS void LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, kt, pg_temp "
     "AS 'DELETE FROM kt.docs'; ALTER FUNCTION kt.wr2() OWNER TO pipeshub_vetted_kt; SELECT kernel.register_function('kt.wr2()', 'read')",
     "36 read-class function body"),
    ("07 exempted random key with a constant default",
     "ALTER TABLE kt.apps ALTER COLUMN client_id SET DEFAULT '00000000-0000-0000-0000-000000000000'", "07 exempted key"),
    ("09 kernel table owned by a vetted role", "CREATE TABLE kernel.t28 (x int); ALTER TABLE kernel.t28 OWNER TO pipeshub_vetted_kt", "09"),
    ("42 vetted role of another schema holds a privilege here",
     "CREATE ROLE pipeshub_vetted_other NOLOGIN; GRANT SELECT ON kt.tokens TO pipeshub_vetted_other", "42"),
    ("44 consumer inbox keyed by message id alone",
     SET_OWNER + "ALTER TABLE platform.consumer_inbox DROP CONSTRAINT consumer_inbox_pkey; ALTER TABLE platform.consumer_inbox ADD PRIMARY KEY (message_id)", "44"),
    ("44 consumer inbox keyed by a time column too",
     SET_OWNER + "ALTER TABLE platform.consumer_inbox DROP CONSTRAINT consumer_inbox_pkey; "
     "ALTER TABLE platform.consumer_inbox ADD PRIMARY KEY (org_id, received_at, message_id)", "44"),
    ("44 consumer inbox without a primary key",
     SET_OWNER + "ALTER TABLE platform.consumer_inbox DROP CONSTRAINT consumer_inbox_pkey", "44"),
    ("43 large object function granted to a service role", "GRANT EXECUTE ON FUNCTION pg_catalog.lo_get(oid) TO pipeshub_node", "43 role can use"),
    ("43 large object function granted to PUBLIC", "GRANT EXECUTE ON FUNCTION pg_catalog.lo_from_bytea(oid, bytea) TO PUBLIC", "43 role can use"),
    ("05 org policy without WITH CHECK",
     "DROP POLICY org_scope ON kt.cfg; CREATE POLICY org_scope ON kt.cfg TO pipeshub_app USING (org_id = NULLIF(current_setting('app.org_id', true), ''))", "05"),
    ("05 org policy for SELECT only",
     "DROP POLICY org_scope ON kt.cfg; CREATE POLICY org_scope ON kt.cfg FOR SELECT TO pipeshub_app USING (org_id = NULLIF(current_setting('app.org_id', true), ''))", "05"),
    ("04 restrictive policy beside a permissive one",
     "CREATE POLICY p_all ON kt.cfg AS PERMISSIVE TO pipeshub_app USING (true); CREATE POLICY p_r ON kt.cfg AS RESTRICTIVE TO pipeshub_app USING (true)", "04"),
    ("02 forgotten DEFAULT partition", SET_OWNER + "CREATE TABLE kt.events_def PARTITION OF kt.events DEFAULT", "02"),
    ("04 leaf secured for another root and attached",
     SET_OWNER + "CREATE TABLE kt.ev_x (LIKE kt.events INCLUDING ALL EXCLUDING IDENTITY); SELECT kernel.apply_rls_partition('kt.ev_x', 'kt.cfg'); "
     "ALTER TABLE kt.events ATTACH PARTITION kt.ev_x FOR VALUES FROM ('2027-01-01') TO ('2027-02-01')", "04"),
    ("18 other-named member of a vetted role", "CREATE ROLE etl LOGIN; GRANT pipeshub_vetted_kt TO etl", "18"),
    ("37 pg_read_all_data granted to the read-only login", "GRANT pg_read_all_data TO pipeshub_ro", "37"),
]


@pytest.mark.parametrize("plant", PLANTS, ids=[p[0] for p in PLANTS])
def test_planted_defect_is_detected(org_db, plant):
    _, sql, expect = plant
    with org_db.admin(autocommit=False) as c:
        try:
            c.execute(sql)
            found = org_db.violations(c)
        finally:
            c.rollback()
    assert any(rule.startswith(expect) for rule, _ in found), f"{expect!r} not reported; got {sorted({r for r, _ in found})}"


def test_planted_defects_cover_every_check(org_db):
    """Every check number 01..44 has a plant (a check without one is untested)."""
    covered = {p[2][:2] for p in PLANTS}
    assert covered >= {f"{n:02d}" for n in range(1, 45)} - {"23"}, sorted({f"{n:02d}" for n in range(1, 45)} - covered)


def test_invalid_index_from_a_failed_concurrent_build_is_detected(org_db):          # 23: cannot run in a transaction
    with org_db.admin() as c:
        c.execute("INSERT INTO kt.docs (org_id, id, title) VALUES (%s, 'dup-1', 'same'), (%s, 'dup-2', 'same')", (ORG_A, ORG_A))
        leaf = c.execute("SELECT DISTINCT tableoid::regclass::text FROM kt.docs WHERE id = 'dup-1'").fetchone()[0]
        try:
            with pytest.raises(pg.UniqueViolation):
                c.execute(f"CREATE UNIQUE INDEX CONCURRENTLY docs_dup_uq ON {leaf} (org_id, title)")
            found = org_db.violations(c)
        finally:
            c.execute("DROP INDEX CONCURRENTLY IF EXISTS kt.docs_dup_uq")
            c.execute("DELETE FROM kt.docs WHERE id LIKE 'dup-%'")
    assert ("23 invalid index", "kt.docs_dup_uq") in found, found
    assert org_db.violations() == []


def test_every_plant_rolled_back(org_db):
    assert org_db.violations() == []
    with org_db.admin() as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('helper_role', 'pipeshub_bad_login')").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM kernel.restore_window").fetchone()[0] == 0


# ---- fault injection through the pooler, generated per registered table -----------------------------------

@pytest.mark.parametrize("root", TABLES)
def test_no_org_foreign_org_and_cross_org_writes(org_db, root):
    insert = TABLES[root]
    with org_db.pooled() as c:
        with uow(c, "pipeshub_node", None):                                           # no org: nothing visible, writes refused
            assert count(c, root) == 0
            with pytest.raises(pg.InsufficientPrivilege):
                with c.transaction():
                    c.execute(insert, (ORG_A,))
        with uow(c, "pipeshub_node", ORG_A):                                          # org A looks for B
            assert count(c, root) > 0
            assert count(c, root, "org_id = %s", ORG_B) == 0
            assert c.execute(f"DELETE FROM {root} WHERE org_id = %s", (ORG_B,)).rowcount == 0
            assert c.execute(f"UPDATE {root} SET org_id = org_id WHERE org_id = %s", (ORG_B,)).rowcount == 0
            assert count(c, f"{root} a JOIN {root} b ON a.org_id = b.org_id", "b.org_id = %s", ORG_B) == 0
        with uow(c, "pipeshub_node", ORG_A), pytest.raises(pg.InsufficientPrivilege):   # writing a row of org B
            c.execute(insert, (ORG_B,))
        with uow(c, "pipeshub_node", ORG_A), pytest.raises(pg.InsufficientPrivilege):   # moving a row to org B
            c.execute(f"UPDATE {root} SET org_id = %s WHERE org_id = %s", (ORG_B, ORG_A))


def test_empty_org_after_a_committed_transaction_on_a_reused_server_connection(org_db):
    with org_db.pooled() as c:
        for _ in range(20):
            with uow(c, "pipeshub_node", ORG_A):
                assert count(c, "kt.docs") >= 5
            with uow(c, "pipeshub_node", ""):
                assert count(c, "kt.docs") == 0
                assert count(c, "kt.events") == 0


@pytest.mark.parametrize("root", TABLES)
def test_partitions_cannot_be_read_directly(org_db, root):
    with org_db.pooled() as c:
        for leaf in leaves(org_db, root):
            with uow(c, "pipeshub_node", ORG_A), pytest.raises(pg.InsufficientPrivilege):
                c.execute(f"SELECT count(*) FROM {leaf}")


def test_session_state_set_by_another_client_does_not_reach_the_next(org_db):
    x = org_db.pooled(autocommit=True)
    x.execute("SELECT set_config('app.org_id', %s, false)", (ORG_B,))                 # session GUC left behind by client X
    x.close()
    with org_db.pooled() as c, uow(c, "pipeshub_node", None):
        assert count(c, "kt.docs") == 0
        assert c.execute("SELECT current_setting('app.org_id', true)").fetchone()[0] in ("", None)


def test_a_raw_connection_without_a_unit_of_work_has_no_privileges(org_db):
    with org_db.pooled(autocommit=True) as c:
        for root in TABLES:
            with pytest.raises(pg.InsufficientPrivilege):
                c.execute(f"SELECT count(*) FROM {root}")
        with pytest.raises(pg.InsufficientPrivilege):
            c.execute("INSERT INTO kt.docs (org_id, id) VALUES (%s, 'raw')", (ORG_A,))
    with org_db.pooled("connectors", autocommit=True) as c, pytest.raises(pg.InsufficientPrivilege):    # and only its own service role is reachable
        c.execute("SET ROLE pipeshub_node")


def test_lineage_reads_ancestors_but_writes_only_its_own_org(org_db):
    with org_db.pooled() as c:
        with uow(c, "pipeshub_node", ORG_A, lineage=f"{ORG_A},{ORG_P}"):
            assert {r[0] for r in c.execute("SELECT org_id FROM kt.cfg")} == {ORG_A, ORG_P}
            assert c.execute("UPDATE kt.cfg SET v = 'hacked' WHERE org_id = %s", (ORG_P,)).rowcount == 0
            assert c.execute("DELETE FROM kt.cfg WHERE org_id = %s", (ORG_P,)).rowcount == 0
        with uow(c, "pipeshub_node", ORG_A):                                          # no lineage set: own org only
            assert {r[0] for r in c.execute("SELECT org_id FROM kt.cfg")} == {ORG_A}
        with uow(c, "pipeshub_node", ORG_A, lineage=f"{ORG_B}"):                       # a lineage list never grants WRITE
            with pytest.raises(pg.InsufficientPrivilege):
                c.execute("INSERT INTO kt.cfg (org_id, k) VALUES (%s, 'x')", (ORG_B,))


def test_jobs_role_reaches_only_the_tables_that_opted_in(org_db):
    with org_db.pooled_as("pipeshub_jobs") as c:
        with c.transaction():
            assert count(c, "kt.docs") >= 15                                          # jobs_all: every org
            assert count(c, "kt.cfg") == 0                                            # no jobs policy: nothing
    with org_db.pooled_as("pipeshub_jobs") as c, pytest.raises(pg.InsufficientPrivilege):
        c.execute("SELECT * FROM kt.tokens")                                          # the jobs role never reads a global table directly


def test_vetted_function_returns_nothing_without_an_org_or_for_a_foreign_org(org_db):
    with org_db.pooled() as c:
        for org, expect in ((None, 0), ("", 0), (ORG_A, 5), (ORG_B, 5)):
            with uow(c, "pipeshub_node", org):
                rows = c.execute("SELECT id FROM kt.docs_search('needle')").fetchall()
                assert len(rows) == expect
        with uow(c, "pipeshub_node", ORG_A):                                          # needle text exists only in org B's rows? it is per org
            assert c.execute("SELECT count(*) FROM kt.docs_search('needle-' || %s)", (ORG_B,)).fetchone()[0] == 0


def test_state_left_by_one_client_is_gone_for_the_next(org_db):
    x = org_db.pooled()
    with x.transaction():
        x.execute("SELECT set_config('role', 'pipeshub_node', true), set_config('app.org_id', %s, true)", (ORG_B,))
        x.execute("DECLARE cur CURSOR WITH HOLD FOR SELECT id FROM kt.docs")           # a session cursor
        x.execute("CREATE TEMP TABLE leak AS SELECT * FROM kt.docs")                   # a session temp table
    x.autocommit = True
    x.execute("SET ROLE pipeshub_node")                                                # a session-level role switch
    x.close()
    with org_db.pooled() as y:
        with uow(y, "pipeshub_node", ORG_A):                                           # the guard passes: role is 'none' again
            with pytest.raises(pg.InvalidCursorName):
                with y.transaction():
                    y.execute("FETCH 10 FROM cur")
            with pytest.raises(pg.UndefinedTable):
                with y.transaction():
                    y.execute("SELECT * FROM leak")
        with pytest.raises(pg.InsufficientPrivilege):
            y.execute("SELECT count(*) FROM kt.docs")                                  # raw query on the reused connection


def test_without_the_pooler_reset_the_role_guard_trips(org_db):
    """The prologue guard is the second layer: a connection that kept a session SET ROLE is refused."""
    with org_db.admin() as su:
        su.execute("SET ROLE pipeshub_node")
        with pytest.raises(AssertionError, match="session role leaked"):
            with uow(su, "pipeshub_node", ORG_A):
                pass


def test_caller_temp_table_does_not_shadow_what_a_vetted_function_reads(org_db):
    with org_db.pooled() as c:
        with uow(c, "pipeshub_node", ORG_A):
            before = c.execute("SELECT count(*) FROM kt.docs_search('needle')").fetchone()[0]
            c.execute("CREATE TEMP TABLE docs (org_id text, id text, title text, body text) ON COMMIT DROP")
            c.execute("INSERT INTO pg_temp.docs VALUES (%s, 'forged', 't', 'needle-forged')", (ORG_A,))
            assert c.execute("SELECT count(*) FROM kt.docs_search('needle')").fetchone()[0] == before == 5


def test_nothing_is_left_on_pooled_connections(org_db):
    for svc in ("node", "connectors"):
        with org_db.pooled(svc) as c, uow(c, f"pipeshub_{svc}", ORG_A):
            c.execute("SELECT 1")
    with org_db.pooled() as c, uow(c, "pipeshub_node", ORG_A):
        assert c.execute("SELECT count(*) FROM pg_cursors").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM pg_tables WHERE schemaname LIKE 'pg_temp%'").fetchone()[0] == 0


def test_backup_and_restore_roles_can_do_one_thing_only(org_db):
    with org_db.admin() as c:
        c.execute("SET ROLE pipeshub_backup")
        assert count(c, "kt.docs") >= 15 and count(c, "kt.tokens") == 2 and count(c, "kt.apps") == 2   # reads every org, global included
        for stmt, err in (("ALTER TABLE kt.docs NO FORCE ROW LEVEL SECURITY", pg.InsufficientPrivilege),
                          ("DROP POLICY org_scope ON kt.docs", pg.InsufficientPrivilege),
                          ("DELETE FROM kt.docs", pg.InsufficientPrivilege), ("UPDATE kt.docs SET title = 'x'", pg.InsufficientPrivilege),
                          ("TRUNCATE kt.docs", pg.InsufficientPrivilege), ("INSERT INTO kt.cfg (org_id, k) VALUES ('" + ORG_A + "', 'bk')", pg.InsufficientPrivilege)):
            with pytest.raises(err):
                c.execute(stmt)
        c.execute("RESET ROLE")
        c.execute("SET ROLE pipeshub_restore")
        for stmt in ("SELECT * FROM kt.docs", "DELETE FROM kt.docs", "UPDATE kt.docs SET title = 'x'", "SELECT * FROM kt.tokens"):
            with pytest.raises(pg.InsufficientPrivilege):
                c.execute(stmt)
        with pytest.raises(pg.InsufficientPrivilege):
            c.execute("ALTER TABLE kt.docs DISABLE ROW LEVEL SECURITY")
        with pytest.raises(pg.InsufficientPrivilege):   # no window: an INSERT is refused by the policy
            c.execute("INSERT INTO kt.cfg (org_id, k) VALUES (%s, 'rs')", (ORG_A,))


def test_exempted_and_global_tables_are_reached_only_through_vetted_functions(org_db):
    with org_db.admin() as su:
        client_b = su.execute("SELECT client_id FROM kt.apps WHERE org_id = %s", (ORG_B,)).fetchone()[0]
    with org_db.pooled() as c:
        for stmt in (f"INSERT INTO kt.apps (org_id, id, client_id, redirect) VALUES ('{ORG_A}', 'x', '{client_b}', 'https://evil/cb') "
                     "ON CONFLICT (client_id) DO UPDATE SET redirect = excluded.redirect",
                     f"INSERT INTO kt.apps (org_id, id, client_id, redirect) VALUES ('{ORG_A}', 'x', '{client_b}', 'https://a/cb') ON CONFLICT (client_id) DO NOTHING",
                     "UPDATE kt.tokens SET user_id = 'attacker'", "SELECT * FROM kt.tokens"):
            with uow(c, "pipeshub_node", ORG_A), pytest.raises(pg.InsufficientPrivilege):
                c.execute(stmt)
        with uow(c, "pipeshub_node", ORG_A):
            other = c.execute("SELECT kt.set_redirect(%s, 'https://evil/cb')", (client_b,)).fetchone()[0]
            unknown = c.execute("SELECT kt.set_redirect(gen_random_uuid(), 'https://evil/cb')").fetchone()[0]
            assert other is False and unknown is False                                  # the same answer: no existence oracle
        with uow(c, "pipeshub_node", ORG_B):
            assert c.execute("SELECT kt.set_redirect(%s, 'https://b.example/new')", (client_b,)).fetchone()[0] is True
    with org_db.admin() as su:
        su.execute("UPDATE kt.apps SET redirect = 'https://b.example/cb' WHERE org_id = %s", (ORG_B,))


def test_write_and_jobs_functions_are_not_callable_by_the_wrong_logins(org_db):
    with org_db.pooled("connectors") as c, uow(c, "pipeshub_connectors", ORG_A), pytest.raises(pg.InsufficientPrivilege):
        c.execute("SELECT kt.set_redirect(gen_random_uuid(), 'https://x/cb')")        # not its writer role
    with org_db.pooled() as c, uow(c, "pipeshub_node", ORG_A), pytest.raises(pg.InsufficientPrivilege):
        c.execute("SELECT kt.import_tokens('h', %s, 'u')", (ORG_A,))                  # jobs only
    with org_db.pooled_as("pipeshub_jobs") as c, pytest.raises(pg.ObjectNotInPrerequisiteState):
        c.execute("SELECT kt.import_tokens('hash-new', %s, 'u')", (ORG_A,))              # no running job
    with org_db.admin() as su:
        su.execute("INSERT INTO platform.jobs (kind, status) VALUES ('restore', 'running')")
    with org_db.pooled_as("pipeshub_jobs") as c, c.transaction():
        c.execute("SELECT kt.import_tokens('hash-new', %s, 'u')", (ORG_A,))
        c.execute("SELECT 1")
    with org_db.admin() as su:
        su.execute("DELETE FROM kt.tokens WHERE token_hash = 'hash-new'")
        su.execute("DELETE FROM platform.jobs")
    with org_db.pooled() as c:                                                          # the prologue fence is callable by the login itself
        assert c.execute("SELECT kt.fence(7)").fetchone()[0] == 7
        c.rollback()
        c.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(pg.InvalidTransactionState, match="read committed"):
            c.execute("SELECT kt.fence(7)")
        c.rollback()


def test_fk_from_an_org_less_table_is_the_oracle_the_check_forbids(org_db):
    """Why check 08 exists: the RI check bypasses RLS. Planted, org A can probe org B's ids; the catalog reports it."""
    with org_db.admin(autocommit=False) as su:
        su.execute("SET LOCAL ROLE pipeshub_owner")
        su.execute("CREATE TABLE kt.probe (o text, i text, FOREIGN KEY (o, i) REFERENCES kt.docs (org_id, id))")
        su.execute("GRANT INSERT ON kt.probe TO pipeshub_node")
        su.commit()
    try:
        with org_db.pooled() as c:
            with uow(c, "pipeshub_node", ORG_A):
                c.execute("INSERT INTO kt.probe VALUES (%s, 'd1')", (ORG_B,))             # accepted: org B's d1 exists
            with pytest.raises(pg.ForeignKeyViolation), uow(c, "pipeshub_node", ORG_A):
                c.execute("INSERT INTO kt.probe VALUES (%s, 'nope')", (ORG_B,))
        assert any(r.startswith("08 FK into") for r, _ in org_db.violations())
    finally:
        with org_db.admin() as su:
            su.execute("DROP TABLE kt.probe")
    assert org_db.violations() == []


def test_a_partition_made_by_plain_ddl_is_reported(org_db):
    with org_db.admin(autocommit=False) as c:
        c.execute("SET LOCAL ROLE pipeshub_owner")
        c.execute("CREATE TABLE kt.events_2026_04 PARTITION OF kt.events FOR VALUES FROM ('2026-04-01') TO ('2026-05-01')")
        rules = {r.split()[0] for r, _ in org_db.violations(c)}
        c.rollback()
    assert {"02", "03"} <= rules


def test_apply_rls_partition_secures_a_new_leaf_before_attach(org_db):
    with org_db.admin() as c:
        c.execute("SET ROLE pipeshub_owner")
        c.execute("CREATE TABLE kt.events_2026_05 (LIKE kt.events INCLUDING ALL EXCLUDING IDENTITY)")
        c.execute("ALTER TABLE kt.events_2026_05 ADD CONSTRAINT events_2026_05_bounds CHECK (created_at >= '2026-05-01' AND created_at < '2026-06-01')")
        c.execute("SELECT kernel.apply_rls_partition('kt.events_2026_05', 'kt.events')")
        c.execute("ALTER TABLE kt.events ATTACH PARTITION kt.events_2026_05 FOR VALUES FROM ('2026-05-01') TO ('2026-06-01')")
        assert c.execute("SELECT count(*) FROM kernel.rls_violations").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.RaiseException, match="not a registered org-scoped root"):
            c.execute("SELECT kernel.apply_rls_partition('kt.events_2026_05', 'kt.tokens')")
        c.execute("ALTER TABLE kt.events DETACH PARTITION kt.events_2026_05")
        c.execute("DROP TABLE kt.events_2026_05")


# ---- thousands of pooled transactions over two server connections ----------------------------------------

def test_cross_org_reads_return_zero_rows_across_3000_pooled_transactions(org_db):
    threads, per_thread = 24, 140                         # 3,360 transactions through a pool of 2 server connections
    orgs = (ORG_A, ORG_B, "", ORG_P)
    errors, pids, done = [], set(), []
    lock = threading.Lock()

    def worker(n):
        mine = 0
        with org_db.pooled() as c:
            for i in range(per_thread):
                org = orgs[(n + i) % len(orgs)]
                with uow(c, "pipeshub_node", org):
                    pid = c.execute("SELECT pg_backend_pid()").fetchone()[0]
                    foreign = c.execute("SELECT count(*) FROM kt.docs WHERE org_id <> %s", (org,)).fetchone()[0]
                    foreign += c.execute("SELECT count(*) FROM kt.events WHERE org_id <> %s", (org,)).fetchone()[0]
                    foreign += c.execute("SELECT count(*) FROM kt.cfg WHERE org_id <> %s", (org,)).fetchone()[0]
                    own = c.execute("SELECT count(*) FROM kt.docs").fetchone()[0]
                    if foreign or own != (0 if org == "" else 5):
                        with lock:
                            errors.append((org, foreign, own))
                    with lock:
                        pids.add(pid)
                mine += 1
        with lock:
            done.append(mine)

    with concurrent.futures.ThreadPoolExecutor(threads) as ex:
        list(ex.map(worker, range(threads)))
    assert sum(done) >= 3000, done
    assert errors == []
    assert 1 <= len(pids) <= 2, pids                                      # the pooler really multiplexed 24 clients onto 2 servers


def test_sql_prepare_text_does_not_survive_the_reset(org_db):
    x = org_db.pooled(autocommit=True)
    x.execute("PREPARE leak AS SELECT 'secret-literal-of-org-a'")
    x.close()
    with org_db.pooled(autocommit=True) as y:
        names = [r[0] for r in y.execute("SELECT name FROM pg_prepared_statements WHERE name = 'leak'")]
        assert names == []


def test_large_objects_cannot_be_created_or_read_by_app_roles(org_db):
    with org_db.pooled() as c:
        with uow(c, "pipeshub_node", ORG_A), pytest.raises(pg.InsufficientPrivilege):
            c.execute("SELECT lo_from_bytea(0, 'org-a-secret'::bytea)")
        with uow(c, "pipeshub_node", ORG_B), pytest.raises(pg.InsufficientPrivilege):
            c.execute("SELECT lo_get(16384)")


def test_invoker_view_spellings_are_not_false_positives(org_db):
    with org_db.admin(autocommit=False) as c:
        try:
            c.execute("SET LOCAL ROLE pipeshub_owner")
            c.execute("CREATE VIEW kt.v_on WITH (security_invoker = on) AS SELECT id FROM kt.docs")
            c.execute("CREATE VIEW kt.v_one WITH (security_invoker = 1) AS SELECT id FROM kt.docs")
            c.execute("RESET ROLE")
            assert c.execute("SELECT count(*) FROM kernel.rls_violations").fetchone()[0] == 0
        finally:
            c.rollback()


def test_consumer_inbox_dedupes_per_org_and_leaks_nothing_across_orgs(org_db):
    ins = "INSERT INTO platform.consumer_inbox (org_id, message_id) VALUES (%s, %s) ON CONFLICT DO NOTHING"
    with org_db.pooled("indexing") as c:
        with uow(c, "pipeshub_indexing", ORG_A):
            assert c.execute(ins, (ORG_A, "m1")).rowcount == 1
            assert c.execute(ins, (ORG_A, "m1")).rowcount == 0               # a replay inside the org
            assert c.execute("SELECT count(*) FROM platform.consumer_inbox").fetchone()[0] == 1
        with uow(c, "pipeshub_indexing", ORG_B):
            assert c.execute(ins, (ORG_B, "m1")).rowcount == 1               # same id in another org: no collision, so no oracle
            assert c.execute("SELECT count(*) FROM platform.consumer_inbox").fetchone()[0] == 1
        with uow(c, "pipeshub_indexing", ORG_B), pytest.raises(pg.InsufficientPrivilege):
            c.execute(ins, (ORG_A, "m2"))
    with org_db.pooled_as("pipeshub_jobs") as c, c.transaction():             # pruning crosses orgs
        assert c.execute("DELETE FROM platform.consumer_inbox WHERE received_at < now() + interval '1 hour'").rowcount == 2
    assert org_db.violations() == []


# ---- planted defects: logins, default privileges, partitions, publications, extensions, administrators ------

MORE_PLANTS = [
    ("extra login in a service role", "CREATE USER etl; GRANT pipeshub_node TO etl WITH INHERIT FALSE, SET TRUE", "37"),
    ("extra login in jobs", "CREATE USER etl IN ROLE pipeshub_jobs", "37"),
    ("extra login in the group", "CREATE USER etl IN GROUP pipeshub_app", "37"),
    ("default privileges on tables", "ALTER DEFAULT PRIVILEGES IN SCHEMA kt GRANT SELECT ON TABLES TO pipeshub_node", "47"),
    ("default privileges on functions", "ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO pipeshub_ro", "47"),
    ("default privileges on sequences", "ALTER DEFAULT PRIVILEGES GRANT UPDATE ON SEQUENCES TO pipeshub_node", "47"),
    ("helper with a global UPDATE granted on with ADMIN only",
     "CREATE ROLE helper_role; GRANT UPDATE ON kt.tokens TO helper_role; GRANT helper_role TO pipeshub_node_login WITH ADMIN TRUE, INHERIT FALSE, SET FALSE", "37"),
    ("helper granted on through an administrator role",
     "UPDATE kernel.settings SET admins = '{dba}'; CREATE ROLE dba NOLOGIN; CREATE ROLE helper_role; GRANT UPDATE ON kt.tokens TO helper_role; "
     "GRANT helper_role TO dba; GRANT dba TO pipeshub_node_login", "37"),
    ("SECURITY DEFINER created by a superuser",
     "CREATE FUNCTION kt.sd() RETURNS int LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS 'SELECT 1'", "15"),
    ("partition attached without RLS",
     SET_OWNER + "CREATE TABLE kt.events_2026_04 (LIKE kt.events INCLUDING ALL EXCLUDING IDENTITY); "
     "ALTER TABLE kt.events ATTACH PARTITION kt.events_2026_04 FOR VALUES FROM ('2026-04-01') TO ('2026-05-01')", "02"),
    ("partition attached with RLS but without FORCE",
     SET_OWNER + "CREATE TABLE kt.events_2026_04 (LIKE kt.events INCLUDING ALL EXCLUDING IDENTITY); SELECT kernel.apply_rls_partition('kt.events_2026_04', 'kt.events'); "
     "ALTER TABLE kt.events_2026_04 NO FORCE ROW LEVEL SECURITY; "
     "ALTER TABLE kt.events ATTACH PARTITION kt.events_2026_04 FOR VALUES FROM ('2026-04-01') TO ('2026-05-01')", "02"),
    ("NO FORCE on a root", SET_OWNER + "ALTER TABLE kt.cfg NO FORCE ROW LEVEL SECURITY", "02"),
    ("publication on an org-scoped table", "CREATE PUBLICATION p13 FOR TABLE kt.docs", "48"),
    ("publication FOR ALL TABLES", "CREATE PUBLICATION p14 FOR ALL TABLES", "48"),
    ("event trigger",
     "CREATE FUNCTION public.evt() RETURNS event_trigger LANGUAGE plpgsql AS 'BEGIN END'; CREATE EVENT TRIGGER e15 ON ddl_command_start EXECUTE FUNCTION public.evt()", "48"),
    ("user mapping for a service role",
     "CREATE FOREIGN DATA WRAPPER fdw_x; CREATE SERVER s16 FOREIGN DATA WRAPPER fdw_x; CREATE USER MAPPING FOR pipeshub_node SERVER s16", "45"),
    ("foreign table with org_id granted to a service role",
     "CREATE FOREIGN DATA WRAPPER fdw_x; CREATE SERVER s17 FOREIGN DATA WRAPPER fdw_x; CREATE FOREIGN TABLE kt.ft (org_id text) SERVER s17; "
     "GRANT SELECT ON kt.ft TO pipeshub_node", "45"),
    ("materialized view of org data", SET_OWNER + "CREATE MATERIALIZED VIEW kt.mv18 AS SELECT org_id, id FROM kt.docs", "14"),
    ("kernel sequence with UPDATE for a service role",
     SET_OWNER + "CREATE SEQUENCE kernel.sq19; GRANT USAGE, UPDATE ON SEQUENCE kernel.sq19 TO pipeshub_node", "46"),
    ("setval on the shared identity sequence", "GRANT UPDATE ON SEQUENCE kt.events_id_seq TO pipeshub_connectors", "46"),
    ("domain whose check function is SECURITY DEFINER",
     "CREATE FUNCTION kt.dchk(text) RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS 'SELECT true'; "
     "CREATE DOMAIN kt.dom AS text CHECK (kt.dchk(VALUE))", "15"),
    ("LEAKPROOF function by a superuser",
     "CREATE FUNCTION kt.lk(text) RETURNS boolean LANGUAGE plpgsql LEAKPROOF AS 'BEGIN RAISE NOTICE ''%'', $1; RETURN true; END'", "49"),
    ("SECURITY DEFINER function added to an extension",
     "CREATE FUNCTION kt.sdx() RETURNS int LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS 'SELECT 1'; ALTER EXTENSION plpgsql ADD FUNCTION kt.sdx()", "50"),
    ("non-invoker view added to an extension", "CREATE VIEW kt.vx AS SELECT 1 AS x; ALTER EXTENSION plpgsql ADD VIEW kt.vx", "50"),
    ("BYPASSRLS administrator role reachable from a service login",
     "UPDATE kernel.settings SET admins = '{dba}'; CREATE ROLE dba NOLOGIN BYPASSRLS; GRANT SELECT ON kt.docs TO dba; GRANT dba TO pipeshub_node_login", "37"),
    ("a service login listed as an administrator",
     "UPDATE kernel.settings SET admins = '{pipeshub_node_login}'; ALTER ROLE pipeshub_node_login BYPASSRLS", "37 settings.admins"),
    ("ADMIN option on a service role", "GRANT pipeshub_node TO pipeshub_node_login WITH ADMIN TRUE", "37"),
    ("INHERITS child of a global table", SET_OWNER + "CREATE TABLE kt.tok_child () INHERITS (kt.tokens)", "01"),
    ("unregistered table keyed by another column",
     SET_OWNER + "CREATE TABLE kt.notes (owner_id text, body text); GRANT SELECT ON kt.notes TO pipeshub_node", "01"),
    ("grant on the settings table", "GRANT SELECT ON kernel.settings TO pipeshub_node", "30"),
    ("administrators may not link to application roles", "UPDATE kernel.settings SET admins = '{dba}'; CREATE ROLE dba NOLOGIN; GRANT pipeshub_jobs TO dba", "37"),
    ("22 a second superuser", "CREATE ROLE su2 SUPERUSER NOLOGIN", "22 superuser"),
    ("51 an extension that is not allowlisted", "UPDATE kernel.settings SET extensions = '{plpgsql}'; CREATE EXTENSION IF NOT EXISTS pg_trgm", "51"),
]


@pytest.mark.parametrize("plant", MORE_PLANTS, ids=[p[0] for p in MORE_PLANTS])
def test_more_plants_are_detected(org_db, plant):
    _, sql, expect = plant
    with org_db.admin(autocommit=False) as c:
        try:
            try:
                c.execute(sql)
            except psycopg.errors.FeatureNotSupported as exc:
                pytest.skip(str(exc))
            except psycopg.errors.UndefinedFile as exc:
                pytest.skip(str(exc))
            found = org_db.violations(c)
        finally:
            c.rollback()
    assert any(rule.startswith(expect) for rule, _ in found), f"{expect!r} not reported; got {sorted({r for r, _ in found})}"


# ---- plants written without reading the checks: the snapshot alone must see every one ---------------------------

BLIND_PLANTS = [
    ("connection limit on a login", "ALTER ROLE pipeshub_node_login CONNECTION LIMIT 5"),
    ("per-role setting", "ALTER ROLE pipeshub_ro SET statement_timeout = '1ms'"),
    ("per-database setting", "ALTER DATABASE t_rls SET row_security = off"),
    ("language usage revoked", "REVOKE USAGE ON LANGUAGE sql FROM PUBLIC"),
    ("database connect revoked", "REVOKE CONNECT ON DATABASE t_rls FROM PUBLIC"),
    ("new enum type", "CREATE TYPE kt.mood AS ENUM ('a')"),
    ("type usage granted", "CREATE TYPE kt.mood AS ENUM ('a'); GRANT USAGE ON TYPE kt.mood TO pipeshub_node"),
    ("trigger on an org-scoped table",
     "CREATE FUNCTION kt.tg() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'; CREATE TRIGGER tg BEFORE INSERT ON kt.cfg FOR EACH ROW EXECUTE FUNCTION kt.tg()"),
    ("extra check constraint", "ALTER TABLE kt.cfg ADD CONSTRAINT chk_true CHECK (true)"),
    ("new invoker view", "CREATE VIEW kt.vv WITH (security_invoker = true) AS SELECT id FROM kt.docs"),
    ("unique index added", "CREATE UNIQUE INDEX cfg_v_idx ON kt.cfg (org_id, v)"),
    ("non-unique index added", "CREATE INDEX cfg_v_idx2 ON kt.cfg (v)"),
    ("function setting changed", "ALTER FUNCTION kt.docs_search(text) SET work_mem = '1MB'"),
    ("function body replaced",
     "CREATE OR REPLACE FUNCTION kt.docs_search(p_pattern text) RETURNS TABLE (id text) LANGUAGE sql STABLE SECURITY DEFINER "
     "SET search_path = pg_catalog, kt, pg_temp AS 'SELECT d.id FROM kt.docs d'"),
    ("table owner changed", "ALTER TABLE kt.cfg OWNER TO pipeshub_jobs"),
    ("storage option changed", "ALTER TABLE kt.cfg SET (fillfactor = 50)"),
    ("new schema", "CREATE SCHEMA other"),
    ("schema owner changed", "ALTER SCHEMA kt OWNER TO pipeshub_node"),
    ("login expiry set", "ALTER ROLE pipeshub_jobs VALID UNTIL '2030-01-01'"),
    ("bulk grant", "GRANT SELECT ON ALL TABLES IN SCHEMA kt TO pipeshub_connectors"),
    ("RLS disabled", "ALTER TABLE kt.docs DISABLE ROW LEVEL SECURITY"),
    ("large object created", "SELECT lo_create(0)"),
    ("group granted to a login", "GRANT pipeshub_app TO pipeshub_node_login"),
    ("service role stops inheriting", "ALTER ROLE pipeshub_node NOINHERIT"),
    ("operator in a user schema", "CREATE OPERATOR kt.=== (LEFTARG = text, RIGHTARG = text, FUNCTION = texteq)"),
    ("cast between user-visible types", "CREATE DOMAIN kt.d1 AS int; CREATE CAST (kt.d1 AS text) WITH INOUT"),
    ("plain public function", "CREATE FUNCTION public.plain() RETURNS int LANGUAGE sql AS 'SELECT 1'"),
    ("aggregate", "CREATE AGGREGATE kt.agg(int) (SFUNC = int4pl, STYPE = int)"),
    ("sequence added", "CREATE SEQUENCE kt.s1"),
    ("sequence usage granted", "GRANT USAGE ON SEQUENCE kt.events_id_seq TO pipeshub_connectors"),
    ("function execute revoked", "REVOKE EXECUTE ON FUNCTION kt.fence(int) FROM pipeshub_node_login"),
    ("policy altered in a harmless-looking way", "ALTER POLICY backup_read ON kt.docs TO pipeshub_backup, pipeshub_jobs"),
    ("column renamed", "ALTER TABLE kt.cfg RENAME COLUMN v TO val"),
    ("column default privileges", "GRANT UPDATE (v) ON kt.cfg TO pipeshub_node"),
    ("publication created", "CREATE PUBLICATION p_blind FOR TABLE kt.cfg"),
    ("foreign data wrapper", "CREATE FOREIGN DATA WRAPPER fdw_blind"),
    ("schema usage revoked", "REVOKE USAGE ON SCHEMA kt FROM pipeshub_app"),
    ("role created", "CREATE ROLE stray NOLOGIN"),
    ("role renamed away", "ALTER ROLE pipeshub_query RENAME TO pipeshub_query_x"),
    ("login flag flipped", "ALTER ROLE pipeshub_backup NOLOGIN"),
    ("migrate made a member of another role", "GRANT pipeshub_jobs TO pipeshub_migrate"),
    ("temp privilege granted", "GRANT TEMPORARY ON DATABASE t_rls TO pipeshub_connectors"),
    ("table dropped", "DROP TABLE kt.apps CASCADE"),
]


@pytest.mark.parametrize("plant", BLIND_PLANTS, ids=[p[0] for p in BLIND_PLANTS])
def test_blind_plant_is_seen_by_the_snapshot_alone(org_db, plant):
    _, sql = plant
    with org_db.admin(autocommit=False) as c:
        try:
            c.execute("SET LOCAL ROLE postgres")
            c.execute(sql)
            found = org_db.surface_only(c)
        finally:
            c.rollback()
    assert found, "the surface snapshot did not change"


def test_large_object_functions_are_a_warning_on_a_declared_managed_platform(org_db):
    with org_db.admin(autocommit=False) as c:
        try:
            c.execute("GRANT EXECUTE ON FUNCTION pg_catalog.lo_get(oid) TO PUBLIC")
            assert any(r.startswith("43 role can use") for r, _ in org_db.violations(c))
            assert any(x.startswith("lofunctions public_execute=1") for x in surface.live_lines(c))
            c.execute("""UPDATE platform.deployment_profile SET profile = '{"managed": true}'""")
            assert org_db.violations(c) == []                                              # a known exception on a managed platform ...
            assert c.execute("SELECT count(*) FROM kernel.rls_warnings").fetchone()[0] == 1   # ... that is still reported
        finally:
            c.rollback()


# ---- planted defects: system schemas, parameters, tablespaces, extension members, switches -------------------

SYSTEM_PLANTS = [
    ("system catalog table granted: pg_statistic", "GRANT SELECT ON pg_catalog.pg_statistic TO pipeshub_node", "53"),
    ("system file reader granted", "GRANT EXECUTE ON FUNCTION pg_catalog.pg_read_binary_file(text) TO pipeshub_node", "53"),
    ("definer view in information_schema over a global table",
     SET_OWNER.replace("pipeshub_owner", "postgres") + "CREATE VIEW information_schema.tokv WITH (security_barrier) AS SELECT token_hash FROM kt.tokens; "
     "GRANT SELECT ON information_schema.tokv TO pipeshub_node", "52"),
    ("SECURITY DEFINER function created in pg_catalog",
     "CREATE FUNCTION pg_catalog.sd_leak() RETURNS SETOF text LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS 'SELECT token_hash FROM kt.tokens'; "
     "GRANT EXECUTE ON FUNCTION pg_catalog.sd_leak() TO pipeshub_node", "52"),
    ("parameter privilege granted", "GRANT SET ON PARAMETER session_replication_role TO pipeshub_node", "54"),
    ("administrator role in a predefined role",
     "UPDATE kernel.settings SET admins = '{dba}'; CREATE ROLE dba NOLOGIN; GRANT pg_read_all_data TO dba", "50"),
    ("password hashes readable", "GRANT SELECT ON pg_catalog.pg_authid TO pipeshub_node", "53"),
    ("CREATE on information_schema", "GRANT CREATE ON SCHEMA information_schema TO pipeshub_node", "50"),
    ("CREATE on a tablespace for PUBLIC", "GRANT CREATE ON TABLESPACE pg_default TO PUBLIC", "54"),
    ("CREATE on a tablespace for a service role", "GRANT CREATE ON TABLESPACE pg_default TO pipeshub_node", "54"),
    ("owner role in a predefined role", "GRANT pg_monitor TO pipeshub_owner", "50"),
    ("SECURITY DEFINER function added to an allowed extension is not exempt",
     "CREATE FUNCTION kt.sdp() RETURNS SETOF text LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS 'SELECT token_hash FROM kt.tokens'; "
     "ALTER EXTENSION plpgsql ADD FUNCTION kt.sdp()", "35"),
    ("view added to an allowed extension is not exempt", "CREATE VIEW kt.vx2 AS SELECT token_hash FROM kt.tokens; ALTER EXTENSION plpgsql ADD VIEW kt.vx2", "14 view"),
]


@pytest.mark.parametrize("plant", SYSTEM_PLANTS, ids=[p[0] for p in SYSTEM_PLANTS])
def test_system_schema_parameter_and_tablespace_plants_are_detected(org_db, plant):
    _, sql, expect = plant
    with org_db.admin(autocommit=False) as c:
        try:
            c.execute(sql)
            found = org_db.violations(c)
        finally:
            c.rollback()
    assert any(rule.startswith(expect) for rule, _ in found), f"{expect!r} not reported; got {sorted({r for r, _ in found})[:12]}"


def test_independently_written_plants_are_detected(org_db):
    from .extra_plants import BLIND
    missed = []
    for name, sql in BLIND:
        with org_db.admin(autocommit=False) as c:
            try:
                c.execute(sql)
                if not org_db.violations(c):
                    missed.append(name)
            except psycopg.Error as exc:
                missed.append(f"{name}: plant failed: {str(exc).splitlines()[0]}")
            finally:
                c.rollback()
    assert missed == []


def test_the_bootstrap_superuser_name_is_not_part_of_the_snapshot():
    live = ["role dbadmin login=true", "database owner=dbadmin", "language c owner=dbadmin", "role dbadminx login=true", 'role "dbadmin" x']
    assert surface.normalise(live, "dbadmin") == {"role <bootstrap-superuser> login=true", "database owner=<bootstrap-superuser>",
                                                  "language c owner=<bootstrap-superuser>", "role dbadminx login=true", 'role "dbadmin" x'}
    assert surface.normalise(["role postgres x"], "postgres") == surface.normalise(["role other x".replace("other", "postgres")], "postgres")


def test_the_administrator_list_is_in_the_snapshot(org_db):
    with org_db.admin() as c:
        assert any(x.startswith("settings admins=") for x in surface.live_lines(c))
