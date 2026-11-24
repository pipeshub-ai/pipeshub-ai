-- The services' INSERT on kg.graph_changes is for the capture trigger alone (kg_0004). It runs as the role that writes the graph, so the privilege cannot be taken away, and a
-- definer trigger is not open to us: every definer function is a vetted function, and a vetted function may not write a table with pins unless it is pinned in one of the fixed
-- shapes (rules 15, 35, 114, 124). Here the stamp trigger tells the capture trigger's insert from a statement of the service's own: the capture trigger fires one level down, so
-- its insert runs at trigger depth 2, and a statement the service sends runs at depth 1. No service can create a trigger or a function, and every trigger that writes is reviewed
-- by its body hash (kernel.exempt_trigger), so depth 2 is reached only from reviewed code. The one row a service writes itself is the demotion request (the record key
-- '3:@demote' alone): it names no node, and its effect, a connector back on the walk, is what the notice on the same channel already asks for.
CREATE OR REPLACE FUNCTION kg.stamp_change() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
BEGIN
  IF current_user IN ('pipeshub_connectors', 'pipeshub_indexing', 'pipeshub_query') THEN
    IF pg_trigger_depth() < 2 AND NEW.ends IS DISTINCT FROM ARRAY['3:@demote'] THEN
      RAISE EXCEPTION 'kg.graph_changes is written by the capture triggers only' USING ERRCODE = '42501';
    END IF;
    NEW.id := nextval('kg.graph_changes_id_seq');
    NEW.txid := pg_current_xact_id();
  END IF;
  RETURN NEW;
END $fn$;
SELECT kernel.exempt_trigger('kg.stamp_change()', 'refuses a change-log row a service role inserts itself (only the capture trigger, one level down, and the demotion request get through) and sets the identifier and the transaction id of the rows it lets through; reads and writes nothing else', 'security-review');
