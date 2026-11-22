-- The change capture and the grant it needs. The tables and the SQL helpers (kg.coll_code, kg.edge_ends, kg.edge_sem, kg.proj_doc) are in earlier revisions; the log is
-- written only from here on, because the drain that consumes and prunes it is in the same change.
-- The capture trigger runs as the role that writes graph.nodes / graph.edges, in its transaction. It inserts the organisation, the connector and the keys. The grant is on the
-- table, not on those columns: the kernel refuses column-level privileges (rls_violations 38). So that a service cannot choose the identifier or the transaction id of a row it
-- logs (a claimed range of future identifiers would make the log insert of a legitimate write fail with a unique violation), a BEFORE INSERT trigger of the invoker's own rights
-- sets both for the three services' roles: the transaction id from the transaction, the identifier from the table's own sequence, on which they hold USAGE.
SELECT kernel.require_vetted_org_policy('kg.graph_changes', ARRAY['insert']);       -- the insert is open: the trigger is run by the role that wrote the graph
GRANT INSERT ON kg.graph_changes TO pipeshub_connectors, pipeshub_indexing, pipeshub_query;
GRANT USAGE ON SEQUENCE kg.graph_changes_id_seq TO pipeshub_connectors, pipeshub_indexing, pipeshub_query;

-- A node id longer than 256 characters cannot be projected (the vetted writers take no more): its key goes in an array of its own, so the drain sets only that row aside and projects
-- the keys that were logged with it.
-- ---- change capture: nothing written to graph.nodes or graph.edges is missed ------------------------------------------------------------------
-- One row per statement and connector in kg.graph_changes, written by the statement-level triggers below as the role that made the change, in its transaction: the
-- keys of the changed nodes (and of the ends of a changed edge that its rows depend on, kg.edge_ends) as an array of 'code:id' strings. A writer pays for a handful
-- of rows per transaction, not one per node. A projection (the inline flush of the connectors service, or the drainer) re-projects the named nodes from the current graph
-- and deletes the rows (kg.w_changes_ack / kg.w_changes_delete); until then a reader of that connector takes today's walk.
-- app_id names the connector of a node row ('' for an edge row, and for the whole org) so the trigger needs no read of kg or of graph: the restore role has neither.
-- shortcut: one statement writes one array per org and connector, however many rows it changes (a restore of many millions of rows is one array), until a bulk loader needs chunks.
-- PostgreSQL rejects a statement trigger with transition tables on several events and a WHEN that reads OLD/NEW, so: three triggers per table and the filters here.
-- enable_nestloop is off for the body: a transition table has no statistics (the planner assumes one row), so the joins of oldtab and newtab were planned as nested loops and a
-- statement that updated 1,000 rows compared a million pairs (measured: +270 ms; the cost grew with the square of the rows). With it off they are hash and merge joins. A plan that
-- still needs a nested loop (the LATERAL over an edge's ends) then carries the planner's penalty for a disabled node, which is enough to start the JIT compiler (100 ms for one edge): jit is off.
CREATE FUNCTION kg.log_graph_change() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, kg, pg_temp SET enable_nestloop = off SET jit = off AS $fn$
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
      INSERT INTO kg.graph_changes (org_id, ends)
        SELECT e.org_id, array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id) FROM newtab e, LATERAL kg.edge_ends(e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, e.doc) x GROUP BY 1, length(x.id) > 256;
    ELSIF TG_OP = 'DELETE' THEN
      INSERT INTO kg.graph_changes (org_id, ends)
        SELECT e.org_id, array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id) FROM oldtab e, LATERAL kg.edge_ends(e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, e.doc) x GROUP BY 1, length(x.id) > 256;
    ELSE   -- a changed document: only when what the projection reads of it changed (the old and the new row name the nodes: a link can be retyped to or from a hierarchy relation)
      INSERT INTO kg.graph_changes (org_id, ends)
        SELECT e.org_id, array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id)
        FROM newtab e JOIN oldtab o ON o.org_id = e.org_id AND o.coll = e.coll AND o.from_coll = e.from_coll AND o.from_id = e.from_id
                                   AND o.to_coll = e.to_coll AND o.to_id = e.to_id AND o.ekey = e.ekey,
             LATERAL (SELECT * FROM kg.edge_ends(e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, e.doc)
                      UNION SELECT * FROM kg.edge_ends(o.coll, o.from_coll, o.from_id, o.to_coll, o.to_id, o.doc)) x
        WHERE kg.edge_sem(e.coll, o.doc) IS DISTINCT FROM kg.edge_sem(e.coll, e.doc) GROUP BY 1, length(x.id) > 256;
      -- an UPDATE of the key columns (from_id, to_id ...): the ends of the edge that went and of the edge that came
      INSERT INTO kg.graph_changes (org_id, ends)
        SELECT o.org_id, array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id)
        FROM oldtab o, LATERAL kg.edge_ends(o.coll, o.from_coll, o.from_id, o.to_coll, o.to_id, o.doc) x
        WHERE NOT EXISTS (SELECT 1 FROM newtab e WHERE e.org_id = o.org_id AND e.coll = o.coll AND e.from_coll = o.from_coll AND e.from_id = o.from_id
                            AND e.to_coll = o.to_coll AND e.to_id = o.to_id AND e.ekey = o.ekey) GROUP BY 1, length(x.id) > 256;
      INSERT INTO kg.graph_changes (org_id, ends)
        SELECT e.org_id, array_agg(DISTINCT kg.coll_code(x.coll) || ':' || x.id)
        FROM newtab e, LATERAL kg.edge_ends(e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, e.doc) x
        WHERE NOT EXISTS (SELECT 1 FROM oldtab o WHERE e.org_id = o.org_id AND e.coll = o.coll AND e.from_coll = o.from_coll AND e.from_id = o.from_id
                            AND e.to_coll = o.to_coll AND e.to_id = o.to_id AND e.ekey = o.ekey) GROUP BY 1, length(x.id) > 256;
    END IF;
  END IF;
  RETURN NULL;
END $fn$;

CREATE FUNCTION kg.refuse_truncate() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
BEGIN
  RAISE EXCEPTION 'truncating graph.nodes or graph.edges would leave kg stale' USING ERRCODE = '0A000';
END $fn$;

SELECT kernel.exempt_trigger('kg.log_graph_change()', 'inserts the keys of the changed nodes into kg.graph_changes as the writer of graph.*, in its transaction; reads nothing but its transition tables', 'security-review');

CREATE TRIGGER kg_nodes_insert AFTER INSERT ON graph.nodes REFERENCING NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION kg.log_graph_change();
CREATE TRIGGER kg_nodes_update AFTER UPDATE ON graph.nodes REFERENCING OLD TABLE AS oldtab NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION kg.log_graph_change();
CREATE TRIGGER kg_nodes_delete AFTER DELETE ON graph.nodes REFERENCING OLD TABLE AS oldtab FOR EACH STATEMENT EXECUTE FUNCTION kg.log_graph_change();
CREATE TRIGGER kg_edges_insert AFTER INSERT ON graph.edges REFERENCING NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION kg.log_graph_change();
CREATE TRIGGER kg_edges_update AFTER UPDATE ON graph.edges REFERENCING OLD TABLE AS oldtab NEW TABLE AS newtab FOR EACH STATEMENT EXECUTE FUNCTION kg.log_graph_change();
CREATE TRIGGER kg_edges_delete AFTER DELETE ON graph.edges REFERENCING OLD TABLE AS oldtab FOR EACH STATEMENT EXECUTE FUNCTION kg.log_graph_change();
CREATE TRIGGER kg_nodes_truncate BEFORE TRUNCATE ON graph.nodes FOR EACH STATEMENT EXECUTE FUNCTION kg.refuse_truncate();
CREATE TRIGGER kg_edges_truncate BEFORE TRUNCATE ON graph.edges FOR EACH STATEMENT EXECUTE FUNCTION kg.refuse_truncate();
-- the triggers fire under session_replication_role = replica too (a pg_restore --disable-triggers is refused by the kernel's restore path instead)
ALTER TABLE graph.nodes ENABLE ALWAYS TRIGGER kg_nodes_insert, ENABLE ALWAYS TRIGGER kg_nodes_update, ENABLE ALWAYS TRIGGER kg_nodes_delete, ENABLE ALWAYS TRIGGER kg_nodes_truncate;
ALTER TABLE graph.edges ENABLE ALWAYS TRIGGER kg_edges_insert, ENABLE ALWAYS TRIGGER kg_edges_update, ENABLE ALWAYS TRIGGER kg_edges_delete, ENABLE ALWAYS TRIGGER kg_edges_truncate;

-- Nothing a service writes into the log is its own choice: for its roles the identifier and the transaction id are the table's (the owner and the migrations may still set them).
CREATE FUNCTION kg.stamp_change() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
BEGIN
  IF current_user IN ('pipeshub_connectors', 'pipeshub_indexing', 'pipeshub_query') THEN
    NEW.id := nextval('kg.graph_changes_id_seq');
    NEW.txid := pg_current_xact_id();
  END IF;
  RETURN NEW;
END $fn$;
SELECT kernel.exempt_trigger('kg.stamp_change()', 'sets the identifier and the transaction id of a change-log row for the service roles; reads and writes nothing else', 'security-review');
CREATE TRIGGER kg_changes_stamp BEFORE INSERT ON kg.graph_changes FOR EACH ROW EXECUTE FUNCTION kg.stamp_change();
ALTER TABLE kg.graph_changes ENABLE ALWAYS TRIGGER kg_changes_stamp;
