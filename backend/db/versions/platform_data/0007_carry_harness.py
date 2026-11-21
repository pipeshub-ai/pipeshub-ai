"""platform data: the key map's deployment-wide form, and the harness tables the remaining carriers write (R46-CARRY)"""
from alembic import op
from db.lib.run_sql import run_sql

revision = "platform_data_0007"
down_revision = "platform_data_0006"
branch_labels = None
depends_on = ("harness_0011",)
older_code_compatible = "adds a table only the import role reads and writes, and the import role's policy and privileges on harness tables; no application role changes and no existing column or function changes"

ORG_SCOPED = (
    "harness.runs", "harness.run_events", "harness.session_counters", "harness.next_runs", "harness.session_events",
    "harness.stream_counters", "harness.journal_payloads", "harness.session_forks", "harness.sandbox_snapshots",
    "harness.command_receipts", "harness.admission_receipts", "harness.admission_quotas",
    "harness.budget_accounts", "harness.budget_reservations", "harness.engine_signins", "harness.memories", "harness.schedules",
    "harness.schedule_firings",
    "harness.schedule_counters", "harness.inbox_preferences", "harness.run_end_notices",
    "harness.session_erasures", "harness.agent_erasures", "harness.principal_erasures", "harness.erasure_backfill_items",
    "harness.erasure_backfill_markers",
    "harness.cp_org_heads", "harness.cp_policy_layers", "harness.cp_policy_history", "harness.cp_migration_ledger",
    "harness.cp_catalog_items", "harness.cp_installations", "harness.cp_installation_history", "harness.cp_grants",
    "harness.cp_capability_ledger", "harness.cp_bindings",
)
DEPLOYMENT_WIDE = ("harness.sandbox_reaps", "harness.erasure_backfill_plans", "harness.cp_deployment_head")


def upgrade():
    run_sql(__file__)


def downgrade():
    for t in ORG_SCOPED:
        op.execute(f"DROP POLICY IF EXISTS import_all ON {t}")
        op.execute(f"UPDATE kernel.rls_registry SET import_access = false WHERE rel = '{t}'::regclass")
        op.execute(f"REVOKE ALL ON {t} FROM pipeshub_import")
    for t in DEPLOYMENT_WIDE:
        op.execute(f"REVOKE ALL ON {t} FROM pipeshub_import")
    op.execute("DROP TABLE platform.docstore_keymap_deployment")
    op.execute("DELETE FROM kernel.rls_registry WHERE rel::oid NOT IN (SELECT oid FROM pg_class)")
