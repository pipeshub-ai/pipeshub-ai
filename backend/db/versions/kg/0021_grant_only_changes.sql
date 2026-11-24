-- The change log tells a change of a grant on a record or record group (a permission edge into it, whatever the principal) from every other change, so that
-- the projection can restamp such a node alone when whether it is granted stays the same (kg.project). A row's grant_only is true only when every key in it comes
-- from such edges; a statement that also changes other edges writes those keys in a row of their own. Older code reads no grant_only and restamps as before.
ALTER TABLE kg.graph_changes ADD COLUMN grant_only boolean NOT NULL DEFAULT false;

-- kg_0004's capture with grant_only on the rows of edge changes. Nodes are unchanged.
CREATE OR REPLACE FUNCTION kg.log_graph_change() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, kg, pg_temp SET enable_nestloop = off SET jit = off AS $fn$
BEGIN
  PERFORM set_config('kg.wrote', '1', true);       -- the unit wrote the graph: the commit hook reads this, and does not have to recognise the statement
  IF TG_TABLE_NAME = 'nodes' THEN
    IF TG_OP = 'INSERT' THEN
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT n.org_id, CASE n.coll WHEN 'apps' THEN n.id ELSE COALESCE(n.doc->>'connectorId', '') END, array_agg(kg.coll_code(n.coll) || ':' || n.id)
        FROM newtab n WHERE kg.coll_code(n.coll) IS NOT NULL GROUP BY 1, 2, length(n.id) > 256;
    ELSIF TG_OP = 'DELETE' THEN
      INSERT INTO kg.graph_changes (org_id, app_id, ends)
        SELECT o.org_id, CASE o.coll WHEN 'apps' THEN o.id ELSE COALESCE(o.doc->>'connectorId', '') END, array_agg(kg.coll_code(o.coll) || ':' || o.id)
        FROM oldtab o WHERE kg.coll_code(o.coll) IS NOT NULL GROUP BY 1, 2, length(o.id) > 256;
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

SELECT kernel.exempt_trigger('kg.log_graph_change()', 'inserts the keys of the changed nodes into kg.graph_changes as the writer of graph.*, in its transaction; reads nothing but its transition tables', 'security-review');
