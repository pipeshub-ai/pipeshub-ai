-- Pure SQL helpers of the projection: the collection codes, which end of an edge a kg row depends on, and the part of a document the projection reads. The reader
-- (kg/reader.py) selects the projected part of a node; the change capture (a later revision) uses the same functions to decide whether a write is a change.
-- The collections kg projects, as the small code graph_changes stores: apps, record groups and records are nodes of kg.records; users, groups,
-- roles, teams and organizations are only keyed (they hold and receive grants and memberships).
CREATE FUNCTION kg.coll_code(p_coll text) RETURNS smallint LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE p_coll WHEN 'apps' THEN 1 WHEN 'recordGroups' THEN 2 WHEN 'records' THEN 3 WHEN 'users' THEN 4 WHEN 'groups' THEN 5
                     WHEN 'roles' THEN 6 WHEN 'teams' THEN 7 WHEN 'organizations' THEN 8 END::smallint $$;

-- These four SQL helpers set no search_path on purpose: a function with a SET clause is never inlined, and the trigger would call each once per row. They name no object, only literals
-- and pg_catalog operators, and the trigger that calls them sets its own search path.
-- Which end of an edge row a change to it changes in kg, and which part of its document the projection reads. A hierarchy link (parent to child) changes the
-- child's row; a belongs-to and an inherits-permissions link, the source's; a grant, the node it is on (a record, record group or app) or, for a membership of a
-- group, role or team, the member (its closure); a user-app link, the app; an authenticated-as link, the user. The other end is untouched, so a connector with
-- a record group of a million members does not re-project the group for each of them.
CREATE FUNCTION kg.edge_ends(p_coll text, p_from_coll text, p_from_id text, p_to_coll text, p_to_id text, p_doc jsonb) RETURNS TABLE (coll text, id text)
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT x.c, x.i FROM (VALUES (p_from_coll, p_from_id, 'f'), (p_to_coll, p_to_id, 't')) x(c, i, side)
  WHERE kg.coll_code(x.c) IS NOT NULL AND CASE p_coll
    WHEN 'nodeRelations' THEN COALESCE(p_doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT'), false) AND x.side = 't'
    WHEN 'belongsTo' THEN x.side = 'f'
    WHEN 'inheritPermissions' THEN x.side = 'f'
    WHEN 'permission' THEN CASE WHEN p_to_coll IN ('apps', 'recordGroups', 'records') THEN x.side = 't' WHEN p_to_coll IN ('groups', 'roles', 'teams') THEN x.side = 'f' ELSE true END
    WHEN 'userAppRelation' THEN x.side = 't'
    WHEN 'authenticatedAs' THEN x.side = 'f'
    ELSE false END $$;

CREATE FUNCTION kg.edge_sem(p_coll text, p_doc jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE p_coll WHEN 'permission' THEN jsonb_build_object('type', p_doc->'type', 'role', p_doc->'role')
                     WHEN 'nodeRelations' THEN jsonb_build_object('relationshipType', p_doc->'relationshipType')
                     WHEN 'authenticatedAs' THEN jsonb_build_object('connectorId', p_doc->'connectorId')
                     ELSE '{}'::jsonb END $$;

-- What the projection reads of a node document. A node update that leaves this unchanged (an indexing-status flip, a generation bump) is not a change.
CREATE FUNCTION kg.proj_doc(p_coll text, p_doc jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN p_coll IN ('apps', 'recordGroups', 'records') THEN jsonb_strip_nulls(jsonb_build_object(
    'accessRule', p_doc->'accessRule', 'permissionModel', p_doc->'permissionModel',
    'connectorId', p_doc->'connectorId', 'type', p_doc->'type', 'name', p_doc->'name', 'groupName', p_doc->'groupName', 'recordName', p_doc->'recordName',
    'externalRecordId', p_doc->'externalRecordId', 'recordType', p_doc->'recordType', 'origin', p_doc->'origin', 'virtualRecordId', p_doc->'virtualRecordId',
    'createdAtTimestamp', p_doc->'createdAtTimestamp', 'updatedAtTimestamp', p_doc->'updatedAtTimestamp',
    'sourceCreatedAtTimestamp', p_doc->'sourceCreatedAtTimestamp', 'sourceLastModifiedTimestamp', p_doc->'sourceLastModifiedTimestamp',
    'sizeInBytes', p_doc->'sizeInBytes', 'mimeType', p_doc->'mimeType', 'hideChildren', p_doc->'hideChildren', 'orgId', p_doc->'orgId'))
    || COALESCE(CASE WHEN jsonb_typeof(p_doc->'isDeleted') <> 'null' THEN jsonb_build_object('isDeleted', p_doc->'isDeleted') END, '{}'::jsonb)
    || COALESCE(CASE WHEN jsonb_typeof(p_doc->'isPlaceholder') <> 'null' THEN jsonb_build_object('isPlaceholder', p_doc->'isPlaceholder') END, '{}'::jsonb) END $$;
