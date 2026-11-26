-- retrieval_0006's triggers read a group's members of kinds 1-3 with `group_key = k AND kind IN (1, 2, 3)`. On an org's leaf that ANALYZE has
-- not seen yet (an org's first large load, before autovacuum reaches it), the planner puts the read at a row on every index of the org, and
-- took group_members_member (org, collection, kind, member_id, group_key): it walked the org's whole membership once per point written, 54 ms
-- a point at 2,771 members, where the primary key (org, collection, group_key, kind, member_id) reads the group's own rows (0.1 ms). The
-- plpgsql plan is cached, so the session kept it until the leaf was analysed. The read now asks for the primary key's order with the group as
-- a range of one value (an equality would make that order hold on any index of the org): only the primary key returns it without a sort, so
-- it wins the tie the missing statistics make, and it is the cheaper read once they exist.
CREATE OR REPLACE FUNCTION retrieval.points_member_keys() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, retrieval, pg_temp AS $fn$
BEGIN
  IF current_user = 'pipeshub_restore' THEN
    NEW.member_keys := NULL;
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND (NEW.org_id, NEW.collection_id, NEW.group_key) IS NOT DISTINCT FROM (OLD.org_id, OLD.collection_id, OLD.group_key) THEN
    RETURN NEW;
  END IF;
  PERFORM 1 FROM retrieval.point_groups g
    WHERE g.org_id = NEW.org_id AND g.collection_id = NEW.collection_id AND g.group_key = NEW.group_key FOR SHARE;
  NEW.member_keys := CASE WHEN FOUND THEN (
    SELECT coalesce(array_agg(s.kind || ':' || s.member_id ORDER BY s.kind, s.member_id), '{}') FROM (SELECT m.kind, m.member_id FROM retrieval.group_members m
     WHERE m.org_id = NEW.org_id AND m.collection_id = NEW.collection_id AND m.group_key >= NEW.group_key AND m.group_key <= NEW.group_key AND m.kind IN (1, 2, 3)
     ORDER BY m.group_key, m.kind, m.member_id) s) END;
  RETURN NEW;
END $fn$;

CREATE OR REPLACE FUNCTION retrieval.group_members_keys() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, retrieval, pg_temp AS $fn$
DECLARE v_org text[]; v_coll smallint[]; v_key text[];
BEGIN
  IF current_user = 'pipeshub_restore' THEN RETURN NULL; END IF;
  IF TG_OP = 'INSERT' THEN
    SELECT array_agg(a.org_id), array_agg(a.collection_id), array_agg(a.group_key) INTO v_org, v_coll, v_key
      FROM (SELECT DISTINCT n.org_id, n.collection_id, n.group_key FROM newtab n WHERE n.kind IN (1, 2, 3)) a;
  ELSIF TG_OP = 'DELETE' THEN
    SELECT array_agg(a.org_id), array_agg(a.collection_id), array_agg(a.group_key) INTO v_org, v_coll, v_key
      FROM (SELECT DISTINCT o.org_id, o.collection_id, o.group_key FROM oldtab o WHERE o.kind IN (1, 2, 3)) a;
  ELSE
    SELECT array_agg(a.org_id), array_agg(a.collection_id), array_agg(a.group_key) INTO v_org, v_coll, v_key
      FROM (SELECT n.org_id, n.collection_id, n.group_key FROM newtab n UNION SELECT o.org_id, o.collection_id, o.group_key FROM oldtab o) a;
  END IF;
  IF v_key IS NULL THEN RETURN NULL; END IF;
  PERFORM 1 FROM retrieval.point_groups g JOIN unnest(v_org, v_coll, v_key) AS a(o, c, k) ON g.org_id = a.o AND g.collection_id = a.c AND g.group_key = a.k
   ORDER BY g.org_id, g.collection_id, g.group_key FOR NO KEY UPDATE OF g;
  UPDATE retrieval.points p SET member_keys = k.keys
    FROM (SELECT a.o, a.c, a.k,
                 CASE WHEN EXISTS (SELECT 1 FROM retrieval.point_groups g WHERE g.org_id = a.o AND g.collection_id = a.c AND g.group_key = a.k)
                 THEN (SELECT coalesce(array_agg(s.kind || ':' || s.member_id ORDER BY s.kind, s.member_id), '{}') FROM (SELECT m.kind, m.member_id FROM retrieval.group_members m
                        WHERE m.org_id = a.o AND m.collection_id = a.c AND m.group_key >= a.k AND m.group_key <= a.k AND m.kind IN (1, 2, 3)
                        ORDER BY m.group_key, m.kind, m.member_id) s) END AS keys
            FROM unnest(v_org, v_coll, v_key) AS a(o, c, k)) k
   WHERE p.org_id = k.o AND p.collection_id = k.c AND p.group_key = k.k AND p.member_keys IS DISTINCT FROM k.keys;
  RETURN NULL;
END $fn$;

-- the reviewed exemptions hold a hash of each body: the same reasons, for the new bodies
SELECT kernel.exempt_trigger('retrieval.points_member_keys()', 'sets the membership keys of a point being written from its group''s group_members rows, '
  'holding the group row FOR SHARE, as the writer of the point, in its transaction', 'security-review');
SELECT kernel.exempt_trigger('retrieval.group_members_keys()', 'rewrites the membership keys of the points of the groups a statement changed in '
  'group_members, as the writer of the membership, in its transaction', 'security-review');
