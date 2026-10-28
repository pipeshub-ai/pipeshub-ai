-- kernel.src_text reads function bodies for every text check. PostgreSQL ends a `--` comment at a carriage return as well as at a line feed; this
-- helper ended it at the line feed only, so `-- x<CR>DELETE FROM t WHERE true;` was one comment to the checks and a DELETE to the server. Same signature,
-- same result for every body without a CR; the lib/vetted.py `normalise` has the same change.
CREATE OR REPLACE FUNCTION kernel.src_text(p_src text, p_keep_strings boolean DEFAULT false) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $fn$
  SELECT regexp_replace(regexp_replace(
    CASE WHEN p_keep_strings THEN x.s ELSE regexp_replace(x.s, '''(?:[^'']|'''')*''', '''''', 'g') END,
    '[ \t\n\r\f\v]*\.[ \t\n\r\f\v]*', '.', 'g'), '[ \t\n\r\f\v]+', ' ', 'g')
  FROM (SELECT regexp_replace(p_src, '(''(?:[^'']|'''')*'')|--[^\n\r]*|/\*([^*]|\*+[^*/])*\*+/', ' \1', 'g') AS s) x
$fn$;
