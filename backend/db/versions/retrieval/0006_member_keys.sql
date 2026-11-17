-- The membership arms of a point's group on the point row (member_keys: 'kind:member_id' of the group's group_members rows of kinds 1-3,
-- sorted), so the vector-index tier tests a candidate with an array overlap on the row it already holds instead of a group_members
-- probe per candidate (the probe was about 40 % of a member-scoped vector search).
-- It is kept by the database, whatever code writes: a point that is inserted, or moved to another group, takes its group's keys
-- (a row trigger), and a change to a group's membership rewrites the keys of the group's points that differ (a statement trigger).
-- The point trigger takes its group row FOR SHARE, which a membership writer's update of that row (FOR NO KEY UPDATE) waits for and
-- makes it wait: the keys a point gets are the membership of a writer that committed first, and a writer that commits later sees the
-- point and rewrites them. A point whose group has no row gets NULL, and so does every point written before this revision: a search
-- tests a NULL row with the group_members probe, so access stays exact while the keys are missing.
-- STORAGE MAIN: a point row with a 1024-dimension halfvec is past the 2 KB TOAST target, and the default would move the keys out of
-- line (four more buffers per candidate).
ALTER TABLE retrieval.points ADD COLUMN member_keys text[] STORAGE MAIN;

-- A restore loads the exported keys as they were (its role has no lock on the group rows), so these triggers leave it alone.
CREATE FUNCTION retrieval.points_member_keys() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, retrieval, pg_temp AS $fn$
BEGIN
  IF current_user = 'pipeshub_restore' OR (TG_OP = 'UPDATE' AND NEW.group_key IS NOT DISTINCT FROM OLD.group_key) THEN
    RETURN NEW;
  END IF;
  PERFORM 1 FROM retrieval.point_groups g
    WHERE g.org_id = NEW.org_id AND g.collection_id = NEW.collection_id AND g.group_key = NEW.group_key FOR SHARE;
  NEW.member_keys := CASE WHEN FOUND THEN (
    SELECT coalesce(array_agg(m.kind || ':' || m.member_id ORDER BY m.kind, m.member_id), '{}') FROM retrieval.group_members m
     WHERE m.org_id = NEW.org_id AND m.collection_id = NEW.collection_id AND m.group_key = NEW.group_key AND m.kind IN (1, 2, 3)) END;
  RETURN NEW;
END $fn$;

-- enable_nestloop is off: a transition table has no statistics (see kg_0004). The writers of group_members hold the group row (they
-- upsert point_groups first), so no point of the group is being given keys meanwhile.
CREATE FUNCTION retrieval.group_members_keys() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, retrieval, pg_temp SET enable_nestloop = off SET jit = off AS $fn$
BEGIN
  IF current_user = 'pipeshub_restore' THEN RETURN NULL; END IF;
  IF TG_OP = 'INSERT' THEN
    UPDATE retrieval.points p SET member_keys = k.keys
      FROM (SELECT g.org_id, g.collection_id, g.group_key,
                   (SELECT coalesce(array_agg(m.kind || ':' || m.member_id ORDER BY m.kind, m.member_id), '{}') FROM retrieval.group_members m
                     WHERE m.org_id = g.org_id AND m.collection_id = g.collection_id AND m.group_key = g.group_key AND m.kind IN (1, 2, 3)) AS keys
              FROM (SELECT DISTINCT n.org_id, n.collection_id, n.group_key FROM newtab n WHERE n.kind IN (1, 2, 3)) g) k
     WHERE p.org_id = k.org_id AND p.collection_id = k.collection_id AND p.group_key = k.group_key AND p.member_keys IS DISTINCT FROM k.keys;
  ELSIF TG_OP = 'DELETE' THEN
    UPDATE retrieval.points p SET member_keys = k.keys
      FROM (SELECT g.org_id, g.collection_id, g.group_key,
                   (SELECT coalesce(array_agg(m.kind || ':' || m.member_id ORDER BY m.kind, m.member_id), '{}') FROM retrieval.group_members m
                     WHERE m.org_id = g.org_id AND m.collection_id = g.collection_id AND m.group_key = g.group_key AND m.kind IN (1, 2, 3)) AS keys
              FROM (SELECT DISTINCT o.org_id, o.collection_id, o.group_key FROM oldtab o WHERE o.kind IN (1, 2, 3)) g) k
     WHERE p.org_id = k.org_id AND p.collection_id = k.collection_id AND p.group_key = k.group_key AND p.member_keys IS DISTINCT FROM k.keys;
  ELSE
    UPDATE retrieval.points p SET member_keys = k.keys
      FROM (SELECT g.org_id, g.collection_id, g.group_key,
                   (SELECT coalesce(array_agg(m.kind || ':' || m.member_id ORDER BY m.kind, m.member_id), '{}') FROM retrieval.group_members m
                     WHERE m.org_id = g.org_id AND m.collection_id = g.collection_id AND m.group_key = g.group_key AND m.kind IN (1, 2, 3)) AS keys
              FROM (SELECT n.org_id, n.collection_id, n.group_key FROM newtab n UNION SELECT o.org_id, o.collection_id, o.group_key FROM oldtab o) g) k
     WHERE p.org_id = k.org_id AND p.collection_id = k.collection_id AND p.group_key = k.group_key AND p.member_keys IS DISTINCT FROM k.keys;
  END IF;
  RETURN NULL;
END $fn$;
SELECT kernel.exempt_trigger('retrieval.points_member_keys()', 'sets the membership keys of a point being written from its group''s group_members rows, '
  'holding the group row FOR SHARE, as the writer of the point, in its transaction', 'security-review');
SELECT kernel.exempt_trigger('retrieval.group_members_keys()', 'rewrites the membership keys of the points of the groups a statement changed in '
  'group_members, as the writer of the membership, in its transaction', 'security-review');

CREATE TRIGGER points_member_keys BEFORE INSERT OR UPDATE OF group_key ON retrieval.points
  FOR EACH ROW EXECUTE FUNCTION retrieval.points_member_keys();
CREATE TRIGGER group_members_keys_insert AFTER INSERT ON retrieval.group_members REFERENCING NEW TABLE AS newtab
  FOR EACH STATEMENT EXECUTE FUNCTION retrieval.group_members_keys();
CREATE TRIGGER group_members_keys_delete AFTER DELETE ON retrieval.group_members REFERENCING OLD TABLE AS oldtab
  FOR EACH STATEMENT EXECUTE FUNCTION retrieval.group_members_keys();
CREATE TRIGGER group_members_keys_update AFTER UPDATE ON retrieval.group_members REFERENCING OLD TABLE AS oldtab NEW TABLE AS newtab
  FOR EACH STATEMENT EXECUTE FUNCTION retrieval.group_members_keys();
