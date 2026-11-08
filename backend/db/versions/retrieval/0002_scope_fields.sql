-- The permission scope fields of a group (one row per virtual record, like its membership), beside the membership arrays and behind the same
-- compare-and-set fence (computed_at). NULL means the group was never stamped, which is not "container". The two id lists are mirrored as
-- group_members rows of kinds 4 (path) and 5 (override scope), so a filter can probe them with the index that serves kinds 1-3.
-- The widened CHECK is added NOT VALID: it is checked for every new row, and the rows that exist already satisfy it.
ALTER TABLE retrieval.point_groups
  ADD COLUMN path_ids text[], ADD COLUMN acl_scope text[], ADD COLUMN scope_stamp text,
  ADD COLUMN scope_version integer, ADD COLUMN scope_generation integer;

ALTER TABLE retrieval.group_members DROP CONSTRAINT group_members_kind_check;
ALTER TABLE retrieval.group_members ADD CONSTRAINT group_members_kind_check CHECK (kind IN (1, 2, 3, 4, 5)) NOT VALID;      -- 4 path, 5 override scope
