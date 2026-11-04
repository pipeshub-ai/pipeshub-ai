"""kernel: the text normaliser ends a -- comment at a carriage return as PostgreSQL does"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kernel_0015"
down_revision = "kernel_0014"
branch_labels = None
depends_on = None
older_code_compatible = "same signature and result for every body without a carriage return; a body with one only shows the checks more text"


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute(r"""CREATE OR REPLACE FUNCTION kernel.src_text(p_src text, p_keep_strings boolean DEFAULT false) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $fn$
  SELECT regexp_replace(regexp_replace(
    CASE WHEN p_keep_strings THEN x.s ELSE regexp_replace(x.s, '''(?:[^'']|'''')*''', '''''', 'g') END,
    '[ \t\n\r\f\v]*\.[ \t\n\r\f\v]*', '.', 'g'), '[ \t\n\r\f\v]+', ' ', 'g')
  FROM (SELECT regexp_replace(p_src, '(''(?:[^'']|'''')*'')|--[^\n]*|/\*([^*]|\*+[^*/])*\*+/', ' \1', 'g') AS s) x
$fn$""")
