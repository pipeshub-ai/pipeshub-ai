"""graph: the code graph's hot lookups seek a page past its cursor inside an index of the key and id, so reading every match pages in O(matches)"""
from alembic import op
from db.lib.ddl import ensure_partitioned_index
from db.lib.vetted import run_vetted

revision = "graph_0018"
down_revision = "graph_0017"
branch_labels = None
depends_on = None
older_code_compatible = "adds three indexes and replaces three function bodies with ones that answer alike; older code calls the same arguments"

FUNCTIONS = {"blocks_by_record": "text, text[], jsonb[], boolean, text, int", "blocks_by_name": "text, text, text[], jsonb[], boolean, text, int",
             "code_files_by_path": "text, text[], jsonb[], boolean, text, int"}
# An index per lookup of a 4-byte hash of the key, then id: the matches of a key come in id order, so a page from a cursor reads n entries past it,
# not every match. The key is hashed, not stored, so an entry is never wider than the same row's primary-key entry (org_id, coll, id): the int4
# after org_id takes at most 7 bytes where coll takes 7 or more ('blocks', 'codeFiles'), so every row graph.nodes holds fits, whatever the length
# of its path, name or record id (a key stored whole fails past about 2,700 bytes, at the build or at a later write). The graph_0001 indexes stay
# until a later contract revision drops them (dropping one here would lock readers out of the table); the prefix reads of a path keep theirs.
ONLINE_INDEXES = [
    {"table": "graph.nodes", "name": "nodes_block_record_seek", "cols": "org_id, hashtext(doc->>'recordId'), id", "where": "coll = 'blocks'"},
    {"table": "graph.nodes", "name": "nodes_block_name_seek", "cols": "org_id, hashtext((doc->>'connectorId') || '/' || (doc->>'nameFolded')), id", "where": "coll = 'blocks'"},
    {"table": "graph.nodes", "name": "nodes_codefile_path_seek", "cols": "org_id, hashtext(doc->>'filePath'), id", "where": "coll = 'codeFiles'"},
]


def upgrade():
    for name, args in FUNCTIONS.items():
        op.execute(f"DROP FUNCTION IF EXISTS graph.{name}({args})")
    op.execute("DELETE FROM kernel.function_registry WHERE proc::oid NOT IN (SELECT oid FROM pg_proc)")
    # db.migrate builds ONLINE_INDEXES before this transaction without blocking writers; here they are found valid (a new install builds them, empty)
    for ix in ONLINE_INDEXES:
        ensure_partitioned_index(op.get_bind(), **ix, blocking=True)
    run_vetted(__file__, *FUNCTIONS)


def downgrade():
    # the bodies this revision installs stay (they answer as the earlier ones did; graph_0003 owns the functions)
    op.execute("DROP INDEX IF EXISTS graph.nodes_block_record_seek, graph.nodes_block_name_seek, graph.nodes_codefile_path_seek")
