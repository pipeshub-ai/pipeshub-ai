"""graph: vetted reads and indexes of the entity pipeline's lookups (named entities by key, alias and key prefix, value mentions by record, stage states by revision)"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.vetted import run_vetted

revision = "graph_0007"
down_revision = "graph_0006"
branch_labels = None
depends_on = None
older_code_compatible = "adds an index and functions only; older code does not call them and reads the same rows"

FUNCTIONS = {"named_entities_by_keys": "text, text, text[]", "named_entities_by_alias": "text, text[], text[], int", "named_entities_by_prefix": "text, text[], text[], int",
             "value_mentions_by_records": "text[]", "stage_states_by_revision": "text, text"}


def upgrade():
    # The key of a named entity, the value mentions of a record and the stage states of a revision are indexed by the baseline (graph_0001); the aliases are not.
    # Built inside the upgrade's one transaction, as graph_0006 does (see its note on a graph that holds rows).
    ensure_partitioned_index(op.get_bind(), "graph.nodes", "nodes_ne_aliases", "(doc->'normalizedAliases')", where="coll = 'namedEntities'", blocking=True, using="gin")
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    op.execute("DROP INDEX IF EXISTS graph.nodes_ne_aliases")
