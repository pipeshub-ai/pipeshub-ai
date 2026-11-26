"""kg: the counters no longer keep facet 2 (record type and origin), which nothing reads; its rows are deleted"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "kg_0023"
down_revision = "kg_0022"
branch_labels = None
depends_on = None
older_code_compatible = ("replaces kg.count_keys so that it adds no facet 2 delta and deletes the facet 2 rows; no release reads facet 2, the fold of an earlier release "
                         "folds what it finds, and its seed may still write facet 2 rows, which the table and kg.w_counts_add still take")

# kg_0015's body, put back by the downgrade. The facet 2 rows deleted by the upgrade are not rebuilt: nothing reads them.
KG_0015_COUNT_KEYS = """CREATE OR REPLACE FUNCTION kg.count_keys(p_rows kg.records[], p_sign bigint)
RETURNS TABLE (org_id text, app_sk bigint, facet smallint, class_id bigint, kind smallint, folder boolean, record_type text, origin text, n bigint)
LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog, pg_temp AS $fn$
  SELECT r.org_id, r.app_sk, 1::smallint, r.class_id, r.kind, r.folder, NULL::text, NULL::text, p_sign * count(*) FROM unnest(p_rows) r
  WHERE r.app_sk IS NOT NULL AND r.kind IN (2, 3) AND NOT r.deleted AND NOT r.placeholder AND NOT r.alien
    AND r.r13 IS FALSE AND r.path IS NOT NULL AND r.class_id IS NOT NULL AND r.fence_job IS NULL
  GROUP BY r.org_id, r.app_sk, r.class_id, r.kind, r.folder
  UNION ALL
  SELECT r.org_id, r.app_sk, 2::smallint, NULL::bigint, NULL::smallint, NULL::boolean, r.record_type, r.origin, p_sign * count(*) FROM unnest(p_rows) r
  WHERE r.app_sk IS NOT NULL AND r.kind = 3 AND NOT r.folder AND NOT r.deleted AND NOT r.placeholder AND NOT r.alien
  GROUP BY r.org_id, r.app_sk, r.record_type, r.origin
$fn$"""


def upgrade():
    run_sql(__file__)


def downgrade():
    op.execute(KG_0015_COUNT_KEYS)
