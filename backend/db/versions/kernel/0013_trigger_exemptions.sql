-- Reviewed exemptions from check 97 for trigger functions that write. See the revision.
CREATE TABLE kernel.trigger_exemptions (
  proc regprocedure PRIMARY KEY,
  body_md5 text NOT NULL,
  reason text NOT NULL,
  reviewed_by text NOT NULL,
  reviewed_at date NOT NULL DEFAULT current_date);

CREATE FUNCTION kernel.exempt_trigger(p_proc regprocedure, p_reason text, p_reviewed_by text) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, kernel, pg_temp AS $fn$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid = p_proc::oid AND p.prorettype = 'trigger'::regtype AND NOT p.prosecdef) THEN
    RAISE EXCEPTION 'exempt_trigger: % is not an invoker trigger function', p_proc;
  END IF;
  INSERT INTO kernel.trigger_exemptions (proc, body_md5, reason, reviewed_by)
    SELECT p_proc, md5(p.prosrc), p_reason, p_reviewed_by FROM pg_proc p WHERE p.oid = p_proc::oid
  ON CONFLICT (proc) DO UPDATE SET body_md5 = excluded.body_md5, reason = excluded.reason, reviewed_by = excluded.reviewed_by, reviewed_at = current_date;
END $fn$;
REVOKE ALL ON FUNCTION kernel.exempt_trigger(regprocedure, text, text) FROM PUBLIC;
