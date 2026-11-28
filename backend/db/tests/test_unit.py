"""Unit tests: no database needed."""
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from db import epoch_lint, lint, locations, migrate, released_heads
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


def test_expected_heads_read_from_the_files(branch):
    _rev(branch, "identity", 4, 3)
    assert epoch_lint.expected_heads([branch]) == ({"identity": (4, 3)}, {"identity_0004"})


def test_epoch_lint_reads_annotated_revision_attributes(branch):
    (branch / "0004_annotated.py").write_text('revision: str = "identity_0004"\ndown_revision: str = "identity_0002"\nmin_code_epoch: int = 4\n')
    assert "expected 'identity_0003'" in "\n".join(epoch_lint.lint_branch(branch))
    (branch / "0004_annotated.py").write_text('revision: str = "identity_0004"\ndown_revision: str = "identity_0003"\nmin_code_epoch: int = 4\n')
    assert epoch_lint.expected_heads([branch]) == ({"identity": (4, 4)}, {"identity_0004"})


def test_expected_heads_refuse_a_second_head(branch):
    _rev(branch, "identity", 4, 3, name="this_pr")
    (branch / "0005_other_pr.py").write_text('revision = "identity_0005"\ndown_revision = "identity_0003"\n')
    with pytest.raises(ValueError, match="2 heads"):
        epoch_lint.expected_heads([branch])


def test_expected_heads_refuse_a_head_whose_id_is_not_its_files_epoch(branch):
    (branch / "0004_wrong.py").write_text('revision = "identity_0005"\ndown_revision = "identity_0003"\n')
    with pytest.raises(ValueError, match="is not epoch 4"):
        epoch_lint.expected_heads([branch])


def test_a_computed_depends_on_or_contract_does_not_break_the_lint(branch):
    _rev(branch, "identity", 4, 3, extra='depends_on = PARENTS\nmin_code_epoch = int("4")')
    assert epoch_lint.lint_branch(branch) == []
    with pytest.raises(ValueError, match="depends_on, min_code_epoch must be a literal"):
        epoch_lint.expected_heads([branch])


def test_no_released_head_is_gone():
    """A branch never goes below the head the last release shipped (``released_heads.json``, edited only by the release
    process): a shipped revision deleted or renumbered would leave installed databases ahead of the files."""
    released = json.loads((locations.DB_DIR / "released_heads.json").read_text())["epochs"]
    heads, _ = epoch_lint.expected_heads(locations.version_locations())
    below = {b: (heads.get(b, (0, 0))[0], n) for b, n in released.items() if heads.get(b, (0, 0))[0] < n}
    assert not below, f"branch heads below the released ones (now, released): {below}"


def test_released_heads_records_the_tree_heads(tmp_path):
    """The release step's writer, and the check the release workflow runs on the tagged tree."""
    out = tmp_path / "released_heads.json"
    released_heads.write("v9.9.9", out)
    heads, _ = epoch_lint.expected_heads(locations.version_locations())
    assert json.loads(out.read_text()) == {"release": "v9.9.9", "epochs": {b: e for b, (e, _) in heads.items()}}
    assert released_heads.check(out) == ""
    # a pre-release or hotfix tag names another release with the same heads
    out.write_text(json.dumps({**json.loads(out.read_text()), "release": "v9.9.10-rc.1"}))
    assert released_heads.check(out) == ""
    stale = json.loads(out.read_text())
    stale["epochs"]["kernel"] -= 1
    out.write_text(json.dumps(stale))
    assert released_heads.check(out) != ""


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


@pytest.mark.parametrize("annotated", [False, True])                  # ONLINE_DROPS: list[str] = [...] is the same declaration
@pytest.mark.parametrize("mark,flagged", [
    ("", True),
    ("min_code_epoch = 0\n", True),                                    # no mark, as for every other rule
    ('older_code_compatible = "unused"\n', True),                      # a reason of fewer than four words is none
    ('older_code_compatible = "no plan of any release reads these"\n', False),
    ('older_code_compatible: str = "no plan of any release reads these"\n', False),
    ("min_code_epoch = 1\n", False),
    ("min_code_epoch: int = 1\n", False),
])
def test_lint_a_revision_that_retires_indexes_says_whether_older_code_needs_them(tmp_path, monkeypatch, mark, flagged, annotated):
    """ONLINE_DROPS is dropped by db.migrate, not by SQL the lint reads, so the revision itself must say min_code_epoch or why older code still works."""
    monkeypatch.setattr(lint, "version_locations", lambda: [str(tmp_path)])
    (tmp_path / "0002_retire.py").write_text(mark + f'ONLINE_DROPS{": list[str]" if annotated else ""} = ["a.ix"]\n')
    assert bool(lint.retiring_violations()) is flagged
    if flagged:
        assert "set min_code_epoch (older code plans with them) or older_code_compatible" in lint.retiring_violations()[0]


@pytest.mark.parametrize("raw,want", [("", None), (" ", None), ("512", "512MB"), ("0", 2), ("-1", 2), ("1.5", 2), ("1GB", 2), ("9999999", 2), ("\u00b2", 2)])
def test_the_index_build_memory_is_a_whole_number_of_megabytes(monkeypatch, raw, want):
    monkeypatch.setenv("PG_INDEX_BUILD_MEMORY_MB", raw)
    if want == 2:
        with pytest.raises(migrate.RunnerError) as e:
            migrate.build_memory()
        assert e.value.code == migrate.EXIT_CONFIG and "PG_INDEX_BUILD_MEMORY_MB" in str(e.value)
    else:
        assert migrate.build_memory() == want


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


def test_bootstrap_creates_the_vetted_owner_of_each_schema_an_extra_location_defines_functions_in(tmp_path, monkeypatch):
    """A migration cannot create a role, so the bootstrap reads the extra locations' manifests for the owners they need."""
    from db import bootstrap_roles
    mod = tmp_path / "extra" / "demo"
    (mod / "vetted").mkdir(parents=True)
    fn = {"class": "read", "scope": "org", "args": [], "returns": "boolean", "no_org": "false", "body": "RETURN true;", "test": {"checks": ["privileges"]}}
    (mod / "vetted" / "manifest.yaml").write_text(yaml.safe_dump({"functions": [{"name": "demo_a.one", **fn}, {"name": "demo_b.two", **fn},
                                                                               {"name": "demo_a.three", **fn}]}))
    (tmp_path / "extra" / "plain").mkdir()                       # a location without functions needs no owner
    ed = tmp_path / "edition.py"
    ed.write_text(f'EXTRA_MIGRATION_LOCATIONS = ["{mod}", "{tmp_path / "extra" / "plain"}"]\n')
    monkeypatch.setenv("PIPESHUB_EDITION_FILE", str(ed))
    assert bootstrap_roles.location_vetted_schemas() == ["demo_a", "demo_b"]
    ed.write_text("EXTRA_MIGRATION_LOCATIONS = []\n")
    assert bootstrap_roles.location_vetted_schemas() == []


def test_bootstrap_accepts_every_schema_name_a_vetted_manifest_accepts(tmp_path, monkeypatch):
    """A manifest may name schema _demo (db.lib.vetted's identifier rule); the bootstrap must create its owner, not refuse the name.
    Only a name whose owner would not fit PostgreSQL's 63-byte role names is refused, before anything is created."""
    from db import bootstrap_roles
    from db.lib import vetted

    class Reached(Exception):
        pass

    class Conn:                                                   # the name check runs before the first statement
        def execute(self, *_a, **_k):
            raise Reached

    mod = tmp_path / "extra" / "demo"
    (mod / "vetted").mkdir(parents=True)
    ed = tmp_path / "edition.py"
    ed.write_text(f'EXTRA_MIGRATION_LOCATIONS = ["{mod}"]\n')
    monkeypatch.setenv("PIPESHUB_EDITION_FILE", str(ed))
    fn = {"class": "read", "scope": "org", "args": [], "returns": "boolean", "no_org": "false", "body": "RETURN true;", "test": {"checks": ["privileges"]}}
    for schema in ("_demo", "a", "a_1", "_", "x" * 47):
        (mod / "vetted" / "manifest.yaml").write_text(yaml.safe_dump({"functions": [{"name": f"{schema}.one", **fn}]}))
        assert vetted.load(mod / "vetted" / "manifest.yaml")[0].schema == schema        # the manifest accepts it
        with pytest.raises(Reached):
            bootstrap_roles.bootstrap(Conn(), [])
        assert len(f"pipeshub_vetted_{schema}") <= 63
    (mod / "vetted" / "manifest.yaml").write_text(yaml.safe_dump({"functions": [{"name": f"{'x' * 48}.one", **fn}]}))
    with pytest.raises(ValueError, match="not a schema name"):
        bootstrap_roles.bootstrap(Conn(), [])


def test_shipped_edition_seam_names_only_module_directories_that_exist():
    """An edition's extra migration modules must all be there: a missing one would silently migrate less."""
    spec = importlib.util.spec_from_file_location("_shipped_edition", locations.BACKEND_DIR / "python" / "app" / "edition.py")
    edition = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(edition)
    declared = getattr(edition, "EXTRA_MIGRATION_LOCATIONS", [])
    assert len(locations.extra_locations()) == len(declared)


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
    assert schema_requirements.requirements(["kernel"]) == {"kernel": {"requires": 24, "code": 24}}
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


def test_the_catalog_check_runs_with_jit_off():
    # with JIT built in (the Debian/pgdg packages) the big catalog views compile for 10-30 s and save nothing
    import inspect
    src = inspect.getsource(migrate._report)
    assert src.index('SET jit = off') < src.index('FROM kernel.rls_violations') < src.index('RESET jit')


def _files_on_disk(tests_dir: Path) -> list[str]:
    """Every test file under the tests directory, at any depth, as the workflow names it (tests/sub/test_x.py)."""
    return sorted(f"tests/{p.relative_to(tests_dir).as_posix()}" for p in tests_dir.rglob("test_*.py"))


def test_the_shard_guard_sees_a_test_file_in_a_subdirectory(tmp_path):
    (tmp_path / "sub" / "deeper").mkdir(parents=True)
    for rel in ("test_a.py", "sub/test_b.py", "sub/deeper/test_c.py", "sub/helper.py"):
        (tmp_path / rel).write_text("")
    assert _files_on_disk(tmp_path) == ["tests/sub/deeper/test_c.py", "tests/sub/test_b.py", "tests/test_a.py"]


def test_every_test_file_is_in_exactly_one_shard_of_the_db_migrations_workflow():
    """CI runs the tests in shards by file; a test file that no shard names would not run, and one named twice would run twice."""
    import yaml

    workflow = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "db-migrations.yml"
    if not workflow.is_file():
        pytest.skip("no workflow file in this checkout (the backend is packaged alone)")
    matrix = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]["migrations"]["strategy"]["matrix"]
    named = [f for shard in matrix["include"] for f in shard["files"].split()]
    assert sorted(named) == _files_on_disk(Path(__file__).parent), named
    assert sorted(s["shard"] for s in matrix["include"]) == sorted(matrix["shard"])


@pytest.mark.parametrize("typ", ["smallint[]", "boolean[]", "text[]"])
def test_item_enum_values_are_quoted_literals_for_every_array_type(typ):
    # an unquoted 0 on a boolean[] is "boolean = integer", which PostgreSQL has no operator for
    from db.lib import vetted

    fn = vetted.parse({"name": "kt.w_x", "class": "read", "scope": "org", "returns": "int", "no_org": "0", "body": "BEGIN RETURN 1; END;",
                       "args": [{"name": "p_a", "type": typ, "max_items": 10, "item_enum": [0, 1]}]})
    assert "NOT IN ('0', '1')" in vetted.render(fn)


@pytest.mark.parametrize("key", ["enum", "item_enum"])
def test_an_empty_enum_is_refused_rather_than_dropping_its_check(key):
    from db.lib import vetted

    arg = {"name": "p_a", "type": "text[]", "max_items": 10, key: []}
    with pytest.raises(vetted.ManifestError, match="non-empty list"):
        vetted.parse({"name": "kt.w_x", "class": "read", "scope": "org", "returns": "int", "no_org": "0", "body": "BEGIN RETURN 1; END;", "args": [arg]})


def test_the_compose_image_role_script_has_the_layout_of_bootstrap_roles():
    """deployment/docker-compose/postgres/pg-roles.sh builds the roles for the Compose install by itself; it must not drift from
    db.bootstrap_roles (a missing vetted owner makes the migrations fail with 'the vetted owner ... is missing')."""
    import re

    from db import bootstrap_roles as b

    text = (Path(__file__).resolve().parents[3] / "deployment/docker-compose/postgres/pg-roles.sh").read_text()
    sql = re.sub(r"--[^\n']*(?=\n)", "", text)                     # comments
    flat = lambda s: re.sub(r"\s+", " ", s).strip()
    vetted = {f"pipeshub_vetted_{s}" for s in b.SHIPPED_VETTED}
    quoted = set(re.findall(r"'(pipeshub_\w+)'", sql))
    assert quoted == {r for r, _ in b.ROLES} | vetted | {b.MONITOR}
    # the opt-in monitoring login: the same one membership both ways (kernel_0019 accepts exactly this one)
    assert "GRANT pg_monitor TO pipeshub_monitor WITH INHERIT TRUE, SET FALSE;" in flat(sql)
    assert 'GRANT pg_monitor TO {} WITH INHERIT TRUE, SET FALSE' in Path(b.__file__).read_text()
    logins = set(re.findall(r"\('(pipeshub_\w+)', :'\w+'\)", sql)) | {"pipeshub_ro"}
    assert logins == {r for r, login in b.ROLES if login}
    assert set(re.findall(r":(PG_\w+_PASSWORD)\b", text)) | set(re.findall(r"\w+:(PG_\w+_PASSWORD)", text)) == set(b.PASSWORD_VARS.values())
    grants, handed_out = set(), set()
    for roles, members, opts in re.findall(r"GRANT\s+(pipeshub_\w+(?:,\s*pipeshub_\w+)*)\s+TO\s+([\w,\s]+?)\s*(WITH[^;]*)?;", sql):
        for role in re.split(r",\s*", roles):
            for member in re.split(r",\s*", members.strip()):
                if role in vetted:
                    handed_out.add(role)
                    assert (member, "ADMIN TRUE", "INHERIT FALSE", "SET FALSE") == ("pipeshub_owner", *re.findall(r"ADMIN TRUE|INHERIT \w+|SET \w+", opts))
                else:
                    flags = dict(re.findall(r"(INHERIT|SET) (\w+)", opts))
                    grants.add((role, member, flags.get("INHERIT", "TRUE") == "TRUE", flags.get("SET", "TRUE") == "TRUE"))
    assert grants == set(b.GRANTS)
    assert handed_out == vetted
    assert flat(b.LARGE_OBJECTS_SQL) in flat(sql)
    assert {"pg_trgm", "vector"} <= set(re.findall(r"CREATE EXTENSION IF NOT EXISTS (\w+) WITH SCHEMA ext", sql))   # the extensions CI installs


_BUILD = 'def upgrade():\n    ensure_partitioned_index(op.get_bind(), "graph.nodes", "nodes_x", "org_id, (doc->>\'x\')", blocking=True)\n'


@pytest.mark.parametrize("name,body,sql,flagged", [
    ("blocking on an existing table", _BUILD, None, True),
    ("its name is one of ONLINE_INDEXES", 'ONLINE_INDEXES = [{"table": "graph.nodes", "name": "nodes_x", "cols": "org_id"}]\n' + _BUILD, None, False),
    ("another name is", 'ONLINE_INDEXES = [{"table": "graph.nodes", "name": "nodes_y", "cols": "org_id"}]\n' + _BUILD, None, True),
    ("the name on another table is", 'ONLINE_INDEXES = [{"table": "kg.records", "name": "nodes_x", "cols": "org_id"}]\n' + _BUILD, None, True),
    ("built from ONLINE_INDEXES", 'ONLINE_INDEXES = []\n\n\ndef upgrade():\n    for ix in ONLINE_INDEXES:\n'
                                  '        ensure_partitioned_index(op.get_bind(), **ix, blocking=True)\n', None, False),
    ("built from another list", 'INDEXES = []\n\n\ndef upgrade():\n    for ix in INDEXES:\n'
                                '        ensure_partitioned_index(op.get_bind(), **ix, blocking=True)\n', None, True),
    ("the loop name rebound by a nested loop", 'ONLINE_INDEXES = []\nOTHER = []\n\n\ndef upgrade():\n    for ix in ONLINE_INDEXES:\n'
                                               '        for ix in OTHER:\n            ensure_partitioned_index(op.get_bind(), **ix, blocking=True)\n',
     None, True),
    ("the loop name reused for another list", 'ONLINE_INDEXES = []\nINDEXES = []\n\n\ndef upgrade():\n    for ix in ONLINE_INDEXES:\n'
                                              '        ensure_partitioned_index(op.get_bind(), **ix, blocking=True)\n    for ix in INDEXES:\n'
                                              '        ensure_partitioned_index(op.get_bind(), **ix, blocking=True)\n', None, True),
    ("names built at run time", 'def upgrade():\n    for t, n in X:\n        ensure_partitioned_index(op.get_bind(), t, n, "c", blocking=True)\n', None, True),
    ("its table is created by the revision's .sql", _BUILD, "CREATE TABLE graph.nodes (org_id text)", False),
    ("IF NOT EXISTS of a table an earlier revision creates", _BUILD, "CREATE TABLE IF NOT EXISTS graph.nodes (org_id text)", True),
    ("its table only in a comment of the .sql", _BUILD, "-- CREATE TABLE graph.nodes (k int)\n/* CREATE TABLE graph.nodes (k int) */\nSELECT 1", True),
    ("its table only in a string of the .sql", _BUILD, "SELECT 'CREATE TABLE graph.nodes (k int)'", True),
    ("a reasoned waiver", 'blocking_index_ok = "the index keys a column this revision adds"\n' + _BUILD, None, False),
    ("a waiver without a reason", 'blocking_index_ok = "ok"\n' + _BUILD, None, True),
    ("online, not blocking", _BUILD.replace("blocking=True", "blocking=False"), None, False),
    ("only in downgrade()", _BUILD.replace("def upgrade", "def downgrade"), None, False),
])
def test_lint_refuses_an_index_built_inside_the_upgrade_transaction(tmp_path, name, body, sql, flagged):
    py = tmp_path / "0003_x.py"
    py.write_text('revision = "graph_0003"\n' + body)
    if sql:
        py.with_suffix(".sql").write_text(sql)
    msgs = lint.lint_revision(py, {"graph": 2})
    assert any("inside the upgrade's transaction" in m for m in msgs) is flagged, (name, msgs)


@pytest.mark.parametrize("later,flagged", [
    ('ONLINE_INDEXES = [{"table": "graph.nodes", "name": "nodes_x", "cols": "org_id"}]\n', False),   # pending whenever 0003 is: built ahead
    ('ONLINE_INDEXES = [{"table": "graph.nodes", "name": "nodes_y", "cols": "org_id"}]\n', True),
])
def test_lint_takes_an_index_a_later_revision_lists_online_as_built_ahead(tmp_path, later, flagged):
    py = tmp_path / "0003_x.py"
    py.write_text('revision = "graph_0003"\n' + _BUILD)
    (tmp_path / "0004_y.py").write_text('revision = "graph_0004"\n' + later)
    (tmp_path / "0002_w.py").write_text('revision = "graph_0002"\nONLINE_INDEXES = [{"table": "graph.nodes", "name": "nodes_x", "cols": "c"}]\n')
    assert any("inside the upgrade's transaction" in m for m in lint.lint_revision(py, {"graph": 2})) is flagged


@pytest.mark.parametrize("name,body,flagged", [
    ("an f-string", 'def upgrade():\n    op.execute(f"DROP INDEX IF EXISTS graph.{n}")\n', True),
    ("concatenated", 'def upgrade():\n    op.execute("DROP INDEX " + n)\n', True),
    ("its kind built at run time", 'def upgrade():\n    op.execute(f"DROP {kind} graph.x")\n', True),
    ("through a name", 'def upgrade():\n    sql = "drop index %s" % n\n    op.execute(sql)\n', True),
    ("through a name assigned again after it ran", 'def upgrade():\n    sql = "DROP INDEX graph.x"\n    op.execute(sql)\n'
                                                    '    sql = "SELECT 1"\n    op.execute(sql)\n', True),
    ("its verb built at run time", 'def upgrade():\n    verb = "DROP"\n    op.execute(f"{verb} INDEX graph.x")\n', True),
    ("one of a name's values built at run time", 'def upgrade():\n    sql = "SELECT 1"\n    sql = f"{verb} INDEX graph.x"\n    op.execute(sql)\n', True),
    ("a module constant", 'UPGRADE = "DROP INDEX graph.x"\n\n\ndef upgrade():\n    op.execute(UPGRADE)\n', True),
    ("a module constant that is no drop", 'UPGRADE = "CREATE OR REPLACE VIEW graph.v AS SELECT 1"\n\n\ndef upgrade():\n    op.execute(UPGRADE)\n', False),
    ("its verb a parameter", 'def upgrade():\n    run(op.get_bind(), "x")\n\n\ndef run(c, sql):\n    c.exec_driver_sql(sql)\n', True),
    ("a CREATE INDEX built at run time", 'def upgrade():\n    op.execute(f"CREATE INDEX IF NOT EXISTS {n} ON graph.x (c)")\n', True),
    ("its verb built at run time in a DO body", 'def upgrade():\n    op.execute(f"DO $$ BEGIN {verb} INDEX graph.x; END $$;")\n', True),
    ("a prefix reassigned after it ran", 'def upgrade():\n    prefix = "DROP "\n    op.execute(prefix + "INDEX graph.x")\n    prefix = "SELECT "\n', True),
    ("a module name shadowed by a parameter", 'sql = "SELECT 1"\n\n\ndef upgrade():\n    run(op.get_bind(), "DROP INDEX graph.x")\n\n\n'
                                              'def run(c, sql):\n    c.exec_driver_sql(sql)\n', True),
    (".format", 'def upgrade():\n    op.get_bind().exec_driver_sql("DROP INDEX {}".format(n))\n', True),
    ("a literal in a revision without .sql", 'def upgrade():\n    op.execute("DROP INDEX graph.x")\n', True),
    ("a waiver does not clear it", 'older_code_compatible = "older code never reads that index at all"\n'
                                   'def upgrade():\n    op.execute(f"DROP INDEX graph.{n}")\n', True),
    ("a contract does", 'min_code_epoch = 3\ndef upgrade():\n    op.execute(f"DROP INDEX graph.{n}")\n', False),
    ("another statement", 'def upgrade():\n    op.execute("CREATE INDEX x ON graph.x (c)")\n', False),
    ("a comment inside the drop", 'def upgrade():\n    op.execute("DROP /* retired lookup */ INDEX graph.x")\n', True),
    ("a comment between the words", 'def upgrade():\n    op.execute("DROP/**/INDEX graph.x")\n', True),
    ("a line comment in a DO body", 'def upgrade():\n    op.execute("DO $$ BEGIN DROP -- retired\\n INDEX graph.x; END $$")\n', True),
    ("DROP INDEX only in a string", 'def upgrade():\n    op.execute("SELECT \'DROP INDEX graph.x\'")\n', False),
    ("EXECUTE of a literal in a DO body", 'def upgrade():\n    op.execute("DO $$ BEGIN EXECUTE \'DROP INDEX graph.x\'; END $$")\n', True),
    ("EXECUTE format in a DO body", 'def upgrade():\n    op.execute("DO $$ BEGIN EXECUTE format(\'DROP INDEX %I\', \'x\'); END $$")\n', True),
    ("any EXECUTE in a DO body", 'def upgrade():\n    op.execute("DO $$ BEGIN EXECUTE \'SELECT 1\'; END $$")\n', True),
    ("a DO nested in a DO body", 'def upgrade():\n    op.execute("DO $o$ BEGIN DO $$ BEGIN DROP INDEX graph.x; END $$; END $o$")\n', True),
    ("a function body created in a DO body", 'def upgrade():\n    op.execute("DO $o$ BEGIN CREATE FUNCTION f() RETURNS void LANGUAGE sql AS $$ SELECT 1 $$; END $o$")\n', True),
    ("a DO with a quoted body", 'def upgrade():\n    op.execute("DO \'BEGIN DROP INDEX graph.x; END\'")\n', True),
    ("a procedure body the upgrade calls", 'def upgrade():\n    op.execute("CREATE PROCEDURE pg_temp.p() LANGUAGE plpgsql AS $$ BEGIN DROP INDEX graph.x; END $$; '
                                           'CALL pg_temp.p();")\n', True),
    ("a function body that runs EXECUTE", 'def upgrade():\n    op.execute("CREATE OR REPLACE FUNCTION pg_temp.f() RETURNS void LANGUAGE plpgsql AS $f$ BEGIN '
                                          'EXECUTE \'SELECT 1\'; END $f$; SELECT pg_temp.f()")\n', True),
    ("a function body in a quoted string", 'def upgrade():\n    op.execute("CREATE FUNCTION pg_temp.f() RETURNS int LANGUAGE sql AS \'SELECT 1\'; SELECT pg_temp.f()")\n', True),
    ("a dollar-quoted default before the body", 'def upgrade():\n    op.execute("CREATE FUNCTION pg_temp.f(a text DEFAULT $$a$$) RETURNS void '
                                                'LANGUAGE plpgsql AS $$ BEGIN DROP INDEX graph.x; END $$; SELECT pg_temp.f()")\n', True),
    ("a function body that runs EXECUTE, not run", 'def upgrade():\n    op.execute("CREATE OR REPLACE FUNCTION pg_temp.f() RETURNS void LANGUAGE plpgsql '
                                                   'AS $f$ BEGIN EXECUTE \'SELECT 1\'; END $f$")\n', False),
    ("a function body in BEGIN ATOMIC", 'def upgrade():\n    op.execute("CREATE FUNCTION pg_temp.f() RETURNS int LANGUAGE sql BEGIN ATOMIC SELECT 1; END; SELECT pg_temp.f()")\n', True),
    ("a function body that drops nothing", 'def upgrade():\n    op.execute("CREATE OR REPLACE FUNCTION kg.f(p jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$ '
                                           'SELECT p || \'{}\'::jsonb $$")\n', False),
    ("a DO with a quoted body inside a DO body", 'def upgrade():\n    op.execute("DO $$ BEGIN DO \'BEGIN DROP INDEX graph.x; END\'; END $$")\n', True),
    ("a procedure with a quoted body created and called in a DO body",
     'def upgrade():\n    op.execute("DO $$ BEGIN CREATE PROCEDURE p() LANGUAGE plpgsql AS \'BEGIN DROP INDEX graph.x; END\'; CALL p(); END $$")\n', True),
    ("a trigger created in a DO body", 'def upgrade():\n    op.execute("DO $$ BEGIN CREATE TRIGGER t AFTER INSERT ON graph.y EXECUTE FUNCTION graph.f(); END $$")\n', True),
    ("DROP INDEX as data in a DO body", 'def upgrade():\n    op.execute("DO $$ BEGIN RAISE NOTICE \'DROP INDEX x\'; INSERT INTO graph.t VALUES (\'DO\'); END $$")\n', False),
    ("a DO body that drops nothing", 'def upgrade():\n    op.execute("DO $$ BEGIN PERFORM 1; END $$")\n', False),
    ("DROP INDEX only in a dollar-quoted string", 'def upgrade():\n    op.execute("SELECT $$DROP INDEX graph.x$$")\n', False),
    ("DROP INDEX only in a tagged dollar-quoted string", 'def upgrade():\n    op.execute("SELECT $q$DROP INDEX graph.x$q$")\n', False),
    ("DROP INDEX only in a comment", 'def upgrade():\n    op.execute("SELECT 1 -- DROP INDEX graph.x")\n', False),
    ("in downgrade()", 'def downgrade():\n    op.execute(f"DROP INDEX graph.{n}")\n\n\ndef upgrade():\n    pass\n', False),
])
def test_lint_refuses_a_drop_index_built_at_run_time(tmp_path, name, body, flagged):
    py = tmp_path / "0003_x.py"
    py.write_text('revision = "graph_0003"\n' + body)
    msgs = lint.lint_revision(py, {"graph": 2})
    assert any("may DROP INDEX" in m for m in msgs) is flagged, (name, msgs)


def test_lint_leaves_released_revisions_alone(tmp_path):
    py = tmp_path / "0003_x.py"
    py.write_text('revision = "graph_0003"\n' + _BUILD + '    op.execute(f"DROP INDEX graph.{n}")\n')
    assert len(lint.lint_revision(py, {"graph": 2})) == 2
    assert lint.lint_revision(py, {"graph": 3}) == []
    assert lint.main([str(py)]) == 0                                # graph_0003 is in released_heads.json
    py.write_text(py.read_text().replace("graph_0003", "graph_9999"))
    assert lint.main([str(py)]) == 1


def test_lint_of_a_revision_py_reads_the_constant_sql_of_its_upgrade_when_it_has_a_sql_file(tmp_path):
    py = tmp_path / "0003_x.py"
    py.write_text('revision = "graph_9999"\n\n\ndef upgrade():\n    op.execute("DROP INDEX graph.x")\n')
    py.with_suffix(".sql").write_text("SELECT 1;\n")
    assert lint.main([str(py)]) == 1
    py.write_text('revision = "graph_9999"\n\n\ndef upgrade():\n    op.execute("DROP/**/INDEX graph.x")\n')    # comments are whitespace
    assert lint.main([str(py)]) == 1


@pytest.mark.parametrize("call,flagged", [
    ('op.execute(build())', True),                              # a call's result
    ('op.execute(sa.text(f"SELECT {n}"))', True),
    ('op.execute(self.sql)', True),                             # an attribute
    ('op.execute("SELECT %s" % n)', True),
    ('op.execute("SELECT {}".format(n))', True),
    ('op.execute("SELECT " + n)', True),
    ('sql = "SELECT 1"\n    op.execute(sql)', True),          # a local name, constant or not
    ('op.execute(LOOPED)', True),                               # a module name bound twice
    ('op.execute(f"SELECT {CONST!r}")', True),                 # a conversion
    ('op.execute("SELECT 1")', False),
    ('op.execute("SELECT " + "1")', False),
    ('op.execute(CONST)', False),                               # a module constant nothing else binds
    ('op.execute(f"SELECT 1 FROM {CONST}")', False),
    ('op.get_bind().exec_driver_sql(sa.text(CONST))', False),
    ('op.execute(sql="DROP INDEX graph.x")', True),           # SQL passed by keyword
    ('op.execute(sqltext="DROP INDEX graph.x")', True),
    ('op.get_bind().exec_driver_sql(statement="DROP INDEX graph.x")', True),
    ('op.execute(sqltext=f"SELECT {n}")', True),
    ('op.execute(sa.text(text=f"SELECT {n}"))', True),
    ('op.execute(**kw)', True),                                 # SQL that cannot be found
    ('op.execute(*args)', True),
    ('op.execute(sqltext="SELECT 1")', False),
])
def test_lint_takes_sql_it_cannot_read_to_the_end_as_a_possible_drop(tmp_path, call, flagged):
    """Fail closed: SQL that is not constant text needs min_code_epoch, wherever its unknown part is; only constant text is read."""
    py = tmp_path / "0003_x.py"
    body = f'CONST = "graph.t"\nLOOPED = "a"\nfor LOOPED in ("b",):\n    pass\n\n\ndef upgrade():\n    {call}\n'
    py.write_text('revision = "graph_0003"\n' + body)
    assert any("may DROP INDEX" in m for m in lint.lint_revision(py, {"graph": 2})) is flagged, call
    py.write_text('revision = "graph_0003"\nmin_code_epoch = 3\n' + body)
    assert not any("may DROP INDEX" in m for m in lint.lint_revision(py, {"graph": 2}))


_PROC = "CREATE PROCEDURE graph.p() LANGUAGE plpgsql AS $$ BEGIN DROP INDEX graph.x; END $$;\n"
_EXEC_FN = "CREATE OR REPLACE FUNCTION graph.f() RETURNS void LANGUAGE plpgsql AS $f$ BEGIN EXECUTE 'SELECT 1'; END $f$;\n"


@pytest.mark.parametrize("name,sql,mark,flagged", [
    ("a procedure that drops an index, called", _PROC + "CALL graph.p();\n", "", True),
    ("called by its bare name", _PROC + "CALL P();\n", "", True),
    ("a function with EXECUTE, selected", _EXEC_FN + "SELECT graph.f();\n", "", True),
    ("performed in a DO body", _EXEC_FN + "DO $$ BEGIN PERFORM \"f\"(); END $$;\n", "", True),
    ("called from a routine that is run", _EXEC_FN + "CREATE FUNCTION graph.g() RETURNS void LANGUAGE sql AS $$ SELECT graph.f() $$;\n"
                                          "SELECT graph.g();\n", "", True),
    ("run by a trigger the file creates", _EXEC_FN + "CREATE TRIGGER t AFTER INSERT ON graph.y FOR EACH ROW EXECUTE FUNCTION graph.f();\n", "", True),
    ("created, not run (a writer the services call)", _EXEC_FN + "GRANT EXECUTE ON FUNCTION graph.f() TO pipeshub_node;\n"
                                                      "SELECT kernel.register_function('graph.f()'::regprocedure);\n", "", False),
    ("run, its body reads nothing unsafe", "CREATE FUNCTION graph.h() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;\nSELECT graph.h();\n", "", False),
    ("run, with a contract", _PROC + "CALL graph.p();\n", "min_code_epoch = 3\n", False),
    ("a dollar-quoted default before the body", "CREATE FUNCTION graph.f(a text DEFAULT $$a$$) RETURNS void LANGUAGE plpgsql "
                                                "AS $$ BEGIN DROP INDEX graph.x; END $$;\nSELECT graph.f();\n", "", True),
    ("a C function, run", "CREATE FUNCTION graph.f() RETURNS void LANGUAGE c AS 'lib', 'sym';\nSELECT graph.f();\n", "", True),
    ("a DO with a quoted body", "DO 'BEGIN DROP INDEX graph.x; END';\n", "", True),
    ("EXECUTE in a DO body, with a waiver", "DO $$ BEGIN EXECUTE 'DROP INDEX graph.x'; END $$;\n", "", True),
    ("a DO body that drops nothing", "DO $$ BEGIN PERFORM 1; END $$;\n", "", False),
    ("a DO with a quoted body inside a DO body", "DO $$ BEGIN DO 'BEGIN DROP INDEX graph.x; END'; END $$;\n", "", True),
    ("a quoted procedure created and called in a DO body",
     "DO $$ BEGIN CREATE PROCEDURE p() LANGUAGE plpgsql AS 'BEGIN DROP INDEX graph.x; END'; CALL p(); END $$;\n", "", True),
    ("DROP INDEX as data in a DO body", "DO $$ BEGIN RAISE NOTICE 'DROP INDEX x'; INSERT INTO graph.t VALUES ('DO'); END $$;\n", "", False),
])
def test_lint_reads_the_body_of_a_routine_the_file_runs(tmp_path, name, sql, mark, flagged):
    """A routine the .sql creates and runs in the same upgrade is code of that upgrade: its body is read with the DO-body rules. A routine it
    only creates (called later by the services) runs outside the upgrade's transaction and is not read."""
    f = tmp_path / "0003_x.sql"
    f.write_text(sql)
    f.with_suffix(".py").write_text('revision = "graph_9999"\nolder_code_compatible = "a test revision that older code never sees"\n' + mark)
    assert any("runs in the upgrade" in m for m in lint.lint_file(f)) is flagged, (name, lint.lint_file(f))


def test_lint_leaves_the_bodies_of_a_released_revision_alone(tmp_path):
    f = tmp_path / "0003_x.sql"
    f.write_text("DO $$ BEGIN EXECUTE 'SELECT 1'; END $$;\n")
    f.with_suffix(".py").write_text('revision = "graph_0003"\nolder_code_compatible = "a test revision that older code never sees"\n')
    assert not any("runs in the upgrade" in m for m in lint.lint_file(f))      # graph_0003 is in released_heads.json: it cannot change


@pytest.mark.parametrize("sql,flagged", [
    ("SELECT E'it\\'s'; DROP INDEX graph.x", True),            # the escaped quote does not end the string
    ("SELECT E'DROP INDEX graph.x \\' still data'", False),
    ("SELECT 'it''s'; DROP INDEX graph.x", True),
    ("SELECT E'\\'', 'x'; DROP INDEX graph.x; SELECT 'y'", True),     # read as '' (a doubled quote) the drop hid in a string
])
def test_lint_reads_backslash_escapes_in_an_e_string(tmp_path, sql, flagged):
    py = tmp_path / "0003_x.py"
    py.write_text('revision = "graph_0003"\n\n\ndef upgrade():\n    op.execute(' + repr(sql) + ')\n')
    assert any("may DROP INDEX" in m for m in lint.lint_revision(py, {"graph": 2})) is flagged, sql


def test_normalise_leaves_field_labels_that_match_a_name():
    from db import surface
    lines = ["database owner=owner", "role owner login=true", "member owner -> x admin=true", "column kernel.settings.owner type=name",
             "policy t p cmd=r permissive=true roles=owner,pipeshub_app using=(owner = CURRENT_USER) check=-",
             "column t.c type=text notnull=false identity= generated= default=owner"]
    assert surface.normalise(lines, "postgres", "owner") == {
        "database owner=<bootstrap-superuser>", "role <bootstrap-superuser> login=true", "member <bootstrap-superuser> -> x admin=true",
        "column kernel.settings.owner type=name",
        "policy t p cmd=r permissive=true roles=<bootstrap-superuser>,pipeshub_app using=(owner = CURRENT_USER) check=-",
        "column t.c type=text notnull=false identity= generated= default=owner"}
