-- The slugs callers chose (createWithAdmin's slug), unique across the install as MongoDB's org collection keeps them. The counter's
-- 'org-<n>' slugs are not here: the sequence keeps those apart, and a caller may not choose that shape. A cross-org unique key is an
-- exempted public_by_design key, so no service role reaches this table: identity.org_slug_claim writes it, and answers a taken slug
-- exactly as a malformed one.
CREATE TABLE identity.org_slugs (
  org_id text PRIMARY KEY,
  slug text NOT NULL,
  CONSTRAINT org_slugs_slug_key UNIQUE (slug)
);
SELECT kernel.apply_rls('identity.org_slugs', 'org', p_vetted => true, p_vetted_write => true);
SELECT kernel.exempt_constraint('identity.org_slugs', 'org_slugs_slug_key', 'public_by_design',
  'an organisation slug a caller chose is unique across the install; identity.org_slug_claim answers a taken one as a malformed one',
  'security-review');
