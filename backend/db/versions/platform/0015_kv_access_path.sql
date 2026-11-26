-- The path a key-value entry's access is checked under: platform.kv_may and the shadow log read this path, never the stored key.
-- This build checks the key itself. An extra migration location may replace the body, for keys stored under a prefix of their own
-- (the path after it is the one the matrix knows); the signature is frozen and the function stays IMMUTABLE SQL, so the planner
-- still inlines the policies.
CREATE FUNCTION platform.kv_access_path(p_key text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT p_key $$;
REVOKE ALL ON FUNCTION platform.kv_access_path(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.kv_access_path(text)
  TO pipeshub_node, pipeshub_connectors, pipeshub_indexing, pipeshub_query, pipeshub_kv;

ALTER POLICY kv_read ON platform.kv_entries
  USING (platform.kv_may(platform.kv_access_path(key), false) OR platform.kv_outside(platform.kv_access_path(key), 'read', expires_at));
ALTER POLICY kv_insert ON platform.kv_entries
  WITH CHECK (platform.kv_may(platform.kv_access_path(key), true) OR platform.kv_outside(platform.kv_access_path(key), 'insert', expires_at));
ALTER POLICY kv_update ON platform.kv_entries
  USING (platform.kv_may(platform.kv_access_path(key), true) OR platform.kv_outside(platform.kv_access_path(key), 'update', expires_at))
  WITH CHECK (platform.kv_may(platform.kv_access_path(key), true) OR platform.kv_outside(platform.kv_access_path(key), 'update', expires_at));
ALTER POLICY kv_delete ON platform.kv_entries
  USING (platform.kv_may(platform.kv_access_path(key), true) OR expires_at <= now()
         OR platform.kv_outside(platform.kv_access_path(key), 'delete', expires_at));

SELECT kernel.register_internal_policy('platform.kv_entries', 'kv_read');
SELECT kernel.register_internal_policy('platform.kv_entries', 'kv_insert');
SELECT kernel.register_internal_policy('platform.kv_entries', 'kv_update');
SELECT kernel.register_internal_policy('platform.kv_entries', 'kv_delete');
