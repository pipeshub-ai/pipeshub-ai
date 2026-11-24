-- What a knowledge base's folder listing and its trash's single-file view filter and order by (kg.model.kb_listing), written by w_kb_listing_set: the record's indexing
-- status and connector name, its own created and updated times, and the file it is typed as (isOfType to 'files': file_kind, file_size). kb_odd marks a record whose values
-- these columns cannot hold as the listing reads them (a listing that meets one is answered by the walk).
ALTER TABLE kg.records ADD COLUMN indexing_status text, ADD COLUMN connector_name text, ADD COLUMN doc_created_ms bigint, ADD COLUMN doc_updated_ms bigint,
  ADD COLUMN file_size bigint, ADD COLUMN file_kind smallint, ADD COLUMN kb_odd boolean NOT NULL DEFAULT false;

-- kg_0019's kg.proj_doc with indexingStatus and connectorName, and what a record's listing reads of the file it is typed as: a change to them is a change of the node.
CREATE OR REPLACE FUNCTION kg.proj_doc(p_coll text, p_doc jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN p_coll IN ('apps', 'recordGroups', 'records') THEN jsonb_strip_nulls(jsonb_build_object(
    'accessRule', p_doc->'accessRule', 'permissionModel', p_doc->'permissionModel',
    'connectorId', p_doc->'connectorId', 'type', p_doc->'type', 'name', p_doc->'name', 'groupName', p_doc->'groupName', 'recordName', p_doc->'recordName',
    'externalRecordId', p_doc->'externalRecordId', 'recordType', p_doc->'recordType', 'origin', p_doc->'origin', 'virtualRecordId', p_doc->'virtualRecordId',
    'createdAtTimestamp', p_doc->'createdAtTimestamp', 'updatedAtTimestamp', p_doc->'updatedAtTimestamp',
    'sourceCreatedAtTimestamp', p_doc->'sourceCreatedAtTimestamp', 'sourceLastModifiedTimestamp', p_doc->'sourceLastModifiedTimestamp',
    'sizeInBytes', p_doc->'sizeInBytes', 'mimeType', p_doc->'mimeType', 'hideChildren', p_doc->'hideChildren', 'accessOnly', p_doc->'accessOnly', 'orgId', p_doc->'orgId',
    'deletedAtTimestamp', p_doc->'deletedAtTimestamp', 'deleteBatchId', p_doc->'deleteBatchId', 'indexingStatus', p_doc->'indexingStatus', 'connectorName', p_doc->'connectorName'))
    || COALESCE(CASE WHEN jsonb_typeof(p_doc->'isDeleted') <> 'null' THEN jsonb_build_object('isDeleted', p_doc->'isDeleted') END, '{}'::jsonb)
    || COALESCE(CASE WHEN jsonb_typeof(p_doc->'isPlaceholder') <> 'null' THEN jsonb_build_object('isPlaceholder', p_doc->'isPlaceholder') END, '{}'::jsonb)
    WHEN p_coll = 'files' THEN jsonb_strip_nulls(jsonb_build_object('isFile', p_doc->'isFile', 'sizeInBytes', p_doc->'sizeInBytes')) END $$;

-- kg_0003's kg.edge_ends with isOfType: the record's listing columns read the file it is typed as.
CREATE OR REPLACE FUNCTION kg.edge_ends(p_coll text, p_from_coll text, p_from_id text, p_to_coll text, p_to_id text, p_doc jsonb) RETURNS TABLE (coll text, id text)
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT x.c, x.i FROM (VALUES (p_from_coll, p_from_id, 'f'), (p_to_coll, p_to_id, 't')) x(c, i, side)
  WHERE kg.coll_code(x.c) IS NOT NULL AND CASE p_coll
    WHEN 'nodeRelations' THEN COALESCE(p_doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT'), false) AND x.side = 't'
    WHEN 'belongsTo' THEN x.side = 'f'
    WHEN 'inheritPermissions' THEN x.side = 'f'
    WHEN 'permission' THEN CASE WHEN p_to_coll IN ('apps', 'recordGroups', 'records') THEN x.side = 't' WHEN p_to_coll IN ('groups', 'roles', 'teams') THEN x.side = 'f' ELSE true END
    WHEN 'userAppRelation' THEN x.side = 't'
    WHEN 'authenticatedAs' THEN x.side = 'f'
    WHEN 'isOfType' THEN x.side = 'f' AND p_from_coll = 'records' AND p_to_coll = 'files'
    ELSE false END $$;

-- kg_0021's capture, and a file's type document: a change of it is logged as a change of the records whose isOfType edge points at it, under their connector.
CREATE OR REPLACE FUNCTION kg.log_graph_change() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, kg, pg_temp SET enable_nestloop = off SET jit = off AS $fn$
BEGIN
  PERFORM set_config('kg.wrote', '1', true);       -- the unit wrote the graph: the commit hook reads this, and does not have to recognise the statement
  IF TG_TABLE_NAME = 'nodes' THEN
    IF TG_OP = 'INSERT' THEN
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT n.org_id, CASE n.coll WHEN 'apps' THEN n.id ELSE COALESCE(n.doc->>'connectorId', '') END, array_agg(kg.coll_code(n.coll) || ':' || n.id)
        FROM newtab n WHERE kg.coll_code(n.coll) IS NOT NULL GROUP BY 1, 2, length(n.id) > 256;
      -- a file's type document: a change of what a record's listing reads of it (kg.proj_doc), or the document coming or going, is a change of the records typed by it
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT f.org_id, COALESCE(r.cid, ''), array_agg(DISTINCT kg.coll_code('records') || ':' || e.from_id)
        FROM (SELECT n.org_id, n.id FROM newtab n WHERE n.coll = 'files') f
        CROSS JOIN LATERAL (SELECT e.from_id FROM graph.edges e WHERE e.org_id = f.org_id AND e.coll = 'isOfType' AND e.to_id = f.id AND e.to_coll = 'files' AND e.from_coll = 'records') e
        LEFT JOIN LATERAL (SELECT n.doc->>'connectorId' AS cid FROM graph.nodes n WHERE n.org_id = f.org_id AND n.coll = 'records' AND n.id = e.from_id) r ON true
        GROUP BY 1, 2, length(e.from_id) > 256;
    ELSIF TG_OP = 'DELETE' THEN
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT o.org_id, CASE o.coll WHEN 'apps' THEN o.id ELSE COALESCE(o.doc->>'connectorId', '') END, array_agg(kg.coll_code(o.coll) || ':' || o.id)
        FROM oldtab o WHERE kg.coll_code(o.coll) IS NOT NULL GROUP BY 1, 2, length(o.id) > 256;
      -- a file's type document: a change of what a record's listing reads of it (kg.proj_doc), or the document coming or going, is a change of the records typed by it
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT f.org_id, COALESCE(r.cid, ''), array_agg(DISTINCT kg.coll_code('records') || ':' || e.from_id)
        FROM (SELECT o.org_id, o.id FROM oldtab o WHERE o.coll = 'files') f
        CROSS JOIN LATERAL (SELECT e.from_id FROM graph.edges e WHERE e.org_id = f.org_id AND e.coll = 'isOfType' AND e.to_id = f.id AND e.to_coll = 'files' AND e.from_coll = 'records') e
        LEFT JOIN LATERAL (SELECT n.doc->>'connectorId' AS cid FROM graph.nodes n WHERE n.org_id = f.org_id AND n.coll = 'records' AND n.id = e.from_id) r ON true
        GROUP BY 1, 2, length(e.from_id) > 256;
    ELSE
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT n.org_id, CASE n.coll WHEN 'apps' THEN n.id ELSE COALESCE(n.doc->>'connectorId', '') END, array_agg(kg.coll_code(n.coll) || ':' || n.id)
        FROM newtab n JOIN oldtab o ON o.org_id = n.org_id AND o.coll = n.coll AND o.id = n.id
        WHERE kg.coll_code(n.coll) IS NOT NULL AND kg.proj_doc(n.coll, o.doc) IS DISTINCT FROM kg.proj_doc(n.coll, n.doc) GROUP BY 1, 2, length(n.id) > 256;
      -- a node that changed connector is also logged under the connector it left, so that connector is not current while its record is stale
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT o.org_id, CASE o.coll WHEN 'apps' THEN o.id ELSE COALESCE(o.doc->>'connectorId', '') END, array_agg(kg.coll_code(o.coll) || ':' || o.id)
        FROM newtab n JOIN oldtab o ON o.org_id = n.org_id AND o.coll = n.coll AND o.id = n.id
        WHERE kg.coll_code(o.coll) IS NOT NULL AND o.coll <> 'apps' AND COALESCE(o.doc->>'connectorId', '') IS DISTINCT FROM COALESCE(n.doc->>'connectorId', '') GROUP BY 1, 2, length(o.id) > 256;
      -- an UPDATE of the key columns: the old key is a node that went, the new one a node that came
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT o.org_id, CASE o.coll WHEN 'apps' THEN o.id ELSE COALESCE(o.doc->>'connectorId', '') END, array_agg(kg.coll_code(o.coll) || ':' || o.id)
        FROM oldtab o WHERE kg.coll_code(o.coll) IS NOT NULL AND NOT EXISTS (SELECT 1 FROM newtab n WHERE n.org_id = o.org_id AND n.coll = o.coll AND n.id = o.id) GROUP BY 1, 2, length(o.id) > 256;
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT n.org_id, CASE n.coll WHEN 'apps' THEN n.id ELSE COALESCE(n.doc->>'connectorId', '') END, array_agg(kg.coll_code(n.coll) || ':' || n.id)
        FROM newtab n WHERE kg.coll_code(n.coll) IS NOT NULL AND NOT EXISTS (SELECT 1 FROM oldtab o WHERE o.org_id = n.org_id AND o.coll = n.coll AND o.id = n.id) GROUP BY 1, 2, length(n.id) > 256;
      -- a file's type document: a change of what a record's listing reads of it (kg.proj_doc), or the document coming or going, is a change of the records typed by it
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT f.org_id, COALESCE(r.cid, ''), array_agg(DISTINCT kg.coll_code('records') || ':' || e.from_id)
        FROM (SELECT n.org_id, n.id FROM newtab n LEFT JOIN oldtab o ON o.org_id = n.org_id AND o.coll = n.coll AND o.id = n.id
                WHERE n.coll = 'files' AND (o.id IS NULL OR kg.proj_doc(n.coll, o.doc) IS DISTINCT FROM kg.proj_doc(n.coll, n.doc))
              UNION SELECT o.org_id, o.id FROM oldtab o WHERE o.coll = 'files' AND NOT EXISTS (SELECT 1 FROM newtab n WHERE n.org_id = o.org_id AND n.coll = o.coll AND n.id = o.id)) f
        CROSS JOIN LATERAL (SELECT e.from_id FROM graph.edges e WHERE e.org_id = f.org_id AND e.coll = 'isOfType' AND e.to_id = f.id AND e.to_coll = 'files' AND e.from_coll = 'records') e
        LEFT JOIN LATERAL (SELECT n.doc->>'connectorId' AS cid FROM graph.nodes n WHERE n.org_id = f.org_id AND n.coll = 'records' AND n.id = e.from_id) r ON true
        GROUP BY 1, 2, length(e.from_id) > 256;
    END IF;
  ELSE
    IF TG_OP = 'INSERT' THEN
      INSERT INTO kg.graph_changes (org_id, grant_only, ends)
        SELECT e.org_id, (e.coll = 'permission' AND e.to_coll IN ('records', 'recordGroups')), array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id) FROM newtab e, LATERAL kg.edge_ends(e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, e.doc) x GROUP BY 1, 2, length(x.id) > 256;
    ELSIF TG_OP = 'DELETE' THEN
      INSERT INTO kg.graph_changes (org_id, grant_only, ends)
        SELECT e.org_id, (e.coll = 'permission' AND e.to_coll IN ('records', 'recordGroups')), array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id) FROM oldtab e, LATERAL kg.edge_ends(e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, e.doc) x GROUP BY 1, 2, length(x.id) > 256;
    ELSE   -- a changed document: only when what the projection reads of it changed (the old and the new row name the nodes: a link can be retyped to or from a hierarchy relation)
      INSERT INTO kg.graph_changes (org_id, grant_only, ends)
        SELECT e.org_id, (e.coll = 'permission' AND e.to_coll IN ('records', 'recordGroups')), array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id)
        FROM newtab e JOIN oldtab o ON o.org_id = e.org_id AND o.coll = e.coll AND o.from_coll = e.from_coll AND o.from_id = e.from_id
                                   AND o.to_coll = e.to_coll AND o.to_id = e.to_id AND o.ekey = e.ekey,
             LATERAL (SELECT * FROM kg.edge_ends(e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, e.doc)
                      UNION SELECT * FROM kg.edge_ends(o.coll, o.from_coll, o.from_id, o.to_coll, o.to_id, o.doc)) x
        WHERE kg.edge_sem(e.coll, o.doc) IS DISTINCT FROM kg.edge_sem(e.coll, e.doc) GROUP BY 1, 2, length(x.id) > 256;
      -- an UPDATE of the key columns (from_id, to_id ...): the ends of the edge that went and of the edge that came
      INSERT INTO kg.graph_changes (org_id, grant_only, ends)
        SELECT o.org_id, (o.coll = 'permission' AND o.to_coll IN ('records', 'recordGroups')), array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id)
        FROM oldtab o, LATERAL kg.edge_ends(o.coll, o.from_coll, o.from_id, o.to_coll, o.to_id, o.doc) x
        WHERE NOT EXISTS (SELECT 1 FROM newtab e WHERE e.org_id = o.org_id AND e.coll = o.coll AND e.from_coll = o.from_coll AND e.from_id = o.from_id
                            AND e.to_coll = o.to_coll AND e.to_id = o.to_id AND e.ekey = o.ekey) GROUP BY 1, 2, length(x.id) > 256;
      INSERT INTO kg.graph_changes (org_id, grant_only, ends)
        SELECT e.org_id, (e.coll = 'permission' AND e.to_coll IN ('records', 'recordGroups')), array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id)
        FROM newtab e, LATERAL kg.edge_ends(e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, e.doc) x
        WHERE NOT EXISTS (SELECT 1 FROM oldtab o WHERE e.org_id = o.org_id AND e.coll = o.coll AND e.from_coll = o.from_coll AND e.from_id = o.from_id
                            AND e.to_coll = o.to_coll AND e.to_id = o.to_id AND e.ekey = o.ekey) GROUP BY 1, 2, length(x.id) > 256;
    END IF;
  END IF;
  RETURN NULL;
END $fn$;

SELECT kernel.exempt_trigger('kg.log_graph_change()', 'inserts the keys of the changed nodes into kg.graph_changes as the writer of graph.*, in its transaction; reads its transition tables and, for a changed file document, the isOfType edges into it and the connector of each record they come from', 'security-review');
