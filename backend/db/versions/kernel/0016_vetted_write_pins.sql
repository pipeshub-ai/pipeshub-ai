-- Pinned vetted writes. A vetted write function may DELETE or use ON CONFLICT only where the kernel registry pins the exact
-- (function, relation, operation) triple, and then only in the fixed shapes below. Everything else stays as it was: checks 57 and 88
-- still refuse ON CONFLICT and DELETE in every function that has no pin for them.
--
--   delete   by the full primary key        DELETE FROM s.t t USING unnest(p_a, p_b) AS k(a, b) WHERE t.org_id = v_org AND t.a = k.a AND t.b = k.b
--                                           (or scalar arguments instead of unnest), the arrays capped at max_items <= 5000 (22023)
--   delete   of the current transaction     DELETE FROM s.t t WHERE t.org_id = v_org AND t.<xid8 column> = pg_current_xact_id()
--   upsert   on the primary key             INSERT INTO s.t (org_id, ...) SELECT v_org, ... FROM unnest(...) AS k(...) ON CONFLICT (<the key>)
--                                           DO NOTHING | DO UPDATE SET c = excluded.c, ...   (the pinned column list, nothing else)
--   insert, update                          as before, but the function may touch only what it pins
--
-- The org is `v_org`, read from app.org_id by the generated template; it is never an argument. The vetted role's row policy is
-- `USING (true)`, so these checks (and the generated foreign-org and no-org tests) are what keeps a function inside its org.
-- A table that has a pin for an operation has no direct privilege for it anywhere else (check 110).

CREATE TABLE kernel.vetted_write_pins (
  proc regprocedure NOT NULL,
  rel regclass NOT NULL,
  op text NOT NULL CHECK (op IN ('insert', 'upsert', 'update', 'delete')),
  key_cols name[],        -- delete by key: the primary key columns after org_id
  set_cols name[],        -- upsert: the DO UPDATE SET columns ({} = DO NOTHING)
  txn_col name,           -- delete of the current transaction: the xid8 column
  max_items int NOT NULL DEFAULT 5000 CHECK (max_items BETWEEN 1 AND 5000),
  PRIMARY KEY (proc, rel, op),
  CONSTRAINT vetted_write_pins_shape CHECK (CASE op
    WHEN 'delete' THEN (key_cols IS NULL) <> (txn_col IS NULL) AND set_cols IS NULL AND (key_cols IS NULL OR cardinality(key_cols) BETWEEN 1 AND 8)
    WHEN 'upsert' THEN set_cols IS NOT NULL AND key_cols IS NULL AND txn_col IS NULL
    ELSE key_cols IS NULL AND set_cols IS NULL AND txn_col IS NULL END));

CREATE FUNCTION kernel.allow_vetted_write_op(p_proc regprocedure, p_rel regclass, p_op text, p_key name[] DEFAULT NULL,
                                             p_set name[] DEFAULT NULL, p_txn name DEFAULT NULL, p_max_items int DEFAULT 5000) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
DECLARE s kernel.settings; g kernel.function_registry; r kernel.rls_registry; v_nsp name; v_vetted name; v_pk name[];
BEGIN
  SELECT * INTO s FROM kernel.settings;
  SELECT * INTO g FROM kernel.function_registry WHERE proc = p_proc;
  IF g IS NULL OR g.class <> 'write' OR g.scope <> 'org' THEN
    RAISE EXCEPTION 'allow_vetted_write_op: % is not a registered org-scoped write function', p_proc;
  END IF;
  SELECT * INTO r FROM kernel.rls_registry WHERE rel = p_rel;
  IF r IS NULL OR r.class <> 'org' OR NOT r.vetted_write OR EXISTS (SELECT 1 FROM pg_class c WHERE c.oid = p_rel AND c.relispartition) THEN
    RAISE EXCEPTION 'allow_vetted_write_op: % is not the root of a registered org table with vetted_write (kernel.apply_rls first)', p_rel;
  END IF;
  SELECT n.nspname INTO v_nsp FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = p_rel;
  v_vetted := s.vetted_prefix || v_nsp;
  IF NOT EXISTS (SELECT 1 FROM pg_proc x WHERE x.oid = p_proc::oid AND x.prosecdef AND x.proowner = to_regrole(v_vetted)) THEN
    RAISE EXCEPTION 'allow_vetted_write_op: % is not a SECURITY DEFINER function owned by %', p_proc, v_vetted;
  END IF;
  IF p_op NOT IN ('insert', 'upsert', 'update', 'delete') THEN RAISE EXCEPTION 'allow_vetted_write_op: unknown operation %', p_op; END IF;
  SELECT array_agg(a.attname ORDER BY k.ord) INTO v_pk
    FROM pg_index i CROSS JOIN LATERAL unnest(i.indkey::int2[]) WITH ORDINALITY k(attnum, ord)
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
    WHERE i.indrelid = p_rel AND i.indisprimary;
  IF v_pk IS NULL OR v_pk[1] <> 'org_id' THEN
    RAISE EXCEPTION 'allow_vetted_write_op: % needs a primary key led by org_id', p_rel;
  END IF;
  IF p_max_items NOT BETWEEN 1 AND 5000 THEN RAISE EXCEPTION 'allow_vetted_write_op: max_items is 1..5000'; END IF;

  IF p_op = 'delete' THEN
    IF (p_key IS NULL) = (p_txn IS NULL) OR p_set IS NOT NULL THEN
      RAISE EXCEPTION 'allow_vetted_write_op: a delete is pinned by its key columns or by its transaction column, one of them';
    END IF;
    IF p_key IS NOT NULL AND NOT (cardinality(p_key) = cardinality(v_pk) - 1 AND p_key @> v_pk[2:] AND p_key <@ v_pk[2:]) THEN
      RAISE EXCEPTION 'allow_vetted_write_op: a delete by key names the whole primary key after org_id (%), nothing else', v_pk[2:];
    END IF;
    IF p_txn IS NOT NULL AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = p_rel AND a.attname = p_txn AND a.atttypid = 'xid8'::regtype AND NOT a.attisdropped) THEN
      RAISE EXCEPTION 'allow_vetted_write_op: % has no xid8 column %', p_rel, p_txn;
    END IF;
    EXECUTE format('GRANT DELETE ON %s TO %I', p_rel, v_vetted);
  ELSIF p_op = 'upsert' THEN
    IF p_set IS NULL OR p_key IS NOT NULL OR p_txn IS NOT NULL THEN
      RAISE EXCEPTION 'allow_vetted_write_op: an upsert is pinned by its DO UPDATE SET columns (an empty list is DO NOTHING)';
    END IF;
    IF p_set && v_pk OR EXISTS (SELECT 1 FROM unnest(p_set) c WHERE NOT EXISTS (
         SELECT 1 FROM pg_attribute a WHERE a.attrelid = p_rel AND a.attname = c AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = '')) THEN
      RAISE EXCEPTION 'allow_vetted_write_op: an upsert sets columns of % that are not part of its primary key', p_rel;
    END IF;
  ELSIF p_key IS NOT NULL OR p_set IS NOT NULL OR p_txn IS NOT NULL THEN
    RAISE EXCEPTION 'allow_vetted_write_op: an % has no key, set or transaction column', p_op;
  END IF;

  INSERT INTO kernel.vetted_write_pins (proc, rel, op, key_cols, set_cols, txn_col, max_items) VALUES (p_proc, p_rel, p_op, p_key, p_set, p_txn, p_max_items)
    ON CONFLICT (proc, rel, op) DO UPDATE SET key_cols = excluded.key_cols, set_cols = excluded.set_cols, txn_col = excluded.txn_col, max_items = excluded.max_items;
END $fn$;
REVOKE ALL ON FUNCTION kernel.allow_vetted_write_op(regprocedure, regclass, text, name[], name[], name, int) FROM PUBLIC;
REVOKE ALL ON TABLE kernel.vetted_write_pins FROM PUBLIC;

-- The roles a pinned function may be written for (rule 125): the stamper is the connectors service. Written by a migration only.
CREATE TABLE kernel.pin_writers (role name PRIMARY KEY);
INSERT INTO kernel.pin_writers VALUES ('pipeshub_connectors');
REVOKE ALL ON TABLE kernel.pin_writers FROM PUBLIC;

-- The checks. Zero rows = pass. Folded into kernel.rls_violations below.
-- Every body is read after kernel.src_text (comments gone, string literals blanked); a pinned function is read statement by statement
-- (split at ';'), with exactly one DML keyword in a statement and none behind a WITH, so a keyword cannot hide inside another statement.
CREATE VIEW kernel.pin_violations WITH (security_invoker = true) AS
WITH RECURSIVE s AS (SELECT * FROM kernel.settings),
nsp AS (SELECT n.oid, n.nspname FROM pg_namespace n
  WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') AND n.nspname NOT LIKE 'pg_temp%' AND n.nspname NOT LIKE 'pg_toast_temp%'),
trusted AS (SELECT r.oid FROM pg_roles r, s WHERE r.rolname IN (s.owner, s.migrate, s.backup, s.restore) OR r.rolname LIKE s.vetted_prefix || '%'),
admin_roles AS (SELECT r.oid FROM pg_roles r, s WHERE r.rolname = ANY (s.admins)),
rall AS (SELECT r.oid FROM pg_roles r WHERE NOT r.rolsuper AND r.rolname NOT LIKE 'pg\_%'),
actors AS (SELECT oid FROM rall WHERE oid NOT IN (SELECT oid FROM trusted) AND oid NOT IN (SELECT oid FROM admin_roles)),
reach AS (
  WITH RECURSIVE t(actor, role) AS (
    SELECT a.oid, a.oid FROM rall a UNION SELECT t.actor, m.roleid FROM t JOIN pg_auth_members m ON m.member = t.role)
  SELECT * FROM t),
reach_roles AS (SELECT DISTINCT role AS oid FROM reach WHERE actor IN (SELECT oid FROM actors)),
pk AS (  -- the primary key columns of each pinned relation, in key order
  SELECT i.indrelid AS rel, array_agg(a.attname ORDER BY k.ord) AS cols
  FROM pg_index i CROSS JOIN LATERAL unnest(i.indkey::int2[]) WITH ORDINALITY k(attnum, ord)
  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
  WHERE i.indisprimary AND i.indrelid IN (SELECT rel FROM kernel.vetted_write_pins) GROUP BY i.indrelid),
pins AS (
  SELECT p.proc AS sig, p.proc::oid AS fn, p.rel, p.op, p.key_cols, p.set_cols, p.txn_col, p.max_items,
         COALESCE(lower(n.nspname || '.' || c.relname), p.rel::text) AS qrel, n.nspname AS rel_nsp, c.relname, pk.cols AS pk_cols
  FROM kernel.vetted_write_pins p LEFT JOIN pg_class c ON c.oid = p.rel LEFT JOIN pg_namespace n ON n.oid = c.relnamespace LEFT JOIN pk ON pk.rel = p.rel),
pinrel AS (SELECT p.rel AS oid FROM pins p UNION SELECT t.relid FROM pins p, LATERAL pg_partition_tree(p.rel) t),
vroles AS (SELECT r.oid FROM pg_roles r, s WHERE r.rolname LIKE s.vetted_prefix || '%'),
rtn_all AS (  -- every SQL or PL/pgSQL routine outside the system schemas and extensions, definer or invoker
  SELECT p.oid, p.oid::regprocedure::text AS name, p.proname, p.proowner, p.prosqlbody IS NOT NULL AS atomic_body, kernel.src_text(p.prosrc) AS src, kernel.src_text(p.prosrc, true) AS srck
  FROM pg_proc p JOIN nsp n ON n.oid = p.pronamespace JOIN pg_language l ON l.oid = p.prolang AND l.lanname IN ('sql', 'plpgsql')
  WHERE NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')),
pinned_rowtypes AS (SELECT t.oid FROM pg_type t WHERE t.typrelid IN (SELECT oid FROM pinrel)),
catalog_runs AS (  -- routines the server runs by itself for a statement on a pinned table or over a value: no name stands in any function text. Read only while a pin exists.
  SELECT tg.tgfoid::oid AS oid FROM pg_trigger tg WHERE tg.tgrelid IN (SELECT oid FROM pinrel) AND NOT tg.tgisinternal          -- a trigger on a pinned table or a partition
  UNION SELECT d.refobjid FROM pg_attrdef ad JOIN pg_depend d ON d.classid = 'pg_attrdef'::regclass AND d.objid = ad.oid AND d.refclassid = 'pg_proc'::regclass
    WHERE ad.adrelid IN (SELECT oid FROM pinrel)                                                                           -- a column default or generation expression
  UNION SELECT d.refobjid FROM pg_constraint k JOIN pg_depend d ON d.classid = 'pg_constraint'::regclass AND d.objid = k.oid AND d.refclassid = 'pg_proc'::regclass
    WHERE k.conrelid IN (SELECT oid FROM pinrel)                                                                           -- a CHECK constraint of a pinned table
  UNION SELECT d.refobjid FROM pg_index i JOIN pg_depend d ON d.classid = 'pg_class'::regclass AND d.objid = i.indexrelid AND d.refclassid = 'pg_proc'::regclass
    WHERE i.indrelid IN (SELECT oid FROM pinrel)                                                                           -- an index expression
  UNION SELECT p.oid FROM pg_proc p WHERE p.proargtypes::oid[] && (SELECT COALESCE(array_agg(oid), '{}') FROM pinned_rowtypes)  -- a function of a pinned table's row type: (t).f
  UNION SELECT o.oprcode::oid FROM pg_operator o WHERE EXISTS (SELECT 1 FROM pins) AND o.oprnamespace IN (SELECT oid FROM nsp)       -- an operator of the install
  UNION SELECT c.castfunc FROM pg_cast c WHERE EXISTS (SELECT 1 FROM pins) AND c.castfunc <> 0                                    -- a cast
  UNION SELECT d.refobjid FROM pg_constraint k JOIN pg_depend d ON d.classid = 'pg_constraint'::regclass AND d.objid = k.oid AND d.refclassid = 'pg_proc'::regclass
    WHERE EXISTS (SELECT 1 FROM pins) AND k.contypid <> 0),                                                                -- a domain's CHECK
scoped(oid) AS (  -- what a vetted role can run: the routines it owns, the routines the server runs for a pinned table, and every routine called by name from those, however deep. A helper nothing vetted reaches is not read
  SELECT r.oid FROM rtn_all r WHERE r.proowner IN (SELECT oid FROM vroles) OR r.oid IN (SELECT oid FROM catalog_runs)
  UNION
  SELECT a.oid FROM scoped sc JOIN rtn_all c ON c.oid = sc.oid JOIN rtn_all a ON a.oid <> c.oid AND c.src ~* ('\m' || regexp_replace(a.proname, '(\W)', '\\\1', 'g') || ' *\(')),
rtn AS (SELECT r.* FROM rtn_all r WHERE r.oid IN (SELECT oid FROM scoped)),
defs AS (  -- every SECURITY DEFINER function, text and all
  SELECT p.oid, p.oid::regprocedure::text AS name, p.proname, n.nspname, p.prosrc, p.prosqlbody IS NOT NULL AS atomic_body, kernel.src_text(p.prosrc) AS src,
         p.oid IN (SELECT fn FROM pins) AS is_pinned, p.oid IN (SELECT oid FROM scoped) AS in_scope
  FROM pg_proc p JOIN nsp n ON n.oid = p.pronamespace WHERE p.prosecdef),
fn AS (  -- the pinned functions
  SELECT p.oid, p.oid::regprocedure::text AS name, p.proowner, p.prosecdef, n.nspname, kernel.src_text(p.prosrc) AS src,
         p.proargnames, p.proargtypes::oid[] AS argtypes, g.class, g.scope, g.writer, COALESCE(p.proacl, acldefault('f', p.proowner)) AS acl
  FROM pg_proc p JOIN nsp n ON n.oid = p.pronamespace LEFT JOIN kernel.function_registry g ON g.proc = p.oid
  WHERE p.oid IN (SELECT fn FROM pins)),
piece AS (  -- a pinned function, one statement at a time
  SELECT f.oid AS fn, f.name, f.nspname, btrim(s.t) AS st FROM fn f, LATERAL regexp_split_to_table(f.src, ';') s(t)),
piece_n AS (
  SELECT p.*, (SELECT count(*) FROM regexp_matches(p.st, '\m(?:insert into|delete from)\M|\mupdate [a-z_][a-z0-9_.]*(?: as)?(?: [a-z_][a-z0-9_]*)? set\M', 'gi')) AS n FROM piece p),
ops AS (  -- the DML statement of a piece that has exactly one, from its keyword on
  SELECT d.fn, d.name, d.st AS whole, x.stmt AS stmt,
         CASE WHEN x.stmt ~* '^insert into' THEN CASE WHEN x.stmt ~* '\mon conflict\M' THEN 'upsert' ELSE 'insert' END
              WHEN x.stmt ~* '^delete from' THEN 'delete' ELSE 'update' END AS op,
         CASE WHEN t.t LIKE '%.%' THEN t.t ELSE lower(d.nspname) || '.' || t.t END AS tgt
  FROM piece_n d,
       LATERAL (SELECT btrim((regexp_match(d.st, '(\m(?:insert into|delete from)\M.*|\mupdate [a-z_][a-z0-9_.]*(?: as)?(?: [a-z_][a-z0-9_]*)? set\M.*)$', 'i'))[1]) AS stmt) x,
       LATERAL (SELECT lower((regexp_match(x.stmt, '^(?:insert into|delete from|update) ([a-z_][a-z0-9_.]*)', 'i'))[1]) AS t) t
  WHERE d.n = 1),
-- every definer function without a pin: every DML keyword and TRUNCATE with its target, wherever it stands in the body
others AS (
  SELECT d.oid AS fn, d.name,
         CASE WHEN lower(m.g[1]) LIKE '%.%' THEN lower(m.g[1]) ELSE lower(d.nspname) || '.' || lower(m.g[1]) END AS tgt
  FROM defs d, LATERAL regexp_matches(d.src, '\m(?:insert into|delete from|update|truncate(?: table)?|merge into)(?: only)? ([a-z_][a-z0-9_.]*)', 'gi') m(g)
  WHERE NOT d.is_pinned AND EXISTS (SELECT 1 FROM kernel.vetted_write_pins)),
-- ---- deletes: the full primary key from capped arguments, or the current transaction ----
dels AS (
  SELECT o.fn, o.name, p.sig, p.key_cols, p.txn_col, p.max_items, o.stmt,
    regexp_match(o.stmt, '^delete from [a-z_][a-z0-9_.]*(?: as)? ([a-z_][a-z0-9_]*) using unnest\(([a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*)\) as ([a-z_][a-z0-9_]*)\(([a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*)\) where \1\.org_id = v_org((?: and \1\.[a-z_][a-z0-9_]* = \3\.[a-z_][a-z0-9_]*)+)$', 'i') AS ra,
    regexp_match(o.stmt, '^delete from [a-z_][a-z0-9_.]*(?: as)? ([a-z_][a-z0-9_]*) where \1\.org_id = v_org((?: and \1\.[a-z_][a-z0-9_]* = [a-z_][a-z0-9_]*)+)$', 'i') AS rs,
    regexp_match(o.stmt, '^delete from [a-z_][a-z0-9_.]*(?: as)? ([a-z_][a-z0-9_]*) where \1\.org_id = v_org and \1\.([a-z_][a-z0-9_]*) = pg_current_xact_id\(\)$', 'i') AS rt
  FROM ops o JOIN pins p ON p.fn = o.fn AND p.qrel = o.tgt AND p.op = 'delete' WHERE o.op = 'delete'),
dels2 AS (
  SELECT d.*,
    (SELECT array_agg(lower(m[1]) ORDER BY lower(m[1])) FROM regexp_matches(COALESCE(d.ra[5], d.rs[2], ''), ' and [a-z_][a-z0-9_]*\.([a-z_][a-z0-9_]*) = ', 'gi') m) AS lefts,
    (SELECT array_agg(lower(m[1]) ORDER BY lower(m[1])) FROM regexp_matches(COALESCE(d.ra[5], ''), ' = [a-z_][a-z0-9_]*\.([a-z_][a-z0-9_]*)', 'gi') m) AS rights,
    (SELECT array_agg(lower(c) ORDER BY lower(c)) FROM unnest(string_to_array(d.ra[4], ', ')) c) AS kcols,
    string_to_array(d.ra[2], ', ') AS arr_args,
    (SELECT array_agg(m[1]) FROM regexp_matches(COALESCE(d.rs[2], ''), ' = ([a-z_][a-z0-9_]*)', 'gi') m) AS scalar_args
  FROM dels d),
-- ---- upserts: the primary key as the conflict target, the pinned columns, the org from v_org ----
ups AS (
  SELECT o.fn, o.name, p.sig, p.set_cols, p.pk_cols, p.max_items, o.stmt,
    regexp_match(o.stmt, '^(.*?) on conflict \(([^)]*)\) do (nothing|update set (.*?))(?: returning (.*))?$', 'i') AS m
  FROM ops o JOIN pins p ON p.fn = o.fn AND p.qrel = o.tgt AND p.op = 'upsert' WHERE o.op = 'upsert'),
ups2 AS (
  SELECT u.*,
    regexp_match(u.m[1], '^insert into [a-z_][a-z0-9_.]* \(org_id(?:, [a-z_][a-z0-9_]*)+\) select v_org((?:, (?:[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*|-?[0-9]+|true|false|null|''''))*) from unnest\(([a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*)\) as ([a-z_][a-z0-9_]*)\(([a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*)\)$', 'i') AS hu,
    regexp_match(u.m[1], '^insert into [a-z_][a-z0-9_.]* \(org_id(?:, [a-z_][a-z0-9_]*)+\) values \(v_org(?:, (?:[a-z_][a-z0-9_]*|-?[0-9]+|true|false|null|''''))*\)$', 'i') AS hv,
    (SELECT array_agg(lower(c) ORDER BY lower(c)) FROM unnest(string_to_array(u.m[2], ', ')) c) AS conflict_sorted,
    string_to_array(lower(u.m[2]), ', ') AS conflict_cols,
    (SELECT array_agg(lower(a[1]) ORDER BY lower(a[1])) FROM unnest(string_to_array(u.m[4], ', ')) e, LATERAL regexp_match(e, '^([a-z_][a-z0-9_]*) = excluded\.\1$', 'i') a) AS set_found,
    cardinality(string_to_array(u.m[4], ', ')) AS set_listed,
    string_to_array(u.m[5], ', ') AS ret_cols
  FROM ups u),
arrays AS (  -- the arrays an array-form statement unnests, with the pin's cap
  SELECT d.fn, d.name, a.arg, d.max_items FROM dels2 d, LATERAL unnest(d.arr_args) a(arg) WHERE d.ra IS NOT NULL
  UNION ALL
  SELECT u.fn, u.name, a.arg, u.max_items FROM ups2 u, LATERAL unnest(string_to_array(u.hu[2], ', ')) a(arg) WHERE u.hu IS NOT NULL),
priv AS (
  SELECT p.rel, bool_or(p.op IN ('insert', 'upsert')) AS ins, bool_or(p.op IN ('update', 'upsert')) AS upd, bool_or(p.op = 'delete') AS del
  FROM kernel.vetted_write_pins p GROUP BY p.rel),
tree AS (SELECT pr.rel AS root, pr.rel AS oid FROM priv pr UNION SELECT pr.rel, t.relid FROM priv pr, LATERAL pg_partition_tree(pr.rel) t),
grants AS (
  SELECT t.root, c.oid, a.privilege_type, a.grantee, NULL::name AS col FROM tree t JOIN pg_class c ON c.oid = t.oid, LATERAL aclexplode(c.relacl) a
  UNION ALL
  SELECT t.root, at.attrelid, a.privilege_type, a.grantee, at.attname FROM tree t JOIN pg_attribute at ON at.attrelid = t.oid AND at.attacl IS NOT NULL AND NOT at.attisdropped,
    LATERAL aclexplode(at.attacl) a),
vdep AS (  -- views (and views over views) that read or write a pinned relation
  SELECT w.ev_class AS view, d.refobjid AS base FROM pg_depend d JOIN pg_rewrite w ON w.oid = d.objid
   WHERE d.classid = 'pg_rewrite'::regclass AND d.refclassid = 'pg_class'::regclass AND d.refobjid IN (SELECT oid FROM pinrel) AND w.ev_class <> d.refobjid
  UNION
  SELECT w.ev_class, v.base FROM vdep v JOIN pg_depend d ON d.refobjid = v.view AND d.classid = 'pg_rewrite'::regclass AND d.refclassid = 'pg_class'::regclass
    JOIN pg_rewrite w ON w.oid = d.objid WHERE w.ev_class <> v.view),
-- what remains of a pinned function's text once the only allowed uses of its org variable are taken out
orgvar AS (
  SELECT f.oid AS fn, f.name,
    regexp_replace(regexp_replace(regexp_replace(regexp_replace(f.src,
      'v_org text := NULLIF\(current_setting\( '''', true\), ''''\)', ' ', 'gi'),
      '\mv_org IS NULL\M', ' ', 'gi'),
      '\morg_id = v_org\M', ' ', 'gi'),
      '\m(select|values \() ?v_org,', ' ', 'gi') AS rest,
    (SELECT count(*) FROM regexp_matches(f.src, '\mv_org text\M', 'gi')) AS declarations
  FROM fn f),
cat AS (
SELECT '100 pin names a function that no longer exists' AS rule, p.sig::text AS object FROM pins p WHERE NOT EXISTS (SELECT 1 FROM pg_proc x WHERE x.oid = p.fn)
UNION ALL
SELECT '101 pinned function is not a registered SECURITY DEFINER org-scoped write function', f.name FROM fn f
  WHERE NOT f.prosecdef OR f.class IS DISTINCT FROM 'write' OR f.scope IS DISTINCT FROM 'org'
UNION ALL
SELECT '102 pinned function is not owned by the vetted role of the pinned table''s schema', p.sig::text FROM pins p JOIN fn f ON f.oid = p.fn, s
  WHERE f.proowner IS DISTINCT FROM to_regrole(s.vetted_prefix || p.rel_nsp)
UNION ALL
SELECT '103 pinned relation is not the root of a registered org table with vetted_write', p.qrel FROM pins p
  WHERE NOT EXISTS (SELECT 1 FROM kernel.rls_registry g WHERE g.rel = p.rel AND g.class = 'org' AND g.vetted_write)
     OR EXISTS (SELECT 1 FROM pg_class c WHERE c.oid = p.rel AND c.relispartition)
UNION ALL
SELECT '104 pin does not match the table''s primary key or columns', p.sig::text || ' -> ' || p.qrel FROM pins p
  WHERE (p.op IN ('delete', 'upsert') AND (p.pk_cols IS NULL OR p.pk_cols[1] IS DISTINCT FROM 'org_id'))
     OR (p.key_cols IS NOT NULL AND NOT (cardinality(p.key_cols) = cardinality(p.pk_cols) - 1 AND p.key_cols @> p.pk_cols[2:] AND p.key_cols <@ p.pk_cols[2:]))
     OR (p.set_cols IS NOT NULL AND p.set_cols && p.pk_cols)
     OR (p.txn_col IS NOT NULL AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = p.rel AND a.attname = p.txn_col AND a.atttypid = 'xid8'::regtype AND NOT a.attisdropped))
UNION ALL -- 105 a pinned function touches what it pins, and pins what it touches, one DML keyword to a statement
SELECT '105 pinned function runs ' || o.op || ' on ' || COALESCE(o.tgt, '?') || ', which it does not pin', o.name FROM ops o
  WHERE NOT EXISTS (SELECT 1 FROM pins p WHERE p.fn = o.fn AND p.qrel = o.tgt AND p.op = o.op)
UNION ALL
SELECT '105 pin names an operation the function never performs: ' || p.op || ' on ' || p.qrel, p.sig::text FROM pins p JOIN fn f ON f.oid = p.fn
  WHERE NOT EXISTS (SELECT 1 FROM ops o WHERE o.fn = p.fn AND o.tgt = p.qrel AND o.op = p.op)
UNION ALL
SELECT '105 pinned function truncates', f.name FROM fn f WHERE f.src ~* '\mtruncate\M'
UNION ALL -- MERGE (any WHEN action) and UPDATE / DELETE FROM ONLY are statements the shapes below do not read
SELECT '105 pinned function uses MERGE or ONLY, which no pinned shape contains', f.name FROM fn f WHERE f.src ~* '\m(merge|only)\M'
UNION ALL
SELECT '105 a statement of a pinned function has more than one DML keyword, or a WITH in front of one', p.name FROM piece_n p
  WHERE p.n > 1 OR (p.n = 1 AND p.st ~* '\mwith\M.*\m(insert into|delete from|update [a-z_][a-z0-9_.]*(?: as)?(?: [a-z_][a-z0-9_]*)? set)\M')
UNION ALL -- 106 a delete is the full primary key (or the current transaction) of the caller's org, and nothing else
SELECT '106 delete is not an equality on the whole primary key (or on the current transaction) of the caller''s org', d.name FROM dels2 d
  WHERE NOT (
    (d.key_cols IS NOT NULL AND d.ra IS NOT NULL AND d.lefts = (SELECT array_agg(lower(c) ORDER BY lower(c)) FROM unnest(d.key_cols) c)
       AND cardinality(d.lefts) = cardinality(d.key_cols) AND d.rights = d.kcols AND cardinality(d.rights) = cardinality(d.kcols)
       AND cardinality(d.arr_args) = cardinality(d.kcols) AND (SELECT count(DISTINCT lower(c)) FROM unnest(d.arr_args) c) = cardinality(d.arr_args))
    OR (d.key_cols IS NOT NULL AND d.rs IS NOT NULL AND d.lefts = (SELECT array_agg(lower(c) ORDER BY lower(c)) FROM unnest(d.key_cols) c)
       AND cardinality(d.lefts) = cardinality(d.key_cols))
    OR (d.txn_col IS NOT NULL AND d.rt IS NOT NULL AND lower(d.rt[2]) = lower(d.txn_col)))
UNION ALL -- 107 an upsert conflicts on the primary key, sets the pinned columns, writes the caller's org
SELECT '107 upsert is not an INSERT of v_org on the primary key with the pinned DO UPDATE SET list', u.name FROM ups2 u
  WHERE NOT (
    u.m IS NOT NULL AND (u.hu IS NOT NULL OR u.hv IS NOT NULL)
    AND u.conflict_cols = (SELECT array_agg(lower(c) ORDER BY ord) FROM unnest(u.pk_cols) WITH ORDINALITY x(c, ord))
    AND (lower(u.m[3]) = 'nothing') = (cardinality(u.set_cols) = 0)
    AND (lower(u.m[3]) = 'nothing' OR (u.set_listed = cardinality(u.set_cols) AND u.set_found = (SELECT array_agg(lower(c) ORDER BY lower(c)) FROM unnest(u.set_cols) c)))
    AND (u.m[5] IS NULL OR NOT EXISTS (SELECT 1 FROM unnest(string_to_array(u.m[5], ', ')) rc WHERE lower(rc) <> ALL (u.pk_cols::text[])))
    AND (u.hu IS NULL OR (SELECT bool_and(lower(m[1]) = lower(u.hu[3])) FROM regexp_matches(u.hu[1], '([a-z_][a-z0-9_]*)\.', 'gi') m) IS NOT FALSE))
UNION ALL -- 108 the arrays a delete or an upsert unnests are arguments of the function, capped before any DML by a guard that raises
SELECT '108 unnested array ' || a.arg || ' is not an array argument capped by IF cardinality() > N THEN RAISE EXCEPTION before the first DML', a.name FROM arrays a JOIN fn f ON f.oid = a.fn
  WHERE NOT (a.arg = ANY (f.proargnames))
     OR NOT EXISTS (SELECT 1 FROM unnest(f.argtypes) WITH ORDINALITY t(typ, ord) JOIN pg_type ty ON ty.oid = t.typ WHERE t.ord = array_position(f.proargnames, a.arg) AND ty.typcategory = 'A')
     OR NOT EXISTS (SELECT 1 FROM regexp_matches(f.src, '(\mif cardinality\(' || a.arg || '\) > ([0-9]{1,4}) then raise exception\M)', 'gi') m
                    WHERE m[2]::int <= a.max_items AND strpos(lower(f.src), lower(m[1])) < COALESCE(
                      least(NULLIF(strpos(lower(f.src), 'insert into'), 0), NULLIF(strpos(lower(f.src), 'delete from'), 0), NULLIF(strpos(lower(f.src), 'update '), 0)), 2147483647))
UNION ALL
SELECT '109 scalar key ' || sa || ' of a delete is not a scalar argument of the function', d.name FROM dels2 d, LATERAL unnest(d.scalar_args) sa, fn f
  WHERE f.oid = d.fn AND d.rs IS NOT NULL
    AND (NOT (sa = ANY (f.proargnames)) OR EXISTS (SELECT 1 FROM unnest(f.argtypes) WITH ORDINALITY t(typ, ord) JOIN pg_type ty ON ty.oid = t.typ
                                                  WHERE t.ord = array_position(f.proargnames, sa) AND ty.typcategory = 'A'))
UNION ALL -- 110 nobody else holds the privilege a pin takes over: not the app group, not a service role, not PUBLIC, on the table, a partition or a column
SELECT '110 ' || CASE WHEN g.grantee = 0 THEN 'PUBLIC' ELSE g.grantee::regrole::text END || ' holds ' || g.privilege_type || COALESCE(' on column ' || g.col, '')
       || ' on a table that only its vetted functions may write', g.oid::regclass::text
  FROM grants g JOIN priv pr ON pr.rel = g.root
  WHERE (g.grantee = 0 OR g.grantee IN (SELECT oid FROM reach_roles))
    AND ((g.privilege_type = 'INSERT' AND pr.ins) OR (g.privilege_type = 'UPDATE' AND pr.upd) OR (g.privilege_type = 'DELETE' AND pr.del))
UNION ALL -- 111 a definer body the DML detectors cannot be sure to read as PostgreSQL does: CR, form feed, vertical tab, a $ or a non-ASCII character outside a string
SELECT '111 SECURITY DEFINER body has a CR, form feed, vertical tab, $ or non-ASCII character outside a string or a comment', d.name FROM defs d
  WHERE d.in_scope AND (d.prosrc ~ E'[\\r\\f\\v]' OR regexp_replace(d.src, '(?<![\w$])\$[0-9]+', ' ', 'g') ~ E'\\$|[^\\x01-\\x7f]')
UNION ALL -- 112 a SQL-standard body (BEGIN ATOMIC, or RETURN <expression>) is stored as a parse tree: prosrc is empty and no text check can read it; a pinned function reaches an invoker routine too
SELECT '112 routine with a SQL-standard body (BEGIN ATOMIC or RETURN expression)', r.name FROM rtn r WHERE r.atomic_body
UNION ALL -- 130 a routine may not start a statement that no text check reads: DO (its code is a string), SET and RESET (they change app.org_id, search_path, role), PREPARE, DDL and the rest
SELECT '130 routine starts a statement the text checks do not read (DO, SET, RESET, PREPARE, DDL, GRANT, COPY ...)', r.name FROM rtn r
  WHERE r.src ~* '(^|;|>>|\mbegin|\mthen|\melse|\mloop|\mexception) *(do|set|reset|prepare|load|listen|notify|discard|create|alter|drop|grant|revoke|copy|import|refresh|reindex|vacuum|analyze|cluster|comment|lock|checkpoint)\M'
UNION ALL -- 131 no routine changes a setting but lock_timeout: set_config('app.org_id', ...) would change the org every policy keys on
SELECT '131 routine calls set_config for anything but lock_timeout', r.name FROM rtn r
  WHERE (SELECT count(*) FROM regexp_matches(r.srck, '\mset_config\M', 'gi')) <> (SELECT count(*) FROM regexp_matches(r.srck, '\mset_config *\( *''lock_timeout''', 'gi'))
UNION ALL -- 113 only the declared writer (and the owner) may execute a pinned function
SELECT '113 pinned function is executable by ' || CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END, f.name
  FROM fn f, LATERAL aclexplode(f.acl) a
  WHERE a.privilege_type = 'EXECUTE' AND a.grantee <> f.proowner AND a.grantee IS DISTINCT FROM to_regrole(f.writer)::oid
UNION ALL -- 114 a table with a pin is written by pinned functions only: no other definer function of the install names it as a DML or TRUNCATE target
SELECT '114 function without a pin writes ' || o.tgt || ', which has pins', o.name FROM others o WHERE o.tgt IN (SELECT qrel FROM pins)
UNION ALL -- 115 the only vetted role with a privilege on a pinned table is the owner of its schema (rule 89 is not applied to a table with a delete pin)
SELECT '115 vetted role ' || g.grantee::regrole::text || ' holds ' || g.privilege_type || ' on a table that belongs to another schema', g.oid::regclass::text
  FROM grants g JOIN (SELECT DISTINCT rel, rel_nsp FROM pins) p ON p.rel = g.root, s
  WHERE g.privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE') AND g.grantee IN (SELECT r.oid FROM pg_roles r WHERE r.rolname LIKE s.vetted_prefix || '%') AND g.grantee IS DISTINCT FROM to_regrole(s.vetted_prefix || p.rel_nsp)::oid
UNION ALL -- 116 a pinned function reads the org once, from the template's declaration, uses it only as org_id = v_org / VALUES (v_org, / SELECT v_org, and changes no setting
SELECT '116 pinned function sets a configuration value or reads settings more than the org once', f.name FROM fn f
  WHERE f.src ~* '\mset_config\M|\mset (?:local|session)\M|\mreset\M' OR (SELECT count(*) FROM regexp_matches(f.src, 'current_setting\s*\(', 'gi')) <> 1
UNION ALL
SELECT '116 pinned function uses the org variable for anything but org_id = v_org, VALUES (v_org, ... or SELECT v_org, ... (assignment, SELECT INTO, a second declaration)', o.name FROM orgvar o
  WHERE o.rest ~* '\mv_org\M' OR o.declarations <> 1
UNION ALL -- 117 a plain insert is one row of v_org and arguments; a plain update sets arguments and constants where the org predicate comes first, nothing else
SELECT '117 ' || o.op || ' of a pinned function is not the plain org-bound shape', o.name FROM ops o
  WHERE (o.op = 'insert' AND o.stmt !~* '^insert into [a-z_][a-z0-9_.]* \(org_id(?:, [a-z_][a-z0-9_]*)+\) (?:values \(v_org(?:, (?:[a-z_][a-z0-9_]*|-?[0-9]+|true|false|null|''''))*\)|select v_org(?:, (?:[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*|-?[0-9]+|true|false|null|''''))* from unnest\([a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*\) as [a-z_][a-z0-9_]*\([a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*\))$')
     OR (o.op = 'update' AND o.stmt !~* '^update [a-z_][a-z0-9_.]*(?: as)? ([a-z_][a-z0-9_]*) set (?!org_id\M)[a-z_][a-z0-9_]* = (?:[a-z_][a-z0-9_]*|-?[0-9]+|true|false|null|'''')(?:, (?!org_id\M)[a-z_][a-z0-9_]* = (?:[a-z_][a-z0-9_]*|-?[0-9]+|true|false|null|''''))* where \1\.org_id = v_org(?: and \1\.[a-z_][a-z0-9_]* = (?:[a-z_][a-z0-9_]*|-?[0-9]+|true|false|null|''''))*$')
UNION ALL -- 120 a function other than the pinned one names the pinned function: a wrapper another role can run would write the table
SELECT '120 function ' || f.name || ' names the pinned function ' || p.name, f.name FROM defs f, defs p
  WHERE p.is_pinned AND f.oid <> p.oid AND NOT f.is_pinned AND f.src ~* ('\m' || regexp_replace(p.proname, '(\W)', '\\\1', 'g') || '\M')
UNION ALL -- 121 a view over a pinned relation: it can be written through, and rule 114 reads table names
SELECT '121 view over a pinned relation', v.view::regclass::text FROM vdep v
UNION ALL -- 122 an inheritance child (not a partition) of a pinned relation: a role that may write the child changes what the parent shows
SELECT '122 inheritance child of a pinned relation', i.inhrelid::regclass::text FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
  WHERE i.inhparent IN (SELECT oid FROM pinrel) AND NOT c.relispartition
UNION ALL -- 123 a foreign key from a pinned relation whose action rewrites or deletes the referenced rows' dependants: RI actions run as the owner, outside any policy
SELECT '123 foreign key that cascades from or into a pinned relation', k.conrelid::regclass::text || ' -> ' || k.confrelid::regclass::text
  FROM pg_constraint k WHERE k.contype = 'f' AND (k.conrelid IN (SELECT oid FROM pinrel) OR k.confrelid IN (SELECT oid FROM pinrel))
    AND (k.confdeltype IN ('c', 'n', 'd') OR k.confupdtype IN ('c', 'n', 'd'))
UNION ALL -- 132 a pinned function calls pinned functions and vetted kernel routines only: any other routine would run its statements outside the shapes
SELECT '132 pinned function calls ' || x.oid::regprocedure::text || ', which is neither a pinned function nor a vetted kernel routine', f.name
  FROM fn f JOIN pg_proc x ON x.oid <> f.oid AND f.src ~* ('\m' || regexp_replace(x.proname, '(\W)', '\\\1', 'g') || ' *\(') JOIN nsp xn ON xn.oid = x.pronamespace, s
  WHERE x.oid NOT IN (SELECT fn FROM pins)
    AND NOT (xn.nspname = 'kernel' AND x.proowner = to_regrole(s.vetted_prefix || 'kernel')::oid)
    AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = x.oid AND d.deptype = 'e')
UNION ALL -- 133 no routine carries an app.* setting of its own: SET app.org_id = ... on a function the pinned function calls would change the org its statements run for
SELECT '133 routine a vetted role can run carries an app.* setting in its configuration', p.oid::regprocedure::text FROM pg_proc p
  WHERE p.oid IN (SELECT oid FROM scoped) AND EXISTS (SELECT 1 FROM unnest(p.proconfig) c WHERE c ~* '^app\.')
UNION ALL -- 134 no routine a vetted role can run builds SQL dynamically (rule 56's list, applied to invoker helpers too): query_to_xml('SELECT set_' || 'config(...)') would change the org
SELECT '134 routine a vetted role can run builds SQL dynamically (EXECUTE, format(), query_to_xml and the like)', r.name FROM rtn r
  WHERE r.src ~* '\mexecute\M|\mformat\s*\(|\m(query|cursor|table|schema|database)_to_xml\w*'
UNION ALL -- 125 the writer of a pinned function is a role registered as one (kernel.pin_writers)
SELECT '125 pinned function has a writer that is not a registered pin writer', f.name FROM fn f WHERE f.writer IS NULL OR f.writer NOT IN (SELECT role FROM kernel.pin_writers)
)
SELECT rule, object FROM cat;
REVOKE ALL ON kernel.pin_violations FROM PUBLIC;

-- The public view: what it was (kernel_0011), minus checks 57 (ON CONFLICT), 88 (DELETE) and 89 (a vetted role's DELETE privilege) for the
-- functions and tables that carry a pin, which check 100-113 hold to the pinned shape instead, plus those checks.
DROP VIEW kernel.rls_violations;
CREATE VIEW kernel.rls_violations WITH (security_invoker = true) AS
SELECT c.rule, c.object FROM kernel.rls_violations_core c
  WHERE NOT (c.rule = '42 vetted role pipeshub_vetted_kernel holds SELECT outside its schema' AND c.object IN (SELECT rel::text FROM kernel.vetted_extension_reads))
    AND NOT EXISTS (SELECT 1 FROM kernel.job_gate_grants j WHERE c.rule = '42 vetted role ' || j.owner || ' holds SELECT outside its schema' AND c.object = j.rel)
UNION ALL
SELECT v.rule, v.object FROM kernel.vetted_violations v
  WHERE NOT EXISTS (SELECT 1 FROM kernel.job_gate_grants j WHERE v.rule = '73 vetted role ' || j.owner || ' holds SELECT on a table that did not opt in' AND v.object = j.rel)
    AND NOT (v.rule = '57 write function uses ON CONFLICT' AND v.object IN (SELECT proc::text FROM kernel.function_registry WHERE dedupe_rel IS NOT NULL))
    AND NOT (v.rule = '57 write function uses ON CONFLICT' AND v.object IN (SELECT proc::text FROM kernel.vetted_write_pins WHERE op = 'upsert'))
UNION ALL
SELECT pv.rule, pv.object FROM kernel.person_violations pv
  WHERE NOT (pv.rule = '57 write function uses ON CONFLICT' AND pv.object IN (SELECT proc::text FROM kernel.function_registry WHERE dedupe_rel IS NOT NULL))
    AND NOT (pv.rule = '57 write function uses ON CONFLICT' AND pv.object IN (SELECT proc::text FROM kernel.vetted_write_pins WHERE op = 'upsert'))
    AND NOT (pv.rule = '88 read or write function deletes' AND pv.object IN (SELECT proc::text FROM kernel.vetted_write_pins WHERE op = 'delete'))
    AND NOT (pv.rule LIKE '89 vetted role % holds DELETE on a table without vetted_delete' AND pv.object IN (SELECT rel::text FROM kernel.vetted_write_pins WHERE op = 'delete'))
UNION ALL
SELECT rule, object FROM kernel.pin_violations;
REVOKE ALL ON kernel.rls_violations FROM PUBLIC;
