-- The slim image can load pg_search and pg_stat_statements. The kernel's list of extensions that may be installed (kernel_0003, immutable once
-- shipped) names only the ones the base schema needs, so the list gains these two here. Idempotent: a name already in the list is not added again.
UPDATE kernel.settings SET extensions = extensions || ARRAY(SELECT n FROM unnest(ARRAY['pg_search', 'pg_stat_statements']::name[]) n WHERE n <> ALL (extensions));
