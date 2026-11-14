"""kg: the projection reads ``accessOnly`` of a record group (a group that carries access only is no row of a listing)"""
from alembic import op

revision = "kg_0013"
down_revision = "kg_0012"
branch_labels = None
depends_on = None
older_code_compatible = "adds a key to the projected subset of a document; older code reads the keys it knows and ignores the new one"

# kg_0003's kg.proj_doc with accessOnly beside hideChildren: a change to it is a change of the node, which the listing column `alien` follows.
UPGRADE = """CREATE OR REPLACE FUNCTION kg.proj_doc(p_coll text, p_doc jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN p_coll IN ('apps', 'recordGroups', 'records') THEN jsonb_strip_nulls(jsonb_build_object(
    'accessRule', p_doc->'accessRule', 'permissionModel', p_doc->'permissionModel',
    'connectorId', p_doc->'connectorId', 'type', p_doc->'type', 'name', p_doc->'name', 'groupName', p_doc->'groupName', 'recordName', p_doc->'recordName',
    'externalRecordId', p_doc->'externalRecordId', 'recordType', p_doc->'recordType', 'origin', p_doc->'origin', 'virtualRecordId', p_doc->'virtualRecordId',
    'createdAtTimestamp', p_doc->'createdAtTimestamp', 'updatedAtTimestamp', p_doc->'updatedAtTimestamp',
    'sourceCreatedAtTimestamp', p_doc->'sourceCreatedAtTimestamp', 'sourceLastModifiedTimestamp', p_doc->'sourceLastModifiedTimestamp',
    'sizeInBytes', p_doc->'sizeInBytes', 'mimeType', p_doc->'mimeType', 'hideChildren', p_doc->'hideChildren', 'accessOnly', p_doc->'accessOnly', 'orgId', p_doc->'orgId'))
    || COALESCE(CASE WHEN jsonb_typeof(p_doc->'isDeleted') <> 'null' THEN jsonb_build_object('isDeleted', p_doc->'isDeleted') END, '{}'::jsonb)
    || COALESCE(CASE WHEN jsonb_typeof(p_doc->'isPlaceholder') <> 'null' THEN jsonb_build_object('isPlaceholder', p_doc->'isPlaceholder') END, '{}'::jsonb) END $$;"""

DOWNGRADE = """CREATE OR REPLACE FUNCTION kg.proj_doc(p_coll text, p_doc jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN p_coll IN ('apps', 'recordGroups', 'records') THEN jsonb_strip_nulls(jsonb_build_object(
    'accessRule', p_doc->'accessRule', 'permissionModel', p_doc->'permissionModel',
    'connectorId', p_doc->'connectorId', 'type', p_doc->'type', 'name', p_doc->'name', 'groupName', p_doc->'groupName', 'recordName', p_doc->'recordName',
    'externalRecordId', p_doc->'externalRecordId', 'recordType', p_doc->'recordType', 'origin', p_doc->'origin', 'virtualRecordId', p_doc->'virtualRecordId',
    'createdAtTimestamp', p_doc->'createdAtTimestamp', 'updatedAtTimestamp', p_doc->'updatedAtTimestamp',
    'sourceCreatedAtTimestamp', p_doc->'sourceCreatedAtTimestamp', 'sourceLastModifiedTimestamp', p_doc->'sourceLastModifiedTimestamp',
    'sizeInBytes', p_doc->'sizeInBytes', 'mimeType', p_doc->'mimeType', 'hideChildren', p_doc->'hideChildren', 'orgId', p_doc->'orgId'))
    || COALESCE(CASE WHEN jsonb_typeof(p_doc->'isDeleted') <> 'null' THEN jsonb_build_object('isDeleted', p_doc->'isDeleted') END, '{}'::jsonb)
    || COALESCE(CASE WHEN jsonb_typeof(p_doc->'isPlaceholder') <> 'null' THEN jsonb_build_object('isPlaceholder', p_doc->'isPlaceholder') END, '{}'::jsonb) END $$;"""


def upgrade():
    op.execute(UPGRADE)


def downgrade():
    op.execute(DOWNGRADE)
