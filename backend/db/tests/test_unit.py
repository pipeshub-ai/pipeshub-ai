"""Unit tests: no database needed."""
from pathlib import Path

import pytest

from db import epoch_lint, lint, locations, migrate
from db.boot_assert import schema_problems


def _rev(d: Path, branch: str, n: int, down: int | None, name: str = "x", extra: str = ""):
    (d / f"{n:04d}_{name}.py").write_text(
        f'revision = "{branch}_{n:04d}"\ndown_revision = {f"{branch}_{down:04d}"!r}\n{extra}\n' if down
        else f'revision = "{branch}_{n:04d}"\ndown_revision = None\n{extra}\n')


@pytest.fixture
def branch(tmp_path):
    d = tmp_path / "identity"
    d.mkdir()
    _rev(d, "identity", 1, None)
    _rev(d, "identity", 2, 1)
    _rev(d, "identity", 3, 2, extra="min_code_epoch = 3")
    return d


def test_epoch_lint_clean(branch):
    assert epoch_lint.lint_branch(branch) == []


def test_epoch_lint_fork_from_a_stale_main(branch):
    _rev(branch, "identity", 4, 2, name="other_pr")
    _rev(branch, "identity", 4, 3, name="this_pr")
    msgs = "\n".join(epoch_lint.lint_branch(branch))
    assert "expected 'identity_0003'" in msgs and "duplicate revision id identity_0004" in msgs


def test_epoch_lint_fork_gives_two_heads(branch):
    (branch / "0004_b.py").write_text('revision = "identity_0004"\ndown_revision = "identity_0002"\n')
    msgs = "\n".join(epoch_lint.lint_branch(branch))
    assert "2 heads" in msgs


def test_epoch_lint_rejects_bad_id_and_file_prefix(branch):
    (branch / "0009_bad.py").write_text('revision = "users_9"\ndown_revision = "identity_0003"\n')
    (branch / "0005_mismatch.py").write_text('revision = "identity_0004"\ndown_revision = "identity_0003"\n')
    msgs = "\n".join(epoch_lint.lint_branch(branch))
    assert "is not identity_NNNN" in msgs and "file prefix does not match" in msgs


def test_shipped_branches_pass_both_lints():
    assert epoch_lint.main([]) == 0
    assert lint.main([]) == 0


def test_statement_splitter_ignores_semicolons_in_bodies_and_strings():
    sql = "-- a;b\nSELECT 'x;y'; /* ; */ CREATE FUNCTION f() RETURNS int AS $f$ BEGIN RETURN 1; END $f$ LANGUAGE plpgsql; SELECT 2"
    assert [s.split()[0] for s in lint.statements(sql)] == ["SELECT", "CREATE", "SELECT"]


@pytest.mark.parametrize("sql,needle", [
    ("DROP TABLE a.b", "min_code_epoch"),
    ("ALTER TABLE a.b RENAME COLUMN x TO y", "min_code_epoch"),
    ("ALTER TABLE a.b ALTER COLUMN x TYPE bigint", "min_code_epoch"),
    ("CREATE INDEX CONCURRENTLY i ON a.b (x)", "ensure_index"),
    ("CREATE INDEX i ON a.b (x)", "existing table"),
    ("ALTER TABLE a.b ADD CONSTRAINT c CHECK (x > 0)", "NOT VALID"),
    ("ALTER TABLE a.b ADD FOREIGN KEY (x) REFERENCES a.c (id)", "NOT VALID"),
    ("CREATE TABLE a.b (org_id text, id text)", "apply_rls"),
    ("CREATE TABLE a.items (org_id text, id text); SELECT kernel.apply_rls('a.items_history', 'org')", "table a.items has org_id but no kernel.apply_rls"),   # a different table
    ("CREATE TABLE y.x (org_id text, id text); SELECT kernel.apply_rls('y.x.items', 'org')", "table y.x has org_id but no kernel.apply_rls"),
    ("CREATE FUNCTION f() RETURNS int LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$", "SECURITY DEFINER"),
    ("create function f() returns int language sql security\n\tdefiner as $$ select 1 $$", "SECURITY DEFINER"),
    ("CREATE FUNCTION f() RETURNS int LANGUAGE sql SECURITY   DEFINER AS $$ SELECT 1 $$", "SECURITY DEFINER"),
    ("GRANT SELECT ON a.b TO PUBLIC", "GRANT to PUBLIC"),
    ("grant usage, create on schema a to pipeshub_node", "GRANT CREATE"),
    ("GRANT ALL ON SCHEMA a TO pipeshub_node", "GRANT CREATE"),
    ("ALTER ROLE r BYPASSRLS", "BYPASSRLS"),
    ("CREATE MATERIALIZED VIEW m AS SELECT 1", "materialized"),
    ("ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO pipeshub_app", "DEFAULT PRIVILEGES"),
])
def test_lint_rejects(tmp_path, sql, needle):
    f = tmp_path / "0001_x.sql"
    f.write_text(sql)
    assert needle in "\n".join(lint.lint_file(f))


@pytest.mark.parametrize("sql", [
    "CREATE TABLE a.b (org_id text, id text); SELECT kernel.apply_rls('a.b', 'org'); CREATE INDEX i ON a.b (id)",
    "CREATE TABLE a.b (id text, c int CHECK (c > 0)); ALTER TABLE a.b ADD CONSTRAINT k CHECK (c < 9)",
    "CREATE TABLE a.b (x int); ALTER TABLE a.b ADD CONSTRAINT c CHECK (x > 0) NOT VALID",
    "ALTER ROLE r NOBYPASSRLS",
    "GRANT USAGE ON SCHEMA a TO pipeshub_node; REVOKE ALL ON FUNCTION f() FROM PUBLIC",
    "CREATE TABLE a.b (id text); -- DROP TABLE a.b",
])
def test_lint_accepts(tmp_path, sql):
    f = tmp_path / "0001_x.sql"
    f.write_text(sql)
    assert lint.lint_file(f) == []


def test_lint_allows_destructive_change_in_a_contract_revision(tmp_path):
    (tmp_path / "0003_x.sql").write_text("ALTER TABLE a.b DROP COLUMN old")
    (tmp_path / "0003_x.py").write_text("min_code_epoch = 3\n")
    assert lint.lint_file(tmp_path / "0003_x.sql") == []


_COMPAT = [   # (rule, breaking sql, harmless sql)
    ("NOT NULL column without a default", "ALTER TABLE a.b ADD COLUMN c int NOT NULL", "ALTER TABLE a.b ADD COLUMN c int NOT NULL DEFAULT 0; ALTER TABLE a.b ADD COLUMN d int"),
    ("SET NOT NULL", "ALTER TABLE a.b ALTER COLUMN c SET NOT NULL", "ALTER TABLE a.b ALTER COLUMN c SET DEFAULT 1"),
    ("new CHECK", "ALTER TABLE a.b ADD CONSTRAINT k CHECK (c > 0) NOT VALID", "CREATE TABLE a.b (c int); ALTER TABLE a.b ADD CONSTRAINT k CHECK (c > 0)"),
    ("new FK", "ALTER TABLE a.b ADD COLUMN c int REFERENCES a.d (id)", "ALTER TABLE a.b ADD COLUMN c int"),
    ("new UNIQUE constraint", "ALTER TABLE a.b ADD CONSTRAINT k UNIQUE (c)", "ALTER TABLE a.b ADD COLUMN u text"),
    ("policy", "CREATE POLICY p ON a.b USING (true)", "CREATE TABLE a.b (c int); CREATE POLICY p ON a.b USING (true)"),
    ("FORCE RLS", "ALTER TABLE a.b FORCE ROW LEVEL SECURITY", "ALTER TABLE a.b OWNER TO r"),
    ("apply_rls on an existing table", "SELECT kernel.apply_rls('a.b', 'org')", "CREATE TABLE a.b (id text); SELECT kernel.apply_rls('a.b', 'org')"),
    ("replaced function", "CREATE OR REPLACE FUNCTION a.f() RETURNS int LANGUAGE sql AS $$ SELECT 2 $$", "CREATE FUNCTION a.f() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$; CREATE OR REPLACE FUNCTION a.f() RETURNS int LANGUAGE sql AS $$ SELECT 2 $$"),
    ("dropped function", "DROP FUNCTION a.f(int)", "CREATE FUNCTION a.f() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$"),
    ("unique index", "CREATE UNIQUE INDEX i ON a.b (c)", "CREATE TABLE a.b (c int); CREATE UNIQUE INDEX i ON a.b (c)"),
]


@pytest.mark.parametrize("rule,bad,good", _COMPAT, ids=[c[0] for c in _COMPAT])
def test_lint_older_code_rules(tmp_path, rule, bad, good):
    f = tmp_path / "0002_x.sql"
    f.write_text(bad)
    assert any("can break older code" in m for m in lint.lint_file(f)), rule
    f.write_text(good)
    assert not any("can break older code" in m for m in lint.lint_file(f)), rule
    f.write_text(bad)
    (tmp_path / "0002_x.py").write_text("min_code_epoch = 2\n")                       # the epoch mark clears it
    assert not any("can break older code" in m for m in lint.lint_file(f))
    (tmp_path / "0002_x.py").write_text('older_code_compatible = "older code never touches this object"\n')
    assert not any("can break older code" in m for m in lint.lint_file(f))             # so does a reasoned waiver


_T = "conversation.chat_sessions"
_CK = f"ALTER TABLE {_T} ADD CONSTRAINT k CHECK (title <> '') NOT VALID;"
_PLANTED_CASES = [   # (case, sql, python revision text): each must be flagged
    ("01_baseline_check", _CK, ""),
    ("02_if_exists_only", f"ALTER TABLE IF EXISTS ONLY {_T} ADD CONSTRAINT k CHECK (title <> '') NOT VALID;", ""),
    ("02b_only_if_exists", f"ALTER TABLE ONLY IF EXISTS {_T} ADD CONSTRAINT k CHECK (title <> '') NOT VALID;", ""),
    ("03_quoted_constraint_space", f"ALTER TABLE {_T} ADD CONSTRAINT \"my k\" CHECK (title <> '') NOT VALID;", ""),
    ("04_quoted_table", "ALTER TABLE \"conversation\".\"chat_sessions\" ADD CHECK (title <> '') NOT VALID;", ""),
    ("05_comment_split", f"ALTER TABLE {_T} -- why\n  /* more */ ADD CHECK (title <> '') NOT VALID;", ""),
    ("06_create_if_not_exists", f"CREATE TABLE IF NOT EXISTS {_T} (id text);\nALTER TABLE {_T} ADD CHECK (title <> '') NOT VALID;", ""),
    ("07_do_block_dynamic", "DO $$ BEGIN EXECUTE 'ALTER TABLE x ADD CHECK (a <> 1) NOT VALID'; END $$;", ""),
    ("08_drop_default", f"ALTER TABLE {_T} ALTER COLUMN title DROP DEFAULT;", ""),
    ("09_revoke_service", f"REVOKE SELECT ON {_T} FROM pipeshub_node;", ""),
    ("10_new_trigger", f"CREATE TRIGGER t BEFORE INSERT ON {_T} FOR EACH ROW EXECUTE FUNCTION kernel.reject();", ""),
    ("11_policy_quoted_space", f"CREATE POLICY \"p 1\" ON {_T} USING (false);", ""),
    ("12_procedure", "CREATE OR REPLACE PROCEDURE kernel.p() LANGUAGE sql AS $$ SELECT 1 $$;", ""),
    ("12b_drop_procedure", "DROP PROCEDURE kernel.p();", ""),
    ("13_apply_rls_named", f"SELECT kernel.apply_rls(p_rel => '{_T}', p_class => 'org');", ""),
    ("14_min_code_zero", _CK, "min_code_epoch = 0\n"),
    ("15_min_code_comment", _CK, "# min_code_epoch is not needed here\n"),
    ("17_waiver_one_word", _CK, 'older_code_compatible = "fine"\n'),
    ("18_drop_view", "DROP VIEW kernel.rls_violations;", ""),
    ("19_alter_domain", "ALTER DOMAIN kernel.d ADD CONSTRAINT c CHECK (VALUE > 0) NOT VALID;", ""),
    ("20_not_null_default_null", f"ALTER TABLE {_T} ADD COLUMN x int NOT NULL DEFAULT NULL;", ""),
    ("21_set_not_null_lower", f"alter table {_T} alter column title set not null;", ""),
    ("22_unique_index_sql", f"CREATE UNIQUE INDEX u ON {_T} (title);", ""),
    ("23_set_schema", f"ALTER TABLE {_T} SET SCHEMA other;", ""),
    ("24_replace_view", "CREATE OR REPLACE VIEW kernel.v AS SELECT 1 AS a;", ""),
    ("25_op_execute", "SELECT 1;", "def upgrade():\n    op.execute(\"ALTER TABLE conversation.chat_sessions ADD CHECK (title <> '') NOT VALID\")\n"),
    ("25b_op_execute_dynamic", "SELECT 1;", "def upgrade():\n    op.execute(f\"ALTER TABLE {t} DROP DEFAULT\")\n"),
    ("26_multiline_newline", f"ALTER TABLE {_T}\n  ADD CONSTRAINT k CHECK (title <> '') NOT VALID;", ""),
    ("27_double_space", f"ALTER TABLE {_T}  ADD CONSTRAINT k CHECK (title <> '') NOT VALID;", ""),
    ("28_multiline_not_null", f"ALTER TABLE {_T}\n  ADD COLUMN x int NOT NULL;", ""),
    ("29_multiline_policy", f"CREATE POLICY p\n  ON {_T} USING (false);", ""),
    ("30_multiline_function", "CREATE OR REPLACE FUNCTION\n  kernel.assert_profile_epoch(p_epoch int) RETURNS void LANGUAGE sql AS $$ SELECT $$;", ""),
    ("31_multiline_unique", f"CREATE UNIQUE INDEX u\n  ON {_T} (title);", ""),
]


@pytest.mark.parametrize("name,sql,py", _PLANTED_CASES, ids=[c[0] for c in _PLANTED_CASES])
def test_lint_flags_the_adversarial_spellings(tmp_path, name, sql, py):
    (tmp_path / "0001_base.sql").write_text(f"CREATE TABLE {_T} (id text, title text)")
    f = tmp_path / "0002_x.sql"
    f.write_text(sql)
    (tmp_path / "0002_x.py").write_text('revision = "x"\n' + py)
    assert lint.lint_file(f), name


def test_lint_accepts_a_four_word_waiver_of_any_text(tmp_path):                 # a waiver is a review item, not machine-checked
    f = tmp_path / "0002_x.sql"
    f.write_text(_CK)
    (tmp_path / "0002_x.py").write_text('older_code_compatible = "a b c d"\n')
    assert lint.lint_file(f) == []


@pytest.mark.parametrize("sql", [
    "REVOKE ALL ON FUNCTION a.f() FROM PUBLIC",
    "REVOKE CREATE ON SCHEMA a FROM pipeshub_vetted_a",
    "CREATE TABLE a.t (id int); REVOKE ALL ON a.t FROM pipeshub_node",
    "CREATE VIEW a.v AS SELECT 1; CREATE OR REPLACE VIEW a.v AS SELECT 2",
    "ALTER TABLE a.b ADD COLUMN c int NOT NULL DEFAULT 0, ADD COLUMN d int",
])
def test_lint_older_code_accepts(tmp_path, sql):
    f = tmp_path / "0002_x.sql"
    f.write_text(sql)
    assert not any("can break older code" in m for m in lint.lint_file(f))


def test_lint_python_sql_is_read_from_upgrade_only(tmp_path):
    f = tmp_path / "0002_x.sql"
    f.write_text("SELECT 1")
    (tmp_path / "0002_x.py").write_text('def downgrade():\n    op.execute("ALTER TABLE a.b DROP DEFAULT")\n\n\ndef upgrade():\n    pass\n')
    assert lint.lint_file(f) == []


# More planted spellings: (case, sql, python revision text)
_MORE_FLAGGED = [
    ('40_do_static_ddl', "DO $$ BEGIN ALTER TABLE conversation.chat_sessions ADD CONSTRAINT k CHECK (title <> '') NOT VALID; END $$;", 'revision = "x"\n'),
    ('41_do_lower_tag', "do $x$ begin alter table conversation.chat_sessions add constraint k check (title <> '') not valid; end $x$;", 'revision = "x"\n'),
    ('42_do_perform_apply_rls', "DO $$ BEGIN PERFORM kernel.apply_rls('conversation.chat_sessions', 'org'); END $$;", 'revision = "x"\n'),
    ('43_do_revoke_static', 'DO $$ BEGIN REVOKE SELECT ON conversation.chat_sessions FROM pipeshub_node; END $$;', 'revision = "x"\n'),
    ('44_case_mixed', "AlTeR TaBlE conversation.chat_sessions AdD CoNsTrAiNt k ChEcK (title <> '') NoT VaLiD;", 'revision = "x"\n'),
    ('45_revoke_grant_option', 'REVOKE GRANT OPTION FOR SELECT ON conversation.chat_sessions FROM pipeshub_node;', 'revision = "x"\n'),
    ('46_revoke_role_membership', 'REVOKE pipeshub_app FROM pipeshub_node;', 'revision = "x"\n'),
    ('47_revoke_all_tables_in_schema', 'REVOKE SELECT ON ALL TABLES IN SCHEMA conversation FROM pipeshub_node;', 'revision = "x"\n'),
    ('48_revoke_ro_backup', 'REVOKE SELECT ON conversation.chat_sessions FROM pipeshub_ro, pipeshub_backup;', 'revision = "x"\n'),
    ('49_alter_policy_quoted_schema', 'ALTER POLICY p ON "conversation".chat_sessions USING (false);', 'revision = "x"\n'),
    ('52_alter_type_rename_value', "ALTER TYPE kernel.t RENAME VALUE 'a' TO 'b';", 'revision = "x"\n'),
    ('53_drop_trigger_if_exists', 'DROP TRIGGER IF EXISTS t ON conversation.chat_sessions;', 'revision = "x"\n'),
    ('56_drop_constraint', 'ALTER TABLE conversation.chat_sessions DROP CONSTRAINT k;', 'revision = "x"\n'),
    ('57_drop_extension', 'DROP EXTENSION pg_trgm;', 'revision = "x"\n'),
    ('58_drop_function_existing', 'DROP FUNCTION IF EXISTS kernel.assert_profile_epoch(int);', 'revision = "x"\n'),
    ('60_create_rule', 'CREATE RULE r AS ON INSERT TO conversation.chat_sessions DO INSTEAD NOTHING;', 'revision = "x"\n'),
    ('61_fstring', '-- nothing\nSELECT 1;', 'from alembic import op\nTBL = \'conversation.chat_sessions\'\ndef upgrade():\n    op.execute(f"ALTER TABLE {TBL} ADD CONSTRAINT k CHECK (title <> \'\') NOT VALID")\n'),
    ('62_concat', '-- nothing\nSELECT 1;', "from alembic import op\ndef upgrade():\n    op.execute('ALTER TABLE ' + 'conversation.chat_sessions' + ' ADD CONSTRAINT k CHECK (title <> '') NOT VALID')\n"),
    ('63_alias_bind', '-- nothing\nSELECT 1;', 'from alembic import op\ndef upgrade():\n    conn = op.get_bind()\n    run = conn.exec_driver_sql\n    run("ALTER TABLE conversation.chat_sessions ADD CONSTRAINT k CHECK (title <> \'\') NOT VALID")\n'),
    ('64_helper_outside_upgrade', '-- nothing\nSELECT 1;', 'from alembic import op\ndef _apply():\n    op.execute("ALTER TABLE conversation.chat_sessions ADD CONSTRAINT k CHECK (title <> \'\') NOT VALID")\ndef upgrade():\n    _apply()\n'),
    ('65_module_level_execute', '-- nothing\nSELECT 1;', 'from alembic import op\nSQL = "ALTER TABLE conversation.chat_sessions ADD CONSTRAINT k CHECK (title <> \'\') NOT VALID"\ndef upgrade():\n    op.execute(SQL)\n'),
    ('66_getattr', '-- nothing\nSELECT 1;', 'from alembic import op\ndef upgrade():\n    getattr(op, \'execute\')("ALTER TABLE conversation.chat_sessions ADD CONSTRAINT k CHECK (title <> \'\') NOT VALID")\n'),
    ('67_alembic_op_api', '-- nothing\nSELECT 1;', 'from alembic import op\nimport sqlalchemy as sa\ndef upgrade():\n    op.create_check_constraint(\'k\', \'chat_sessions\', sa.text("title <> \'\'"), schema=\'conversation\')\n'),
    ('68_op_alter_column', '-- nothing\nSELECT 1;', "from alembic import op\ndef upgrade():\n    op.alter_column('chat_sessions', 'title', nullable=False, schema='conversation')\n"),
    ('70_upgrade_lambda', '-- nothing\nSELECT 1;', 'from alembic import op\ndef upgrade():\n    for s in ["ALTER TABLE conversation.chat_sessions ADD CONSTRAINT k CHECK (title <> \'\') NOT VALID"]:\n        op.execute(s)\n'),
    ('71_revoke_from_vetted_owner', 'REVOKE SELECT ON conversation.chat_sessions FROM pipeshub_vetted_conversation;', 'revision = "x"\n'),
    ('72_revoke_exec_vetted', 'REVOKE EXECUTE ON FUNCTION conversation.search_recent(text, integer) FROM pipeshub_node;', 'revision = "x"\n'),
    ('73_revoke_quoted_role', 'REVOKE SELECT ON conversation.chat_sessions FROM "pipeshub_node";', 'revision = "x"\n'),
    ('74_revoke_group', 'REVOKE SELECT ON conversation.chat_sessions FROM GROUP pipeshub_node;', 'revision = "x"\n'),
    ('75_drop_owned', 'DROP OWNED BY pipeshub_node;', 'revision = "x"\n'),
    ('76_alter_role_nologin', 'ALTER ROLE pipeshub_node_login NOLOGIN;', 'revision = "x"\n'),
    ('77_alter_default_privs', 'ALTER DEFAULT PRIVILEGES IN SCHEMA conversation REVOKE SELECT ON TABLES FROM pipeshub_node;', 'revision = "x"\n'),
    ('78_do_create_or_replace_fn', "DO $$ BEGIN EXECUTE format('CREATE OR REPLACE FUNCTION kernel.f() RETURNS int LANGUAGE sql AS %L', 'SELECT 1'); END $$;", 'revision = "x"\n'),
    ('80_truncate', 'TRUNCATE conversation.chat_sessions;', 'revision = "x"\n'),
    ('81_drop_role', 'DROP ROLE pipeshub_node;', 'revision = "x"\n'),
    ('83_drop_unique_constraint', 'ALTER TABLE conversation.chat_sessions DROP CONSTRAINT chat_sessions_pkey;', 'revision = "x"\n'),
    ('84_detach_partition', 'ALTER TABLE conversation.chat_sessions DETACH PARTITION conversation.chat_sessions_def;', 'revision = "x"\n'),
]

_MORE_ACCEPTED = [
    ('50_with_cte_update', "WITH x AS (SELECT 1) UPDATE conversation.chat_sessions SET title = '' WHERE false;", 'revision = "x"\n'),
    ('51_alter_type_add_value', "ALTER TYPE kernel.t ADD VALUE 'x';", 'revision = "x"\n'),
    ('54_alter_table_disable_trigger', 'ALTER TABLE conversation.chat_sessions DISABLE TRIGGER ALL;', 'revision = "x"\n'),
    ('55_alter_column_set_default_expr', 'ALTER TABLE conversation.chat_sessions ALTER COLUMN title SET DEFAULT kernel.f();', 'revision = "x"\n'),
    ('59_alter_table_set_logged', 'ALTER TABLE conversation.chat_sessions SET UNLOGGED;', 'revision = "x"\n'),
    ('69_run_sql_other_file', '-- nothing\nSELECT 1;', "from db.lib.run_sql import run_sql\ndef upgrade():\n    run_sql(__file__.replace('69_run_sql_other_file', 'zz_hidden'))\n"),
    ('82_delete_rows', 'DELETE FROM conversation.chat_sessions;', 'revision = "x"\n'),
]


@pytest.mark.parametrize("name,sql,py", _MORE_FLAGGED, ids=[c[0] for c in _MORE_FLAGGED])
def test_lint_flags_round_two_spellings(tmp_path, name, sql, py):
    (tmp_path / "0001_base.sql").write_text(f"CREATE TABLE {_T} (id text, title text)")
    f = tmp_path / "0002_x.sql"
    f.write_text(sql)
    (tmp_path / "0002_x.py").write_text(py or 'revision = "x"\n')
    assert lint.lint_file(f), name


@pytest.mark.parametrize("name,sql,py", _MORE_ACCEPTED, ids=[c[0] for c in _MORE_ACCEPTED])
def test_lint_accepts_round_two_harmless_changes(tmp_path, name, sql, py):
    f = tmp_path / "0002_x.sql"
    f.write_text(sql)
    (tmp_path / "0002_x.py").write_text(py or 'revision = "x"\n')
    assert not any("can break older code" in m for m in lint.lint_file(f)), name


def test_lint_create_if_not_exists_of_another_modules_table(tmp_path, monkeypatch):
    other, mine = tmp_path / "a", tmp_path / "b"
    other.mkdir(), mine.mkdir()
    (other / "0001_x.sql").write_text("CREATE TABLE a.t (id int)")
    f = mine / "0001_y.sql"
    f.write_text("CREATE TABLE IF NOT EXISTS a.t (id int); ALTER TABLE a.t ADD COLUMN c int NOT NULL")
    monkeypatch.setattr(lint, "version_locations", lambda: [str(other), str(mine)])
    assert any("NOT NULL column" in m for m in lint.lint_file(f))
    monkeypatch.setattr(lint, "version_locations", lambda: [str(mine)])
    assert lint.lint_file(f) == []


@pytest.mark.parametrize("sql,flagged", [
    ("REVOKE CREATE ON SCHEMA a FROM pipeshub_vetted_a", False),
    ("REVOKE pipeshub_vetted_a, pipeshub_vetted_b FROM pipeshub_owner", False),
    ("REVOKE SELECT ON a.t FROM pipeshub_vetted_a", True),
    ("REVOKE EXECUTE ON FUNCTION a.f(int) FROM pipeshub_vetted_a", True),
    ("DO $$ BEGIN IF true THEN PERFORM kernel.apply_rls('a.t', 'org'); END IF; END $$", True),
    ("DO $$ BEGIN CREATE TABLE a.t (id int); END $$; ALTER TABLE a.t ADD COLUMN c int NOT NULL", False),
])
def test_lint_revoke_exemption_and_do_bodies(tmp_path, sql, flagged):
    f = tmp_path / "0002_x.sql"
    f.write_text(sql)
    assert any("can break older code" in m for m in lint.lint_file(f)) is flagged


def test_lint_waiver_needs_a_reason(tmp_path):
    f = tmp_path / "0002_x.sql"
    f.write_text("ALTER TABLE a.b ADD COLUMN c int NOT NULL")
    for body in ('older_code_compatible = ""', 'older_code_compatible = "ok"', "older_code_compatible = True"):
        (tmp_path / "0002_x.py").write_text(body + "\n")
        assert any("can break older code" in m for m in lint.lint_file(f)), body


def test_lint_unique_index_in_a_python_revision(tmp_path):
    f = tmp_path / "0002_x.sql"
    f.write_text("CREATE TABLE a.n (id int)")
    py = tmp_path / "0002_x.py"
    py.write_text('def upgrade():\n    ensure_index(c, "a.i", "CREATE UNIQUE INDEX i ON a.b (c)")\n'
                  '    ensure_partitioned_index(c, "a.p", "pi", "c", unique=True)\n')
    assert len([m for m in lint.lint_file(f) if "unique index" in m]) == 2
    py.write_text('def upgrade():\n    ensure_index(c, "a.i", "CREATE UNIQUE INDEX i ON a.n (id)")\n    ensure_index(c, "a.j", "CREATE INDEX j ON a.b (c)")\n')
    assert lint.lint_file(f) == []


def test_lint_vetted_function_file_follows_the_revision_that_runs_it(tmp_path):
    (tmp_path / "vetted").mkdir()
    v = tmp_path / "vetted" / "fn.sql"
    v.write_text("CREATE OR REPLACE FUNCTION a.fn() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$; ALTER FUNCTION a.fn() OWNER TO r")
    (tmp_path / "0003_x.py").write_text('run_vetted(__file__, "fn")\n')
    assert any("REPLACE FUNCTION" in m for m in lint.lint_file(v))
    (tmp_path / "0003_x.py").write_text('older_code_compatible = "same signature and result as before"\nrun_vetted(__file__, "fn")\n')
    assert lint.lint_file(v) == []


def test_boot_assert():
    req = {"identity": {"requires": 7, "code": 7}}
    assert schema_problems({"identity": (7, 0)}, req) == []
    assert schema_problems({"identity": (6, 0)}, req) == ["schema identity epoch 6 < required 7"]
    assert schema_problems({"identity": (8, 8)}, req) == ["schema identity min_code_epoch 8 > code 7"]
    assert schema_problems({}, req) == ["schema identity epoch 0 < required 7"]
    assert schema_problems({"other": (1, 1)}, {}) == []                  # unlisted branches are ignored


def test_extra_locations_read_from_the_edition_file(tmp_path, monkeypatch):
    mod = tmp_path / "extra" / "usage"
    mod.mkdir(parents=True)
    ed = tmp_path / "edition.py"
    ed.write_text(f'EXTRA_MIGRATION_LOCATIONS = ["{mod}", "missing/dir"]\n')
    monkeypatch.setenv("PIPESHUB_EDITION_FILE", str(ed))
    assert locations.extra_locations() == [str(mod)]
    assert locations.version_locations()[-1] == str(mod)


def test_shipped_edition_seam_is_empty():
    assert locations.extra_locations() == []


def test_url_is_required_and_normalised(monkeypatch):
    monkeypatch.delenv("PG_MIGRATE_URL", raising=False)
    with pytest.raises(migrate.RunnerError) as e:
        migrate.migrate_url()
    assert e.value.code == migrate.EXIT_CONFIG
    monkeypatch.setenv("PG_MIGRATE_URL", "postgresql://u:p%40ss@h/db")
    assert migrate.migrate_url() == "postgresql+psycopg://u:p%40ss@h/db"


def test_pooler_is_refused(monkeypatch):
    pids = iter([11, 12, 13])

    class Conn:
        def execute(self, *a, **k):
            return self
        def fetchone(self):
            return (next(pids),)
        def close(self):
            pass

    monkeypatch.setattr(migrate.psycopg, "connect", lambda *a, **k: Conn())
    with pytest.raises(migrate.RunnerError, match="pooler") as e, migrate.locked("postgresql+psycopg://u@h/d"):
        pass
    assert e.value.code == migrate.EXIT_CONFIG


def test_schema_requirements_from_the_tree():
    from db import schema_requirements
    assert schema_requirements.requirements(["kernel"]) == {"kernel": {"requires": 14, "code": 14}}
    with pytest.raises(SystemExit):
        schema_requirements.requirements(["nope"])


def test_lint_ignores_words_inside_string_literals_and_column_names(tmp_path):
    f = tmp_path / "v.sql"
    f.write_text("CREATE VIEW kernel.v AS SELECT 'SECURITY DEFINER' AS a, rolbypassrls AS b FROM pg_roles")
    assert lint.lint_file(f) == []
    f.write_text("CREATE ROLE r BYPASSRLS")
    assert lint.lint_file(f) == ["v.sql: BYPASSRLS"]


def test_lint_rejects_notify_and_listen(tmp_path):
    f = tmp_path / "n.sql"
    for stmt in ("NOTIFY chan, 'x'", "LISTEN chan", "SELECT pg_notify('chan', org_id) FROM kt.t"):
        f.write_text(stmt)
        assert any("NOTIFY / LISTEN" in m for m in lint.lint_file(f)), stmt
    f.write_text("SELECT 1 -- notify later")
    assert lint.lint_file(f) == []


@pytest.mark.parametrize("existing, said, not_said", [(True, "Nothing was applied", "some may be applied"),
                                                       (False, "some may be applied", "Nothing was applied")])
def test_a_full_lock_table_names_the_setting_to_raise(monkeypatch, capsys, existing, said, not_said):
    class Boom(Exception):
        sqlstate = "53200"

    class Wrapped(Exception):
        orig = Boom("out of shared memory\nHINT: You might need to increase max_locks_per_transaction.")

    def fail(_argv):
        raise Wrapped("(psycopg.errors.OutOfMemory) out of shared memory")

    monkeypatch.setattr(migrate, "run", fail)
    monkeypatch.setitem(migrate._path, "existing_install", existing)
    assert migrate.main(["upgrade"]) == migrate.EXIT_FAILED
    out = capsys.readouterr().out
    assert "max_locks_per_transaction" in out and said in out and not_said not in out


def test_every_extension_the_capabilities_file_names_may_be_installed():
    """The catalog check refuses an installed extension that is not in the allowlist, so a named one must be in it."""
    import re

    import yaml

    named = set(yaml.safe_load((Path(migrate.__file__).parent / "capabilities.yaml").read_text())["extensions"])
    allowed = re.search(r"ARRAY\[('plpgsql'.*?)\]", (Path(migrate.__file__).parent / "versions/kernel/0003_rls.sql").read_text(), re.S)
    assert named <= set(re.findall(r"'([a-z_0-9]+)'", allowed[1]))


def test_autocommit_block_is_refused_in_a_verified_upgrade():
    from db.lib.ddl import refuse_autocommit

    with pytest.raises(RuntimeError, match="autocommit_block"):
        refuse_autocommit()


@pytest.mark.parametrize("raw", ["abc", "", "0", "-5", "0.0001", "nan"])
def test_a_bad_lock_timeout_is_a_configuration_error_before_any_connection(monkeypatch, raw):
    monkeypatch.setenv("PG_MIGRATE_LOCK_TIMEOUT_S", raw)
    with pytest.raises(migrate.RunnerError) as excinfo:
        with migrate.locked("postgresql://nobody@127.0.0.1:1/none"):
            pass
    assert excinfo.value.code == migrate.EXIT_CONFIG
    assert "PG_MIGRATE_LOCK_TIMEOUT_S" in str(excinfo.value)


def test_no_shipped_table_uses_the_lineage_class():
    """The `lineage` row policy is a hook for deployments that add a parent hierarchy; no table shipped here may register it."""
    import re
    versions = Path(__file__).resolve().parents[1] / "versions"
    use = re.compile(r"""apply_rls\s*\([^;]*?['"]lineage['"]""", re.S)
    found = [str(p.relative_to(versions)) for p in sorted(versions.rglob("*"))
             if p.suffix in (".sql", ".py") and use.search(p.read_text(encoding="utf-8"))]
    assert found == [], f"these revisions register the lineage class: {found}"
