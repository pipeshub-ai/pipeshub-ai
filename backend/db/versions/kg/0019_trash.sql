-- deleted_ms, delete_batch: the node's deletedAtTimestamp and deleteBatchId, which a trash listing pages by (written by w_trash_set).
ALTER TABLE kg.records ADD COLUMN deleted_ms bigint, ADD COLUMN delete_batch text;

-- kg_0018's kg.proj_doc with deletedAtTimestamp and deleteBatchId: a change to them is a change of the node, which the trash columns follow.
CREATE OR REPLACE FUNCTION kg.proj_doc(p_coll text, p_doc jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN p_coll IN ('apps', 'recordGroups', 'records') THEN jsonb_strip_nulls(jsonb_build_object(
    'accessRule', p_doc->'accessRule', 'permissionModel', p_doc->'permissionModel',
    'connectorId', p_doc->'connectorId', 'type', p_doc->'type', 'name', p_doc->'name', 'groupName', p_doc->'groupName', 'recordName', p_doc->'recordName',
    'externalRecordId', p_doc->'externalRecordId', 'recordType', p_doc->'recordType', 'origin', p_doc->'origin', 'virtualRecordId', p_doc->'virtualRecordId',
    'createdAtTimestamp', p_doc->'createdAtTimestamp', 'updatedAtTimestamp', p_doc->'updatedAtTimestamp',
    'sourceCreatedAtTimestamp', p_doc->'sourceCreatedAtTimestamp', 'sourceLastModifiedTimestamp', p_doc->'sourceLastModifiedTimestamp',
    'sizeInBytes', p_doc->'sizeInBytes', 'mimeType', p_doc->'mimeType', 'hideChildren', p_doc->'hideChildren', 'accessOnly', p_doc->'accessOnly', 'orgId', p_doc->'orgId',
    'deletedAtTimestamp', p_doc->'deletedAtTimestamp', 'deleteBatchId', p_doc->'deleteBatchId'))
    || COALESCE(CASE WHEN jsonb_typeof(p_doc->'isDeleted') <> 'null' THEN jsonb_build_object('isDeleted', p_doc->'isDeleted') END, '{}'::jsonb)
    || COALESCE(CASE WHEN jsonb_typeof(p_doc->'isPlaceholder') <> 'null' THEN jsonb_build_object('isPlaceholder', p_doc->'isPlaceholder') END, '{}'::jsonb) END $$;
