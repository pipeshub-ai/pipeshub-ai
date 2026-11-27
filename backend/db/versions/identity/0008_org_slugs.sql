-- The slugs callers chose (createWithAdmin's slug), unique across the install as MongoDB's org collection keeps them. The counter's
-- 'org-<n>' slugs are not here: the sequence keeps those apart, and a caller may not choose that shape. The table answers across
-- organisations (one slug, one organisation), so it is global: no service role reaches it, and only its two vetted writes fill it,
-- each answering a taken slug as a malformed one.
CREATE TABLE identity.org_slugs (
  slug text PRIMARY KEY,
  org_id text NOT NULL UNIQUE
);
SELECT kernel.apply_rls('identity.org_slugs', 'global', p_vetted_write => true);
-- The document copy claims each copied organisation's slug too (identity.copied_org_slug_claim, in the unit that writes the row), so the
-- table holds every chosen slug and the unique key decides a claim racing the copy. Rows copied before this revision are claimed here.
INSERT INTO identity.org_slugs (org_id, slug) SELECT o.org_id, o.slug FROM identity.orgs o WHERE o.slug !~ '^org-[0-9]+$';
