"""graph: a code file's path is looked up within its connector and record group, and a repo name finds its record groups by their repoKey: four seek
indexes and their vetted reads"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.vetted import run_vetted

revision = "graph_0024"
down_revision = "graph_0023"
branch_labels = None
depends_on = None
older_code_compatible = "adds four indexes and four read functions; older code calls none of them"

FUNCTIONS = {"code_files_by_scope": "text, text, text[], jsonb[], boolean, text, int", "code_files_by_repo": "text, text, text, text[], jsonb[], boolean, text, int",
             "record_groups_by_repo_key": "text, text[], jsonb[], boolean, text, int", "record_groups_by_parent": "text, text, text[], jsonb[], boolean, text, int"}
# As graph_0018: a 4-byte hash of the key, then id, so an entry is never wider than the row's primary-key entry and a page from a cursor reads a page.
# The keys are the document fields the code-graph projection and the backfill write (codeFiles connectorId and recordGroupId, recordGroups repoKey).
ONLINE_INDEXES = [
    {"table": "graph.nodes", "name": "nodes_codefile_scope_seek", "cols": "org_id, hashtext((doc->>'connectorId') || '/' || (doc->>'filePath')), id", "where": "coll = 'codeFiles'"},
    {"table": "graph.nodes", "name": "nodes_codefile_repo_seek",
     "cols": "org_id, hashtext((doc->>'connectorId') || '/' || (doc->>'recordGroupId') || '/' || (doc->>'filePath')), id", "where": "coll = 'codeFiles'"},
    {"table": "graph.nodes", "name": "nodes_group_repo_key_seek", "cols": "org_id, hashtext(doc->>'repoKey'), id", "where": "coll = 'recordGroups'"},
    {"table": "graph.nodes", "name": "nodes_group_parent_seek", "cols": "org_id, hashtext((doc->>'connectorId') || '/' || (doc->>'parentExternalGroupId')), id",
     "where": "coll = 'recordGroups'"},
]


def upgrade():
    # db.migrate builds ONLINE_INDEXES before this transaction without blocking writers; here they are found valid (a new install builds them, empty)
    for ix in ONLINE_INDEXES:
        ensure_partitioned_index(op.get_bind(), **ix, blocking=True)
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    op.execute("DROP INDEX IF EXISTS graph.nodes_codefile_scope_seek, graph.nodes_codefile_repo_seek, graph.nodes_group_repo_key_seek, graph.nodes_group_parent_seek")
